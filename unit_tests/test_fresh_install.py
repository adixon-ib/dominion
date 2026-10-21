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
    def run(returncode, tag_xml=TAG_XML, check=True, logger=LOGGER, verbose=False):
        if tag_xml is not None:
            (tmp_path / "t.xml").write_text(tag_xml)
        proc = MagicMock(returncode=returncode, stdout=iter(["line\n"]))
        monkeypatch.setattr(grid_mod.subprocess, "Popen", lambda *a, **k: proc)
        monkeypatch.setattr(grid_mod.Appliance, "from_config", staticmethod(
            lambda cfg: SimpleNamespace(hostname=cfg.hostname, ip=cfg.ip)))
        cfg = GridConfig(topology="standalone", setup_dir=str(tmp_path), tag="t", logger=logger)
        return Grid.from_config(cfg, check=check, verbose=verbose)
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


def test_from_config_unusable_tag_file_includes_output_tail(run_grid):
    with pytest.raises(RuntimeError, match="last grid_deploy output:\nline"):
        run_grid(3, tag_xml=None, check=False)


def test_from_config_keeps_gm_image(run_grid):
    xml = '<root><master UNIT_1_ID="vm-48-10" UNIT_1_IPADDR="10.0.0.48" image="a.ova"/></root>'
    assert run_grid(0, tag_xml=xml).gm_image == "a.ova"


def test_from_config_verbose_logs_output_at_info(run_grid):
    for verbose in (True, False):
        log = MagicMock()
        run_grid(0, logger=log, verbose=verbose)
        shown = (log.info if verbose else log.debug).call_args_list
        other = (log.debug if verbose else log.info).call_args_list
        assert any(c.args == ("line",) for c in shown)
        assert not any(c.args == ("line",) for c in other)


def test_from_config_zero_exit_returncode_zero(run_grid):
    assert run_grid(0).returncode == 0


# -- fresh_install ---------------------------------------------------------------

IMG = "nios-9.2.0-56265-fixed-500G.ova"
FULL_IMG = f"/import/builds/nios/develop/9.2/NIOS_9.2.0_56265_abc/{IMG}"
CONFIG = "CLIENT_VERSION=9.2.0-56265\nEXTERNAL_VERSION=9.2.0-56265-abcdef\nOTHER=1\n"


@pytest.fixture
def install(monkeypatch):
    def go(returncode=0, tag=("vm-48-10", "10.0.0.48"), ssh=True, wapi_ok=True,
           tag_image=IMG, image=FULL_IMG, lan_ip="10.0.0.48", no_master=False,
           config=CONFIG):
        order = []
        app = None
        if not no_master:
            app = SimpleNamespace(hostname=tag[0], ip=tag[1], wapi=MagicMock(),
                                  container=MagicMock())
            app.container._transport.run.return_value = (0, config, "")
            if wapi_ok:
                app.wapi.wait_until_ready.side_effect = lambda: order.append("wapi")
            else:
                app.wapi.wait_until_ready.side_effect = RuntimeError("WAPI not ready after 300s")
        calls = {"from_config": []}
        monkeypatch.setattr(fi, "get_lab_info", lambda h: {"LAN_IPADDR": lan_ip})

        def from_config(cfg, check=True, verbose=False):
            calls["from_config"].append((cfg, check, verbose))
            return SimpleNamespace(gm=app, returncode=returncode, gm_image=tag_image)

        monkeypatch.setattr(fi.Grid, "from_config", staticmethod(from_config))
        probes = iter(ssh) if isinstance(ssh, list) else None
        monkeypatch.setattr(
            fi, "ssh_reachable",
            lambda ip: order.append("ssh") or (next(probes, False) if probes else ssh))
        monkeypatch.setattr(fi.time, "sleep", lambda s: None)
        logger = MagicMock()
        return SimpleNamespace(
            app=app, logger=logger, order=order, calls=calls,
            call=lambda: fi.fresh_install("vm-48-10", image, logger=logger),
        )
    return go


def test_clean_install(install):
    t = install()
    assert t.call() is t.app
    t.logger.warning.assert_not_called()
    assert t.order == ["ssh", "wapi"]


def test_nonzero_exit_but_verified(install):
    t = install(returncode=1)
    assert t.call() is t.app
    assert "exited %d but" in t.logger.warning.call_args[0][0]


def test_wrong_host_fails(install):
    with pytest.raises(RuntimeError, match="tag file has"):
        install(returncode=1, tag=("vm-99-99", "10.0.0.48")).call()


def test_wrong_ip_fails(install):
    with pytest.raises(RuntimeError, match="tag file has"):
        install(tag=("vm-48-10", "10.9.9.9")).call()


def test_no_master_in_tag_file_fails(install):
    with pytest.raises(RuntimeError, match="tag file has None"):
        install(returncode=1, no_master=True).call()


def test_empty_lan_ipaddr_fails(install):
    with pytest.raises(RuntimeError, match="no LAN_IPADDR"):
        install(lan_ip="").call()


def test_stale_image_in_tag_file_fails(install):
    with pytest.raises(RuntimeError, match="tag file image 'nios-old.ova'"):
        install(returncode=1, tag_image="nios-old.ova").call()


def test_image_with_deployment_option_matches(install):
    t = install(tag_image=f"{IMG}#105", image=f"{FULL_IMG}#105")
    assert t.call() is t.app


def test_wapi_not_ready_fails(install):
    with pytest.raises(RuntimeError, match="WAPI not ready"):
        install(returncode=1, wapi_ok=False).call()


def test_ssh_fails_before_wapi_after_retries(install):
    t = install(returncode=1, ssh=False)
    with pytest.raises(RuntimeError, match="SSH to 10.0.0.48 failed after 60s"):
        t.call()
    assert t.order == ["ssh"] * 12


def test_ssh_retried_until_up(install):
    t = install(ssh=[False, False, True])
    assert t.call() is t.app
    assert t.order == ["ssh", "ssh", "ssh", "wapi"]


def test_stale_running_build_fails(install):
    t = install(returncode=1, config="EXTERNAL_VERSION=9.2.0-56206-abc\n")
    with pytest.raises(RuntimeError, match="running build '9.2.0-56206-abc' does not match"):
        t.call()


def test_running_build_prefix_number_not_a_match(install):
    with pytest.raises(RuntimeError, match="does not match"):
        install(config="EXTERNAL_VERSION=9.2.0-5626\n").call()


def test_custom_build_matches_whole_version(install):
    img = "nios-10.0.0-adixon1-2026-10-16-20-39-2026-10-19-fixed-500G.ova"
    t = install(image=img, tag_image=img,
                config="EXTERNAL_VERSION=10.0.0-adixon1-2026-10-16-20-39\n")
    assert t.call() is t.app


def test_client_version_fallback(install):
    assert install(config="CLIENT_VERSION=9.2.0-56265\n").call()


def test_no_version_in_config_fails(install):
    with pytest.raises(RuntimeError, match="no EXTERNAL_VERSION"):
        install(config="OTHER=1\n").call()


@pytest.mark.parametrize("image", [FULL_IMG, IMG])
def test_image_passed_through_unchanged_and_verbose(install, image):
    t = install(image=image)
    t.call()
    cfg, check, verbose = t.calls["from_config"][0]
    assert (check, verbose) == (False, True)
    assert (cfg.topology, cfg.member, cfg.file) == ("standalone", "vm-48-10", image)
