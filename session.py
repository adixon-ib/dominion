from __future__ import annotations

import logging
import re
from typing import Union

import pexpect

from .constants import NIOS_CONSOLE_USER, NIOS_CONSOLE_PASSWORD

log = logging.getLogger(__name__)

_ANSI_RE = re.compile(r"\x1b\[\??[0-9;]*[a-zA-Z]")


class BaseSession:

    def __init__(
        self,
        spawn_cmd: str,
        prompt_pattern: str,
        logger: logging.Logger | None = None,
        timeout: float = 30,
    ):
        self._spawn_cmd = spawn_cmd
        self._prompt_pattern = prompt_pattern
        self._logger = logger or log
        self._default_timeout = timeout
        self._child: pexpect.spawn | None = None
        self._last_prompt: str = ""

    def __enter__(self):
        self.open()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()
        return False

    def open(self) -> None:
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

    def close(self) -> None:
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

    @property
    def is_open(self) -> bool:
        return self._child is not None and self._child.isalive()

    def send(self, text: str) -> None:
        if self._child is None:
            raise RuntimeError("Session is not open")
        self._logger.info(">>> %s", text.rstrip("\r\n"))
        self._child.send(text)

    def sendline(self, text: str) -> None:
        if self._child is None:
            raise RuntimeError("Session is not open")
        self._logger.info("%s %s", self._last_prompt or ">>>", text)
        self._child.sendline(text)

    def expect(
        self,
        pattern: Union[str, list[str]],
        timeout: float | None = None,
    ) -> str:
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
        return output

    def expect_one_of(self, patterns: list[str], timeout: float | None = None) -> int:
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
        return idx

    @staticmethod
    def _clean(text: str) -> str:
        text = _ANSI_RE.sub("", text)
        text = text.replace("\r\n", "\n").replace("\r", "")
        return text.strip()

    def run(self, cmd: str, timeout: float | None = None) -> str:
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

    @property
    def match_index(self) -> int | None:
        if self._child is None:
            return None
        return self._child.match_index if hasattr(self._child, "match_index") else None


class ShellSession(BaseSession):

    PROMPT_PATTERN = r"\S+@\S+:[^#$]*[#$]\s*"

    def __init__(
        self,
        container_name: str,
        transport_cmd_prefix: str = "",
        logger: logging.Logger | None = None,
        timeout: float = 30,
    ):
        if transport_cmd_prefix:
            tty_prefix = transport_cmd_prefix.replace("ssh ", "ssh -tt ", 1)
            spawn_cmd = f"{tty_prefix} docker exec -it {container_name} /bin/bash"
        else:
            spawn_cmd = f"docker exec -it {container_name} /bin/bash"

        super().__init__(
            spawn_cmd=spawn_cmd,
            prompt_pattern=self.PROMPT_PATTERN,
            logger=logger,
            timeout=timeout,
        )
        self._container_name = container_name


class ConsoleSession(BaseSession):

    PROMPT_PATTERN = r"Infoblox\s*>"
    LOGIN_PATTERN = r"login:"
    PASSWORD_PATTERN = r"[Pp]assword:"

    def __init__(
        self,
        container_name: str | None = None,
        host: str | None = None,
        port: int = 22,
        user: str = NIOS_CONSOLE_USER,
        password: str = NIOS_CONSOLE_PASSWORD,
        local: bool = False,
        transport_cmd_prefix: str = "",
        logger: logging.Logger | None = None,
        timeout: float = 60,
    ):
        self._user = user
        self._password = password

        if local or container_name:
            if transport_cmd_prefix:
                tty_prefix = transport_cmd_prefix.replace("ssh ", "ssh -tt ", 1)
                spawn_cmd = (
                    f"{tty_prefix} "
                    f"docker exec -it {container_name} "
                    f"/infoblox/one/bin/serial_console"
                )
            else:
                spawn_cmd = (
                    f"docker exec -it {container_name} "
                    f"/infoblox/one/bin/serial_console"
                )
        else:
            spawn_cmd = (
                f"ssh -o StrictHostKeyChecking=no -p {port} "
                f"{user}@{host}"
            )

        super().__init__(
            spawn_cmd=spawn_cmd,
            prompt_pattern=self.PROMPT_PATTERN,
            logger=logger,
            timeout=timeout,
        )

    def open(self) -> None:
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
            self._child.expect(self.PASSWORD_PATTERN, timeout=15)
            self._child.sendline(self._password)
            self._child.expect(self.PROMPT_PATTERN, timeout=30)
        elif index == 1:
            self._logger.debug("Password prompt detected, authenticating")
            self._child.sendline(self._password)
            self._child.expect(self.PROMPT_PATTERN, timeout=30)

        self._last_prompt = _ANSI_RE.sub("", self._child.after or "").strip()
        self._logger.debug("Console session ready (Infoblox > prompt)")
