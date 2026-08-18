"""Unit tests for config.py dataclasses: __post_init__ defaulting logic only.

No I/O, no transport, no network — these are plain dataclasses. Defaults
is the one exception (it reads a JSON file), so its tests use tmp_path
rather than touching the real src/config.json.
"""

import json
import os

import pytest

from nios_test import (
    ApplianceConfig,
    BuildConfig,
    Defaults,
    GridConfig,
    LocalApplianceConfig,
    LocalContainerConfig,
)
from nios_test.src import config as config_module

_EXAMPLE_CONFIG_PATH = os.path.join(os.path.dirname(config_module.__file__), "config.example.json")

with open(_EXAMPLE_CONFIG_PATH) as _f:
    # Required fields only — drop optional ones (null in the example, e.g.
    # output_dir) so tests can add them back in explicitly where relevant.
    _REQUIRED_FIELDS = {k: v for k, v in json.load(_f).items() if v is not None}

# -- LocalContainerConfig -------------------------------------------------------

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


# -- Defaults --------------------------------------------------------------

def test_defaults_from_json_accepts_arbitrary_path(tmp_path):
    """from_json(path) isn't limited to src/config.json — any script can
    keep its own config.json (e.g. a ticket script pinned to a specific
    worktree/appliance) and load it explicitly by path."""
    config_file = tmp_path / "config.json"
    config_file.write_text(json.dumps(_REQUIRED_FIELDS))

    defaults = Defaults.from_json(str(config_file))

    for field, value in _REQUIRED_FIELDS.items():
        assert getattr(defaults, field) == value


def test_example_config_json_is_valid():
    """Guards against config.example.json drifting out of sync with the
    Defaults schema (e.g. a new required field added to one but not the
    other)."""
    defaults = Defaults.from_json(_EXAMPLE_CONFIG_PATH)

    for field in _REQUIRED_FIELDS:
        assert getattr(defaults, field)
    assert defaults.output_dir is None


def test_defaults_from_json_all_fields_optional(tmp_path):
    """A config.json only needs the fields the caller's *Config actually
    uses -- e.g. a LocalContainerConfig user never needs hostname/ip, so
    omitting them must not block loading the file at all."""
    config_file = tmp_path / "config.json"
    config_file.write_text(json.dumps({"container": "balerion"}))

    defaults = Defaults.from_json(str(config_file))

    assert defaults.hostname is None
    assert defaults.ip is None
    assert defaults.worktree is None
    assert defaults.container == "balerion"
    assert defaults.chroot == config_module.DEFAULT_CHROOT_PATH
    assert defaults.log_level == config_module.DEFAULT_LOG_LEVEL


def test_defaults_from_json_empty_file(tmp_path):
    config_file = tmp_path / "config.json"
    config_file.write_text("{}")

    defaults = Defaults.from_json(str(config_file))

    assert defaults.hostname is None
    assert defaults.container is None


def test_defaults_from_json_output_dir_defaults_to_none(tmp_path):
    config_file = tmp_path / "config.json"
    config_file.write_text(json.dumps(_REQUIRED_FIELDS))

    defaults = Defaults.from_json(str(config_file))

    assert defaults.output_dir is None


def test_defaults_from_json_respects_explicit_output_dir(tmp_path):
    config_file = tmp_path / "config.json"
    config_file.write_text(json.dumps({**_REQUIRED_FIELDS, "output_dir": "/tmp/results"}))

    defaults = Defaults.from_json(str(config_file))

    assert defaults.output_dir == "/tmp/results"


def test_defaults_from_json_missing_file_raises(tmp_path):
    missing = tmp_path / "config.json"

    with pytest.raises(FileNotFoundError, match="config.example.json"):
        Defaults.from_json(str(missing))


def test_defaults_from_json_uses_colocated_config_when_no_path_given(tmp_path, monkeypatch):
    config_file = tmp_path / "config.json"
    config_file.write_text(json.dumps(_REQUIRED_FIELDS))
    monkeypatch.setattr(config_module, "_DEFAULT_CONFIG_PATH", str(config_file))

    defaults = Defaults.from_json()

    assert defaults.hostname == "vm-04-89"
