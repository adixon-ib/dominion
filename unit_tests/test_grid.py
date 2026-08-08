"""Unit tests for grid.py: _build_cmd, _parse_tag_file, Grid.appliances,
_make_appliance. No real grid_deploy subprocess and no real SSH probe."""

import logging
from collections import namedtuple

import nios_test.src.grid as grid_mod
from nios_test import ApplianceConfig, Grid, GridConfig
from nios_test.src.grid import _build_cmd, _make_appliance, _parse_tag_file

LOGGER = logging.getLogger("nios_test.unit_test")

MockAppliance = namedtuple("MockAppliance", ["hostname", "ip"])


# -- _build_cmd() -----------------------------------------------------------------

def test_build_cmd_minimal():
    cfg = GridConfig(topology="standalone", setup_dir="/tmp/my_setup-test", tag="mygrid-test")
    cmd = _build_cmd(cfg)
    assert cmd == [
        grid_mod.GRID_DEPLOY, "standalone", "--max-retries", "5",
        "--setup-dir", "/tmp/my_setup-test", "--tag", "mygrid-test",
    ]


def test_build_cmd_auto_generates_unique_setup_dir_and_tag():
    cfg1 = GridConfig(topology="standalone")
    cfg2 = GridConfig(topology="standalone")
    assert cfg1.setup_dir != cfg2.setup_dir
    assert cfg1.tag != cfg2.tag
    cmd = _build_cmd(cfg1)
    assert "--setup-dir" in cmd
    assert cmd[cmd.index("--setup-dir") + 1] == cfg1.setup_dir
    assert "--tag" in cmd
    assert cmd[cmd.index("--tag") + 1] == cfg1.tag


def test_build_cmd_includes_all_optional_fields():
    cfg = GridConfig(
        topology="2member", member="vm-02-89", gm="vm-03-89", ha="vm-04-89",
        file="image.bin", ova="image.ova", bin="image.bin2",
        ipv4_only=True, max_retries=3,
    )
    cmd = _build_cmd(cfg)
    assert "--member" in cmd
    assert cmd[cmd.index("--member") + 1] == "vm-02-89"
    assert "--gm" in cmd
    assert cmd[cmd.index("--gm") + 1] == "vm-03-89"
    assert "--ha" in cmd
    assert cmd[cmd.index("--ha") + 1] == "vm-04-89"
    assert "--file" in cmd
    assert cmd[cmd.index("--file") + 1] == "image.bin"
    assert "--ova" in cmd
    assert cmd[cmd.index("--ova") + 1] == "image.ova"
    assert "--bin" in cmd
    assert cmd[cmd.index("--bin") + 1] == "image.bin2"
    assert "--ipv4-only" in cmd
    assert "--max-retries" in cmd
    assert cmd[cmd.index("--max-retries") + 1] == "3"
    assert "--setup-dir" in cmd
    assert cmd[cmd.index("--setup-dir") + 1] == cfg.setup_dir
    assert "--tag" in cmd
    assert cmd[cmd.index("--tag") + 1] == cfg.tag


# -- _parse_tag_file() ---------------------------------------------------------------

# config_grid tag files store node fields as XML attributes, not child
# elements -- these fixtures match the real format (see grid.py's
# _parse_tag_file docstring/comment).
_MASTER_ONLY_XML = """<?xml version="1.0"?>
<root>
  <master UNIT_1_ID="vm-03-89" UNIT_1_IPADDR="10.35.211.3" />
</root>
"""

_MASTER_PLUS_MEMBERS_XML = """<?xml version="1.0"?>
<root>
  <master UNIT_1_ID="vm-03-89" UNIT_1_IPADDR="10.35.211.3" />
  <member0 UNIT_1_ID="vm-04-89" UNIT_1_IPADDR="10.35.211.4" />
  <member1 UNIT_1_ID="vm-05-89" UNIT_1_IPADDR="10.35.211.5" />
</root>
"""


def test_parse_tag_file_master_only(tmp_path):
    tag_file = tmp_path / "mygrid.xml"
    tag_file.write_text(_MASTER_ONLY_XML)

    result = _parse_tag_file(str(tag_file))

    assert result == {"master": {"hostname": "vm-03-89", "ip": "10.35.211.3"}}


def test_parse_tag_file_master_plus_members(tmp_path):
    tag_file = tmp_path / "mygrid.xml"
    tag_file.write_text(_MASTER_PLUS_MEMBERS_XML)

    result = _parse_tag_file(str(tag_file))

    assert result["master"]["hostname"] == "vm-03-89"
    assert result["member0"] == {"hostname": "vm-04-89", "ip": "10.35.211.4"}
    assert result["member1"] == {"hostname": "vm-05-89", "ip": "10.35.211.5"}
    assert "member2" not in result


# -- Grid.appliances property ---------------------------------------------------------

def test_grid_appliances_gm_only():
    g = Grid(gm="GM_STUB", members=[], topology="standalone", logger=LOGGER)
    assert g.appliances == ["GM_STUB"]


def test_grid_appliances_gm_plus_members():
    g = Grid(
        gm="GM_STUB", members=["M0_STUB", "M1_STUB"], topology="2member", logger=LOGGER,
    )
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

    monkeypatch.setattr(
        grid_mod.Appliance, "from_config", staticmethod(fake_from_config),
    )

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


# -- Grid.__str__() ------------------------------------------------------------------

def test_grid_str_gm_only():
    gm = MockAppliance(hostname="vm-03-89", ip="10.35.211.3")
    g = Grid(gm=gm, members=[], topology="standalone", logger=LOGGER)
    assert str(g) == "standalone: vm-03-89 (10.35.211.3)"


def test_grid_str_gm_plus_members():
    gm = MockAppliance(hostname="vm-03-89", ip="10.35.211.3")
    m0 = MockAppliance(hostname="vm-04-89", ip="10.35.211.4")
    m1 = MockAppliance(hostname="vm-05-89", ip="10.35.211.5")
    g = Grid(gm=gm, members=[m0, m1], topology="2member", logger=LOGGER)
    assert str(g) == "2member: vm-03-89 (10.35.211.3), vm-04-89 (10.35.211.4), vm-05-89 (10.35.211.5)"


def test_grid_str_appliance_without_ip():
    gm = MockAppliance(hostname="vm-03-89", ip=None)
    g = Grid(gm=gm, members=[], topology="standalone", logger=LOGGER)
    assert str(g) == "standalone: vm-03-89"


def test_grid_str_no_appliances():
    g = Grid(gm=None, members=[], topology="standalone", logger=LOGGER)
    assert str(g) == "standalone: "


def test_grid_str_includes_setup_dir_and_tag():
    gm = MockAppliance(hostname="vm-03-89", ip="10.35.211.3")
    g = Grid(
        gm=gm, members=[], topology="standalone", logger=LOGGER,
        setup_dir="/tmp/my_setup-abc12345", tag="grid-abc12345",
    )
    assert str(g) == (
        "standalone: vm-03-89 (10.35.211.3) "
        "[setup_dir=/tmp/my_setup-abc12345, tag=grid-abc12345]"
    )


def test_grid_repr_includes_setup_dir_and_tag():
    gm = MockAppliance(hostname="vm-03-89", ip="10.35.211.3")
    g = Grid(
        gm=gm, members=[], topology="standalone", logger=LOGGER,
        setup_dir="/tmp/my_setup-abc12345", tag="grid-abc12345",
    )
    assert repr(g) == (
        "Grid(topology='standalone', appliances=['10.35.211.3'], "
        "setup_dir='/tmp/my_setup-abc12345', tag='grid-abc12345')"
    )
