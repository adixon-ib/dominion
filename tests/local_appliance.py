#!/usr/bin/env python3
"""Local appliance (NCC set_network) integration tests.

Tests NiosContainer.get_container_ip() and LocalAppliance construction against balerion.

WARNING: If the network is not yet configured, LocalAppliance.from_config() is
destructive and slow (~5-7 minutes):
  - Reconfigures NIOS networking via serial console (set network)
  - Waits 300s for NIOS to reboot
  - Polls WAPI until NIOS is back up (~60s additional)

If the network is already configured (WAPI responds at the ncc_lan1 IP), the
set_network step is automatically skipped and construction completes immediately.

After construction the tests verify:
  - Appliance has a valid IP and local transport
  - Host route exists for the NCC IP
  - WAPI is reachable from the host at that IP
  - Shell session works via the Appliance (docker exec)
  - Console session works via the Appliance (docker exec)

Usage:
    python3 local_appliance.py
    NIOS_TEST_OUTPUT_DIR=/other/path python3 local_appliance.py

Revert: slot stop balerion && slot start balerion
"""

import os
import re
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from helpers import (
    get_ncc_ip,
    local_appliance_config,
    local_container_config,
    skip,
    summary,
    test,
    WAPI_VERSION,
)
from nios_test import LocalAppliance, LocalDockerTransport, LocalNiosContainer

_VALID_IP = re.compile(r"^\d{1,3}(\.\d{1,3}){3}$")

_appliance: LocalAppliance | None = None

_container = LocalNiosContainer.from_config(local_container_config())

try:
    _ncc_ip = get_ncc_ip()
    if _ncc_ip:
        print(f"Network already configured at {_ncc_ip} — skipping set_network")
    else:
        print("WAPI not responding — running set_network (~5-7 min)")
    _appliance = LocalAppliance.from_config(local_appliance_config(ip=_ncc_ip))
except Exception:
    pass


def test_get_container_ip():
    ip = _container.get_container_ip()
    print(f"  ncc_lan1 IP: {ip}")
    assert _VALID_IP.match(ip), f"Not a valid IPv4: {ip!r}"


def test_appliance_ip():
    assert _appliance, "LocalAppliance was not created successfully"
    print(f"  ip: {_appliance.ip}")
    print(f"  repr: {_appliance!r}")
    assert _VALID_IP.match(_appliance.ip), f"Not a valid IPv4: {_appliance.ip!r}"
    assert isinstance(_appliance.container._transport, LocalDockerTransport), \
        "Expected LocalDockerTransport"


def test_host_route():
    assert _appliance, "LocalAppliance was not created successfully"
    result = subprocess.run(
        ["ip", "route", "show", f"{_appliance.ip}/32"],
        capture_output=True, text=True,
    )
    print(f"  route: {result.stdout.strip()!r}")
    assert _appliance.ip in result.stdout, f"No host route found for {_appliance.ip}"


def test_wapi_from_host():
    assert _appliance, "LocalAppliance was not created successfully"
    result = subprocess.run(
        ["curl", "-sk", "-u", "admin:infoblox",
         f"https://{_appliance.ip}/wapi/{WAPI_VERSION}/grid"],
        capture_output=True, text=True, timeout=30,
    )
    print(f"  exit={result.returncode} response={result.stdout[:80]!r}")
    assert result.returncode == 0, f"WAPI unreachable at {_appliance.ip}"


def test_shell_via_appliance_appliance():
    assert _appliance, "LocalAppliance was not created successfully"
    with _appliance.shell_session() as shell:
        out = shell.run("echo hello_appliance")
        assert "hello_appliance" in out


def test_console_via_appliance_appliance():
    assert _appliance, "LocalAppliance was not created successfully"
    with _appliance.console_session() as console:
        out = console.run("show network")
        assert _appliance.ip in out, \
            f"Expected {_appliance.ip!r} in show network output, got: {out!r}"


def test_deploy_token_py_on_appliance():
    assert _appliance, "LocalAppliance was not created successfully"
    result = _appliance.deploy_token_py(groups={"pyabs", "wapi"})
    print(f"  artifact: {result.artifact}")
    print(f"  files:    {len(result.details.get('files', []))}")
    print(f"  groups:   {result.details.get('groups')}")
    assert result.artifact == "token_py"
    assert result.details.get("files"), "No files in deploy result"
    with _appliance.shell_session() as shell:
        out = shell.run("ls /infoblox/one/lib/python/infoblox/one/grid_token_pool.py")
        assert "grid_token_pool.py" in out, f"Spot-check file missing: {out!r}"


test("get_container_ip()", test_get_container_ip)
test("networked appliance has valid IP and local transport", test_appliance_ip)
test("host route exists for NCC IP", test_host_route)
test("WAPI reachable from host at NCC IP", test_wapi_from_host)
test("Appliance.shell_session() on networked NCC", test_shell_via_appliance_appliance)
test("Appliance.console_session(): show network contains IP", test_console_via_appliance_appliance)
test("deploy_token_py pyabs+wapi on networked NCC", test_deploy_token_py_on_appliance)

summary()
