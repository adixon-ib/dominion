"""Parse and check NIOS `oplog` request lines in infoblox.log.

The line format comes from a NIOS change that is not merged yet (a NIOS PR),
so it may change. Real lines look like

    one/token_bundle.py:217 apply(): ipc:bundle rid=6a0638b9 user=admin fn=apply
        outcome=rejected code=one.token_bundle.NOT_ENABLED reason="..." dur=0.000s

(one line in infoblox.log, after a timestamp and process prefix). One summary
line per request (`fn=`, `outcome=`), optional `step=` and `event=` lines
sharing the request's `rid=`. The check functions here record through
`Checks.expect`, so they land in report.md. Use them via the `Checks.oplog_*`
methods.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass, field

TAG = "ipc:bundle"
SECRET_CHUNK = 40  # leading characters of a secret that must not appear in any line
PAIR_RE = re.compile(r'([^\s=]+)=("(?:\\.|[^"\\])*"|[^\s"]*)')
CALLER_RE = re.compile(r"(\S+):\d+\s+\S+\(\):\s*$")
RID_RE = re.compile(r"^[0-9a-f]{8}$")
DUR_RE = re.compile(r"^\d+\.\d{3}s$")


@dataclass
class Line:
    raw: str
    fields: dict[str, str] = field(default_factory=dict)
    file: str = ""  # source file in the line prefix, e.g. one/token_bundle.py


def parse(lines: Iterable[str | Line], tag: str = TAG) -> list[Line]:
    """One Line per input line. fields are the key=value pairs after the first
    `tag ` (quoted values unquoted, \\" becomes "). A line without the tag has
    no fields and no file. Line items pass through."""
    out = []
    for raw in lines:
        if isinstance(raw, Line):
            out.append(raw)
            continue
        head, sep, rest = raw.partition(tag + " ")
        if not sep:
            out.append(Line(raw))
            continue
        pairs = {
            k: v[1:-1].replace('\\"', '"') if v.startswith('"') else v
            for k, v in PAIR_RE.findall(rest)
        }
        m = CALLER_RE.search(head)
        out.append(Line(raw, pairs, m.group(1) if m else ""))
    return out


def _one(c, label: str, found: list[Line]) -> Line:
    c.expect(f"{label}: line count", 1, len(found))
    return found[0] if len(found) == 1 else Line("")


def _expect_rid(c, label: str, f: dict) -> None:
    c.expect(f"{label}: rid 8 lowercase hex", "[0-9a-f]{8}", f.get("rid", ""),
             ok=bool(RID_RE.match(f.get("rid", ""))))


def _expect_dur(c, label: str, f: dict) -> None:
    c.expect(f"{label}: dur n.nnns", "n.nnns", f.get("dur", ""),
             ok=bool(DUR_RE.match(f.get("dur", ""))))


def check_summary(  # noqa: PLR0913
    c, lines, *, fn: str, outcome: str, code_suffix: str | None = None,
    user: str | None = None, tag: str = TAG,
) -> Line:
    """Exactly one summary line for fn. Checks outcome, rid, dur, and user
    (when given). With code_suffix: code ends with it and reason is non-empty."""
    label = f"summary fn={fn}"
    s = _one(c, label, [x for x in parse(lines, tag) if x.fields.get("fn") == fn])
    f = s.fields
    c.expect(f"{label}: outcome", outcome, f.get("outcome"))
    _expect_rid(c, label, f)
    _expect_dur(c, label, f)
    if user is not None:
        c.expect(f"{label}: user", user, f.get("user"))
    if code_suffix is not None:
        c.expect(f"{label}: code ends with {code_suffix}", f"*{code_suffix}",
                 f.get("code", ""), ok=f.get("code", "").endswith(code_suffix))
        c.expect(f"{label}: reason non-empty", True, bool(f.get("reason")))
    return s


def check_count(
    c, lines, n: int, *, fn: str | None = None, tag: str = TAG,
) -> None:
    """Exactly n summary lines (lines with fn=), only those for fn when given."""
    label = "summary lines" if fn is None else f"summary fn={fn} lines"
    got = [x for x in parse(lines, tag)
           if "fn" in x.fields and (fn is None or x.fields["fn"] == fn)]
    c.expect(f"{label}: count", n, len(got))


def check_step(c, lines, rid: str, name: str, *, tag: str = TAG) -> Line:
    """Exactly one step=name line of request rid, outcome ok, with a duration."""
    label = f"step={name} rid={rid}"
    s = _one(c, label, [
        x for x in parse(lines, tag)
        if x.fields.get("rid") == rid and x.fields.get("step") == name
    ])
    c.expect(f"step={name}: outcome", "ok", s.fields.get("outcome"))
    _expect_dur(c, f"step={name}", s.fields)
    return s


def check_event(
    c, lines, rid: str, name: str, *, tag: str = TAG, **want,
) -> Line:
    """Exactly one event=name line of request rid. Each key in want must equal
    the str() of the given value. Fields named tag, name or rid cannot be
    asserted through want."""
    ev = _one(c, f"event={name} rid={rid}", [
        x for x in parse(lines, tag)
        if x.fields.get("rid") == rid and x.fields.get("event") == name
    ])
    for key, value in want.items():
        c.expect(f"event={name}: {key}", str(value), ev.fields.get(key))
    return ev


CONTRACT_KINDS = ("caller", "rid", "secret", "tb")


def check_contract(  # noqa: PLR0913
    c, lines, *, caller_file: str, secrets: Iterable[str] = (), tag: str = TAG,
    labels: dict[str, str] | None = None,
) -> None:
    """Over all lines: written by caller_file, rid format, no secret and no
    tb= (traceback) field. One check each; got is the list of offending lines.

    labels maps a check kind to a prefix, rendered as "<prefix>: <label>", e.g.
    {"caller": "criterion 7"}. Kinds: caller (lines from caller_file), rid (rid
    format), secret (no secret in a line), tb (no tb= field). An unknown kind
    raises ValueError."""
    labels = labels or {}
    unknown = sorted(set(labels) - set(CONTRACT_KINDS))
    if unknown:
        raise ValueError(
            f"unknown oplog_contract label kinds {unknown}, use {CONTRACT_KINDS}"
        )

    def name(kind: str, text: str) -> str:
        return f"{labels[kind]}: {text}" if kind in labels else text

    rows = parse(lines, tag)
    chunks = [s.split()[0][:SECRET_CHUNK] for s in secrets if s.split()]
    n = len(rows)
    c.expect(name("caller", f"all {n} lines from {caller_file}"), [],
             [x.raw for x in rows if not x.file.endswith(caller_file)])
    c.expect(name("rid", f"rid: all {n} lines 8 lowercase hex"), [],
             [x.raw for x in rows if not RID_RE.match(x.fields.get("rid", ""))])
    c.expect(name("secret", f"no secret in {n} lines"), [],
             [x.raw for x in rows if any(k in x.raw for k in chunks)])
    c.expect(name("tb", f"no tb= field in {n} lines"), [],
             [x.raw for x in rows if "tb" in x.fields])
