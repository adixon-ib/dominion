"""Unit tests for grid.py: _build_cmd, _parse_tag_file, Grid.appliances,
_make_appliance. No real grid_deploy subprocess and no real SSH probe."""

import logging

import nios_test.grid as grid_mod
from nios_test import ApplianceConfig, Grid, GridConfig
from nios_test.grid import _build_cmd, _make_appliance, _parse_tag_file

LOGGER = logging.getLogger("nios_test.unit_test")


# -- _build_cmd() -----------------------------------------------------------------

def test_build_cmd_minimal():
    cfg = GridConfig(topology="standalone")
    cmd = _build_cmd(cfg)
    assert cmd == [grid_mod.GRID_DEPLOY, "standalone", "--max-retries", "5"]


def test_build_cmd_includes_all_optional_fields():
    cfg = GridConfig(
        topology="2member", member="vm-02-89", gm="vm-03-89", ha="vm-04-89",
        file="image.bin", ova="image.ova", bin="image.bin2",
        ipv4_only=True, max_retries=3,
    )
    cmd = _build_cmd(cfg)
    assert "--member" in cmd and cmd[cmd.index("--member") + 1] == "vm-02-89"
    assert "--gm" in cmd and cmd[cmd.index("--gm") + 1] == "vm-03-89"
    assert "--ha" in cmd and cmd[cmd.index("--ha") + 1] == "vm-04-89"
    assert "--file" in cmd and cmd[cmd.index("--file") + 1] == "image.bin"
    assert "--ova" in cmd and cmd[cmd.index("--ova") + 1] == "image.ova"
    assert "--bin" in cmd and cmd[cmd.index("--bin") + 1] == "image.bin2"
    assert "--ipv4-only" in cmd
    assert cmd[-2:] == ["--max-retries", "3"]


# -- _parse_tag_file() ---------------------------------------------------------------

_MASTER_ONLY_XML = """<?xml version="1.0"?>
<root>
  <master>
    <UNIT_1_ID>vm-03-89</UNIT_1_ID>
    <UNIT_1_IPADDR>10.35.211.3</UNIT_1_IPADDR>
  </master>
</root>
"""

_MASTER_PLUS_MEMBERS_XML = """<?xml version="1.0"?>
<root>
  <master>
    <UNIT_1_ID>vm-03-89</UNIT_1_ID>
    <UNIT_1_IPADDR>10.35.211.3</UNIT_1_IPADDR>
  </master>
  <member0>
    <UNIT_1_ID>vm-04-89</UNIT_1_ID>
    <UNIT_1_IPADDR>10.35.211.4</UNIT_1_IPADDR>
  </member0>
  <member1>
    <UNIT_1_ID>vm-05-89</UNIT_1_ID>
    <UNIT_1_IPADDR>10.35.211.5</UNIT_1_IPADDR>
  </member1>
</root>
"""


def test_parse_tag_file_master_only(tmp_path, monkeypatch):
    tag_file = tmp_path / "mygrid.xml"
    tag_file.write_text(_MASTER_ONLY_XML)
    monkeypatch.setattr(grid_mod, "TAG_FILE", str(tag_file))

    result = _parse_tag_file()

    assert result == {"master": {"hostname": "vm-03-89", "ip": "10.35.211.3"}}


def test_parse_tag_file_master_plus_members(tmp_path, monkeypatch):
    tag_file = tmp_path / "mygrid.xml"
    tag_file.write_text(_MASTER_PLUS_MEMBERS_XML)
    monkeypatch.setattr(grid_mod, "TAG_FILE", str(tag_file))

    result = _parse_tag_file()

    assert result["master"]["hostname"] == "vm-03-89"
    assert result["member0"] == {"hostname": "vm-04-89", "ip": "10.35.211.4"}
    assert result["member1"] == {"hostname": "vm-05-89", "ip": "10.35.211.5"}
    assert "member2" not in result


# -- Grid.appliances property ---------------------------------------------------------

def test_grid_appliances_gm_only():
    g = Grid(gm="GM_STUB", members=[], topology="standalone", logger=LOGGER)
    assert g.appliances == ["GM_STUB"]


def test_grid_appliances_gm_plus_members():
    g = Grid(gm="GM_STUB", members=["M0_STUB", "M1_STUB"], topology="2member", logger=LOGGER)
    assert g.appliances == ["GM_STUB", "M0_STUB", "M1_STUB"]


def test_grid_appliances_no_gm_no_members():
    g = Grid(gm=None, members=[], topology="standalone", logger=LOGGER)
    assert g.appliances == []


# -- _make_appliance() ----------------------------------------------------------------

def test_make_appliance_builds_correct_config(monkeypatch):
    captured = {}

    def fake_from_config(cfg):
        captured["cfg"] = cfg
        return "APPLIANCE_STUB"

    monkeypatch.setattr(grid_mod.Appliance, "from_config", staticmethod(fake_from_config))

    cfg = GridConfig(
        topology="standalone", chroot="/local/91/chroots/balerion",
        worktree="/blox/adixon1/my-branch", log_level="debug",
    )
    result = _make_appliance("vm-02-89", "10.35.211.2", cfg, LOGGER)

    assert result == "APPLIANCE_STUB"
    built_cfg = captured["cfg"]
    assert isinstance(built_cfg, ApplianceConfig)
    assert built_cfg.hostname == "vm-02-89"
    assert built_cfg.ip == "10.35.211.2"
    assert built_cfg.chroot == "/local/91/chroots/balerion"
    assert built_cfg.worktree == "/blox/adixon1/my-branch"
    assert built_cfg.log_level == "debug"


def test_make_appliance_empty_ip_becomes_none(monkeypatch):
    captured = {}
    monkeypatch.setattr(
        grid_mod.Appliance, "from_config",
        staticmethod(lambda cfg: captured.setdefault("cfg", cfg)),
    )
    cfg = GridConfig(topology="standalone")
    _make_appliance("vm-02-89", "", cfg, LOGGER)
    assert captured["cfg"].ip is None
