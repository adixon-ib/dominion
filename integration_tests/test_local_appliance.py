"""Local appliance (NCC set_network) integration tests.

Tests NiosContainer.get_container_ip() and LocalAppliance construction against
balerion.

WARNING: If the network is not yet configured, constructing the
`local_networked_appliance` fixture is destructive and slow (~5-7 minutes):
  - Reconfigures NIOS networking via serial console (set network)
  - Waits 300s for NIOS to reboot
  - Polls WAPI until NIOS is back up (~60s additional)

If the network is already configured (WAPI responds at the ncc_lan1 IP), the
set_network step is automatically skipped and construction completes
immediately.

Destructive — excluded by default. Usage:

    pytest integration_tests/test_local_appliance.py -m destructive

Revert: slot stop balerion && slot start balerion
"""

import re
import subprocess

import pytest
from helpers import WAPI_VERSION
from nios_test import LocalDockerTransport

pytestmark = [pytest.mark.destructive, pytest.mark.ncc]

_VALID_IP = re.compile(r"^\d{1,3}(\.\d{1,3}){3}$")


def test_get_container_ip(local_ncc_container):
    ip = local_ncc_container.get_container_ip()
    assert _VALID_IP.match(ip), f"Not a valid IPv4: {ip!r}"


def test_appliance_ip(local_networked_appliance):
    assert _VALID_IP.match(local_networked_appliance.ip), \
        f"Not a valid IPv4: {local_networked_appliance.ip!r}"
    assert isinstance(
        local_networked_appliance.container._transport, LocalDockerTransport,
    ), "Expected LocalDockerTransport"


def test_host_route(local_networked_appliance):
    result = subprocess.run(
        ["ip", "route", "show", f"{local_networked_appliance.ip}/32"],
        capture_output=True, text=True, check=False,
    )
    assert local_networked_appliance.ip in result.stdout, \
        f"No host route found for {local_networked_appliance.ip}"


def test_wapi_from_host(local_networked_appliance):
    result = subprocess.run(
        ["curl", "-sk", "-u", "admin:infoblox",
         f"https://{local_networked_appliance.ip}/wapi/{WAPI_VERSION}/grid"],
        capture_output=True, text=True, timeout=30, check=False,
    )
    assert result.returncode == 0, f"WAPI unreachable at {local_networked_appliance.ip}"


def test_shell_via_appliance(local_networked_appliance):
    with local_networked_appliance.shell_session() as shell:
        out = shell.run("echo hello_appliance")
        assert "hello_appliance" in out


def test_console_via_appliance(local_networked_appliance):
    with local_networked_appliance.console_session() as console:
        out = console.run("show network")
        assert local_networked_appliance.ip in out, (
            f"Expected {local_networked_appliance.ip!r} in show network output, "
            f"got: {out!r}"
        )


def test_deploy_token_py_on_appliance(local_networked_appliance):
    result = local_networked_appliance.deploy_token_py(groups={"pyabs", "wapi"})
    assert result.artifact == "token_py"
    assert result.details.get("files"), "No files in deploy result"
    with local_networked_appliance.shell_session() as shell:
        out = shell.run("ls /infoblox/one/lib/python/infoblox/one/grid_token_pool.py")
        assert "grid_token_pool.py" in out, f"Spot-check file missing: {out!r}"
