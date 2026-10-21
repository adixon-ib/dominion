"""Unit tests for enable_token_mode_wapi with a mocked app.wapi."""

import os
import tempfile
from unittest.mock import MagicMock

import pytest

from dominion import WapiError
from dominion.actions import token_enable
from dominion.actions.token_enable import enable_token_mode_wapi


def _status(overall, phase="P1", pct=0, phases=None):
    return {"overall_status": overall, "current_phase": phase,
            "percentage": pct, "phases": phases or []}


@pytest.fixture
def app(monkeypatch):
    def fake_patched(app, bundle_path):
        return os.path.join(tempfile.mkdtemp(), "patched.tar")

    monkeypatch.setattr(token_enable, "_patched_bundle", fake_patched)
    monkeypatch.setattr(token_enable.time, "sleep", lambda s: None)
    a = MagicMock()
    a.hostname = "vm-test"
    a.wapi.upload_file.return_value = "tok"
    return a


def _wire(app, enable, polls=(), get=None):
    uploaded = {"uid": "u", "total": 5}
    seq = iter(polls)

    def post(path, data=None):
        if path.endswith("upload"):
            return uploaded
        if path.endswith("enable"):
            return enable
        return next(seq)

    app.wapi.post.side_effect = post
    app.wapi.get.return_value = get if get is not None else [{"uid": "u"}]


def test_success_polls_until_completed(app):
    done = _status("COMPLETED", pct=100)
    _wire(app, _status("IN_PROGRESS"), [_status("IN_PROGRESS", pct=50), done])

    result = enable_token_mode_wapi(app, "b.tar")

    assert result == {"upload": {"uid": "u", "total": 5}, "status": done, "bundle": [{"uid": "u"}]}
    app.wapi.post.assert_any_call("ipc:bundle?_function=upload", {"token": "tok"})
    app.wapi.get.assert_called_once_with("ipc:bundle")


def test_upload_rejected(app):
    app.wapi.post.side_effect = WapiError("POST", "ipc:bundle", 400, "Invalid token bundle")

    with pytest.raises(RuntimeError, match="rejected.*Invalid token bundle"):
        enable_token_mode_wapi(app, "b.tar")
    app.wapi.get.assert_not_called()


def test_failed_phase(app):
    failed = _status("FAILED", "PHASE_X", phases=[
        {"phase": "PHASE_A", "status": "COMPLETED", "message": "ok"},
        {"phase": "PHASE_X", "status": "FAILED", "message": "disk full"},
    ])
    _wire(app, _status("IN_PROGRESS"), [failed])

    with pytest.raises(RuntimeError, match="PHASE_X.*PHASE_X: disk full"):
        enable_token_mode_wapi(app, "b.tar")


def test_timeout(app):
    _wire(app, _status("IN_PROGRESS"), iter(lambda: _status("IN_PROGRESS"), None))

    with pytest.raises(TimeoutError, match="not finished"):
        enable_token_mode_wapi(app, "b.tar", timeout=0)
