"""Fresh install of a standalone NIOS grid on a lab appliance via grid_deploy."""

from __future__ import annotations

import os
from typing import TYPE_CHECKING

from dominion.src.appliance import Appliance, ssh_reachable
from dominion.src.config import GridConfig
from dominion.src.grid import Grid, _parse_tag_file
from dominion.src.lab_info import get_lab_info
from dominion.src.log import create_logger

if TYPE_CHECKING:
    from dominion.src.log import NiosLogger


def fresh_install(
    hostname: str,
    image: str,
    logger: NiosLogger | None = None,
    **grid_kwargs,
) -> Appliance:
    """Install an image as a standalone grid on a lab appliance and verify it.

    Wraps Grid.from_config(topology="standalone") but does not trust the
    grid_deploy exit code (config_grid sometimes exits non-zero after a good
    install, dominion issue #1). Success means the tag file names the expected
    host, IP and image (so an old install still up after a failed config_grid
    is not accepted), SSH works, and WAPI is ready.

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
            does not match the lab info and image, or SSH or WAPI verification fails.
    """
    logger = logger or create_logger(name=f"dominion.fresh_install.{hostname}")

    expected_ip = get_lab_info(hostname).get("LAN_IPADDR")
    if not expected_ip:
        raise RuntimeError(f"no LAN_IPADDR from get_lab_info for {hostname}")

    config = GridConfig(
        topology="standalone", member=hostname, file=image, logger=logger, **grid_kwargs,
    )
    grid = Grid.from_config(config, check=False)

    app = grid.gm
    if app is None or (app.hostname, app.ip) != (hostname, expected_ip):
        found = (app.hostname, app.ip) if app else None
        raise RuntimeError(
            f"fresh install of {hostname}: tag file has {found}, "
            f"expected ({hostname}, {expected_ip}) (grid_deploy exit {grid.returncode})"
        )

    tag_image = _parse_tag_file(
        os.path.join(grid.setup_dir, f"{grid.tag}.xml")
    )["master"]["image"]
    want_image = os.path.basename(image.split("#")[0])
    if os.path.basename(tag_image.split("#")[0]) != want_image:
        raise RuntimeError(
            f"fresh install of {hostname}: tag file image {tag_image!r}, "
            f"expected {want_image!r} (grid_deploy exit {grid.returncode})"
        )

    if not ssh_reachable(expected_ip):
        raise RuntimeError(
            f"fresh install of {hostname}: SSH to {expected_ip} failed "
            f"(grid_deploy exit {grid.returncode})"
        )
    app.wapi.wait_until_ready()

    if grid.returncode:
        logger.warning(
            "grid_deploy exited %d but %s (%s) verified: tag file, SSH and WAPI OK",
            grid.returncode, hostname, expected_ip,
        )
    logger.info("Fresh install complete: %s (%s)", hostname, expected_ip)
    return app
