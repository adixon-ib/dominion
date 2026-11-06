"""Unit tests for report.html: Ticket writes it next to report.md from the same
section data. Reuses the fakes of test_ticket."""

import contextlib
import os
import re

from dominion.src import report_html
from dominion.unit_tests.test_oplog import REJECT
from dominion.unit_tests.test_ticket import (  # noqa: F401  (fake_lab is autouse)
    Box,
    FakeApp,
    boom,
    fake_lab,
    make,
    read_report,
    run_ticket,
)


def read_html(t):
    with open(os.path.join(t.output_dir, "report.html")) as f:
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
