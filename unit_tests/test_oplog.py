"""Unit tests for oplog.py: parse and the Checks.oplog_* methods, on real lines
from NIOS-111696-oplog (infoblox.log of vm-48-10, 2026-11-06)."""

from dominion import Checks
from dominion.src import oplog

P = "[2026/11/06 19:07:{s}] (196217 /usr/bin/httpd) one/token_bundle.py:{n} {fn}(): "
REJECT = (
    P.format(s="00.277", n=217, fn="apply")
    + "ipc:bundle rid=6a0638b9 user=admin fn=apply outcome=rejected "
    'code=one.token_bundle.NOT_ENABLED reason="IPc mode is not enabled. '
    'Use the enable function to enable it." dur=0.000s'
)
STEP = (
    P.format(s="26.047", n=186, fn="upload")
    + "ipc:bundle rid=bac4b248 step=ValidateTokens outcome=ok dur=12.566s"
)
STAGED = (
    P.format(s="26.047", n=203, fn="upload")
    + "ipc:bundle rid=bac4b248 event=staged uid=1ffdfd17a3544186b6a691885fe30601 "
    "start=2026-04-17 expiry=2029-09-17 total=999999999"
)
UPLOAD = (
    P.format(s="26.047", n=175, fn="upload")
    + "ipc:bundle rid=bac4b248 user=admin fn=upload outcome=ok dur=12.567s"
)
LINES = [REJECT, STEP, STAGED, UPLOAD]
N_VIOLATIONS = 4
N_UNTAGGED = 2


def run(check):
    """Run check(c) on a Checks with a sink: (checks, sink)."""
    sink = []
    c = Checks("t", sink)
    check(c)
    return c, sink


def test_parse_real_lines():
    rows = oplog.parse(LINES, "ipc:bundle")
    assert rows[0].file == "one/token_bundle.py"
    assert rows[0].raw == REJECT
    assert rows[0].fields == {
        "rid": "6a0638b9", "user": "admin", "fn": "apply",
        "outcome": "rejected", "code": "one.token_bundle.NOT_ENABLED",
        "reason": "IPc mode is not enabled. Use the enable function to enable it.",
        "dur": "0.000s",
    }
    assert rows[2].fields["event"] == "staged"
    assert rows[2].fields["total"] == "999999999"


def test_parse_unquotes_escaped_quotes_and_empty_values():
    line = P.format(s="1", n=1, fn="f") + 'ipc:bundle rid=- reason="a \\"b\\" c=d" x=""'
    assert oplog.parse([line], "ipc:bundle")[0].fields == {
        "rid": "-", "reason": 'a "b" c=d', "x": "",
    }


def test_parse_line_without_tag_has_no_fields_or_file():
    row = oplog.parse(["no tag here rid=1"], "ipc:bundle")[0]
    assert (row.fields, row.file) == ({}, "")


def test_parse_passes_line_objects_through():
    row = oplog.parse([REJECT], "ipc:bundle")[0]
    assert oplog.parse([row], "ipc:bundle") == [row]


def test_summary_rejected_ok_and_returns_the_line():
    holder = {}

    def check(c):
        holder["s"] = c.oplog_summary(
            LINES, fn="apply", outcome="rejected", code_suffix="NOT_ENABLED",
        )

    c, sink = run(check)
    assert not c.failures
    assert holder["s"].fields["rid"] == "6a0638b9"
    assert any("code ends with NOT_ENABLED" in e["label"] for e in sink)


def test_summary_ok_checks_user_when_given():
    c, _ = run(lambda c: c.oplog_summary(LINES, fn="upload", outcome="ok",
                                         user="admin"))
    assert not c.failures
    c, _ = run(lambda c: c.oplog_summary(LINES, fn="upload", outcome="ok",
                                         user="root"))
    assert len(c.failures) == 1
    assert "user" in c.failures[0]


def test_summary_failures():
    bad = REJECT.replace("rid=6a0638b9", "rid=XYZ").replace("dur=0.000s", "dur=1")
    c, _ = run(lambda c: c.oplog_summary([bad], fn="apply", outcome="ok"))
    msgs = " | ".join(c.failures)
    assert "outcome: expected ok, got rejected" in msgs
    assert "rid 8 lowercase hex" in msgs
    assert "dur n.nnns" in msgs


def test_summary_not_exactly_one_line():
    c, _ = run(lambda c: c.oplog_summary([], fn="apply", outcome="ok"))
    assert "line count: expected 1, got 0" in c.failures[0]
    c, _ = run(lambda c: c.oplog_summary([UPLOAD, UPLOAD], fn="upload",
                                         outcome="ok"))
    assert "line count: expected 1, got 2" in c.failures[0]


def test_summary_empty_reason_fails():
    bad = REJECT.replace('reason="IPc mode is not enabled. '
                         'Use the enable function to enable it."', 'reason=""')
    c, _ = run(lambda c: c.oplog_summary(
        [bad], fn="apply", outcome="rejected", code_suffix="NOT_ENABLED"))
    assert any("reason non-empty" in f for f in c.failures)


def test_step_ok_and_missing():
    c, _ = run(lambda c: c.oplog_step(LINES, "bac4b248", "ValidateTokens"))
    assert not c.failures
    c, _ = run(lambda c: c.oplog_step(LINES, "bac4b248", "Nope"))
    assert any("line count: expected 1, got 0" in f for f in c.failures)
    c, _ = run(lambda c: c.oplog_step(LINES, "00000000", "ValidateTokens"))
    assert c.failures


def test_step_outcome_not_ok_fails():
    c, _ = run(lambda c: c.oplog_step(
        [STEP.replace("outcome=ok", "outcome=error")], "bac4b248", "ValidateTokens"))
    assert any("outcome: expected ok, got error" in f for f in c.failures)


def test_event_compares_str_of_wanted_values():
    holder = {}

    def check(c):
        holder["e"] = c.oplog_event(
            LINES, "bac4b248", "staged",
            uid="1ffdfd17a3544186b6a691885fe30601", start="2026-04-17",
            total=999999999,
        )

    c, _ = run(check)
    assert not c.failures
    assert holder["e"].fields["expiry"] == "2029-09-17"
    c, _ = run(lambda c: c.oplog_event(LINES, "bac4b248", "staged", total=5))
    assert any("total: expected 5, got 999999999" in f for f in c.failures)


def test_event_none_value_fails_against_missing_field():
    c, _ = run(lambda c: c.oplog_event(LINES, "bac4b248", "staged", gone=None))
    assert c.failures


def test_contract_passes_on_real_lines():
    c, _ = run(lambda c: c.oplog_contract(
        LINES, caller_file="token_bundle.py", secrets=["tok-not-in-lines"]))
    assert not c.failures


def test_contract_flags_each_violation():
    token = "A" * 50 + " trailing"
    leak = STEP.replace("outcome=ok", f"outcome=ok note={'A' * 40}")
    other = UPLOAD.replace("one/token_bundle.py", "one/other.py")
    badrid = STEP.replace("rid=bac4b248", "rid=BAD")
    tb = STEP + ' tb="Traceback"'
    c, _ = run(lambda c: c.oplog_contract(
        [leak, other, badrid, tb], caller_file="token_bundle.py", secrets=[token]))
    assert len(c.failures) == N_VIOLATIONS
    assert c.failures[0].startswith("all 4 lines from token_bundle.py")
    assert other in c.failures[0]
    assert c.failures[1].startswith("rid: all 4")
    assert c.failures[2].startswith("no secret in 4")
    assert c.failures[3].startswith("no tb= field")


def test_contract_line_without_tag_is_a_violation():
    c, _ = run(lambda c: c.oplog_contract(
        ["plain line"], caller_file="token_bundle.py"))
    assert len(c.failures) == N_UNTAGGED  # caller file and rid


def test_checks_record_into_the_sink():
    _, sink = run(lambda c: c.oplog_step(LINES, "bac4b248", "ValidateTokens"))
    assert [e["ok"] for e in sink] == [True, True, True]


def test_empty_contract_has_nothing_to_flag():
    c, _ = run(lambda c: c.oplog_contract([], caller_file="token_bundle.py"))
    assert not c.failures


def test_tag_argument_selects_the_tag_in_every_check():
    swap = [x.replace("ipc:bundle ", "other:tag ") for x in LINES]
    c, _ = run(lambda c: c.oplog_summary(
        swap, fn="upload", outcome="ok", tag="other:tag"))
    assert not c.failures
    c, _ = run(lambda c: c.oplog_step(swap, "bac4b248", "ValidateTokens",
                                      tag="other:tag"))
    assert not c.failures
    c, _ = run(lambda c: c.oplog_event(swap, "bac4b248", "staged",
                                       tag="other:tag", total=999999999))
    assert not c.failures
    c, _ = run(lambda c: c.oplog_contract(swap, caller_file="token_bundle.py",
                                          tag="other:tag"))
    assert not c.failures
    c, _ = run(lambda c: c.oplog_summary(swap, fn="upload", outcome="ok"))
    assert c.failures  # default tag finds nothing
