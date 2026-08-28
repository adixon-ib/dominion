"""Run: a test-step/reporting harness for standalone dominion scripts.

For any script that drives itself directly via `python3 script.py` (ticket
tests under scripts/tickets/*/test_*.py, one-off repro scripts, etc.) rather
than through the integration_tests/ pytest suite. Exported from the
top-level dominion package. Use `from dominion import Run`.

Do not use this module from anything under integration_tests/ itself.
pytest already has its own reporting (--junit-xml), and
resolve_ip_from_argv() reads sys.argv, which under a pytest run is pytest's
own argv (file paths, -m expressions), not an appliance IP.
"""

import os
import socket
import sys
import time
import traceback
import xml.etree.ElementTree as ET
from datetime import datetime

from .config import DEFAULT_RESULTS_DIR, Defaults

_defaults: Defaults | None = None


def _get_defaults() -> Defaults:
    global _defaults
    if _defaults is None:
        _defaults = Defaults.from_json()
    return _defaults


def resolve_ip_from_argv():
    """Resolve the target IP for a standalone script's CLI invocation.

    Returns:
        The first non-flag command-line argument if one was given, else the
        NIOS_IP environment variable if set, else DEFAULT_IP from config.json.
    """
    for arg in sys.argv[1:]:
        if not arg.startswith("-"):
            return arg
    return os.environ.get("NIOS_IP", _get_defaults().ip)


class Run:
    """One script's test run: tracks pass/fail/skip counts and writes
    summary.txt/junit.xml under its own output directory.

    Unlike the old module-level test()/skip()/summary() functions, a Run's
    state (counts, output directory) lives on the instance, not on the
    dominion module. There's nothing to race against import order: the
    output directory is resolved the moment you construct the Run, from
    whichever argument/env var/config value you pass in, not as a side
    effect of importing dominion.

    Args:
        output_dir: Base directory this run's output goes under (a
            timestamped subdirectory is created inside it). Falls back to
            the NIOS_TEST_OUTPUT_DIR env var, then config.json's
            output_dir, then DEFAULT_RESULTS_DIR.
        script: Name recorded in reports. Defaults to the invoking script's
            basename (sys.argv[0]).
    """

    def __init__(self, output_dir: str | None = None, script: str | None = None):
        defaults = _get_defaults()
        base = output_dir or os.environ.get(
            "NIOS_TEST_OUTPUT_DIR", defaults.output_dir or DEFAULT_RESULTS_DIR,
        )
        self.output_dir = os.path.join(base, datetime.now().strftime("%Y-%m-%d_%H-%M-%S"))
        self.script = script or os.path.basename(sys.argv[0])
        self._passed = 0
        self._failed = 0
        self._skipped = 0
        self._results: list[dict] = []
        self._targets: list = []

    def add(self, target) -> None:
        """Register a target (Appliance, LocalAppliance, NiosContainer, or
        LocalNiosContainer) with this run. summary() will auto-dump its
        infoblox.log and use it for report labeling."""
        if target not in self._targets:
            self._targets.append(target)

    def test(self, name, fn):
        """Run fn as one named test step, recording pass/fail for the summary.

        Args:
            fn: Zero-argument callable. Any exception it raises is caught
                and recorded as a failure rather than propagated.

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
            self._passed += 1
            print("  PASS")
        except Exception:
            self._failed += 1
            error = traceback.format_exc()
            traceback.print_exc()
            print("  FAIL")
        duration = time.monotonic() - start
        self._results.append({
            "status": "FAIL" if error else "PASS", "name": name,
            "duration": duration, "error": error,
        })
        return result

    def skip(self, name, reason=""):
        """Record a named test step as skipped, without running it.

        Args:
            reason: Optional explanation, printed and included in the summary.
        """
        print(f"\n{'=' * 60}")
        print(f"SKIP: {name}{f' — {reason}' if reason else ''}")
        print(f"{'=' * 60}")
        self._skipped += 1
        self._results.append({
            "status": "SKIP", "name": name, "duration": 0.0,
            "error": None, "reason": reason,
        })

    def summary(self, app=None, *, no_ib_log=False):
        """Print totals, write summary.txt and junit.xml, then exit the process.

        Terminates the script with sys.exit(): 1 if any test failed, else 0.
        Automatically dumps infoblox.log for every registered appliance
        unless no_ib_log=True.

        Args:
            app: Optional. If provided, registers it (backward compat for
                callers that pass run.summary(gm)). New code should use
                run.add(app) instead and call run.summary() with no args.
            no_ib_log: Skip the automatic infoblox.log dump.
        """
        if app is not None:
            self.add(app)
        if not self._targets:
            raise TypeError(
                "No targets registered. Use run.add(target) or pass one to summary()."
            )
        if not no_ib_log:
            self._dump_infoblox_logs()
        primary = self._targets[0]
        total = self._passed + self._failed + self._skipped
        print(f"\n{'=' * 60}")
        print(
            f"RESULTS: {self._passed} passed, {self._failed} failed, "
            f"{self._skipped} skipped out of {total}"
        )
        print(f"{'=' * 60}")
        if self.output_dir:
            self._write_junit(primary)
            files = sorted(os.listdir(self.output_dir)) if os.path.isdir(self.output_dir) else []
            if "summary.txt" not in files:
                files.append("summary.txt")
                files.sort()
            self._write_summary(primary, files)
            print(f"Logs: {self.output_dir}/")
            for name in files:
                print(f"  {name}")
        sys.exit(1 if self._failed else 0)

    def _dump_infoblox_logs(self) -> None:
        for target in self._targets:
            name = getattr(target, "hostname", None) or getattr(target, "container_name", str(target))
            try:
                dest = os.path.join(self.output_dir, f"{name}_infoblox.log")
                target.dump_infoblox_log(dest=dest)
            except Exception as e:
                print(f"  WARNING: failed to dump infoblox.log for {name}: {e}")

    def _target(self, app) -> str:
        """Label for app in reports. Each app type formats itself via __str__."""
        return str(app)

    def _write_summary(self, app, files):
        """Write output_dir/summary.txt with the run's target, steps, and totals.

        Args:
            files: Full listing of output_dir, including summary.txt's own
                name and any log files written during the run, so the
                file's final block matches what summary() prints to the
                console.
        """
        run_id = os.path.basename(self.output_dir)
        target = self._target(app)
        lines = [
            f"Run:    {run_id}",
            f"Script: {self.script}",
            f"Target: {target}",
            "",
        ]

        for r in self._results:
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

        total = self._passed + self._failed + self._skipped
        lines += [
            "",
            f"{self._passed} passed, {self._failed} failed, "
            f"{self._skipped} skipped out of {total}",
            f"Logs: {self.output_dir}/",
            *[f"  {name}" for name in files],
        ]

        summary_path = os.path.join(self.output_dir, "summary.txt")
        os.makedirs(self.output_dir, exist_ok=True)
        with open(summary_path, "w") as f:
            f.write("\n".join(lines) + "\n")

    def _write_junit(self, app):
        """Write output_dir/junit.xml in the same shape pytest's own
        --junit-xml produces, so dominion/reckoning/verdict (which only
        read junit.xml, not summary.txt) pick up ticket-test runs too.

        Args:
            app: The appliance/container object the run tested against.
                Used to label the report target via str(app).
        """
        target = self._target(app)

        total_time = sum(r["duration"] for r in self._results)
        testsuites = ET.Element("testsuites", {"name": "dominion ticket tests"})
        testsuite = ET.SubElement(testsuites, "testsuite", {
            "name": self.script,
            "errors": "0",
            "failures": str(self._failed),
            "skipped": str(self._skipped),
            "tests": str(self._passed + self._failed + self._skipped),
            "time": f"{total_time:.3f}",
            "timestamp": datetime.now().isoformat(),
            "hostname": socket.gethostname(),
        })

        properties = ET.SubElement(testsuite, "properties")
        ET.SubElement(properties, "property", {"name": "target", "value": target})
        ET.SubElement(properties, "property", {"name": "invocation", "value": self.script})

        classname = os.path.splitext(self.script)[0]
        for r in self._results:
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

        junit_path = os.path.join(self.output_dir, "junit.xml")
        os.makedirs(self.output_dir, exist_ok=True)
        ET.ElementTree(testsuites).write(junit_path, encoding="utf-8", xml_declaration=True)
