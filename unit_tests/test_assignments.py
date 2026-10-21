"""Unit tests for assignments: JSON store, conflict/force, atomic writes."""

import json
import multiprocessing
import os
import stat
import subprocess
import sys

import pytest

from dominion.src import assignments as A


@pytest.fixture(autouse=True)
def store(tmp_path, monkeypatch):
    path = tmp_path / "res" / "assignments.json"
    monkeypatch.setenv("DOMINION_ASSIGNMENTS", str(path))
    monkeypatch.setattr(A, "get_lab_info", lambda h: {"HOST": h.lower()} if h.lower().startswith("vm-") else {})
    return path


def reg(*names, role="conquest"):
    for n in names:
        A.register_agent(n, role)


def test_assign_and_query(store):
    reg("conquest")
    A.assign("vm-01", "conquest", task="T-1")
    rec = json.loads(store.read_text())["appliances"]["vm-01"]
    assert rec["agent"] == "conquest" and rec["task"] == "T-1"
    assert rec["assigned_at"].endswith("Z")
    assert A.assigned_appliances("conquest") == ["vm-01"]
    assert A.assigned_appliances("other") == []
    A.require_assigned("vm-01", "conquest")


def test_unknown_host_rejected(store):
    reg("conquest")
    before = store.read_text()
    with pytest.raises(A.AssignmentError):
        A.assign("bogus", "conquest")
    assert store.read_text() == before


def test_conflict_and_force(store):
    reg("conquest", "other")
    A.assign("vm-01", "conquest")
    with pytest.raises(A.AssignmentError):
        A.assign("vm-01", "other")
    A.assign("vm-01", "conquest", task="T-2")
    assert A.load()["appliances"]["vm-01"]["task"] == "T-2"
    A.assign("vm-01", "other", force=True)
    assert A.assigned_appliances("other") == ["vm-01"]


def test_release(store):
    reg("conquest")
    A.assign("vm-01", "conquest")
    A.release("vm-01")
    assert A.load()["appliances"] == {}
    with pytest.raises(A.AssignmentError):
        A.release("vm-01")


def test_require_assigned_raises(store):
    reg("conquest", "other")
    with pytest.raises(A.AssignmentError):
        A.require_assigned("vm-01", "conquest")
    A.assign("vm-01", "other")
    with pytest.raises(A.AssignmentError):
        A.require_assigned("vm-01", "conquest")


def test_load_missing_file(store):
    assert A.load() == {"appliances": {}, "agents": {}}


def test_write_is_atomic(store, monkeypatch):
    reg("conquest")
    A.assign("vm-01", "conquest")
    before = store.read_text()

    def boom(src, dst):
        raise OSError("replace failed")

    monkeypatch.setattr(os, "replace", boom)
    with pytest.raises(OSError):
        A.assign("vm-02", "conquest")
    assert store.read_text() == before
    assert [p.name for p in store.parent.iterdir() if p.suffix == ".tmp"] == []


def test_hostname_case_normalized(store):
    reg("conquest", "other")
    A.assign("VM-04-89", "conquest")
    assert list(json.loads(store.read_text())["appliances"]) == ["vm-04-89"]
    with pytest.raises(A.AssignmentError):
        A.assign("vm-04-89", "other")
    A.require_assigned("VM-04-89", "conquest")
    A.release("VM-04-89")
    assert A.load()["appliances"] == {}


def test_lab_info_failures_are_assignment_errors(store, monkeypatch):
    reg("conquest")
    def timeout(h):
        raise subprocess.TimeoutExpired("get_lab_info", 30)

    monkeypatch.setattr(A, "get_lab_info", timeout)
    with pytest.raises(A.AssignmentError, match="get_lab_info failed"):
        A.assign("vm-01", "conquest")


@pytest.mark.parametrize("content", ["", "{not json", "[]", '{"appliances": []}', '{"agents": []}'])
def test_corrupt_store_raises_assignment_error(store, content):
    store.parent.mkdir()
    store.write_text(content)
    with pytest.raises(A.AssignmentError):
        A.load()
    with pytest.raises(A.AssignmentError):
        A.assign("vm-01", "conquest")
    assert store.read_text() == content


def test_empty_object_store_is_empty(store):
    store.parent.mkdir()
    store.write_text("{}")
    assert A.load() == {"appliances": {}, "agents": {}}
    reg("conquest")
    A.assign("vm-01", "conquest")


def test_release_agent_check(store):
    reg("conquest", "other")
    A.assign("vm-01", "conquest")
    with pytest.raises(A.AssignmentError):
        A.release("vm-01", agent="other")
    assert A.assigned_appliances("conquest") == ["vm-01"]
    A.release("vm-01", agent="other", force=True)
    A.assign("vm-01", "conquest")
    A.release("vm-01", agent="conquest")
    assert A.load()["appliances"] == {}


def test_file_mode_0644(store):
    reg("conquest")
    A.assign("vm-01", "conquest")
    assert stat.S_IMODE(store.stat().st_mode) == 0o644


def _assign_many(prefix, n):
    for i in range(n):
        A.assign(f"vm-{prefix}-{i}", prefix)


def test_concurrent_assign_both_persist(store):
    reg("a", "b")
    ctx = multiprocessing.get_context("fork")
    procs = [ctx.Process(target=_assign_many, args=(p, 15)) for p in ("a", "b")]
    for p in procs:
        p.start()
    for p in procs:
        p.join(60)
        assert p.exitcode == 0
    assert len(A.load()["appliances"]) == 30


def test_register_creates_and_updates(store):
    A.register_agent("Aegon", "Conquest", task="T-1", session="s1")
    rec = A.agents()["aegon"]
    assert rec["role"] == "conquest" and rec["task"] == "T-1" and rec["status"] == "active"
    assert rec["session"] == "s1" and rec["updated_at"].endswith("Z")
    A.register_agent("aegon", "conquest", status="idle")
    rec = A.agents()["aegon"]
    assert rec["status"] == "idle" and rec["task"] == "T-1" and rec["session"] == "s1"
    A.register_agent("aegon", "conquest", task="")
    assert A.agents()["aegon"]["task"] is None


@pytest.mark.parametrize("name", ["", "1a", "-a", "a_b", "a b", "a.b"])
def test_invalid_agent_name(store, name):
    with pytest.raises(A.AssignmentError, match="invalid agent name"):
        A.register_agent(name, "conquest")
    assert not store.exists()


def test_invalid_role_and_status(store):
    with pytest.raises(A.AssignmentError):
        A.register_agent("aegon", "Bad Role")
    with pytest.raises(A.AssignmentError):
        A.register_agent("aegon", "conquest", status="busy")
    assert not store.exists()


def test_assign_requires_registered_agent(store):
    with pytest.raises(A.AssignmentError, match="assignments agent add visenya"):
        A.assign("vm-01", "visenya")
    assert not store.exists()
    reg("visenya")
    A.assign("vm-01", "Visenya")
    assert A.assigned_appliances("visenya") == ["vm-01"]


def test_agents_role_filter(store):
    reg("aegon", "visenya")
    reg("sam", role="review")
    assert sorted(A.agents()) == ["aegon", "sam", "visenya"]
    assert sorted(A.agents("conquest")) == ["aegon", "visenya"]
    assert sorted(A.agents("review")) == ["sam"]
    assert A.agents("nope") == {}


def test_unregister(store):
    reg("aegon")
    A.unregister_agent("AEGON")
    assert A.agents() == {}
    with pytest.raises(A.AssignmentError, match="not registered"):
        A.unregister_agent("aegon")


def test_unregister_holding_appliances(store):
    reg("aegon", "visenya")
    A.assign("vm-01", "aegon")
    A.assign("vm-02", "aegon")
    A.assign("vm-03", "visenya")
    with pytest.raises(A.AssignmentError, match="vm-01, vm-02"):
        A.unregister_agent("aegon")
    assert "aegon" in A.agents() and A.assigned_appliances("aegon") == ["vm-01", "vm-02"]
    A.unregister_agent("aegon", force=True)
    assert "aegon" not in A.agents()
    assert A.assigned_appliances("aegon") == []
    assert A.assigned_appliances("visenya") == ["vm-03"]


def test_old_format_file_without_agents_loads(store):
    store.parent.mkdir()
    old = {"appliances": {"vm-01": {"agent": "conquest", "task": None, "assigned_at": "2026-01-01T00:00:00Z"}}}
    store.write_text(json.dumps(old))
    assert A.load() == {**old, "agents": {}}
    assert A.assigned_appliances("conquest") == ["vm-01"]
    reg("conquest")
    assert json.loads(store.read_text())["appliances"] == old["appliances"]
    A.release("vm-01")


def test_agent_case_normalized_everywhere(store):
    reg("conquest", "other")
    A.assign("vm-01", "conquest")
    assert A.assigned_appliances("CONQUEST") == ["vm-01"]
    A.require_assigned("vm-01", "Conquest")
    with pytest.raises(A.AssignmentError):
        A.release("vm-01", agent="OTHER")
    A.release("vm-01", agent="CoNqUeSt")
    assert A.load()["appliances"] == {}
    A.assign("vm-01", "conquest")
    A.release("vm-01", agent=None)
    assert A.load()["appliances"] == {}


def test_register_role_optional_on_update_and_required_on_create(store):
    with pytest.raises(A.AssignmentError, match="role is required"):
        A.register_agent("aegon")
    assert not store.exists()
    reg("aegon")
    A.register_agent("aegon", status="idle")
    A.register_agent("aegon", "CONQUEST", task="T-1")
    rec = A.agents()["aegon"]
    assert rec["role"] == "conquest" and rec["status"] == "idle" and rec["task"] == "T-1"


def test_register_role_change_refused_unless_forced(store):
    reg("aegon")
    before = store.read_text()
    with pytest.raises(A.AssignmentError, match="use force"):
        A.register_agent("aegon", "review")
    assert store.read_text() == before
    A.register_agent("aegon", "review", force=True)
    assert A.agents()["aegon"]["role"] == "review"


def test_task_normalized_and_reassign_keeps_task(store):
    reg("conquest", "other")
    A.assign("vm-01", "conquest", task="")
    assert A.load()["appliances"]["vm-01"]["task"] is None
    A.assign("vm-01", "conquest", task="T-1")
    data = json.loads(store.read_text())
    data["appliances"]["vm-01"]["assigned_at"] = "2020-01-01T00:00:00Z"
    store.write_text(json.dumps(data))
    A.assign("vm-01", "conquest")
    rec = A.load()["appliances"]["vm-01"]
    assert rec["task"] == "T-1" and rec["assigned_at"] == "2020-01-01T00:00:00Z"
    A.assign("vm-01", "other", force=True)
    assert A.load()["appliances"]["vm-01"]["task"] is None


@pytest.mark.parametrize("content", [
    '{"agents": {"a": {}}}',
    '{"agents": {"a": null}}',
    '{"agents": {"a": {"role": 1}}}',
    '{"appliances": {"vm-01": {}}}',
    '{"appliances": {"vm-01": null}}',
    '{"appliances": {"vm-01": {"agent": null}}}',
])
def test_malformed_records_raise_assignment_error(store, content):
    store.parent.mkdir()
    store.write_text(content)
    for call in (A.load, A.agents, lambda: A.assigned_appliances("a"), lambda: A.require_assigned("vm-01", "a"),
                 lambda: A.release("vm-01"), lambda: A.unregister_agent("a"), lambda: A.assign("vm-01", "a")):
        with pytest.raises(A.AssignmentError):
            call()
    assert store.read_text() == content


def _register_many(n):
    for i in range(n):
        A.register_agent(f"r{i}", "conquest")


def test_concurrent_register_and_assign_both_persist(store):
    reg("a")
    ctx = multiprocessing.get_context("fork")
    procs = [ctx.Process(target=_register_many, args=(15,)), ctx.Process(target=_assign_many, args=("a", 15))]
    for p in procs:
        p.start()
    for p in procs:
        p.join(60)
        assert p.exitcode == 0
    data = A.load()
    assert len(data["agents"]) == 16 and len(data["appliances"]) == 15


def test_unregister_refusal_leaves_file_unchanged(store):
    reg("aegon")
    A.assign("vm-01", "aegon")
    before = store.read_text()
    with pytest.raises(A.AssignmentError):
        A.unregister_agent("aegon")
    with pytest.raises(A.AssignmentError):
        A.unregister_agent("nobody")
    assert store.read_text() == before


CLI = os.path.join(os.path.dirname(__file__), "..", "bin", "assignments")


def cli(*args):
    return subprocess.run([sys.executable, CLI, *args], capture_output=True, text=True, timeout=60)


def test_cli_agent_add_list_rm(store):
    r = cli("agent", "add", "aegon", "--role", "conquest", "--task", "T-1")
    assert r.returncode == 0 and r.stdout == ""
    r = cli("agent", "list")
    assert r.returncode == 0 and "aegon" in r.stdout and "T-1" in r.stdout
    assert json.loads(cli("agent", "list", "--json").stdout)["aegon"]["role"] == "conquest"
    assert cli("agent", "list", "--role", "review", "--json").stdout.strip() == "{}"
    assert cli("agent", "add", "aegon", "--status", "idle").returncode == 0
    assert json.loads(cli("agent", "list", "--json").stdout)["aegon"]["role"] == "conquest"
    r = cli("agent", "add", "aegon", "--role", "review")
    assert r.returncode == 1 and r.stderr.startswith("error:")
    assert cli("agent", "add", "aegon", "--role", "review", "--force").returncode == 0
    assert cli("agent", "rm", "aegon").returncode == 0
    r = cli("agent", "rm", "aegon")
    assert r.returncode == 1 and r.stderr.startswith("error:") and r.stdout == ""


def test_cli_add_without_role_on_create_fails(store):
    r = cli("agent", "add", "aegon")
    assert r.returncode == 1 and "error:" in r.stderr
    assert not store.exists()


def test_cli_list_and_release(store):
    reg("conquest", "other")
    A.assign("vm-01", "conquest", task="T-1")
    A.assign("vm-02", "other")
    r = cli("list", "--agent", "CONQUEST")
    assert r.returncode == 0 and "vm-01" in r.stdout and "vm-02" not in r.stdout
    data = json.loads(cli("list", "--json").stdout)
    assert sorted(data) == ["agents", "appliances"] and sorted(data["agents"]) == ["conquest", "other"]
    assert sorted(data["appliances"]) == ["vm-01", "vm-02"]
    r = cli("release", "vm-01", "--agent", "other")
    assert r.returncode == 1 and r.stderr.startswith("error:")
    assert cli("release", "vm-01", "--agent", "Conquest").returncode == 0
    r = cli("release", "vm-01")
    assert r.returncode == 1 and "not assigned" in r.stderr


def test_cli_corrupt_store_is_error_exit(store):
    store.parent.mkdir()
    store.write_text('{"agents": {"a": {}}}')
    r = cli("agent", "list")
    assert r.returncode == 1 and r.stderr.startswith("error:") and "Traceback" not in r.stderr
