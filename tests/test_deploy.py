#!/usr/bin/env python3
"""Deploy layer integration tests.

Tests deploy_serial_console, deploy_token_py, and deploy_token_stats_consumer
against both SSH (vm-04-89) and local NCC (balerion) targets.

Usage:
    python3 -m nios_test.tests.test_deploy [IP]

WARNING: These tests are destructive — they deploy real binaries/files.
    Revert appliance: ssh root@IP "docker restart nios"
    Revert NCC: slot stop balerion && slot start balerion
"""

import hashlib
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from helpers import (
    APPLIANCE_DOWN_REASON,
    DEFAULT_WORKTREE,
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


# -- deploy_serial_console → appliance (vm-04-89) -----------------------------

if should_run_appliance() and is_appliance_up():
    ssh_appliance = Appliance.from_config(appliance_config(log_level="verbose"))

    def test_deploy_serial_console_appliance():
        result = ssh_appliance.deploy_serial_console()

        print(f"  artifact:    {result.artifact}")
        print(f"  changed:     {result.changed}")
        print(f"  restarted:   {result.restarted}")
        print(f"  local_hash:  {result.local_hash}")
        print(f"  remote_hash: {result.remote_hash}")

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
            print(f"  respawned pid: {pid}")
            assert pid, "serial_console did not respawn"

    def test_deploy_token_py_appliance():
        result = ssh_appliance.deploy_token_py(groups={"pyabs", "wapi"})

        print(f"  artifact:  {result.artifact}")
        print(f"  changed:   {result.changed}")
        print(f"  restarted: {result.restarted}")
        print(f"  groups:    {result.details.get('groups')}")
        print(f"  files:     {len(result.details.get('files', []))}")

        assert result.artifact == "token_py", f"Wrong artifact: {result.artifact}"
        assert result.details.get("files"), "No files in deploy result"

        transport = ssh_appliance.container._transport
        for path in [
            "/infoblox/one/lib/python/infoblox/one/grid_token_pool.py",
            "/infoblox/one/lib/python/infoblox/one/wapi/grid_token_pool.py",
        ]:
            code, _, _ = transport.run(f"test -f {path}", check=False)
            status = "EXISTS" if code == 0 else "MISSING"
            print(f"  {path}: {status}")
            assert code == 0, f"File missing: {path}"

    def test_deploy_token_stats_consumer_appliance():
        result = ssh_appliance.deploy_token_stats_consumer()

        print(f"  artifact:  {result.artifact}")
        print(f"  local_hash:  {result.local_hash}")
        print(f"  remote_hash: {result.remote_hash}")
        print(f"  restarted:   {result.restarted}")

        assert result.artifact == "token_stats_consumer"
        assert result.local_hash and len(result.local_hash) == 32
        assert result.remote_hash == result.local_hash, (
            f"Hash mismatch: {result.local_hash} != {result.remote_hash}"
        )

    test("deploy_serial_console → appliance", test_deploy_serial_console_appliance)
    test("deploy_token_py → appliance (pyabs + wapi)", test_deploy_token_py_appliance)
    test("deploy_token_stats_consumer → appliance", test_deploy_token_stats_consumer_appliance)
elif should_run_appliance():
    skip("deploy tests (SSH)", APPLIANCE_DOWN_REASON)
else:
    skip("deploy tests (SSH)", "target is not appliance")


# -- deploy_serial_console → NCC (balerion) ------------------------------------
# SKIP: deploy_serial_console uses host-level operations (host_checksum,
# upload_to_host) which assume /infoblox/ is on the host filesystem.
# On NCC, /infoblox/ lives inside the container OverlayFS and is not
# accessible from the host. This is a known library limitation.

skip("deploy_serial_console → NCC", "host-level ops not supported on NCC OverlayFS")


# -- deploy_token_py → NCC (balerion) ------------------------------------------

if should_run_ncc() and is_ncc_up():
    local_container = LocalNiosContainer.from_config(local_container_config(log_level="verbose"))

    PYABS_DIR = "/infoblox/one/lib/python/infoblox/one"
    WAPI_DIR = "/infoblox/one/lib/python/infoblox/one/wapi"

    SAMPLE_PYABS_FILE = "grid_token_pool.py"
    SAMPLE_WAPI_FILE = "grid_token_pool.py"

    def test_deploy_token_py_ncc():
        result = local_container.deploy_token_py(groups={"pyabs", "wapi"})

        print(f"  artifact:  {result.artifact}")
        print(f"  changed:   {result.changed}")
        print(f"  restarted: {result.restarted}")
        print(f"  groups:    {result.details.get('groups')}")
        print(f"  files:     {len(result.details.get('files', []))}")

        assert result.artifact == "token_py", f"Wrong artifact: {result.artifact}"

        transport = local_container._transport

        for path in [
            f"{PYABS_DIR}/{SAMPLE_PYABS_FILE}",
            f"{WAPI_DIR}/{SAMPLE_WAPI_FILE}",
        ]:
            code, _, _ = transport.run(f"test -f {path}", check=False)
            status = "EXISTS" if code == 0 else "MISSING"
            print(f"  {path}: {status}")
            assert code == 0, f"File missing: {path}"

        _, out, _ = transport.run(
            f"find {PYABS_DIR}/__pycache__ -name 'grid_token_pool*.pyc' 2>/dev/null | wc -l"
        )
        pyc_count = out.strip()
        print(f"  pyabs pycache grid_token_pool .pyc count: {pyc_count}")
        assert pyc_count == "0", f"Expected 0 cached .pyc files, got {pyc_count}"

        _, out, _ = transport.run(
            f"find {WAPI_DIR}/__pycache__ -name 'grid_token_pool*.pyc' 2>/dev/null | wc -l"
        )
        pyc_count = out.strip()
        print(f"  wapi pycache grid_token_pool .pyc count: {pyc_count}")
        assert pyc_count == "0", f"Expected 0 cached .pyc files, got {pyc_count}"

        local_path = os.path.join(
            DEFAULT_WORKTREE, "products/one/server/src/pyabs", SAMPLE_PYABS_FILE
        )
        with open(local_path, "rb") as f:
            local_md5 = hashlib.md5(f.read()).hexdigest()

        remote_md5 = transport.checksum(f"{PYABS_DIR}/{SAMPLE_PYABS_FILE}")
        print(f"  {SAMPLE_PYABS_FILE} local_md5:  {local_md5}")
        print(f"  {SAMPLE_PYABS_FILE} remote_md5: {remote_md5}")
        assert local_md5 == remote_md5, f"Hash mismatch: {local_md5} != {remote_md5}"

    def test_deploy_token_stats_consumer_ncc():
        result = local_container.deploy_token_stats_consumer()

        print(f"  artifact:    {result.artifact}")
        print(f"  local_hash:  {result.local_hash}")
        print(f"  remote_hash: {result.remote_hash}")
        print(f"  restarted:   {result.restarted}")

        assert result.artifact == "token_stats_consumer"
        assert result.local_hash and len(result.local_hash) == 32
        assert result.remote_hash == result.local_hash, (
            f"Hash mismatch: {result.local_hash} != {result.remote_hash}"
        )

    test("deploy_token_py → NCC (pyabs + wapi)", test_deploy_token_py_ncc)
    test("deploy_token_stats_consumer → NCC", test_deploy_token_stats_consumer_ncc)
elif should_run_ncc():
    skip("deploy tests (NCC)", NCC_DOWN_REASON)
else:
    skip("deploy tests (NCC)", "target is not ncc")

summary()
