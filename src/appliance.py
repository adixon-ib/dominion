from __future__ import annotations

import subprocess
import time
from typing import TYPE_CHECKING

import pexpect

from .config import ApplianceConfig, LocalApplianceConfig
from .constants import (
    ADDKEYS_TIMEOUT,
    NIOS_CONSOLE_USER,
    NIOS_CONSOLE_PASSWORD,
    NIOS_REBOOT_TIMEOUT,
    NIOS_SSH_READY_DELAY,
)
from .container import NiosContainer, LocalNiosContainer
from .log import create_logger
from .transport import LocalDockerTransport, SSHDockerTransport

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


class _ApplianceBase:

    def __init__(self, container: NiosContainer, logger: NiosLogger):
        self.container = container
        self._logger = logger

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
            name=f"nios_test.{config.hostname}",
            level=config.log_level,
            log_file=config.log_file,
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

    def _ssh_available(self) -> bool:
        return ssh_reachable(self._ip or self._hostname)

    def _enable_ssh_via_console(self) -> None:
        ip = self._ip or self._hostname
        self._logger.info("SSH unavailable on %s — enabling via console_connect", ip)

        try:
            child = pexpect.spawn(
                f"console_connect -H {self._hostname}",
                encoding="utf-8", timeout=10,
            )
            idx = child.expect(["login:", "[Pp]assword:", r"Infoblox\s*>"], timeout=10)
            if idx == 0:
                child.sendline(self._console_user)
                child.expect("[Pp]assword:", timeout=10)
                child.sendline(self._console_password)
                child.expect(r"Infoblox\s*>", timeout=10)
            elif idx == 1:
                child.sendline(self._console_password)
                child.expect(r"Infoblox\s*>", timeout=10)
        except (pexpect.TIMEOUT, pexpect.EOF, Exception) as e:
            self._logger.warning(
                "Cannot connect to console on %s: %s. "
                "Enable SSH manually, or close any open console session and retry.",
                self._hostname, e,
            )
            return

        try:
            child.sendline("set remote_console")
            child.expect(r"y or n\):", timeout=10)
            child.sendline("y")
            child.expect(r"y or n\):", timeout=10)
            child.sendline("y")
            child.expect(r"Infoblox\s*>", timeout=10)

            child.sendline("set support_access")
            child.expect(r"y or n\):", timeout=10)
            child.sendline("y")
            child.expect(r"y or n\):", timeout=10)
            child.sendline("y")
            child.expect(r"Infoblox\s*>", timeout=10)

            child.sendline("exit")
            child.close()
        except (pexpect.TIMEOUT, pexpect.EOF) as e:
            self._logger.warning("Console command failed: %s", e)
            child.close(force=True)
            return

        self._logger.info("Console setup done — waiting %ds for SSH to come up", NIOS_SSH_READY_DELAY)
        time.sleep(NIOS_SSH_READY_DELAY)

        self._logger.info("Running addkeys %s", ip)
        try:
            result = subprocess.run(["addkeys", ip], timeout=ADDKEYS_TIMEOUT)
            if result.returncode != 0:
                self._logger.warning(
                    "addkeys %s failed (exit %d) — SSH is enabled but key push failed. "
                    "You may not be able to SSH in.",
                    ip, result.returncode,
                )
        except subprocess.TimeoutExpired:
            self._logger.warning(
                "addkeys %s timed out after %ds — SSH is enabled but key push failed. "
                "You may not be able to SSH in.",
                ip, ADDKEYS_TIMEOUT,
            )

    def _ensure_ssh(self) -> None:
        if self._ssh_available():
            return
        self._enable_ssh_via_console()
        if not self._ssh_available():
            raise RuntimeError(
                f"SSH unavailable on {self._ip or self._hostname} after attempting to enable it. "
                "Check that the appliance is reachable and no other process holds the console."
            )

    def __repr__(self) -> str:
        return f"Appliance({self._hostname!r}, container={self.container.container_name!r})"


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
            name=f"nios_test.{config.container_name}",
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
