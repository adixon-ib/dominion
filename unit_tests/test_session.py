"""Unit tests for session.py: BaseSession, ShellSession, ConsoleSession.

No real pexpect spawn — `_child` is a FakeChild double, or pexpect.spawn
itself is monkeypatched to return one.
"""

import pexpect
import pytest

from nios_test import ConsoleSession, ShellSession
from nios_test.src.session import BaseSession

# -- BaseSession._clean() ---------------------------------------------------------

def test_clean_strips_ansi_codes():
    assert BaseSession._clean("\x1b[31mred text\x1b[0m") == "red text"


def test_clean_normalizes_crlf():
    assert BaseSession._clean("line1\r\nline2\r\n") == "line1\nline2"


# -- BaseSession is abstract -------------------------------------------------------

def test_base_session_cannot_be_instantiated_directly():
    with pytest.raises(TypeError):
        BaseSession(spawn_cmd="x", prompt_pattern=r"\$")


# -- send()/sendline() require an open session ------------------------------------

def test_sendline_without_open_raises():
    s = ShellSession(spawn_cmd="fake")
    with pytest.raises(RuntimeError):
        s.sendline("hi")


# -- expect() / expect_one_of() ---------------------------------------------------

def test_expect_returns_before_plus_after_and_updates_last_prompt(fake_child_factory):
    s = ShellSession(spawn_cmd="fake")
    s._child = fake_child_factory(script=[(0, "output text", "prompt$ ")])

    result = s.expect(r"\$")

    assert result == "output textprompt$ "
    assert s._last_prompt == "prompt$"


def test_expect_timeout_raises_timeout_error(fake_child_factory):
    s = ShellSession(spawn_cmd="fake")
    s._child = fake_child_factory(script=[pexpect.TIMEOUT("timed out")])
    with pytest.raises(TimeoutError):
        s.expect(r"\$")


def test_expect_eof_raises_runtime_error(fake_child_factory):
    s = ShellSession(spawn_cmd="fake")
    s._child = fake_child_factory(script=[pexpect.EOF("eof")])
    with pytest.raises(RuntimeError):
        s.expect(r"\$")


def test_expect_one_of_returns_matched_index(fake_child_factory):
    s = ShellSession(spawn_cmd="fake")
    s._child = fake_child_factory(script=[(1, "", "match2")])
    assert s.expect_one_of(["pat1", "pat2"]) == 1


def test_expect_one_of_timeout_raises_timeout_error(fake_child_factory):
    s = ShellSession(spawn_cmd="fake")
    s._child = fake_child_factory(script=[pexpect.TIMEOUT("timed out")])
    with pytest.raises(TimeoutError):
        s.expect_one_of(["pat1", "pat2"])


# -- run() -------------------------------------------------------------------------

def test_run_strips_echoed_command_from_output(fake_child_factory):
    s = ShellSession(spawn_cmd="fake")
    child = fake_child_factory(script=[(0, "echo hello\r\nhello\r\n", "user@host:~$ ")])
    s._child = child

    result = s.run("echo hello")

    assert result == "hello"
    assert ("sendline", "echo hello") in child.sent


def test_run_keeps_output_when_echo_not_present(fake_child_factory):
    s = ShellSession(spawn_cmd="fake")
    s._child = fake_child_factory(script=[(0, "some output\r\n", "user@host:~$ ")])

    result = s.run("something")

    assert result == "some output"


# -- ShellSession spawn_cmd construction --------------------------------------------

def test_shell_session_uses_spawn_cmd_as_given():
    s = ShellSession(spawn_cmd="docker exec -it balerion /bin/bash")
    assert s._spawn_cmd == "docker exec -it balerion /bin/bash"


# -- ConsoleSession spawn_cmd construction -------------------------------------------

def test_console_session_uses_spawn_cmd_as_given():
    s = ConsoleSession(
        spawn_cmd="docker exec -it balerion /infoblox/one/bin/serial_console",
    )
    assert s._spawn_cmd == "docker exec -it balerion /infoblox/one/bin/serial_console"


# -- ConsoleSession._open() login branching -------------------------------------------

def test_console_session_open_handles_login_prompt(monkeypatch, fake_child_factory):
    import nios_test.src.session as session_mod
    child = fake_child_factory(script=[
        (0, "", "login: "),
        (0, "", "Password: "),
        (0, "", "Infoblox > "),
    ])
    monkeypatch.setattr(session_mod.pexpect, "spawn", lambda *a, **k: child)

    s = session_mod.ConsoleSession(
        spawn_cmd="docker exec -it balerion /infoblox/one/bin/serial_console",
        user="admin", password="infoblox",
    )
    s._open()

    assert s._last_prompt == "Infoblox >"
    assert ("sendline", "admin") in child.sent
    assert ("sendline", "infoblox") in child.sent


def test_console_session_open_handles_password_only_prompt(
    monkeypatch, fake_child_factory,
):
    import nios_test.src.session as session_mod
    child = fake_child_factory(script=[
        (1, "", "Password: "),
        (0, "", "Infoblox > "),
    ])
    monkeypatch.setattr(session_mod.pexpect, "spawn", lambda *a, **k: child)

    s = session_mod.ConsoleSession(
        spawn_cmd="docker exec -it balerion /infoblox/one/bin/serial_console",
        password="infoblox",
    )
    s._open()

    assert ("sendline", "infoblox") in child.sent
    assert s._last_prompt == "Infoblox >"


def test_console_session_open_already_at_prompt_skips_auth(
    monkeypatch, fake_child_factory,
):
    import nios_test.src.session as session_mod
    child = fake_child_factory(script=[(2, "", "Infoblox > ")])
    monkeypatch.setattr(session_mod.pexpect, "spawn", lambda *a, **k: child)

    s = session_mod.ConsoleSession(
        spawn_cmd="docker exec -it balerion /infoblox/one/bin/serial_console",
    )
    s._open()

    assert child.sent == []
    assert s._last_prompt == "Infoblox >"
