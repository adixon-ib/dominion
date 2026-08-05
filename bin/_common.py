"""Shared display helpers for dominion/reckoning/verdict."""

import re
from datetime import datetime

B      = "\033[1m"
DIM    = "\033[2m"
GREEN  = "\033[32m"
RED    = "\033[31m"
YELLOW = "\033[33m"
CYAN   = "\033[36m"
RST    = "\033[0m"


def parse_summary(path):
    info = {"script": "—", "target": "—", "passed": None, "failed": None, "skipped": None}
    try:
        for line in path.read_text().splitlines():
            if line.startswith("Script:"):
                info["script"] = line.split(":", 1)[1].strip()
            elif line.startswith("Target:"):
                info["target"] = line.split(":", 1)[1].strip().split("(")[0].strip()
            elif m := re.match(r"(\d+) passed, (\d+) failed, (\d+) skipped", line):
                info["passed"]  = int(m.group(1))
                info["failed"]  = int(m.group(2))
                info["skipped"] = int(m.group(3))
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
