"""Session layer integration tests.

Tests ShellSession and ConsoleSession via both SSH and local transports.

Usage:
    pytest integration_tests/test_session.py -m appliance
    pytest integration_tests/test_session.py -m ncc
"""

import pytest

# -- SSH sessions (vm-04-89) ---------------------------------------------------

@pytest.mark.appliance
def test_ssh_shell_echo(ssh_appliance):
    with ssh_appliance.shell_session() as shell:
        out = shell.run("echo hello_nios_test")
        assert "hello_nios_test" in out, f"Expected echo string, got {out!r}"


@pytest.mark.appliance
def test_ssh_shell_hostname(ssh_appliance):
    with ssh_appliance.shell_session() as shell:
        out = shell.run("cat /etc/hostname")
        assert out.strip(), "Empty hostname"


@pytest.mark.appliance
def test_ssh_console(ssh_appliance):
    with ssh_appliance.console_session() as console:
        out = console.run("show status")
        assert out.strip(), "Empty show status output"


# -- Local sessions (balerion) -------------------------------------------------

@pytest.mark.ncc
def test_local_shell_echo(local_ncc_container):
    with local_ncc_container.shell_session() as shell:
        out = shell.run("echo hello_nios_test")
        assert "hello_nios_test" in out, f"Expected echo string, got {out!r}"


@pytest.mark.ncc
def test_local_console(local_ncc_container):
    with local_ncc_container.console_session() as console:
        out = console.run("show status")
        assert out.strip(), "Empty show status output"
