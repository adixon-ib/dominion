#!/usr/bin/env python3
"""Run WAPI unit tests in a running NCC container, using the nios_test library.

Assumes WAPI files are already deployed (via ncc_wapi_deploy.py). Runs the
requested test classes via run_py_tests and saves results.

Usage:
    python3 ncc_wapi_token_ut.py -c balerion -w /blox/adixon1/my-worktree ipc_sync_status
    python3 ncc_wapi_token_ut.py -c balerion -w /blox/adixon1/my-worktree member_ipc ipc_overage
    python3 ncc_wapi_token_ut.py -c balerion ipc_sync_status
"""

from __future__ import annotations

import argparse
import datetime
import os
import re
import subprocess
import sys
import time

from _common import container_from_args

from nios_test import LocalNiosContainer

DEFAULT_RESULTS_ROOT = "/blox/adixon1/ncc-results"
SUMMARY_LOG = os.path.join(DEFAULT_RESULTS_ROOT, "summary.log")
TEST_CLASSES = {
    "member_ipc": "infoblox.test.one.wapi.test_member_token.WapiMemberIpc215",
    "ipc_alert_threshold": "infoblox.test.one.wapi.test_token_alert_threshold.WapiIpcAlertThreshold215",
    "ipc_gridwide": "infoblox.test.one.wapi.test_token_gridwide.WapiIpcGridWide215",
    "ipc_overage": "infoblox.test.one.wapi.test_token_overage.WapiIpcOverage215",
    "grid_ipc_pool": "infoblox.test.one.wapi.test_grid_token_pool.WapiGridIpcPool215",
    "ipc_audit_log": "infoblox.test.one.wapi.test_token_audit_log.WapiIpcAuditLog215",
    "ipc_sync_status": "infoblox.test.one.wapi.test_token_sync_status.WapiIpcSyncStatus215",
}
TEST_RUN_TIMEOUT = 300  # run_py_tests can run well past the transport's 30s default


def resolve_ticket(worktree: str | None) -> str:
    """Extract ticket name from branch name, or return 'MANUAL' if not found."""
    if not worktree:
        return "MANUAL"
    try:
        out = subprocess.run(
            ["git", "-C", worktree, "rev-parse", "--abbrev-ref", "HEAD"],
            capture_output=True, text=True, timeout=10,
        ).stdout.strip()
    except Exception:
        return "MANUAL"
    branch = out.rsplit("/", 1)[-1]
    m = re.match(r"([A-Za-z]+-\d+)", branch)
    return m.group(1).upper() if m else "MANUAL"


def resolve_run_dir(worktree: str | None, run_dir: str | None) -> str:
    """Resolve results directory path, using ticket/date/time if not provided."""
    if run_dir:
        return run_dir
    ticket = resolve_ticket(worktree)
    now = datetime.datetime.now()
    hour12 = now.hour % 12 or 12
    ampm = "am" if now.hour < 12 else "pm"
    stamp = f"{hour12}{now.minute:02d}{ampm}"
    date_part = f"{now.strftime('%B').lower()}-{now.day}"
    return os.path.join(DEFAULT_RESULTS_ROOT, f"{ticket}-{date_part}-{stamp}")


def _run_single_test(container: LocalNiosContainer, name: str, run_dir: str) -> tuple[str, str, str] | None:
    """Run a single test and return (name, result, log_path) or None if test not found."""
    cls = TEST_CLASSES.get(name)
    if not cls:
        print(f"Unknown test: {name} (available: {', '.join(TEST_CLASSES)})",
              file=sys.stderr)
        return None

    print(f"=== {name} ===")
    print(f"Class: {cls}\n")
    # run_py_tests writes its verbose progress/results to stderr, not
    # stdout — merge streams in the command itself so nothing is lost.
    cmd = (
        f"/infoblox/tests/bin/run_py_tests --no-check-licenses -k wapi -v "
        f"-N {cls} 2>&1"
    )
    out = ""
    for attempt in range(1, 4):
        _, out, _ = container._transport.run(cmd, check=False)
        if "503: 0: Service Unavailable" not in out:
            break
        print(f"  (attempt {attempt}: WAPI still returning 503 post-restart, retrying...)")
        time.sleep(10)
    print(out)

    log_path = os.path.join(run_dir, f"{name}.log")
    with open(log_path, "w") as f:
        f.write(out)

    failed = any(s in out for s in ("FAILED", "import error", "0 suites ran"))
    result = "FAIL" if failed else "PASS"
    print(f"Result: {result}\nSaved: {log_path}\n")
    return (name, result, log_path)


def _write_summary_log(run_name: str, results: list[tuple[str, str, str]]) -> None:
    """Write test results to the summary log file."""
    os.makedirs(DEFAULT_RESULTS_ROOT, exist_ok=True)
    header = f"Run: {run_name}"
    existing = ""
    if os.path.exists(SUMMARY_LOG):
        with open(SUMMARY_LOG) as f:
            existing = f.read()
    with open(SUMMARY_LOG, "a") as f:
        if header not in existing:
            f.write(f"\n{'=' * 38}\n{header}\n{'=' * 38}\n")
        for name, result, log_path in results:
            f.write(f"  {name:<25} {result}  {log_path}\n")


def run_tests(container: LocalNiosContainer, test_names: list[str], run_dir: str) -> bool:
    os.makedirs(run_dir, exist_ok=True)
    run_name = os.path.basename(run_dir)
    results = []
    container._transport.timeout = TEST_RUN_TIMEOUT

    for name in test_names:
        result = _run_single_test(container, name, run_dir)
        if result:
            results.append(result)

    _write_summary_log(run_name, results)

    passed = sum(1 for _, r, _ in results if r == "PASS")
    failed = sum(1 for _, r, _ in results if r == "FAIL")
    print(f"=== Summary: {passed} passed, {failed} failed ===")
    print(f"Results: {run_dir}/")
    return failed == 0 and passed > 0


def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("-c", "--container", required=True,
                        help="NCC container name, e.g. balerion")
    parser.add_argument("-w", "--worktree", default=None,
                        help="Worktree root (used for ticket name resolution)")
    parser.add_argument("--chroot", default=None,
                        help="Chroot path (default: /local/91/chroots/<container>)")
    parser.add_argument("-d", "--run-dir", default=None,
                        help="Results directory (default: auto, per ticket/date/time)")
    parser.add_argument("tests", nargs="+",
                        help="Test name(s): " + ", ".join(TEST_CLASSES))
    args = parser.parse_args()

    container = container_from_args(args.container, args.worktree, args.chroot)
    run_dir = resolve_run_dir(args.worktree, args.run_dir)
    ok = run_tests(container, args.tests, run_dir)
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
