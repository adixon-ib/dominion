from __future__ import annotations

import subprocess
import xml.etree.ElementTree as ET
from typing import TYPE_CHECKING

from .appliance import Appliance
from .config import ApplianceConfig, GridConfig
from .log import create_logger

if TYPE_CHECKING:
    import logging

GRID_DEPLOY = "/blox/adixon1/scripts/lab/grid_deploy"
TAG_FILE = "/tmp/my_setup/mygrid.xml"


class Grid:

    def __init__(
        self,
        gm: Appliance | None,
        members: list[Appliance],
        topology: str,
        logger: logging.Logger,
    ):
        self.gm = gm
        self.members = members
        self.topology = topology
        self._logger = logger

    @property
    def appliances(self) -> list[Appliance]:
        if not self.members:
            return [self.gm] if self.gm else []
        return [self.gm, *self.members]

    @classmethod
    def from_config(cls, config: GridConfig) -> "Grid":
        logger = config.logger or create_logger(
            name=f"nios_test.grid-{config.member or config.gm or config.topology}",
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
        for line in proc.stdout:
            logger.info(line.rstrip())
        proc.wait()

        if proc.returncode != 0:
            raise RuntimeError(
                f"grid_deploy exited {proc.returncode} for topology {config.topology!r}"
            )

        tag_data = _parse_tag_file()
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

        return cls(gm=gm, members=members, topology=config.topology, logger=logger)

    def __repr__(self) -> str:
        hosts = [a.ip or "?" for a in self.appliances]
        return f"Grid(topology={self.topology!r}, appliances={hosts})"


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
    return cmd


def _parse_tag_file() -> dict[str, dict[str, str]]:
    tree = ET.parse(TAG_FILE)
    root = tree.getroot()

    result: dict[str, dict[str, str]] = {}

    master = root.find("master")
    if master is not None:
        result["master"] = {
            "hostname": master.findtext("UNIT_1_ID") or "",
            "ip": master.findtext("UNIT_1_IPADDR") or "",
        }

    i = 0
    while True:
        member = root.find(f"member{i}")
        if member is None:
            break
        result[f"member{i}"] = {
            "hostname": member.findtext("UNIT_1_ID") or "",
            "ip": member.findtext("UNIT_1_IPADDR") or "",
        }
        i += 1

    return result


def _make_appliance(
    hostname: str,
    ip: str,
    config: GridConfig,
    logger: logging.Logger,
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
