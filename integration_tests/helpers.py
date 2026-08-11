"""Shared config factories and up/down checks for nios_test integration tests.

For the integration_tests/ pytest suite itself, test execution/reporting (the
old test()/skip()/summary() harness) is gone — pytest + conftest.py
fixtures/markers replace it, and only the config factories and up/down checks
below are used.

The old test()/skip()/summary()/resolve_ip_from_argv() harness has moved to
nios_test/src/harness.py (exported from the top-level nios_test package) —
it's the general run harness for any standalone nios_test script, not just
scripts/tickets/*/test_*.py.
"""

import os
import subprocess
import time
from datetime import datetime

from nios_test import (
    ApplianceConfig,
    BuildConfig,
    Defaults,
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

_defaults = Defaults.from_json()

DEFAULT_HOSTNAME = _defaults.hostname
DEFAULT_IP = _defaults.ip
DEFAULT_CHROOT = _defaults.chroot
DEFAULT_WORKTREE = _defaults.worktree
DEFAULT_CONTAINER = _defaults.container
LOG_LEVEL = _defaults.log_level

NCC_DOWN_REASON = f"{DEFAULT_CONTAINER} slot is not running"
APPLIANCE_DOWN_REASON = f"{DEFAULT_HOSTNAME} is not reachable via SSH"

_RESULTS_BASE = os.environ.get(
    "NIOS_TEST_OUTPUT_DIR",
    _defaults.output_dir or "/blox/adixon1/scripts/nios_test/results",
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


def is_ncc_up(container_name=DEFAULT_CONTAINER) -> bool:
    return LocalDockerTransport(container_name=container_name).is_container_running()


def is_appliance_up(ip=None) -> bool:
    return ssh_reachable(ip or resolve_ip())


def disable_ssh_rate_limit(ip=None) -> None:
    """Flush the appliance's SSH_RATE_LIMIT iptables chain. Each appliance
    integration test opens its own fresh SSH connection (shell_session()/
    console_session() don't reuse one), and a full run can exceed the
    default 10-connections/60s limit, causing later tests to fail with a
    silent connection timeout rather than a real error. Mirrors the
    nios_fix_ssh shell function."""
    ip = ip or resolve_ip()
    subprocess.run(
        [
            "ssh", "-o", "StrictHostKeyChecking=no", f"root@{ip}",
            "docker exec nios bash -c "
            "'iptables -F SSH_RATE_LIMIT && "
            "iptables -I SSH_RATE_LIMIT -j RETURN && "
            "touch /infoblox/var/debug_ssh_enabled'",
        ],
        capture_output=True, timeout=15,
    )


def appliance_config(log_level=LOG_LEVEL):
    return ApplianceConfig(
        hostname=DEFAULT_HOSTNAME,
        ip=resolve_ip(),
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
