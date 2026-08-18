"""Grid deployment integration tests.

Tests Grid.from_config() against real lab VMs. Runs grid_deploy as a
subprocess and verifies the returned Appliance objects are usable.

Destructive (deploys a real grid to vm-02-89) — excluded by default. Usage:

    IMAGE_FILE=nios-9.2.0-ddi.ova pytest integration_tests/test_grid.py -m destructive

If IMAGE_FILE is not set, the whole file is skipped.
"""

import os
import re
import subprocess

import pytest
from helpers import DEFAULT_CHROOT, DEFAULT_WORKTREE, LOG_LEVEL, WAPI_VERSION
from nios_test import Appliance, Grid, GridConfig, SSHDockerTransport

pytestmark = pytest.mark.destructive

_VALID_IP = re.compile(r"^\d{1,3}(\.\d{1,3}){3}$")


@pytest.fixture(scope="module")
def grid(output_dir):
    image_file = os.environ.get("IMAGE_FILE")
    if not image_file:
        pytest.skip("IMAGE_FILE env var not set")
    config = GridConfig(
        topology="standalone",
        member="vm-02-89",
        file=image_file,
        chroot=DEFAULT_CHROOT,
        worktree=DEFAULT_WORKTREE,
        log_level=LOG_LEVEL,
        output_dir=output_dir,
    )
    return Grid.from_config(config)


def test_grid_structure(grid):
    assert grid.gm is not None, "No GM appliance returned"
    assert isinstance(grid.gm, Appliance), f"Expected Appliance, got {type(grid.gm)}"
    assert grid.members == [], f"Standalone should have no members, got {grid.members}"
    assert len(grid.appliances) == 1


def test_gm_has_valid_ip(grid):
    ip = grid.gm.ip
    assert ip is not None, "GM has no IP"
    assert _VALID_IP.match(ip), f"Not a valid IPv4: {ip!r}"


def test_gm_has_ssh_transport(grid):
    transport = grid.gm.container._transport
    assert isinstance(transport, SSHDockerTransport), \
        f"Expected SSHDockerTransport, got {type(transport).__name__}"
    assert transport.host == grid.gm.ip


def test_gm_shell_session(grid):
    with grid.gm.shell_session() as shell:
        out = shell.run("echo hello_grid")
        assert "hello_grid" in out


def test_gm_wapi_reachable(grid):
    result = subprocess.run(
        ["curl", "-sk", "-u", "admin:infoblox",
         f"https://{grid.gm.ip}/wapi/{WAPI_VERSION}/grid"],
        capture_output=True, text=True, timeout=30, check=False,
    )
    assert result.returncode == 0, f"WAPI unreachable at {grid.gm.ip}"


def test_gm_console_session(grid):
    with grid.gm.console_session() as console:
        out = console.run("show network")
        assert grid.gm.ip in out, f"Expected {grid.gm.ip!r} in show network output"
