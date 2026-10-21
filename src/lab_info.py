"""get_lab_info CLI wrapper."""

from __future__ import annotations

import subprocess


def get_lab_info(hostname: str) -> dict[str, str]:
    """Parse get_lab_info output for a hostname into a flat dict."""
    result = subprocess.run(
        ["get_lab_info", "-H", hostname],
        capture_output=True, text=True, check=True, timeout=30,
    )
    info = {}
    for line in result.stdout.splitlines():
        if "=" in line:
            key, _, value = line.partition("=")
            info[key.strip()] = value.strip()
    return info
