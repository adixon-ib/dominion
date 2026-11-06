"""Coverage of the changed lines of a ticket, measured in the NIOS container.

    coverage_on(app, include=["*/infoblox/one/token_bundle.py"])
    ... run the ticket tests ...
    result = coverage_off(app, out_dir, worktree=WORKTREE, base="origin/main")

coverage_on installs a .pth hook so every Python process that starts in the
container measures itself with coverage (the image's coverage 6.2 in
/usr/lib/python3/dist-packages: a .pth under /usr/local does not find it).
admin-httpd runs Python through mod_python and its workers never run atexit, so
the hook also starts a daemon thread that saves the data every 2 seconds.
coverage_off combines and exports the data, removes the hook, and on the host
reports which of the lines changed between base and HEAD ran.

Every Python process on the box loads the hook during the session: include
limits what gets measured. On an appliance, ticket tests in a coverage session
must run with skip_home: reset_all restores the image Python and removes the
hook. A session needs a clean worktree at HEAD, the line numbers come from it.
"""

from __future__ import annotations

import base64
import io
import json
import os
import re
import shlex
import subprocess
import tarfile
import time
from dataclasses import dataclass, field

from dominion.actions.coverage_page import HTML_DIR, render
from dominion.src.deploy import CONTAINER_IBAP, CONTAINER_PYABS, CONTAINER_WAPI

COV_DIR = "/tmp/cov"
RC_FILE = f"{COV_DIR}/.coveragerc"
DATA_FILE = f"{COV_DIR}/.coverage"
HOOK_MODULE = "dominion_cov_hook"
HOOK_FILE = f"{COV_DIR}/{HOOK_MODULE}.py"
PTH_FILE = "/usr/lib/python3/dist-packages/zz_dominion_cov.pth"
JSON_FILE = f"{COV_DIR}/coverage.json"
HTML_SRC = f"{COV_DIR}/html"
FLUSH_WAIT = 3  # seconds: the saver thread saves every 2
STEP_TIMEOUT = 300

SRC = "products/one/server/src"
# worktree directory -> container directory the deploy puts its files in. pyutil
# files install next to the pyabs ones (oplog.py: .../infoblox/one/oplog.py).
CONTAINER_DIRS = {
    f"{SRC}/pyabs": CONTAINER_PYABS,
    f"{SRC}/pyutil": CONTAINER_PYABS,
    f"{SRC}/wapi": CONTAINER_WAPI,
    f"{SRC}/ibap": CONTAINER_IBAP,
}

HOOK = f"""\
import os
os.environ.setdefault("COVERAGE_PROCESS_START", "{RC_FILE}")
import coverage
cov = coverage.process_startup()
if cov:
    import threading
    import time

    def _save():
        while True:
            time.sleep(2)
            cov.save()

    threading.Thread(target=_save, daemon=True).start()"""
PTH = f'import sys; sys.path.insert(0, "{COV_DIR}"); import {HOOK_MODULE}'


def rcfile(include: list[str]) -> str:
    return (
        f"[run]\nparallel = True\ndata_file = {DATA_FILE}\n"
        f"include = {','.join(include)}"
    )


@dataclass
class FileCoverage:
    run: int  # changed lines that ran
    total: int  # changed lines that are executable
    missing: list[int] = field(default_factory=list)  # changed lines that did not run
    ran: list[int] = field(default_factory=list)  # changed lines that ran


@dataclass
class CoverageResult:
    files: dict[str, FileCoverage]  # worktree path -> measured changed lines
    not_measured: list[str]  # changed .py files without data
    coverage_json: str
    coverage_txt: str
    changed_lines_json: str
    coverage_html: str = ""  # coverage.html, the changed hunks
    coverage_html_dir: str = ""  # coverage's own report


def _container(target):
    return getattr(target, "container", target)


def _write(container, path: str, text: str) -> None:
    container.sh(f"cat > {shlex.quote(path)} <<'DOMINION_EOF'\n{text}\nDOMINION_EOF")


def coverage_on(target, include: list[str]) -> None:
    """Install the hook for the patterns in include (coverage `include` globs, e.g.
    "*/infoblox/one/token_bundle.py") and restart admin-httpd. target is an
    Appliance or a container. See the module docstring for the cautions."""
    if not include:
        raise ValueError("include must list at least one pattern")
    c = _container(target)
    c.sh(f"mkdir -p {COV_DIR}")
    _write(c, RC_FILE, rcfile(include))
    _write(c, HOOK_FILE, HOOK)
    _write(c, PTH_FILE, PTH)
    c.stop_admin_httpd()
    c.start_admin_httpd()
    c.wait_ready()


def coverage_off(target, out_dir: str, *, worktree: str, base: str) -> CoverageResult:
    """End the session: flush, stop admin-httpd, combine the data, copy
    coverage.json and coverage's own html report (coverage_html/) to out_dir, move
    the .pth to /tmp/cov (mv, not rm), start admin-httpd. Then, on the host,
    intersect the lines changed between base and HEAD of worktree with the
    measured ones and write coverage.txt, coverage.html (the changed hunks,
    green where they ran, red where not) and changed_lines.json next to
    coverage.json. The hook is removed and admin-httpd restarted even if combine
    fails."""
    c = _container(target)
    time.sleep(FLUSH_WAIT)
    c.stop_admin_httpd()
    try:
        env = f"COVERAGE_RCFILE={RC_FILE}"
        c.sh(f"{env} python3 -m coverage combine --keep", timeout=STEP_TIMEOUT)
        c.sh(f"{env} python3 -m coverage json -o {JSON_FILE}", timeout=STEP_TIMEOUT)
        c.sh(f"{env} python3 -m coverage html -d {HTML_SRC}", timeout=STEP_TIMEOUT)
        _, raw, _ = c.sh(f"cat {JSON_FILE}", timeout=STEP_TIMEOUT, report_lines=20)
        _, packed, _ = c.sh(
            f"tar -C {HTML_SRC} -czf - . | base64",
            timeout=STEP_TIMEOUT, report_lines=20,
        )
    finally:
        c.sh(f"mv {PTH_FILE} {COV_DIR}/", check=False)
        c.start_admin_httpd()
        c.wait_ready()
    os.makedirs(out_dir, exist_ok=True)
    coverage_json = os.path.join(out_dir, "coverage.json")
    with open(coverage_json, "w", encoding="utf-8") as f:
        f.write(raw)
    measured = json.loads(raw)["files"]
    changed = changed_lines(git_diff(worktree, base))
    changed_json = os.path.join(out_dir, "changed_lines.json")
    with open(changed_json, "w", encoding="utf-8") as f:
        json.dump(changed, f, indent=2, sort_keys=True)
    files, not_measured = intersect(changed, measured)
    html_dir = os.path.join(out_dir, HTML_DIR)
    unpack(packed, html_dir)
    pages = html_pages(html_dir, files)
    txt = os.path.join(out_dir, "coverage.txt")
    with open(txt, "w", encoding="utf-8") as f:
        f.write(report(files, not_measured, worktree, list(measured)))
    page = os.path.join(out_dir, "coverage.html")
    with open(page, "w", encoding="utf-8") as f:
        f.write(render(files, changed, not_measured, base=base, worktree=worktree,
                       pages=pages))
    return CoverageResult(
        files, not_measured, coverage_json, txt, changed_json, page, html_dir,
    )


def unpack(packed: str, dest: str) -> None:
    """Extract the base64 tar.gz of coverage's html folder into dest. Only plain
    files directly named inside the archive are written, page names unchanged."""
    os.makedirs(dest, exist_ok=True)
    data = base64.b64decode(packed)
    with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as tar:
        for m in tar.getmembers():
            name = os.path.normpath(m.name)
            if not m.isfile() or name.startswith(("..", "/")):
                continue
            target = os.path.join(dest, name)
            os.makedirs(os.path.dirname(target), exist_ok=True)
            with open(target, "wb") as f:
                f.write(tar.extractfile(m).read())


INDEX_LINK_RE = re.compile(r'<td class="name left"><a href="([^"]+)">([^<]+)</a>')


def html_pages(html_dir: str, files: dict) -> dict[str, str]:
    """worktree path -> href of coverage's page for it, read from coverage's own
    index.html (the page names are hashed, not guessed)."""
    try:
        with open(os.path.join(html_dir, "index.html"), encoding="utf-8") as f:
            index = {name: href for href, name in INDEX_LINK_RE.findall(f.read())}
    except OSError:
        return {}
    pages = {}
    for path in files:
        href = lookup(index, container_path(path) or "")
        if href:
            pages[path] = f"{HTML_DIR}/{href}"
    return pages


def git_diff(worktree: str, base: str) -> str:
    out = subprocess.run(
        ["git", "-C", worktree, "diff", "-U0", f"{base}..HEAD"],
        capture_output=True, text=True, check=True,
    )
    return out.stdout


FILE_RE = re.compile(r"^\+\+\+ b/(.+)$")
HUNK_RE = re.compile(r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,(\d+))? @@")


def changed_lines(diff: str) -> dict[str, list[int]]:
    """Added and changed line numbers per file from `git diff -U0`. A hunk that
    only deletes (+N,0) adds none, a deleted file has no `+++ b/` line."""
    out: dict[str, list[int]] = {}
    path = None
    for line in diff.splitlines():
        if line.startswith("+++ "):
            m = FILE_RE.match(line)
            path = m.group(1) if m else None
        elif path and (m := HUNK_RE.match(line)):
            start, count = int(m.group(1)), int(m.group(2) or 1)
            if count:
                out.setdefault(path, []).extend(range(start, start + count))
    return out


def container_path(path: str) -> str | None:
    """Where the deploy puts the worktree file, by its directory and basename."""
    base = CONTAINER_DIRS.get(os.path.dirname(path))
    return f"{base}/{os.path.basename(path)}" if base else None


def lookup(table: dict, path: str):
    """table[path], or the entry of a key that ends with path (coverage may record
    a longer path), or of a key path ends with (a shorter, relative one)."""
    if path in table:
        return table[path]
    for key, value in table.items():
        if path and (key.endswith(path) or path.endswith("/" + key.lstrip("/"))):
            return value
    return None


def intersect(
    changed: dict[str, list[int]], measured: dict,
) -> tuple[dict[str, FileCoverage], list[str]]:
    files, not_measured = {}, []
    for path, lines in sorted(changed.items()):
        if not path.endswith(".py"):
            continue
        data = lookup(measured, container_path(path) or "")
        if data is None:
            not_measured.append(path)
            continue
        ran, missing = set(data["executed_lines"]), set(data["missing_lines"])
        files[path] = FileCoverage(
            run=len(ran & set(lines)),
            total=len((ran | missing) & set(lines)),
            missing=sorted(missing & set(lines)),
            ran=sorted(ran & set(lines)),
        )
    return files, not_measured


def report(
    files: dict[str, FileCoverage], not_measured: list[str], worktree: str,
    measured_keys: list[str] = (),
) -> str:
    out = []
    if not files and not not_measured:
        out.append("no changed .py files")
    if not files and not_measured:
        shown = ", ".join(measured_keys[:3]) or "none"
        out.append(
            f"NOTE: no measured file matched the {len(not_measured)} changed .py "
            f"file(s). coverage.json has {len(measured_keys)} file(s), e.g.: {shown}"
        )
    for path, fc in files.items():
        out.append(f"{path}: changed lines run {fc.run}/{fc.total}")
        try:
            with open(os.path.join(worktree, path), encoding="utf-8") as f:
                source = f.read().splitlines()
        except OSError:
            source = []
        out += [
            f"  {n}: {source[n - 1] if n <= len(source) else ''}" for n in fc.missing
        ]
    out += [f"{path}: not measured" for path in not_measured]
    return "\n".join(out) + "\n"
