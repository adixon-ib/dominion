#!/usr/bin/env python3
"""Session layer integration tests.

Tests ShellSession and ConsoleSession via both SSH and local transports.

Usage:
    python3 -m nios_test.tests.test_session [IP]
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from helpers import (
    APPLIANCE_DOWN_REASON,
    NCC_DOWN_REASON,
    appliance_config,
    is_appliance_up,
    is_ncc_up,
    local_container_config,
    should_run_appliance,
    should_run_ncc,
    skip,
    summary,
    test,
)
from nios_test import Appliance, LocalNiosContainer


# -- SSH sessions (vm-04-89) ---------------------------------------------------

if should_run_appliance() and is_appliance_up():
    ssh_appliance = Appliance.from_config(appliance_config())

    def test_ssh_shell_echo():
        with ssh_appliance.shell_session() as shell:
            out = shell.run("echo hello_nios_test")
            assert "hello_nios_test" in out, f"Expected echo string, got {out!r}"

    def test_ssh_shell_hostname():
        with ssh_appliance.shell_session() as shell:
            out = shell.run("cat /etc/hostname")
            assert out.strip(), "Empty hostname"

    def test_ssh_console():
        with ssh_appliance.console_session() as console:
            out = console.run("show status")
            assert out.strip(), "Empty show status output"

    test("ssh_shell: echo", test_ssh_shell_echo)
    test("ssh_shell: hostname", test_ssh_shell_hostname)
    test("ssh_console: show status", test_ssh_console)
elif should_run_appliance():
    skip("ssh_session (all)", APPLIANCE_DOWN_REASON)
else:
    skip("ssh_session (all)", "target is not appliance")


# -- Local sessions (balerion) -------------------------------------------------

if should_run_ncc() and is_ncc_up():
    local_container = LocalNiosContainer.from_config(local_container_config())

    def test_local_shell_echo():
        with local_container.shell_session() as shell:
            out = shell.run("echo hello_nios_test")
            assert "hello_nios_test" in out, f"Expected echo string, got {out!r}"

    def test_local_console():
        with local_container.console_session() as console:
            out = console.run("show status")
            assert out.strip(), "Empty show status output"

    test("local_shell: echo", test_local_shell_echo)
    test("local_console: show status", test_local_console)
elif should_run_ncc():
    skip("local_session (all)", NCC_DOWN_REASON)
else:
    skip("local_session (all)", "target is not ncc")

summary()
