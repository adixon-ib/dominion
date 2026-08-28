"""Join a NIOS member appliance to a grid master.

A join is considered successful when the console is back up (shows
"Grid Master IP" in status). After joining, the member's WAPI redirects
to the GM and no longer serves JSON locally, so WAPI readiness is not
checked on the member.
"""

from __future__ import annotations

from typing import Any

from dominion.src.appliance import Appliance

_MEMBER_FIELDS = "_return_fields=host_name,vip_setting,ipv6_setting,config_addr_type,platform"


def _register_on_master(
    master: Appliance,
    member: Appliance,
    identity: dict[str, Any],
) -> str:
    """Clean stale records on master and pre-register the member via WAPI."""
    member_vip = identity["vip_setting"]["address"]
    existing_members = master.wapi.get("member", params="_return_fields=vip_setting")
    for existing in existing_members:
        if existing.get("vip_setting", {}).get("address") == member_vip:
            master._logger.debug("Cleaning stale member record for %s", member_vip)
            master.wapi.delete(existing["_ref"])

    new_member = {
        "host_name": f"{member.hostname}.mygrid.infoblox.com",
        "config_addr_type": identity["config_addr_type"],
        "vip_setting": identity["vip_setting"],
        "platform": identity["platform"],
    }
    if identity.get("ipv6_setting"):
        new_member["ipv6_setting"] = identity["ipv6_setting"]

    ref = master.wapi.post("member", new_member)
    assert ref, f"Failed to pre-register member on master: {ref}"
    master._logger.debug("Pre-registered member: %s", ref)
    return ref


def _console_join(
    member: Appliance,
    master_ip: str,
    grid_name: str,
    shared_secret: str,
) -> None:
    """Drive the 'set membership' console wizard, then verify the join."""
    with member.console_connect_session() as console:
        console.sendline("set membership")
        console.expect("Master VIP")
        console.sendline(master_ip)
        console.expect("Grid Name")
        console.sendline(grid_name)
        console.expect("Shared Secret")
        console.sendline(shared_secret)
        console.expect("Is this correct")
        console.sendline("y")
        console.expect("Are you sure")
        console.sendline("y")
    member._logger.debug("Join command sent, member is rebooting")

    # Fresh console session to validate the member rebooted and joined.
    with member.console_connect_session(timeout=600) as console:
        output = console.run("show status")
        assert "Grid Master IP" in output, f"Member didn't join grid: {output}"
    member._logger.debug("Console ready, grid join confirmed")


def join_grid(
    member: Appliance,
    master: Appliance,
    grid_name: str = "Infoblox",
    shared_secret: str = "test",
) -> None:
    """Join a member to a grid master. Blocks until console confirms the join.

    After joining, the member's WAPI redirects all requests to the GM.
    Only the console check is used to verify the join succeeded.

    Args:
        member: The appliance to join.
        master: The grid master.
        grid_name: Grid name.
        shared_secret: Grid shared secret.

    Raises:
        TypeError: If member or master is not an Appliance.
    """
    for name, obj in [("member", member), ("master", master)]:
        if not isinstance(obj, Appliance):
            raise TypeError(
                f"{name} must be an Appliance (got {type(obj).__name__}). "
                "join_grid requires console_connect, which is only available on lab appliances."
            )

    member._logger.debug("Reading member identity from %s", member.hostname)
    records = member.wapi.get("member", params=_MEMBER_FIELDS)
    assert records, "Member WAPI returned no member records"
    identity = records[0]
    member._logger.debug("Member VIP: %s", identity["vip_setting"]["address"])

    master._logger.debug("Registering %s on master %s", member.hostname, master.hostname)
    _register_on_master(master, member, identity)

    member._logger.debug("Starting console join wizard")
    _console_join(member, master.ip, grid_name, shared_secret)
    member._logger.debug("Join complete on %s", member.hostname)
