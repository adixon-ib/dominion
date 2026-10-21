"""Fresh install of a standalone NIOS grid on a lab appliance via grid_deploy."""

from __future__ import annotations

from typing import TYPE_CHECKING

from dominion.src.appliance import Appliance, ssh_reachable
from dominion.src.config import GridConfig
from dominion.src.grid import Grid
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
    host and IP, WAPI is ready, and SSH works.

    Args:
        hostname: Lab hostname (e.g. vm-48-10).
        image: Image filename; grid_deploy looks it up, do not pass a path.
        logger: Optional logger.
        **grid_kwargs: Extra GridConfig fields (max_retries, output_dir, ...).

    Returns:
        The installed Appliance.

    Raises:
        RuntimeError: If grid_deploy produced no usable tag file, the tag file
            does not match the lab info, or WAPI or SSH verification fails.
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

    app.wapi.wait_until_ready()
    if not ssh_reachable(expected_ip):
        raise RuntimeError(
            f"fresh install of {hostname}: SSH to {expected_ip} failed "
            f"(grid_deploy exit {grid.returncode})"
        )

    if grid.returncode:
        logger.warning(
            "grid_deploy exited %d but %s (%s) verified: tag file, WAPI and SSH OK",
            grid.returncode, hostname, expected_ip,
        )
    logger.info("Fresh install complete: %s (%s)", hostname, expected_ip)
    return app
