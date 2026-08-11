from __future__ import annotations

import time
from contextlib import contextmanager
from typing import TYPE_CHECKING

from .config import LocalContainerConfig
from .constants import (
    NIOS_CONSOLE_USER,
    NIOS_CONSOLE_PASSWORD,
    NIOS_REBOOT_TIMEOUT,
    NIOS_WAPI_POLL_INTERVAL,
    NIOS_WAPI_READY_TIMEOUT,
    WAPI_VERSION,
)
from .deploy import DeployMixin
from .log import create_logger
from .network import LocalNetworkMixin
from .session import ShellSession, ConsoleSession
from .transport import LocalDockerTransport

if TYPE_CHECKING:
    from .log import NiosLogger

log = create_logger(name=__name__)


class NiosContainer(DeployMixin):
    """Docker-exec access to NIOS running on a real appliance over SSH.

    Construct only via Appliance/LocalAppliance, which establish the
    identity guarantees this class assumes (e.g. SSH reachability) before
    handing back a container. For NCC (no separate host identity), use
    LocalNiosContainer.from_config() directly.
    """

    def __init__(
        self,
        container_name: str,
        transport,
        chroot: str,
        worktree: str | None = None,
        logger: NiosLogger | None = None,
        console_user: str = NIOS_CONSOLE_USER,
        console_password: str = NIOS_CONSOLE_PASSWORD,
    ):
        self.container_name = container_name
        self._transport = transport
        self.chroot = chroot
        self.worktree = worktree
        self._logger = logger or log
        self._console_user = console_user
        self._console_password = console_password

    @contextmanager
    def shell_session(self, logger=None, timeout=30):
        spawn_cmd = self._transport.build_exec_cmd(
            f"docker exec -it {self.container_name} /bin/bash", tty=True,
        )
        session = ShellSession(
            spawn_cmd=spawn_cmd,
            logger=logger or self._logger,
            timeout=timeout,
        )
        try:
            session._open()
            yield session
        finally:
            session._close()

    @contextmanager
    def console_session(self, logger=None, timeout=60):
        ssh_opts = " ".join(self._transport.SSH_OPTS)
        spawn_cmd = f"ssh {ssh_opts} -p 22 {self._console_user}@{self._transport.host}"
        session = ConsoleSession(
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

    def _wait_for_wapi(self, timeout: int = NIOS_WAPI_READY_TIMEOUT) -> None:
        deadline = time.time() + timeout
        while time.time() < deadline:
            # -f makes curl fail (non-zero exit) on HTTP error responses
            # (e.g. 503 while WAPI is still starting up) — without it, a
            # successful TCP/TLS connect with a 503 body still reports
            # rc == 0, so this would report "ready" while WAPI still
            # rejects real requests.
            rc, _, _ = self._transport.run(
                f"curl -sk -f -u {NIOS_CONSOLE_USER}:{NIOS_CONSOLE_PASSWORD} "
                f"https://localhost/wapi/{WAPI_VERSION}/grid",
                check=False,
            )
            if rc == 0:
                return
            time.sleep(NIOS_WAPI_POLL_INTERVAL)
        raise RuntimeError(f"NIOS did not respond to WAPI within {timeout}s")

    def restart(self, timeout: int = NIOS_REBOOT_TIMEOUT) -> None:
        """Restart the docker container, discarding hot-deployed files and
        restoring image-layer originals. Blocks until WAPI answers again."""
        self._logger.info("Restarting %s container", self.container_name)
        self._transport.run_on_host(f"docker restart {self.container_name}")
        self._wait_for_wapi(timeout=timeout)
        self._logger.info("%s is back up", self.container_name)

    def set_network(
        self, ip: str, netmask: str, gateway: str, reboot_timeout: int = NIOS_REBOOT_TIMEOUT,
    ) -> str:
        """Run the console "set network" wizard with explicit IP/netmask/gateway,
        then wait out the reboot. Callers already know these values (e.g. from
        Appliance/config) and are responsible for confirming NIOS is back up —
        this does not poll WAPI. LocalNiosContainer overrides this to
        auto-discover the values when omitted and additionally poll WAPI +
        add a host route, via super().set_network(...).
        """
        self._logger.info(
            "Configuring NIOS network: IP=%s netmask=%s gateway=%s",
            ip, netmask, gateway,
        )

        with self.console_session() as console:
            console.sendline("set network")
            console.expect("Enter IPv4 address")
            console.sendline(ip)
            console.expect("Enter netmask")
            console.sendline(netmask)
            console.expect("Enter gateway")
            console.sendline(gateway)
            console.expect("Enter VLAN")
            console.sendline("")

            idx = console.expect_one_of(
                ["Enter DSCP", "Configure IPv6", "Become grid member"],
                timeout=30,
            )
            if idx == 0:
                console.sendline("")
                console.expect("Configure IPv6")
                console.sendline("n")
            elif idx == 1:
                console.sendline("n")

            if idx != 2:
                console.expect("Become grid member")
            console.sendline("n")

            console.expect("Is this correct")
            console.sendline("y")
            console.expect("Are you sure")
            console.sendline("y")
            console.expect("Network settings have been updated", timeout=60)

        self._logger.info("Network configured — waiting %ds for NIOS reboot", reboot_timeout)
        time.sleep(reboot_timeout)
        return ip

    def __repr__(self) -> str:
        return f"NiosContainer({self.container_name!r})"

    def __str__(self) -> str:
        return self.container_name


class LocalNiosContainer(LocalNetworkMixin, NiosContainer):
    """Docker-exec access to NIOS running in a local NCC container.

    Safe to construct standalone via from_config() — unlike NiosContainer,
    there's no separate appliance identity to establish first.
    """

    @classmethod
    def from_config(cls, config: LocalContainerConfig) -> LocalNiosContainer:
        logger = config.logger or create_logger(
            name=f"nios_test.{config.container_name}",
            level=config.log_level,
            log_file=config.log_file,
        )
        transport = LocalDockerTransport(container_name=config.container_name, logger=logger)
        return cls(
            container_name=config.container_name,
            transport=transport,
            chroot=config.chroot,
            worktree=config.worktree,
            logger=logger,
            console_user=config.console_user,
            console_password=config.console_password,
        )

    @contextmanager
    def console_session(self, logger=None, timeout=60):
        spawn_cmd = self._transport.build_exec_cmd(
            f"docker exec -it {self.container_name} /infoblox/one/bin/serial_console",
            tty=True,
        )
        session = ConsoleSession(
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

    def __repr__(self) -> str:
        return f"LocalNiosContainer({self.container_name!r})"
