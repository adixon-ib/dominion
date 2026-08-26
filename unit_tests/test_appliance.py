"""Unit tests for appliance.py: Appliance, LocalAppliance, ssh_reachable().

Constructing a real `Appliance` normally probes SSH for real (`_ensure_ssh()`
in `__init__`) — every test that constructs one here mocks `ssh_reachable`
so no real network call happens. `_ensure_ssh`/`_enable_ssh_via_console` are
also tested directly against a bare (non-`__init__`'d) instance, since those
methods hold the real branching logic and were previously untested.
"""

import logging

import pytest

import dominion.src.appliance as appliance_mod
from dominion import (
    Appliance,
    ApplianceConfig,
    LocalAppliance,
    LocalApplianceConfig,
    LocalDockerTransport,
    LocalNiosContainer,
    SSHDockerTransport,
)

LOGGER = logging.getLogger("dominion.unit_test")
DEFAULT_HOSTNAME = "vm-04-89"
DEFAULT_IP = "10.35.211.4"
DEFAULT_CONTAINER = "balerion"
DEFAULT_CHROOT = "/local/91/chroots/balerion"


@pytest.fixture
def ssh_always_reachable(monkeypatch):
    monkeypatch.setattr(appliance_mod, "ssh_reachable", lambda host, timeout=5: True)


def _bare_appliance(hostname=DEFAULT_HOSTNAME, ip=DEFAULT_IP):
    """An Appliance built without running __init__ (skips _ensure_ssh()),
    for testing _ensure_ssh/_enable_ssh_via_console in isolation."""
    a = Appliance.__new__(Appliance)
    a._hostname = hostname
    a._ip = ip
    a._console_user = "admin"
    a._console_password = "infoblox"
    a._logger = LOGGER
    return a


# -- ApplianceConfig / LocalApplianceConfig (no I/O) ------------------------------

def test_config_defaults_container_name_to_nios():
    cfg = ApplianceConfig(hostname=DEFAULT_HOSTNAME, ip=DEFAULT_IP)
    assert cfg.container_name == "nios"


def test_config_output_dir_resolves_log_file(tmp_path):
    cfg = ApplianceConfig(
        hostname=DEFAULT_HOSTNAME, ip=DEFAULT_IP, output_dir=str(tmp_path),
    )
    assert cfg.log_file == str(tmp_path / f"{DEFAULT_HOSTNAME}.log")


def test_local_appliance_config_output_dir_resolves_log_file(tmp_path):
    cfg = LocalApplianceConfig(
        container_name=DEFAULT_CONTAINER, output_dir=str(tmp_path),
    )
    assert cfg.log_file == str(tmp_path / f"{DEFAULT_CONTAINER}.log")


# -- from_config wiring ------------------------------------------------------------

def test_appliance_from_config_creates_ssh_transport(ssh_always_reachable):
    cfg = ApplianceConfig(
        hostname=DEFAULT_HOSTNAME, ip=DEFAULT_IP, chroot=DEFAULT_CHROOT,
    )
    appliance = Appliance.from_config(cfg)
    transport = appliance.container._transport
    assert isinstance(transport, SSHDockerTransport)
    assert transport.host == DEFAULT_IP


def test_local_appliance_from_config_creates_local_transport():
    cfg = LocalApplianceConfig(
        container_name=DEFAULT_CONTAINER, chroot=DEFAULT_CHROOT, ip=DEFAULT_IP,
    )
    appliance = LocalAppliance.from_config(cfg)
    transport = appliance.container._transport
    assert isinstance(transport, LocalDockerTransport)
    assert isinstance(appliance.container, LocalNiosContainer)


def test_appliance_repr(ssh_always_reachable):
    cfg = ApplianceConfig(hostname=DEFAULT_HOSTNAME, ip=DEFAULT_IP)
    appliance = Appliance.from_config(cfg)
    r = repr(appliance)
    assert DEFAULT_HOSTNAME in r
    assert "nios" in r


def test_local_appliance_repr():
    cfg = LocalApplianceConfig(
        container_name=DEFAULT_CONTAINER, chroot=DEFAULT_CHROOT, ip=DEFAULT_IP,
    )
    appliance = LocalAppliance.from_config(cfg)
    assert DEFAULT_CONTAINER in repr(appliance)


def test_local_appliance_ip_provided_skips_set_network(monkeypatch):
    called = {"set_network": False}
    monkeypatch.setattr(
        LocalNiosContainer, "set_network",
        lambda self, **kw: (
            called.__setitem__("set_network", True) or "should-not-be-used"
        ),
    )
    cfg = LocalApplianceConfig(
        container_name=DEFAULT_CONTAINER, chroot=DEFAULT_CHROOT, ip=DEFAULT_IP,
    )
    appliance = LocalAppliance.from_config(cfg)

    assert called["set_network"] is False
    assert appliance.ip == DEFAULT_IP


def test_local_appliance_no_ip_triggers_set_network(monkeypatch):
    monkeypatch.setattr(
        LocalNiosContainer, "set_network", lambda self, **kw: "172.20.0.5",
    )
    cfg = LocalApplianceConfig(
        container_name=DEFAULT_CONTAINER, chroot=DEFAULT_CHROOT, ip=None,
    )
    appliance = LocalAppliance.from_config(cfg)

    assert appliance.ip == "172.20.0.5"


# -- ssh_reachable() ----------------------------------------------------------------

def test_ssh_reachable_true_on_zero_exit(monkeypatch):
    monkeypatch.setattr(
        appliance_mod.subprocess, "run",
        lambda *a, **k: type("R", (), {"returncode": 0})(),
    )
    assert appliance_mod.ssh_reachable("10.0.0.1") is True


def test_ssh_reachable_false_on_nonzero_exit(monkeypatch):
    monkeypatch.setattr(
        appliance_mod.subprocess, "run",
        lambda *a, **k: type("R", (), {"returncode": 255})(),
    )
    assert appliance_mod.ssh_reachable("10.0.0.1") is False


def test_ssh_reachable_false_on_exception(monkeypatch):
    def raise_exc(*a, **k):
        raise Exception("boom")

    monkeypatch.setattr(appliance_mod.subprocess, "run", raise_exc)
    assert appliance_mod.ssh_reachable("10.0.0.1") is False


# -- Appliance._ensure_ssh() ---------------------------------------------------------

def test_ensure_ssh_noop_when_already_reachable(monkeypatch):
    a = _bare_appliance()
    monkeypatch.setattr(appliance_mod, "ssh_reachable", lambda host, timeout=5: True)
    called = {"enable": False}
    monkeypatch.setattr(
        a, "_enable_ssh_via_console", lambda: called.__setitem__("enable", True),
    )

    a._ensure_ssh()

    assert called["enable"] is False


def test_ensure_ssh_enables_then_succeeds(monkeypatch):
    a = _bare_appliance()
    responses = iter([False, True])
    monkeypatch.setattr(
        appliance_mod, "ssh_reachable", lambda host, timeout=5: next(responses),
    )
    monkeypatch.setattr(a, "_enable_ssh_via_console", lambda: None)

    a._ensure_ssh()  # must not raise


def test_ensure_ssh_raises_when_still_unreachable(monkeypatch):
    a = _bare_appliance()
    monkeypatch.setattr(appliance_mod, "ssh_reachable", lambda host, timeout=5: False)
    monkeypatch.setattr(a, "_enable_ssh_via_console", lambda: None)

    with pytest.raises(RuntimeError):
        a._ensure_ssh()


# -- Appliance._enable_ssh_via_console() ---------------------------------------------

def _patch_console_env(monkeypatch, fake_child_factory, script):
    child = fake_child_factory(script=script)
    monkeypatch.setattr(appliance_mod.pexpect, "spawn", lambda *a, **k: child)
    monkeypatch.setattr(appliance_mod.time, "sleep", lambda s: None)
    monkeypatch.setattr(
        appliance_mod.subprocess, "run",
        lambda *a, **k: type("R", (), {"returncode": 0})(),
    )
    return child


def test_enable_ssh_via_console_login_branch(monkeypatch, fake_child_factory):
    script = [(0, "", "login: ")] + [(0, "", "y or n): ") for _ in range(8)]
    child = _patch_console_env(monkeypatch, fake_child_factory, script)

    a = _bare_appliance()
    a._enable_ssh_via_console()

    assert ("sendline", "admin") in child.sent
    assert ("sendline", "infoblox") in child.sent
    assert ("sendline", "set remote_console") in child.sent
    assert ("sendline", "set support_access") in child.sent
    assert ("sendline", "exit") in child.sent


def test_enable_ssh_via_console_password_only_branch(monkeypatch, fake_child_factory):
    script = [(1, "", "Password: ")] + [(0, "", "y or n): ") for _ in range(7)]
    child = _patch_console_env(monkeypatch, fake_child_factory, script)

    a = _bare_appliance()
    a._enable_ssh_via_console()

    assert child.sent[0] == ("sendline", "infoblox")


def test_enable_ssh_via_console_already_at_prompt_skips_auth(
    monkeypatch, fake_child_factory,
):
    script = [(2, "", "Infoblox > ")] + [(0, "", "y or n): ") for _ in range(6)]
    child = _patch_console_env(monkeypatch, fake_child_factory, script)

    a = _bare_appliance()
    a._enable_ssh_via_console()

    assert child.sent[0] == ("sendline", "set remote_console")


def test_enable_ssh_via_console_returns_gracefully_on_connect_failure(monkeypatch):
    a = _bare_appliance()

    def raise_spawn(*args, **kwargs):
        raise Exception("connection refused")

    monkeypatch.setattr(appliance_mod.pexpect, "spawn", raise_spawn)

    a._enable_ssh_via_console()  # must not raise


# -- wapi / wapi_version (on _ApplianceBase) ----------------------------------------

def _local_appliance():
    cfg = LocalApplianceConfig(
        container_name=DEFAULT_CONTAINER, chroot=DEFAULT_CHROOT, ip=DEFAULT_IP,
    )
    return LocalAppliance.from_config(cfg)


def test_wapi_property_returns_wapi_client():
    from dominion import WapiClient
    app = _local_appliance()
    assert isinstance(app.wapi, WapiClient)


def test_wapi_property_is_cached():
    app = _local_appliance()
    assert app.wapi is app.wapi


def test_wapi_version_defaults_to_constant():
    from dominion.src.constants import WAPI_VERSION
    app = _local_appliance()
    assert app.wapi_version == WAPI_VERSION


def test_wapi_version_setter_rebuilds_client():
    app = _local_appliance()
    old_client = app.wapi
    app.wapi_version = "v2.15"
    assert app.wapi is not old_client
    assert app.wapi_version == "v2.15"


def test_wapi_version_setter_propagates_to_client():
    app = _local_appliance()
    app.wapi_version = "v2.15"
    assert app.wapi._version == "v2.15"
