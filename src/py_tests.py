"""Run NIOS unit tests (run_py_tests) in the container and parse the result.

The runner prints unittest output to stderr, so the command merges it into
stdout. Its exit code alone is not enough: a run whose class name does not
import prints "All tests passed." and exits 0, and a hung child ends a run with
fewer results than the "Ran N tests" count. PyTestResult.ok covers both.
"""

from __future__ import annotations

import re
import shlex
from dataclasses import dataclass, field

RUN_PY_TESTS = "/infoblox/tests/bin/run_py_tests"
OK_STATUSES = ("ok", "skipped", "expected failure")
REASON_MAX = 300

RESULT_RE = re.compile(
    r"^(test_\w+) \(([\w.]+)\)(?: \.\.\. |\n[^\n]*? \.\.\. )(\S+)(.*)$", re.M)
RAN_RE = re.compile(r"^Ran (\d+) tests? in .*$", re.M)
FAIL_BLOCK_RE = re.compile(
    r"^(?:FAIL|ERROR): (test_\w+) \(([\w.]+)\)\n-{70}\n(.*?)(?=^={70}$|^-{70}$)",
    re.M | re.S)
IMPORT_ERRORS_RE = re.compile(r"^\d+ import errors?:\n((?:[ \t]+\S.*\n?)+)", re.M)
VERDICT_PREFIXES = ("All tests passed", "OK", "FAILED")


@dataclass
class PyTest:
    id: str  # <module>.<Class>.<test_name>
    status: str  # ok, skipped, FAIL, ERROR, ...
    reason: str = ""  # skip reason, or the last line of a failure's traceback


@dataclass
class PyTestResult:
    ran: int | None  # sum of the "Ran N tests" lines, None if there was none
    tests: list[PyTest]
    counts: dict[str, int]  # status -> number of tests
    raw: str  # the full output
    rc: int
    import_errors: list[str] = field(default_factory=list)
    verdict: str = "no verdict line"
    log_path: str | None = None  # file with raw, when the container had an output dir

    @property
    def ok(self) -> bool:
        """rc 0, at least one test ran, nothing but ok, skipped or expected
        failure, no import error, and as many parsed results as the "Ran N tests"
        count."""
        return (
            self.rc == 0
            and bool(self.ran)
            and not self.import_errors
            and all(t.status in OK_STATUSES for t in self.tests)
            and len(self.tests) == self.ran
        )


def command(tests: str | list[str], keywords: str = "all") -> str:
    """The run_py_tests command line: -N once, then the test names, stderr merged.

    -N is the boolean --dontfork flag (ibtest.py getopt 'hHvqk:l:r:NCe:...'), the
    tests are positional. A repeated -N makes the second one a test name: "-N:
    Not a test module", an import error with exit code 0."""
    names = [tests] if isinstance(tests, str) else list(tests)
    if not names or not all(isinstance(n, str) and n for n in names):
        raise ValueError(f"tests must be a name or a list of names, got {tests!r}")
    q = shlex.quote
    return (
        f"{RUN_PY_TESTS} --no-check-licenses -k {q(keywords)} -v "
        + "-N "
        + " ".join(q(n) for n in names)
        + " 2>&1"
    )


def parse(output: str, rc: int = 0) -> PyTestResult:
    reasons = {}
    for name, cls, body in FAIL_BLOCK_RE.findall(output):
        lines = [ln.strip() for ln in body.splitlines() if ln.strip()]
        reasons[f"{cls}.{name}"] = (lines[-1] if lines else "")[:REASON_MAX]
    tests = []
    for name, cls, first, tail in RESULT_RE.findall(output):
        tid = f"{cls}.{name}"
        status, rest = first, tail
        word = re.match(r"\s*(failure|success)\b", rest)
        if status in ("expected", "unexpected") and word:
            status, rest = f"{status} {word.group(1)}", rest[word.end():]
        reason = rest.strip().strip("'\"") or reasons.get(tid, "")
        tests.append(PyTest(tid, status, reason))
    counts: dict[str, int] = {}
    for t in tests:
        counts[t.status] = counts.get(t.status, 0) + 1
    ran = RAN_RE.findall(output)
    m = IMPORT_ERRORS_RE.search(output)
    return PyTestResult(
        ran=sum(map(int, ran)) if ran else None,
        tests=tests,
        counts=counts,
        raw=output,
        rc=rc,
        import_errors=[ln.strip() for ln in m.group(1).splitlines()] if m else [],
        verdict=next((ln for ln in output.splitlines()
                      if ln.startswith(VERDICT_PREFIXES)), "no verdict line"),
    )
