"""Transport layer integration tests.

Tests SSHDockerTransport against vm-04-89 and LocalDockerTransport against
balerion.

Usage:
    pytest integration_tests/test_transport.py -m appliance
    pytest integration_tests/test_transport.py -m ncc
"""

import os
import tempfile

import pytest

MD5_HEX_LEN = 32

# -- SSH transport (vm-04-89) --------------------------------------------------

@pytest.mark.appliance
def test_ssh_run(ssh_transport):
    code, out, _ = ssh_transport.run("hostname", check=False)
    assert code == 0, f"Expected exit 0, got {code}"
    assert out.strip(), "Empty hostname"


@pytest.mark.appliance
def test_ssh_run_on_host(ssh_transport):
    code, _out, _ = ssh_transport.run_on_host("hostname", check=False)
    assert code == 0, f"Expected exit 0, got {code}"


@pytest.mark.appliance
def test_ssh_container_running(ssh_transport):
    assert ssh_transport.is_container_running(), "Container not running"


@pytest.mark.appliance
def test_ssh_checksum(ssh_transport):
    md5 = ssh_transport.checksum("/etc/hostname")
    assert len(md5) == MD5_HEX_LEN, f"Bad md5 length: {len(md5)}"


@pytest.mark.appliance
def test_ssh_host_checksum(ssh_transport):
    md5 = ssh_transport.host_checksum("/etc/hostname")
    assert len(md5) == MD5_HEX_LEN, f"Bad md5 length: {len(md5)}"


# -- Local transport (balerion) ------------------------------------------------

@pytest.mark.ncc
def test_local_run(local_transport):
    code, out, _ = local_transport.run("hostname", check=False)
    assert code == 0, f"Expected exit 0, got {code}"
    assert out.strip(), "Empty hostname"


@pytest.mark.ncc
def test_local_run_on_host(local_transport):
    code, _out, _ = local_transport.run_on_host("hostname", check=False)
    assert code == 0, f"Expected exit 0, got {code}"


@pytest.mark.ncc
def test_local_container_running(local_transport):
    assert local_transport.is_container_running(), "Container not running"


@pytest.mark.ncc
def test_local_checksum(local_transport):
    md5 = local_transport.checksum("/etc/hostname")
    assert len(md5) == MD5_HEX_LEN, f"Bad md5 length: {len(md5)}"


@pytest.mark.ncc
def test_local_host_checksum(local_transport):
    md5 = local_transport.host_checksum("/etc/hostname")
    assert len(md5) == MD5_HEX_LEN, f"Bad md5 length: {len(md5)}"


@pytest.mark.ncc
def test_local_upload(local_transport):
    marker = "nios_test_upload_check_12345"
    remote_path = "/tmp/_nios_test_upload_check.txt"

    with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False) as f:
        f.write(marker)
        local_path = f.name

    try:
        local_transport.upload(local_path, remote_path)
        code, out, _ = local_transport.run(f"cat {remote_path}", check=False)
        assert code == 0, f"cat failed: exit {code}"
        assert marker in out, (
            f"Content mismatch: expected {marker!r}, got {out.strip()!r}"
        )
    finally:
        os.unlink(local_path)
        local_transport.run(f"rm -f {remote_path}", check=False)
