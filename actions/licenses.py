"""License listing and IPc enable preflight over WAPI."""

from __future__ import annotations

from typing import TYPE_CHECKING

from dominion.actions.token_enable import (
    _BUNDLE_WAPI_VERSION,
    _fetch_lpc_uid,
    _version_tuple,
)
from dominion.src.wapi import WapiError

if TYPE_CHECKING:
    from dominion import Appliance

_LICENSE_FIELDS = "type,kind,expiration_status,expiry_date,limit,key,hwid"
# Same "no model" cases ipc:bundle rejects (pyabs/token_bundle.py _check_model).
_NO_MODELS = ("", "IB-VM", "IB-UNKNOWN")


def licenses(app: Appliance) -> list[dict]:
    """Return the member licenses via WAPI GET member:license."""
    return app.wapi.get("member:license", params=f"_return_fields={_LICENSE_FIELDS}")


def _nios_model(lics: list[dict]) -> str:
    for lic in lics:
        if lic.get("type") == "NIOS" and lic.get("limit"):
            return str(lic["limit"])
    return ""


def ipc_preflight(app: Appliance) -> dict:
    """Check the prerequisites for enabling IPc mode.

    Checks, in order: a NIOS model is configured and GM capable, IPc is not
    already enabled (ipc:gridwide returns rows only when enabled), and the grid
    has an lpc_uid. Raises app.wapi_version to v2.15 if lower, since
    ipc:gridwide needs it.

    The model check is a license-based proxy (NIOS license limit) for
    ipc:bundle's _check_model, which reads the hardware type and also rejects
    reporting-capable models; only the cheap "IB-" prefix test is done here.
    It does not mirror the CLI, which prompts for a model instead of rejecting.

    Returns:
        {"model": str, "enabled": False, "lpc_uid": str}

    Raises:
        RuntimeError: On the first failed check, or if the WAPI is too old for
            ipc (ipc:gridwide answers 400 or 404).
    """
    host = app.hostname

    model = _nios_model(licenses(app))
    if model in _NO_MODELS or "IB-FLEX" in model:
        raise RuntimeError(f"{host}: no NIOS model configured (NIOS license limit {model!r})")
    if not model.startswith("IB-"):
        raise RuntimeError(f"{host}: model {model!r} cannot be a Grid Master (needs IB- prefix)")

    if _version_tuple(app.wapi_version) < _version_tuple(_BUNDLE_WAPI_VERSION):
        app.wapi_version = _BUNDLE_WAPI_VERSION
    try:
        rows = app.wapi.get("ipc:gridwide")
    except WapiError as e:
        if e.status not in (400, 404):
            raise
        raise RuntimeError(f"{host}: WAPI too old for ipc ({app.wapi_version})") from e
    if rows:
        raise RuntimeError(f"{host}: IPc already enabled (ipc:gridwide returned {len(rows)} rows)")

    try:
        lpc_uid = _fetch_lpc_uid(app)
    except (AssertionError, WapiError) as e:
        raise RuntimeError(f"{host}: no lpc_uid in grid:license_pool_container: {e}") from e

    return {"model": model, "enabled": False, "lpc_uid": lpc_uid}
