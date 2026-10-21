"""Appliance-to-agent assignments: one JSON file, atomic writes under a file lock.

Appliances may reference agents absent from `agents` (files written before the
registry existed). Readers must tolerate that.
"""

from __future__ import annotations

import fcntl
import json
import os
import re
import subprocess
import tempfile
from contextlib import contextmanager
from datetime import datetime, timezone

from .lab_info import get_lab_info

DEFAULT_PATH = "/blox/adixon1/resources/assignments.json"
_NAME_RE = re.compile(r"^[a-z][a-z0-9-]*$")
STATUSES = ("active", "idle")


class AssignmentError(Exception):
    pass


def _path() -> str:
    return os.environ.get("DOMINION_ASSIGNMENTS", DEFAULT_PATH)


def _read(path: str) -> dict:
    try:
        with open(path) as f:
            data = json.load(f)
    except FileNotFoundError:
        return {"appliances": {}, "agents": {}}
    except ValueError as e:
        raise AssignmentError(f"{path} is corrupt or empty: {e}") from e
    if not isinstance(data, dict) or not all(isinstance(data.setdefault(k, {}), dict) for k in ("appliances", "agents")):
        raise AssignmentError(f"{path} is malformed: expected an object with 'appliances' and 'agents' objects")
    for n, a in data["agents"].items():
        if not isinstance(a, dict) or not isinstance(a.get("role"), str):
            raise AssignmentError(f"{path} is malformed: agent {n!r} must be an object with a string 'role'")
    for h, a in data["appliances"].items():
        if not isinstance(a, dict) or not isinstance(a.get("agent"), str):
            raise AssignmentError(f"{path} is malformed: appliance {h!r} must be an object with a string 'agent'")
    return data


@contextmanager
def _locked(path: str):
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path + ".lock", "w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        yield


def _write(path: str, data: dict) -> None:
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(path) or ".", suffix=".tmp")
    try:
        os.fchmod(fd, 0o644)
        with os.fdopen(fd, "w") as f:
            json.dump(data, f, indent=2, sort_keys=True)
            f.write("\n")
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except BaseException:
        os.unlink(tmp)
        raise


def load() -> dict:
    return _read(_path())


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def agents(role: str | None = None) -> dict:
    return {n: a for n, a in load()["agents"].items() if role is None or a["role"] == role.lower()}


def register_agent(name: str, role: str | None = None, task: str | None = None, status: str | None = None,
                   session: str | None = None, force: bool = False) -> None:
    """Create or update. role is required on create; changing an existing role needs force.
    None leaves a field unchanged on update (defaults: status active, others null); task "" clears it."""
    name = name.lower()
    role = role.lower() if role else None
    if not _NAME_RE.match(name):
        raise AssignmentError(f"invalid agent name {name!r}: must match {_NAME_RE.pattern}")
    if role is not None and not _NAME_RE.match(role):
        raise AssignmentError(f"invalid role {role!r}: must match {_NAME_RE.pattern}")
    if status is not None and status not in STATUSES:
        raise AssignmentError(f"invalid status {status!r}: must be one of {', '.join(STATUSES)}")
    path = _path()
    with _locked(path):
        data = _read(path)
        rec = data["agents"].get(name)
        if rec is None:
            if role is None:
                raise AssignmentError(f"agent {name} is not registered: role is required to create it")
            rec = data["agents"][name] = {"task": None, "status": "active", "session": None, "role": role}
        elif role is not None and role != rec["role"]:
            if not force:
                raise AssignmentError(f"agent {name} has role {rec['role']!r}, not {role!r} (use force to change it)")
            rec["role"] = role
        for k, v in (("task", task), ("status", status), ("session", session)):
            if v is not None:
                rec[k] = v or None
        rec["updated_at"] = _now()
        _write(path, data)


def unregister_agent(name: str, force: bool = False) -> None:
    name = name.lower()
    path = _path()
    with _locked(path):
        data = _read(path)
        if name not in data["agents"]:
            raise AssignmentError(f"agent {name} is not registered")
        held = sorted(h for h, a in data["appliances"].items() if a["agent"] == name)
        if held and not force:
            raise AssignmentError(f"agent {name} still holds {', '.join(held)} (use force to release them)")
        for h in held:
            del data["appliances"][h]
        del data["agents"][name]
        _write(path, data)


def assign(host: str, agent: str, task: str | None = None, force: bool = False) -> None:
    agent = agent.lower()
    try:
        info = get_lab_info(host)
    except (subprocess.CalledProcessError, FileNotFoundError, subprocess.TimeoutExpired) as e:
        raise AssignmentError(f"get_lab_info failed for {host!r}: {e}") from e
    # get_lab_info exits 0 with an ERROR line on stdout for unknown hosts.
    if not info.get("HOST"):
        raise AssignmentError(f"unknown host {host!r}: get_lab_info returned no HOST")
    host = info["HOST"].lower()
    path = _path()
    with _locked(path):
        data = _read(path)
        if agent not in data["agents"]:
            raise AssignmentError(f"agent {agent!r} is not registered: run `assignments agent add {agent} --role <role>`")
        cur = data["appliances"].get(host)
        if cur and cur["agent"] != agent and not force:
            raise AssignmentError(f"{host} already assigned to {cur['agent']} (use force)")
        if cur and cur["agent"] == agent and not task:
            return
        data["appliances"][host] = {
            "agent": agent,
            "task": task or None,
            "assigned_at": _now(),
        }
        _write(path, data)


def release(host: str, agent: str | None = None, force: bool = False) -> None:
    host = host.lower()
    agent = agent.lower() if agent else None
    path = _path()
    with _locked(path):
        data = _read(path)
        cur = data["appliances"].get(host)
        if cur is None:
            raise AssignmentError(f"{host} is not assigned")
        if agent and cur["agent"] != agent and not force:
            raise AssignmentError(f"{host} is assigned to {cur['agent']}, not {agent} (use force)")
        del data["appliances"][host]
        _write(path, data)


def assigned_appliances(agent: str) -> list[str]:
    agent = agent.lower()
    return sorted(h for h, a in load()["appliances"].items() if a["agent"] == agent)


def require_assigned(host: str, agent: str) -> None:
    host, agent = host.lower(), agent.lower()
    cur = load()["appliances"].get(host)
    if not cur or cur["agent"] != agent:
        holder = cur["agent"] if cur else "nobody"
        raise AssignmentError(f"{host} is not assigned to {agent} (held by {holder})")
