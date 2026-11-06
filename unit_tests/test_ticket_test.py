"""Unit tests for ticket_test.py (TicketTest, step) and actions/ipc_ticket.py."""

import contextlib
import os
import types

import pytest

import dominion.src.ticket as ticket_mod
from dominion import Appliance, DeployResult, TicketTest, step
from dominion.actions import ipc_ticket
from dominion.actions.ipc_ticket import IPC, LICENSES, IpcTicketTest


class Box:
    def __init__(self, state="A"):
        self.state = state
        self.log = []


class FakeContainer:
    def __init__(self, log):
        self.log = log

    def upload_file(self, src, dest):
        self.log.append(("upload_file", src, dest))


class FakeWapi:
    def __init__(self, log):
        self.log = log

    def wait_until_ready(self):
        self.log.append(("wait_until_ready",))

    def request(self, method, path, **kw):
        self.log.append(("request", method, path, kw))
        return 200, {"ok": True}


class FakeApp:
    box = None
    skipped = None

    def __init__(self, cfg):
        self.hostname = cfg.hostname
        self.wapi_version = None
        self.on_call = None
        self.container = FakeContainer(self.box.log)
        self.wapi = FakeWapi(self.box.log)

    @contextlib.contextmanager
    def console_session(self):
        yield types.SimpleNamespace(run=lambda cmd: "Version : 1.0-1\n")

    def deploy_token_py(self, groups=None):
        self.box.log.append(("deploy_token_py", groups))
        return DeployResult("token_py", True, details={"skipped": self.skipped})

    def usage_mode(self):
        return self.box.state

    def __str__(self):
        return self.hostname


@pytest.fixture
def box(monkeypatch):
    b = Box()
    FakeApp.box, FakeApp.skipped = b, None
    monkeypatch.setattr(ticket_mod, "lab_network", lambda h: ("10.0.0.9", "m", "g"))
    monkeypatch.setattr(
        Appliance, "from_config", classmethod(lambda cls, cfg: FakeApp(cfg)),
    )
    return b


def make_test(box, **attrs):
    """A concrete TicketTest over box with steps A -> B -> C."""
    def move(to):
        def fn(self):
            box.log.append(("step", to))
            box.state = to
            return False  # ignored
        return fn

    ns = {
        "name": "NIOS-T", "home_state": "A",
        "probe": lambda self: box.state,
        "home": lambda self: (box.log.append(("home",)), setattr(box, "state", "A")),
        "to_b": step("A", "B", "to b")(move("B")),
        "to_c": step("B", "C", "to c")(move("C")),
    }
    ns.update(attrs)
    return type("T", (TicketTest,), ns)


def run(tt):
    with pytest.raises(SystemExit) as e:
        tt.run()
    return e.value.code


def report(tt):
    with open(os.path.join(tt.t.output_dir, "report.md")) as f:
        return f.read()


def headings(text):
    return [ln[3:].split("  ")[0] for ln in text.splitlines() if ln.startswith("## ")]


def test_phases_then_steps_are_numbered_in_definition_order(box, tmp_path):
    tt = make_test(box, deploy_groups={"pyabs"})("vm-1", results_dir=str(tmp_path))
    assert run(tt) == 0
    assert headings(report(tt)) == [
        "1. connect", "2. deploy", "3. preflight", "4. to b", "5. to c", "home",
    ]
    assert [e for e in box.log if e[0] in ("step", "home")] == [
        ("step", "B"), ("step", "C"), ("home",),
    ]


def test_nothing_to_deploy_is_a_deliberate_skip(box, tmp_path):
    tt = make_test(box)("vm-1", results_dir=str(tmp_path))
    assert run(tt) == 0
    assert "Skipped: nothing to deploy" in report(tt)
    assert not [e for e in box.log if e[0] in ("deploy_token_py", "wait_until_ready")]


def test_subclass_steps_follow_inherited_and_override_keeps_position(box, tmp_path):
    Base = make_test(box)

    class Sub(Base):
        @step("B", "B", "to b again")
        def to_b(self):
            box.log.append(("sub to_b",))

        @step("C", "C", "extra")
        def extra(self):
            box.log.append(("extra",))

    assert [a for a, _ in Sub._steps()] == ["to_b", "to_c", "extra"]
    assert Sub._steps()[0][1] == ("B", "B", "to b again")
    assert [a for a, _ in Base._steps()] == ["to_b", "to_c"]


def test_override_without_decorator_keeps_inherited_step_meta(box):
    Base = make_test(box)

    class Sub(Base):
        def to_c(self):
            pass

    assert dict(Sub._steps())["to_c"] == ("B", "C", "to c")


def test_inherited_steps_run_with_the_subclass_body(box, tmp_path):
    Base = make_test(box)

    class Sub(Base):
        @step("C", "C", "extra")
        def extra(self):
            box.log.append(("extra",))

    tt = Sub("vm-1", results_dir=str(tmp_path))
    assert run(tt) == 0
    assert headings(report(tt))[3:] == ["4. to b", "5. to c", "6. extra", "home"]
    assert ("extra",) in box.log


def test_abstract_probe_and_home_are_enforced():
    class NoProbe(TicketTest):
        name, home_state = "x", "A"

        def home(self):
            pass

    class NoHome(TicketTest):
        name, home_state = "x", "A"

        def probe(self):
            pass

    for cls in (NoProbe, NoHome):
        with pytest.raises(TypeError, match="abstract"):
            cls("h")


@pytest.mark.parametrize("missing", ["name", "home_state"])
def test_name_and_home_state_are_required(box, missing):
    ns = {"name": "x", "home_state": "A", "probe": lambda s: 1, "home": lambda s: 1}
    del ns[missing]
    with pytest.raises(TypeError, match=missing):
        type("T", (TicketTest,), ns)("h")


def test_skip_home_registers_no_home_step(box, tmp_path):
    tt = make_test(box)("vm-1", results_dir=str(tmp_path), skip_home=True)
    assert run(tt) == 0
    assert "home" not in headings(report(tt))
    assert ("home",) not in box.log
    assert box.state == "C"


def test_skip_deploy_skips_phase_and_uploads_nothing(box, tmp_path):
    cls = make_test(box, deploy_groups={"pyabs"}, deploy_files={"a.py": "/d/a.py"})
    tt = cls("vm-1", results_dir=str(tmp_path), skip_deploy=True)
    assert run(tt) == 0
    assert "Skipped: skip_deploy set" in report(tt)
    assert not [e for e in box.log if e[0] in ("upload_file", "deploy_token_py")]


def test_deploy_uploads_files_deploys_groups_and_waits(box, tmp_path):
    wt = tmp_path / "wt"
    wt.mkdir()
    (wt / "here.py").write_text("x")
    FakeApp.skipped = ["/wt/gone.py"]
    cls = make_test(
        box, deploy_groups={"pyabs"},
        deploy_files={"here.py": "/d/here.py", "gone.py": "/d/gone.py"},
    )
    tt = cls("vm-1", results_dir=str(tmp_path / "r"), worktree=str(wt))
    assert run(tt) == 0
    deploy_calls = [e for e in box.log if e[0] != "step"][:3]
    assert deploy_calls == [
        ("upload_file", str(wt / "here.py"), "/d/here.py"),
        ("deploy_token_py", {"pyabs"}),
        ("wait_until_ready",),
    ]
    text = report(tt)
    assert "gone.py absent in worktree: not uploaded" in text
    assert "deploy_token_py skipped" in text


def test_deploy_files_without_worktree_fails_deploy_and_blocks_steps(box, tmp_path):
    cls = make_test(box, deploy_files={"a.py": "/d/a.py"})
    tt = cls("vm-1", results_dir=str(tmp_path))
    assert run(tt) == 1
    assert not [e for e in box.log if e[0] == "step"]
    assert "needs 2. deploy (failed)" in report(tt)


def test_default_preflight_checks_home_state(box, tmp_path):
    box.state = "B"
    tt = make_test(box)("vm-1", results_dir=str(tmp_path))
    assert run(tt) == 1
    text = report(tt)
    assert "box in 'A': expected A, got B  FAIL" in text
    assert not [e for e in box.log if e[0] == "step"]


def test_preflight_override_extends_default(box, tmp_path):
    seen = []

    class T(make_test(box)):
        def preflight(self):
            super().preflight()
            seen.append(self.probe())
            raise ValueError("extra")

    tt = T("vm-1", results_dir=str(tmp_path))
    assert run(tt) == 1
    assert seen == ["A"]
    assert "extra" in report(tt)


def test_failed_step_runs_home_after_step_started(box, tmp_path):
    def bad(self):
        box.state = "B"
        raise ValueError("nope")

    cls = make_test(box, to_b=step("A", "B", "to b")(bad))
    tt = cls("vm-1", results_dir=str(tmp_path))
    assert run(tt) == 1
    assert ("home",) in box.log
    assert box.state == "A"


def test_accessors_before_run_raise_and_work_during_run(box, tmp_path):
    seen = {}

    def peek(self):
        seen["app"], seen["state"] = self.app, self.state
        seen["container"] = self.container
        with self.checks("c") as c:
            c.expect("x", 1, 1)
        with self.nios_log("p"):
            pass

    cls = make_test(box, to_b=step("A", "B", "to b")(peek))
    tt = cls("vm-1", results_dir=str(tmp_path))
    with pytest.raises(RuntimeError, match="not running"):
        tt.app
    run(tt)
    assert isinstance(seen["app"], FakeApp)
    assert seen["container"] is seen["app"].container


def test_initial_state_is_per_instance_copy(box, tmp_path):
    cls = make_test(box, initial_state={"tokens": []})

    def add(self):
        self.state.tokens.append("t")
        box.state = "B"

    cls.to_b = step("A", "B", "to b")(add)
    run(cls("vm-1", results_dir=str(tmp_path / "1")))
    assert cls.initial_state == {"tokens": []}


# -- IpcTicketTest ---------------------------------------------------------------

def ipc_test(box, **attrs):
    ns = {"name": "NIOS-I", "bundle_path": "/b/token_bundle.tar"}
    ns.update(attrs)
    return type("I", (IpcTicketTest,), ns)


def test_ipc_defaults(box):
    tt = ipc_test(box)("vm-1")
    assert (LICENSES, IPC) == ("Licenses", "IPc")
    assert tt.home_state == LICENSES
    assert tt.deploy_groups == {"pyabs"}
    assert tt.wapi_version == "v2.15"
    assert ipc_test(box)("vm-1", wapi_version="v2.20").wapi_version == "v2.20"


def test_ipc_probe_and_home(box, monkeypatch, tmp_path):
    resets = []
    monkeypatch.setattr(ipc_ticket, "reset_all", resets.append)
    seen = {}

    def peek(self):
        seen["probe"] = self.probe()
        self.home()
        box.state = "IPc"

    cls = ipc_test(box, peek=step(LICENSES, IPC, "peek")(peek))
    box.state = LICENSES
    tt = cls("vm-1", results_dir=str(tmp_path), skip_home=True)
    run(tt)
    assert seen["probe"] == LICENSES
    assert len(resets) == 1
    assert isinstance(resets[0], FakeApp)


def test_ipc_bundle_posts_without_raise_and_apply_gets_long_timeout(box, tmp_path):
    out = {}

    def calls(self):
        out["upload"] = self.bundle("upload", {"token": "t"})
        out["apply"] = self.bundle("apply", {"token": "t"})
        out["custom"] = self.bundle("apply", {}, timeout=7)
        box.state = "B"

    cls = ipc_test(box, home_state="A", calls=step("A", "B", "calls")(calls),
                   probe=lambda self: box.state)
    run(cls("vm-1", results_dir=str(tmp_path), skip_home=True))
    reqs = [e for e in box.log if e[0] == "request"]
    assert [r[3]["timeout"] for r in reqs] == [None, 300, 7]
    assert reqs[0][1:3] == ("POST", "ipc:bundle")
    assert reqs[0][3]["params"] == "_function=upload"
    assert reqs[0][3]["check"] is False
    assert reqs[0][3]["data"] == {"token": "t"}
    assert out["upload"] == (200, {"ok": True})


def test_ipc_upload_bundle_token_records_token_in_state(box, monkeypatch, tmp_path):
    uploads = []
    monkeypatch.setattr(
        ipc_ticket, "upload_bundle_token",
        lambda app, path: uploads.append(path) or f"tok{len(uploads)}",
    )
    out = []

    def go(self):
        out.append(self.upload_bundle_token())
        out.append(self.upload_bundle_token("/other.tar"))
        out.append(list(self.state.tokens))
        box.state = "B"

    cls = ipc_test(box, home_state="A", go=step("A", "B", "go")(go),
                   probe=lambda self: box.state)
    run(cls("vm-1", results_dir=str(tmp_path), skip_home=True))
    assert uploads == ["/b/token_bundle.tar", "/other.tar"]
    assert out == ["tok1", "tok2", ["tok1", "tok2"]]


def test_ipc_upload_bundle_token_needs_a_bundle(box):
    tt = ipc_test(box, bundle_path=None)("vm-1")
    tt._t = types.SimpleNamespace(app=None, state=types.SimpleNamespace(tokens=[]))
    with pytest.raises(ValueError, match="no bundle"):
        tt.upload_bundle_token()


# -- deploy_required and deploy_result -------------------------------------------

def test_deploy_result_is_kept(box, tmp_path):
    cls = make_test(box, deploy_groups={"pyabs"})
    tt = cls("vm-1", results_dir=str(tmp_path))
    assert tt.deploy_result is None
    run(tt)
    assert tt.deploy_result.artifact == "token_py"


def test_required_deploy_file_absent_fails_before_anything_deploys(box, tmp_path):
    wt = tmp_path / "wt"
    wt.mkdir()
    cls = make_test(
        box, deploy_groups={"pyabs"}, deploy_files={"gone.py": "/d/gone.py"},
        deploy_required=("gone.py",),
    )
    tt = cls("vm-1", results_dir=str(tmp_path / "r"), worktree=str(wt))
    assert run(tt) == 1
    assert "required files absent from the worktree: ['gone.py']" in report(tt)
    assert not [e for e in box.log if e[0] in ("deploy_token_py", "step")]


def test_required_file_skipped_by_deploy_token_py_fails_deploy(box, tmp_path):
    wt = tmp_path / "wt"
    wt.mkdir()
    FakeApp.skipped = [str(wt / "sub/need.py"), str(wt / "sub/other.py")]
    cls = make_test(box, deploy_groups={"pyabs"}, deploy_required=("sub/need.py",))
    tt = cls("vm-1", results_dir=str(tmp_path / "r"), worktree=str(wt))
    assert run(tt) == 1
    assert "required files absent from the worktree: ['sub/need.py']" in report(tt)
    assert tt.deploy_result is not None
    assert not [e for e in box.log if e[0] == "step"]


def test_optional_missing_files_do_not_fail_when_not_required(box, tmp_path):
    wt = tmp_path / "wt"
    wt.mkdir()
    FakeApp.skipped = [str(wt / "sub/other.py")]
    cls = make_test(
        box, deploy_groups={"pyabs"}, deploy_files={"gone.py": "/d/gone.py"},
        deploy_required=("sub/need.py",),
    )
    tt = cls("vm-1", results_dir=str(tmp_path / "r"), worktree=str(wt))
    assert run(tt) == 0


def test_ipc_upload_bundle_token_survives_initial_state_without_tokens(
    box, monkeypatch, tmp_path,
):
    monkeypatch.setattr(ipc_ticket, "upload_bundle_token", lambda app, path: "tok")
    out = []

    def go(self):
        out.append(self.upload_bundle_token())
        out.append(list(self.state.tokens))
        box.state = "B"

    cls = ipc_test(box, home_state="A", initial_state={"other": 1},
                   go=step("A", "B", "go")(go), probe=lambda self: box.state)
    run(cls("vm-1", results_dir=str(tmp_path), skip_home=True))
    assert out == ["tok", ["tok"]]


# -- enable_ipc and phase titles -------------------------------------------------

def test_enable_ipc_runs_action_keeps_token_and_returns_result(
    box, monkeypatch, tmp_path,
):
    seen = []

    def fake_enable(app, path):
        seen.append((app, path))
        return {"token": "tok", "status": {"overall_status": "COMPLETED"}}

    monkeypatch.setattr(ipc_ticket, "enable_token_mode_wapi", fake_enable)
    out = []

    def go(self):
        out.append(self.enable_ipc())
        out.append(self.enable_ipc("/other.tar"))
        out.append(list(self.state.tokens))
        box.state = "B"

    cls = ipc_test(box, home_state="A", initial_state={},
                   go=step("A", "B", "go")(go), probe=lambda self: box.state)
    run(cls("vm-1", results_dir=str(tmp_path), skip_home=True))
    assert [p for _, p in seen] == ["/b/token_bundle.tar", "/other.tar"]
    assert isinstance(seen[0][0], FakeApp)
    assert out[0]["status"] == {"overall_status": "COMPLETED"}
    assert out[2] == ["tok", "tok"]


def test_enable_ipc_needs_a_bundle(box, monkeypatch):
    monkeypatch.setattr(
        ipc_ticket, "enable_token_mode_wapi", lambda app, path: pytest.fail("called"),
    )
    tt = ipc_test(box, bundle_path=None)("vm-1")
    tt._t = types.SimpleNamespace(app=None, state=types.SimpleNamespace())
    with pytest.raises(ValueError, match="no bundle"):
        tt.enable_ipc()


def test_phase_titles_are_overridable_and_default_output_is_unchanged(box, tmp_path):
    cls = make_test(
        box, deploy_groups={"pyabs"}, deploy_title="push files",
        preflight_title="box clean", home_title="reset",
    )
    tt = cls("vm-1", results_dir=str(tmp_path))
    assert run(tt) == 0
    assert headings(report(tt)) == [
        "1. connect", "2. push files", "3. box clean", "4. to b", "5. to c", "reset",
    ]


def test_default_titles():
    assert (TicketTest.deploy_title, TicketTest.preflight_title,
            TicketTest.home_title) == ("deploy", "preflight", "home")
