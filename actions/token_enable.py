"""Enable IPc (token) mode on an air-gapped appliance."""

from __future__ import annotations

import json
import os
import shutil
import tarfile
import tempfile
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from dominion import Appliance

_REQUIRED_MEMBERS = ("tab.json", "docker-compose.yml", "usage_calculator")
_REMOTE_BUNDLE_PATH = "/storage/tmp/token_bundle.tar"
_AIRGAPPED_FLAG = "/infoblox/var/flags/airgapped_skip"


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
    if not os.path.exists(bundle_path):
        raise FileNotFoundError(f"Token bundle not found: {bundle_path}")

    transport = app.container._transport

    app._logger.debug("Fetching lpc_uid from %s", app.hostname)
    lpc_uid = _fetch_lpc_uid(app)
    app._logger.debug("lpc_uid: %s", lpc_uid)

    app._logger.debug("Patching bundle with lpc_uid")
    patched = _prepare_bundle(bundle_path, lpc_uid)

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
