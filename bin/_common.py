"""Shared display helpers for dominion/reckoning/verdict."""

import xml.etree.ElementTree as ET
from datetime import datetime

B      = "\033[1m"
DIM    = "\033[2m"
GREEN  = "\033[32m"
RED    = "\033[31m"
YELLOW = "\033[33m"
CYAN   = "\033[36m"
RST    = "\033[0m"


def parse_junit(path):
    """Parse a pytest --junit-xml report (written to <run_dir>/junit.xml by
    integration_tests/conftest.py). Returns the same
    {script, target, passed, failed, skipped} shape this codebase has always
    used for dominion/reckoning, plus a 'cases' list (status/name/duration/
    message per <testcase>) for verdict's per-test detail view.

    'script' and 'target' come from custom <property> elements
    (record_testsuite_property("invocation", ...) / ("target", ...)) recorded
    once per session in conftest.py — there's no single "script" once pytest
    can run many files in one invocation, so this is the closest equivalent.
    """
    info = {"script": "—", "target": "—", "passed": None, "failed": None, "skipped": None, "cases": []}
    try:
        suite = ET.parse(path).getroot().find("testsuite")
        if suite is None:
            return info

        tests    = int(suite.get("tests", 0))
        failures = int(suite.get("failures", 0))
        errors   = int(suite.get("errors", 0))
        skipped  = int(suite.get("skipped", 0))
        info["passed"]  = max(tests - failures - errors - skipped, 0)
        info["failed"]  = failures + errors
        info["skipped"] = skipped

        props = {p.get("name"): p.get("value") for p in suite.findall("./properties/property")}
        info["target"] = props.get("target", "—")
        info["script"] = props.get("invocation", "pytest")

        for case in suite.findall("testcase"):
            name = f"{case.get('classname', '')}::{case.get('name', '')}"
            duration = float(case.get("time", 0.0))
            failure = case.find("failure")
            error = case.find("error")
            skip_el = case.find("skipped")
            if failure is not None or error is not None:
                detail = failure if failure is not None else error
                status, message = "FAIL", (detail.text or detail.get("message", "") or "").strip()
            elif skip_el is not None:
                status, message = "SKIP", (skip_el.get("message") or "").strip()
            else:
                status, message = "PASS", ""
            info["cases"].append({"status": status, "name": name, "duration": duration, "message": message})
    except Exception:
        pass
    return info


def fmt_run_id(run_id, fmt="%Y-%m-%d %H:%M"):
    try:
        return datetime.strptime(run_id, "%Y-%m-%d_%H-%M-%S").strftime(fmt)
    except Exception:
        return run_id


def age_str(dt):
    """Format a past datetime as a short relative age string (e.g. '5m ago').

    All timestamps in this codebase (.slots.json fields, results/ run-id dirs)
    are naive local wall-clock times from datetime.now() — compare naive-to-naive.
    """
    diff = int((datetime.now() - dt).total_seconds())
    if diff < 60:
        return f"{diff}s ago"
    if diff < 3600:
        return f"{diff // 60}m ago"
    if diff < 86400:
        return f"{diff // 3600}h ago"
    return dt.strftime("%b %d")


def latest_run_age(results_dir):
    """Age string for the most recent run directory under results_dir, or '—'."""
    if not results_dir.exists():
        return "—"
    dirs = sorted((d for d in results_dir.iterdir() if d.is_dir()), reverse=True)
    if not dirs:
        return "—"
    try:
        return age_str(datetime.strptime(dirs[0].name, "%Y-%m-%d_%H-%M-%S"))
    except Exception:
        return "—"
