"""Unit tests for run_py_tests: the parser, the command line, ok semantics,
NiosContainer.run_py_tests, deploy_test_files, Checks.py_tests and
Ticket.connect_local. Fixtures are slices of real run_py_tests output from
NIOS-111696 on drogon (counts in the "Ran N tests" lines adjusted to the slice).
No docker, no network.
"""

import contextlib
import logging
import os
import types

import pytest

import dominion.src.ticket as ticket_mod
from dominion import (
    Checks,
    DeployError,
    LocalAppliance,
    LocalNiosContainer,
    NiosContainer,
    Ticket,
)
from dominion.src import py_tests
from dominion.src.py_tests import PyTest, PyTestResult

LOGGER = logging.getLogger("dominion.unit_test")
CLS = "infoblox.test.one.wapi.test_token_bundle.WapiIpcBundle215"
HEAD = """\
Restoring network settings for loopback interface
======================================================================
infoblox.test.one.wapi.test_token_bundle.WapiIpcBundle215
1 of 1
pid: 31830 time: Thu Nov  5 21:10:17 2026
keywords: base, token, wapi

NOTICE: Network configuration before test:

Before:
----------------------------------------------------------------------
    inet 127.0.0.1/8 scope host lo
----------------------------------------------------------------------
"""
PASSING = HEAD + f"""\
test_apply_cloud_connected ({CLS}) ... ok
test_apply_not_gm ({CLS}) ... ok
test_enable_expired_staged_bundle ({CLS}) ... skipped 'expiry is checked after the model check'
test_upload_valid_bundle ({CLS}) ... skipped 'IPC_TEST_BUNDLE not set'

======================================================================
SKIP: test_enable_expired_staged_bundle ({CLS})
----------------------------------------------------------------------
expiry is checked after the model check
======================================================================
SKIP: test_upload_valid_bundle ({CLS})
----------------------------------------------------------------------
IPC_TEST_BUNDLE not set
----------------------------------------------------------------------
Ran 4 tests in 26.296s

OK (skipped=2)
Skipped 2 test cases

Factory reset...
Wait for httpd... httpd failed to start (250.158s)

======================================================================
1 suite ran in 278.011s
1 suite had one or more disabled test case
All tests passed.
1 suite left garbage:
  {CLS}
"""  # noqa: E501
SCHEMA = """\
test_max_field_length (infoblox.test.admin_conn.test_wapi.TestWapiSchema) ... ok
test_out_of_sync (infoblox.test.admin_conn.test_wapi.TestWapiSchema)
Test WAPI schema of current schema version matches the one ... ok
test_overall_schema (infoblox.test.admin_conn.test_wapi.TestWapiSchema)
Test overall WAPI schema. ... ok

----------------------------------------------------------------------
Ran 3 tests in 126.087s

OK
"""
ERRORED = HEAD + f"""\
test_upload_uid_mismatch ({CLS}) ... ok
test_upload_insufficient_credits ({CLS}) ... skipped 'IPC_TEST_BUNDLE_LOW not set'
test_upload_valid_bundle ({CLS}) ... ERROR

======================================================================
ERROR: test_upload_valid_bundle ({CLS})
----------------------------------------------------------------------
@ 1792691995.244 (2026-10-22 17:59:55.244)
Traceback (most recent call last):
  File "/infoblox/common/lib/python/infoblox/test/wapiutil.py", line 259, in call
    raise map_http_status_to_wapifault(status)(
infoblox.test.wapiutil.WAPIFaultBadRequest: (WAPIFaultBadRequest) 400: 0: Bad Request
 Data: Error: IPc bundle contains a mismatching Grid UID

======================================================================
SKIP: test_upload_insufficient_credits ({CLS})
----------------------------------------------------------------------
IPC_TEST_BUNDLE_LOW not set
----------------------------------------------------------------------
Ran 3 tests in 3.741s

FAILED (errors=1, skipped=1)
Skipped 1 test case

======================================================================
1 suite ran in 5.202s
1 suites had failures or errors:
  {CLS}: errors=1
Garbage not found.
"""
FAILED = f"""\
test_a ({CLS}) ... FAIL

======================================================================
FAIL: test_a ({CLS})
----------------------------------------------------------------------
Traceback (most recent call last):
  File "x.py", line 1, in test_a
AssertionError: 400 != 200
----------------------------------------------------------------------
Ran 1 test in 0.1s

FAILED (failures=1)
"""
BAD_NAME = """\
#WapiIpcBundle215: Not a test module, class or test
Restoring network settings for loopback interface
======================================================================
0 suites ran in 0.000s
All tests passed.
1 import error:
  WapiIpcBundle215:Not a test module, class or test
Garbage not found.
"""
HUNG = f"""\
test_status ({CLS}) ... ERROR
test_upload_bad_tar ({CLS}) ... ERROR
test_upload_expired ({CLS}) ...
ERROR: Child process timed out after 300 seconds
Cleanup licenses...
Factory reset...
"""


def ids(result):
    return [(t.id.rsplit(".", 1)[-1], t.status, t.reason) for t in result.tests]


# -- parser ----------------------------------------------------------------------

def test_parse_passing_run_with_skips():
    r = py_tests.parse(PASSING)
    assert ids(r) == [
        ("test_apply_cloud_connected", "ok", ""),
        ("test_apply_not_gm", "ok", ""),
        ("test_enable_expired_staged_bundle", "skipped",
         "expiry is checked after the model check"),
        ("test_upload_valid_bundle", "skipped", "IPC_TEST_BUNDLE not set"),
    ]
    assert r.tests[0].id == f"{CLS}.test_apply_cloud_connected"
    assert r.ran == 4
    assert r.counts == {"ok": 2, "skipped": 2}
    assert r.verdict == "OK (skipped=2)"  # the first verdict line, as before
    assert r.import_errors == []
    assert r.raw == PASSING
    assert r.ok


def test_parse_docstring_style_result_lines():
    r = py_tests.parse(SCHEMA)
    assert ids(r) == [
        ("test_max_field_length", "ok", ""),
        ("test_out_of_sync", "ok", ""),
        ("test_overall_schema", "ok", ""),
    ]
    assert r.ran == 3
    assert r.verdict == "OK"
    assert r.ok


def test_parse_error_run_takes_the_reason_from_the_traceback():
    r = py_tests.parse(ERRORED, rc=1)
    assert ids(r)[2] == (
        "test_upload_valid_bundle", "ERROR",
        "Data: Error: IPc bundle contains a mismatching Grid UID",
    )
    assert r.counts == {"ok": 1, "skipped": 1, "ERROR": 1}
    assert r.verdict == "FAILED (errors=1, skipped=1)"
    assert r.ran == 3
    assert not r.ok


def test_parse_failure_reason():
    r = py_tests.parse(FAILED, rc=1)
    assert ids(r) == [("test_a", "FAIL", "AssertionError: 400 != 200")]
    assert r.ran == 1
    assert not r.ok


def test_parse_bad_name_is_an_import_error_even_though_rc_is_zero():
    r = py_tests.parse(BAD_NAME, rc=0)
    assert r.tests == []
    assert r.ran is None
    assert r.import_errors == ["WapiIpcBundle215:Not a test module, class or test"]
    assert r.verdict == "All tests passed."
    assert not r.ok


def test_parse_hung_child_has_fewer_results_than_ran_and_no_ran_line():
    r = py_tests.parse(HUNG, rc=1)
    assert [t.status for t in r.tests] == ["ERROR", "ERROR"]
    assert r.ran is None
    assert not r.ok


def test_ran_sums_over_every_class_in_one_invocation():
    r = py_tests.parse(SCHEMA + SCHEMA.replace("TestWapiSchema", "TestWapiSchemaV1"))
    assert r.ran == 6
    assert len(r.tests) == 6
    assert r.ok


def test_parse_empty_output():
    r = py_tests.parse("", rc=1)
    assert (r.ran, r.tests, r.counts, r.verdict) == (None, [], {}, "no verdict line")
    assert not r.ok


# -- ok semantics ----------------------------------------------------------------

def result(tests, ran, rc=0, import_errors=()):
    return PyTestResult(ran, tests, {}, "", rc, list(import_errors))


@pytest.mark.parametrize(("kwargs", "ok"), [
    (dict(tests=[PyTest("a", "ok"), PyTest("b", "skipped")], ran=2), True),
    (dict(tests=[PyTest("a", "ok")], ran=1, rc=1), False),
    (dict(tests=[PyTest("a", "ok")], ran=2), False),
    (dict(tests=[PyTest("a", "ok"), PyTest("b", "ok")], ran=1), False),
    (dict(tests=[PyTest("a", "FAIL")], ran=1), False),
    (dict(tests=[PyTest("a", "ERROR")], ran=1), False),
    (dict(tests=[PyTest("a", "unexpected")], ran=1), False),
    (dict(tests=[PyTest("a", "ok")], ran=1, import_errors=["x"]), False),
    (dict(tests=[], ran=None), False),
])
def test_ok_semantics(kwargs, ok):
    assert result(**kwargs).ok is ok


# -- command line ----------------------------------------------------------------

def test_command_has_no_check_licenses_keywords_each_n_and_merges_stderr():
    cmd = py_tests.command(["a.b.C", "a.b.D"], "wapi")
    assert cmd == (
        "/infoblox/tests/bin/run_py_tests --no-check-licenses -k wapi -v "
        "-N a.b.C -N a.b.D 2>&1"
    )
    assert "pytest" not in cmd


def test_command_str_default_keywords_and_quoting():
    assert py_tests.command("a.b.C") == (
        "/infoblox/tests/bin/run_py_tests --no-check-licenses -k all -v -N a.b.C 2>&1"
    )
    assert "-k 'a b'" in py_tests.command("x", "a b")
    assert "-N 'x; rm -rf /'" in py_tests.command("x; rm -rf /")


@pytest.mark.parametrize("bad", [[], "", [""], [None]])
def test_command_rejects_missing_test_names(bad):
    with pytest.raises(ValueError, match="tests must be"):
        py_tests.command(bad)


# -- NiosContainer.run_py_tests --------------------------------------------------

@pytest.fixture
def container(fake_transport_factory):
    def make(responses=None, **kw):
        base = {
            "[r]un_py_tests": (1, "", ""),  # the pgrep guard: nothing running
            "[a]dmin-httpd": (0, "", ""),
            "curl": (0, "{}", ""),
            "run_py_tests --no-check": (0, PASSING, ""),
        }
        base.update(responses or {})
        transport = fake_transport_factory(responses=base, **kw)
        transport.timeouts = []
        real = transport.run

        def run(cmd, check=True, **kwargs):
            transport.timeouts.append((cmd, kwargs.get("timeout")))
            return real(cmd, check=check)

        transport.run = run
        c = NiosContainer(
            container_name="nios", transport=transport, chroot="/c", logger=LOGGER,
        )
        transport.timeouts.clear()  # the constructor's iptables call
        return c, transport
    return make


def sh_cmds(transport):
    return [c for c, _ in transport.timeouts if c.startswith("sh -c")]


def test_run_py_tests_runs_the_command_with_timeout_and_parses(container):
    c, transport = container()
    r = c.run_py_tests([CLS, "x.y.Z"], keywords="wapi", timeout=77)
    assert r.ok
    assert r.ran == 4
    run = [(cmd, t) for cmd, t in transport.timeouts if "no-check-licenses" in cmd]
    assert len(run) == 1
    assert "-k wapi -v -N " + CLS + " -N x.y.Z 2>&1" in run[0][0]
    assert run[0][1] == 77


def test_run_py_tests_default_timeout_is_the_constant(container):
    from dominion.src.constants import PY_TESTS_TIMEOUT
    c, transport = container()
    c.run_py_tests(CLS)
    assert [t for cmd, t in transport.timeouts if "no-check-licenses" in cmd] == [
        PY_TESTS_TIMEOUT,
    ]


def test_run_py_tests_waits_for_admin_httpd_and_wapi_first(container):
    c, transport = container()
    c.run_py_tests(CLS)
    cmds = [cmd for cmd, _ in transport.timeouts]
    order = [next(i for i, x in enumerate(cmds) if key in x)
             for key in ("[r]un_py_tests", "[a]dmin-httpd", "curl", "no-check-lic")]
    assert order == sorted(order)


def test_run_py_tests_wait_false_skips_the_wait(container):
    c, transport = container()
    c.run_py_tests(CLS, wait=False)
    cmds = [cmd for cmd, _ in transport.timeouts]
    assert not [x for x in cmds if "[a]dmin" in x or "curl" in x]


def test_run_py_tests_refuses_while_another_run_is_going(container):
    c, transport = container({"[r]un_py_tests": (0, "123\n", "")})
    with pytest.raises(RuntimeError, match="already running in nios"):
        c.run_py_tests(CLS)
    assert not [cmd for cmd, _ in transport.timeouts if "no-check-licenses" in cmd]


def test_run_py_tests_nonzero_rc_does_not_raise(container):
    c, _ = container({"run_py_tests --no-check": (1, ERRORED, "")})
    r = c.run_py_tests(CLS)
    assert r.rc == 1
    assert not r.ok
    assert r.tests[2].status == "ERROR"


def test_run_py_tests_record_is_capped_but_result_is_complete(container):
    out = "".join(f"line{i}\n" for i in range(300))
    c, _ = container({"run_py_tests --no-check": (0, out, "")})
    records = []
    c.on_call = records.append
    r = c.run_py_tests(CLS, wait=False)
    rec = next(x for x in records if "no-check-licenses" in x["cmd"])
    assert rec["stdout"].splitlines()[0] == "... 100 more lines"
    assert rec["stdout"].splitlines()[1:] == [f"line{i}" for i in range(100, 300)]
    assert r.raw == out


def test_sh_timeout_is_passed_only_when_given(container):
    c, transport = container()
    c.sh("ls")
    c.sh("ls", timeout=5)
    assert [t for _, t in transport.timeouts] == [None, 5]


def test_wait_ready_fails_when_admin_httpd_never_starts(container):
    c, _ = container({"[a]dmin-httpd": (1, "", "")})
    with pytest.raises(RuntimeError, match="admin-httpd not running"):
        c.wait_ready()


def test_wait_ready_retries_wapi_until_it_answers_then_gives_up(
    container, monkeypatch,
):
    c, _ = container()
    calls = []

    def flaky(timeout):
        calls.append(timeout)
        if len(calls) < 3:
            raise RuntimeError("not yet")

    monkeypatch.setattr(c, "_wait_for_wapi", flaky)
    c.wait_ready()
    assert len(calls) == 3

    def never(timeout):
        raise RuntimeError("never")

    monkeypatch.setattr(c, "_wait_for_wapi", never)
    with pytest.raises(RuntimeError, match="WAPI not answering after 0s"):
        c.wait_ready(timeout=0)


# -- deploy_test_files -----------------------------------------------------------

WAPI_TEST = "products/one/server/src/wapi/test/test_token_bundle.py"
PYUTIL_TEST = "products/one/server/src/pyutil/test/test_oplog.py"


@pytest.fixture
def worktree(tmp_path):
    for sub, makefile in (
        ("wapi/test", "PRODUCT_ROOT = ../../../..\nPY_TEST_SUBDIR\t= one/wapi\n"),
        ("pyutil/test", "PRODUCT_ROOT = ../../../..\nPY_TEST_SRC = test_oplog.py\n"),
    ):
        d = tmp_path / "products/one/server/src" / sub
        d.mkdir(parents=True)
        (d / "Makefile").write_text(makefile)
    (tmp_path / WAPI_TEST).write_text("# wapi test\n")
    common = tmp_path / "products/one/server/src/wapi/test/token_test_common.py"
    common.write_text("# c\n")
    (tmp_path / PYUTIL_TEST).write_text("# oplog test\n")
    return tmp_path


def test_deploy_test_files_maps_by_the_makefile_subdir(
    fake_transport, worktree,
):
    c = NiosContainer(container_name="nios", transport=fake_transport,
                      chroot="/c", worktree=str(worktree), logger=LOGGER)
    r = c.deploy_test_files([
        WAPI_TEST, "products/one/server/src/wapi/test/token_test_common.py",
        PYUTIL_TEST,
    ])
    base = "/infoblox/tests/lib/python/infoblox/test"
    assert [f["file"] for f in r.details["files"]] == [
        f"{base}/one/wapi/test_token_bundle.py",
        f"{base}/one/wapi/token_test_common.py",
        f"{base}/one/test_oplog.py",
    ]
    uploaded = fake_transport.uploaded_content
    assert uploaded[f"{base}/one/test_oplog.py"] == b"# oplog test\n"
    cmds = [x[1] for x in fake_transport.calls if x[0] == "run"]
    assert f"rm -f {base}/one/test_oplog.pyc {base}/one/test_oplog.pys" in cmds


def test_deploy_test_files_hash_mismatch_raises(fake_transport_factory, worktree):
    c = NiosContainer(
        container_name="nios", transport=fake_transport_factory(corrupt_uploads=True),
        chroot="/c", worktree=str(worktree), logger=LOGGER,
    )
    with pytest.raises(DeployError, match=r"Hash mismatch for test_oplog\.py"):
        c.deploy_test_files([PYUTIL_TEST])


@pytest.mark.parametrize(("sub", "match"), [
    ("products/one/server/src/wapi/test/nope.py", "test file not found"),
    ("products/lb/x/test/test_a.py", "only tests under products/one/"),
])
def test_deploy_test_files_refuses_what_it_cannot_map(
    fake_transport, worktree, sub, match,
):
    (worktree / "products/lb/x/test").mkdir(parents=True)
    (worktree / "products/lb/x/test/test_a.py").write_text("x")
    c = NiosContainer(container_name="nios", transport=fake_transport,
                      chroot="/c", worktree=str(worktree), logger=LOGGER)
    with pytest.raises(DeployError, match=match):
        c.deploy_test_files([sub])
    assert not fake_transport.uploaded_content


def test_deploy_test_files_without_a_makefile_raises(fake_transport, worktree):
    d = worktree / "products/one/server/src/other/test"
    d.mkdir(parents=True)
    (d / "test_x.py").write_text("x")
    c = NiosContainer(container_name="nios", transport=fake_transport,
                      chroot="/c", worktree=str(worktree), logger=LOGGER)
    with pytest.raises(DeployError, match="no Makefile"):
        c.deploy_test_files(["products/one/server/src/other/test/test_x.py"])


# -- Checks.py_tests -------------------------------------------------------------

def test_checks_py_tests_records_one_expect_per_test_plus_totals():
    sink = []
    c = Checks("t", sink)
    c.py_tests(py_tests.parse(ERRORED, rc=1))
    by = {e["label"]: e for e in sink}
    assert by[f"{CLS}.test_upload_uid_mismatch"]["ok"]
    assert by[f"{CLS}.test_upload_insufficient_credits"]["ok"]  # skipped counts ok
    bad = by[f"{CLS}.test_upload_valid_bundle"]
    assert not bad["ok"]
    assert bad["got"] == (
        "ERROR: Data: Error: IPc bundle contains a mismatching Grid UID"
    )
    assert by["Ran N tests equals parsed results"]["ok"]
    assert not by["run_py_tests exit code"]["ok"]
    assert by["import errors"]["ok"]
    assert len(c.failures) == 2


def test_checks_py_tests_ran_mismatch_and_import_errors_fail():
    c = Checks("t")
    c.py_tests(py_tests.parse(HUNG, rc=1))
    assert any(
        f.startswith("Ran N tests equals parsed results: expected None")
        for f in c.failures
    )
    c = Checks("t")
    c.py_tests(py_tests.parse(BAD_NAME, rc=0))
    assert [f.split(":")[0] for f in c.failures] == [
        "Ran N tests equals parsed results", "import errors",
    ]


def test_checks_py_tests_passes_a_clean_run():
    c = Checks("t")
    c.py_tests(py_tests.parse(PASSING))
    assert not c.failures


# -- Ticket.connect_local --------------------------------------------------------

def test_connect_local_builds_a_local_appliance_with_calls_in_the_report(
    monkeypatch, tmp_path, fake_transport,
):
    seen = {}

    def from_config(cls, cfg):
        seen["cfg"] = cfg
        c = LocalNiosContainer(
            container_name=cfg.container_name, transport=fake_transport,
            chroot=cfg.chroot, worktree=cfg.worktree, logger=LOGGER,
        )

        @contextlib.contextmanager
        def console_session(**kw):
            yield types.SimpleNamespace(run=lambda cmd: "Version : 9.1.0-99\n")

        c.console_session = console_session
        return c

    monkeypatch.setattr(LocalNiosContainer, "from_config", classmethod(from_config))
    t = Ticket("NIOS-1", None, results_dir=str(tmp_path), worktree="/wt",
               wapi_version="v2.15")
    out = {}

    def body(t):
        c = t.phase("1. connect", lambda: t.connect_local("drogon"))

        def work():
            t.container.sh("echo hi")
            out["app"] = t.app

        t.phase("2. work", work, needs=c)

    with pytest.raises(SystemExit) as e, t:
        body(t)
    assert e.value.code == 0
    assert isinstance(out["app"], LocalAppliance)
    assert out["app"].wapi_version == "v2.15"
    assert out["app"].hostname == "drogon"
    assert seen["cfg"].container_name == "drogon"
    assert seen["cfg"].worktree == "/wt"
    assert t.host == "drogon"
    assert t.build == "9.1.0-99"
    with open(os.path.join(t.output_dir, "report.md")) as f:
        report = f.read()
    assert "- Host: drogon" in report
    assert "$ echo hi  -> exit 0" in report


def test_connect_local_chroot_is_passed_when_given(
    monkeypatch, tmp_path, fake_transport,
):
    seen = {}

    def from_config(cls, cfg):
        seen["chroot"] = cfg.chroot
        return LocalNiosContainer(
            container_name=cfg.container_name, transport=fake_transport,
            chroot=cfg.chroot, logger=LOGGER,
        )

    monkeypatch.setattr(LocalNiosContainer, "from_config", classmethod(from_config))
    monkeypatch.setattr(Ticket, "_read_build", lambda self: None)
    t = Ticket("n", None, results_dir=str(tmp_path))
    t.connect_local("drogon", chroot="/local/chroot")
    assert seen["chroot"] == "/local/chroot"
    assert ticket_mod.LocalAppliance is LocalAppliance
