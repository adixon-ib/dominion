"""Remove a NIOS member from a grid (set nogrid).

Drives 'set nogrid' on the member's console, waits for reboot, then
deletes the stale member record from the GM via WAPI.
"""

from __future__ import annotations

from dominion.src.appliance import Appliance
from dominion.src.constants import NIOS_REBOOT_TIMEOUT


def _console_nogrid(member: Appliance) -> None:
    """Drive 'set nogrid' on the member. Member reboots after confirmation."""
    with member.console_connect_session() as console:
        console.sendline("set nogrid")
        console.expect(r"Is this such an emergency")
        console.sendline("y")
        console.expect(r"Are you sure")
        console.sendline("y")
    member._logger.debug("set nogrid sent, member is rebooting")


def _delete_member_from_master(master: Appliance, member_ip: str) -> None:
    """Delete the member record matching member_ip from the GM via WAPI."""
    existing = master.wapi.get("member", params="_return_fields=vip_setting")
    for rec in existing:
        if rec.get("vip_setting", {}).get("address") == member_ip:
            master.wapi.delete(rec["_ref"])
            master._logger.debug("Deleted member record: %s", rec["_ref"])
            return
    master._logger.debug("No member record found for %s", member_ip)


def leave_grid(member: Appliance, master: Appliance) -> None:
    """Remove a member from a grid. Blocks until the member is standalone again.

    Runs 'set nogrid' on the member console (triggers reboot), waits for
    the member to come back as a standalone GM, then deletes the stale
    member record from the master's WAPI.

    Args:
        member: The member appliance to remove from the grid.
        master: The grid master.

    Raises:
        TypeError: If member or master is not an Appliance.
    """
    for name, obj in [("member", member), ("master", master)]:
        if not isinstance(obj, Appliance):
            raise TypeError(
                f"{name} must be an Appliance (got {type(obj).__name__}). "
                "leave_grid requires console_connect, which is only available "
                "on lab appliances."
            )

    member._logger.info("Leaving grid: %s -> standalone", member.hostname)

    _console_nogrid(member)

    member._logger.info("Waiting for %s to reboot as standalone...", member.hostname)
    with member.console_connect_session(timeout=NIOS_REBOOT_TIMEOUT) as console:
        output = console.run("show status")
        assert "Grid Master IP" not in output, (
            f"Member still shows Grid Master IP after nogrid: {output}"
        )
    member._logger.debug("Member is standalone")

    master._logger.info("Cleaning member record from %s", master.hostname)
    _delete_member_from_master(master, member.ip)

    member._logger.info("leave_grid complete: %s is standalone", member.hostname)
