"""Unit tests for enable_token_mode_wapi with a mocked app.wapi."""

import json
import os
import shutil
import tarfile
import tempfile
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from dominion import LocalAppliance, LocalApplianceConfig, WapiClient, WapiError
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
    a.wapi_version = "v2.12"
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


def test_transient_poll_error_retried(app):
    done = _status("COMPLETED", pct=100)
    seq = iter([WapiError("POST", "ipc:bundle", 503, "restarting"), done])

    def post(path, data=None):
        if path.endswith("upload"):
            return {"uid": "u"}
        if path.endswith("enable"):
            return _status("IN_PROGRESS")
        item = next(seq)
        if isinstance(item, Exception):
            raise item
        return item

    app.wapi.post.side_effect = post
    app.wapi.get.return_value = []

    result = enable_token_mode_wapi(app, "b.tar")

    assert result["status"] == done
    app._logger.warning.assert_called_once()


def test_poll_auth_error_raises_immediately(app):
    def post(path, data=None):
        if path.endswith("upload"):
            return {"uid": "u"}
        if path.endswith("enable"):
            return _status("IN_PROGRESS")
        raise WapiError("POST", "ipc:bundle", 401, "Authorization Required")

    app.wapi.post.side_effect = post

    with pytest.raises(WapiError) as exc:
        enable_token_mode_wapi(app, "b.tar")
    assert exc.value.status == 401
    app._logger.warning.assert_not_called()
    app.wapi.get.assert_not_called()


def test_poll_status_zero_is_retried(app):
    seq = iter([WapiError("POST", "ipc:bundle", 0, ""), _status("COMPLETED", pct=100)])

    def post(path, data=None):
        if path.endswith("upload"):
            return {"uid": "u"}
        if path.endswith("enable"):
            return _status("IN_PROGRESS")
        item = next(seq)
        if isinstance(item, Exception):
            raise item
        return item

    app.wapi.post.side_effect = post
    app.wapi.get.return_value = []

    assert enable_token_mode_wapi(app, "b.tar")["status"]["overall_status"] == "COMPLETED"


def test_persistent_poll_error_times_out(app, monkeypatch):
    clock = iter(range(0, 1000, 10))
    monkeypatch.setattr(token_enable, "time", SimpleNamespace(
        monotonic=lambda: next(clock), sleep=lambda s: None,
    ))

    def post(path, data=None):
        if path.endswith("upload"):
            return {"uid": "u"}
        if path.endswith("enable"):
            return _status("IN_PROGRESS")
        raise WapiError("POST", "ipc:bundle", 503, "down")

    app.wapi.post.side_effect = post

    with pytest.raises(TimeoutError, match="not finished"):
        enable_token_mode_wapi(app, "b.tar", timeout=50)
    assert app._logger.warning.call_count >= 2


def test_enable_rejected_raises_immediately(app):
    def post(path, data=None):
        if path.endswith("upload"):
            return {"uid": "u"}
        raise WapiError("POST", "ipc:bundle", 400, "already enabled")

    app.wapi.post.side_effect = post

    with pytest.raises(RuntimeError, match="rejected.*already enabled"):
        enable_token_mode_wapi(app, "b.tar")
    assert app.wapi.post.call_count == 2
    app.wapi.get.assert_not_called()


@pytest.mark.parametrize("before,after", [
    ("v2.12", "v2.15"),
    ("v2.9", "v2.15"),
    ("v2.15", "v2.15"),
    ("v2.20", "v2.20"),
])
def test_wapi_version_raised_not_lowered(app, before, after):
    app.wapi_version = before
    _wire(app, _status("COMPLETED", pct=100))

    enable_token_mode_wapi(app, "b.tar")

    assert app.wapi_version == after


def _make_bundle(path, tab, members=("docker-compose.yml", "usage_calculator", "tab.json")):
    src = tempfile.mkdtemp()
    try:
        inner = os.path.join(src, "bundle")
        os.makedirs(inner)
        for m in members:
            with open(os.path.join(inner, m), "w") as f:
                if m == "tab.json":
                    json.dump(tab, f)
                else:
                    f.write(m)
        with tarfile.open(path, "w") as tar:
            tar.add(inner, arcname="bundle")
    finally:
        shutil.rmtree(src)


@pytest.fixture
def lpc_app(monkeypatch):
    monkeypatch.setattr(token_enable, "_fetch_lpc_uid", lambda app: "LPC-123")
    return MagicMock()


def test_patched_bundle_injects_lpc_uid(lpc_app, tmp_path):
    bundle = str(tmp_path / "b.tar")
    _make_bundle(bundle, {"licenseUid": "old", "keep": 1})

    out = token_enable._patched_bundle(lpc_app, bundle)
    try:
        with tarfile.open(out) as tar:
            names = sorted(tar.getnames())
            tab = json.load(tar.extractfile("tab.json"))
    finally:
        shutil.rmtree(os.path.dirname(out))

    assert names == ["docker-compose.yml", "tab.json", "usage_calculator"]
    assert tab == {"license_uid": "LPC-123", "keep": 1}
    assert os.path.exists(bundle)


def test_patched_bundle_missing_file(lpc_app, tmp_path):
    with pytest.raises(FileNotFoundError, match="not found"):
        token_enable._patched_bundle(lpc_app, str(tmp_path / "nope.tar"))


def test_patched_bundle_missing_member(lpc_app, tmp_path, monkeypatch):
    bundle = str(tmp_path / "b.tar")
    work = tmp_path / "work"
    work.mkdir()
    monkeypatch.setenv("TMPDIR", str(work))
    monkeypatch.setattr(tempfile, "tempdir", None)
    _make_bundle(bundle, {}, members=("tab.json", "docker-compose.yml"))

    with pytest.raises(AssertionError, match="usage_calculator"):
        token_enable._patched_bundle(lpc_app, bundle)
    assert list(work.iterdir()) == []


def test_patched_bundle_missing_tab_json(lpc_app, tmp_path, monkeypatch):
    bundle = str(tmp_path / "b.tar")
    work = tmp_path / "work"
    work.mkdir()
    monkeypatch.setenv("TMPDIR", str(work))
    monkeypatch.setattr(tempfile, "tempdir", None)
    _make_bundle(bundle, {}, members=("docker-compose.yml", "usage_calculator"))

    with pytest.raises(AssertionError, match="tab.json not found"):
        token_enable._patched_bundle(lpc_app, bundle)
    assert list(work.iterdir()) == []


def test_wapi_rebuilt_at_v215_on_real_appliance(monkeypatch):
    monkeypatch.setattr(token_enable, "_patched_bundle", lambda a, b: os.path.join(tempfile.mkdtemp(), "p.tar"))
    monkeypatch.setattr(WapiClient, "upload_file", lambda self, path: "tok")
    monkeypatch.setattr(WapiClient, "post", lambda self, path, data=None, check=True: _status("COMPLETED", pct=100))
    monkeypatch.setattr(WapiClient, "get", lambda self, path, params="", check=True: [])
    app = LocalAppliance.from_config(LocalApplianceConfig(
        container_name="balerion", chroot="/local/91/chroots/balerion", ip="10.0.0.1",
    ))
    old = app.wapi
    assert old._base_url.endswith("/wapi/v2.12")

    enable_token_mode_wapi(app, "b.tar")

    assert app.wapi is not old
    assert app.wapi._base_url.endswith("/wapi/v2.15")
