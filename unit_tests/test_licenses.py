"""Unit tests for licenses and ipc_preflight with a mocked app.wapi."""

import importlib
from unittest.mock import MagicMock

import pytest

from dominion import WapiError

lic = importlib.import_module("dominion.actions.licenses")

NIOS = {"type": "NIOS", "limit": "IB-V926"}


def _app(lics=(NIOS,), gridwide=(), lpc=({"lpc_uid": "uid1"},), version="v2.12"):
    app = MagicMock()
    app.hostname = "vm-48-10"
    app.wapi_version = version

    def get(path, params=""):
        if path == "member:license":
            return list(lics)
        if path == "ipc:gridwide":
            if isinstance(gridwide, Exception):
                raise gridwide
            return list(gridwide)
        if path == "grid:license_pool_container":
            return list(lpc)
        raise AssertionError(path)

    app.wapi.get.side_effect = get
    return app


def test_licenses_requests_fields():
    app = _app()
    assert lic.licenses(app) == [NIOS]
    app.wapi.get.assert_called_once_with(
        "member:license",
        params="_return_fields=type,kind,expiration_status,expiry_date,limit,key,hwid",
    )


def test_preflight_ok_and_bumps_version():
    app = _app()
    assert lic.ipc_preflight(app) == {"model": "IB-V926", "enabled": False, "lpc_uid": "uid1"}
    assert app.wapi_version == "v2.15"


def test_preflight_keeps_higher_version():
    app = _app(version="v2.16")
    lic.ipc_preflight(app)
    assert app.wapi_version == "v2.16"


@pytest.mark.parametrize("lics", [
    [],
    [{"type": "DNS", "limit": ""}],
    [{"type": "NIOS", "limit": ""}],
    [{"type": "NIOS", "limit": "IB-VM"}],
    [{"type": "NIOS", "limit": "IB-FLEX-1"}],
])
def test_preflight_no_model(lics):
    app = _app(lics=lics)
    with pytest.raises(RuntimeError, match="no NIOS model"):
        lic.ipc_preflight(app)


def test_preflight_already_enabled():
    app = _app(gridwide=[{"type": "SERVERS"}])
    with pytest.raises(RuntimeError, match="already enabled"):
        lic.ipc_preflight(app)


@pytest.mark.parametrize("status", [400, 404])
def test_preflight_old_wapi_reports_too_old(status):
    app = _app(gridwide=WapiError("GET", "ipc:gridwide", status, "unknown object"))
    with pytest.raises(RuntimeError, match=r"WAPI too old for ipc \(v2.15\)"):
        lic.ipc_preflight(app)


@pytest.mark.parametrize("limit", ["TE-926", "V926"])
def test_preflight_model_not_gm_capable(limit):
    app = _app(lics=[{"type": "NIOS", "limit": limit}])
    with pytest.raises(RuntimeError, match="cannot be a Grid Master"):
        lic.ipc_preflight(app)


def test_preflight_other_wapi_error_propagates():
    app = _app(gridwide=WapiError("GET", "ipc:gridwide", 401, "denied"))
    with pytest.raises(WapiError):
        lic.ipc_preflight(app)


@pytest.mark.parametrize("lpc", [[], [{"lpc_uid": ""}]])
def test_preflight_missing_lpc_uid(lpc):
    app = _app(lpc=lpc)
    with pytest.raises(RuntimeError, match="no lpc_uid"):
        lic.ipc_preflight(app)


def test_first_failure_wins():
    app = _app(lics=[], gridwide=[{"type": "SERVERS"}], lpc=[])
    with pytest.raises(RuntimeError, match="no NIOS model"):
        lic.ipc_preflight(app)
