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


# -- mark_log / dump_log -----------------------------------------------------------

def test_mark_log_parses_wc_output(fake_transport_factory):
    transport = fake_transport_factory(
        responses={"wc -l": (0, "  1234 /infoblox/var/infoblox.log\n", "")},
    )
    c = NiosContainer(
        container_name="nios",
        transport=transport,
        chroot=DEFAULT_CHROOT,
        logger=LOGGER,
    )
    mark = c.mark_log("/infoblox/var/infoblox.log")
    assert mark.log_path == "/infoblox/var/infoblox.log"
    assert mark.line_count == 1234


def test_mark_infoblox_log_uses_default_path(fake_transport_factory):
    transport = fake_transport_factory(
        responses={"wc -l": (0, "500 /infoblox/var/infoblox.log\n", "")},
    )
    c = NiosContainer(
        container_name="nios",
        transport=transport,
        chroot=DEFAULT_CHROOT,
        logger=LOGGER,
    )
    mark = c.mark_infoblox_log()
    assert mark.log_path == "/infoblox/var/infoblox.log"
    assert mark.line_count == 500


def test_dump_log_full_file(fake_transport_factory, tmp_path):
    transport = fake_transport_factory(
        responses={"cat": (0, "line1\nline2\nline3\n", "")},
    )
    c = NiosContainer(
        container_name="nios",
        transport=transport,
        chroot=DEFAULT_CHROOT,
        logger=LOGGER,
    )
    dest = tmp_path / "out.log"
    result = c.dump_log("/infoblox/var/infoblox.log", dest=dest)
    assert result.path == dest
    assert result.text == "line1\nline2\nline3\n"
    assert result.lines == ["line1", "line2", "line3"]
    assert result.line_count == 3
    assert dest.read_text() == "line1\nline2\nline3\n"
    run_cmds = [call[1] for call in transport.calls if call[0] == "run"]
    assert any("cat" in cmd and "infoblox.log" in cmd for cmd in run_cmds)


def test_dump_log_with_since(fake_transport_factory, tmp_path):
    from dominion.src.log import LogMark

    transport = fake_transport_factory(
        responses={"tail": (0, "new line\n", "")},
    )
    c = NiosContainer(
        container_name="nios",
        transport=transport,
        chroot=DEFAULT_CHROOT,
        logger=LOGGER,
    )
    mark = LogMark(log_path="/infoblox/var/infoblox.log", line_count=100)
    dest = tmp_path / "out.log"
    c.dump_log("/infoblox/var/infoblox.log", dest=dest, since=mark)
    run_cmds = [call[1] for call in transport.calls if call[0] == "run"]
    assert any("tail -n +101" in cmd for cmd in run_cmds)


def test_dump_log_with_grep(fake_transport_factory, tmp_path):
    transport = fake_transport_factory(
        responses={"cat": (0, "matched line\n", "")},
    )
    c = NiosContainer(
        container_name="nios",
        transport=transport,
        chroot=DEFAULT_CHROOT,
        logger=LOGGER,
    )
    dest = tmp_path / "out.log"
    c.dump_log("/var/log/syslog", dest=dest, grep="ERROR")
    run_cmds = [call[1] for call in transport.calls if call[0] == "run"]
    assert any("grep" in cmd and "ERROR" in cmd for cmd in run_cmds)


def test_dump_log_creates_parent_dirs(fake_transport_factory, tmp_path):
    transport = fake_transport_factory(
        responses={"cat": (0, "content\n", "")},
    )
    c = NiosContainer(
        container_name="nios",
        transport=transport,
        chroot=DEFAULT_CHROOT,
        logger=LOGGER,
    )
    dest = tmp_path / "nested" / "dir" / "out.log"
    c.dump_log("/infoblox/var/infoblox.log", dest=dest)
    assert dest.exists()


def test_dump_log_dest_directory_uses_log_basename(fake_transport_factory, tmp_path):
    transport = fake_transport_factory(
        responses={"cat": (0, "dir content\n", "")},
    )
    c = NiosContainer(
        container_name="nios",
        transport=transport,
        chroot=DEFAULT_CHROOT,
        logger=LOGGER,
    )
    result = c.dump_log("/infoblox/var/infoblox.log", dest=tmp_path)
    assert result.path == tmp_path / "infoblox.log"
    assert result.text == "dir content\n"


def test_dump_log_dest_new_directory_auto_creates(fake_transport_factory, tmp_path):
    transport = fake_transport_factory(
        responses={"cat": (0, "new dir\n", "")},
    )
    c = NiosContainer(
        container_name="nios",
        transport=transport,
        chroot=DEFAULT_CHROOT,
        logger=LOGGER,
    )
    dest = tmp_path / "2026-08-26_14-30-00"
    result = c.dump_log("/var/log/syslog", dest=dest)
    assert result.path == dest / "syslog"
    assert result.text == "new dir\n"


def test_dump_infoblox_log_delegates(fake_transport_factory, tmp_path):
    transport = fake_transport_factory(
        responses={"cat": (0, "log content\n", "")},
    )
    c = NiosContainer(
        container_name="nios",
        transport=transport,
        chroot=DEFAULT_CHROOT,
        logger=LOGGER,
    )
    dest = tmp_path / "infoblox.log"
    result = c.dump_infoblox_log(dest=dest)
    assert result.path == dest
    assert result.text == "log content\n"


# -- count_log / count_infoblox_log -----------------------------------------------

def test_count_log_returns_match_count(fake_transport_factory):
    transport = fake_transport_factory(
        responses={"grep -c": (0, "7\n", "")},
    )
    c = NiosContainer(
        container_name="nios",
        transport=transport,
        chroot=DEFAULT_CHROOT,
        logger=LOGGER,
    )
    count = c.count_log("/infoblox/var/infoblox.log", "ERROR")
    assert count == 7
    run_cmds = [call[1] for call in transport.calls if call[0] == "run"]
    assert any("grep -c" in cmd and "ERROR" in cmd for cmd in run_cmds)


def test_count_log_with_since(fake_transport_factory):
    from dominion.src.log import LogMark

    transport = fake_transport_factory(
        responses={"grep -c": (0, "3\n", "")},
    )
    c = NiosContainer(
        container_name="nios",
        transport=transport,
        chroot=DEFAULT_CHROOT,
        logger=LOGGER,
    )
    mark = LogMark(log_path="/infoblox/var/infoblox.log", line_count=50)
    count = c.count_log("/infoblox/var/infoblox.log", "token", since=mark)
    assert count == 3
    run_cmds = [call[1] for call in transport.calls if call[0] == "run"]
    assert any("tail -n +51" in cmd and "grep -c" in cmd for cmd in run_cmds)


def test_count_log_zero_matches(fake_transport_factory):
    transport = fake_transport_factory(
        responses={"grep -c": (1, "0\n", "")},
    )
    c = NiosContainer(
        container_name="nios",
        transport=transport,
        chroot=DEFAULT_CHROOT,
        logger=LOGGER,
    )
    assert c.count_log("/infoblox/var/infoblox.log", "NONEXISTENT") == 0


def test_count_infoblox_log_delegates(fake_transport_factory):
    transport = fake_transport_factory(
        responses={"grep -c": (0, "12\n", "")},
    )
    c = NiosContainer(
        container_name="nios",
        transport=transport,
        chroot=DEFAULT_CHROOT,
        logger=LOGGER,
    )
    assert c.count_infoblox_log("token") == 12
