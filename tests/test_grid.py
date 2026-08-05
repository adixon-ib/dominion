#!/usr/bin/env python3
"""Grid deployment integration tests.

Tests Grid.from_config() against real lab VMs. Runs grid_deploy as a subprocess
and verifies the returned Appliance objects are usable.

Usage:
    IMAGE_FILE=nios-9.2.0-ddi.ova python3 test_grid.py

If IMAGE_FILE is not set, all deployment tests are skipped.
"""

import os
import re
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from helpers import (
    DEFAULT_CHROOT,
    DEFAULT_WORKTREE,
    LOG_LEVEL,
    OUTPUT_DIR,
    WAPI_VERSION,
    skip,
    summary,
    test,
)
from nios_test import Appliance, Grid, GridConfig, SSHDockerTransport

_VALID_IP = re.compile(r"^\d{1,3}(\.\d{1,3}){3}$")

IMAGE_FILE = os.environ.get("IMAGE_FILE")
_grid: Grid | None = None

if not IMAGE_FILE:
    skip("standalone grid deploy (vm-02-89)", "IMAGE_FILE env var not set")
else:
    config = GridConfig(
        topology="standalone",
        member="vm-02-89",
        file=IMAGE_FILE,
        chroot=DEFAULT_CHROOT,
        worktree=DEFAULT_WORKTREE,
        log_level=LOG_LEVEL,
        output_dir=OUTPUT_DIR,
    )
    _grid = Grid.from_config(config)

    def test_grid_structure():
        assert _grid.gm is not None, "No GM appliance returned"
        assert isinstance(_grid.gm, Appliance), f"Expected Appliance, got {type(_grid.gm)}"
        assert _grid.members == [], f"Standalone should have no members, got {_grid.members}"
        assert len(_grid.appliances) == 1
        print(f"  grid: {_grid!r}")

    def test_gm_has_valid_ip():
        ip = _grid.gm.ip
        print(f"  ip: {ip}")
        assert ip is not None, "GM has no IP"
        assert _VALID_IP.match(ip), f"Not a valid IPv4: {ip!r}"

    def test_gm_has_ssh_transport():
        transport = _grid.gm.container._transport
        print(f"  transport: {type(transport).__name__}")
        assert isinstance(transport, SSHDockerTransport), \
            f"Expected SSHDockerTransport, got {type(transport).__name__}"
        assert transport.host == _grid.gm.ip

    def test_gm_shell_session():
        with _grid.gm.shell_session() as shell:
            out = shell.run("echo hello_grid")
            print(f"  output: {out!r}")
            assert "hello_grid" in out

    def test_gm_wapi_reachable():
        result = subprocess.run(
            ["curl", "-sk", "-u", "admin:infoblox",
             f"https://{_grid.gm.ip}/wapi/{WAPI_VERSION}/grid"],
            capture_output=True, text=True, timeout=30,
        )
        print(f"  exit={result.returncode} response={result.stdout[:80]!r}")
        assert result.returncode == 0, f"WAPI unreachable at {_grid.gm.ip}"

    def test_gm_console_session():
        with _grid.gm.console_session() as console:
            out = console.run("show network")
            print(f"  show network (first 80): {out[:80]!r}")
            assert _grid.gm.ip in out, \
                f"Expected {_grid.gm.ip!r} in show network output"

    test("grid structure: gm set, no members, 1 appliance", test_grid_structure)
    test("GM has valid IPv4", test_gm_has_valid_ip)
    test("GM has SSHDockerTransport", test_gm_has_ssh_transport)
    test("GM shell session: echo", test_gm_shell_session)
    test("GM WAPI reachable", test_gm_wapi_reachable)
    test("GM console session: show network", test_gm_console_session)


summary()
