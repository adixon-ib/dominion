#!/usr/bin/env python3
"""Container layer integration tests.

Tests ContainerConfig/LocalContainerConfig validation and NiosContainer/
LocalNiosContainer construction, transport routing, logger naming, session
creation, and repr — via both local and SSH transports.

Usage:
    python3 -m nios_test.tests.test_container [IP]
"""

import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from helpers import (
    APPLIANCE_DOWN_REASON,
    DEFAULT_CHROOT,
    NCC_DOWN_REASON,
    container_config,
    is_appliance_up,
    is_ncc_up,
    local_container_config,
    should_run_appliance,
    should_run_ncc,
    skip,
    summary,
    test,
)
from nios_test import (
    ContainerConfig,
    LocalContainerConfig,
    LocalDockerTransport,
    LocalNiosContainer,
    NiosContainer,
    SSHDockerTransport,
)


# -- ContainerConfig validation (unit tests) ----------------------------------

def test_config_ssh_requires_host():
    cfg = ContainerConfig(container_name="nios", host="10.35.211.4")
    assert cfg.host == "10.35.211.4"
    print(f"  host: {cfg.host}")


def test_config_output_dir_resolves_log_file():
    with tempfile.TemporaryDirectory() as d:
        cfg = LocalContainerConfig(container_name="testbox", output_dir=d)
        print(f"  log_file: {cfg.log_file}")
        assert cfg.log_file is not None, "log_file should be set"
        assert d in cfg.log_file, f"log_file not under output_dir: {cfg.log_file}"
        assert "testbox" in cfg.log_file, f"name missing from log_file: {cfg.log_file}"


def test_from_config_local_creates_local_transport():
    cfg = LocalContainerConfig(container_name="balerion", chroot=DEFAULT_CHROOT)
    container = LocalNiosContainer.from_config(cfg)
    print(f"  transport: {type(container._transport).__name__}")
    assert isinstance(container._transport, LocalDockerTransport)
    assert container._transport.container_name == "balerion"


def test_from_config_ssh_creates_ssh_transport():
    cfg = ContainerConfig(
        container_name="nios",
        host="10.35.211.4",
        chroot=DEFAULT_CHROOT,
    )
    container = NiosContainer.from_config(cfg)
    print(f"  transport: {type(container._transport).__name__}")
    print(f"  host: {container._transport.host}")
    assert isinstance(container._transport, SSHDockerTransport)
    assert container._transport.host == "10.35.211.4"


def test_from_config_logger_name():
    cfg = LocalContainerConfig(container_name="balerion", chroot=DEFAULT_CHROOT)
    container = LocalNiosContainer.from_config(cfg)
    print(f"  logger.name: {container._logger.name}")
    assert container._logger.name == "nios_test.balerion"


def test_local_nios_container_repr():
    cfg = LocalContainerConfig(container_name="balerion", chroot=DEFAULT_CHROOT)
    container = LocalNiosContainer.from_config(cfg)
    r = repr(container)
    print(f"  repr: {r}")
    assert "balerion" in r
    assert "Local" in r


def test_local_nios_container_is_nios_container():
    cfg = LocalContainerConfig(container_name="balerion", chroot=DEFAULT_CHROOT)
    container = LocalNiosContainer.from_config(cfg)
    assert isinstance(container, NiosContainer)
    print(f"  isinstance(LocalNiosContainer, NiosContainer): True")


test("ContainerConfig: SSH config stores host", test_config_ssh_requires_host)
test("LocalContainerConfig: output_dir resolves to log_file", test_config_output_dir_resolves_log_file)
test("LocalNiosContainer.from_config() → LocalDockerTransport", test_from_config_local_creates_local_transport)
test("NiosContainer.from_config() → SSHDockerTransport", test_from_config_ssh_creates_ssh_transport)
test("LocalNiosContainer.from_config(): logger name is nios_test.<container_name>", test_from_config_logger_name)
test("LocalNiosContainer repr includes container_name and 'Local'", test_local_nios_container_repr)
test("LocalNiosContainer is instance of NiosContainer", test_local_nios_container_is_nios_container)


# -- NCC (balerion) -----------------------------------------------------------

if should_run_ncc() and is_ncc_up():
    local_container = LocalNiosContainer.from_config(local_container_config())

    def test_local_shell_session():
        with local_container.shell_session() as shell:
            out = shell.run("echo hello_container_test")
            assert "hello_container_test" in out

    def test_local_console_session():
        with local_container.console_session() as console:
            out = console.run("show status")
            assert out.strip(), "Empty show status output"

    test("LocalNiosContainer.shell_session() (local)", test_local_shell_session)
    test("LocalNiosContainer.console_session() (local)", test_local_console_session)
elif should_run_ncc():
    skip("LocalNiosContainer sessions (local)", NCC_DOWN_REASON)
else:
    skip("LocalNiosContainer sessions (local)", "target is not ncc")


# -- SSH via NiosContainer directly (vm-04-89) --------------------------------

if should_run_appliance() and is_appliance_up():
    ssh_container = NiosContainer.from_config(container_config(log_level="verbose"))

    def test_ssh_shell_session():
        with ssh_container.shell_session() as shell:
            out = shell.run("echo hello_container_test")
            assert "hello_container_test" in out

    def test_ssh_console_session():
        with ssh_container.console_session() as console:
            out = console.run("show status")
            assert out.strip(), "Empty show status output"

    test("NiosContainer.shell_session() (SSH)", test_ssh_shell_session)
    test("NiosContainer.console_session() (SSH)", test_ssh_console_session)
elif should_run_appliance():
    skip("NiosContainer sessions (SSH)", APPLIANCE_DOWN_REASON)
else:
    skip("NiosContainer sessions (SSH)", "target is not appliance")


summary()
