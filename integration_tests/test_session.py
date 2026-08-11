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


@pytest.mark.appliance
def test_ssh_shell_run_with_check_success(ssh_appliance):
    with ssh_appliance.shell_session() as shell:
        out = shell.run_with_check("echo hello_nios_test")
        assert "hello_nios_test" in out
        assert shell.last_exit_code == 0


@pytest.mark.appliance
def test_ssh_shell_run_with_check_raises_on_failure(ssh_appliance):
    with ssh_appliance.shell_session() as shell:
        with pytest.raises(RuntimeError, match="rc=1"):
            shell.run_with_check("false")
        assert shell.last_exit_code == 1


@pytest.mark.appliance
def test_ssh_shell_last_exit_code_tracks_pgrep(ssh_appliance):
    """pgrep's nonzero exit here is a normal outcome, not an error. Use
    run(), not run_with_check()."""
    with ssh_appliance.shell_session() as shell:
        out = shell.run("pgrep -f nonexistent_process_xyz")
        assert out == ""
        assert shell.last_exit_code == 1


@pytest.mark.appliance
def test_ssh_console_run_not_corrupted_by_shell_syntax(ssh_appliance):
    """ConsoleSession.run() must not append shell syntax (`; echo ...:$?`)
    to the command. The NIOS CLI isn't a shell, and rejects the whole
    line if it's there."""
    with ssh_appliance.console_session() as console:
        out = console.run("show status")
        assert "unknown show command" not in out.lower()
        assert "Grid Status" in out


@pytest.mark.appliance
def test_ssh_console_has_no_run_with_check(ssh_appliance):
    """ConsoleSession has no exit-code support. The NIOS CLI has no
    shell-style $?. run_with_check() only exists on ShellSession."""
    with ssh_appliance.console_session() as console:
        assert not hasattr(console, "run_with_check")


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


@pytest.mark.ncc
def test_local_shell_run_with_check_success(local_ncc_container):
    with local_ncc_container.shell_session() as shell:
        out = shell.run_with_check("echo hello_nios_test")
        assert "hello_nios_test" in out
        assert shell.last_exit_code == 0


@pytest.mark.ncc
def test_local_shell_run_with_check_raises_on_failure(local_ncc_container):
    with local_ncc_container.shell_session() as shell:
        with pytest.raises(RuntimeError, match="rc=1"):
            shell.run_with_check("false")
        assert shell.last_exit_code == 1


@pytest.mark.ncc
def test_local_shell_last_exit_code_tracks_pgrep(local_ncc_container):
    """pgrep's nonzero exit here is a normal outcome, not an error. Use
    run(), not run_with_check()."""
    with local_ncc_container.shell_session() as shell:
        out = shell.run("pgrep -f nonexistent_process_xyz")
        assert out == ""
        assert shell.last_exit_code == 1


@pytest.mark.ncc
def test_local_console_run_not_corrupted_by_shell_syntax(local_ncc_container):
    """ConsoleSession.run() must not append shell syntax (`; echo ...:$?`)
    to the command. The NIOS CLI isn't a shell, and rejects the whole
    line if it's there."""
    with local_ncc_container.console_session() as console:
        out = console.run("show status")
        assert "unknown show command" not in out.lower()
        assert "Grid Status" in out


@pytest.mark.ncc
def test_local_console_has_no_run_with_check(local_ncc_container):
    """ConsoleSession has no exit-code support. The NIOS CLI has no
    shell-style $?. run_with_check() only exists on ShellSession."""
    with local_ncc_container.console_session() as console:
        assert not hasattr(console, "run_with_check")
