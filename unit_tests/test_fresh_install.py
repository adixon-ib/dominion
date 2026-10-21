"""Unit tests for fresh_install and Grid.from_config(check=False). No grid_deploy, no SSH."""

import importlib
import logging
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

import dominion.src.grid as grid_mod
from dominion import Grid, GridConfig

fi = importlib.import_module("dominion.actions.fresh_install")

LOGGER = logging.getLogger("dominion.unit_test")
TAG_XML = '<root><master UNIT_1_ID="vm-48-10" UNIT_1_IPADDR="10.0.0.48"/></root>'


# -- Grid.from_config check flag ------------------------------------------------

@pytest.fixture
def run_grid(monkeypatch, tmp_path):
    def run(returncode, tag_xml=TAG_XML, check=True):
        if tag_xml is not None:
            (tmp_path / "t.xml").write_text(tag_xml)
        proc = MagicMock(returncode=returncode, stdout=iter(["line\n"]))
        monkeypatch.setattr(grid_mod.subprocess, "Popen", lambda *a, **k: proc)
        monkeypatch.setattr(grid_mod.Appliance, "from_config", staticmethod(
            lambda cfg: SimpleNamespace(hostname=cfg.hostname, ip=cfg.ip)))
        cfg = GridConfig(topology="standalone", setup_dir=str(tmp_path), tag="t", logger=LOGGER)
        return Grid.from_config(cfg, check=check)
    return run


def test_from_config_nonzero_raises_by_default(run_grid):
    with pytest.raises(RuntimeError, match="exited 3"):
        run_grid(3)


def test_from_config_nonzero_check_false_returns_grid(run_grid):
    g = run_grid(3, check=False)
    assert g.returncode == 3
    assert (g.gm.hostname, g.gm.ip) == ("vm-48-10", "10.0.0.48")


def test_from_config_nonzero_check_false_no_tag_file_raises(run_grid):
    with pytest.raises(RuntimeError, match="exited 3.*unusable"):
        run_grid(3, tag_xml=None, check=False)


def test_from_config_zero_exit_returncode_zero(run_grid):
    assert run_grid(0).returncode == 0


# -- fresh_install ---------------------------------------------------------------

def _grid(app, returncode):
    return SimpleNamespace(gm=app, returncode=returncode)


@pytest.fixture
def install(monkeypatch):
    def go(returncode=0, tag=("vm-48-10", "10.0.0.48"), ssh=True, wapi_ok=True):
        app = SimpleNamespace(hostname=tag[0], ip=tag[1], wapi=MagicMock())
        if not wapi_ok:
            app.wapi.wait_until_ready.side_effect = RuntimeError("WAPI not ready after 300s")
        monkeypatch.setattr(fi, "get_lab_info", lambda h: {"LAN_IPADDR": "10.0.0.48"})
        monkeypatch.setattr(fi.Grid, "from_config",
                            staticmethod(lambda cfg, check=True: _grid(app, returncode)))
        monkeypatch.setattr(fi, "ssh_reachable", lambda ip: ssh)
        logger = MagicMock()
        return app, logger, lambda: fi.fresh_install("vm-48-10", "img.ova", logger=logger)
    return go


def test_clean_install(install):
    app, logger, call = install()
    assert call() is app
    logger.warning.assert_not_called()


def test_nonzero_exit_but_verified(install):
    app, logger, call = install(returncode=1)
    assert call() is app
    assert "exited %d but" in logger.warning.call_args[0][0]


def test_wrong_host_fails(install):
    _, _, call = install(returncode=1, tag=("vm-99-99", "10.0.0.48"))
    with pytest.raises(RuntimeError, match="tag file has"):
        call()


def test_wrong_ip_fails(install):
    _, _, call = install(tag=("vm-48-10", "10.9.9.9"))
    with pytest.raises(RuntimeError, match="tag file has"):
        call()


def test_wapi_not_ready_fails(install):
    _, _, call = install(returncode=1, wapi_ok=False)
    with pytest.raises(RuntimeError, match="WAPI not ready"):
        call()


def test_ssh_fails(install):
    _, _, call = install(returncode=1, ssh=False)
    with pytest.raises(RuntimeError, match="SSH to 10.0.0.48 failed"):
        call()


def test_passes_standalone_config_check_false(monkeypatch, install):
    app, _, call = install()
    seen = {}

    def fake(cfg, check=True):
        seen.update(cfg=cfg, check=check)
        return _grid(app, 0)

    monkeypatch.setattr(fi.Grid, "from_config", staticmethod(fake))
    call()
    assert seen["check"] is False
    assert (seen["cfg"].topology, seen["cfg"].member, seen["cfg"].file) == (
        "standalone", "vm-48-10", "img.ova")
