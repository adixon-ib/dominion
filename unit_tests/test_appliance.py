"""Unit tests for appliance.py: Appliance, LocalAppliance, ssh_reachable(),
reset_console(), console_connect_session().

Constructing a real `Appliance` normally probes SSH for real (`_ensure_ssh()`
in `__init__`) — every test that constructs one here mocks `ssh_reachable`
so no real network call happens. `_ensure_ssh`/`_enable_ssh_via_console` are
also tested directly against a bare (non-`__init__`'d) instance, since those
methods hold the real branching logic and were previously untested.
"""

import logging
from contextlib import contextmanager

import pexpect
import pytest

import dominion.src.appliance as appliance_mod
import dominion.src.session as session_mod
from dominion import (
    Appliance,
    ApplianceConfig,
    LocalAppliance,
    LocalApplianceConfig,
    LocalDockerTransport,
    LocalNiosContainer,
    SSHDockerTransport,
    reset_console,
)

LOGGER = logging.getLogger("dominion.unit_test")
DEFAULT_HOSTNAME = "vm-04-89"
DEFAULT_IP = "10.35.211.4"
DEFAULT_CONTAINER = "balerion"
DEFAULT_CHROOT = "/local/91/chroots/balerion"


@pytest.fixture
def ssh_always_reachable(monkeypatch):
    monkeypatch.setattr(appliance_mod, "ssh_reachable", lambda host, timeout=5: True)
    from dominion.src.container import NiosContainer
    monkeypatch.setattr(NiosContainer, "_disable_ssh_rate_limit", lambda self: None)


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


@pytest.fixture
def ensure(monkeypatch):
    """Harness for _ensure_ssh: scripted ssh/port/addkeys/console results, fake clock, call log."""
    calls = []
    clock = {"t": 0.0}

    class Env:
        ssh = []          # successive ssh_reachable results (last repeats)
        port_open = False
        addkeys = [True]  # successive addkeys results (last repeats)
        wapi_timeouts = []
        wapi_error = None  # RuntimeError message from wait_for_wapi, or None for ready
        console_error = None

    def pop(seq):
        return seq.pop(0) if len(seq) > 1 else seq[0]

    def ssh(host, timeout=5):
        calls.append("ssh")
        return pop(Env.ssh)

    def sleep(s):
        clock["t"] += s

    monkeypatch.setattr(appliance_mod, "ssh_reachable", ssh)
    monkeypatch.setattr(appliance_mod, "ssh_port_open", lambda h, timeout=5: Env.port_open)
    monkeypatch.setattr(
        appliance_mod, "time", type("T", (), {"time": staticmethod(lambda: clock["t"]), "sleep": staticmethod(sleep)}),
    )
    Env.wapi_timeouts = []

    def wapi(ip, logger, timeout=None):
        calls.append("wapi")
        Env.wapi_timeouts.append(timeout)
        if Env.wapi_error:
            raise RuntimeError(Env.wapi_error)

    monkeypatch.setattr(appliance_mod, "wait_for_wapi", wapi)
    monkeypatch.setattr(
        appliance_mod, "run_addkeys",
        lambda ip, logger: calls.append("addkeys") or pop(Env.addkeys),
    )
    a = _bare_appliance()
    monkeypatch.setattr(
        a, "_enable_ssh_via_console", lambda: calls.append("console") or Env.console_error,
    )
    Env.a, Env.calls = a, calls
    return Env


def test_ensure_ssh_addkeys_after_console_then_ssh_succeeds(ensure):
    ensure.ssh = [False, True]

    ensure.a._ensure_ssh()

    assert ensure.calls == ["ssh", "console", "wapi", "addkeys", "ssh"]


def test_ensure_ssh_retries_addkeys_until_it_succeeds(ensure):
    ensure.ssh = [False, True]
    ensure.addkeys = [False, False, True]

    ensure.a._ensure_ssh()

    assert ensure.calls.count("addkeys") == 3


def test_ensure_ssh_skips_power_cycle_when_addkeys_fixes_auth(ensure):
    ensure.ssh = [False, True]
    ensure.port_open = True

    ensure.a._ensure_ssh()

    assert ensure.calls == ["ssh", "wapi", "addkeys", "ssh"]


def test_ensure_ssh_falls_back_to_power_cycle_when_cheap_addkeys_fails(ensure):
    ensure.ssh = [False, True]
    ensure.port_open = True
    ensure.addkeys = [False, True]

    ensure.a._ensure_ssh()

    # Quick path is a single attempt, then power-cycle and a fresh gate + loop.
    assert ensure.calls == ["ssh", "wapi", "addkeys", "console", "wapi", "addkeys", "ssh"]
    # Live numbers: 60s WAPI wait on the quick path, 360s after the power-cycle.
    assert ensure.wapi_timeouts == [60, 360]


def test_ensure_ssh_no_cheap_addkeys_when_port_closed(ensure):
    ensure.ssh = [False, True]

    ensure.a._ensure_ssh()

    assert ensure.calls[:3] == ["ssh", "console", "wapi"]


def test_ensure_ssh_addkeys_failure_names_step(ensure):
    ensure.ssh = [False]
    ensure.addkeys = [False]

    with pytest.raises(RuntimeError, match=r"addkeys failed on all \d+ attempts"):
        ensure.a._ensure_ssh()

    assert "ssh" not in ensure.calls[1:]  # never probes ssh after a failed addkeys


def test_ensure_ssh_console_error_is_first_line_only(ensure, monkeypatch):
    ensure.ssh = [False]
    ensure.wapi_error = "WAPI not ready\nsecond wapi line"
    monkeypatch.setattr(
        appliance_mod.subprocess, "run",
        lambda *a, **k: type("R", (), {"returncode": 0})(),
    )
    monkeypatch.setattr(
        appliance_mod.pexpect, "spawn",
        lambda *a, **k: (_ for _ in ()).throw(Exception("console busy\nTraceback: noisy detail")),
    )
    monkeypatch.setattr(
        ensure.a, "_enable_ssh_via_console",
        Appliance._enable_ssh_via_console.__get__(ensure.a),
    )

    with pytest.raises(RuntimeError) as exc:
        ensure.a._ensure_ssh()

    msg = str(exc.value)
    assert "console enable failed (console busy)" in msg
    assert "WAPI not ready" in msg
    assert "noisy detail" not in msg and "second wapi line" not in msg


def test_enable_ssh_reboot_system_timeout_names_step(monkeypatch):
    import subprocess

    def run(cmd, **k):
        if cmd[-1] == "poweron":
            raise subprocess.TimeoutExpired(cmd, 30)
        return type("R", (), {"returncode": 0})()

    monkeypatch.setattr(appliance_mod.subprocess, "run", run)
    monkeypatch.setattr(appliance_mod.time, "sleep", lambda s: None)

    assert _bare_appliance()._enable_ssh_via_console() == "reboot_system poweron timed out after 30s"


def test_ensure_ssh_wapi_never_ready_skips_addkeys_and_names_step(ensure):
    ensure.ssh = [False]
    ensure.wapi_error = "WAPI not ready on 10.0.0.1 after 360s"

    with pytest.raises(RuntimeError, match="WAPI not ready on 10.0.0.1 after 360s"):
        ensure.a._ensure_ssh()

    assert "addkeys" not in ensure.calls


def test_ensure_ssh_port_open_wapi_down_falls_back_to_power_cycle(ensure, monkeypatch):
    ensure.ssh = [False, True]
    ensure.port_open = True
    state = {"n": 0}
    orig = appliance_mod.wait_for_wapi

    def flaky(ip, logger, timeout=None):
        state["n"] += 1
        if state["n"] == 1:
            ensure.calls.append("wapi")
            raise RuntimeError("WAPI not ready")
        return orig(ip, logger, timeout)

    monkeypatch.setattr(appliance_mod, "wait_for_wapi", flaky)
    ensure.a._ensure_ssh()

    assert ensure.calls[:4] == ["ssh", "wapi", "console", "wapi"]


def test_ensure_ssh_addkeys_ok_but_ssh_denied_names_step(ensure):
    ensure.ssh = [False]

    with pytest.raises(RuntimeError, match="still failing after addkeys succeeded"):
        ensure.a._ensure_ssh()


def test_ensure_ssh_console_failure_still_tries_addkeys_and_names_both(ensure):
    ensure.ssh = [False]
    ensure.addkeys = [False]
    ensure.console_error = "connection refused"

    with pytest.raises(RuntimeError, match=r"console enable failed \(connection refused\); addkeys failed"):
        ensure.a._ensure_ssh()

    assert "addkeys" in ensure.calls


def test_ensure_ssh_retry_is_bounded(ensure):
    ensure.ssh = [False]
    ensure.addkeys = [False]

    with pytest.raises(RuntimeError):
        ensure.a._ensure_ssh()

    # Fake clock advances only by the retry sleep, so attempts == timeout / interval exactly.
    n = ensure.calls.count("addkeys")
    assert n == appliance_mod.ADDKEYS_RETRY_TIMEOUT // appliance_mod.ADDKEYS_RETRY_INTERVAL
    assert appliance_mod.ADDKEYS_RETRY_INTERVAL >= 20  # SSH rate limit headroom


# -- run_addkeys() / wait_for_wapi() -------------------------------------------------

@pytest.mark.parametrize("rc,expected", [(0, True), (1, False)])
def test_run_addkeys_returncode(monkeypatch, rc, expected):
    seen = {}
    monkeypatch.setattr(
        appliance_mod.subprocess, "run",
        lambda cmd, **k: seen.setdefault("cmd", cmd) and type("R", (), {"returncode": rc})(),
    )

    assert appliance_mod.run_addkeys("10.0.0.1", LOGGER) is expected
    assert seen["cmd"] == ["addkeys", "10.0.0.1"]


def test_run_addkeys_false_on_timeout_and_missing_binary(monkeypatch):
    import subprocess

    def timeout(*a, **k):
        raise subprocess.TimeoutExpired("addkeys", 1)

    monkeypatch.setattr(appliance_mod.subprocess, "run", timeout)
    assert appliance_mod.run_addkeys("10.0.0.1", LOGGER) is False

    def missing(*a, **k):
        raise FileNotFoundError("addkeys")

    monkeypatch.setattr(appliance_mod.subprocess, "run", missing)
    assert appliance_mod.run_addkeys("10.0.0.1", LOGGER) is False


def test_wait_for_wapi_returns_on_200(monkeypatch):
    codes = iter(["000", "503", "200"])
    monkeypatch.setattr(appliance_mod.time, "sleep", lambda s: None)
    monkeypatch.setattr(
        appliance_mod.subprocess, "run",
        lambda *a, **k: type("R", (), {"stdout": next(codes)})(),
    )

    appliance_mod.wait_for_wapi("10.0.0.1", LOGGER, timeout=60)


def test_wait_for_wapi_curl_argv(monkeypatch):
    seen = []
    monkeypatch.setattr(
        appliance_mod.subprocess, "run",
        lambda cmd, **k: seen.append(cmd) or type("R", (), {"stdout": "200"})(),
    )

    appliance_mod.wait_for_wapi("10.0.0.1", LOGGER, timeout=60)

    assert seen == [[
        "curl", "-sk", "-o", "/dev/null", "-w", "%{http_code}",
        "-u", "admin:infoblox",
        f"https://10.0.0.1/wapi/{appliance_mod.WAPI_VERSION}/grid",
    ]]


def test_wait_for_wapi_raises_on_timeout(monkeypatch):
    clock = {"t": 0.0}
    monkeypatch.setattr(
        appliance_mod, "time",
        type("T", (), {"time": staticmethod(lambda: clock["t"]),
                       "sleep": staticmethod(lambda s: clock.__setitem__("t", clock["t"] + s))}),
    )
    monkeypatch.setattr(
        appliance_mod.subprocess, "run",
        lambda *a, **k: type("R", (), {"stdout": "503"})(),
    )

    with pytest.raises(RuntimeError, match="WAPI not ready on 10.0.0.1 after 20s"):
        appliance_mod.wait_for_wapi("10.0.0.1", LOGGER, timeout=20)


# -- Appliance._enable_ssh_via_console() ---------------------------------------------

class FakeChild:
    """Fake pexpect child for _enable_ssh_via_console boot + login flow."""
    def __init__(self):
        self.after = ""
        self.before = ""

    def expect(self, pattern, timeout=None):
        self.after = "Infoblox > "
        return 0

    def sendline(self, text):
        pass

    def read_nonblocking(self, size=1, timeout=-1):
        raise pexpect.TIMEOUT("fake")

    def isalive(self):
        return True

    def close(self, force=False):
        pass


class FakeConsole:
    """Minimal fake for ConsoleSession used by _enable_ssh_via_console."""
    def __init__(self, show_support_access_output="Current support access status: off",
                 prompts_per_set=2):
        self.sent: list[str] = []
        self._show_support_access_output = show_support_access_output
        self._prompts_per_set = prompts_per_set
        self._prompt_count = 0

    def sendline(self, text):
        self.sent.append(text)
        if text.startswith("set "):
            self._prompt_count = 0

    def expect(self, pattern, timeout=None):
        return ""

    def expect_one_of(self, patterns, timeout=None):
        if self._prompt_count < self._prompts_per_set:
            self._prompt_count += 1
            return 1  # y or n prompt
        return 0  # Infoblox >

    def run(self, cmd, timeout=None):
        if cmd == "show support_access":
            return self._show_support_access_output
        return ""


def _patch_enable_ssh(monkeypatch, fake_console=None):
    console = fake_console or FakeConsole()

    def fake_spawn(*a, **k):
        return FakeChild()

    def fake_init(self, *a, **k):
        self._spawn_cmd = ""
        self._prompt_pattern = r"Infoblox\s*>"
        self._logger = LOGGER
        self._default_timeout = 30
        self._child = None
        self._last_prompt = ""
        self._user = "admin"
        self._password = "infoblox"
        self._disconnect_cmd = "\r~."
        # Inject fake console methods onto the session
        self.sent = console.sent
        self.sendline = console.sendline
        self.expect = console.expect
        self.expect_one_of = console.expect_one_of
        self.run = console.run
        self._close = lambda: None

    monkeypatch.setattr(appliance_mod.pexpect, "spawn", fake_spawn)
    monkeypatch.setattr(
        "dominion.src.session.ConsoleConnectSession.__init__", fake_init,
    )
    monkeypatch.setattr(appliance_mod.time, "sleep", lambda s: None)
    monkeypatch.setattr(
        appliance_mod.subprocess, "run",
        lambda *a, **k: type("R", (), {"returncode": 0})(),
    )
    return console


def test_enable_ssh_when_support_access_off(monkeypatch):
    console = _patch_enable_ssh(monkeypatch,
        FakeConsole(show_support_access_output="Current support access status: off"))

    a = _bare_appliance()
    assert a._enable_ssh_via_console() is None

    assert "set remote_console" in console.sent
    assert "set support_access" in console.sent


def test_enable_ssh_when_support_access_expired(monkeypatch):
    console = _patch_enable_ssh(monkeypatch,
        FakeConsole(show_support_access_output="Code has expired"))

    a = _bare_appliance()
    a._enable_ssh_via_console()

    assert "set remote_console" in console.sent
    assert "set support_access" in console.sent


def test_enable_ssh_when_support_access_active(monkeypatch):
    console = _patch_enable_ssh(monkeypatch,
        FakeConsole(show_support_access_output="code (expires in 23:45): ABC123"))

    a = _bare_appliance()
    a._enable_ssh_via_console()

    assert "set remote_console" in console.sent
    assert "set support_access" not in console.sent


def test_enable_ssh_via_console_returns_gracefully_on_connect_failure(monkeypatch):
    a = _bare_appliance()

    monkeypatch.setattr(appliance_mod.time, "sleep", lambda s: None)
    monkeypatch.setattr(
        appliance_mod.subprocess, "run",
        lambda *a, **k: type("R", (), {"returncode": 0})(),
    )
    monkeypatch.setattr(
        appliance_mod.pexpect, "spawn",
        lambda *a, **k: (_ for _ in ()).throw(Exception("connection refused")),
    )

    assert a._enable_ssh_via_console() == "connection refused"


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


# -- console_connect_session() ----------------------------------------------------

def _console_connect_script():
    """Script for FakeChild: drain + wakeup + disconnect."""
    import pexpect as _pexpect
    return [
        (0, "", "Infoblox > "),    # _open: wakeup sendline("") -> expect PROMPT
        _pexpect.EOF("done"),      # _close: disconnect with \r~.
    ]


def _mock_reset_console(monkeypatch):
    monkeypatch.setattr(
        appliance_mod.subprocess, "run",
        lambda *a, **k: type("R", (), {"returncode": 0})(),
    )


def test_console_connect_session_spawns_correct_command(monkeypatch, fake_child_factory):
    child = fake_child_factory(script=_console_connect_script())
    _mock_reset_console(monkeypatch)
    monkeypatch.setattr(session_mod.pexpect, "spawn", lambda *a, **k: child)

    a = _bare_appliance()
    with a.console_connect_session(timeout=10) as session:
        assert session is not None


def test_console_connect_session_uses_hostname(monkeypatch, fake_child_factory):
    child = fake_child_factory(script=_console_connect_script())
    _mock_reset_console(monkeypatch)
    spawned_cmds = []

    def capture_spawn(cmd, **kwargs):
        spawned_cmds.append(cmd)
        return child

    monkeypatch.setattr(session_mod.pexpect, "spawn", capture_spawn)

    a = _bare_appliance(hostname="vm-99-01")
    with a.console_connect_session() as _session:
        pass

    assert any("console_connect -H vm-99-01" in cmd for cmd in spawned_cmds)


def test_console_connect_session_sends_disconnect_on_close(monkeypatch, fake_child_factory):
    child = fake_child_factory(script=_console_connect_script())
    _mock_reset_console(monkeypatch)
    monkeypatch.setattr(session_mod.pexpect, "spawn", lambda *a, **k: child)

    a = _bare_appliance()
    with a.console_connect_session() as _session:
        pass

    assert ("sendline", "\r~.") in child.sent


def test_console_connect_session_resets_before_connect(monkeypatch, fake_child_factory):
    child = fake_child_factory(script=_console_connect_script())
    reset_calls = []

    def fake_run(args, **kwargs):
        reset_calls.append(args)
        return type("R", (), {"returncode": 0})()

    monkeypatch.setattr(appliance_mod.subprocess, "run", fake_run)
    monkeypatch.setattr(session_mod.pexpect, "spawn", lambda *a, **k: child)

    a = _bare_appliance(hostname="vm-04-89")
    with a.console_connect_session() as _session:
        pass

    assert ["reset_console", "-H", "vm-04-89"] in reset_calls


# -- reset_console() ---------------------------------------------------------------

def test_reset_console_calls_binary(monkeypatch):
    calls = []

    def fake_run(args, **kwargs):
        calls.append(args)
        return type("R", (), {"returncode": 0})()

    monkeypatch.setattr(appliance_mod.subprocess, "run", fake_run)
    reset_console("vm-04-89")
    assert calls == [["reset_console", "-H", "vm-04-89"]]


def test_reset_console_raises_on_failure(monkeypatch):
    import subprocess as sp

    def fake_run(args, **kwargs):
        raise sp.CalledProcessError(1, args)

    monkeypatch.setattr(appliance_mod.subprocess, "run", fake_run)
    with pytest.raises(sp.CalledProcessError):
        reset_console("vm-04-89")
