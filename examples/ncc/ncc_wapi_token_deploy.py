#!/usr/bin/env python3
"""Deploy the token WAPI suite (pyabs/wapi/test files) into a running NCC container.

Uses the dominion library's LocalNiosContainer.deploy_token_py() for production
files and adds deployment of WAPI test files + shared fixtures (test_*.py,
token_test_common.py). Also handles waiting out admin-httpd's restart (the
process reappears in ~5s, but WAPI itself isn't actually serving again for ~45s).

Usage:
    python3 ncc_wapi_token_deploy.py -c balerion -w /blox/adixon1/my-worktree
    python3 ncc_wapi_token_deploy.py -c balerion -w /blox/adixon1/my-worktree --skip-restart
"""

from __future__ import annotations

import argparse
import os
import time

from _common import container_from_args

from dominion import LocalNiosContainer

# Test files + shared fixtures — not covered by deploy_token_py, which only
# deploys production pyabs/wapi/ibap/branding files.
TEST_FILES = [
    "test_member_token.py",
    "test_grid_token_pool.py",
    "test_token_gridwide.py",
    "test_token_overage.py",
    "test_token_alert_threshold.py",
    "test_token_audit_log.py",
    "test_token_sync_status.py",
    "test_token_bundle.py",
    "token_test_common.py",
]

TEST_SRC_SUBPATH = "products/one/server/src/wapi/test"
TEST_DEST_DIR = "/infoblox/tests/lib/python/infoblox/test/one/wapi"

HTTPD_RESTART_WAIT = 45  # observed: httpd process reappears in ~5s, but WAPI
                         # itself doesn't actually start serving for ~45s


def deploy_test_files(container: LocalNiosContainer) -> list[str]:
    """Upload WAPI test files + shared fixtures, verifying each by hash."""
    src_base = os.path.join(container.worktree, TEST_SRC_SUBPATH)
    deployed = []
    for fname in TEST_FILES:
        local_path = os.path.join(src_base, fname)
        if not os.path.isfile(local_path):
            print(f"  SKIP (not found): {local_path}")
            continue
        dest = f"{TEST_DEST_DIR}/{fname}"
        local_h = container._local_md5(local_path)
        container._transport.upload(local_path, dest)
        container._clean_pycache(dest)
        remote_h = container._remote_md5(dest)
        if remote_h != local_h:
            raise RuntimeError(
                f"Hash mismatch deploying {fname}: local={local_h} remote={remote_h}"
            )
        deployed.append(fname)
    print(f"  Deployed {len(deployed)} test files to {TEST_DEST_DIR}")
    return deployed


def deploy(container: LocalNiosContainer, skip_restart: bool) -> None:
    print(f"=== Deploying to container: {container.container_name} ===")
    print(f"Source: {container.worktree}\n")

    print("--- pyabs + wapi ---")
    result = container.deploy_token_py(skip_restart=skip_restart)
    print(result)
    for f in result.details["files"]:
        print(f"  deployed {f['file']}")
    for skipped_path in result.details.get("skipped", []):
        print(f"  SKIP (not found): {skipped_path}")

    print("\n--- test files ---")
    deploy_test_files(container)

    if not skip_restart:
        print("\n--- restarting httpd ---")
        print(f"  waiting {HTTPD_RESTART_WAIT}s for httpd/WAPI to fully come back up...")
        time.sleep(HTTPD_RESTART_WAIT)

    print("\nDeploy complete.\n")


def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("-c", "--container", required=True,
                        help="NCC container name, e.g. balerion")
    parser.add_argument("-w", "--worktree", required=True,
                        help="Worktree root to deploy from")
    parser.add_argument("--chroot", default=None,
                        help="Chroot path (default: /local/91/chroots/<container>)")
    parser.add_argument("--skip-restart", action="store_true",
                        help="Deploy without restarting admin-httpd (test-only changes)")
    args = parser.parse_args()

    container = container_from_args(args.container, args.worktree, args.chroot)
    deploy(container, skip_restart=args.skip_restart)


if __name__ == "__main__":
    main()
