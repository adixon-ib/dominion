"""Shared config factories and up/down checks for nios_test integration tests.

For the integration_tests/ pytest suite itself, test execution/reporting (the
old test()/skip()/summary() harness) is gone — pytest + conftest.py
fixtures/markers replace it, and only the config factories and up/down checks
below are used.

test()/skip()/summary()/resolve_ip_from_argv() are kept here only for
scripts/tickets/*/test_*.py — standalone scripts (not part of the pytest
suite, run directly via `python3`) that predate this split and still use the
old harness directly. Do not use them from anything under integration_tests/
itself: resolve_ip_from_argv() reads sys.argv, which under a pytest run is
pytest's own argv (file paths, -m expressions), not an appliance IP.
"""

import os
import sys
import time
import traceback
from datetime import datetime

from nios_test import (
    ApplianceConfig,
    BuildConfig,
    ContainerConfig,
    LocalApplianceConfig,
    LocalContainerConfig,
    LocalDockerTransport,
    LocalNiosContainer,
    ssh_reachable,
)
from nios_test.src.constants import (
    NIOS_CONSOLE_PASSWORD,
    NIOS_CONSOLE_USER,
    WAPI_VERSION,
)

DEFAULT_HOSTNAME = "vm-04-89"
DEFAULT_IP = "10.35.211.4"
DEFAULT_CHROOT = "/local/91/chroots/balerion"
DEFAULT_WORKTREE = "/blox/adixon1/AURORA-2295-wapi-ipc-audit-log"
DEFAULT_CONTAINER = "balerion"
LOG_LEVEL = "info"

NCC_DOWN_REASON = f"{DEFAULT_CONTAINER} slot is not running"
APPLIANCE_DOWN_REASON = f"{DEFAULT_HOSTNAME} is not reachable via SSH"

_RESULTS_BASE = os.environ.get(
    "NIOS_TEST_OUTPUT_DIR", "/blox/adixon1/scripts/nios_test/results",
)

# Computed eagerly at import time so standalone ticket-test scripts (which
# never trigger conftest.py) still get a real per-run directory. When this
# module is imported under pytest, conftest.py's pytest_configure()
# overwrites this with a fresh run dir shared with the junit-xml report —
# that reassignment always runs after this line, so it's safe either way.
OUTPUT_DIR: str = os.path.join(
    _RESULTS_BASE, datetime.now().strftime("%Y-%m-%d_%H-%M-%S"),
)


def resolve_ip():
    return os.environ.get("NIOS_IP", DEFAULT_IP)


def resolve_ip_from_argv():
    """For standalone ticket-test scripts only (`python3 script.py [IP]`).
    Do not call this from integration_tests/ itself — see module docstring."""
    for arg in sys.argv[1:]:
        if not arg.startswith("-"):
            return arg
    return os.environ.get("NIOS_IP", DEFAULT_IP)


def is_ncc_up(container_name=DEFAULT_CONTAINER) -> bool:
    return LocalDockerTransport(container_name=container_name).is_container_running()


def is_appliance_up(ip=None) -> bool:
    return ssh_reachable(ip or resolve_ip())


def appliance_config(log_level=LOG_LEVEL):
    return ApplianceConfig(
        hostname=DEFAULT_HOSTNAME,
        ip=resolve_ip(),
        chroot=DEFAULT_CHROOT,
        worktree=DEFAULT_WORKTREE,
        log_level=log_level,
        output_dir=OUTPUT_DIR,
    )


def container_config(log_level=LOG_LEVEL):
    return ContainerConfig(
        container_name="nios",
        host=resolve_ip(),
        chroot=DEFAULT_CHROOT,
        worktree=DEFAULT_WORKTREE,
        log_level=log_level,
        output_dir=OUTPUT_DIR,
    )


def local_appliance_config(log_level=LOG_LEVEL, ip=None):
    return LocalApplianceConfig(
        container_name=DEFAULT_CONTAINER,
        chroot=DEFAULT_CHROOT,
        worktree=DEFAULT_WORKTREE,
        ip=ip,
        log_level=log_level,
        output_dir=OUTPUT_DIR,
    )


def local_container_config(log_level=LOG_LEVEL):
    return LocalContainerConfig(
        container_name=DEFAULT_CONTAINER,
        chroot=DEFAULT_CHROOT,
        worktree=DEFAULT_WORKTREE,
        log_level=log_level,
        output_dir=OUTPUT_DIR,
    )


def build_config(log_level=LOG_LEVEL):
    return BuildConfig(
        worktree=DEFAULT_WORKTREE,
        slot=DEFAULT_CONTAINER,
        log_level=log_level,
        output_dir=OUTPUT_DIR,
    )


def get_ncc_ip(retries=10, interval=5) -> str | None:
    container = LocalNiosContainer.from_config(local_container_config())
    try:
        ip = container.get_container_ip()
    except Exception:
        return None
    print(f"\nPre-check: container IP is {ip}")
    for attempt in range(1, retries + 1):
        try:
            rc, _, _ = container._transport.run(
                f"curl -sk --max-time 5 -u {NIOS_CONSOLE_USER}:{NIOS_CONSOLE_PASSWORD} "
                f"https://localhost/wapi/{WAPI_VERSION}/grid",
                check=False,
            )
            if rc == 0:
                print("  WAPI is up — skipping set_network")
                return ip
        except Exception:
            pass
        if attempt < retries:
            print(f"  Attempt {attempt}/{retries} failed — sleeping {interval}s")
            time.sleep(interval)
    print(f"  WAPI did not respond after {retries} attempts")
    return None


# -- Legacy harness, for scripts/tickets/*/test_*.py only ----------------------
# Not used anywhere under integration_tests/ itself — see module docstring.

class _Counters:
    passed = 0
    failed = 0
    skipped = 0


_COUNTS = _Counters()
_RUN_SCRIPT = os.path.basename(sys.argv[0])
_RUN_RESULTS: list[dict] = []


def test(name, fn):
    print(f"\n{'=' * 60}")
    print(f"TEST: {name}")
    print(f"{'=' * 60}")
    start = time.monotonic()
    error = None
    try:
        fn()
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


def skip(name, reason=""):
    print(f"\n{'=' * 60}")
    print(f"SKIP: {name}{f' — {reason}' if reason else ''}")
    print(f"{'=' * 60}")
    _COUNTS.skipped += 1
    _RUN_RESULTS.append({
        "status": "SKIP", "name": name, "duration": 0.0,
        "error": None, "reason": reason,
    })


def summary(app=None):
    total = _COUNTS.passed + _COUNTS.failed + _COUNTS.skipped
    print(f"\n{'=' * 60}")
    print(
        f"RESULTS: {_COUNTS.passed} passed, {_COUNTS.failed} failed, "
        f"{_COUNTS.skipped} skipped out of {total}"
    )
    print(f"{'=' * 60}")
    if OUTPUT_DIR:
        print(f"Logs: {OUTPUT_DIR}/")
        _write_summary(app)
        _write_junit(app)
    sys.exit(1 if _COUNTS.failed else 0)


def _write_summary(app=None):
    run_id = os.path.basename(OUTPUT_DIR)
    if app is not None:
        target = f"{app.hostname} ({app.ip})"
    else:
        target = f"{DEFAULT_HOSTNAME} ({DEFAULT_IP})"
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
    ]

    summary_path = os.path.join(OUTPUT_DIR, "summary.txt")
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    with open(summary_path, "w") as f:
        f.write("\n".join(lines) + "\n")
    print(f"Summary: {summary_path}")


def _write_junit(app=None):
    """Emit results/<run>/junit.xml in the same shape pytest's own
    --junit-xml produces, so dominion/reckoning/verdict (which only read
    junit.xml, not summary.txt) pick up ticket-test runs too."""
    import socket
    import xml.etree.ElementTree as ET

    if app is not None:
        target = f"{app.hostname} ({app.ip})"
    else:
        target = f"{DEFAULT_HOSTNAME} ({DEFAULT_IP})"

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
    print(f"Report: {junit_path}")
