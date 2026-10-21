"""Reset a NIOS appliance to factory defaults and reconfigure it.

Drives 'reset all' via console_connect, then restores networking
(from get_lab_info), remote console, support access, and adds SSH keys.
"""

from __future__ import annotations

import re
import subprocess
import time

from dominion.src.appliance import Appliance, run_addkeys, wait_for_wapi
from dominion.src.constants import (
    NIOS_REBOOT_TIMEOUT,
)

def _get_lab_network(hostname: str) -> tuple[str, str, str]:
    """Query get_lab_info for the appliance's LAN settings."""
    result = subprocess.run(
        ["get_lab_info", "-H", hostname],
        capture_output=True, text=True, check=True,
    )
    output = result.stdout

    ip_match = re.search(r"LAN_IPADDR=(\S+)", output)
    mask_match = re.search(r"LAN_NETMASK=(\S+)", output)
    gw_match = re.search(r"LAN_ROUTER=(\S+)", output)

    if not (ip_match and mask_match and gw_match):
        raise RuntimeError(
            f"Failed to parse LAN settings from get_lab_info for {hostname}. "
            f"Output: {output!r}"
        )

    return ip_match.group(1), mask_match.group(1), gw_match.group(1)


def _console_set_network(console, ip: str, netmask: str, gateway: str) -> None:
    """Drive the 'set network' wizard. Session dies after confirmation."""
    console.sendline("set network")
    console.expect("IP address")
    console.sendline(ip)
    console.expect("netmask")
    console.sendline(netmask)
    console.expect("gateway")
    console.sendline(gateway)
    console.expect("VLAN")
    console.sendline("")

    idx = console.expect_one_of(
        ["DSCP", "IPv6", "grid member"],
        timeout=30,
    )
    if idx == 0:
        console.sendline("")
        console.expect("IPv6")
        console.sendline("n")
    elif idx == 1:
        console.sendline("n")

    if idx != 2:
        console.expect("grid member")
    console.sendline("n")

    console.expect("Is this correct")
    console.sendline("y")
    console.expect("Are you sure")
    console.sendline("y")


def _flush_ssh_rate_limit(app: Appliance) -> None:
    """Disable the SSH firewall rate limiter to prevent lockouts."""
    app._logger.info("Flushing SSH rate limiter")
    with app.shell_session() as shell:
        shell.run(
            "iptables -F SSH_RATE_LIMIT"
            " && iptables -I SSH_RATE_LIMIT -j RETURN"
            " && touch /infoblox/var/debug_ssh_enabled"
        )


def reset_all(app: Appliance) -> str:
    """Reset an appliance to factory defaults and reconfigure it.

    Runs 'reset all' via out-of-band console, restores the network
    configuration from get_lab_info, enables remote console and support
    access, waits for WAPI, and pushes SSH keys.

    Args:
        app: The appliance to reset. Must be a lab Appliance (not LocalAppliance).

    Returns:
        The configured IP address.

    Raises:
        TypeError: If app is not an Appliance.
        RuntimeError: If get_lab_info fails, network info cannot be parsed,
            SSH verification fails, or WAPI does not become ready.
    """
    if not isinstance(app, Appliance):
        raise TypeError(
            f"app must be an Appliance (got {type(app).__name__}). "
            "reset_all requires console_connect, which is only available "
            "on lab appliances."
        )

    ip, netmask, gateway = _get_lab_network(app.hostname)
    app._logger.info(
        "Reset target: %s -> %s/%s gw %s", app.hostname, ip, netmask, gateway,
    )

    # Phase 1: reset all, wait for reboot.
    with app.console_connect_session(timeout=NIOS_REBOOT_TIMEOUT) as console:
        app._logger.info("Sending reset all")
        console.sendline("reset all")
        console.expect(r"y or n\):", timeout=30)
        console.sendline("y")

    # Phase 2: verify appliance is up after reset.
    with app.console_connect_session(timeout=NIOS_REBOOT_TIMEOUT) as console:
        output = console.run("show status")
        app._logger.debug("Post-reset status: %s", output)

    # Phase 3: set network. Triggers a reboot that kills this session.
    app._logger.info("Setting network to %s/%s gw %s", ip, netmask, gateway)
    with app.console_connect_session(timeout=NIOS_REBOOT_TIMEOUT) as console:
        _console_set_network(console, ip, netmask, gateway)

    # Phase 4: fresh session after network reboot.
    # Enable remote console and support access.
    app._logger.info("Waiting for NIOS to reboot with new network...")
    with app.console_connect_session(timeout=NIOS_REBOOT_TIMEOUT) as console:
        output = console.run("show status")
        app._logger.debug("Post-network status: %s", output)
        Appliance._console_set(console, "set remote_console")
        Appliance._console_set(console, "set support_access")

    # Phase 5: wait for WAPI (curl from lab host, no SSH needed),
    # then push SSH keys once WAPI is up.
    wait_for_wapi(ip, app._logger)
    run_addkeys(ip, app._logger)

    # Phase 6: verify SSH works.
    app._logger.info("Verifying SSH connectivity")
    with app.shell_session() as shell:
        shell.run("hostname")
    app._logger.info("SSH verified")

    # Phase 7: flush SSH rate limiter so callers don't get locked out.
    _flush_ssh_rate_limit(app)

    app._logger.info("Reset complete: %s is ready at %s", app.hostname, ip)
    return ip
