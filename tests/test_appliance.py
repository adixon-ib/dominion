#!/usr/bin/env python3
"""Appliance layer integration tests.

Tests ApplianceConfig/LocalApplianceConfig validation and Appliance/LocalAppliance
construction, transport routing, session/deploy forwarding, and repr.

Usage:
    python3 -m nios_test.tests.test_appliance [IP]
"""

import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from helpers import (
    APPLIANCE_DOWN_REASON,
    DEFAULT_CHROOT,
    DEFAULT_CONTAINER,
    DEFAULT_HOSTNAME,
    DEFAULT_IP,
    NCC_DOWN_REASON,
    appliance_config,
    get_ncc_ip,
    is_appliance_up,
    is_ncc_up,
    local_appliance_config,
    should_run_appliance,
    should_run_ncc,
    skip,
    summary,
    test,
)
from nios_test import (
    Appliance,
    ApplianceConfig,
    LocalAppliance,
    LocalApplianceConfig,
    LocalDockerTransport,
    LocalNiosContainer,
    SSHDockerTransport,
)


# -- ApplianceConfig validation (unit tests) ----------------------------------

def test_config_defaults_container_name_to_nios():
    cfg = ApplianceConfig(hostname=DEFAULT_HOSTNAME, ip=DEFAULT_IP)
    print(f"  container_name: {cfg.container_name!r}")
    assert cfg.container_name == "nios", f"Expected 'nios', got {cfg.container_name!r}"


def test_config_output_dir_resolves_log_file():
    with tempfile.TemporaryDirectory() as d:
        cfg = ApplianceConfig(hostname=DEFAULT_HOSTNAME, ip=DEFAULT_IP, output_dir=d)
        print(f"  log_file: {cfg.log_file}")
        assert cfg.log_file is not None, "log_file should be set"
        assert d in cfg.log_file, f"log_file not under output_dir: {cfg.log_file}"
        assert DEFAULT_HOSTNAME in cfg.log_file, f"hostname missing from log_file: {cfg.log_file}"


def test_local_appliance_config_output_dir_resolves_log_file():
    with tempfile.TemporaryDirectory() as d:
        cfg = LocalApplianceConfig(container_name=DEFAULT_CONTAINER, output_dir=d)
        print(f"  log_file: {cfg.log_file}")
        assert cfg.log_file is not None, "log_file should be set"
        assert d in cfg.log_file, f"log_file not under output_dir: {cfg.log_file}"
        assert DEFAULT_CONTAINER in cfg.log_file, f"container_name missing from log_file: {cfg.log_file}"


def test_appliance_from_config_creates_ssh_transport():
    cfg = ApplianceConfig(hostname=DEFAULT_HOSTNAME, ip=DEFAULT_IP, chroot=DEFAULT_CHROOT)
    appliance = Appliance.from_config(cfg)
    transport = appliance.container._transport
    print(f"  transport: {type(transport).__name__}")
    print(f"  host: {transport.host}")
    assert isinstance(transport, SSHDockerTransport)
    assert transport.host == DEFAULT_IP


def test_local_appliance_from_config_creates_local_transport():
    cfg = LocalApplianceConfig(
        container_name=DEFAULT_CONTAINER,
        chroot=DEFAULT_CHROOT,
        ip=DEFAULT_IP,
    )
    appliance = LocalAppliance.from_config(cfg)
    transport = appliance.container._transport
    print(f"  transport: {type(transport).__name__}")
    print(f"  container type: {type(appliance.container).__name__}")
    assert isinstance(transport, LocalDockerTransport)
    assert transport.container_name == DEFAULT_CONTAINER
    assert isinstance(appliance.container, LocalNiosContainer)


def test_appliance_repr():
    cfg = ApplianceConfig(hostname=DEFAULT_HOSTNAME, ip=DEFAULT_IP)
    appliance = Appliance.from_config(cfg)
    r = repr(appliance)
    print(f"  repr: {r}")
    assert DEFAULT_HOSTNAME in r
    assert "nios" in r


def test_local_appliance_repr():
    cfg = LocalApplianceConfig(container_name=DEFAULT_CONTAINER, chroot=DEFAULT_CHROOT, ip=DEFAULT_IP)
    appliance = LocalAppliance.from_config(cfg)
    r = repr(appliance)
    print(f"  repr: {r}")
    assert DEFAULT_CONTAINER in r


test("ApplianceConfig: defaults container_name to 'nios'", test_config_defaults_container_name_to_nios)
test("ApplianceConfig: output_dir resolves to log_file", test_config_output_dir_resolves_log_file)
test("LocalApplianceConfig: output_dir resolves to log_file", test_local_appliance_config_output_dir_resolves_log_file)
test("Appliance.from_config() → SSHDockerTransport", test_appliance_from_config_creates_ssh_transport)
test("LocalAppliance.from_config() → LocalDockerTransport", test_local_appliance_from_config_creates_local_transport)
test("Appliance repr includes hostname and container name", test_appliance_repr)
test("LocalAppliance repr includes container name", test_local_appliance_repr)


# -- NCC via LocalAppliance (balerion) ----------------------------------------

if should_run_ncc() and is_ncc_up():
    local_appliance = LocalAppliance.from_config(local_appliance_config(ip=get_ncc_ip()))

    def test_local_appliance_shell_session():
        with local_appliance.shell_session() as shell:
            out = shell.run("echo hello_appliance_test")
            assert "hello_appliance_test" in out

    def test_local_appliance_console_session():
        with local_appliance.console_session() as console:
            out = console.run("show status")
            assert out.strip(), "Empty show status output"

    test("LocalAppliance.shell_session() (local)", test_local_appliance_shell_session)
    test("LocalAppliance.console_session() (local)", test_local_appliance_console_session)
elif should_run_ncc():
    skip("LocalAppliance sessions (local)", NCC_DOWN_REASON)
else:
    skip("LocalAppliance sessions (local)", "target is not ncc")


# -- SSH Appliance (vm-04-89) -------------------------------------------------

if should_run_appliance() and is_appliance_up():
    ssh_appliance = Appliance.from_config(appliance_config())

    def test_ssh_appliance_shell_session():
        with ssh_appliance.shell_session() as shell:
            out = shell.run("echo hello_appliance_test")
            assert "hello_appliance_test" in out

    def test_ssh_appliance_console_session():
        with ssh_appliance.console_session() as console:
            out = console.run("show status")
            assert out.strip(), "Empty show status output"

    test("Appliance.shell_session() (SSH)", test_ssh_appliance_shell_session)
    test("Appliance.console_session() (SSH)", test_ssh_appliance_console_session)
elif should_run_appliance():
    skip("Appliance sessions (SSH)", APPLIANCE_DOWN_REASON)
else:
    skip("Appliance sessions (SSH)", "target is not appliance")


summary()
