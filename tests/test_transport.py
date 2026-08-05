#!/usr/bin/env python3
"""Transport layer integration tests.

Tests SSHDockerTransport against vm-04-89 and LocalDockerTransport against balerion.

Usage:
    python3 -m nios_test.tests.test_transport [IP]
"""

import os
import sys
import tempfile

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


# -- SSH transport (vm-04-89) --------------------------------------------------

if should_run_appliance() and is_appliance_up():
    ssh_appliance = Appliance.from_config(appliance_config())
    ssh_transport = ssh_appliance.container._transport

    def test_ssh_run():
        code, out, _ = ssh_transport.run("hostname", check=False)
        print(f"  exit={code} hostname={out.strip()!r}")
        assert code == 0, f"Expected exit 0, got {code}"
        assert out.strip(), "Empty hostname"

    def test_ssh_run_on_host():
        code, out, _ = ssh_transport.run_on_host("hostname", check=False)
        print(f"  exit={code} host_hostname={out.strip()!r}")
        assert code == 0, f"Expected exit 0, got {code}"

    def test_ssh_container_running():
        running = ssh_transport.is_container_running()
        print(f"  container running: {running}")
        assert running, "Container not running"

    def test_ssh_checksum():
        md5 = ssh_transport.checksum("/etc/hostname")
        print(f"  md5(/etc/hostname) = {md5}")
        assert len(md5) == 32, f"Bad md5 length: {len(md5)}"

    def test_ssh_host_checksum():
        md5 = ssh_transport.host_checksum("/etc/hostname")
        print(f"  host md5(/etc/hostname) = {md5}")
        assert len(md5) == 32, f"Bad md5 length: {len(md5)}"

    test("ssh_transport.run()", test_ssh_run)
    test("ssh_transport.run_on_host()", test_ssh_run_on_host)
    test("ssh_transport.is_container_running()", test_ssh_container_running)
    test("ssh_transport.checksum()", test_ssh_checksum)
    test("ssh_transport.host_checksum()", test_ssh_host_checksum)
elif should_run_appliance():
    skip("ssh_transport (all)", APPLIANCE_DOWN_REASON)
else:
    skip("ssh_transport (all)", "target is not appliance")


# -- Local transport (balerion) ------------------------------------------------

if should_run_ncc() and is_ncc_up():
    local_container = LocalNiosContainer.from_config(local_container_config())
    local_transport = local_container._transport

    def test_local_run():
        code, out, _ = local_transport.run("hostname", check=False)
        print(f"  exit={code} hostname={out.strip()!r}")
        assert code == 0, f"Expected exit 0, got {code}"
        assert out.strip(), "Empty hostname"

    def test_local_run_on_host():
        code, out, _ = local_transport.run_on_host("hostname", check=False)
        print(f"  exit={code} host_hostname={out.strip()!r}")
        assert code == 0, f"Expected exit 0, got {code}"

    def test_local_container_running():
        running = local_transport.is_container_running()
        print(f"  container running: {running}")
        assert running, "Container not running"

    def test_local_checksum():
        md5 = local_transport.checksum("/etc/hostname")
        print(f"  md5(/etc/hostname) = {md5}")
        assert len(md5) == 32, f"Bad md5 length: {len(md5)}"

    def test_local_host_checksum():
        md5 = local_transport.host_checksum("/etc/hostname")
        print(f"  host md5(/etc/hostname) = {md5}")
        assert len(md5) == 32, f"Bad md5 length: {len(md5)}"

    def test_local_upload():
        marker = "nios_test_upload_check_12345"
        remote_path = "/tmp/_nios_test_upload_check.txt"

        with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False) as f:
            f.write(marker)
            local_path = f.name

        try:
            local_transport.upload(local_path, remote_path)
            code, out, _ = local_transport.run(f"cat {remote_path}", check=False)
            print(f"  uploaded and read back: {out.strip()!r}")
            assert code == 0, f"cat failed: exit {code}"
            assert marker in out, f"Content mismatch: expected {marker!r}, got {out.strip()!r}"
        finally:
            os.unlink(local_path)
            local_transport.run(f"rm -f {remote_path}", check=False)

    test("local_transport.run()", test_local_run)
    test("local_transport.run_on_host()", test_local_run_on_host)
    test("local_transport.is_container_running()", test_local_container_running)
    test("local_transport.checksum()", test_local_checksum)
    test("local_transport.host_checksum()", test_local_host_checksum)
    test("local_transport.upload()", test_local_upload)
elif should_run_ncc():
    skip("local_transport (all)", NCC_DOWN_REASON)
else:
    skip("local_transport (all)", "target is not ncc")

summary()
