"""Interactive, prompt-driven sessions over pexpect: ShellSession (docker exec
bash) and ConsoleSession (serial console / SSH login).

Use these when you need to hold a session open across multiple commands
and react to prompts inline.

For one-shot commands with a return code, use transport.py instead."""

from __future__ import annotations

import re
import time
from typing import TYPE_CHECKING, Union

import pexpect
from typing_extensions import override

from .constants import NIOS_CONSOLE_USER, NIOS_CONSOLE_PASSWORD
from .log import create_logger

if TYPE_CHECKING:
    from .log import NiosLogger

log = create_logger(name=__name__)

# Match ANSI escape codes (colors, formatting) for removal from terminal output
# to keep logs readable
_ANSI_RE = re.compile(r"\x1b\[\??[0-9;]*[a-zA-Z]")


class BaseSession:

    def __init__(
        self,
        spawn_cmd: str,
        prompt_pattern: str,
        logger: NiosLogger | None = None,
        timeout: float = 30,
    ):
        if type(self) is BaseSession:
            raise TypeError("BaseSession is abstract. Use ShellSession or ConsoleSession")
        self._spawn_cmd = spawn_cmd
        self._prompt_pattern = prompt_pattern
        self._logger = logger or log
        self._default_timeout = timeout
        self._child: pexpect.spawn | None = None
        self._last_prompt: str = ""

    def __enter__(self):
        self._open()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self._close()
        return False

    def _open(self) -> None:
        """Spawn the child process and wait for its first prompt.

        Internal — sessions are opened via the `with` statement, or by
        Container/Appliance's session factories.
        """
        self._logger.debug("Opening session: %s", self._spawn_cmd)
        self._child = pexpect.spawn(
            self._spawn_cmd,
            encoding="utf-8",
            timeout=int(self._default_timeout),
            echo=False,
        )
        self._child.expect(self._prompt_pattern, timeout=int(self._default_timeout))
        self._last_prompt = _ANSI_RE.sub("", self._child.after or "").strip()
        self._logger.debug("Session ready")

    def _close(self) -> None:
        """Try to exit the child process cleanly, then force-kill it.

        Internal — sessions are closed via the `with` statement, or by
        Container/Appliance's session factories. Safe to call even if the
        session is already closed or the process already died.
        """
        if self._child and self._child.isalive():
            self._logger.debug("Closing session")
            try:
                self._child.sendline("exit")
                self._child.expect(pexpect.EOF, timeout=5)
            except (pexpect.ExceptionPexpect, OSError):
                pass
            finally:
                self._child.close(force=True)
        self._child = None

    def sendline(self, text: str) -> None:
        """Write text plus a newline; doesn't wait for a response.

        Pair with expect()/expect_one_of() to wait for a specific prompt
        afterward — for interactive, multi-step flows like a setup
        or CLI wizard (`set network` for example).

        Raises:
            RuntimeError: If the session is not open.
        """
        if self._child is None:
            raise RuntimeError("Session is not open")
        self._logger.info("%s %s", self._last_prompt or ">>>", text)
        # Delegates to pexpect.spawn's own sendline(), which writes to the pty.
        self._child.sendline(text)

    def expect(
        self,
        pattern: Union[str, list[str]],
        timeout: float | None = None,
    ) -> str:
        """Block until pattern appears in the session's output.

        Use this for interactive, multi-step flows (e.g. a CLI wizard) where
        you need to wait for a specific prompt before sending the next
        input. run() only waits for the session's own shell prompt, not
        arbitrary text.

        Returns:
            Everything received up to and including the match.

        Raises:
            TimeoutError: If pattern doesn't appear in time.
            RuntimeError: If the process exits unexpectedly.
        """
        if self._child is None:
            raise RuntimeError("Session is not open")
        timeout = int(timeout or self._default_timeout)
        try:
            self._child.expect(pattern, timeout=timeout)
        except pexpect.TIMEOUT:
            received = self._child.before or ""
            self._logger.debug("TIMEOUT waiting for %r, got: %r", pattern, received)
            raise TimeoutError(
                f"Timed out after {timeout}s waiting for {pattern!r}. "
                f"Received: {received!r}"
            )
        except pexpect.EOF:
            received = self._child.before or ""
            raise RuntimeError(f"Process exited unexpectedly. Received: {received!r}")

        before = self._child.before or ""
        after = self._child.after or ""
        output = before + after
        self._last_prompt = _ANSI_RE.sub("", after).strip()
        self._logger.verbose("Matched %r after %d bytes", pattern, len(before))
        return output

    def expect_one_of(self, patterns: list[str], timeout: float | None = None) -> int:
        """Wait for any one of several possible prompts.

        Use this when an interactive session can branch. For example a CLI
        wizard whose next question depends on system state, like
        `set network` asking about DSCP, IPv6, or grid membership depending
        on the build.

        Returns:
            The index into patterns of whichever one matched. Compare
            against it to decide which branch to follow.

        Raises:
            TimeoutError: If none of the patterns appear in time.
            RuntimeError: If the process exits unexpectedly.
        """
        if self._child is None:
            raise RuntimeError("Session is not open")
        timeout = int(timeout or self._default_timeout)
        try:
            idx = self._child.expect(patterns, timeout=timeout)
        except pexpect.TIMEOUT:
            received = self._child.before or ""
            self._logger.debug("TIMEOUT waiting for any of %r, got: %r", patterns, received)
            raise TimeoutError(
                f"Timed out after {timeout}s waiting for any of {patterns!r}. "
                f"Received: {received!r}"
            )
        except pexpect.EOF:
            received = self._child.before or ""
            raise RuntimeError(f"Process exited unexpectedly. Received: {received!r}")
        after = self._child.after or ""
        self._last_prompt = _ANSI_RE.sub("", after).strip()
        self._logger.verbose("Matched index %d of %r after %d bytes", idx, patterns, len(self._child.before or ""))
        return idx

    @staticmethod
    def _clean(text: str) -> str:
        text = _ANSI_RE.sub("", text)
        text = text.replace("\r\n", "\n").replace("\r", "")
        return text.strip()

    def run(self, cmd: str, timeout: float | None = None) -> str:
        """Run cmd and return its output.

        For a single command that goes straight back to the prompt with no
        follow-up questions, like "show status".

        Args:
            timeout: Defaults to the session's default timeout if None.

        Returns:
            The command's output, with the echoed command line stripped.
        """
        self.sendline(cmd)
        self.expect(self._prompt_pattern, timeout=timeout)
        text = self._child.before or ""
        text = self._clean(text)
        lines = text.split("\n")
        if lines and cmd in lines[0]:
            lines = lines[1:]

        result = "\n".join(lines).strip()
        if result:
            self._logger.info("%s", result)
        if self._last_prompt:
            self._logger.info("%s", self._last_prompt)
        return result


class ShellSession(BaseSession):

    PROMPT_PATTERN = r"\S+@\S+:[^#$]*[#$]\s*"

    # Unique sentinel appended after a command (see run() below) to smuggle
    # its exit code back through plain terminal output, since pexpect has
    # no built-in way to read a command's $?.
    _RC_MARKER = "___NIOS_TEST_RC___"

    def __init__(
        self,
        spawn_cmd: str,
        logger: NiosLogger | None = None,
        timeout: float = 30,
    ):
        super().__init__(
            spawn_cmd=spawn_cmd,
            prompt_pattern=self.PROMPT_PATTERN,
            logger=logger,
            timeout=timeout,
        )
        self._last_exit_code: int | None = None

    @property
    def last_exit_code(self) -> int | None:
        return self._last_exit_code

    @override
    def run(self, cmd: str, timeout: float | None = None) -> str:
        """Run cmd and return its output.

        For a single command that goes straight back to the prompt with no
        follow-up questions, like "pgrep -f token_aggregator".
        
        Use when a failing exit code doesn't need
        to abort the script, either because it's an expected outcome you'll
        branch on (check `last_exit_code` yourself), or because you
        don't need to verify success at all. Use run_with_check() when
        failure should abort the script.

        Args:
            timeout: Defaults to the session's default timeout if None.

        Returns:
            The command's output, with the echoed command line stripped.

        Example:
            run("pgrep -f token_aggregator") against a running process (A)
            vs. one that isn't (B):

            A: process running   -> result="1234", last_exit_code=0
            B: process not found -> result="",     last_exit_code=1

            Both are normal outcomes here. Check last_exit_code or the
            output to decide, rather than treating B as an error.
        """
        self.sendline(f"{cmd}; echo {self._RC_MARKER}:$?")
        self.expect(self._prompt_pattern, timeout=timeout)
        text = self._child.before or ""
        text = self._clean(text)
        lines = text.split("\n")
        if lines and cmd in lines[0]:
            lines = lines[1:]

        rc = None
        if lines and lines[-1].startswith(self._RC_MARKER):
            rc = int(lines[-1].split(":", 1)[1])
            lines = lines[:-1]
        self._last_exit_code = rc

        result = "\n".join(lines).strip()
        if result:
            self._logger.info("%s", result)
        if self._last_prompt:
            self._logger.info("%s", self._last_prompt)
        return result

    def run_with_check(self, cmd: str, timeout: float | None = None) -> str:
        """Like run(), but raises if cmd exits nonzero.

        Use when success is required and failure should abort the script.
        If the exit code can't be determined (e.g. a backgrounded command),
        treats it as success rather than failing closed.

        Returns:
            The command's output.

        Raises:
            RuntimeError: If cmd exits with a nonzero status.
        """
        result = self.run(cmd, timeout=timeout)
        if self._last_exit_code not in (None, 0):
            raise RuntimeError(
                f"Command failed (rc={self._last_exit_code}): {cmd!r}. "
                f"Output: {result!r}"
            )
        return result


class ConsoleSession(BaseSession):

    PROMPT_PATTERN = r"Infoblox\s*>"
    LOGIN_PATTERN = r"login:"
    PASSWORD_PATTERN = r"[Pp]assword:"

    def __init__(
        self,
        spawn_cmd: str,
        user: str = NIOS_CONSOLE_USER,
        password: str = NIOS_CONSOLE_PASSWORD,
        logger: NiosLogger | None = None,
        timeout: float = 60,
    ):
        self._user = user
        self._password = password

        super().__init__(
            spawn_cmd=spawn_cmd,
            prompt_pattern=self.PROMPT_PATTERN,
            logger=logger,
            timeout=timeout,
        )

    @override
    def _open(self) -> None:
        """Spawn the console and authenticate only if prompted.

        Internal — sessions are opened via the `with` statement, or by
        Container/Appliance's session factories. Handles three cases
        depending on connection type and state: a full login prompt (serial
        console), a password-only prompt (SSH, since the username is
        already in the connection string), or already sitting at the
        Infoblox> prompt with no auth needed.
        """
        self._logger.debug("Opening console session: %s", self._spawn_cmd)
        self._child = pexpect.spawn(
            self._spawn_cmd,
            encoding="utf-8",
            timeout=int(self._default_timeout),
            echo=False,
        )

        index = self._child.expect(
            [self.LOGIN_PATTERN, self.PASSWORD_PATTERN, self.PROMPT_PATTERN],
            timeout=int(self._default_timeout),
        )

        if index == 0:
            self._logger.debug("Login prompt detected, authenticating")
            self._child.sendline(self._user)
            self._child.expect(self.PASSWORD_PATTERN, timeout=int(self._default_timeout))
            self._child.sendline(self._password)
            self._child.expect(self.PROMPT_PATTERN, timeout=int(self._default_timeout))
        elif index == 1:
            self._logger.debug("Password prompt detected, authenticating")
            self._child.sendline(self._password)
            self._child.expect(self.PROMPT_PATTERN, timeout=int(self._default_timeout))

        self._last_prompt = _ANSI_RE.sub("", self._child.after or "").strip()
        self._logger.debug("Console session ready (Infoblox > prompt)")

    @override
    def _close(self) -> None:
        if self._child and self._child.isalive():
            self._logger.debug("Closing console session")
            try:
                self._child.sendline("exit")
                self._child.expect(pexpect.EOF, timeout=5)
            except (pexpect.ExceptionPexpect, OSError):
                pass
            finally:
                self._child.close(force=True)
        self._child = None


class ConsoleConnectSession(ConsoleSession):
    """Out-of-band console via lab console server. No login flow needed
    (session is already authenticated), disconnect with ~."""

    def __init__(
        self,
        spawn_cmd: str,
        user: str = NIOS_CONSOLE_USER,
        password: str = NIOS_CONSOLE_PASSWORD,
        logger: NiosLogger | None = None,
        timeout: float = 60,
    ):
        super().__init__(
            spawn_cmd=spawn_cmd,
            user=user,
            password=password,
            logger=logger,
            timeout=timeout,
        )

    @override
    def _open(self) -> None:
        self._logger.debug("Opening console_connect session: %s", self._spawn_cmd)
        self._child = pexpect.spawn(
            self._spawn_cmd,
            encoding="utf-8",
            timeout=int(self._default_timeout),
            echo=False,
        )

        # Wait for the telnet connection to establish and any stale
        # data (prompts from previous sessions, console server
        # artifacts) to arrive through the serial path.
        time.sleep(2)

        # Drain everything without sending anything new. Each
        # sendline("") would add another prompt to the queue, making
        # the stale-prompt problem worse.
        drained = []
        try:
            while True:
                chunk = self._child.read_nonblocking(size=4096, timeout=1)
                drained.append(chunk)
        except (pexpect.TIMEOUT, pexpect.EOF):
            pass
        if drained:
            self._logger.debug("Drained stale data: %r", "".join(drained))

        # One sendline to get a fresh prompt. If a stray '%' is in
        # NIOS's input buffer, our '\n' completes it as a command
        # (producing error + prompt), which the expect consumes.
        self._child.sendline("")
        index = self._child.expect(
            [self.PROMPT_PATTERN, self.LOGIN_PATTERN],
            timeout=int(self._default_timeout),
        )
        if index == 1:
            self._logger.debug("Login prompt detected, authenticating")
            self._child.sendline(self._user)
            self._child.expect(self.PASSWORD_PATTERN, timeout=int(self._default_timeout))
            self._child.sendline(self._password)
            self._child.expect(self.PROMPT_PATTERN, timeout=int(self._default_timeout))

        self._last_prompt = _ANSI_RE.sub("", self._child.after or "").strip()
        self._logger.debug("Console connect session ready")

    @override
    def run(self, cmd: str, timeout: float | None = None) -> str:
        output = super().run(cmd, timeout=timeout)
        if "unknown command '%'" in output:
            # Console server injected a stray '%' into NIOS's input.
            # NIOS discards remaining input after the error, so our
            # command never executed. Retry on a clean buffer.
            self._logger.debug("Stray '%%' absorbed, retrying: %s", cmd)
            output = super().run(cmd, timeout=timeout)
        return output

    @override
    def _close(self) -> None:
        if self._child and self._child.isalive():
            self._logger.debug("Disconnecting console_connect session")
            try:
                self._child.sendline("\r~.")
                self._child.expect(pexpect.EOF, timeout=5)
            except (pexpect.ExceptionPexpect, OSError):
                pass
            finally:
                self._child.close(force=True)
        self._child = None
