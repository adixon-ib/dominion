"""Ticket: the plumbing every ticket test needs, as one context manager.

    with Ticket("NIOS-1", HOST, results_dir=DIR) as t:
        t.cleanup(lambda: undo(t))
        connect = t.phase("1. connect", t.connect)
        t.phase("2. check", lambda: check(t), needs=connect)

Writes run.log (stdout, stderr and logger output), report.md (per phase:
preconditions, WAPI and shell calls, checks, errors), summary.txt and
junit.xml under a timestamped directory, and exits the process on leaving the
block: 1 if any phase, cleanup or the block itself failed or a phase was
skipped for an unmet `pre` precondition, else 0 (deliberate `skip=` and
needs-propagated skips do not change it).
"""

from __future__ import annotations

import json
import logging
import os
import re
import subprocess
import sys
from collections.abc import Callable, Iterable
from dataclasses import dataclass

from .appliance import Appliance, lab_network
from .config import ApplianceConfig
from .constants import WAPI_VERSION
from .container import NiosContainer
from .harness import Run
from .log import create_logger

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


def _fmt(v) -> str:
    return v if isinstance(v, str) else repr(v)


class Checks:
    """Record every expectation in a block, then raise once listing failures.
    Get one from Ticket.checks() so the expectations land in the report."""

    def __init__(self, what: str, sink: list | None = None):
        self.what = what
        self.failures: list[str] = []
        self._sink = sink

    def expect(self, label: str, expected, got, ok: bool | None = None) -> None:
        if ok is None:
            ok = expected == got
        if self._sink is not None:
            self._sink.append(
                {"label": label, "expected": expected, "got": got, "ok": ok}
            )
        if not ok:
            msg = f"{label}: expected {_fmt(expected)}, got {_fmt(got)}"
            log.error("CHECK FAILED: %s", msg)
            self.failures.append(msg)

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        if exc_type is None and self.failures:
            raise AssertionError(
                f"{self.what}:\n  " + "\n  ".join(self.failures)
            )
        return False


def _fence(text: str, lang: str = "") -> str:
    ticks = "`" * max(3, max(map(len, re.findall("`+", text)), default=0) + 1)
    return f"{ticks}{lang}\n{text.rstrip(chr(10))}\n{ticks}"


def _compact(text: str) -> str:
    """One line per list element, or one line for anything else. Not JSON: as is."""
    def dump(v):
        return json.dumps(v, separators=(", ", ": "), sort_keys=True)

    try:
        v = json.loads(text)
    except ValueError:
        return _fence(text)
    return _fence("\n".join(dump(el) for el in v) if v and isinstance(v, list)
                  else dump(v))


def _collapse(calls: list[dict]) -> list[tuple[dict, int]]:
    """Merge consecutive calls with the same command, keeping the last."""
    out: list[tuple[dict, int]] = []
    for c in calls:
        key = (c["kind"], c.get("method"), c.get("path"), c.get("cmd"))
        if out and out[-1][2] == key:
            out[-1] = (c, out[-1][1] + 1, key)
        else:
            out.append((c, 1, key))
    return [(c, n) for c, n, _ in out]


def _render_call(c: dict, n: int = 1) -> list[str]:
    times = f"  (x{n}, last shown)" if n > 1 else ""
    if c["kind"] == "wapi":
        out = [f"{c['method']} {c['path']}  -> {c['status']}{times}"]
        if c["body"] is not None:
            out += ["Request body", _compact(json.dumps(c["body"]))]
        if c["response"]:
            out.append(_compact(c["response"]))
        return out
    out = [f"$ {c['cmd']}  -> exit {c['rc']}{times}"]
    for key in ("stdout", "stderr"):
        if c[key].strip():
            out += [key, _compact(c[key]) if key == "stdout" else _fence(c[key])]
    return out


BUILD_RE = re.compile(r"Version\s*:\s*(\S+)")


def _new_section(name: str, pre: list | None = None) -> dict:
    return {
        "name": name, "status": "SKIP", "duration": 0.0, "reason": "", "error": None,
        "pre": pre if pre is not None else [], "calls": [], "checks": [],
    }


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
        self.build = "unknown"
        self.run = Run(output_dir=results_dir)
        self.output_dir = self.run.output_dir
        self._app: Appliance | None = None
        self._cleanups: list[Callable[[], object]] = []
        self._warnings: list[str] = []
        self._notes: list[str] = []
        self._sections: list[dict] = []
        self._current: dict | None = None
        self._unmet: list[str] = []
        self._saved: tuple | None = None
        self._logfile = None
        self._log = create_logger(name="dominion.ticket")

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
                f"{self.name}: no host given (lab hostname, e.g. NIOS_TEST_HOST)"
            )
        ip = lab_network(self.host)[0]
        app = Appliance.from_config(ApplianceConfig(
            hostname=self.host, ip=ip, worktree=self.worktree,
            output_dir=self.output_dir,
        ))
        app.wapi_version = self.wapi_version
        app.on_call = self._record_call
        self._app = app
        self.run.add(app)
        self._log.info("Connected %s (%s)", self.host, ip)
        self._read_build()

    def _record_call(self, record: dict) -> None:
        if self._current is not None:
            self._current["calls"].append(record)

    def _read_build(self) -> None:
        try:
            with self.app.console_session() as console:
                out = console.run("show version")
            m = BUILD_RE.search(out)
            if not m:
                raise RuntimeError(f"no Version line in {out!r}")
            self.build = m.group(1)
        except Exception as e:
            self.warn(f"build not read: {e}")

    def checks(self, what: str) -> Checks:
        sink = self._current["checks"] if self._current is not None else None
        return Checks(what, sink)

    def _skip(
        self, section: dict, reason: str, blocked: bool, unmet: bool = False,
    ) -> Phase:
        self.run.skip(section["name"], reason, _counts_as_failure=unmet)
        section["reason"] = reason
        self._sections.append(section)
        return Phase(section["name"], "SKIP", reason, blocked=blocked)

    def _step(self, section: dict, fn: Callable[[], object]) -> str:
        """Run fn as a recorded step, attributing app calls to section."""
        prev, self._current = self._current, section
        try:
            self.run.test(section["name"], fn)
        except BaseException:
            section.update(status="FAIL", error="interrupted, see the interrupted step")
            self._sections.append(section)
            raise
        finally:
            self._current = prev
        r = self.run._results[-1]
        section.update(status=r["status"], duration=r["duration"], error=r["error"])
        self._sections.append(section)
        return r["status"]

    def phase(
        self,
        name: str,
        fn: Callable[[], object],
        *,
        needs: Phase | Iterable[Phase] = (),
        pre: Iterable[tuple[str, Callable[[], object]]] = (),
        skip: str | None = None,
    ) -> Phase:
        section = _new_section(name)
        if skip:
            return self._skip(section, skip, blocked=False)
        deps = [needs] if isinstance(needs, Phase) else list(needs or ())
        pre = list(pre)
        unmet = []
        for d in deps:
            if d.status == "FAIL" or (d.status == "SKIP" and d.blocked):
                state = "failed" if d.status == "FAIL" else "skipped"
                unmet.append(f"{d.name} ({state})")
                section["pre"].append((d.name, f"not met ({state})"))
        if unmet:
            section["pre"] += [(label, "not evaluated") for label, _ in pre]
            return self._skip(section, "needs " + ", ".join(unmet), blocked=True)
        not_met = []
        crashed = None
        prev, self._current = self._current, section
        try:
            for i, (label, check) in enumerate(pre):
                try:
                    ok = bool(check())
                except Exception as e:
                    crashed = e
                    section["pre"].append(
                        (label, f"not met ({type(e).__name__}: {e})")
                    )
                    section["pre"] += [(lb, "not evaluated") for lb, _ in pre[i + 1:]]
                    break
                section["pre"].append((label, "met" if ok else "not met"))
                if not ok:
                    not_met.append(label)
        finally:
            self._current = prev
        if crashed is not None:
            return Phase(name, self._step(section, lambda: self._reraise(crashed)))
        if not_met:
            self._unmet.append(f"{name}: {', '.join(not_met)}")
            return self._skip(
                section, "not met: " + ", ".join(not_met), blocked=True, unmet=True,
            )
        return Phase(name, self._step(section, fn))

    @staticmethod
    def _reraise(exc: BaseException):
        raise exc

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
            json.dump(obj, f, indent=2, default=str, sort_keys=True)
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
                self._step(
                    _new_section(f"interrupted: {exc_type.__name__}"),
                    lambda: self._raise_from(exc),
                )
            self._run_cleanups()
            self._write_report()
            self.run.summary(extra=self._extra(), _allow_no_target=True)
        finally:
            _rebind_log_handlers(sys.stderr, old_err)
            sys.stdout, sys.stderr = old_out, old_err
            self._logfile.close()

    def _run_cleanups(self) -> None:
        several = len(self._cleanups) > 1
        for n, fn in reversed(list(enumerate(self._cleanups, 1))):
            section = _new_section(f"cleanup {n}" if several else "cleanup")
            if self._app is None:
                self._skip(section, "not connected", blocked=False)
            else:
                self._step(section, self._survive(fn))

    def _survive(self, fn: Callable[[], object]) -> Callable[[], object]:
        """Make a BaseException (second Ctrl-C) fail this cleanup, not the rest."""
        def guarded():
            try:
                return fn()
            except Exception:
                raise
            except BaseException as e:
                self._raise_from(e)
        return guarded

    def _extra(self) -> list[str]:
        return [
            f"host: {self.host or '(none)'}",
            f"dominion {PACKAGE_DIR}: {_git_rev(PACKAGE_DIR)}",
            *[f"WARNING: {w}" for w in self._warnings],
            *[f"NOTE: {n}" for n in self._notes],
            *[f"UNMET (exit 1): {u}" for u in self._unmet],
        ]

    def _write_report(self) -> None:
        run = self.run
        verdict = "FAIL" if run._failed or self._unmet else "PASS"
        lines = [
            f"# {self.name}",
            "",
            f"- Host: {self.host or '(none)'}",
            f"- Build: {self.build}",
            f"- Dominion: {_git_rev(PACKAGE_DIR)}",
            f"- Verdict: {verdict} ({run._passed} passed, {run._failed} failed, "
            f"{run._skipped} skipped)",
        ]
        for sec in self._sections:
            dur = "" if sec["status"] == "SKIP" else f" ({sec['duration']:.1f}s)"
            lines += ["", f"## {sec['name']}  {sec['status']}{dur}"]
            if sec["reason"]:
                lines += ["", f"Skipped: {sec['reason']}"]
            if sec["pre"]:
                lines += ["", "Preconditions"]
                lines += [f"- {label}: {state}" for label, state in sec["pre"]]
            if sec["checks"]:
                lines += ["", "Checks"]
                lines += [
                    f"- {c['label']}: expected {_fmt(c['expected'])}, "
                    f"got {_fmt(c['got'])}  {'ok' if c['ok'] else 'FAIL'}"
                    for c in sec["checks"]
                ]
            if sec["status"] == "FAIL" and sec["error"]:
                lines += ["", "Error", _fence(sec["error"])]
            if sec["calls"]:
                lines += ["", "Calls"]
                for call, n in _collapse(sec["calls"]):
                    lines += _render_call(call, n)
        for title, items in (("Warnings", self._warnings), ("Notes", self._notes)):
            if items:
                lines += ["", f"## {title}", *[f"- {i}" for i in items]]
        with open(os.path.join(self.output_dir, "report.md"), "w") as f:
            f.write("\n".join(lines) + "\n")
