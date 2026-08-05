"""Deploy layer integration tests.

Tests deploy_serial_console, deploy_token_py, and deploy_token_stats_consumer
against both SSH (vm-04-89) and local NCC (balerion) targets.

WARNING: destructive — deploys real binaries/files. Excluded by default
(pytest.ini's `addopts = -m "not destructive"`); run explicitly with:

    pytest integration_tests/test_deploy.py -m "destructive and appliance"
    pytest integration_tests/test_deploy.py -m "destructive and ncc"

    Revert appliance: ssh root@IP "docker restart nios"
    Revert NCC: slot stop balerion && slot start balerion
"""

import hashlib
import os
import time

import pytest

from helpers import DEFAULT_WORKTREE

pytestmark = pytest.mark.destructive


# -- deploy_serial_console → appliance (vm-04-89) -----------------------------

@pytest.mark.appliance
def test_deploy_serial_console_appliance(ssh_appliance):
    result = ssh_appliance.deploy_serial_console()

    assert result.artifact == "serial_console", f"Wrong artifact: {result.artifact}"
    assert len(result.local_hash) == 32, f"Bad local hash: {result.local_hash}"
    assert len(result.remote_hash) == 32, f"Bad remote hash: {result.remote_hash}"
    assert result.local_hash == result.remote_hash, (
        f"Hash mismatch: {result.local_hash} != {result.remote_hash}"
    )

    time.sleep(3)
    with ssh_appliance.shell_session() as shell:
        out = shell.run("pgrep -x serial_console | head -1")
        pid = out.strip()
        assert pid, "serial_console did not respawn"


@pytest.mark.appliance
def test_deploy_token_py_appliance(ssh_appliance):
    result = ssh_appliance.deploy_token_py(groups={"pyabs", "wapi"})

    assert result.artifact == "token_py", f"Wrong artifact: {result.artifact}"
    assert result.details.get("files"), "No files in deploy result"

    transport = ssh_appliance.container._transport
    for path in [
        "/infoblox/one/lib/python/infoblox/one/grid_token_pool.py",
        "/infoblox/one/lib/python/infoblox/one/wapi/grid_token_pool.py",
    ]:
        code, _, _ = transport.run(f"test -f {path}", check=False)
        assert code == 0, f"File missing: {path}"


@pytest.mark.appliance
def test_deploy_token_stats_consumer_appliance(ssh_appliance):
    result = ssh_appliance.deploy_token_stats_consumer()

    assert result.artifact == "token_stats_consumer"
    assert result.local_hash and len(result.local_hash) == 32
    assert result.remote_hash == result.local_hash, (
        f"Hash mismatch: {result.local_hash} != {result.remote_hash}"
    )


# -- deploy_serial_console → NCC (balerion) ------------------------------------

@pytest.mark.ncc
@pytest.mark.skip(reason="host-level ops not supported on NCC OverlayFS: deploy_serial_console "
                         "uses host_checksum/upload_to_host, which assume /infoblox/ is on the "
                         "host filesystem; on NCC it's inside the container OverlayFS")
def test_deploy_serial_console_ncc():
    pass


# -- deploy_token_py → NCC (balerion) ------------------------------------------

PYABS_DIR = "/infoblox/one/lib/python/infoblox/one"
WAPI_DIR = "/infoblox/one/lib/python/infoblox/one/wapi"
SAMPLE_PYABS_FILE = "grid_token_pool.py"
SAMPLE_WAPI_FILE = "grid_token_pool.py"


@pytest.mark.ncc
def test_deploy_token_py_ncc(local_ncc_container):
    result = local_ncc_container.deploy_token_py(groups={"pyabs", "wapi"})

    assert result.artifact == "token_py", f"Wrong artifact: {result.artifact}"

    transport = local_ncc_container._transport

    for path in [
        f"{PYABS_DIR}/{SAMPLE_PYABS_FILE}",
        f"{WAPI_DIR}/{SAMPLE_WAPI_FILE}",
    ]:
        code, _, _ = transport.run(f"test -f {path}", check=False)
        assert code == 0, f"File missing: {path}"

    _, out, _ = transport.run(
        f"find {PYABS_DIR}/__pycache__ -name 'grid_token_pool*.pyc' 2>/dev/null | wc -l"
    )
    assert out.strip() == "0", f"Expected 0 cached .pyc files, got {out.strip()}"

    _, out, _ = transport.run(
        f"find {WAPI_DIR}/__pycache__ -name 'grid_token_pool*.pyc' 2>/dev/null | wc -l"
    )
    assert out.strip() == "0", f"Expected 0 cached .pyc files, got {out.strip()}"

    local_path = os.path.join(
        DEFAULT_WORKTREE, "products/one/server/src/pyabs", SAMPLE_PYABS_FILE
    )
    with open(local_path, "rb") as f:
        local_md5 = hashlib.md5(f.read()).hexdigest()

    remote_md5 = transport.checksum(f"{PYABS_DIR}/{SAMPLE_PYABS_FILE}")
    assert local_md5 == remote_md5, f"Hash mismatch: {local_md5} != {remote_md5}"


@pytest.mark.ncc
def test_deploy_token_stats_consumer_ncc(local_ncc_container):
    result = local_ncc_container.deploy_token_stats_consumer()

    assert result.artifact == "token_stats_consumer"
    assert result.local_hash and len(result.local_hash) == 32
    assert result.remote_hash == result.local_hash, (
        f"Hash mismatch: {result.local_hash} != {result.remote_hash}"
    )
