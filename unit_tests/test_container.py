"""Unit tests for container.py: NiosContainer / LocalNiosContainer.

Config-wiring tests carried over from the pre-split test_container.py.
New coverage: _wait_for_wapi, restart(), and LocalNiosContainer's previously
untested parsing logic (_get_container_network, _find_forwarding_bridge —
implemented in network.py's LocalNetworkMixin, layered on NiosContainer's shared
_configure_network_console) — all driven with a FakeTransport, never a real
docker/ssh call.
"""

import json
import logging
import tempfile
from contextlib import contextmanager

import pytest

from dominion import (
    LocalContainerConfig,
    LocalDockerTransport,
    LocalNiosContainer,
    NiosContainer,
    SSHDockerTransport,
)

LOGGER = logging.getLogger("dominion.unit_test")
DEFAULT_CHROOT = "/local/91/chroots/balerion"


# -- LocalContainerConfig / from_config wiring (carried over from mixed test file) --

def test_config_output_dir_resolves_log_file():
    with tempfile.TemporaryDirectory() as d:
        cfg = LocalContainerConfig(container_name="testbox", output_dir=d)
        assert cfg.log_file is not None
        assert d in cfg.log_file
        assert "testbox" in cfg.log_file


def test_from_config_local_creates_local_transport():
    cfg = LocalContainerConfig(container_name="balerion", chroot=DEFAULT_CHROOT)
    container = LocalNiosContainer.from_config(cfg)
    assert isinstance(container._transport, LocalDockerTransport)
    assert container._transport.container_name == "balerion"


def test_from_config_logger_name():
    cfg = LocalContainerConfig(container_name="balerion", chroot=DEFAULT_CHROOT)
    container = LocalNiosContainer.from_config(cfg)
    assert container._logger.name == "dominion.balerion"


def test_local_nios_container_repr():
    cfg = LocalContainerConfig(container_name="balerion", chroot=DEFAULT_CHROOT)
    container = LocalNiosContainer.from_config(cfg)
    r = repr(container)
    assert "balerion" in r
    assert "Local" in r


def test_local_nios_container_is_nios_container():
    cfg = LocalContainerConfig(container_name="balerion", chroot=DEFAULT_CHROOT)
    container = LocalNiosContainer.from_config(cfg)
    assert isinstance(container, NiosContainer)


# -- _wait_for_wapi ---------------------------------------------------------------

def test_wait_for_wapi_returns_on_first_success(fake_transport):
    c = NiosContainer(
        container_name="nios",
        transport=fake_transport,
        chroot=DEFAULT_CHROOT,
        logger=LOGGER,
    )
    # default_response is (0, "", "") -- succeeds immediately
    c._wait_for_wapi(timeout=5)
    assert any(call[0] == "run" for call in fake_transport.calls)


def test_wait_for_wapi_raises_after_timeout(monkeypatch, fake_transport_factory):
    transport = fake_transport_factory(default_response=(1, "", "connection refused"))
    c = NiosContainer(
        container_name="nios",
        transport=transport,
        chroot=DEFAULT_CHROOT,
        logger=LOGGER,
    )

    import dominion.src.container as container_mod
    monkeypatch.setattr(container_mod.time, "sleep", lambda s: None)
    monkeypatch.setattr(container_mod.time, "time", _make_fake_clock())

    with pytest.raises(RuntimeError):
        c._wait_for_wapi(timeout=1)


def _make_fake_clock():
    """A time.time() replacement that advances past any deadline on the
    second call, so retry loops terminate without a real sleep."""
    state = {"n": 0}

    def fake_time():
        state["n"] += 1
        return 0 if state["n"] == 1 else 999999

    return fake_time


# -- restart() ----------------------------------------------------------------

def test_restart_calls_docker_restart_then_waits_for_wapi(fake_transport):
    c = NiosContainer(
        container_name="nios",
        transport=fake_transport,
        chroot=DEFAULT_CHROOT,
        logger=LOGGER,
    )
    c.restart(timeout=5)
    host_cmds = [call[1] for call in fake_transport.calls if call[0] == "run_on_host"]
    assert any("docker restart nios" in cmd for cmd in host_cmds)
    assert any(call[0] == "run" for call in fake_transport.calls)  # the WAPI poll


# -- NiosContainer.console_session --------------------------------------------

def test_console_session_builds_ssh_spawn_cmd_from_transport(monkeypatch):
    transport = SSHDockerTransport(host="vm-04-89", container_name="nios")
    c = NiosContainer(
        container_name="nios",
        transport=transport,
        chroot=DEFAULT_CHROOT,
        logger=LOGGER,
    )

    captured = {}

    class FakeConsoleSession:
        def __init__(self, **kwargs):
            captured.update(kwargs)

        def _open(self):
            pass

        def _close(self):
            pass

    import dominion.src.container as container_mod
    monkeypatch.setattr(container_mod, "ConsoleSession", FakeConsoleSession)

    with c.console_session():
        pass

    assert captured["spawn_cmd"] == (
        "ssh -o StrictHostKeyChecking=no -o LogLevel=ERROR -o ConnectTimeout=10 "
        "-p 22 admin@vm-04-89"
    )
    assert captured["user"] == "admin"
    assert captured["password"] == "infoblox"


# -- LocalNiosContainer._get_container_network ------------------------------------

DEFAULT_PREFIX_LEN = 24


def _inspect_json(ip="172.20.0.5", prefix=DEFAULT_PREFIX_LEN, network="ncc_lan1"):
    return json.dumps([{
        "NetworkSettings": {
            "Networks": {network: {"IPAddress": ip, "IPPrefixLen": prefix}},
        },
    }])


def test_get_container_network_parses_ip_and_prefix(fake_transport_factory):
    transport = fake_transport_factory(
        responses={"docker inspect": (0, _inspect_json(), "")},
    )
    c = LocalNiosContainer(
        container_name="balerion",
        transport=transport,
        chroot=DEFAULT_CHROOT,
        logger=LOGGER,
    )
    ip, prefix = c._get_container_network()
    assert ip == "172.20.0.5"
    assert prefix == DEFAULT_PREFIX_LEN


def test_get_container_ip_wraps_get_container_network(fake_transport_factory):
    transport = fake_transport_factory(
        responses={"docker inspect": (0, _inspect_json(ip="172.20.0.9"), "")},
    )
    c = LocalNiosContainer(
        container_name="balerion",
        transport=transport,
        chroot=DEFAULT_CHROOT,
        logger=LOGGER,
    )
    assert c.get_container_ip() == "172.20.0.9"


def test_get_container_network_raises_without_ncc_lan1(fake_transport_factory):
    transport = fake_transport_factory(
        responses={"docker inspect": (0, _inspect_json(network="bridge"), "")},
    )
    c = LocalNiosContainer(
        container_name="balerion",
        transport=transport,
        chroot=DEFAULT_CHROOT,
        logger=LOGGER,
    )
    with pytest.raises(RuntimeError, match="ncc_lan1"):
        c._get_container_network()


def test_get_container_network_raises_when_no_ip(fake_transport_factory):
    transport = fake_transport_factory(
        responses={"docker inspect": (0, _inspect_json(ip=""), "")},
    )
    c = LocalNiosContainer(
        container_name="balerion",
        transport=transport,
        chroot=DEFAULT_CHROOT,
        logger=LOGGER,
    )
    with pytest.raises(RuntimeError, match="no IP"):
        c._get_container_network()


# -- LocalNiosContainer._find_forwarding_bridge -----------------------------------

def test_find_forwarding_bridge_parses_master(fake_transport_factory):
    transport = fake_transport_factory(responses={
        "iflink": (0, "42\n", ""),
    })

    # run() is used for the /sys/class/net probe; run_on_host() for everything else.
    def fake_run_on_host(cmd, check=True):
        transport.calls.append(("run_on_host", cmd))
        if cmd == "ip -o link show":
            return 0, "42: veth1234@if43: <BROADCAST> mtu 1500\n", ""
        if cmd == "bridge link show dev veth1234":
            return 0, "42: veth1234 state forwarding\n", ""
        if cmd == "ip -o link show dev veth1234":
            return 0, "42: veth1234@if43: <BROADCAST> master br-abc123 state up\n", ""
        return 0, "", ""

    transport.run_on_host = fake_run_on_host
    c = LocalNiosContainer(
        container_name="balerion",
        transport=transport,
        chroot=DEFAULT_CHROOT,
        logger=LOGGER,
    )
    assert c._find_forwarding_bridge() == "br-abc123"


def test_find_forwarding_bridge_returns_none_when_not_forwarding(
    fake_transport_factory,
):
    transport = fake_transport_factory(responses={"iflink": (0, "42\n", "")})

    def fake_run_on_host(cmd, check=True):
        if cmd == "ip -o link show":
            return 0, "42: veth1234@if43: <BROADCAST> mtu 1500\n", ""
        if cmd == "bridge link show dev veth1234":
            return 0, "42: veth1234 state disabled\n", ""
        return 0, "", ""

    transport.run_on_host = fake_run_on_host
    c = LocalNiosContainer(
        container_name="balerion",
        transport=transport,
        chroot=DEFAULT_CHROOT,
        logger=LOGGER,
    )
    assert c._find_forwarding_bridge() is None


def test_find_forwarding_bridge_returns_none_without_indices(fake_transport_factory):
    transport = fake_transport_factory(responses={"iflink": (0, "", "")})
    c = LocalNiosContainer(
        container_name="balerion",
        transport=transport,
        chroot=DEFAULT_CHROOT,
        logger=LOGGER,
    )
    assert c._find_forwarding_bridge() is None


# -- LocalNiosContainer.set_network() ----------------------------------------------

class _FakeConsole:
    def __init__(self):
        self.sent = []

    def sendline(self, text):
        self.sent.append(text)

    def expect(self, pattern, timeout=None):
        return ""

    def expect_one_of(self, patterns, timeout=None):
        return 2  # shortcut straight to "Become grid member"


def test_set_network_computes_netmask_and_adds_route(monkeypatch, fake_transport):
    c = LocalNiosContainer(
        container_name="balerion",
        transport=fake_transport,
        chroot=DEFAULT_CHROOT,
        logger=LOGGER,
    )

    monkeypatch.setattr(c, "_get_container_network", lambda: ("172.20.0.5", 24))
    monkeypatch.setattr(c, "_find_forwarding_bridge", lambda: "br-abc123")
    monkeypatch.setattr(c, "_wait_for_wapi", lambda timeout=None: None)

    import dominion.src.container as container_mod
    monkeypatch.setattr(container_mod.time, "sleep", lambda s: None)

    @contextmanager
    def fake_console_session(**kwargs):
        yield _FakeConsole()

    monkeypatch.setattr(c, "console_session", fake_console_session)

    ip = c.set_network()

    assert ip == "172.20.0.5"
    host_cmds = [call[1] for call in fake_transport.calls if call[0] == "run_on_host"]
    assert any(
        "ip route replace 172.20.0.5/32 dev br-abc123" in cmd for cmd in host_cmds
    )
