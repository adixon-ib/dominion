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

IMG = "nios-9.2.0-1-fixed-500G.ova"
FULL_IMG = f"/import/builds/nios/develop/9.2/NIOS_9.2.0_1_abc/{IMG}"


def _tag(tmp_path, image):
    (tmp_path / "t.xml").write_text(
        f'<root><master UNIT_1_ID="vm-48-10" UNIT_1_IPADDR="10.0.0.48" image="{image}"/></root>'
    )


@pytest.fixture
def install(monkeypatch, tmp_path):
    def go(returncode=0, tag=("vm-48-10", "10.0.0.48"), ssh=True, wapi_ok=True,
           tag_image=IMG, image=FULL_IMG, lan_ip="10.0.0.48", no_master=False):
        _tag(tmp_path, tag_image)
        app = None if no_master else SimpleNamespace(hostname=tag[0], ip=tag[1], wapi=MagicMock())
        if not wapi_ok:
            app.wapi.wait_until_ready.side_effect = RuntimeError("WAPI not ready after 300s")
        order = []
        monkeypatch.setattr(fi, "get_lab_info", lambda h: {"LAN_IPADDR": lan_ip})
        monkeypatch.setattr(fi.Grid, "from_config", staticmethod(
            lambda cfg, check=True: SimpleNamespace(
                gm=app, returncode=returncode, setup_dir=str(tmp_path), tag="t")))
        monkeypatch.setattr(fi, "ssh_reachable", lambda ip: order.append("ssh") or ssh)
        if app:
            app.wapi.wait_until_ready.side_effect = (
                (lambda: order.append("wapi")) if wapi_ok else app.wapi.wait_until_ready.side_effect)
        logger = MagicMock()
        return app, logger, order, lambda: fi.fresh_install("vm-48-10", image, logger=logger)
    return go


def test_clean_install(install):
    app, logger, order, call = install()
    assert call() is app
    logger.warning.assert_not_called()
    assert order == ["ssh", "wapi"]


def test_nonzero_exit_but_verified(install):
    app, logger, _, call = install(returncode=1)
    assert call() is app
    assert "exited %d but" in logger.warning.call_args[0][0]


def test_wrong_host_fails(install):
    _, _, _, call = install(returncode=1, tag=("vm-99-99", "10.0.0.48"))
    with pytest.raises(RuntimeError, match="tag file has"):
        call()


def test_wrong_ip_fails(install):
    _, _, _, call = install(tag=("vm-48-10", "10.9.9.9"))
    with pytest.raises(RuntimeError, match="tag file has"):
        call()


def test_no_master_in_tag_file_fails(install):
    _, _, _, call = install(returncode=1, no_master=True)
    with pytest.raises(RuntimeError, match="tag file has None"):
        call()


def test_empty_lan_ipaddr_fails(install):
    _, _, _, call = install(lan_ip="")
    with pytest.raises(RuntimeError, match="no LAN_IPADDR"):
        call()


def test_stale_image_in_tag_file_fails(install):
    _, _, _, call = install(returncode=1, tag_image="nios-old.ova")
    with pytest.raises(RuntimeError, match="tag file image 'nios-old.ova'"):
        call()


def test_image_with_deployment_option_matches(install):
    app, _, _, call = install(tag_image=f"{IMG}#105", image=f"{FULL_IMG}#105")
    assert call() is app


def test_wapi_not_ready_fails(install):
    _, _, _, call = install(returncode=1, wapi_ok=False)
    with pytest.raises(RuntimeError, match="WAPI not ready"):
        call()


def test_ssh_fails_before_wapi(install):
    app, _, order, call = install(returncode=1, ssh=False)
    with pytest.raises(RuntimeError, match="SSH to 10.0.0.48 failed"):
        call()
    assert order == ["ssh"]


@pytest.mark.parametrize("image", [FULL_IMG, IMG])
def test_image_passed_through_unchanged(monkeypatch, install, image):
    app, _, _, call = install(image=image, tag_image=IMG)
    seen = {}

    def fake(cfg, check=True):
        seen.update(cfg=cfg, check=check)
        return SimpleNamespace(gm=app, returncode=0, setup_dir=seen["cfg"].setup_dir, tag=seen["cfg"].tag)

    monkeypatch.setattr(fi.Grid, "from_config", staticmethod(fake))
    monkeypatch.setattr(fi, "_parse_tag_file", lambda f: {"master": {"image": IMG}})
    fi.fresh_install("vm-48-10", image, logger=MagicMock())
    assert seen["check"] is False
    assert (seen["cfg"].topology, seen["cfg"].member, seen["cfg"].file) == (
        "standalone", "vm-48-10", image)
