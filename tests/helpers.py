"""Shared test infrastructure for nios_test integration tests."""

import os
import sys
import time
import traceback
from datetime import datetime

SCRIPTS_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, SCRIPTS_DIR)

from nios_test import ApplianceConfig, BuildConfig, ContainerConfig, LocalApplianceConfig, LocalContainerConfig, LocalDockerTransport, LocalNiosContainer, ssh_reachable
from nios_test.constants import NIOS_CONSOLE_USER, NIOS_CONSOLE_PASSWORD, WAPI_VERSION

PASS = 0
FAIL = 0
SKIP = 0

_RUN_SCRIPT = os.path.basename(sys.argv[0])
_RUN_RESULTS: list[dict] = []

DEFAULT_HOSTNAME = "vm-04-89"
DEFAULT_IP = "10.35.211.4"
DEFAULT_CHROOT = "/local/91/chroots/balerion"
DEFAULT_WORKTREE = "/blox/adixon1/AURORA-2295-wapi-ipc-audit-log"
DEFAULT_CONTAINER = "balerion"
LOG_LEVEL = "info"

NCC_DOWN_REASON = f"{DEFAULT_CONTAINER} slot is not running"
APPLIANCE_DOWN_REASON = f"{DEFAULT_HOSTNAME} is not reachable via SSH"

_RESULTS_BASE = os.environ.get("NIOS_TEST_OUTPUT_DIR", "/blox/adixon1/scripts/nios_test/results")
OUTPUT_DIR = os.path.join(_RESULTS_BASE, datetime.now().strftime("%Y-%m-%d_%H-%M-%S"))


def get_target():
    return os.environ.get("NIOS_TEST_TARGET", "all")


def should_run_appliance():
    t = get_target()
    return t in ("all", "appliance")


def should_run_ncc():
    t = get_target()
    return t in ("all", "ncc")


def resolve_ip():
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


def test(name, fn):
    global PASS, FAIL
    print(f"\n{'=' * 60}")
    print(f"TEST: {name}")
    print(f"{'=' * 60}")
    start = time.monotonic()
    error = None
    try:
        fn()
        PASS += 1
        print(f"  PASS")
    except Exception:
        FAIL += 1
        error = traceback.format_exc()
        traceback.print_exc()
        print(f"  FAIL")
    duration = time.monotonic() - start
    _RUN_RESULTS.append({"status": "FAIL" if error else "PASS", "name": name, "duration": duration, "error": error})


def skip(name, reason=""):
    global SKIP
    print(f"\n{'=' * 60}")
    print(f"SKIP: {name}{f' — {reason}' if reason else ''}")
    print(f"{'=' * 60}")
    SKIP += 1
    _RUN_RESULTS.append({"status": "SKIP", "name": name, "duration": 0.0, "error": None, "reason": reason})


def summary(app=None):
    total = PASS + FAIL + SKIP
    print(f"\n{'=' * 60}")
    print(f"RESULTS: {PASS} passed, {FAIL} failed, {SKIP} skipped out of {total}")
    print(f"{'=' * 60}")
    if OUTPUT_DIR:
        print(f"Logs: {OUTPUT_DIR}/")
        _write_summary(app)
    sys.exit(1 if FAIL else 0)


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
        reason = f"  — {r.get('reason', '')}" if r["status"] == "SKIP" and r.get("reason") else ""
        lines.append(f"{r['status']:<5} {r['name']:<45} {duration_str}{reason}")
        if r["error"]:
            for err_line in r["error"].rstrip().splitlines():
                lines.append(f"      {err_line}")

    total = PASS + FAIL + SKIP
    lines += [
        "",
        f"{PASS} passed, {FAIL} failed, {SKIP} skipped out of {total}",
        f"Logs: {OUTPUT_DIR}/",
    ]

    summary_path = os.path.join(OUTPUT_DIR, "summary.txt")
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    with open(summary_path, "w") as f:
        f.write("\n".join(lines) + "\n")
    print(f"Summary: {summary_path}")
