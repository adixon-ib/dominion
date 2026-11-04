"""Unit tests for ticket.py: Ticket and Checks. No network, docker or lab tools."""

import contextlib
import datetime
import json
import os
import re
import sys

import pytest

import dominion.src.ticket as ticket_mod
from dominion import Appliance, Ticket, create_logger
from dominion.src.harness import Run


class FakeContainer:
    def __init__(self, app):
        self.app = app

    def sh(self, cmd, check=True):
        rec = {"kind": "sh", "cmd": cmd, "rc": 0, "stdout": "out\n", "stderr": ""}
        if self.app.on_call:
            self.app.on_call(rec)
        return 0, "out\n", ""


class FakeConsole:
    def __init__(self, text):
        self.text = text

    def run(self, cmd):
        if isinstance(self.text, Exception):
            raise self.text
        return self.text


class FakeApp:
    console_text = "Version : 9.0.7-12345\n"

    def __init__(self, cfg):
        self.cfg = cfg
        self.hostname = cfg.hostname
        self.wapi_version = None
        self.on_call = None
        self.container = FakeContainer(self)

    @contextlib.contextmanager
    def console_session(self):
        yield FakeConsole(self.console_text)

    def dump_infoblox_log(self, dest):
        with open(dest, "w") as f:
            f.write("ib log\n")

    def __str__(self):
        return f"{self.hostname} ({self.cfg.ip})"


@pytest.fixture(autouse=True)
def fake_lab(monkeypatch):
    monkeypatch.setattr(ticket_mod, "lab_network", lambda h: ("10.0.0.9", "m", "g"))
    monkeypatch.setattr(
        Appliance, "from_config", classmethod(lambda cls, cfg: FakeApp(cfg)),
    )


def read_report(t):
    with open(os.path.join(t.output_dir, "report.md")) as f:
        return f.read()


def make(tmp_path, host="vm-1", **kw):
    return Ticket("NIOS-1", host, results_dir=str(tmp_path), **kw)


def run_ticket(t, body):
    """Run body inside the Ticket and return (exit code, summary.txt text)."""
    with pytest.raises(SystemExit) as exc, t:
        body(t)
    with open(os.path.join(t.output_dir, "summary.txt")) as f:
        return exc.value.code, f.read()


def boom():
    raise ValueError("boom")


# -- phases -----------------------------------------------------------------------

def test_all_pass_exits_zero_and_none_return_is_pass(tmp_path):
    phases = []

    def body(t):
        phases.append(t.phase("1. connect", t.connect))
        phases.append(t.phase("2. quiet", lambda: None, needs=phases[0]))

    code, text = run_ticket(make(tmp_path), body)
    assert code == 0
    assert [p.status for p in phases] == ["PASS", "PASS"]
    assert "2 passed, 0 failed, 0 skipped" in text


def test_failed_need_blocks_with_reason_and_propagates(tmp_path):
    ran = []
    phases = {}

    def body(t):
        phases["pre"] = t.phase("2. preflight", boom)
        phases["base"] = t.phase(
            "3. baseline", lambda: ran.append(1), needs=phases["pre"],
        )
        phases["next"] = t.phase(
            "4. next", lambda: ran.append(2), needs=[phases["base"]],
        )

    code, text = run_ticket(make(tmp_path), body)
    assert code == 1
    assert ran == []
    assert phases["pre"].status == "FAIL"
    assert phases["base"].reason == "needs 2. preflight (failed)"
    assert phases["next"].reason == "needs 3. baseline (skipped)"
    assert "needs 2. preflight (failed)" in text
    assert "needs 3. baseline (skipped)" in text


def test_reason_names_every_blocker(tmp_path):
    phases = {}

    def body(t):
        a = t.phase("a", boom)
        b = t.phase("b", boom)
        phases["c"] = t.phase("c", lambda: None, needs=[a, b])

    run_ticket(make(tmp_path), body)
    assert phases["c"].reason == "needs a (failed), b (failed)"


def test_deliberate_skip_does_not_block(tmp_path):
    phases = {}

    def body(t):
        phases["skip"] = t.phase("1b. deploy", boom, skip="no --deploy")
        phases["next"] = t.phase("2. pre", lambda: None, needs=phases["skip"])

    code, text = run_ticket(make(tmp_path), body)
    assert code == 0
    assert phases["skip"].status == "SKIP"
    assert phases["skip"].reason == "no --deploy"
    assert phases["next"].status == "PASS"
    assert "no --deploy" in text


# -- cleanup ----------------------------------------------------------------------

def test_cleanup_runs_after_failed_phase_in_reverse_and_failure_does_not_mask(tmp_path):
    order = []

    def bad():
        order.append("bad")
        raise RuntimeError("cleanup broke")

    def body(t):
        t.cleanup(lambda: order.append("first"))
        t.cleanup(bad)
        t.phase("1. connect", t.connect)
        t.phase("2. fails", boom)

    code, text = run_ticket(make(tmp_path), body)
    assert code == 1
    assert order == ["bad", "first"]
    assert "FAIL  cleanup 2" in text
    assert "PASS  cleanup 1" in text
    assert "cleanup broke" in text


def test_single_cleanup_is_named_cleanup(tmp_path):
    def body(t):
        t.cleanup(lambda: None)
        t.phase("1. connect", t.connect)

    _, text = run_ticket(make(tmp_path), body)
    assert "PASS  cleanup " in text
    assert "cleanup 1" not in text


def test_cleanup_runs_on_keyboard_interrupt_and_exit_is_nonzero(tmp_path):
    ran = []

    def body(t):
        t.cleanup(lambda: ran.append("cleanup"))
        t.phase("1. connect", t.connect)
        raise KeyboardInterrupt

    code, text = run_ticket(make(tmp_path), body)
    assert code == 1
    assert ran == ["cleanup"]
    assert "FAIL  interrupted: KeyboardInterrupt" in text
    assert "Traceback" in text


def test_exception_in_block_is_recorded(tmp_path):
    def body(t):
        t.phase("1. connect", t.connect)
        raise ValueError("escaped")

    code, text = run_ticket(make(tmp_path), body)
    assert code == 1
    assert "interrupted: ValueError" in text
    assert "escaped" in text


def test_cleanup_skipped_when_not_connected(tmp_path):
    ran = []

    def body(t):
        t.cleanup(lambda: ran.append(1))
        t.phase("1. connect", t.connect)

    code, text = run_ticket(make(tmp_path, host=None), body)
    assert code == 1
    assert ran == []
    assert "SKIP  cleanup" in text
    assert "not connected" in text


# -- summary ----------------------------------------------------------------------

def test_summary_has_host_dominion_warning_and_note_lines(tmp_path):
    def body(t):
        t.phase("1. connect", t.connect)
        t.warn("careful")
        t.note("fyi")

    _, text = run_ticket(make(tmp_path), body)
    lines = text.splitlines()
    assert "host: vm-1" in lines
    prefix = f"dominion {ticket_mod.PACKAGE_DIR}: "
    assert any(line.startswith(prefix) for line in lines)
    assert "WARNING: careful" in lines
    assert "NOTE: fyi" in lines
    assert "Target: vm-1 (10.0.0.9)" in lines


def test_connect_failed_still_writes_summary_and_junit(tmp_path):
    def body(t):
        t.phase("1. connect", t.connect)

    t = make(tmp_path, host="")
    code, text = run_ticket(t, body)
    assert code == 1
    assert "Target: (none)" in text
    assert "host: (none)" in text
    assert "FAIL  1. connect" in text
    assert "no host given" in text
    assert os.path.isfile(os.path.join(t.output_dir, "junit.xml"))


def test_connect_sets_wapi_version_and_config(tmp_path):
    t = make(tmp_path, worktree="/wt", wapi_version="v2.15")
    with pytest.raises(SystemExit), t:
        t.phase("1. connect", t.connect)
        assert t.app.wapi_version == "v2.15"
        assert t.app.cfg.ip == "10.0.0.9"
        assert t.app.cfg.worktree == "/wt"
        assert t.app.cfg.output_dir == t.output_dir
        assert t.container is t.app.container


def test_app_property_error_when_not_connected(tmp_path):
    t = make(tmp_path)
    with pytest.raises(RuntimeError, match="appliance not connected"):
        t.app
    with pytest.raises(RuntimeError, match="appliance not connected"):
        t.container


def test_write_dumps_json(tmp_path):
    t = make(tmp_path)
    with pytest.raises(SystemExit), t:
        path = t.write("x.json", {"a": 1})
    assert path == os.path.join(t.output_dir, "x.json")
    with open(path) as f:
        assert '"a": 1' in f.read()


# -- run.log and stream restore ---------------------------------------------------

def test_run_log_gets_stdout_and_logger_output_and_streams_restored(tmp_path, capsys):
    out, err = sys.stdout, sys.stderr
    lg = create_logger("dominion.test_ticket_log")
    t = make(tmp_path)
    with pytest.raises(SystemExit), t:
        assert sys.stdout is not out
        print("hello stdout")
        lg.info("hello logger")
        t.phase("1. connect", t.connect)
    assert sys.stdout is out
    assert sys.stderr is err
    assert all(h.stream is err for h in lg.handlers)
    with open(os.path.join(t.output_dir, "run.log")) as f:
        log_text = f.read()
    assert "hello stdout" in log_text
    assert "hello logger" in log_text
    assert "TEST: 1. connect" in log_text
    assert "RESULTS: 1 passed" in log_text
    assert "host: vm-1" in capsys.readouterr().out


# -- Run compatibility ------------------------------------------------------------

def test_run_summary_without_target_still_raises_type_error(tmp_path):
    with pytest.raises(TypeError):
        Run(output_dir=str(tmp_path)).summary()


def test_run_summary_extra_lines(tmp_path):
    run = Run(output_dir=str(tmp_path))
    run.add(FakeApp(type("C", (), {"hostname": "h", "ip": "1.1.1.1"})()))
    run.test("x", lambda: None)
    with pytest.raises(SystemExit) as exc:
        run.summary(extra=["EXTRA: one"])
    assert exc.value.code == 0
    with open(os.path.join(run.output_dir, "summary.txt")) as f:
        assert f.read().rstrip().endswith("EXTRA: one")


# -- Checks -----------------------------------------------------------------------

def test_checks_raises_listing_all_failures():
    t = Ticket("x", "h")
    with pytest.raises(AssertionError) as exc, t.checks("baseline") as c:
        c.expect("a", 1, 1)
        c.expect("b", "one", "uno")
        c.expect("c", "QPS", [])
    assert str(exc.value) == (
        "baseline:\n  b: expected one, got uno\n  c: expected QPS, got []"
    )


def test_checks_ok_flag_overrides_equality():
    with Ticket("x", "h").checks("x") as c:
        c.expect("range", "1..5", 3, ok=True)


def test_checks_does_not_mask_in_flight_exception():
    with pytest.raises(KeyError), Ticket("x", "h").checks("x") as c:
        c.expect("a", 1, 2)
        raise KeyError("real")


# -- report.md --------------------------------------------------------------------

def test_report_structure_and_checks(tmp_path):
    def member_latch(t):
        t.container.sh("ls /var/lib/token_data")
        with t.checks("latch") as c:
            c.expect("upsize_status", "OVERAGE_UPSIZE", "OVERAGE_UPSIZE")
            c.expect("capacity types", "QPS,LPS,Objects", [])

    def body(t):
        connect = t.phase("1. connect", t.connect)
        t.phase("6. member latch", lambda: member_latch(t), needs=connect,
                pre=[("archive backed up", lambda: True)])

    t = make(tmp_path)
    code, _ = run_ticket(t, body)
    report = read_report(t)
    print(report)
    assert code == 1
    head = report.split("\n## ")[0]
    assert "# NIOS-1" in head
    assert "- Host: vm-1" in head
    assert "- Build: 9.0.7-12345" in head
    assert "- Verdict: FAIL (1 passed, 1 failed, 0 skipped)" in head
    assert re.search(r"## 6\. member latch  FAIL \(\d+\.\ds\)", report)
    assert "Preconditions\n- archive backed up: met" in report
    assert "1. connect: met" not in report
    assert "$ ls /var/lib/token_data  -> exit 0" in report
    assert "stdout\n```\nout\n```" in report
    assert "- upsize_status: expected OVERAGE_UPSIZE, got OVERAGE_UPSIZE  ok" in report
    assert "- capacity types: expected QPS,LPS,Objects, got []  FAIL" in report
    assert "Error\n```\nTraceback" in report
    assert "AssertionError: latch:" in report


def test_unmet_pre_skips_blocks_dependants_and_names_label(tmp_path):
    phases = {}
    ran = []

    def body(t):
        phases["a"] = t.phase(
            "5. a", lambda: ran.append("a"),
            pre=[("backed up", lambda: True), ("archive present", lambda: False),
                 ("reachable", lambda: 0)],
        )
        phases["b"] = t.phase("6. b", lambda: ran.append("b"), needs=phases["a"])

    t = make(tmp_path)
    code, text = run_ticket(t, body)
    report = read_report(t)
    assert code == 1
    assert "UNMET (exit 1): 5. a: archive present, reachable" in text.splitlines()
    assert "- Verdict: FAIL" in report
    assert "0 failed, 2 skipped" in text
    assert "SKIP  5. a" in text
    assert ran == []
    assert phases["a"].status == "SKIP"
    assert phases["a"].blocked
    assert phases["a"].reason == "not met: archive present, reachable"
    assert phases["b"].reason == "needs 5. a (skipped)"
    assert "not met: archive present, reachable" in text
    assert "- backed up: met" in report
    assert "- archive present: not met\n" in report
    assert "## 5. a  SKIP" in report


def test_pre_that_raises_fails_the_phase(tmp_path):
    phases = {}
    ran = []

    def raises():
        raise OSError("no ssh")

    def body(t):
        phases["a"] = t.phase(
            "5. a", lambda: ran.append("a"),
            pre=[("backed up", lambda: True), ("reachable", raises),
                 ("later", lambda: ran.append("later"))],
        )
        phases["b"] = t.phase("6. b", lambda: ran.append("b"), needs=phases["a"])

    t = make(tmp_path)
    code, text = run_ticket(t, body)
    report = read_report(t)
    assert code == 1
    assert ran == []
    assert phases["a"].status == "FAIL"
    assert phases["b"].reason == "needs 5. a (failed)"
    assert "FAIL  5. a" in text
    assert "OSError: no ssh" in text
    assert "- reachable: not met (OSError: no ssh)\n- later: not evaluated" in report
    assert "## 5. a  FAIL" in report
    assert "Error\n```\nTraceback" in report


def test_needs_none_means_no_needs(tmp_path):
    phases = {}
    run_ticket(
        make(tmp_path),
        lambda t: phases.update(a=t.phase("1. a", lambda: None, needs=None)),
    )
    assert phases["a"].status == "PASS"


def test_nested_phase_restores_attribution_to_outer(tmp_path):
    def outer(t):
        t.container.sh("before")
        t.phase("inner", lambda: t.container.sh("in inner"))
        t.container.sh("after")

    t = make(tmp_path)

    def body(t):
        t.phase("1. connect", t.connect)
        t.phase("outer", lambda: outer(t))

    run_ticket(t, body)
    report = read_report(t)
    inner = report.split("## inner")[1].split("\n## ")[0]
    assert "in inner" in inner
    assert "before" not in inner
    outer_sec = report.split("## outer")[1].split("\n## ")[0]
    assert "$ before" in outer_sec
    assert "$ after" in outer_sec


def test_second_interrupt_in_cleanup_does_not_stop_the_rest(tmp_path):
    order = []

    def interrupted():
        order.append("second")
        raise KeyboardInterrupt

    def body(t):
        t.cleanup(lambda: order.append("first"))
        t.cleanup(interrupted)
        t.phase("1. connect", t.connect)

    t = make(tmp_path)
    code, text = run_ticket(t, body)
    report = read_report(t)
    assert code == 1
    assert order == ["second", "first"]
    assert "FAIL  cleanup 2" in text
    assert "PASS  cleanup 1" in text
    assert "KeyboardInterrupt" in report
    assert os.path.isfile(os.path.join(t.output_dir, "junit.xml"))


def test_write_sorts_keys(tmp_path):
    t = make(tmp_path)
    with pytest.raises(SystemExit), t:
        path = t.write("x.json", {"b": 1, "a": 2})
    with open(path) as f:
        text = f.read()
    assert text.index('"a"') < text.index('"b"')


def test_unmet_need_leaves_pre_not_evaluated(tmp_path):
    called = []
    phases = {}

    def body(t):
        bad = t.phase("2. bad", boom)
        phases["x"] = t.phase(
            "3. x", lambda: None, needs=bad,
            pre=[("thing", lambda: called.append(1) or True)],
        )

    t = make(tmp_path)
    run_ticket(t, body)
    report = read_report(t)
    assert called == []
    assert phases["x"].reason == "needs 2. bad (failed)"
    assert "- 2. bad: not met (failed)\n- thing: not evaluated" in report


def test_report_has_cleanup_and_interrupted_sections_and_warnings(tmp_path):
    def body(t):
        t.cleanup(lambda: t.container.sh("rm -f x"))
        t.phase("1. connect", t.connect)
        t.warn("watch out")
        t.note("fyi")
        raise KeyboardInterrupt

    t = make(tmp_path)
    run_ticket(t, body)
    report = read_report(t)
    assert "## interrupted: KeyboardInterrupt  FAIL" in report
    assert "## cleanup  PASS" in report
    assert "$ rm -f x  -> exit 0" in report
    assert "## Warnings\n- watch out" in report
    assert "## Notes\n- fyi" in report


def test_report_written_when_not_connected(tmp_path):
    t = make(tmp_path, host=None)
    run_ticket(t, lambda t: t.phase("1. connect", t.connect))
    report = read_report(t)
    assert "- Host: (none)" in report
    assert "- Build: unknown" in report
    assert "## 1. connect  FAIL" in report


def test_calls_outside_a_phase_are_dropped(tmp_path):
    t = make(tmp_path)
    with pytest.raises(SystemExit), t:
        t.phase("1. connect", t.connect)
        t.container.sh("outside")
        t.phase("2. in", lambda: t.container.sh("inside"))
    report = read_report(t)
    assert "outside" not in report
    assert "$ inside" in report


def test_build_failure_only_warns(tmp_path, monkeypatch):
    monkeypatch.setattr(FakeApp, "console_text", RuntimeError("console down"))
    t = make(tmp_path)
    code, text = run_ticket(t, lambda t: t.phase("1. connect", t.connect))
    assert code == 0
    assert t.build == "unknown"
    assert "WARNING: build not read: console down" in text


def test_build_without_version_line_warns(tmp_path, monkeypatch):
    monkeypatch.setattr(FakeApp, "console_text", "nothing here")
    t = make(tmp_path)
    code, text = run_ticket(t, lambda t: t.phase("1. connect", t.connect))
    assert code == 0
    assert "WARNING: build not read: no Version line" in text


def test_sample_report(tmp_path):
    """Renders a full report, printed with -s."""
    def member_latch(t):
        t.container.sh("ls /var/lib/token_data")
        with t.checks("latch") as c:
            c.expect("upsize_status", "OVERAGE_UPSIZE", "OVERAGE_UPSIZE")
            c.expect("capacity types", "QPS,LPS,Objects", [])

    def body(t):
        t.cleanup(lambda: t.container.sh("rm -f /tmp/x"))
        connect = t.phase("1. connect", t.connect)
        dep = t.phase("1b. deploy", boom, needs=connect, skip="no --deploy")
        t.phase("6. member latch", lambda: member_latch(t), needs=[connect, dep],
                pre=[("archive backed up", lambda: True)])
        t.phase("7. grid", lambda: None, needs=connect,
                pre=[("calculator running", lambda: False)])
        t.warn("usage_percent is 0")

    t = make(tmp_path)
    run_ticket(t, body)
    print(read_report(t))


def test_wapi_list_renders_one_compact_line_per_element():
    rows = [{"type": "SERVERS", "b": [1, 2], "a": {"z": 1}}, {"type": "X"}]
    text = "\n".join(ticket_mod._render_call({
        "kind": "wapi", "method": "GET",
        "path": "/wapi/v2.15/ipc:overage?_return_fields=type",
        "body": None, "status": 200, "response": json.dumps(rows),
    }))
    assert text == (
        "GET /wapi/v2.15/ipc:overage?_return_fields=type  -> 200\n"
        "```\n"
        '{"a": {"z": 1}, "b": [1, 2], "type": "SERVERS"}\n'
        '{"type": "X"}\n'
        "```"
    )


def test_wapi_dict_and_request_body_are_one_compact_line():
    text = "\n".join(ticket_mod._render_call({
        "kind": "wapi", "method": "PUT", "path": "/wapi/v2.15/x",
        "body": {"b": 1, "a": [1, 2]}, "status": 200, "response": '{"y": 1,\n "x": 2}',
    }))
    assert 'Request body\n```\n{"a": [1, 2], "b": 1}\n```' in text
    assert '```\n{"x": 2, "y": 1}\n```' in text


def test_json_stdout_compacted_and_other_stdout_as_is():
    def render(out):
        return "\n".join(ticket_mod._render_call({
            "kind": "sh", "cmd": "c", "rc": 0, "stdout": out, "stderr": "",
        }))

    assert render('{"b": 1,\n "a": 2}\n').endswith('```\n{"a": 2, "b": 1}\n```')
    assert render("3\n").endswith("```\n3\n```")
    assert render("a\nb c\n").endswith("```\na\nb c\n```")


def test_wapi_status_zero_with_no_body_renders_header_only():
    text = "\n".join(ticket_mod._render_call({
        "kind": "wapi", "method": "GET", "path": "/wapi/v2.15/x",
        "body": None, "status": 0, "response": "",
    }))
    assert text == "GET /wapi/v2.15/x  -> 0"


def _sh(cmd, out="", rc=0):
    return {"kind": "sh", "cmd": cmd, "rc": rc, "stdout": out, "stderr": ""}


def _wapi(path, status=200, response=""):
    return {"kind": "wapi", "method": "GET", "path": path, "body": None,
            "status": status, "response": response}


def test_collapse_merges_consecutive_only_and_keeps_last():
    calls = [_sh("snap", "1"), _sh("snap", "2"), _sh("other"), _sh("snap", "3")]
    got = ticket_mod._collapse(calls)
    assert [(c["cmd"], n) for c, n in got] == [("snap", 2), ("other", 1), ("snap", 1)]
    assert got[0][0]["stdout"] == "2"


def test_collapse_wapi_by_method_path_query_and_status():
    calls = [_wapi("/a?x=1", 200), _wapi("/a?x=1", 200), _wapi("/a?x=1", 500),
             _wapi("/a?x=2")]
    got = ticket_mod._collapse(calls)
    assert [(c["path"], c["status"], n) for c, n in got] == [
        ("/a?x=1", 200, 2), ("/a?x=1", 500, 1), ("/a?x=2", 200, 1),
    ]


def test_collapse_keeps_posts_with_different_bodies_apart():
    def post(body):
        return {"kind": "wapi", "method": "POST", "path": "/wapi/v2.15/x",
                "body": body, "status": 200, "response": ""}

    calls = [post({"a": 1, "b": 2}), post({"b": 2, "a": 1}), post({"a": 2})]
    got = ticket_mod._collapse(calls)
    assert [(c["body"]["a"], n) for c, n in got] == [(1, 2), (2, 1)]


def test_collapse_keeps_same_cmd_with_different_rc_apart():
    got = ticket_mod._collapse([_sh("t", rc=1), _sh("t", rc=1), _sh("t", rc=0)])
    assert [(c["rc"], n) for c, n in got] == [(1, 2), (0, 1)]


def test_render_collapsed_header_shows_count_and_last_result():
    text = "\n".join(ticket_mod._render_call(_sh("snap", "9", rc=1), 7))
    assert text.startswith("$ snap  -> exit 1  (x7, last shown)")
    assert "```\n9\n```" in text


def test_met_needs_hidden_and_unmet_need_shown(tmp_path):
    def body(t):
        ok = t.phase("1. ok", lambda: None)
        bad = t.phase("2. bad", boom)
        t.phase("3. fine", lambda: None, needs=ok)
        t.phase("4. blocked", lambda: None, needs=[ok, bad])

    t = make(tmp_path)
    run_ticket(t, body)
    report = read_report(t)
    fine = report.split("## 3. fine")[1].split("\n## ")[0]
    assert "Preconditions" not in fine
    blocked = report.split("## 4. blocked")[1].split("\n## ")[0]
    assert "Preconditions\n- 2. bad: not met (failed)" in blocked
    assert "1. ok" not in blocked


def test_section_order_and_phase6_shape(tmp_path):
    rows = [{"type": f"T{i}", "n": i} for i in range(4)]
    t = make(tmp_path)

    def phase6():
        t.container.sh("test -f backup")
        for _ in range(7):
            t.container.sh("helper snapshot", check=False)
        path = "/wapi/v2.15/ipc:overage?f=type"
        t.app.on_call(_wapi(path, 200, json.dumps(rows)))
        with t.checks("latch") as c:
            c.expect("upsize_status", "OVERAGE_UPSIZE", "OVERAGE_UPSIZE")
            c.expect("capacity types", "QPS,LPS,Objects", [])

    with pytest.raises(SystemExit), t:
        connect = t.phase("1. connect", t.connect)
        t.phase("6. member latch", phase6, needs=connect,
                pre=[("archive backed up", lambda: True)])
    report = read_report(t)
    sec = "## 6. member latch" + report.split("## 6. member latch")[1].split("\n## ")[0]
    print(sec)
    heads = [sec.index(h) for h in ("Preconditions", "Checks", "Error", "Calls")]
    assert heads == sorted(heads)
    assert "$ helper snapshot  -> exit 0  (x7, last shown)" in sec
    assert "$ test -f backup  -> exit 0\n" in sec
    assert '{"n": 3, "type": "T3"}' in sec
    max_lines = 45
    assert len(sec.splitlines()) < max_lines


def test_unmet_pre_alone_exits_one_with_skip_line(tmp_path):
    t = make(tmp_path)
    code, text = run_ticket(
        t, lambda t: t.phase("1. a", lambda: None, pre=[("thing", lambda: False)]),
    )
    assert code == 1
    assert "SKIP  1. a" in text
    assert "not met: thing" in text
    assert "0 passed, 0 failed, 1 skipped" in text


def test_deliberate_skip_alone_exits_zero(tmp_path):
    code, _ = run_ticket(
        make(tmp_path), lambda t: t.phase("1. a", boom, skip="no --deploy"),
    )
    assert code == 0


def test_needs_propagated_skip_alone_does_not_force_failure(tmp_path):
    def body(t):
        skipped = t.phase("1. a", boom, skip="no --deploy")
        t.phase("2. b", lambda: None, needs=skipped)

    code, _ = run_ticket(make(tmp_path), body)
    assert code == 0


def test_phase_fail_plus_unmet_pre_exits_one(tmp_path):
    def body(t):
        t.phase("1. bad", boom)
        t.phase("2. a", lambda: None, pre=[("thing", lambda: False)])

    code, text = run_ticket(make(tmp_path), body)
    assert code == 1
    assert "FAIL  1. bad" in text
    assert "UNMET (exit 1): 2. a: thing" in text


def test_body_exception_plus_unmet_pre_exits_one(tmp_path):
    def body(t):
        t.phase("1. a", lambda: None, pre=[("thing", lambda: False)])
        raise ValueError("escaped")

    code, text = run_ticket(make(tmp_path), body)
    assert code == 1
    assert "interrupted: ValueError" in text
    assert "UNMET (exit 1): 1. a: thing" in text


def _junit(t):
    import xml.etree.ElementTree as ET

    root = ET.parse(os.path.join(t.output_dir, "junit.xml")).getroot()
    suite = root.find("testsuite")
    return suite, {c.get("name"): c for c in suite.findall("testcase")}


def test_unmet_pre_is_a_junit_failure(tmp_path):
    t = make(tmp_path)
    run_ticket(
        t, lambda t: t.phase("1. a", lambda: None, pre=[("thing", lambda: False)]),
    )
    suite, cases = _junit(t)
    assert suite.get("failures") == "1"
    assert suite.get("skipped") == "0"
    failure = cases["1. a"].find("failure")
    assert failure.get("message") == "not met: thing"
    assert cases["1. a"].find("skipped") is None


def test_deliberate_skip_stays_skipped_in_junit(tmp_path):
    t = make(tmp_path)
    run_ticket(t, lambda t: t.phase("1. a", boom, skip="no --deploy"))
    suite, cases = _junit(t)
    assert suite.get("failures") == "0"
    assert suite.get("skipped") == "1"
    assert cases["1. a"].find("skipped") is not None
    assert cases["1. a"].find("failure") is None


def _render_sh(out):
    return "\n".join(ticket_mod._render_call(_sh("c", out)))


def test_stdout_scalars_stay_as_is_only_containers_compact():
    assert _render_sh("0.10\n").endswith("```\n0.10\n```")
    assert _render_sh("007\n").endswith("```\n007\n```")
    assert _render_sh('"a"\n').endswith('```\n"a"\n```')
    assert _render_sh("[1,\n 2]").endswith("```\n1\n2\n```")


def test_output_with_backticks_keeps_the_fence_intact():
    text = _render_sh("a\n```\nb\n")
    assert text.endswith("````\na\n```\nb\n````")


def test_deeply_nested_json_falls_back_to_text():
    deep = "[" * 100000 + "]" * 100000
    assert _render_sh(deep).endswith("```\n" + deep + "\n```")


def test_compact_keeps_non_ascii():
    text = "\n".join(ticket_mod._render_call({
        "kind": "wapi", "method": "GET", "path": "/p", "body": None,
        "status": 200, "response": '{"name": "caf\\u00e9"}',
    }))
    assert '{"name": "caf\u00e9"}' in text


def test_string_request_body_renders_quoted():
    text = "\n".join(ticket_mod._render_call({
        "kind": "wapi", "method": "POST", "path": "/p", "body": "raw",
        "status": 200, "response": "",
    }))
    assert 'Request body\n```\n"raw"\n```' in text


def test_state_defaults_to_empty_namespace_and_no_section(tmp_path):
    t = make(tmp_path)
    assert vars(t.state) == {}
    run_ticket(t, lambda t: t.phase("1. connect", t.connect))
    assert "State at exit" not in read_report(t)


def test_state_defaults_and_runtime_fields_sorted_after_phases(tmp_path):
    t = make(tmp_path, state={"zeta": [1, 2], "alpha": {"b": 1, "a": "x"}})
    assert t.state.zeta == [1, 2]

    def body(t):
        t.phase("1. connect", t.connect)
        t.state.mid = 3
        t.warn("w")

    run_ticket(t, body)
    report = read_report(t)
    section = report.split("## State at exit\n")[1].split("\n## ")[0]
    assert section.splitlines() == [
        '- alpha: {"a": "x", "b": 1}', "- mid: 3", "- zeta: [1, 2]",
    ]
    assert report.index("## 1. connect") < report.index("## State at exit")
    assert report.index("## State at exit") < report.index("## Warnings")


def test_state_non_json_values_fall_back_to_repr(tmp_path):
    class Thing:
        def __repr__(self):
            return "<Thing>"

    when = datetime.datetime(2026, 1, 2, 3, 4, 5)
    t = make(tmp_path, state={"when": when, "thing": Thing()})
    run_ticket(t, lambda t: t.phase("1. connect", t.connect))
    report = read_report(t)
    assert "- thing: <Thing>" in report
    assert f"- when: {when!r}" in report


def test_state_repr_never_raises_and_escapes_newlines(tmp_path):
    class Bad:
        def __repr__(self):
            raise RuntimeError("no repr")

    class Multi:
        def __repr__(self):
            return "a\nb"

    circular = []
    circular.append(circular)
    deep = []
    for _ in range(5000):
        deep = [deep]
    t = make(tmp_path, state={
        "bad": Bad(), "multi": Multi(), "circular": circular, "deep": deep,
    })
    code, text = run_ticket(t, lambda t: t.phase("1. connect", t.connect))
    report = read_report(t)
    assert code == 0
    assert "- bad: <unrepresentable Bad>" in report
    assert "- multi: a\\nb" in report
    assert "- circular: [[...]]" in report
    assert "- deep: " in report
    assert "passed" in text
