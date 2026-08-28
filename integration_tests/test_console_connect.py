"""Integration tests for console_connect_session on Appliance.

Tests the out-of-band console connection via lab console server.
Uses vm-48-10, a standalone NIOS 9.0.x appliance.

Usage:
    pytest integration_tests/test_console_connect.py -m console_connect
    pytest integration_tests/test_console_connect.py -m console_connect_reboot
"""

import subprocess
import time

import pytest

from dominion import Appliance, ApplianceConfig

HOSTNAME = "vm-48-10"
IP = "10.35.131.48"


@pytest.fixture(scope="module")
def appliance():
    return Appliance.from_config(ApplianceConfig(hostname=HOSTNAME, ip=IP))


@pytest.mark.console_connect
def test_console_connect_session_opens_and_closes(appliance):
    with appliance.console_connect_session() as console:
        assert console is not None


@pytest.mark.console_connect
def test_show_license(appliance):
    with appliance.console_connect_session() as console:
        output = console.run("show license")
        assert "License Type" in output, f"Missing 'License Type' in: {output!r}"
        assert "Expiration Date" in output, f"Missing 'Expiration Date' in: {output!r}"
        assert "License String" in output, f"Missing 'License String' in: {output!r}"
        assert "Hardware ID" in output, f"Missing 'Hardware ID' in: {output!r}"
        assert "Version" in output, f"Missing 'Version' in: {output!r}"


@pytest.mark.console_connect
def test_show_status(appliance):
    with appliance.console_connect_session() as console:
        output = console.run("show status")
        assert "Grid Status" in output, f"Missing 'Grid Status' in: {output!r}"
        assert "HA Status" in output, f"Missing 'HA Status' in: {output!r}"
        assert "Hostname" in output, f"Missing 'Hostname' in: {output!r}"


@pytest.mark.console_connect
def test_multiple_commands_in_one_session(appliance):
    with appliance.console_connect_session() as console:
        status = console.run("show status")
        assert "Grid Status" in status

        license_out = console.run("show license")
        assert "License Type" in license_out


@pytest.mark.console_connect_reboot
def test_console_connect_after_power_cycle(appliance):
    """Power-cycle VM, reconnect via console_connect_session, verify NIOS boots."""
    with appliance.console_connect_session() as console:
        output = console.run("show status")
        assert "Grid Status" in output, "Appliance not up before power cycle"

    subprocess.run(
        ["reboot_system", "-H", HOSTNAME, "-a", "poweroff"],
        timeout=30, check=True,
    )
    time.sleep(5)
    subprocess.run(
        ["reboot_system", "-H", HOSTNAME, "-a", "poweron"],
        timeout=30, check=True,
    )

    with appliance.console_connect_session(timeout=300) as console:
        output = console.run("show status")
        assert "Grid Status" in output
