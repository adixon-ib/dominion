"""Fresh install of a standalone NIOS grid on a lab appliance via grid_deploy."""

from __future__ import annotations

import os
import re
import time
from typing import TYPE_CHECKING

from dominion.src.appliance import Appliance, ssh_reachable
from dominion.src.config import GridConfig
from dominion.src.constants import FRESH_INSTALL_SSH_INTERVAL, FRESH_INSTALL_SSH_TIMEOUT
from dominion.src.grid import Grid
from dominion.src.lab_info import get_lab_info
from dominion.src.log import create_logger

if TYPE_CHECKING:
    from dominion.src.log import NiosLogger

# Official builds: 9.2.0-56265[-hash]. Custom builds: 10.0.0-<user>-<date>.
_OFFICIAL_BUILD = re.compile(r"\d+\.\d+\.\d+-\d+(?=-|$)")


def _running_version(app: Appliance) -> str:
    """Read EXTERNAL_VERSION (else CLIENT_VERSION) from /infoblox/config in the nios container."""
    _, out, _ = app.container._transport.run("cat /infoblox/config")
    found = dict(
        line.split("=", 1) for line in out.splitlines()
        if line.startswith(("EXTERNAL_VERSION=", "CLIENT_VERSION="))
    )
    version = (found.get("EXTERNAL_VERSION") or found.get("CLIENT_VERSION") or "").strip()
    if not version:
        raise RuntimeError(f"no EXTERNAL_VERSION in /infoblox/config: {out[:200]!r}")
    return version


def _image_matches_version(image_basename: str, version: str) -> bool:
    m = _OFFICIAL_BUILD.match(version)
    key = m.group(0) if m else version
    return re.search(rf"(?<!\d){re.escape(key)}(?!\d)", image_basename) is not None


def fresh_install(
    hostname: str,
    image: str,
    logger: NiosLogger | None = None,
    **grid_kwargs,
) -> Appliance:
    """Install an image as a standalone grid on a lab appliance and verify it.

    Wraps Grid.from_config(topology="standalone") but does not trust the
    grid_deploy exit code (config_grid sometimes exits non-zero after a good
    install, dominion issue #1). config_grid writes the tag file before it
    installs, so the tag file alone does not prove the install happened.
    Success means:
      1. the tag file names the expected host, IP and image (cheap check);
      2. SSH works (retried for up to FRESH_INSTALL_SSH_TIMEOUT);
      3. WAPI is ready;
      4. the build running on the appliance (EXTERNAL_VERSION in
         /infoblox/config) matches the image name, which rules out an old
         install that is still up after a failed config_grid. Official builds
         compare "<ver>-<build>" (e.g. 9.2.0-56265), custom builds the whole
         version string.

    grid_deploy output is streamed at info level.

    Args:
        hostname: Lab hostname (e.g. vm-48-10).
        image: Image full path or bare filename, passed to grid_deploy unchanged.
            config_grid needs the full path for an image that is not on the
            install server yet (e.g.
            /import/builds/nios/develop/9.2/NIOS_9.2.0_<n>_.../nios-...-fixed-500G.ova).
        logger: Optional logger.
        **grid_kwargs: Extra GridConfig fields (max_retries, output_dir, ...).

    Returns:
        The installed Appliance.

    Raises:
        RuntimeError: If grid_deploy produced no usable tag file, the tag file
            does not match the lab info and image, SSH or WAPI verification
            fails, or the running build does not match the image.
    """
    logger = logger or create_logger(name=f"dominion.fresh_install.{hostname}")

    expected_ip = get_lab_info(hostname).get("LAN_IPADDR")
    if not expected_ip:
        raise RuntimeError(f"no LAN_IPADDR from get_lab_info for {hostname}")

    config = GridConfig(
        topology="standalone", member=hostname, file=image, logger=logger, **grid_kwargs,
    )
    grid = Grid.from_config(config, check=False, verbose=True)

    app = grid.gm
    if app is None or (app.hostname, app.ip) != (hostname, expected_ip):
        found = (app.hostname, app.ip) if app else None
        raise RuntimeError(
            f"fresh install of {hostname}: tag file has {found}, "
            f"expected ({hostname}, {expected_ip}) (grid_deploy exit {grid.returncode})"
        )

    want_image = os.path.basename(image.split("#")[0])
    if os.path.basename(grid.gm_image.split("#")[0]) != want_image:
        raise RuntimeError(
            f"fresh install of {hostname}: tag file image {grid.gm_image!r}, "
            f"expected {want_image!r} (grid_deploy exit {grid.returncode})"
        )

    for attempt in range(FRESH_INSTALL_SSH_TIMEOUT // FRESH_INSTALL_SSH_INTERVAL):
        if ssh_reachable(expected_ip):
            break
        logger.info("Waiting for SSH on %s (attempt %d)", expected_ip, attempt + 1)
        time.sleep(FRESH_INSTALL_SSH_INTERVAL)
    else:
        raise RuntimeError(
            f"fresh install of {hostname}: SSH to {expected_ip} failed "
            f"after {FRESH_INSTALL_SSH_TIMEOUT}s (grid_deploy exit {grid.returncode})"
        )
    app.wapi.wait_until_ready()

    version = _running_version(app)
    if not _image_matches_version(want_image, version):
        raise RuntimeError(
            f"fresh install of {hostname}: running build {version!r} does not match "
            f"image {want_image!r}; old install still up? (grid_deploy exit {grid.returncode})"
        )

    if grid.returncode:
        logger.warning(
            "grid_deploy exited %d but %s (%s) verified: tag file, SSH, WAPI and "
            "running build %s OK",
            grid.returncode, hostname, expected_ip, version,
        )
    logger.info("Fresh install complete: %s (%s) running %s", hostname, expected_ip, version)
    return app
