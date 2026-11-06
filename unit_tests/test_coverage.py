"""Unit tests for actions/coverage.py. Fakes only: the container is a recording
object, the git diff is a string, and any real process raises."""

import json
import os
import subprocess
import types

import pytest

import dominion.actions.coverage as cov
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


class FakeContainer:
    def __init__(self, coverage_json="{}", fail=None):
        self.cmds, self.events = [], []
        self.coverage_json = coverage_json
        self.fail = fail  # substring of a command that raises
        self.container_name = "fake"

    def sh(self, cmd, check=True, timeout=None, report_lines=None):
        self.cmds.append(cmd)
        self.events.append(("sh", cmd))
        if self.fail and self.fail in cmd:
            raise RuntimeError(f"{self.fail} failed")
        return 0, (self.coverage_json if cmd.startswith("cat /tmp/cov/cov") else ""), ""

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
    assert files[PYABS] == cov.FileCoverage(run=2, total=2, missing=[])
    assert files[WAPI] == cov.FileCoverage(run=0, total=2, missing=[95, 96])
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
        ("sh", "cat"),
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
    assert c.cmds[2] == "cat /tmp/cov/coverage.json"


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
    assert r.files[PYABS] == cov.FileCoverage(34, 100, list(range(35, 101)))
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
