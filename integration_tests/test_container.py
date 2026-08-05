"""Container layer integration tests.

Tests NiosContainer/LocalNiosContainer session creation via both local and
SSH transports, against real targets. Config validation and from_config()
wiring live in unit_tests/test_container.py (no I/O needed for those).

Usage:
    pytest integration_tests/test_container.py -m ncc
    pytest integration_tests/test_container.py -m appliance
"""

import pytest

# -- NCC (balerion) -----------------------------------------------------------

@pytest.mark.ncc
def test_local_shell_session(local_ncc_container):
    with local_ncc_container.shell_session() as shell:
        out = shell.run("echo hello_container_test")
        assert "hello_container_test" in out


@pytest.mark.ncc
def test_local_console_session(local_ncc_container):
    with local_ncc_container.console_session() as console:
        out = console.run("show status")
        assert out.strip(), "Empty show status output"


# -- SSH via NiosContainer directly (vm-04-89) --------------------------------

@pytest.mark.appliance
def test_ssh_shell_session(ssh_container):
    with ssh_container.shell_session() as shell:
        out = shell.run("echo hello_container_test")
        assert "hello_container_test" in out


@pytest.mark.appliance
def test_ssh_console_session(ssh_container):
    with ssh_container.console_session() as console:
        out = console.run("show status")
        assert out.strip(), "Empty show status output"
