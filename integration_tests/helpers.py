"""Shared config factories and up/down checks for nios_test integration tests.

Test execution/reporting (the old test()/skip()/summary() harness) is gone —
pytest + conftest.py fixtures/markers replace it. This module now only holds
what pytest doesn't provide out of the box: NIOS-specific config factories and
appliance/NCC reachability checks.
"""

import os

from nios_test import ApplianceConfig, BuildConfig, ContainerConfig, LocalApplianceConfig, LocalContainerConfig, LocalDockerTransport, LocalNiosContainer, ssh_reachable
from nios_test.constants import NIOS_CONSOLE_USER, NIOS_CONSOLE_PASSWORD, WAPI_VERSION

DEFAULT_HOSTNAME = "vm-04-89"
DEFAULT_IP = "10.35.211.4"
DEFAULT_CHROOT = "/local/91/chroots/balerion"
DEFAULT_WORKTREE = "/blox/adixon1/AURORA-2295-wapi-ipc-audit-log"
DEFAULT_CONTAINER = "balerion"
LOG_LEVEL = "info"

NCC_DOWN_REASON = f"{DEFAULT_CONTAINER} slot is not running"
APPLIANCE_DOWN_REASON = f"{DEFAULT_HOSTNAME} is not reachable via SSH"

# Set once per session by conftest.py's pytest_configure(), so config
# factories and the junit-xml report land in the same timestamped run dir.
# None until then — falls back to no per-run log files.
OUTPUT_DIR: str | None = None


def resolve_ip():
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
    import time

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
