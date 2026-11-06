"""Unit tests for actions/coverage.py. Fakes only: the container is a recording
object, the git diff is a string, and any real process raises."""

import base64
import io
import json
import os
import subprocess
import tarfile
import types

import pytest

import dominion.actions.coverage as cov
import dominion.actions.coverage_page as page_mod
import dominion.src.transport as transport_mod
from dominion.actions import CoverageResult, coverage_off, coverage_on


@pytest.fixture(autouse=True)
def no_real_processes(monkeypatch):
    """Nothing here may reach docker, ssh, git or a container."""
    def refuse(*a, **kw):
        raise AssertionError(f"real process spawned: {a}")

    fake = types.SimpleNamespace(run=refuse, TimeoutExpired=subprocess.TimeoutExpired)
    monkeypatch.setattr(transport_mod, "subprocess", fake)
    monkeypatch.setattr(cov, "subprocess", fake)
    monkeypatch.setattr(cov.time, "sleep", lambda s: None)


PYABS_C = "/infoblox/one/lib/python/infoblox/one/token_bundle.py"
WAPI_C = "/infoblox/one/lib/python/infoblox/one/wapi/token_bundle.py"
PYABS_PAGE = "d_1111111111111111_token_bundle_py.html"  # coverage's hashed names
WAPI_PAGE = "d_2222222222222222_token_bundle_py.html"
INDEX = (
    '<table><tr class="file">'
    f'<td class="name left"><a href="{PYABS_PAGE}">{PYABS_C}</a></td></tr>'
    '<tr class="file">'
    f'<td class="name left"><a href="{WAPI_PAGE}">{WAPI_C}</a></td></tr></table>'
)


def html_tar(files=None):
    """Base64 tar.gz of coverage's html folder, as `tar | base64` prints it."""
    files = files or {"index.html": INDEX, PYABS_PAGE: "<p>pyabs</p>",
                      WAPI_PAGE: "<p>wapi</p>", "style.css": "body{}"}
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        for name, text in files.items():
            data = text.encode()
            info = tarfile.TarInfo(name)
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))
    return base64.encodebytes(buf.getvalue()).decode()


class FakeContainer:
    def __init__(self, coverage_json="{}", fail=None, html=None):
        self.cmds, self.events = [], []
        self.coverage_json = coverage_json
        self.html = html_tar() if html is None else html
        self.fail = fail  # substring of a command that raises
        self.container_name = "fake"

    def sh(self, cmd, check=True, timeout=None, report_lines=None):
        self.cmds.append(cmd)
        self.events.append(("sh", cmd))
        if self.fail and self.fail in cmd:
            raise RuntimeError(f"{self.fail} failed")
        if cmd.startswith("cat /tmp/cov/cov"):
            return 0, self.coverage_json, ""
        if cmd.startswith("tar -C /tmp/cov/html"):
            return 0, self.html, ""
        return 0, "", ""

    def stop_admin_httpd(self):
        self.events.append(("stop",))

    def start_admin_httpd(self):
        self.events.append(("start",))

    def wait_ready(self):
        self.events.append(("ready",))


# -- coverage_on: the files and the restart --------------------------------------

def test_coverage_on_writes_rc_hook_and_pth_with_their_paths_and_contents():
    c = FakeContainer()
    coverage_on(c, [
        "*/infoblox/one/token_bundle.py", "*/infoblox/one/wapi/token_bundle.py",
    ])
    assert c.cmds[0] == "mkdir -p /tmp/cov"
    rc = (
        "cat > /tmp/cov/.coveragerc <<'DOMINION_EOF'\n"
        "[run]\nparallel = True\ndata_file = /tmp/cov/.coverage\n"
        "include = */infoblox/one/token_bundle.py,*/infoblox/one/wapi/token_bundle.py\n"
        "DOMINION_EOF"
    )
    assert rc in c.cmds
    pth = (
        "cat > /usr/lib/python3/dist-packages/zz_dominion_cov.pth <<'DOMINION_EOF'\n"
        'import sys; sys.path.insert(0, "/tmp/cov"); import dominion_cov_hook\n'
        "DOMINION_EOF"
    )
    assert pth in c.cmds
    hook = next(
        x for x in c.cmds if x.startswith("cat > /tmp/cov/dominion_cov_hook.py")
    )
    for line in (
        'os.environ.setdefault("COVERAGE_PROCESS_START", "/tmp/cov/.coveragerc")',
        "cov = coverage.process_startup()",
        "if cov:",
        "time.sleep(2)",
        "cov.save()",
        "threading.Thread(target=_save, daemon=True).start()",
    ):
        assert line in hook
    order = [hook.index(k) for k in ("setdefault", "process_startup", "if cov:")]
    assert order == sorted(order)
    assert len(c.cmds) == N_INSTALL_CMDS  # mkdir and the three files
    assert "/usr/local" not in " ".join(c.cmds)


def test_hook_source_is_valid_python():
    compile(cov.HOOK, "hook", "exec")


def test_coverage_on_restarts_admin_httpd_then_waits_ready():
    c = FakeContainer()
    coverage_on(c, ["*/x.py"])
    assert c.events[-3:] == [("stop",), ("start",), ("ready",)]
    assert [e for e in c.events if e[0] == "sh"][-1][1].startswith("cat > /usr/lib")


def test_coverage_on_needs_an_include_pattern():
    with pytest.raises(ValueError, match="include"):
        coverage_on(FakeContainer(), [])


def test_target_can_be_an_appliance_with_a_container():
    c = FakeContainer()
    coverage_on(types.SimpleNamespace(container=c), ["*/x.py"])
    assert c.cmds


# -- diff parsing ----------------------------------------------------------------

DIFF = """\
diff --git a/products/one/server/src/pyabs/token_bundle.py b/products/one/server/src/pyabs/token_bundle.py
index 111..222 100644
--- a/products/one/server/src/pyabs/token_bundle.py
+++ b/products/one/server/src/pyabs/token_bundle.py
@@ -10,0 +11,2 @@ def f():
+a
+b
@@ -20,3 +22,1 @@ def g():
-x
-y
-z
+changed
@@ -30,2 +30,0 @@ def h():
-gone1
-gone2
@@ -40 +40 @@ def i():
-old
+new
diff --git a/products/one/server/src/pyutil/oplog.py b/products/one/server/src/pyutil/oplog.py
new file mode 100644
--- /dev/null
+++ b/products/one/server/src/pyutil/oplog.py
@@ -0,0 +1,3 @@
+one
+two
+three
diff --git a/products/one/server/src/pyabs/old.py b/products/one/server/src/pyabs/old.py
deleted file mode 100644
--- a/products/one/server/src/pyabs/old.py
+++ /dev/null
@@ -1,4 +0,0 @@
-a
"""  # noqa: E501
N_INSTALL_CMDS = 4
N_DETAILS = 4
DELETED_AT = 30
PYABS = "products/one/server/src/pyabs/token_bundle.py"
OPLOG = "products/one/server/src/pyutil/oplog.py"
WAPI = "products/one/server/src/wapi/token_bundle.py"


def test_changed_lines_added_changed_deletion_and_new_file():
    got = cov.changed_lines(DIFF)
    assert got == {PYABS: [11, 12, 22, 40], OPLOG: [1, 2, 3]}


def test_pure_deletion_contributes_no_lines_and_deleted_file_is_absent():
    got = cov.changed_lines(DIFF)
    assert DELETED_AT not in got[PYABS]
    assert "products/one/server/src/pyabs/old.py" not in got


def test_changed_lines_empty_diff():
    assert cov.changed_lines("") == {}


# -- path mapping ----------------------------------------------------------------

@pytest.mark.parametrize(("path", "want"), [
    (PYABS, "/infoblox/one/lib/python/infoblox/one/token_bundle.py"),
    (WAPI, "/infoblox/one/lib/python/infoblox/one/wapi/token_bundle.py"),
    (OPLOG, "/infoblox/one/lib/python/infoblox/one/oplog.py"),
    ("products/one/server/src/ibap/tokenobj.py",
     "/infoblox/one/lib/python/infoblox/one/ibap/tokenobj.py"),
    ("products/one/server/src/wapi/test/test_token_bundle.py", None),
    ("products/one/server/src/bin/util/x.py", None),
])
def test_container_path_by_directory_and_basename(path, want):
    assert cov.container_path(path) == want


# -- coverage.txt from a coverage 6.2 shaped json --------------------------------

def coverage_json():
    # pyabs/token_bundle.py: 34% of the changed lines ran, wapi: 88%
    def entry(executed, missing):
        return {"executed_lines": executed, "missing_lines": missing,
                "summary": {"percent_covered": 0}}
    return json.dumps({
        "meta": {"version": "6.2"},
        "files": {
            cov.container_path(PYABS): entry(list(range(1, 35)), list(range(35, 101))),
            cov.container_path(WAPI): entry(list(range(1, 89)), list(range(89, 101))),
        },
    })


def changed_for_report():
    return {
        PYABS: list(range(1, 101)),  # 100 executable lines, 34 ran
        WAPI: list(range(1, 101)),  # 100 executable lines, 88 ran
        OPLOG: [1, 2, 3],  # never measured
        "products/one/server/src/pyabs/README.md": [1],  # not python
    }


def test_intersect_counts_only_executable_changed_lines():
    measured = json.loads(coverage_json())["files"]
    changed = {PYABS: [1, 2, 200, 300], WAPI: [95, 96]}
    # 1 and 2 ran; 200 and 300 are not executable (comments, blanks): not counted
    files, nm = cov.intersect(changed, measured)
    assert files[PYABS] == cov.FileCoverage(run=2, total=2, missing=[], ran=[1, 2])
    assert files[WAPI] == cov.FileCoverage(run=0, total=2, missing=[95, 96], ran=[])
    assert nm == []


def test_report_text_with_the_measured_numbers(tmp_path):
    measured = json.loads(coverage_json())["files"]
    files, nm = cov.intersect(changed_for_report(), measured)
    for path in (PYABS, WAPI):
        full = tmp_path / path
        full.parent.mkdir(parents=True, exist_ok=True)
        full.write_text("".join(f"line {i}\n" for i in range(1, 101)))
    text = cov.report(files, nm, str(tmp_path))
    lines = text.splitlines()
    assert lines[0] == f"{PYABS}: changed lines run 34/100"
    assert lines[1] == "  35: line 35"
    assert lines[66] == "  100: line 100"
    assert lines[67] == f"{WAPI}: changed lines run 88/100"
    assert lines[68:80] == [f"  {n}: line {n}" for n in range(89, 101)]
    assert lines[-1] == f"{OPLOG}: not measured"
    assert "README.md" not in text
    assert text.endswith("\n")


def test_report_missing_source_file_keeps_the_line_numbers():
    fc = cov.FileCoverage(run=0, total=1, missing=[7])
    assert cov.report({"a.py": fc}, [], "/nonexistent") == (
        "a.py: changed lines run 0/1\n  7: \n"
    )


# -- coverage_off ----------------------------------------------------------------

@pytest.fixture
def run_off(tmp_path, monkeypatch):
    def run(container, diff=DIFF):
        monkeypatch.setattr(cov, "git_diff", lambda worktree, base: diff)
        return coverage_off(
            container, str(tmp_path / "out"), worktree=str(tmp_path / "wt"),
            base="BASE",
        )
    return run


def test_coverage_off_order_and_commands(run_off):
    c = FakeContainer(coverage_json())
    run_off(c)
    kinds = [e if e[0] != "sh" else ("sh", e[1].split()[0]) for e in c.events]
    assert kinds == [
        ("stop",),
        ("sh", "COVERAGE_RCFILE=/tmp/cov/.coveragerc"),
        ("sh", "COVERAGE_RCFILE=/tmp/cov/.coveragerc"),
        ("sh", "COVERAGE_RCFILE=/tmp/cov/.coveragerc"),
        ("sh", "cat"),
        ("sh", "tar"),
        ("sh", "mv"),
        ("start",),
        ("ready",),
    ]
    assert c.cmds[0] == (
        "COVERAGE_RCFILE=/tmp/cov/.coveragerc python3 -m coverage combine --keep"
    )
    assert c.cmds[1] == (
        "COVERAGE_RCFILE=/tmp/cov/.coveragerc python3 -m coverage json "
        "-o /tmp/cov/coverage.json"
    )
    assert c.cmds[2] == (
        "COVERAGE_RCFILE=/tmp/cov/.coveragerc python3 -m coverage html "
        "-d /tmp/cov/html"
    )
    assert c.cmds[3] == "cat /tmp/cov/coverage.json"
    assert c.cmds[4] == "tar -C /tmp/cov/html -czf - . | base64"


def test_disable_is_mv_never_rm(run_off):
    c = FakeContainer(coverage_json())
    run_off(c)
    assert "mv /usr/lib/python3/dist-packages/zz_dominion_cov.pth /tmp/cov/" in c.cmds
    assert not [x for x in c.cmds if "rm " in x]


def test_coverage_off_flushes_before_stopping(run_off, monkeypatch):
    seen = []
    monkeypatch.setattr(cov.time, "sleep", seen.append)
    c = FakeContainer(coverage_json())
    run_off(c)
    assert seen == [3]


def test_hook_is_removed_and_httpd_restarted_when_combine_fails(run_off, tmp_path):
    c = FakeContainer(coverage_json(), fail="coverage combine")
    with pytest.raises(RuntimeError, match="combine failed"):
        run_off(c)
    assert "mv /usr/lib/python3/dist-packages/zz_dominion_cov.pth /tmp/cov/" in c.cmds
    assert c.events[-2:] == [("start",), ("ready",)]
    assert not (tmp_path / "out" / "coverage.json").exists()


def test_coverage_off_writes_files_and_returns_the_result(run_off, tmp_path):
    wt = tmp_path / "wt"
    src = wt / PYABS
    src.parent.mkdir(parents=True)
    src.write_text("".join(f"pyabs line {i}\n" for i in range(1, 101)))
    diff = (
        f"+++ b/{PYABS}\n@@ -0,0 +1,100 @@\n" + f"+++ b/{OPLOG}\n@@ -0,0 +1,2 @@\n"
    )
    c = FakeContainer(coverage_json())
    r = run_off(c, diff)
    out = tmp_path / "out"
    assert isinstance(r, CoverageResult)
    assert r.files[PYABS] == cov.FileCoverage(
        34, 100, list(range(35, 101)), list(range(1, 35)),
    )
    assert r.not_measured == [OPLOG]
    assert r.coverage_json == str(out / "coverage.json")
    assert r.coverage_txt == str(out / "coverage.txt")
    assert r.changed_lines_json == str(out / "changed_lines.json")
    assert (out / "coverage.json").read_text() == coverage_json()
    assert json.loads((out / "changed_lines.json").read_text()) == {
        PYABS: list(range(1, 101)), OPLOG: [1, 2],
    }
    text = (out / "coverage.txt").read_text().splitlines()
    assert text[0] == f"{PYABS}: changed lines run 34/100"
    assert text[1] == "  35: pyabs line 35"
    assert text[-1] == f"{OPLOG}: not measured"
    assert os.path.isdir(out)


def test_git_diff_is_the_only_host_process_and_refused_here():
    with pytest.raises(AssertionError, match="real process spawned"):
        cov.git_diff("/wt", "BASE")


# -- hunks -----------------------------------------------------------------------

def test_hunks_context_clipped_to_the_file_start_and_end():
    assert page_mod.hunks([1], 100) == [(1, 4, [1])]
    assert page_mod.hunks([100], 100) == [(97, 100, [100])]
    assert page_mod.hunks([50], 100) == [(47, 53, [50])]
    assert page_mod.hunks([1], 2) == [(1, 2, [1])]


def test_hunks_merge_overlapping_and_adjacent_contexts_but_not_separate_ones():
    # 10 and 16: contexts 7-13 and 13-19 overlap; 17 and 24: 14-20 and 21-27 touch
    assert page_mod.hunks([10, 16], 100) == [(7, 19, [10, 16])]
    assert page_mod.hunks([17, 24], 100) == [(14, 27, [17, 24])]
    assert page_mod.hunks([10, 25], 100) == [(7, 13, [10]), (22, 28, [25])]
    assert page_mod.hunks([12, 11, 12, 10], 100) == [(7, 15, [10, 11, 12])]
    assert page_mod.hunks([], 100) == []


# -- coverage.html ---------------------------------------------------------------

def page(tmp_path, files, changed, source, not_measured=(), pages=None):  # noqa: PLR0913, PLR0917
    for path, lines in source.items():
        full = tmp_path / path
        full.parent.mkdir(parents=True, exist_ok=True)
        full.write_text("\n".join(lines) + "\n")
    return page_mod.render(
        files, changed, list(not_measured), base="origin/main",
        worktree=str(tmp_path), pages=pages or {},
    )


def numbered(n):
    return [f"src {i}" for i in range(1, n + 1)]


def rows(html_text):
    """(class, line number, text) of every code row in the page, in order."""
    import re
    return [(c, int(n), t) for c, n, t in re.findall(
        r'<tr class="(\w+)"><td class="n">(\d+)</td><td><pre>(.*?)</pre></td></tr>',
        html_text)]


def test_classes_run_miss_and_ctx(tmp_path):
    fc = cov.FileCoverage(run=1, total=2, missing=[11], ran=[10])
    out = page(tmp_path, {PYABS: fc}, {PYABS: [10, 11]}, {PYABS: numbered(30)})
    got = {n: c for c, n, _ in rows(out)}
    assert got[10] == "run"
    assert got[11] == "miss"
    assert got[7] == got[8] == got[9] == got[12] == got[13] == got[14] == "ctx"


def test_non_executable_changed_lines_are_ctx(tmp_path):
    fc = cov.FileCoverage(run=1, total=1, missing=[], ran=[10])
    out = page(tmp_path, {PYABS: fc}, {PYABS: [10, 11]}, {PYABS: numbered(30)})
    assert {n: c for c, n, _ in rows(out)}[11] == "ctx"


def test_source_text_is_escaped(tmp_path):
    src = numbered(10)
    src[4] = 'x = "<script>alert(1)</script>" & y'
    fc = cov.FileCoverage(run=1, total=1, missing=[], ran=[5])
    out = page(tmp_path, {PYABS: fc}, {PYABS: [5]}, {PYABS: src})
    assert "<script" not in out
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in out
    assert "&amp; y" in out


def test_details_hold_20_more_lines_each_side_without_repeating_any_line(tmp_path):
    fc = cov.FileCoverage(run=2, total=2, missing=[], ran=[50, 120])
    out = page(tmp_path, {PYABS: fc}, {PYABS: [50, 120]}, {PYABS: numbered(150)})
    shown = [n for _, n, _ in rows(out)]
    assert len(shown) == len(set(shown))  # no line twice on the whole page
    assert out.count("<details>") == N_DETAILS  # above and below, two hunks
    assert "<details open" not in out
    # first hunk 47-53: 20 above (27-46) and 20 below (54-73)
    assert shown[:7] == [27 + i for i in range(20)][:7]
    assert set(range(27, 74)) <= set(shown)
    assert "show 20 more lines above" in out
    assert "show 20 more lines below" in out
    # the second hunk's extra-above stops at 100..116, 117-123 hunk, 124-143 below
    assert set(range(97, 144)) <= set(shown)


def test_details_stop_at_the_file_edges_and_at_the_neighbour_hunk(tmp_path):
    fc = cov.FileCoverage(run=2, total=2, missing=[], ran=[5, 40])
    out = page(tmp_path, {PYABS: fc}, {PYABS: [5, 40]}, {PYABS: numbered(45)})
    shown = [n for _, n, _ in rows(out)]
    assert len(shown) == len(set(shown))
    assert sorted(shown) == list(range(1, 46))  # whole file, once
    assert "show 1 more lines above" in out  # line 1 before the 2-8 hunk
    assert "show 20 more lines below" in out  # 9-28 after it
    assert "show 8 more lines above" in out  # 29-36 before the 37-43 hunk
    assert "show 2 more lines below" in out  # 44-45 after it


def test_hunk_header_and_file_header_link_to_coveragepage_anchor(tmp_path):
    fc = cov.FileCoverage(run=1, total=2, missing=[40], ran=[10])
    href = f"coverage_html/{PYABS_PAGE}"
    out = page(tmp_path, {PYABS: fc}, {PYABS: [10, 40]}, {PYABS: numbered(60)},
               pages={PYABS: href})
    assert f'<a href="{href}">coverage page</a>' in out
    assert f'<a class="hh" href="{href}#t10">{PYABS}:10</a>' in out
    assert f'<a class="hh" href="{href}#t40">{PYABS}:40</a>' in out


def test_merged_hunk_header_shows_the_range_and_links_to_its_first_line(tmp_path):
    fc = cov.FileCoverage(run=2, total=2, missing=[], ran=[10, 13])
    out = page(tmp_path, {PYABS: fc}, {PYABS: [10, 13]}, {PYABS: numbered(60)},
               pages={PYABS: "coverage_html/p.html"})
    assert 'href="coverage_html/p.html#t10">' + PYABS + ":10-13</a>" in out
    assert out.count('class="hh"') == 1


def test_no_link_when_coverage_has_no_page_for_the_file(tmp_path):
    fc = cov.FileCoverage(run=1, total=1, missing=[], ran=[10])
    out = page(tmp_path, {PYABS: fc}, {PYABS: [10]}, {PYABS: numbered(30)})
    assert "coverage page" not in out
    assert "href=" not in out


def test_totals_range_and_out_of_scope_list(tmp_path):
    files = {
        PYABS: cov.FileCoverage(run=34, total=100, missing=[35], ran=[10]),
        WAPI: cov.FileCoverage(run=88, total=100, missing=[], ran=[10]),
    }
    changed = {PYABS: [10, 35], WAPI: [10], OPLOG: [1], "products/x/y.c": [3],
               "docs/<b>.md": [1]}
    src = {PYABS: numbered(60), WAPI: numbered(60)}
    out = page(tmp_path, files, changed, src, not_measured=[OPLOG])
    assert "changed lines run 122/200 (61%)" in out
    assert "origin/main..HEAD" in out
    assert "changed lines run 34/100 (34%)" in out
    assert "changed lines run 88/100 (88%)" in out
    scope = out[out.index("out of scope"):out.index("</ul>")]
    assert f"<li>{OPLOG}</li>" in scope
    assert "<li>products/x/y.c</li>" in scope
    assert "<li>docs/&lt;b&gt;.md</li>" in scope
    assert PYABS not in scope


def test_unreadable_source_is_noted_not_fatal(tmp_path):
    fc = cov.FileCoverage(run=1, total=1, missing=[], ran=[1])
    out = page(tmp_path, {PYABS: fc}, {PYABS: [1]}, {})
    assert "source not readable" in out


def test_page_is_self_contained_and_theme_aware(tmp_path):
    fc = cov.FileCoverage(run=1, total=1, missing=[], ran=[1])
    out = page(tmp_path, {PYABS: fc}, {PYABS: [1]}, {PYABS: numbered(5)})
    assert "prefers-color-scheme:dark" in out
    assert "<script" not in out
    assert "<link" not in out
    assert "src=" not in out


# -- coverage html copied out, page names, lookup, notes -------------------------

def test_coverage_off_runs_html_copies_it_out_and_links_the_real_page_names(
    run_off, tmp_path,
):
    wt = tmp_path / "wt"
    src = wt / PYABS
    src.parent.mkdir(parents=True)
    src.write_text("\n".join(numbered(120)) + "\n")
    diff = f"+++ b/{PYABS}\n@@ -0,0 +50,2 @@\n"
    c = FakeContainer(coverage_json())
    r = run_off(c, diff)
    out = tmp_path / "out"
    assert any(x.endswith("coverage html -d /tmp/cov/html") for x in c.cmds)
    assert r.coverage_html_dir == str(out / "coverage_html")
    assert (out / "coverage_html" / "index.html").read_text() == INDEX
    assert (out / "coverage_html" / PYABS_PAGE).read_text() == "<p>pyabs</p>"
    assert (out / "coverage_html" / "style.css").exists()
    assert r.coverage_html == str(out / "coverage.html")
    text = (out / "coverage.html").read_text()
    assert f'href="coverage_html/{PYABS_PAGE}#t50"' in text
    assert f'<a href="coverage_html/{PYABS_PAGE}">coverage page</a>' in text


def test_unpack_ignores_unsafe_members(tmp_path):
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        for name in ("../evil.txt", "/abs.txt", "ok.html"):
            info = tarfile.TarInfo(name)
            info.size = 1
            tar.addfile(info, io.BytesIO(b"x"))
    cov.unpack(base64.b64encode(buf.getvalue()).decode(), str(tmp_path / "d"))
    assert sorted(os.listdir(tmp_path / "d")) == ["ok.html"]
    assert not (tmp_path / "evil.txt").exists()


def test_html_pages_come_from_the_index_not_from_a_naming_scheme(tmp_path):
    d = tmp_path / "h"
    d.mkdir()
    (d / "index.html").write_text(INDEX)
    files = {PYABS: cov.FileCoverage(1, 1), WAPI: cov.FileCoverage(1, 1),
             OPLOG: cov.FileCoverage(1, 1)}
    assert cov.html_pages(str(d), files) == {
        PYABS: f"coverage_html/{PYABS_PAGE}", WAPI: f"coverage_html/{WAPI_PAGE}",
    }
    assert cov.html_pages(str(tmp_path / "missing"), files) == {}


@pytest.mark.parametrize("key", [
    "/infoblox/one/lib/python/infoblox/one/token_bundle.py",  # exact
    "/chroot/infoblox/one/lib/python/infoblox/one/token_bundle.py",  # longer
    "lib/python/infoblox/one/token_bundle.py",  # relative to a cwd
])
def test_lookup_matches_exact_longer_and_relative_keys(key):
    assert cov.lookup({key: "hit"}, PYABS_C) == "hit"


def test_lookup_does_not_match_a_different_file_or_the_wrong_directory():
    table = {"/infoblox/one/lib/python/infoblox/one/wapi/token_bundle.py": "w",
             "/x/other_token_bundle.py": "o"}
    assert cov.lookup(table, PYABS_C) is None
    assert cov.lookup(table, "") is None


def test_intersect_accepts_longer_keys():
    measured = {"/chroot" + cov.container_path(PYABS): {
        "executed_lines": [1], "missing_lines": [2]}}
    files, nm = cov.intersect({PYABS: [1, 2]}, measured)
    assert files[PYABS].run == 1
    assert nm == []


def test_coverage_txt_notes_when_no_measured_file_matched():
    keys = ["/a/x.py", "/a/y.py", "/a/z.py", "/a/w.py"]
    text = cov.report({}, [PYABS, WAPI], "/wt", keys)
    assert text.splitlines()[0] == (
        "NOTE: no measured file matched the 2 changed .py file(s). coverage.json "
        "has 4 file(s), e.g.: /a/x.py, /a/y.py, /a/z.py"
    )
    assert f"{PYABS}: not measured" in text
    assert "NOTE" not in cov.report(
        {PYABS: cov.FileCoverage(1, 1)}, [WAPI], "/nonexistent", ["/a/x.py"],
    )


def test_coverage_txt_says_so_when_nothing_python_changed():
    assert cov.report({}, [], "/wt") == "no changed .py files\n"
