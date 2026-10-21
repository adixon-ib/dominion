from __future__ import annotations

import socket
import subprocess
import time
from contextlib import contextmanager

import pexpect
from typing import TYPE_CHECKING

from .config import ApplianceConfig, LocalApplianceConfig
from .constants import (
    ADDKEYS_RETRY_INTERVAL,
    ADDKEYS_RETRY_TIMEOUT,
    ADDKEYS_TIMEOUT,
    NIOS_CONSOLE_USER,
    NIOS_CONSOLE_PASSWORD,
    NIOS_REBOOT_TIMEOUT,
    NIOS_SSH_READY_DELAY,
    WAPI_VERSION,
)
from .container import NiosContainer, LocalNiosContainer
from .log import LogDump, LogMark, create_logger
from .session import ConsoleConnectSession, ConsoleSession
from .transport import LocalDockerTransport, SSHDockerTransport
from .wapi import WapiClient

if TYPE_CHECKING:
    from .deploy import DeployResult
    from .log import NiosLogger


def ssh_reachable(host: str, timeout: int = 5) -> bool:
    try:
        result = subprocess.run(
            ["ssh", "-o", "StrictHostKeyChecking=no",
             "-o", f"ConnectTimeout={timeout}", "-o", "BatchMode=yes",
             f"root@{host}", "echo", "ok"],
            capture_output=True, timeout=timeout * 2,
        )
        return result.returncode == 0
    except Exception:
        return False


def ssh_port_open(host: str, timeout: int = 5) -> bool:
    try:
        with socket.create_connection((host, 22), timeout=timeout):
            return True
    except OSError:
        return False


def reset_console(hostname: str) -> None:
    """Force-release a stuck console server session."""
    subprocess.run(["reset_console", "-H", hostname], check=True)


class _ApplianceBase:

    def __init__(self, container: NiosContainer, logger: NiosLogger):
        self.container = container
        self._logger = logger
        self._wapi: WapiClient | None = None
        self._wapi_version: str = WAPI_VERSION

    @property
    def wapi(self) -> WapiClient:
        if self._wapi is None:
            self._wapi = WapiClient(
                transport=self.container._transport,
                version=self._wapi_version,
                logger=self._logger,
            )
        return self._wapi

    @property
    def wapi_version(self) -> str:
        return self._wapi_version

    @wapi_version.setter
    def wapi_version(self, value: str):
        self._wapi_version = value
        self._wapi = None

    def shell_session(self, **kwargs):
        return self.container.shell_session(**kwargs)

    def console_session(self, **kwargs):
        return self.container.console_session(**kwargs)

    def deploy_serial_console(self) -> DeployResult:
        return self.container.deploy_serial_console()

    def deploy_shared_lib(self, **kwargs) -> DeployResult:
        return self.container.deploy_shared_lib(**kwargs)

    def deploy_token_py(self, **kwargs) -> DeployResult:
        return self.container.deploy_token_py(**kwargs)

    def deploy_token_stats_consumer(self, **kwargs) -> DeployResult:
        return self.container.deploy_token_stats_consumer(**kwargs)

    def deploy_clusterd(self) -> DeployResult:
        return self.container.deploy_clusterd()

    def deploy_nios_model_cloud_init(self) -> DeployResult:
        return self.container.deploy_nios_model_cloud_init()

    def mark_log(self, log_path: str) -> LogMark:
        return self.container.mark_log(log_path)

    def mark_infoblox_log(self) -> LogMark:
        return self.container.mark_infoblox_log()

    def dump_log(self, log_path: str, **kwargs) -> LogDump:
        return self.container.dump_log(log_path, **kwargs)

    def dump_infoblox_log(self, **kwargs) -> LogDump:
        return self.container.dump_infoblox_log(**kwargs)

    def count_log(self, log_path: str, pattern: str, **kwargs) -> int:
        return self.container.count_log(log_path, pattern, **kwargs)

    def count_infoblox_log(self, pattern: str, **kwargs) -> int:
        return self.container.count_infoblox_log(pattern, **kwargs)

    def revert(self, timeout: int = NIOS_REBOOT_TIMEOUT) -> None:
        """Restart the nios container, discarding hot-deployed files and
        restoring image-layer originals."""
        self.container.restart(timeout=timeout)

    def __repr__(self) -> str:
        return f"{type(self).__name__}(container={self.container.container_name!r})"


class Appliance(_ApplianceBase):

    def __init__(
        self,
        container: NiosContainer,
        logger: NiosLogger,
        hostname: str,
        ip: str | None = None,
        console_user: str = NIOS_CONSOLE_USER,
        console_password: str = NIOS_CONSOLE_PASSWORD,
    ):
        super().__init__(container, logger)
        self._hostname = hostname
        self._ip = ip
        self._console_user = console_user
        self._console_password = console_password
        # SSH is a construction-time invariant. Probes SSH; if unavailable, enables
        # it via console_connect (~15s delay + addkeys). Raises RuntimeError if SSH
        # still cannot be reached after the attempt.
        self._ensure_ssh()

    @classmethod
    def from_config(cls, config: ApplianceConfig) -> "Appliance":
        logger = config.logger or create_logger(
            name=f"dominion.{config.hostname}",
            level=config.log_level,
            log_file=config.log_file,
        )
        logger.debug(
            "Appliance %s: host=%s port=%d chroot=%s",
            config.hostname, config.ip or config.hostname, config.port, config.chroot,
        )
        transport = SSHDockerTransport(
            host=config.ip or config.hostname,
            container_name=config.container_name,
            user=config.user,
            key_path=config.key_path,
            port=config.port,
            logger=logger,
        )
        container = NiosContainer(
            container_name=config.container_name,
            transport=transport,
            chroot=config.chroot,
            worktree=config.worktree,
            logger=logger,
            console_user=config.console_user,
            console_password=config.console_password,
        )
        return cls(
            container=container,
            logger=logger,
            hostname=config.hostname,
            ip=config.ip,
            console_user=config.console_user,
            console_password=config.console_password,
        )

    @property
    def ip(self) -> str | None:
        return self._ip

    @property
    def hostname(self) -> str:
        return self._hostname

    @contextmanager
    def console_connect_session(self, logger=None, timeout=60):
        """Out-of-band console via lab console server. Survives NIOS reboots.
        Only works for lab-registered appliances."""
        reset_console(self._hostname)
        spawn_cmd = f"console_connect -H {self._hostname}"
        session = ConsoleConnectSession(
            spawn_cmd=spawn_cmd,
            user=self._console_user,
            password=self._console_password,
            logger=logger or self._logger,
            timeout=timeout,
        )
        try:
            session._open()
            yield session
        finally:
            session._close()

    def _ssh_available(self) -> bool:
        return ssh_reachable(self._ip or self._hostname)

    @staticmethod
    def _console_set(console, cmd) -> None:
        """Run a console set command, answering y to all prompts."""
        console.sendline(cmd)
        while True:
            idx = console.expect_one_of([r"Infoblox\s*>", r"y or n\):"])
            if idx == 0:
                break
            time.sleep(0.1)
            console.sendline("y")

    def _enable_ssh_via_console(self) -> str | None:
        """Power-cycle and enable remote_console + support_access. Returns an error string on failure."""
        ip = self._ip or self._hostname
        self._logger.info("SSH unavailable on %s — power-cycling and enabling via console", ip)

        self._logger.info("Powering off %s", self._hostname)
        subprocess.run(
            ["reboot_system", "-H", self._hostname, "-a", "poweroff"],
            timeout=30,
        )
        time.sleep(5)
        self._logger.info("Powering on %s", self._hostname)
        subprocess.run(
            ["reboot_system", "-H", self._hostname, "-a", "poweron"],
            timeout=30,
        )

        try:
            reset_console(self._hostname)
            spawn_cmd = f"console_connect -H {self._hostname}"
            session = ConsoleConnectSession(
                spawn_cmd=spawn_cmd,
                user=self._console_user,
                password=self._console_password,
                logger=self._logger,
                timeout=NIOS_REBOOT_TIMEOUT,
            )
            session._child = pexpect.spawn(
                spawn_cmd, encoding="utf-8",
                timeout=NIOS_REBOOT_TIMEOUT, echo=False,
            )
            # Wait for NIOS to boot — login: appears after ~2-3 minutes
            self._logger.info("Waiting for %s to boot (up to %ds)...", self._hostname, NIOS_REBOOT_TIMEOUT)
            session._child.expect(session.LOGIN_PATTERN, timeout=NIOS_REBOOT_TIMEOUT)
            session._child.sendline(self._console_user)
            session._child.expect(session.PASSWORD_PATTERN, timeout=15)
            session._child.sendline(self._console_password)
            session._child.expect(session.PROMPT_PATTERN, timeout=30)
            session._last_prompt = session._child.after or ""

            try:
                self._console_set(session, "set remote_console")

                output = session.run("show support_access")
                if "expires in" in output:
                    self._logger.debug("Support access already active on %s", ip)
                else:
                    self._console_set(session, "set support_access")
            finally:
                session._close()
        except Exception as e:
            self._logger.info(
                "Cannot enable SSH on %s via console: %s. "
                "Enable SSH manually, or close any open console session and retry.",
                self._hostname, e,
            )
            return str(e) or type(e).__name__

        self._logger.info("Console setup done on %s", ip)
        return None

    def _addkeys(self, ip: str) -> bool:
        self._logger.info("Running addkeys %s", ip)
        try:
            rc = subprocess.run(["addkeys", ip], timeout=ADDKEYS_TIMEOUT).returncode
        except subprocess.TimeoutExpired:
            self._logger.info("addkeys %s timed out after %ds", ip, ADDKEYS_TIMEOUT)
            return False
        except OSError as e:
            self._logger.info("addkeys %s could not run: %s", ip, e)
            return False
        if rc != 0:
            self._logger.info("addkeys %s failed (exit %d)", ip, rc)
        return rc == 0

    def _ensure_ssh(self) -> None:
        ip = self._ip or self._hostname
        if self._ssh_available():
            self._logger.debug("SSH already reachable on %s", ip)
            return

        # Port open but login failed means keys are missing, not that SSH is off.
        if ssh_port_open(ip):
            self._logger.info("SSH port open but login failed on %s — trying addkeys before power-cycle", ip)
            if self._addkeys(ip) and self._ssh_available():
                self._logger.info("addkeys restored SSH on %s", ip)
                return
            self._logger.info("addkeys alone did not restore SSH on %s — power-cycling", ip)

        console_error = self._enable_ssh_via_console()
        if console_error is None:
            self._logger.info("Waiting %ds for SSH to come up on %s", NIOS_SSH_READY_DELAY, ip)
            time.sleep(NIOS_SSH_READY_DELAY)

        # addkeys needs WAPI, which can lag behind the console login prompt, so retry.
        deadline = time.time() + ADDKEYS_RETRY_TIMEOUT
        attempts = 0
        addkeys_ok = False
        while True:
            attempts += 1
            addkeys_ok = self._addkeys(ip)
            if addkeys_ok and self._ssh_available():
                self._logger.info("SSH available on %s after addkeys (attempt %d)", ip, attempts)
                return
            if time.time() + ADDKEYS_RETRY_INTERVAL >= deadline:
                break
            time.sleep(ADDKEYS_RETRY_INTERVAL)

        if addkeys_ok:
            failed = f"SSH login still failing after addkeys succeeded ({attempts} attempts)"
        else:
            failed = f"addkeys failed on all {attempts} attempts over {ADDKEYS_RETRY_TIMEOUT}s"
        if console_error is not None:
            failed = f"console enable failed ({console_error}); {failed}"
        raise RuntimeError(
            f"SSH unavailable on {ip}: {failed}. "
            "Check that the appliance is reachable and no other process holds the console."
        )

    def __repr__(self) -> str:
        return f"Appliance({self._hostname!r}, container={self.container.container_name!r})"

    def __str__(self) -> str:
        return f"{self._hostname} ({self._ip})" if self._ip else self._hostname


class LocalAppliance(_ApplianceBase):

    def __init__(
        self,
        container: NiosContainer,
        logger: NiosLogger,
        ip: str,
    ):
        super().__init__(container, logger)
        self._ip = ip

    @classmethod
    def from_config(cls, config: LocalApplianceConfig) -> "LocalAppliance":
        logger = config.logger or create_logger(
            name=f"dominion.{config.container_name}",
            level=config.log_level,
            log_file=config.log_file,
        )
        transport = LocalDockerTransport(container_name=config.container_name, logger=logger)
        container = LocalNiosContainer(
            container_name=config.container_name,
            transport=transport,
            chroot=config.chroot,
            worktree=config.worktree,
            logger=logger,
            console_user=config.console_user,
            console_password=config.console_password,
        )
        # If ip is provided, the network is already configured — use it directly.
        # If not, set_network() runs here: ~300s reboot wait + WAPI polling.
        # This makes a valid IP an invariant of every LocalAppliance instance.
        if config.ip is not None:
            ip = config.ip
        else:
            ip = container.set_network()
        return cls(container=container, logger=logger, ip=ip)

    @property
    def ip(self) -> str:
        return self._ip

    @property
    def hostname(self) -> str:
        # No real network hostname (local docker exec, no SSH hop) - container
        # name disambiguates between containers on the same machine.
        return self.container.container_name

    def __repr__(self) -> str:
        return f"LocalAppliance(container={self.container.container_name!r}, ip={self._ip!r})"

    def __str__(self) -> str:
        return f"{self.hostname} ({self._ip})"
