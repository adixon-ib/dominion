#!/usr/bin/env python3
"""Run all nios_test integration tests.

Usage:
    python3 -m nios_test.tests.run_all [--target appliance|ncc|all]

Runs test files in dependency order: transport → session → deploy.
"""

import argparse
import subprocess
import sys
import os

TESTS_DIR = os.path.dirname(os.path.abspath(__file__))

TEST_FILES = [
    "test_transport.py",
    "test_session.py",
    "test_container.py",
    "test_appliance.py",
    "test_deploy.py",
]


def run_test(filename, extra_args):
    path = os.path.join(TESTS_DIR, filename)
    print(f"\n{'#' * 70}")
    print(f"# Running {filename}")
    print(f"{'#' * 70}\n")

    result = subprocess.run(
        [sys.executable, path] + extra_args,
        cwd=TESTS_DIR,
    )
    return result.returncode


def main():
    parser = argparse.ArgumentParser(description="Run nios_test integration tests")
    parser.add_argument(
        "--target",
        choices=["appliance", "ncc", "all"],
        default="all",
        help="Which target to test (default: all)",
    )
    parser.add_argument(
        "--output-dir", "-o",
        default=None,
        help="Directory for log files (each test file writes its own log)",
    )
    parser.add_argument(
        "ip",
        nargs="?",
        default=None,
        help="Appliance IP (passed to individual tests)",
    )
    args = parser.parse_args()

    extra_args = [args.ip] if args.ip else []

    os.environ["NIOS_TEST_TARGET"] = args.target
    if args.output_dir:
        os.makedirs(args.output_dir, exist_ok=True)
        os.environ["NIOS_TEST_OUTPUT_DIR"] = os.path.abspath(args.output_dir)

    results = {}
    for filename in TEST_FILES:
        rc = run_test(filename, extra_args)
        results[filename] = rc

    print(f"\n{'#' * 70}")
    print(f"# Overall Results")
    print(f"{'#' * 70}")
    for filename, rc in results.items():
        status = "PASS" if rc == 0 else "FAIL"
        print(f"  {filename}: {status}")

    failed = sum(1 for rc in results.values() if rc != 0)
    total = len(results)
    print(f"\n  {total - failed}/{total} test files passed")

    if args.output_dir:
        print(f"  Logs: {os.path.abspath(args.output_dir)}/")

    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
