"""test()/skip()/summary() run harness for standalone nios_test scripts.

For any script that drives itself directly via `python3 script.py` (ticket
tests under scripts/tickets/*/test_*.py, one-off repro scripts, etc.) rather
than through the integration_tests/ pytest suite. Exported from the
top-level nios_test package. Use `from nios_test import test, skip, summary`.

Do not use this module from anything under integration_tests/ itself.
pytest already has its own reporting (--junit-xml), and
resolve_ip_from_argv() reads sys.argv, which under a pytest run is pytest's
own argv (file paths, -m expressions), not an appliance IP.
"""

import os
import sys
import time
import traceback
from datetime import datetime

from .config import Defaults

_defaults = Defaults.from_json()
DEFAULT_IP = _defaults.ip

_RESULTS_BASE = os.environ.get(
    "NIOS_TEST_OUTPUT_DIR",
    _defaults.output_dir or "/blox/adixon1/scripts/nios_test/results",
)
OUTPUT_DIR: str = os.path.join(
    _RESULTS_BASE, datetime.now().strftime("%Y-%m-%d_%H-%M-%S"),
)


def resolve_ip_from_argv():
    """Resolve the target IP for a standalone script's CLI invocation.

    Returns:
        The first non-flag command-line argument if one was given, else the
        NIOS_IP environment variable if set, else DEFAULT_IP from config.json.
    """
    for arg in sys.argv[1:]:
        if not arg.startswith("-"):
            return arg
    return os.environ.get("NIOS_IP", DEFAULT_IP)


class _Counters:
    passed = 0
    failed = 0
    skipped = 0


_COUNTS = _Counters()
_RUN_SCRIPT = os.path.basename(sys.argv[0])
_RUN_RESULTS: list[dict] = []


def test(name, fn):
    """Run fn as one named test step, recording pass/fail for the summary.

    Args:
        fn: Zero-argument callable. Any exception it raises is caught and
            recorded as a failure rather than propagated.

    Returns:
        fn's return value, or None if fn raised.
    """
    print(f"\n{'=' * 60}")
    print(f"TEST: {name}")
    print(f"{'=' * 60}")
    start = time.monotonic()
    error = None
    result = None
    try:
        result = fn()
        _COUNTS.passed += 1
        print("  PASS")
    except Exception:
        _COUNTS.failed += 1
        error = traceback.format_exc()
        traceback.print_exc()
        print("  FAIL")
    duration = time.monotonic() - start
    _RUN_RESULTS.append({
        "status": "FAIL" if error else "PASS", "name": name,
        "duration": duration, "error": error,
    })
    return result


def skip(name, reason=""):
    """Record a named test step as skipped, without running it.

    Args:
        reason: Optional explanation, printed and included in the summary.
    """
    print(f"\n{'=' * 60}")
    print(f"SKIP: {name}{f' — {reason}' if reason else ''}")
    print(f"{'=' * 60}")
    _COUNTS.skipped += 1
    _RUN_RESULTS.append({
        "status": "SKIP", "name": name, "duration": 0.0,
        "error": None, "reason": reason,
    })


def summary(app):
    """Print totals, write summary.txt and junit.xml, then exit the process.

    Terminates the script with sys.exit(): 1 if any test failed, else 0.

    Args:
        app: The appliance/container object the run tested against. Used to
            label the report target via str(app).

    Raises:
        TypeError: If app is None.
    """
    if app is None:
        raise TypeError(
            "summary() requires the appliance/container object the run "
            "tested against, to label the report target correctly"
        )
    total = _COUNTS.passed + _COUNTS.failed + _COUNTS.skipped
    print(f"\n{'=' * 60}")
    print(
        f"RESULTS: {_COUNTS.passed} passed, {_COUNTS.failed} failed, "
        f"{_COUNTS.skipped} skipped out of {total}"
    )
    print(f"{'=' * 60}")
    if OUTPUT_DIR:
        _write_junit(app)
        files = sorted(os.listdir(OUTPUT_DIR)) if os.path.isdir(OUTPUT_DIR) else []
        if "summary.txt" not in files:
            files.append("summary.txt")
            files.sort()
        _write_summary(app, files)
        print(f"Logs: {OUTPUT_DIR}/")
        for name in files:
            print(f"  {name}")
    sys.exit(1 if _COUNTS.failed else 0)


def _target(app) -> str:
    """Label for app in reports. Each app type formats itself via __str__."""
    return str(app)


def _write_summary(app, files):
    """Write OUTPUT_DIR/summary.txt with the run's target, steps, and totals.

    Args:
        files: Full listing of OUTPUT_DIR, including summary.txt's own name
            and any log files written during the run, so the file's final
            block matches what summary() prints to the console.
    """
    run_id = os.path.basename(OUTPUT_DIR)
    target = _target(app)
    lines = [
        f"Run:    {run_id}",
        f"Script: {_RUN_SCRIPT}",
        f"Target: {target}",
        "",
    ]

    for r in _RUN_RESULTS:
        duration_str = f"({r['duration']:.1f}s)"
        reason = (
            f"  — {r.get('reason', '')}"
            if r["status"] == "SKIP" and r.get("reason") else ""
        )
        lines.append(
            f"{r['status']:<5} {r['name']:<45} {duration_str}{reason}"
        )
        if r["error"]:
            for err_line in r["error"].rstrip().splitlines():
                lines.append(f"      {err_line}")

    total = _COUNTS.passed + _COUNTS.failed + _COUNTS.skipped
    lines += [
        "",
        f"{_COUNTS.passed} passed, {_COUNTS.failed} failed, "
        f"{_COUNTS.skipped} skipped out of {total}",
        f"Logs: {OUTPUT_DIR}/",
        *[f"  {name}" for name in files],
    ]

    summary_path = os.path.join(OUTPUT_DIR, "summary.txt")
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    with open(summary_path, "w") as f:
        f.write("\n".join(lines) + "\n")


def _write_junit(app):
    """Write OUTPUT_DIR/junit.xml in the same shape pytest's own --junit-xml
    produces, so dominion/reckoning/verdict (which only read junit.xml, not
    summary.txt) pick up ticket-test runs too.

    Args:
        app: The appliance/container object the run tested against. Used to
            label the report target via str(app).
    """
    import socket
    import xml.etree.ElementTree as ET

    target = _target(app)

    total_time = sum(r["duration"] for r in _RUN_RESULTS)
    testsuites = ET.Element("testsuites", {"name": "nios_test ticket tests"})
    testsuite = ET.SubElement(testsuites, "testsuite", {
        "name": _RUN_SCRIPT,
        "errors": "0",
        "failures": str(_COUNTS.failed),
        "skipped": str(_COUNTS.skipped),
        "tests": str(_COUNTS.passed + _COUNTS.failed + _COUNTS.skipped),
        "time": f"{total_time:.3f}",
        "timestamp": datetime.now().isoformat(),
        "hostname": socket.gethostname(),
    })

    properties = ET.SubElement(testsuite, "properties")
    ET.SubElement(properties, "property", {"name": "target", "value": target})
    ET.SubElement(properties, "property", {"name": "invocation", "value": _RUN_SCRIPT})

    classname = os.path.splitext(_RUN_SCRIPT)[0]
    for r in _RUN_RESULTS:
        case = ET.SubElement(testsuite, "testcase", {
            "classname": classname,
            "name": r["name"],
            "time": f"{r['duration']:.3f}",
        })
        if r["status"] == "FAIL":
            failure = ET.SubElement(case, "failure", {"message": "test failed"})
            failure.text = r["error"] or ""
        elif r["status"] == "SKIP":
            ET.SubElement(case, "skipped", {"message": r.get("reason", "")})

    junit_path = os.path.join(OUTPUT_DIR, "junit.xml")
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    ET.ElementTree(testsuites).write(junit_path, encoding="utf-8", xml_declaration=True)
