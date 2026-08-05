"""Unit tests for config.py dataclasses: __post_init__ defaulting logic only.

No I/O, no transport, no network — these are plain dataclasses.
"""

from nios_test import (
    ApplianceConfig,
    BuildConfig,
    ContainerConfig,
    GridConfig,
    LocalApplianceConfig,
    LocalContainerConfig,
)

# -- ContainerConfig / LocalContainerConfig -----------------------------------

def test_container_config_stores_required_fields():
    cfg = ContainerConfig(container_name="nios", host="10.35.211.4")
    assert cfg.container_name == "nios"
    assert cfg.host == "10.35.211.4"
    assert cfg.chroot == "/local/91/aslan_chroot"
    assert cfg.log_file is None


def test_container_config_output_dir_resolves_log_file(tmp_path):
    cfg = ContainerConfig(
        container_name="nios", host="10.35.211.4", output_dir=str(tmp_path),
    )
    assert cfg.log_file == str(tmp_path / "nios.log")


def test_container_config_explicit_log_file_wins_over_output_dir(tmp_path):
    explicit = str(tmp_path / "explicit.log")
    cfg = ContainerConfig(
        container_name="nios", host="10.35.211.4",
        log_file=explicit, output_dir=str(tmp_path),
    )
    assert cfg.log_file == explicit


def test_local_container_config_output_dir_resolves_log_file(tmp_path):
    cfg = LocalContainerConfig(container_name="balerion", output_dir=str(tmp_path))
    assert cfg.log_file == str(tmp_path / "balerion.log")


# -- ApplianceConfig / LocalApplianceConfig -----------------------------------

def test_appliance_config_defaults_container_name_to_nios():
    cfg = ApplianceConfig(hostname="vm-04-89")
    assert cfg.container_name == "nios"


def test_appliance_config_respects_explicit_container_name():
    cfg = ApplianceConfig(hostname="vm-04-89", container_name="other")
    assert cfg.container_name == "other"


def test_appliance_config_output_dir_resolves_log_file(tmp_path):
    cfg = ApplianceConfig(hostname="vm-04-89", output_dir=str(tmp_path))
    assert cfg.log_file == str(tmp_path / "vm-04-89.log")


def test_local_appliance_config_output_dir_resolves_log_file(tmp_path):
    cfg = LocalApplianceConfig(container_name="balerion", output_dir=str(tmp_path))
    assert cfg.log_file == str(tmp_path / "balerion.log")


# -- GridConfig ----------------------------------------------------------------

def test_grid_config_log_file_label_prefers_member():
    cfg = GridConfig(
        topology="standalone", member="vm-02-89", gm="vm-03-89", output_dir="/tmp/x",
    )
    assert cfg.log_file == "/tmp/x/grid-vm-02-89.log"


def test_grid_config_log_file_label_falls_back_to_gm():
    cfg = GridConfig(topology="2member", gm="vm-03-89", output_dir="/tmp/x")
    assert cfg.log_file == "/tmp/x/grid-vm-03-89.log"


def test_grid_config_log_file_label_falls_back_to_topology():
    cfg = GridConfig(topology="2member", output_dir="/tmp/x")
    assert cfg.log_file == "/tmp/x/grid-2member.log"


def test_grid_config_no_output_dir_means_no_log_file():
    cfg = GridConfig(topology="2member")
    assert cfg.log_file is None


# -- BuildConfig ---------------------------------------------------------------

def test_build_config_chroot_defaults_from_slot():
    cfg = BuildConfig(worktree="/blox/adixon1/my-branch", slot="balerion")
    assert cfg.chroot == "/local/91/chroots/balerion"


def test_build_config_respects_explicit_chroot():
    cfg = BuildConfig(
        worktree="/blox/adixon1/my-branch", slot="balerion", chroot="/custom/chroot",
    )
    assert cfg.chroot == "/custom/chroot"


def test_build_config_output_dir_resolves_log_file(tmp_path):
    cfg = BuildConfig(worktree="/x", slot="balerion", output_dir=str(tmp_path))
    assert cfg.log_file == str(tmp_path / "build-balerion.log")
