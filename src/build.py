# NiosBuild — wraps make install for balerion/drogon slots.
#
# No test file exists for this intentionally — builds are destructive and slow.
# Use directly from Python or a workflow script:
#
#   from dominion import NiosBuild, BuildConfig
#   build = NiosBuild.from_config(BuildConfig(worktree="...", slot="balerion"))
#   build.quick()   # touch skip_slow && make install         (~5-15 min)
#   build.full()    # installclean + build-env + install      (~60 min)
#   build.clean()   # make installclean only (no rebuild)

from __future__ import annotations

import os
import subprocess
from typing import TYPE_CHECKING

from .config import BuildConfig
from .log import create_logger

if TYPE_CHECKING:
    from .log import NiosLogger


class NiosBuild:

    def __init__(self, config: BuildConfig, logger: NiosLogger):
        self._config = config
        self._logger = logger

    @classmethod
    def from_config(cls, config: BuildConfig) -> "NiosBuild":
        logger = config.logger or create_logger(
            name=f"dominion.build-{config.slot}",
            level=config.log_level,
            log_file=config.log_file,
        )
        return cls(config, logger)

    def _is_container_running(self) -> bool:
        proc = subprocess.run(
            ["docker", "inspect", "-f", "{{.State.Running}}", self._config.slot],
            capture_output=True, text=True, check=False,
        )
        return proc.stdout.strip().lower() == "true"

    def _run(self, make_cmds: str) -> None:
        if not os.path.isdir(self._config.worktree):
            raise RuntimeError(
                f"Worktree does not exist: {self._config.worktree}"
            )
        if self._is_container_running():
            raise RuntimeError(
                f"Cannot build for slot {self._config.slot}: container is running. "
                "Stop the container first to prevent OverlayFS corruption."
            )
        bash = (
            f"cd {self._config.worktree} && "
            f"setbuild && "
            f"export ASLAN_INSTALL_ROOT={self._config.chroot} && "
            f"{make_cmds}"
        )
        self._logger.info("Build (%s): %s", self._config.slot, make_cmds)
        proc = subprocess.Popen(
            ["bash", "-lc", bash],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
        )
        for line in proc.stdout:
            self._logger.info(line.rstrip())
        proc.wait()
        if proc.returncode != 0:
            raise RuntimeError(
                f"Build failed (exit {proc.returncode}): {make_cmds}"
            )

    def quick(self) -> None:
        """Incremental build"""
        self._run("touch skip_slow && make install")

    def full(self) -> None:
        """Full rebuild from scratch"""
        self._run("make installclean && make build-env && make install")

    def clean(self) -> None:
        """Wipe the chroot"""
        self._run("make installclean")

    def __repr__(self) -> str:
        return f"NiosBuild(slot={self._config.slot!r}, worktree={self._config.worktree!r})"
