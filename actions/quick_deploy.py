"""Quick deploy: reinstall NIOS on a lab appliance and bring it to a usable state.

Runs upgrade_test_system, waits for boot, configures networking from lab info,
enables SSH, and polls WAPI. Faster than full grid_deploy/config_grid when you
just need a single standalone appliance.
"""

from __future__ import annotations

import subprocess
import time
from typing import TYPE_CHECKING

import pexpect

from dominion.src.appliance import reset_console, run_addkeys, wait_for_wapi
from dominion.src.constants import (
    NIOS_CONSOLE_USER,
    NIOS_CONSOLE_PASSWORD,
    NIOS_REBOOT_TIMEOUT,
)
from dominion.src.lab_info import get_lab_info
from dominion.src.log import create_logger
from dominion.src.session import ConsoleConnectSession

if TYPE_CHECKING:
    from dominion.src.log import NiosLogger

UPGRADE_TIMEOUT = 1200
BOOT_TIMEOUT = 1200


def _console_set(console, cmd: str) -> None:
    """Run a console set command, answering y to all prompts."""
    console.sendline(cmd)
    while True:
        idx = console.expect_one_of([r"Infoblox\s*>", r"y or n\):"])
        if idx == 0:
            break
        time.sleep(0.1)
        console.sendline("y")


def _set_network(console, ip: str, netmask: str, gateway: str) -> None:
    """Drive the set network console wizard."""
    console.sendline("set network")
    console.expect(r"Enter.*[Aa]ddress")
    console.sendline(ip)
    console.expect("Enter netmask")
    console.sendline(netmask)
    console.expect("Enter gateway")
    console.sendline(gateway)
    console.expect("Enter VLAN")
    console.sendline("")

    idx = console.expect_one_of(
        ["Enter DSCP", "Configure IPv6", "Become grid member"],
        timeout=30,
    )
    if idx == 0:
        console.sendline("")
        console.expect("Configure IPv6")
        console.sendline("n")
    elif idx == 1:
        console.sendline("n")

    if idx != 2:
        console.expect("Become grid member")
    console.sendline("n")

    console.expect("Is this correct")
    console.sendline("y")
    console.expect("Are you sure")
    console.sendline("y")
    console.expect("Network settings have been updated", timeout=60)


def _wait_for_login(hostname: str, logger, timeout: int) -> ConsoleConnectSession:
    """Open a console_connect session and wait for NIOS to boot to login."""
    reset_console(hostname)
    spawn_cmd = f"console_connect -H {hostname}"
    session = ConsoleConnectSession(
        spawn_cmd=spawn_cmd,
        logger=logger,
        timeout=timeout,
    )
    session._child = pexpect.spawn(
        spawn_cmd, encoding="utf-8",
        timeout=timeout, echo=False,
    )
    session._child.logfile_read = session._build_logfile_read()
    session._child.expect(session.LOGIN_PATTERN, timeout=timeout)
    session._child.sendline(NIOS_CONSOLE_USER)
    session._child.expect(session.PASSWORD_PATTERN, timeout=15)
    session._child.sendline(NIOS_CONSOLE_PASSWORD)
    session._child.expect(session.PROMPT_PATTERN, timeout=30)
    session._last_prompt = session._child.after or ""
    return session


def quick_deploy(
    hostname: str,
    image_file: str,
    logger: NiosLogger | None = None,
) -> str:
    """Quick deploy a NIOS image to a lab appliance.

    Steps: upgrade_test_system -> wait for boot -> set network (from lab info)
    -> wait for reboot -> set support_access + remote_console -> poll WAPI
    -> addkeys.

    Args:
        hostname: Lab hostname (e.g. vm-09-89).
        image_file: Image filename already on the install server.
        logger: Optional logger.

    Returns:
        The appliance's LAN IP address.
    """
    logger = logger or create_logger(name=f"dominion.quick_deploy.{hostname}")

    logger.info("Getting lab info for %s", hostname)
    lab_info = get_lab_info(hostname)
    ip = lab_info.get("LAN_IPADDR")
    netmask = lab_info.get("LAN_NETMASK")
    gateway = lab_info.get("LAN_ROUTER")
    if not all([ip, netmask, gateway]):
        raise RuntimeError(
            f"Missing network info from get_lab_info: "
            f"ip={ip} netmask={netmask} gateway={gateway}"
        )
    logger.info("Network: ip=%s netmask=%s gateway=%s", ip, netmask, gateway)

    logger.info("Installing %s on %s", image_file, hostname)
    proc = subprocess.Popen(
        ["upgrade_test_system", "-H", hostname, "-f", image_file],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
    )
    for line in proc.stdout:
        logger.debug(line.rstrip())
    proc.wait(timeout=UPGRADE_TIMEOUT)
    if proc.returncode != 0:
        raise RuntimeError(f"upgrade_test_system failed (exit {proc.returncode})")
    logger.info("Install kicked off, waiting for boot")

    logger.info("Waiting for %s to boot (up to %ds)...", hostname, BOOT_TIMEOUT)
    session = _wait_for_login(hostname, logger, BOOT_TIMEOUT)
    logger.info("NIOS booted, console login successful")

    output = session.run("show status")
    logger.info("show status:\n%s", output)

    logger.info("Setting network: %s/%s gw %s", ip, netmask, gateway)
    _set_network(session, ip, netmask, gateway)
    logger.info("Network configured, NIOS is rebooting")
    session._close()

    time.sleep(30)
    logger.info("Waiting for %s to reboot (up to %ds)...", hostname, NIOS_REBOOT_TIMEOUT)
    session = _wait_for_login(hostname, logger, NIOS_REBOOT_TIMEOUT)
    logger.info("NIOS back up after network change")

    _console_set(session, "set support_access")
    logger.info("support_access enabled")
    _console_set(session, "set remote_console")
    logger.info("remote_console enabled")
    session._close()

    wait_for_wapi(ip, logger)

    if run_addkeys(ip, logger):
        logger.info("SSH keys pushed")
    else:
        logger.warning("SSH may not work: addkeys failed")

    logger.info("Quick deploy complete: %s (%s)", hostname, ip)
    return ip
