"""Enable IPc (token) mode on an air-gapped appliance."""

from __future__ import annotations

import json
import os
import shutil
import tarfile
import tempfile
import time
from typing import TYPE_CHECKING

from dominion.src.constants import (
    TOKEN_BUNDLE_ENABLE_TIMEOUT,
    TOKEN_BUNDLE_POLL_INTERVAL,
)
from dominion.src.wapi import WapiError

if TYPE_CHECKING:
    from dominion import Appliance

_REQUIRED_MEMBERS = ("tab.json", "docker-compose.yml", "usage_calculator")
_REMOTE_BUNDLE_PATH = "/storage/tmp/token_bundle.tar"
_AIRGAPPED_FLAG = "/infoblox/var/flags/airgapped_skip"
_BUNDLE_WAPI_VERSION = "v2.15"


def _version_tuple(version: str) -> tuple[int, ...]:
    return tuple(int(n) for n in version.lstrip("v").split("."))


def _fetch_lpc_uid(app: Appliance) -> str:
    """Fetch the grid's lpc_uid via WAPI."""
    records = app.wapi.get(
        "grid:license_pool_container",
        params="_return_fields=lpc_uid",
    )
    assert records and records[0].get("lpc_uid"), "Could not fetch lpc_uid from GM"
    return records[0]["lpc_uid"]


def _prepare_bundle(bundle_path: str, lpc_uid: str) -> str:
    """Extract bundle, patch tab.json with lpc_uid, repack to a temp file.

    Args:
        bundle_path: Path to the original token_bundle.tar.
        lpc_uid: The license UID to inject into tab.json.

    Returns:
        Path to the repacked tar (in a temp directory, caller should clean up).
    """
    work_dir = tempfile.mkdtemp()
    try:
        return _repack_bundle(bundle_path, lpc_uid, work_dir)
    except BaseException:
        shutil.rmtree(work_dir, ignore_errors=True)
        raise


def _repack_bundle(bundle_path: str, lpc_uid: str, work_dir: str) -> str:
    with tarfile.open(bundle_path) as tar:
        tar.extractall(work_dir)

    tab_path = None
    for root, _, files in os.walk(work_dir):
        if "tab.json" in files:
            tab_path = os.path.join(root, "tab.json")
            break
    assert tab_path, f"tab.json not found inside {bundle_path}"

    with open(tab_path) as f:
        tab = json.load(f)
    tab.pop("licenseUid", None)
    tab["license_uid"] = lpc_uid
    with open(tab_path, "w") as f:
        json.dump(tab, f, indent=2)

    tab_dir = os.path.dirname(tab_path)
    for member in _REQUIRED_MEMBERS:
        assert os.path.exists(os.path.join(tab_dir, member)), (
            f"Required member '{member}' missing from bundle"
        )

    out_path = os.path.join(work_dir, "token_bundle_patched.tar")
    with tarfile.open(out_path, "w") as tar:
        for member in _REQUIRED_MEMBERS:
            tar.add(os.path.join(tab_dir, member), arcname=member)

    return out_path


def _patched_bundle(app: Appliance, bundle_path: str) -> str:
    """Return a copy of the bundle patched with the appliance's lpc_uid.

    The caller removes the returned file's directory.

    Raises:
        FileNotFoundError: If bundle_path does not exist.
    """
    if not os.path.exists(bundle_path):
        raise FileNotFoundError(f"Token bundle not found: {bundle_path}")

    app._logger.debug("Fetching lpc_uid from %s", app.hostname)
    lpc_uid = _fetch_lpc_uid(app)
    app._logger.debug("lpc_uid: %s", lpc_uid)

    app._logger.debug("Patching bundle with lpc_uid")
    return _prepare_bundle(bundle_path, lpc_uid)


def enable_token_mode(app: Appliance, bundle_path: str) -> None:
    """Enable IPc (token) mode on an air-gapped appliance.

    Fetches the grid's lpc_uid, patches the token bundle, uploads it
    to the appliance, sets the airgapped_skip flag, then drives the
    console wizard to enable IPc mode.

    Args:
        app: The appliance to enable token mode on.
        bundle_path: Path to token_bundle.tar.

    Raises:
        FileNotFoundError: If bundle_path does not exist.
        RuntimeError: If the console does not confirm IPc mode enabled.
    """
    patched = _patched_bundle(app, bundle_path)
    transport = app.container._transport

    app._logger.debug("Uploading bundle to %s:%s", app.hostname, _REMOTE_BUNDLE_PATH)
    transport.upload_to_host(patched, _REMOTE_BUNDLE_PATH)
    shutil.rmtree(os.path.dirname(patched))

    app._logger.debug("Setting airgapped_skip flag")
    transport.run_on_host(f"touch {_AIRGAPPED_FLAG}")

    with app.console_session() as console:
        console.sendline("set nios_usage_mode IPc")
        console.expect(r"Do you want to proceed\? \(y or n\)")
        console.sendline("y")
        output = console.expect("IPc mode enabled successfully")
        if "IPc mode enabled successfully" not in output:
            raise RuntimeError("Failed to enable IPc mode")

    app._logger.debug("IPc mode enabled")


def _failed_phases(status: dict) -> str:
    return "; ".join(
        f"{p.get('phase')}: {p.get('message')}"
        for p in status.get("phases", [])
        if p.get("status") == "FAILED"
    ) or "no failed phase reported"


def enable_token_mode_wapi(
    app: Appliance,
    bundle_path: str,
    timeout: int = TOKEN_BUNDLE_ENABLE_TIMEOUT,
    poll_interval: int = TOKEN_BUNDLE_POLL_INTERVAL,
) -> dict:
    """Enable IPc (token) mode on an air-gapped appliance via ipc:bundle WAPI.

    Fetches the grid's lpc_uid, patches the token bundle, uploads it with
    fileop, then calls ipc:bundle upload, enable and polls status until
    COMPLETED or FAILED. Raises app.wapi_version to v2.15 if it is lower.
    Transient WapiErrors (status 0 or >= 500) on the status poll are retried
    until the deadline; other WapiErrors propagate.

    Args:
        app: The appliance to enable token mode on.
        bundle_path: Path to token_bundle.tar.
        timeout: Seconds to wait for the migration to finish.
        poll_interval: Seconds between status polls.

    Returns:
        {"upload": upload result, "status": final status, "bundle": GET ipc:bundle}.

    Raises:
        FileNotFoundError: If bundle_path does not exist.
        RuntimeError: If upload/enable is rejected or a migration phase FAILED.
        TimeoutError: If the migration does not finish within timeout.
    """
    log = app._logger
    if _version_tuple(app.wapi_version) < _version_tuple(_BUNDLE_WAPI_VERSION):
        app.wapi_version = _BUNDLE_WAPI_VERSION
    patched = _patched_bundle(app, bundle_path)
    try:
        log.info("Uploading bundle to %s via fileop", app.hostname)
        token = app.wapi.upload_file(patched)
    finally:
        shutil.rmtree(os.path.dirname(patched))

    try:
        log.info("ipc:bundle upload")
        uploaded = app.wapi.post("ipc:bundle?_function=upload", {"token": token})
        log.info("Bundle accepted: %s", uploaded)
        log.info("ipc:bundle enable")
        status = app.wapi.post("ipc:bundle?_function=enable", {})
    except WapiError as e:
        raise RuntimeError(f"ipc:bundle on {app.hostname} rejected: {e}") from e

    deadline = time.monotonic() + timeout
    while status["overall_status"] not in ("COMPLETED", "FAILED"):
        log.info(
            "Migration %s: %s (%s%%)",
            status["overall_status"], status["current_phase"], status["percentage"],
        )
        if time.monotonic() >= deadline:
            raise TimeoutError(
                f"IPc migration on {app.hostname} not finished after {timeout}s, "
                f"last status: {status}"
            )
        time.sleep(poll_interval)
        try:
            status = app.wapi.post("ipc:bundle?_function=status", {})
        except WapiError as e:
            if e.status != 0 and e.status < 500:
                raise
            log.warning("Status poll on %s failed, retrying: %s", app.hostname, e)

    if status["overall_status"] == "FAILED":
        raise RuntimeError(
            f"IPc migration on {app.hostname} failed in phase "
            f"{status['current_phase']}: {_failed_phases(status)}"
        )

    log.info("IPc migration completed")
    return {
        "upload": uploaded,
        "status": status,
        "bundle": app.wapi.get("ipc:bundle"),
    }
