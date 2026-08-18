"""Common helpers for NCC WAPI deploy and test scripts."""

from __future__ import annotations

import os
import sys

SCRIPTS_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, SCRIPTS_DIR)

from nios_test import LocalNiosContainer, LocalContainerConfig  # noqa: E402


def container_from_args(container_name: str, worktree: str | None, chroot: str | None) -> LocalNiosContainer:
    """Build and return a LocalNiosContainer from arguments."""
    resolved_chroot = chroot or f"/local/91/chroots/{container_name}"
    return LocalNiosContainer.from_config(LocalContainerConfig(
        container_name=container_name,
        chroot=resolved_chroot,
        worktree=worktree,
        log_level="info",
    ))
