"""Unit tests for report.html: Ticket writes it next to report.md from the same
section data. Minimal local fakes, independent of the other test modules."""

import contextlib
import html.parser
import os
import re
import types

import pytest

import dominion.src.ticket as ticket_mod
from dominion import Appliance, LogDump, LogMark, Ticket
from dominion.src import report_html

REJECT = (
    "[2026/11/06 19:07:00.277] (1962173 /usr/bin/httpd) one/token_bundle.py:217 "
    "apply(): ipc:bundle rid=6a0638b9 user=admin fn=apply outcome=rejected "
    'code=one.token_bundle.NOT_ENABLED reason="IPc mode is not enabled." dur=0.000s'
)


class FakeContainer:
    def __init__(self, app):
        self.app = app

    def sh(self, cmd, check=True):
        if self.app.on_call:
            self.app.on_call(
                {"kind": "sh", "cmd": cmd, "rc": 0, "stdout": "out\n", "stderr": ""},
            )
        return 0, "out\n", ""


class FakeApp:
    script = None  # per-capture line lists, popped in order, instead of log_lines
    log_lines = ("old rid=0\n", "new ipc:bundle rid=1\n", "new other\n")

    def __init__(self, cfg):
        self.hostname = cfg.hostname
        self.wapi_version = None
        self.on_call = None
        self.container = FakeContainer(self)

    @contextlib.contextmanager
    def console_session(self):
        yield types.SimpleNamespace(run=lambda cmd: "Version : 9.0.7-12345\n")

    def mark_infoblox_log(self):
        return LogMark(log_path="/ib.log", line_count=1)

    def dump_infoblox_log(self, dest, since=None, grep=None, fixed=False):
        if self.script is not None:
            text = "".join(f"{ln}\n" for ln in self.script.pop(0))
        else:
            rows = [r for r in self.log_lines[since.line_count:] if grep in r]
            text = "".join(rows)
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        with open(dest, "w") as f:
            f.write(text)
        return LogDump(path=dest, text=text)

    def __str__(self):
        return self.hostname


@pytest.fixture(autouse=True)
def fake_lab(monkeypatch):
    monkeypatch.setattr(ticket_mod, "lab_network", lambda h: ("10.0.0.9", "m", "g"))
    monkeypatch.setattr(
        Appliance, "from_config", classmethod(lambda cls, cfg: FakeApp(cfg)),
    )


class Box:
    def __init__(self, state="Licenses"):
        self.state = state

    def probe(self):
        return self.state

    def to(self, state):
        def fn():
            self.state = state
        return fn


def boom():
    raise ValueError("boom")


def make(tmp_path, **kw):
    return Ticket("NIOS-1", "vm-1", results_dir=str(tmp_path), **kw)


def run_ticket(t, body):
    with pytest.raises(SystemExit) as exc, t:
        body(t)
    with open(os.path.join(t.output_dir, "summary.txt")) as f:
        return exc.value.code, f.read()


def read_report(t):
    with open(os.path.join(t.output_dir, "report.md"), encoding="utf-8") as f:
        return f.read()


def read_html(t):
    with open(os.path.join(t.output_dir, "report.html"), encoding="utf-8") as f:
        return f.read()


def card_names(page):
    return re.findall(r'<h2><span>(.*?)</span><span class="pill', page)


def test_written_on_passing_and_failing_runs(tmp_path):
    t = make(tmp_path / "ok")
    run_ticket(t, lambda t: t.phase("1. connect", t.connect))
    page = read_html(t)
    assert page.startswith("<!doctype html>")
    assert '<span class="badge PASS">PASS</span>' in page
    assert "1 passed, 0 failed, 0 skipped" in page
    assert "<script" not in page.lower().replace("&lt;script", "")

    t = make(tmp_path / "bad")
    run_ticket(t, lambda t: t.phase("1. boom", boom))
    page = read_html(t)
    assert '<span class="badge FAIL">FAIL</span>' in page
    assert '<span class="pill FAIL">FAIL</span>' in page
    assert '<pre class="error">' in page
    assert "ValueError: boom" in page


def test_header_has_host_build_rev_and_run_id(tmp_path):
    t = make(tmp_path)
    run_ticket(t, lambda t: t.phase("1. connect", t.connect))
    page = read_html(t)
    assert "<dt>Host</dt><dd>vm-1</dd>" in page
    assert "<dt>Build</dt><dd>9.0.7-12345</dd>" in page
    assert f"<dt>Run</dt><dd>{os.path.basename(t.output_dir)}</dd>" in page
    assert "<dt>Dominion</dt>" in page


def test_page_is_self_contained(tmp_path):
    t = make(tmp_path)
    run_ticket(t, lambda t: t.phase("1. connect", t.connect))
    page = read_html(t)
    assert "prefers-color-scheme:dark" in page
    assert 'name="viewport"' in page
    assert not re.search(r"<script|<link|src=|href=|@import|url\(", page)


def test_text_is_escaped_in_log_line_check_value_and_error(monkeypatch, tmp_path):
    evil = 'x <script>alert("a&b")</script> ipc:bundle '
    monkeypatch.setattr(FakeApp, "log_lines", ("old\n", evil + "\n"))
    t = make(tmp_path)

    def body(t):
        c = t.phase("1. connect", t.connect)

        def work():
            with t.nios_log("ipc:bundle "):
                pass
            with contextlib.suppress(AssertionError), t.checks("c") as ck:
                ck.expect("<b>label</b>", "e&1", "<script>g</script>")
            raise ValueError("<script>err & more</script>")

        t.phase("2. <i>work</i>", work, needs=c)

    run_ticket(t, body)
    page = read_html(t)
    assert "<script" not in page
    assert "<i>work" not in page
    for escaped in ("&lt;script&gt;alert(&quot;a&amp;b&quot;)&lt;/script&gt;",
                    "&lt;b&gt;label&lt;/b&gt;", "e&amp;1",
                    "&lt;script&gt;g&lt;/script&gt;",
                    "&lt;script&gt;err &amp; more&lt;/script&gt;",
                    "2. &lt;i&gt;work&lt;/i&gt;"):
        assert escaped in page


def test_cards_follow_report_md_order_with_home_last(tmp_path):
    box, order = Box(), []

    def body(t):
        t.cleanup(lambda: order.append("cleanup"))
        c = t.phase("1. connect", t.connect)
        t.steps(
            [("2. up", "Licenses", "IPc", boom)], probe=box.probe, needs=c,
            home=("home", "Licenses", lambda: order.append("home")),
        )

    t = make(tmp_path)
    run_ticket(t, body)
    md = re.findall(r"^## (.+?)  ", read_report(t), re.M)
    assert card_names(read_html(t)) == md
    assert md[-1] == "home"


def test_failed_check_listed_first_with_expected_and_got(tmp_path):
    def body(t):
        def work():
            with t.checks("c") as ck:
                ck.expect("good", 1, 1)
                ck.expect("bad one", 400, 200)

        t.phase("1. work", work)

    t = make(tmp_path)
    run_ticket(t, body)
    page = read_html(t)
    bad = page.index('class="failed"')
    good = page.index("good")
    assert bad < good
    assert "bad one: expected <code>400</code>, got <code>200</code>" in page


def test_skipped_step_shows_reason_and_precondition(tmp_path):
    box = Box("Other")

    def body(t):
        c = t.phase("1. connect", t.connect)
        t.steps([("2. up", "Licenses", "IPc", lambda: None)],
                probe=box.probe, needs=c)

    t = make(tmp_path)
    run_ticket(t, body)
    page = read_html(t)
    assert '<span class="pill SKIP">SKIP</span>' in page
    assert "not met: box in Licenses" in page
    assert "box in Licenses: not met (box in Other)" in page


def test_state_line_for_a_step(tmp_path):
    box = Box()

    def body(t):
        c = t.phase("1. connect", t.connect)
        t.steps([("2. up", "Licenses", "IPc", box.to("IPc"))],
                probe=box.probe, needs=c)

    t = make(tmp_path)
    run_ticket(t, body)
    assert ("State: Licenses -&gt; IPc (probed before: Licenses, after: IPc)"
            in read_html(t))


def test_nios_log_lines_are_rendered_and_oplog_fields_highlighted(
    monkeypatch, tmp_path,
):
    plain = "plain ipc:bundle line"
    monkeypatch.setattr(FakeApp, "log_lines", ("old\n", REJECT + "\n", plain + "\n"))
    t = make(tmp_path)

    def body(t):
        c = t.phase("1. connect", t.connect)

        def work():
            with t.nios_log("ipc:bundle "):
                pass

        t.phase("2. log", work, needs=c)

    run_ticket(t, body)
    page = read_html(t)
    assert "infoblox.log: ipc:bundle  (2 lines)" in page
    assert '<div class="logline o-rejected">' in page
    assert '<span class="badge">fn apply</span>' in page
    assert '<span class="badge warn">rejected</span>' in page
    assert '<span class="badge muted">rid 6a0638b9</span>' in page
    assert '<span class="badge muted">dur 0.000s</span>' in page
    assert f'<div class="logline"><code>{plain}</code></div>' in page


def test_nios_log_outside_a_phase_still_works(tmp_path):
    t = make(tmp_path)
    out = {}

    def body(t):
        t.connect()
        with t.nios_log("new") as nl:
            pass
        out["lines"] = nl.lines

    run_ticket(t, body)
    assert out["lines"] == ["new ipc:bundle rid=1", "new other"]


def test_calls_are_in_collapsed_details_with_a_count(tmp_path):
    def body(t):
        c = t.phase("1. connect", t.connect)
        t.phase("2. calls", lambda: (t.container.sh("ls"), t.container.sh("pwd")),
                needs=c)

    t = make(tmp_path)
    run_ticket(t, body)
    page = read_html(t)
    assert "<details><summary>Calls (2)</summary>" in page
    assert "<details open" not in page
    assert "$ ls -&gt; exit 0" in page


def test_state_at_exit_and_warnings_notes_listed(tmp_path):
    t = make(tmp_path, state={"tokens": ["a"]})

    def body(t):
        t.warn("careful <b>")
        t.note("fyi")

    run_ticket(t, body)
    page = read_html(t)
    assert "State at exit" in page
    assert "tokens: <code>[&quot;a&quot;]</code>" in page
    assert "careful &lt;b&gt;" in page
    assert "<li>fyi</li>" in page


def test_render_error_warns_and_does_not_break_summary_or_junit(
    monkeypatch, tmp_path,
):
    def explode(t):
        raise RuntimeError("render bug")

    monkeypatch.setattr(report_html, "render", explode)
    t = make(tmp_path)
    code, text = run_ticket(t, lambda t: t.phase("1. connect", t.connect))
    assert code == 0
    assert not os.path.exists(os.path.join(t.output_dir, "report.html"))
    assert os.path.exists(os.path.join(t.output_dir, "junit.xml"))
    assert "report.html not written: RuntimeError: render bug" in text


def test_report_md_is_unchanged_by_the_html_report(monkeypatch, tmp_path):
    def body(t):
        c = t.phase("1. connect", t.connect)

        def work():
            t.container.sh("ls")
            with t.checks("c") as ck:
                ck.expect("a", 1, 1)

        t.phase("2. work", work, needs=c)

    def md_of(sub):
        t = make(tmp_path / sub)
        run_ticket(t, body)
        return re.sub(r"\(\d+\.\ds\)", "(Ns)", read_report(t))

    with_html = md_of("html")
    monkeypatch.setattr(report_html, "render", lambda t: "x")
    assert md_of("no_html") == with_html


class _Balance(html.parser.HTMLParser):
    VOID = frozenset({"meta", "br", "hr", "img", "link", "input"})

    def __init__(self):
        super().__init__()
        self.stack, self.errors = [], []

    def handle_starttag(self, tag, attrs):
        if tag not in self.VOID:
            self.stack.append(tag)

    def handle_endtag(self, tag):
        if not self.stack or self.stack.pop() != tag:
            self.errors.append(tag)


def test_page_is_well_formed_with_every_card_kind(monkeypatch, tmp_path):
    lines = ("old\n", REJECT + "\n", "ipc:bundle x\n")
    monkeypatch.setattr(FakeApp, "log_lines", lines)
    box = Box()

    def body(t):
        t.warn("w")
        t.note("n")
        c = t.phase("1. connect", t.connect)

        def work():
            t.container.sh("ls")
            with t.nios_log("ipc:bundle "):
                pass
            with t.checks("c") as ck:
                ck.expect("a", 1, 1)
                ck.expect("b", 1, 2)

        t.phase("2. work", work, needs=c)
        t.steps([("3. up", "Licenses", "IPc", boom), ("4. no", "X", "Y", boom)],
                probe=box.probe, needs=c, home=("home", "Licenses", box.to("Licenses")))

    t = make(tmp_path, state={"k": 1})
    run_ticket(t, body)
    p = _Balance()
    p.feed(read_html(t))
    p.close()
    assert p.errors == []
    assert p.stack == []


def test_outcome_cannot_inject_attributes(monkeypatch, tmp_path):
    evil = ('ipc:bundle rid=aaaaaaaa fn=apply outcome="x\\" onmouseover=\\"alert(1)" '
            "dur=0.000s")
    monkeypatch.setattr(FakeApp, "log_lines", ("old\n", evil + "\n"))
    t = make(tmp_path)

    def body(t):
        c = t.phase("1. connect", t.connect)

        def work():
            with t.nios_log("ipc:bundle "):
                pass

        t.phase("2. log", work, needs=c)

    run_ticket(t, body)
    page = read_html(t)
    assert 'class="logline o-other"' in page

    names = set()

    class Attrs(html.parser.HTMLParser):
        def handle_starttag(self, tag, attrs):
            names.update(n for n, _ in attrs)

    Attrs().feed(page)
    assert not {n for n in names if n.startswith("on")}


def test_capture_is_capped_to_last_200_lines_with_a_pointer_to_the_file(
    monkeypatch, tmp_path,
):
    total = 250
    rows = tuple(f"ipc:bundle row{i}\n" for i in range(total))
    monkeypatch.setattr(FakeApp, "log_lines", ("old\n", *rows))
    t = make(tmp_path)

    def body(t):
        c = t.phase("1. connect", t.connect)

        def work():
            with t.nios_log("ipc:bundle "):
                pass

        t.phase("2. log", work, needs=c)

    run_ticket(t, body)
    page = read_html(t)
    assert "(250 lines)" in page
    assert "... 50 more lines, see nios_log/1.log" in page
    assert "row49<" not in page
    assert "row50<" in page
    assert "row249<" in page
    with open(os.path.join(t.output_dir, "nios_log", "1.log")) as f:
        assert f.read().count("\n") == total


def test_short_capture_has_no_more_lines_note(tmp_path):
    t = make(tmp_path)

    def body(t):
        c = t.phase("1. connect", t.connect)

        def work():
            with t.nios_log("ipc:bundle "):
                pass

        t.phase("2. log", work, needs=c)

    run_ticket(t, body)
    assert "more lines" not in read_html(t)


def test_nested_phase_capture_is_attributed_to_its_own_section(tmp_path):
    t = make(tmp_path)

    def body(t):
        c = t.phase("1. connect", t.connect)

        def inner():
            with t.nios_log("ipc:bundle "):
                pass

        def outer():
            with t.nios_log("other"):
                pass
            t.phase("3. inner", inner)

        t.phase("2. outer", outer, needs=c)

    run_ticket(t, body)
    page = read_html(t)
    cards = page.split('<section class="card">')
    by_name = {re.search(r"<span>(.*?)</span>", c).group(1): c for c in cards[1:]
               if "<h2><span>" in c}
    assert "infoblox.log: other " in by_name["2. outer"]
    assert "infoblox.log: ipc:bundle " not in by_name["2. outer"]
    assert "infoblox.log: ipc:bundle " in by_name["3. inner"]
    assert "infoblox.log: other" not in by_name["3. inner"]


def test_reports_are_written_as_utf8(tmp_path):
    t = make(tmp_path)

    def body(t):
        t.note("caf\u00e9 \u2713")

    run_ticket(t, body)
    assert "caf\u00e9 \u2713" in read_html(t)
    assert "caf\u00e9 \u2713" in read_report(t)


# -- nios_log.log: the chronological file of every captured line -----------------
# Lines are real ones from NIOS-111696-oplog (vm-48-10, 2026-11-06), shortened.

def L(stamp, rest):
    prefix = "(2321081 /usr/bin/httpd) one/token_bundle.py:1 f():"
    return f"[2026/11/06 {stamp}] {prefix} {rest}"


BUNDLE = "ipc:bundle rid="
APPLY_REJECTED = L("21:35:53.524", BUNDLE + "da8bd471 user=admin fn=apply "
                   "outcome=rejected code=one.token_bundle.NOT_ENABLED dur=0.000s")
UP_STEP = L("21:36:25.684",
            BUNDLE + "52ee3558 step=ValidateTokens outcome=ok dur=20.705s")
UP_EVENT = L("21:36:25.684", BUNDLE + "52ee3558 event=staged total=999999999")
UP_SUMMARY = L("21:36:25.684",
               BUNDLE + "52ee3558 user=admin fn=upload outcome=ok dur=20.705s")
EN_STEP = L("21:36:26.012",
            BUNDLE + "0f498b0b step=InitTokenMigration outcome=ok dur=0.003s")
EN_EVENT = L("21:36:26.012",
             BUNDLE + "0f498b0b event=migration_queued overall_status=NOT_STARTED")
EN_SUMMARY = L("21:36:26.012",
               BUNDLE + "0f498b0b user=admin fn=enable outcome=ok dur=0.007s")
UP_REJECTED = L("21:37:18.642", BUNDLE + "456590f6 user=admin fn=upload "
                "outcome=rejected code=one.token_bundle.ALREADY_ENABLED dur=0.000s")
CONTINUATION = "  Traceback continuation without a timestamp"


def read_nios_log(t):
    with open(os.path.join(t.output_dir, "nios_log.log"), encoding="utf-8") as f:
        return f.read()


def flow(box, script, steps):
    def body(t):
        c = t.phase("1. connect", t.connect)
        t.steps(steps(t), probe=box.probe, needs=c,
                home=("home", "Licenses", box.to("Licenses")))
    return body


def capture(t, pattern="ipc:bundle "):
    with t.nios_log(pattern):
        pass


def test_nios_log_file_groups_by_section_sorted_with_states_and_results(
    monkeypatch, tmp_path,
):
    box = Box()
    script = [
        [APPLY_REJECTED],
        [EN_STEP, UP_STEP, UP_EVENT, CONTINUATION, UP_SUMMARY, EN_EVENT, EN_SUMMARY],
        [],
        [UP_REJECTED],
    ]
    monkeypatch.setattr(FakeApp, "script", script)

    def steps(t):
        return [
            ("2. apply rejected", "Licenses", "Licenses", lambda: capture(t)),
            ("3. enable", "Licenses", "IPc", lambda: (capture(t), box.to("IPc")())),
            ("4. quiet", "IPc", "IPc", lambda: capture(t)),
            ("5. no capture", "IPc", "IPc", lambda: None),
            ("6. upload rejected", "IPc", "IPc", lambda: capture(t)),
        ]

    t = make(tmp_path)
    code, _ = run_ticket(t, flow(box, script, steps))
    assert code == 0
    header = "== {} | {} | PASS 0.0s | {}"
    assert read_nios_log(t) == "\n\n".join([
        "\n".join([header.format("2. apply rejected", "Licenses -> Licenses", "1 line"),
                   APPLY_REJECTED]),
        "\n".join([header.format("3. enable", "Licenses -> IPc", "7 lines"),
                   UP_STEP, UP_EVENT, CONTINUATION, UP_SUMMARY,
                   EN_STEP, EN_EVENT, EN_SUMMARY]),
        header.format("4. quiet", "IPc -> IPc", "0 lines"),
        "\n".join([header.format("6. upload rejected", "IPc -> IPc", "1 line"),
                   UP_REJECTED]),
    ]) + "\n"
    assert not script


def test_header_without_state_and_failed_result(monkeypatch, tmp_path):
    monkeypatch.setattr(FakeApp, "script", [[UP_STEP]])

    def body(t):
        c = t.phase("1. connect", t.connect)

        def work():
            capture(t)
            raise ValueError("x")

        t.phase("2. work", work, needs=c)

    t = make(tmp_path)
    code, _ = run_ticket(t, body)
    assert code == 1
    assert read_nios_log(t) == f"== 2. work | FAIL 0.0s | 1 line\n{UP_STEP}\n"


def test_no_captures_means_no_file_summary_line_or_link(tmp_path):
    t = make(tmp_path)
    _, text = run_ticket(t, lambda t: t.phase("1. connect", t.connect))
    assert not os.path.exists(os.path.join(t.output_dir, "nios_log.log"))
    assert "NIOS log:" not in text
    assert "nios_log.log" not in read_html(t)


def test_file_is_written_on_ctrl_c_and_home_still_runs(monkeypatch, tmp_path):
    monkeypatch.setattr(FakeApp, "script", [[UP_STEP]])
    box = Box()

    def steps(t):
        def interrupted():
            capture(t)
            raise KeyboardInterrupt

        return [("2. up", "Licenses", "IPc", interrupted)]

    t = make(tmp_path)
    with pytest.raises(SystemExit), t:
        flow(box, None, steps)(t)
    assert read_nios_log(t) == (
        f"== 2. up | Licenses -> IPc | FAIL 0.0s | 1 line\n{UP_STEP}\n"
    )


def test_summary_line_console_print_and_html_link(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(FakeApp, "script", [[UP_STEP, UP_EVENT]])

    def body(t):
        c = t.phase("1. connect", t.connect)
        t.phase("2. log", lambda: capture(t), needs=c)

    t = make(tmp_path)
    _, text = run_ticket(t, body)
    path = os.path.join(t.output_dir, "nios_log.log")
    assert f"NIOS log: {path} (2 lines)" in text
    out = capsys.readouterr().out
    assert "nios_log.log\n" in out  # listed with the other files
    tail = out[out.index("RESULTS:"):]
    assert tail.index(f"NIOS log: {path} (2 lines)") < tail.index("== 2. log")
    assert tail.rstrip().endswith(UP_EVENT)
    page = read_html(t)
    assert '<a href="nios_log.log">nios_log.log</a> (2 lines)' in page
    with open(os.path.join(t.output_dir, "run.log")) as f:
        assert "== 2. log" in f.read()


def test_console_print_is_capped_with_a_pointer(monkeypatch, tmp_path, capsys):
    total = 130
    lines = [L("21:36:25.684", f"ipc:bundle rid={i:08x} fn=x outcome=ok dur=0.001s")
             for i in range(total)]
    monkeypatch.setattr(FakeApp, "script", [lines])

    def body(t):
        c = t.phase("1. connect", t.connect)
        t.phase("2. log", lambda: capture(t), needs=c)

    t = make(tmp_path)
    run_ticket(t, body)
    out = capsys.readouterr().out
    printed = out[out.index("== 2. log"):].splitlines()
    cap = 100
    assert printed[:cap] == read_nios_log(t).splitlines()[:cap]
    assert printed[cap] == (
        f"... {total + 1 - cap} more, see {os.path.join(t.output_dir, 'nios_log.log')}"
    )
    assert len(read_nios_log(t).splitlines()) == total + 1


def test_write_failure_warns_and_does_not_break_the_run(monkeypatch, tmp_path):
    monkeypatch.setattr(FakeApp, "script", [[UP_STEP]])

    def body(t):
        c = t.phase("1. connect", t.connect)
        t.phase("2. log", lambda: capture(t), needs=c)

    def bad(sections):
        raise RuntimeError("builder bug")

    monkeypatch.setattr(ticket_mod, "_nios_log_text", bad)
    t = make(tmp_path)
    code, text = run_ticket(t, body)
    assert code == 0
    assert "nios_log.log not written: RuntimeError: builder bug" in text
    assert os.path.exists(os.path.join(t.output_dir, "junit.xml"))
    assert "NIOS log:" not in text
