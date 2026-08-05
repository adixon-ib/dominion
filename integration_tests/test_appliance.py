"""Appliance layer integration tests.

Tests Appliance/LocalAppliance session/deploy forwarding against real
targets. Config validation, from_config() wiring, and repr live in
unit_tests/test_appliance.py (no I/O needed for those).

Usage:
    pytest integration_tests/test_appliance.py -m ncc
    pytest integration_tests/test_appliance.py -m appliance
"""

import pytest


# -- NCC via LocalAppliance (balerion) ----------------------------------------

@pytest.mark.ncc
def test_local_appliance_shell_session(local_appliance):
    with local_appliance.shell_session() as shell:
        out = shell.run("echo hello_appliance_test")
        assert "hello_appliance_test" in out


@pytest.mark.ncc
def test_local_appliance_console_session(local_appliance):
    with local_appliance.console_session() as console:
        out = console.run("show status")
        assert out.strip(), "Empty show status output"


# -- SSH Appliance (vm-04-89) -------------------------------------------------

@pytest.mark.appliance
def test_ssh_appliance_shell_session(ssh_appliance):
    with ssh_appliance.shell_session() as shell:
        out = shell.run("echo hello_appliance_test")
        assert "hello_appliance_test" in out


@pytest.mark.appliance
def test_ssh_appliance_console_session(ssh_appliance):
    with ssh_appliance.console_session() as console:
        out = console.run("show status")
        assert out.strip(), "Empty show status output"
