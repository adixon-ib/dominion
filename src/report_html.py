"""report.html: the results of a Ticket run as one self-contained page.

render(ticket) builds it from the same section data as report.md. Inline CSS
only, no JS and no external assets, light and dark via prefers-color-scheme.
Every piece of text is escaped. Each nios_log capture shows at most its last
200 lines, the full text is in its nios_log/N.log file. Highlighting of log lines
is specific to the oplog tag (oplog.TAG); other lines are shown as plain text.
"""

from __future__ import annotations

import html
import os

from . import oplog
from .container import NiosContainer
from .ticket import (
    NIOS_LOG_FILE,
    PACKAGE_DIR,
    _collapse,
    _dump,
    _fmt,
    _git_rev,
    _show,
    _state_text,
)

CSS = """
:root{--bg:#f6f7f9;--card:#fff;--fg:#1c2128;--muted:#667085;--line:#d8dee6;
--ok:#1a7f37;--okbg:#dafbe1;--bad:#cf222e;--badbg:#ffebe9;--warn:#9a6700;
--warnbg:#fff8c5;--skip:#57606a;--skipbg:#eaeef2;--code:#f0f2f5}
@media (prefers-color-scheme:dark){:root{--bg:#0d1117;--card:#161b22;--fg:#e6edf3;
--muted:#8b949e;--line:#30363d;--ok:#56d364;--okbg:#12361d;--bad:#ff7b72;
--badbg:#4a1b1b;--warn:#e3b341;--warnbg:#3b2f0b;--skip:#9da7b3;--skipbg:#262c35;
--code:#0d1117}}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--fg);font:15px/1.45 system-ui,
-apple-system,"Segoe UI",Roboto,"Helvetica Neue",Arial,sans-serif}
main{max-width:960px;margin:0 auto;padding:16px}
h1{font-size:1.4rem;margin:0 0 8px;overflow-wrap:anywhere}
h2{font-size:1.05rem;margin:0;display:flex;flex-wrap:wrap;gap:8px;
align-items:center;overflow-wrap:anywhere}
.card{background:var(--card);border:1px solid var(--line);border-radius:8px;
padding:12px 14px;margin:12px 0}
.badge,.pill{display:inline-block;border-radius:999px;padding:1px 10px;
font-size:.8rem;font-weight:600;border:1px solid currentColor}
.PASS,.ok{color:var(--ok)}.FAIL,.bad{color:var(--bad)}.SKIP{color:var(--skip)}
.warn{color:var(--warn)}
.pill.PASS{background:var(--okbg)}.pill.FAIL{background:var(--badbg)}
.pill.SKIP{background:var(--skipbg)}
.dur,.muted{color:var(--muted);font-size:.85rem;font-weight:400}
.meta{display:grid;grid-template-columns:max-content 1fr;gap:2px 12px;
margin:8px 0 0;font-size:.9rem}
.meta dt{color:var(--muted)}.meta dd{margin:0;overflow-wrap:anywhere}
.counts{margin:6px 0 0;color:var(--muted)}
.counts a{color:inherit}
ul{margin:6px 0;padding-left:0;list-style:none}
li{padding:2px 0;overflow-wrap:anywhere}
li.failed{background:var(--badbg);border-left:3px solid var(--bad);
padding:4px 8px;margin:3px 0;border-radius:4px}
pre,code,.log{font:12.5px/1.4 ui-monospace,SFMono-Regular,Menlo,Consolas,
monospace}
pre{background:var(--code);border:1px solid var(--line);border-radius:6px;
padding:8px;margin:6px 0;overflow-x:auto;white-space:pre-wrap;
overflow-wrap:anywhere}
pre.error{border-color:var(--bad)}
.label{font-weight:600;font-size:.85rem;margin:10px 0 2px;color:var(--muted);
text-transform:uppercase;letter-spacing:.03em}
.logline{background:var(--code);border-left:3px solid var(--line);
padding:3px 8px;margin:2px 0;border-radius:4px;overflow-wrap:anywhere}
.logline.o-ok{border-left-color:var(--ok)}
.logline.o-rejected{border-left-color:var(--warn)}
.logline.o-error{border-left-color:var(--bad)}
.logline .badge{margin-right:6px;font-size:.72rem;padding:0 7px}
.logline .raw{display:block;color:var(--muted)}
details{margin-top:10px}summary{cursor:pointer;font-weight:600}
.call{margin:8px 0;border-top:1px solid var(--line);padding-top:6px}
.call b{overflow-wrap:anywhere}
"""

def _e(v) -> str:
    return html.escape(str(v))


def _log_line(raw: str) -> str:
    line = oplog.parse([raw])[0]
    f = line.fields
    if not f:
        return f'<div class="logline"><code>{_e(raw)}</code></div>'
    outcome = f.get("outcome", "")
    cls = {"ok": "ok", "rejected": "warn"}.get(outcome, "bad" if outcome else "")
    badges = []
    for key in ("fn", "step", "event"):
        if key in f:
            badges.append(f'<span class="badge">{key} {_e(f[key])}</span>')
    if outcome:
        badges.append(f'<span class="badge {cls}">{_e(outcome)}</span>')
    for key in ("rid", "dur"):
        if key in f:
            badges.append(f'<span class="badge muted">{key} {_e(f[key])}</span>')
    kind = outcome if outcome in ("ok", "rejected", "error") else "other"
    box = f"logline o-{kind}" if outcome else "logline"
    return (f'<div class="{_e(box)}">{"".join(badges)}'
            f'<code class="raw">{_e(raw)}</code></div>')


def _logs(logs: list[dict], output_dir: str) -> str:
    keep = NiosContainer.REPORT_LOG_LINES
    out = []
    for cap in logs:
        lines = cap["lines"]
        n = len(lines)
        out.append(f'<div class="label">infoblox.log: {_e(cap["pattern"])} '
                   f'({n} line{"" if n == 1 else "s"})</div>')
        if n > keep:
            where = os.path.relpath(cap["path"], output_dir) if cap.get("path") else ""
            note = f"... {n - keep} more lines" + (f", see {where}" if where else "")
            out.append(f'<div class="muted">{_e(note)}</div>')
        out += [_log_line(raw) for raw in lines[-keep:]] or [
            '<div class="muted">no matching lines</div>'
        ]
    return "".join(out)


def _checks(checks: list[dict]) -> str:
    bad = [c for c in checks if not c["ok"]]
    good = [c for c in checks if c["ok"]]
    items = [
        f'<li class="failed"><b class="bad">FAIL</b> {_e(c["label"])}: expected '
        f'<code>{_e(_fmt(c["expected"]))}</code>, got '
        f'<code>{_e(_fmt(c["got"]))}</code></li>'
        for c in bad
    ] + [
        f'<li><b class="ok">ok</b> {_e(c["label"])} '
        f'<span class="muted">(got {_e(_fmt(c["got"]))})</span></li>'
        for c in good
    ]
    return f'<div class="label">Checks</div><ul>{"".join(items)}</ul>'


def _call(call: dict, n: int) -> str:
    times = f" (x{n}, last shown)" if n > 1 else ""
    if call["kind"] == "wapi":
        head = f'{call["method"]} {call["path"]} -> {call["status"]}{times}'
        parts = [f"<b>{_e(head)}</b>"]
        if call["body"] is not None:
            parts.append(f'<pre>{_e(_dump(call["body"]))}</pre>')
        if call["response"]:
            parts.append(f'<pre>{_e(call["response"])}</pre>')
    else:
        head = f"$ {call['cmd']} -> exit {call['rc']}{times}"
        parts = [f"<b>{_e(head)}</b>"]
        for key in ("stdout", "stderr"):
            if call[key].strip():
                parts.append(f'<pre>{_e(call[key].rstrip())}</pre>')
    return f'<div class="call">{"".join(parts)}</div>'


def _calls(calls: list[dict]) -> str:
    body = "".join(_call(c, n) for c, n in _collapse(calls))
    return f"<details><summary>Calls ({len(calls)})</summary>{body}</details>"


def _card(sec: dict, output_dir: str) -> str:
    dur = "" if sec["status"] == "SKIP" else f'{sec["duration"]:.1f}s'
    parts = [
        f'<section class="card"><h2><span>{_e(sec["name"])}</span>'
        f'<span class="pill {_e(sec["status"])}">{_e(sec["status"])}</span>'
        f'<span class="dur">{_e(dur)}</span></h2>'
    ]
    if sec["state"]:
        parts.append(f'<div class="muted">State: {_e(_state_text(sec["state"]))}</div>')
    if sec["reason"]:
        parts.append(f'<div class="label">Skipped</div><div>{_e(sec["reason"])}</div>')
    if sec["pre"]:
        pre = "".join(f"<li>{_e(lb)}: {_e(st)}</li>" for lb, st in sec["pre"])
        parts.append(f'<div class="label">Preconditions</div><ul>{pre}</ul>')
    if sec["checks"]:
        parts.append(_checks(sec["checks"]))
    if sec["status"] == "FAIL" and sec["error"]:
        parts.append(f'<div class="label">Error</div>'
                     f'<pre class="error">{_e(sec["error"])}</pre>')
    if sec.get("logs"):
        parts.append(_logs(sec["logs"], output_dir))
    if sec["calls"]:
        parts.append(_calls(sec["calls"]))
    return "".join(parts) + "</section>"


def render(t) -> str:
    """The page for a Ticket whose run has finished."""
    run = t.run
    verdict = t._verdict()
    meta = [
        ("Host", t.host or "(none)"), ("Build", t.build),
        ("Dominion", _git_rev(PACKAGE_DIR)),
        ("Run", os.path.basename(t.output_dir.rstrip(os.sep))),
    ]
    nios_log = ""
    if t._nios_log_text is not None:
        nios_log = (f'<div class="counts"><a href="{_e(NIOS_LOG_FILE)}">'
                    f"{_e(NIOS_LOG_FILE)}</a> ({t._nios_log_count} lines)</div>")
    head = (
        f'<header><h1>{_e(t.name)} <span class="badge {verdict}">{verdict}</span>'
        f'</h1><div class="counts">{run._passed} passed, {run._failed} failed, '
        f'{run._skipped} skipped</div>{nios_log}<dl class="meta">'
        + "".join(f"<dt>{k}</dt><dd>{_e(v)}</dd>" for k, v in meta)
        + "</dl></header>"
    )
    body = [head] + [_card(s, t.output_dir) for s in t._sections]
    state = sorted(vars(t.state).items())
    if state:
        items = "".join(f"<li>{_e(k)}: <code>{_e(_show(v))}</code></li>"
                        for k, v in state)
        body.append(f'<section class="card"><h2>State at exit</h2><ul>{items}</ul>'
                    "</section>")
    for title, notes, cls in (("Warnings", t._warnings, "warn"),
                              ("Notes", t._notes, "muted")):
        if notes:
            items = "".join(f"<li>{_e(n)}</li>" for n in notes)
            body.append(f'<section class="card"><h2 class="{cls}">{title}</h2>'
                        f"<ul>{items}</ul></section>")
    return (
        '<!doctype html><html lang="en"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1">'
        f"<title>{_e(t.name)} report</title><style>{CSS}</style></head>"
        f'<body><main>{"".join(body)}</main></body></html>\n'
    )
