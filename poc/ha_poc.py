#!/usr/bin/env python3
"""HA pair from two local NCC containers.

Merges the ncc_ha and ncc_lan1 docker bridges onto a single L2 domain,
then configures an HA pair through NIOS console + WAPI.

The NCC FAQ says HA doesn't work because "HA requires that the LAN1 and
HA interfaces be on the same subnet." This script works around that by
moving the HA veth pairs onto the LAN1 bridge post-start.

Prerequisites:
    slot start balerion && slot start drogon
    Wait ~300s for NIOS to boot in both containers.

Usage:
    python3 scripts/nios_test/poc/ha_poc.py
    python3 scripts/nios_test/poc/ha_poc.py --phase 3          # resume from phase 3
    python3 scripts/nios_test/poc/ha_poc.py --discover          # dump WAPI member schema
    python3 scripts/nios_test/poc/ha_poc.py --active-ip 172.20.0.2 --phase 3

Phases:
    0  Preflight — verify containers running, show IP plan
    1  Bridge merge — move HA veths onto LAN1 bridge
    2  Active node — set_network on balerion via serial console
    3  HA config — WAPI PUT to convert standalone → HA pair
    4  Passive node — set_network on drogon via serial console
    5  Grid join — set membership on drogon via serial console
    6  Verify — query grid status at VIP
"""

import argparse
import json
import re
import subprocess
import sys
import time

from nios_test import (
    NIOS_CONSOLE_PASSWORD,
    NIOS_CONSOLE_USER,
    NIOS_REBOOT_TIMEOUT,
    NIOS_WAPI_POLL_INTERVAL,
    NIOS_WAPI_READY_TIMEOUT,
    WAPI_VERSION,
    LocalContainerConfig,
    LocalNiosContainer,
)

# ---- configuration ----

ACTIVE_SLOT = "balerion"
PASSIVE_SLOT = "drogon"
CHROOT = "/local/91/chroots/balerion"

# All IPs in ncc_lan1 subnet (172.20.0.0/16).
# LAN IPs are discovered from docker inspect; HA IPs + VIP are fixed.
HA_ACTIVE = "172.20.100.1"
HA_PASSIVE = "172.20.100.2"
VIP = "172.20.100.10"
NETMASK = "255.255.0.0"
GATEWAY = "172.20.0.1"
VRID = 100
GRID_NAME = "Infoblox"
GRID_SECRET = "test"


# ---- helpers ----

def sh(cmd, check=True):
    r = subprocess.run(cmd, shell=True, capture_output=True, text=True)
    if check and r.returncode != 0:
        raise RuntimeError(f"$ {cmd}\n  exit={r.returncode}\n  {r.stderr.strip()}")
    return r.stdout.strip()


def wapi(ip, method, path, data=None):
    data_flag = f" -d '{json.dumps(data)}'" if data else ""
    out = sh(
        f"curl -sk -X {method} -u {NIOS_CONSOLE_USER}:{NIOS_CONSOLE_PASSWORD} "
        f'-H "Content-Type: application/json"{data_flag} '
        f"https://{ip}/wapi/{WAPI_VERSION}/{path}",
        check=False,
    )
    try:
        return json.loads(out)
    except (json.JSONDecodeError, TypeError):
        return out


def wait_wapi(ip, timeout=NIOS_WAPI_READY_TIMEOUT):
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            r = subprocess.run(
                f"curl -sk -o /dev/null -w '%{{http_code}}' "
                f"-u {NIOS_CONSOLE_USER}:{NIOS_CONSOLE_PASSWORD} --max-time 5 "
                f"https://{ip}/wapi/{WAPI_VERSION}/grid",
                shell=True, capture_output=True, text=True, timeout=10,
            )
            if r.stdout.strip().strip("'") == "200":
                return True
        except Exception:
            pass
        time.sleep(NIOS_WAPI_POLL_INTERVAL)
    return False


def add_host_route(ip):
    bridge = get_bridge_id("ncc_lan1")
    sh(f"sudo ip route replace {ip}/32 dev {bridge}", check=False)


def get_bridge_id(network):
    net_id = sh(f"docker network inspect {network} --format '{{{{.Id}}}}'")
    return f"br-{net_id[:12]}"


def get_lan_ip(container):
    return sh(
        f"docker inspect {container} --format "
        "'{{{{(index .NetworkSettings.Networks \"ncc_lan1\").IPAddress}}}}'"
    )


def container_for(slot):
    return LocalNiosContainer.from_config(LocalContainerConfig(
        container_name=slot,
        chroot=CHROOT,
        log_level="info",
    ))


# ---- phases ----

def phase_0():
    print("\n" + "=" * 60)
    print("Phase 0: Preflight")
    print("=" * 60)

    for slot in [ACTIVE_SLOT, PASSIVE_SLOT]:
        running = sh(
            f"docker inspect --format '{{{{.State.Running}}}}' {slot}",
            check=False,
        )
        if "true" not in running.lower():
            print(f"  FAIL: {slot} not running. Run: slot start {slot}")
            sys.exit(1)
        print(f"  {slot}: running")

    active_ip = get_lan_ip(ACTIVE_SLOT)
    passive_ip = get_lan_ip(PASSIVE_SLOT)

    print("\n  IP plan:")
    print(f"    {ACTIVE_SLOT} LAN (eth1):  {active_ip}")
    print(f"    {PASSIVE_SLOT} LAN (eth1): {passive_ip}")
    print(f"    {ACTIVE_SLOT} HA (eth2):   {HA_ACTIVE}")
    print(f"    {PASSIVE_SLOT} HA (eth2):  {HA_PASSIVE}")
    print(f"    VIP:                        {VIP}")
    print(f"    VRID:                       {VRID}")
    print("    All in 172.20.0.0/16 (ncc_lan1 subnet)")
    return active_ip, passive_ip


def phase_1():
    print("\n" + "=" * 60)
    print("Phase 1: Bridge Merge (ncc_ha → ncc_lan1)")
    print("=" * 60)

    lan1_br = get_bridge_id("ncc_lan1")
    ha_br = get_bridge_id("ncc_ha")
    print(f"  LAN1 bridge: {lan1_br}")
    print(f"  HA bridge:   {ha_br}")

    bridge_out = sh("bridge link show", check=False)
    veths = []
    for line in bridge_out.splitlines():
        if ha_br in line:
            m = re.match(r"\d+:\s+(\S+?)[@:\s]", line)
            if m:
                veths.append(m.group(1))

    if not veths:
        print("  No veths on HA bridge — already merged or containers not running")
        return

    for v in veths:
        print(f"  Moving {v}: {ha_br} → {lan1_br}")
        sh(f"sudo ip link set {v} master {lan1_br}")

    print(f"  Merged {len(veths)} HA interfaces onto LAN1 bridge")
    print("  eth1 (LAN) and eth2 (HA) now share L2")


def phase_2():
    print("\n" + "=" * 60)
    print(f"Phase 2: set_network on {ACTIVE_SLOT}")
    print("=" * 60)

    c = container_for(ACTIVE_SLOT)
    ip = c.set_network()
    print(f"  {ACTIVE_SLOT} ready at {ip}")
    return ip


def phase_3(active_ip, passive_ip):
    print("\n" + "=" * 60)
    print(f"Phase 3: Configure HA via WAPI ({active_ip})")
    print("=" * 60)

    members = wapi(active_ip, "GET", "member")
    if not isinstance(members, list) or not members:
        print(f"  FAIL: cannot list members: {members}")
        return False

    ref = members[0]["_ref"]
    print(f"  Member ref: {ref}")

    detail = wapi(active_ip, "GET",
        f"{ref}?_return_fields="
        "host_name,enable_ha,router_id,vip_setting,node_info,config_addr_type"
    )
    print(f"  Current state:\n{json.dumps(detail, indent=2)}")

    ha_config = {
        "enable_ha": True,
        "router_id": VRID,
        "vip_setting": {
            "address": VIP,
            "subnet_mask": NETMASK,
            "gateway": GATEWAY,
        },
        "node_info": [
            {
                "ha_ip_address": HA_ACTIVE,
                "lan_ha_port_setting": {
                    "ha_ip_address": HA_ACTIVE,
                    "mgmt_lan": active_ip,
                },
            },
            {
                "ha_ip_address": HA_PASSIVE,
                "lan_ha_port_setting": {
                    "ha_ip_address": HA_PASSIVE,
                    "mgmt_lan": passive_ip,
                },
            },
        ],
    }

    print(f"\n  Sending HA config:\n{json.dumps(ha_config, indent=2)}")
    result = wapi(active_ip, "PUT", ref, ha_config)
    print(f"\n  Response: {result}")

    err = str(result)
    if "Error" in err or "error" in err:
        print("\n  HA configuration rejected by WAPI.")
        print("  Run with --discover to inspect member schema.")
        print("  The WAPI field names may differ from what this PoC assumes.")
        return False

    print(f"\n  HA config accepted — {ACTIVE_SLOT} restarting...")
    print(f"  Waiting {NIOS_REBOOT_TIMEOUT}s for reboot...")
    time.sleep(NIOS_REBOOT_TIMEOUT)

    add_host_route(VIP)
    add_host_route(active_ip)

    print(f"  Polling WAPI at VIP ({VIP})...")
    if wait_wapi(VIP):
        print(f"  WAPI responding at VIP {VIP}")
        return True

    print(f"  VIP not responding — trying active LAN IP ({active_ip})...")
    if wait_wapi(active_ip):
        print(f"  WAPI responding at {active_ip}")
        return True

    print("  FAIL: WAPI did not come back after HA config")
    return False


def phase_4():
    print("\n" + "=" * 60)
    print(f"Phase 4: set_network on {PASSIVE_SLOT}")
    print("=" * 60)

    c = container_for(PASSIVE_SLOT)
    ip = c.set_network()
    print(f"  {PASSIVE_SLOT} ready at {ip}")
    return ip


def phase_5():
    print("\n" + "=" * 60)
    print(f"Phase 5: {PASSIVE_SLOT} set membership → VIP {VIP}")
    print("=" * 60)

    c = container_for(PASSIVE_SLOT)

    with c.console_session(timeout=120) as console:
        console.sendline("set membership")
        console.expect("Enter New Grid Master VIP")
        console.sendline(VIP)

        # Grid Name prompt may include a default
        console.expect("Enter Grid Name")
        console.sendline(GRID_NAME)

        console.expect("Enter Grid Shared Secret")
        console.sendline(GRID_SECRET)

        # Optional management port prompt
        idx = console.expect_one_of([
            "Enable grid services on the Management port",
            "Is this correct",
        ])
        if idx == 0:
            console.sendline("n")
            console.expect("Is this correct")

        console.sendline("y")
        console.expect("Are you sure")
        console.sendline("y")

    print(f"  {PASSIVE_SLOT} joining grid — rebooting...")
    print(f"  Waiting {NIOS_REBOOT_TIMEOUT}s for reboot...")
    time.sleep(NIOS_REBOOT_TIMEOUT)

    add_host_route(VIP)
    print(f"  Polling WAPI at VIP ({VIP})...")
    if wait_wapi(VIP, timeout=NIOS_WAPI_READY_TIMEOUT):
        print(f"  Grid WAPI responding at {VIP}")
    else:
        print("  WARNING: VIP not responding — grid may still be syncing")
        print(
            f"  Check manually: curl -sk -u {NIOS_CONSOLE_USER}:{NIOS_CONSOLE_PASSWORD} "
            f"https://{VIP}/wapi/{WAPI_VERSION}/grid"
        )


def phase_6():
    print("\n" + "=" * 60)
    print("Phase 6: Verify HA pair")
    print("=" * 60)

    add_host_route(VIP)

    members = wapi(VIP, "GET",
        "member?_return_fields=host_name,enable_ha,node_info,service_status")
    if isinstance(members, list):
        for m in members:
            print(f"\n  {m.get('host_name', '?')}:")
            print(f"    HA enabled: {m.get('enable_ha')}")
            print(f"    Status: {m.get('service_status')}")
            if m.get("node_info"):
                for i, node in enumerate(m["node_info"]):
                    print(f"    Node {i}: {json.dumps(node, indent=6)}")
    else:
        print(f"  Members response: {members}")

    grid = wapi(VIP, "GET", "grid")
    print(f"\n  Grid: {json.dumps(grid, indent=2)}")


def discover(ip):
    print(f"\n=== WAPI member schema discovery ({ip}) ===")

    add_host_route(ip)

    schema = wapi(ip, "GET", "member?_schema")
    if isinstance(schema, dict):
        fields = schema.get("fields", [])
        ha_keywords = ["ha", "vip", "node", "router", "failover", "pair", "vrrp"]
        ha_fields = [
            f for f in fields
            if any(k in f.get("name", "").lower() for k in ha_keywords)
        ]
        print(f"\n  HA-related fields ({len(ha_fields)}/{len(fields)} total):")
        for f in sorted(ha_fields, key=lambda x: x["name"]):
            supports = ",".join(f.get("supports", []))
            print(f"    {f['name']:<40} {f.get('type', '?'):<15} [{supports}]")
            if f.get("schema"):
                for sf in f["schema"].get("fields", []):
                    print(f"      .{sf['name']:<38} {sf.get('type', '?')}")
    else:
        print(f"  Schema response: {schema}")

    members = wapi(ip, "GET", "member?_return_fields+")
    if isinstance(members, list) and members:
        print("\n  Full member object (all fields):")
        print(json.dumps(members[0], indent=2))


# ---- main ----

def main():
    parser = argparse.ArgumentParser(description="HA pair from two local NCC containers")
    parser.add_argument("--phase", type=int, default=0, help="Start from this phase (0-6)")
    parser.add_argument("--discover", action="store_true", help="Dump WAPI member schema and exit")
    parser.add_argument("--active-ip", help="Active node LAN IP (skip phases 0-2)")
    parser.add_argument("--passive-ip", help="Passive node LAN IP (skip phase 4)")
    args = parser.parse_args()

    if args.discover:
        ip = args.active_ip or get_lan_ip(ACTIVE_SLOT)
        discover(ip)
        return

    start = args.phase
    active_ip = args.active_ip
    passive_ip = args.passive_ip

    if start <= 0:
        active_ip, passive_ip = phase_0()

    if start <= 1:
        phase_1()

    if start <= 2 and not active_ip:
        active_ip = phase_2()

    if start <= 3:
        if not active_ip:
            active_ip = get_lan_ip(ACTIVE_SLOT)
        if not passive_ip:
            passive_ip = get_lan_ip(PASSIVE_SLOT)
        if not phase_3(active_ip, passive_ip):
            print("\nStopping — fix HA config and re-run with --phase 3")
            sys.exit(1)

    if start <= 4 and not passive_ip:
        passive_ip = phase_4()

    if start <= 5:
        phase_5()

    if start <= 6:
        phase_6()

    print("\n" + "=" * 60)
    print("Done. If both nodes show ONLINE, the HA pair is working.")
    print(f"VIP: {VIP}  VRID: {VRID}")
    print(f"Active: {ACTIVE_SLOT}  Passive: {PASSIVE_SLOT}")
    print("=" * 60)


if __name__ == "__main__":
    main()
