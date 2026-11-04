"""Ticket: the plumbing every ticket test needs, as one context manager.

    with Ticket("NIOS-1", HOST, results_dir=DIR) as t:
        t.cleanup(lambda: undo(t))
        connect = t.phase("1. connect", t.connect)
        t.phase("2. check", lambda: check(t), needs=connect)

Writes run.log (stdout, stderr and logger output), summary.txt and junit.xml
under a timestamped directory, and exits the process on leaving the block:
1 if any phase, cleanup or the block itself failed, else 0.
"""

from __future__ import annotations

import json
import logging
import os
import subprocess
import sys
from collections.abc import Callable, Iterable
from dataclasses import dataclass

from .appliance import Appliance, lab_network
from .config import ApplianceConfig
from .constants import WAPI_VERSION
from .container import NiosContainer
from .harness import Run

PACKAGE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

log = logging.getLogger("dominion.ticket")


class _Tee:
    def __init__(self, stream, logfile):
        self.stream, self.logfile = stream, logfile

    def write(self, text):
        self.stream.write(text)
        self.logfile.write(text)
        self.logfile.flush()
        return len(text)

    def flush(self):
        self.stream.flush()
        self.logfile.flush()

    def __getattr__(self, name):
        return getattr(self.stream, name)


def _rebind_log_handlers(old, new) -> None:
    """dominion loggers bind sys.stderr at creation, so repoint them."""
    for lg in logging.Logger.manager.loggerDict.values():
        for h in getattr(lg, "handlers", []):
            if isinstance(h, logging.StreamHandler) and h.stream is old:
                h.setStream(new)


def _git_rev(path: str) -> str:
    try:
        out = subprocess.run(
            ["git", "-C", path, "rev-parse", "--short", "HEAD"],
            capture_output=True, text=True, check=True,
        )
        return out.stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return "unknown"


@dataclass
class Phase:
    name: str
    status: str  # "PASS" | "FAIL" | "SKIP"
    reason: str = ""
    blocked: bool = False  # SKIP caused by a prerequisite, as opposed to deliberate


class Checks:
    """Collect every failed expectation in a block, then raise once."""

    def __init__(self, what: str):
        self.what = what
        self.failures: list[str] = []

    def expect(self, ok, message) -> None:
        if not ok:
            log.error("CHECK FAILED: %s", message)
            self.failures.append(message)

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        if exc_type is None and self.failures:
            raise AssertionError(
                f"{self.what}:\n  " + "\n  ".join(self.failures)
            )
        return False


class Ticket:
    def __init__(
        self,
        name: str,
        host: str | None,
        *,
        results_dir: str | None = None,
        worktree: str | None = None,
        wapi_version: str = WAPI_VERSION,
    ):
        self.name = name
        self.host = host
        self.worktree = worktree
        self.wapi_version = wapi_version
        self.run = Run(output_dir=results_dir)
        self.output_dir = self.run.output_dir
        self._app: Appliance | None = None
        self._cleanups: list[Callable[[], object]] = []
        self._warnings: list[str] = []
        self._notes: list[str] = []
        self._saved: tuple | None = None
        self._logfile = None
        self._log = logging.getLogger("dominion.ticket")

    @property
    def app(self) -> Appliance:
        if self._app is None:
            raise RuntimeError(
                "appliance not connected: connect failed or did not run"
            )
        return self._app

    @property
    def container(self) -> NiosContainer:
        return self.app.container

    def connect(self) -> None:
        if not self.host:
            raise RuntimeError(
                f"{self.name}: no host given (set the lab hostname, e.g. NIOS_TEST_HOST)"
            )
        ip = lab_network(self.host)[0]
        app = Appliance.from_config(ApplianceConfig(
            hostname=self.host, ip=ip, worktree=self.worktree,
            output_dir=self.output_dir,
        ))
        app.wapi_version = self.wapi_version
        self._app = app
        self.run.add(app)
        self._log.info("Connected %s (%s)", self.host, ip)

    def phase(
        self,
        name: str,
        fn: Callable[[], object],
        *,
        needs: Phase | Iterable[Phase] = (),
        skip: str | None = None,
    ) -> Phase:
        if skip:
            self.run.skip(name, skip)
            return Phase(name, "SKIP", skip)
        deps = [needs] if isinstance(needs, Phase) else list(needs)
        blockers = [
            f"{d.name} ({'failed' if d.status == 'FAIL' else 'skipped'})"
            for d in deps
            if d.status == "FAIL" or (d.status == "SKIP" and d.blocked)
        ]
        if blockers:
            reason = "needs " + ", ".join(blockers)
            self.run.skip(name, reason)
            return Phase(name, "SKIP", reason, blocked=True)
        self.run.test(name, fn)
        return Phase(name, self.run._results[-1]["status"])

    def cleanup(self, fn: Callable[[], object]) -> None:
        self._cleanups.append(fn)

    def warn(self, msg: str) -> None:
        self._log.warning(msg)
        self._warnings.append(msg)

    def note(self, msg: str) -> None:
        self._log.info(msg)
        self._notes.append(msg)

    def write(self, name: str, obj) -> str:
        path = os.path.join(self.output_dir, name)
        with open(path, "w") as f:
            json.dump(obj, f, indent=2, default=str)
        return path

    def __enter__(self):
        os.makedirs(self.output_dir, exist_ok=True)
        self._logfile = open(os.path.join(self.output_dir, "run.log"), "w")
        old_out, old_err = sys.stdout, sys.stderr
        sys.stdout = _Tee(old_out, self._logfile)
        sys.stderr = _Tee(old_err, self._logfile)
        _rebind_log_handlers(old_err, sys.stderr)
        self._saved = (old_out, old_err)
        return self

    def _raise_from(self, exc: BaseException):
        raise RuntimeError(f"{type(exc).__name__}: {exc}") from exc

    def __exit__(self, exc_type, exc, tb):
        old_out, old_err = self._saved
        try:
            if exc is not None:
                self.run.test(
                    f"interrupted: {exc_type.__name__}",
                    lambda: self._raise_from(exc),
                )
            self._run_cleanups()
            self.run.summary(extra=self._extra(), _allow_no_target=True)
        finally:
            _rebind_log_handlers(sys.stderr, old_err)
            sys.stdout, sys.stderr = old_out, old_err
            self._logfile.close()

    def _run_cleanups(self) -> None:
        several = len(self._cleanups) > 1
        for n, fn in reversed(list(enumerate(self._cleanups, 1))):
            name = f"cleanup {n}" if several else "cleanup"
            if self._app is None:
                self.run.skip(name, "not connected")
            else:
                self.run.test(name, fn)

    def _extra(self) -> list[str]:
        return [
            f"host: {self.host or '(none)'}",
            f"dominion {PACKAGE_DIR}: {_git_rev(PACKAGE_DIR)}",
            *[f"WARNING: {w}" for w in self._warnings],
            *[f"NOTE: {n}" for n in self._notes],
        ]
