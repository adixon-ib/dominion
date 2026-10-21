from __future__ import annotations

import os
import subprocess
from collections import deque
import xml.etree.ElementTree as ET
from typing import TYPE_CHECKING

from .appliance import Appliance
from .config import ApplianceConfig, GridConfig
from .log import create_logger

if TYPE_CHECKING:
    from .log import NiosLogger

_OUTPUT_TAIL_LINES = 20
GRID_DEPLOY = os.path.join(os.path.dirname(os.path.dirname(__file__)), "tools", "grid_deploy")


class Grid:

    def __init__(
        self,
        gm: Appliance | None,
        members: list[Appliance],
        topology: str,
        logger: NiosLogger,
        setup_dir: str | None = None,
        tag: str | None = None,
        returncode: int = 0,
        gm_image: str = "",
    ):
        self.gm = gm
        self.members = members
        self.topology = topology
        self.setup_dir = setup_dir
        self.tag = tag
        self.returncode = returncode
        self.gm_image = gm_image
        self._logger = logger

    @property
    def appliances(self) -> list[Appliance]:
        if not self.members:
            return [self.gm] if self.gm else []
        return [self.gm, *self.members]

    @classmethod
    def from_config(
        cls, config: GridConfig, check: bool = True, verbose: bool = False,
    ) -> "Grid":
        """Run grid_deploy and build the Grid from its tag file.

        With check=False a non-zero grid_deploy exit is logged and the Grid is
        still returned if the tag file parses; the exit code is kept in
        Grid.returncode. The caller must verify the result itself.

        grid_deploy output is logged at debug, or at info with verbose=True.
        Grid.gm_image is the image recorded for the master in the tag file.
        """
        logger = config.logger or create_logger(
            name=f"dominion.grid-{config.member or config.gm or config.topology}",
            level=config.log_level,
            log_file=config.log_file,
        )

        cmd = _build_cmd(config)
        logger.info("Running: %s", " ".join(cmd))

        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
        )
        tail: deque[str] = deque(maxlen=_OUTPUT_TAIL_LINES)
        log_line = logger.info if verbose else logger.debug
        for line in proc.stdout:
            tail.append(line.rstrip())
            log_line(line.rstrip())
        proc.wait()

        msg = f"grid_deploy exited {proc.returncode} for topology {config.topology!r}"
        if proc.returncode != 0:
            if check:
                raise RuntimeError(msg)
            logger.warning("%s; continuing, caller must verify", msg)

        tag_file = os.path.join(config.setup_dir, f"{config.tag}.xml")
        try:
            tag_data = _parse_tag_file(tag_file)
        except (OSError, ET.ParseError) as e:
            if proc.returncode == 0:
                raise
            raise RuntimeError(
                f"{msg}; tag file {tag_file} unusable: {e}\n"
                f"last grid_deploy output:\n" + "\n".join(tail)
            ) from e
        logger.info("Tag file parsed: %s", list(tag_data.keys()))

        gm_data = tag_data.get("master")
        gm = None
        if gm_data:
            gm = _make_appliance(gm_data["hostname"], gm_data["ip"], config, logger)
            logger.info("GM: %s (%s)", gm_data["hostname"], gm_data["ip"])

        members = []
        i = 0
        while f"member{i}" in tag_data:
            m = tag_data[f"member{i}"]
            appliance = _make_appliance(m["hostname"], m["ip"], config, logger)
            logger.info("Member%d: %s (%s)", i, m["hostname"], m["ip"])
            members.append(appliance)
            i += 1

        return cls(
            gm=gm,
            members=members,
            topology=config.topology,
            logger=logger,
            setup_dir=config.setup_dir,
            tag=config.tag,
            returncode=proc.returncode,
            gm_image=gm_data["image"] if gm_data else "",
        )

    def __repr__(self) -> str:
        hosts = [a.ip or "?" for a in self.appliances]
        return (
            f"Grid(topology={self.topology!r}, appliances={hosts}, "
            f"setup_dir={self.setup_dir!r}, tag={self.tag!r})"
        )

    def __str__(self) -> str:
        apps = ", ".join(
            f"{a.hostname} ({a.ip})" if a.ip else a.hostname
            for a in self.appliances
        )
        location = f" [setup_dir={self.setup_dir}, tag={self.tag}]" if self.tag else ""
        return f"{self.topology}: {apps}{location}"


def _build_cmd(config: GridConfig) -> list[str]:
    cmd = [GRID_DEPLOY, config.topology]
    if config.member:
        cmd += ["--member", config.member]
    if config.gm:
        cmd += ["--gm", config.gm]
    if config.ha:
        cmd += ["--ha", config.ha]
    if config.file:
        cmd += ["--file", config.file]
    if config.ova:
        cmd += ["--ova", config.ova]
    if config.bin:
        cmd += ["--bin", config.bin]
    if config.ipv4_only:
        cmd += ["--ipv4-only"]
    cmd += ["--max-retries", str(config.max_retries)]
    cmd += ["--setup-dir", config.setup_dir, "--tag", config.tag]
    return cmd


def _parse_tag_file(tag_file: str) -> dict[str, dict[str, str]]:
    tree = ET.parse(tag_file)
    root = tree.getroot()

    result: dict[str, dict[str, str]] = {}

    # config_grid tag files store node fields as XML attributes on the
    # <master>/<memberN> elements (e.g. <master UNIT_1_ID="vm-09-89" .../>),
    # not as child elements.
    master = root.find("master")
    if master is not None:
        result["master"] = {
            "hostname": master.get("UNIT_1_ID") or "",
            "ip": master.get("UNIT_1_IPADDR") or "",
            "image": master.get("image") or "",
        }

    i = 0
    while True:
        member = root.find(f"member{i}")
        if member is None:
            break
        result[f"member{i}"] = {
            "hostname": member.get("UNIT_1_ID") or "",
            "ip": member.get("UNIT_1_IPADDR") or "",
        }
        i += 1

    return result


def _make_appliance(
    hostname: str,
    ip: str,
    config: GridConfig,
    logger: NiosLogger,
) -> Appliance:
    cfg = ApplianceConfig(
        hostname=hostname,
        ip=ip or None,
        chroot=config.chroot,
        worktree=config.worktree,
        log_level=config.log_level,
        output_dir=config.output_dir,
        logger=logger,
    )
    return Appliance.from_config(cfg)
