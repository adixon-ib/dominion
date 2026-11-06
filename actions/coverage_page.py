"""coverage.html: the changed hunks of a coverage session, green where they ran
and red where they did not. One self-contained page: inline CSS (the report.html
style), no JS, light and dark.

Each hunk is the changed lines plus CONTEXT lines on each side, overlapping or
adjacent hunks merged so no line is shown twice. Two <details> per hunk hold
MORE further lines above and below, already rendered, and never reach into a
neighbouring hunk or repeat a line. The hunk header links to coverage's own
page for the file: coverage 6.x numbers its source lines with id="t<lineno>"
(verified in coverage 6.2 htmlfiles/pyfile.html).
"""

from __future__ import annotations

from dominion.src.report_html import CSS, _e

CONTEXT = 3
MORE = 20
HTML_DIR = "coverage_html"

EXTRA_CSS = """
table.code{border-collapse:collapse;width:100%;margin:4px 0}
table.code td{padding:0 6px;vertical-align:top}
table.code td.n{color:var(--muted);text-align:right;width:1%;white-space:nowrap;
user-select:none}
table.code pre{margin:0;padding:0;border:0;background:none}
tr.run{background:var(--okbg)}tr.miss{background:var(--badbg)}
tr.ctx{background:var(--code);color:var(--muted)}
a.hh{font-weight:600;overflow-wrap:anywhere}
.out li{color:var(--muted)}
"""


def hunks(changed: list[int], total: int) -> list[tuple[int, int, list[int]]]:
    """(first shown line, last shown line, changed lines in it) per hunk, with
    CONTEXT lines each side, clipped to 1..total, overlapping or touching
    windows merged."""
    out: list[list] = []
    for n in sorted(set(changed)):
        lo, hi = max(1, n - CONTEXT), min(total, n + CONTEXT)
        if out and lo <= out[-1][1] + 1:
            out[-1][1] = max(out[-1][1], hi)
            out[-1][2].append(n)
        else:
            out.append([lo, hi, [n]])
    return [(lo, hi, lines) for lo, hi, lines in out]


def _rows(source: list[str], lo: int, hi: int, classes: dict[int, str]) -> str:
    return "<table class=\"code\">" + "".join(
        f'<tr class="{classes.get(n, "ctx")}"><td class="n">{n}</td>'
        f"<td><pre>{_e(source[n - 1])}</pre></td></tr>"
        for n in range(lo, hi + 1)
    ) + "</table>"


def _more(label: str, source, lo: int, hi: int) -> str:
    if lo > hi:
        return ""
    return (f"<details><summary>show {hi - lo + 1} more lines {label}</summary>"
            f"{_rows(source, lo, hi, {})}</details>")


def _file(path, fc, changed, source, page) -> str:
    pct = f" ({round(100 * fc.run / fc.total)}%)" if fc.total else ""
    link = f' <a href="{_e(page)}">coverage page</a>' if page else ""
    parts = [
        f'<section class="card"><h2><span>{_e(path)}</span>'
        f'<span class="pill {"PASS" if fc.run == fc.total else "FAIL"}">'
        f"changed lines run {fc.run}/{fc.total}{_e(pct)}</span>{link}</h2>"
    ]
    classes = {n: "miss" for n in fc.missing} | {n: "run" for n in fc.ran}
    spans = hunks(changed, len(source))
    shown_to = 0  # last line rendered so far, extras included
    for i, (lo, hi, lines) in enumerate(spans):
        first = lines[0]
        next_start = spans[i + 1][0] if i + 1 < len(spans) else len(source) + 1
        above_from = max(shown_to + 1, lo - MORE)
        below_to = min(next_start - 1, hi + MORE)
        shown_to = max(hi, below_to)
        head = f"{path}:{first}" + (f"-{lines[-1]}" if lines[-1] != first else "")
        anchor = f'<span class="hh">{_e(head)}</span>'
        if page:
            anchor = f'<a class="hh" href="{_e(page)}#t{first}">{_e(head)}</a>'
        parts += [
            f'<div class="label">{anchor}</div>',
            _more("above", source, above_from, lo - 1),
            _rows(source, lo, hi, classes),
            _more("below", source, hi + 1, below_to),
        ]
    return "".join(parts) + "</section>"


def render(  # noqa: PLR0913
    files, changed, not_measured, *, base: str, source, pages,
) -> str:
    """files: path -> FileCoverage, changed: path -> changed lines, not_measured:
    changed .py files without data, source: path -> its lines (or None), pages:
    path -> href of coverage's page for the file (relative to this page), or
    missing."""
    run = sum(fc.run for fc in files.values())
    total = sum(fc.total for fc in files.values())
    pct = f" ({round(100 * run / total)}%)" if total else ""
    other = [p for p in sorted(changed) if p not in files and p not in not_measured]
    head = (
        f"<header><h1>Coverage of the changed lines</h1>"
        f'<div class="counts">changed lines run {run}/{total}{_e(pct)}</div>'
        f'<dl class="meta"><dt>Range</dt><dd>{_e(base)}...HEAD</dd></dl>'
    )
    out_of_scope = not_measured + other
    if out_of_scope:
        items = "".join(f"<li>{_e(p)}</li>" for p in out_of_scope)
        head += (f'<div class="label">Changed, not measured (out of scope)</div>'
                 f'<ul class="out">{items}</ul>')
    body = [head + "</header>"]
    for path, fc in files.items():
        lines = source(path)
        if lines is None:
            body.append(f'<section class="card"><h2>{_e(path)}</h2>'
                        '<div class="muted">source not readable</div></section>')
            continue
        body.append(_file(path, fc, changed.get(path, []), lines, pages.get(path)))
    return (
        '<!doctype html><html lang="en"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1">'
        f"<title>Coverage of the changed lines</title><style>{CSS}{EXTRA_CSS}</style>"
        f'</head><body><main>{"".join(body)}</main></body></html>\n'
    )
