"""Unit tests for session.py: BaseSession, ShellSession, ConsoleSession.

No real pexpect spawn — `_child` is a FakeChild double, or pexpect.spawn
itself is monkeypatched to return one.
"""

import pexpect
import pytest

from nios_test import ConsoleSession, ShellSession
from nios_test.session import BaseSession


# -- BaseSession._clean() ---------------------------------------------------------

def test_clean_strips_ansi_codes():
    assert BaseSession._clean("\x1b[31mred text\x1b[0m") == "red text"


def test_clean_normalizes_crlf():
    assert BaseSession._clean("line1\r\nline2\r\n") == "line1\nline2"


# -- send()/sendline() require an open session ------------------------------------

def test_send_without_open_raises():
    s = BaseSession(spawn_cmd="x", prompt_pattern=r"\$")
    with pytest.raises(RuntimeError):
        s.send("hi")


def test_sendline_without_open_raises():
    s = BaseSession(spawn_cmd="x", prompt_pattern=r"\$")
    with pytest.raises(RuntimeError):
        s.sendline("hi")


# -- expect() / expect_one_of() ---------------------------------------------------

def test_expect_returns_before_plus_after_and_updates_last_prompt(fake_child_factory):
    s = BaseSession(spawn_cmd="x", prompt_pattern=r"\$")
    s._child = fake_child_factory(script=[(0, "output text", "prompt$ ")])

    result = s.expect(r"\$")

    assert result == "output textprompt$ "
    assert s._last_prompt == "prompt$"


def test_expect_timeout_raises_timeout_error(fake_child_factory):
    s = BaseSession(spawn_cmd="x", prompt_pattern=r"\$")
    s._child = fake_child_factory(script=[pexpect.TIMEOUT("timed out")])
    with pytest.raises(TimeoutError):
        s.expect(r"\$")


def test_expect_eof_raises_runtime_error(fake_child_factory):
    s = BaseSession(spawn_cmd="x", prompt_pattern=r"\$")
    s._child = fake_child_factory(script=[pexpect.EOF("eof")])
    with pytest.raises(RuntimeError):
        s.expect(r"\$")


def test_expect_one_of_returns_matched_index(fake_child_factory):
    s = BaseSession(spawn_cmd="x", prompt_pattern=r"\$")
    s._child = fake_child_factory(script=[(1, "", "match2")])
    assert s.expect_one_of(["pat1", "pat2"]) == 1


def test_expect_one_of_timeout_raises_timeout_error(fake_child_factory):
    s = BaseSession(spawn_cmd="x", prompt_pattern=r"\$")
    s._child = fake_child_factory(script=[pexpect.TIMEOUT("timed out")])
    with pytest.raises(TimeoutError):
        s.expect_one_of(["pat1", "pat2"])


# -- run() -------------------------------------------------------------------------

def test_run_strips_echoed_command_from_output(fake_child_factory):
    s = BaseSession(spawn_cmd="x", prompt_pattern=r"\$")
    child = fake_child_factory(script=[(0, "echo hello\r\nhello\r\n", "user@host:~$ ")])
    s._child = child

    result = s.run("echo hello")

    assert result == "hello"
    assert ("sendline", "echo hello") in child.sent


def test_run_keeps_output_when_echo_not_present(fake_child_factory):
    s = BaseSession(spawn_cmd="x", prompt_pattern=r"\$")
    s._child = fake_child_factory(script=[(0, "some output\r\n", "user@host:~$ ")])

    result = s.run("something")

    assert result == "some output"


# -- ShellSession spawn_cmd construction --------------------------------------------

def test_shell_session_spawn_cmd_without_prefix():
    s = ShellSession(container_name="balerion")
    assert s._spawn_cmd == "docker exec -it balerion /bin/bash"


def test_shell_session_spawn_cmd_with_ssh_prefix_inserts_tt():
    prefix = "ssh -o StrictHostKeyChecking=no root@10.35.211.4"
    s = ShellSession(container_name="nios", transport_cmd_prefix=prefix)
    assert s._spawn_cmd == (
        "ssh -tt -o StrictHostKeyChecking=no root@10.35.211.4 docker exec -it nios /bin/bash"
    )


# -- ConsoleSession spawn_cmd construction -------------------------------------------

def test_console_session_local_container_spawn_cmd():
    s = ConsoleSession(container_name="balerion", local=True)
    assert s._spawn_cmd == "docker exec -it balerion /infoblox/one/bin/serial_console"


def test_console_session_ssh_spawn_cmd():
    s = ConsoleSession(host="vm-04-89", port=22, user="admin")
    assert s._spawn_cmd == "ssh -o StrictHostKeyChecking=no -p 22 admin@vm-04-89"


def test_console_session_container_with_ssh_prefix_inserts_tt():
    prefix = "ssh -o StrictHostKeyChecking=no root@10.35.211.4"
    s = ConsoleSession(container_name="nios", transport_cmd_prefix=prefix)
    assert s._spawn_cmd == (
        "ssh -tt -o StrictHostKeyChecking=no root@10.35.211.4 "
        "docker exec -it nios /infoblox/one/bin/serial_console"
    )


# -- ConsoleSession.open() login branching -------------------------------------------

def test_console_session_open_handles_login_prompt(monkeypatch, fake_child_factory):
    import nios_test.session as session_mod
    child = fake_child_factory(script=[
        (0, "", "login: "),
        (0, "", "Password: "),
        (0, "", "Infoblox > "),
    ])
    monkeypatch.setattr(session_mod.pexpect, "spawn", lambda *a, **k: child)

    s = session_mod.ConsoleSession(container_name="balerion", local=True, user="admin", password="infoblox")
    s.open()

    assert s._last_prompt == "Infoblox >"
    assert ("sendline", "admin") in child.sent
    assert ("sendline", "infoblox") in child.sent


def test_console_session_open_handles_password_only_prompt(monkeypatch, fake_child_factory):
    import nios_test.session as session_mod
    child = fake_child_factory(script=[
        (1, "", "Password: "),
        (0, "", "Infoblox > "),
    ])
    monkeypatch.setattr(session_mod.pexpect, "spawn", lambda *a, **k: child)

    s = session_mod.ConsoleSession(container_name="balerion", local=True, password="infoblox")
    s.open()

    assert ("sendline", "infoblox") in child.sent
    assert s._last_prompt == "Infoblox >"


def test_console_session_open_already_at_prompt_skips_auth(monkeypatch, fake_child_factory):
    import nios_test.session as session_mod
    child = fake_child_factory(script=[(2, "", "Infoblox > ")])
    monkeypatch.setattr(session_mod.pexpect, "spawn", lambda *a, **k: child)

    s = session_mod.ConsoleSession(container_name="balerion", local=True)
    s.open()

    assert child.sent == []
    assert s._last_prompt == "Infoblox >"
