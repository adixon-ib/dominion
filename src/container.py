from __future__ import annotations

import json
import logging
import re
import time
from contextlib import contextmanager

from .config import ContainerConfig, LocalContainerConfig
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
from .session import ShellSession, ConsoleSession
from .transport import LocalDockerTransport, SSHDockerTransport

log = logging.getLogger(__name__)


class NiosContainer(DeployMixin):

    def __init__(
        self,
        container_name: str,
        transport,
        chroot: str,
        worktree: str | None = None,
        logger: logging.Logger | None = None,
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

    @classmethod
    def from_config(cls, config: ContainerConfig) -> NiosContainer:
        logger = config.logger or create_logger(
            name=f"nios_test.{config.container_name}",
            level=config.log_level,
            log_file=config.log_file,
        )
        transport = SSHDockerTransport(
            host=config.host,
            container_name=config.container_name,
            user=config.user,
            key_path=config.key_path,
            port=config.port,
        )
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

    def __repr__(self) -> str:
        return f"NiosContainer({self.container_name!r})"


class LocalNiosContainer(NiosContainer):

    @classmethod
    def from_config(cls, config: LocalContainerConfig) -> LocalNiosContainer:
        logger = config.logger or create_logger(
            name=f"nios_test.{config.container_name}",
            level=config.log_level,
            log_file=config.log_file,
        )
        transport = LocalDockerTransport(container_name=config.container_name)
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

    def _get_container_network(self) -> tuple[str, int]:
        _, out, _ = self._transport.run_on_host(
            f"docker inspect {self.container_name}",
        )
        data = json.loads(out)
        networks = data[0]["NetworkSettings"]["Networks"]
        if "ncc_lan1" not in networks:
            raise RuntimeError(
                f"No ncc_lan1 network on {self.container_name} — "
                "is the container running with NCC networking?"
            )
        net = networks["ncc_lan1"]
        ip = net["IPAddress"]
        prefix = int(net["IPPrefixLen"])
        if not ip:
            raise RuntimeError(f"ncc_lan1 has no IP on {self.container_name}")
        return ip, prefix

    def get_container_ip(self) -> str:
        ip, _ = self._get_container_network()
        return ip

    def _find_forwarding_bridge(self) -> str | None:
        _, out, _ = self._transport.run(
            'for eth in /sys/class/net/eth*/iflink; do cat "$eth" 2>/dev/null; done',
            check=False,
        )
        indices = out.strip().split()
        if not indices:
            return None

        _, links_out, _ = self._transport.run_on_host("ip -o link show", check=False)

        for idx in indices:
            veth = None
            for line in links_out.splitlines():
                parts = line.split()
                if parts and parts[0] == f"{idx}:":
                    veth = parts[1].split("@")[0]
                    break
            if not veth:
                continue

            _, bridge_out, _ = self._transport.run_on_host(
                f"bridge link show dev {veth}", check=False
            )
            if "state forwarding" not in bridge_out:
                continue

            _, link_out, _ = self._transport.run_on_host(
                f"ip -o link show dev {veth}", check=False
            )
            m = re.search(r"master (\S+)", link_out)
            if m:
                return m.group(1)

        return None

    def set_network(self, reboot_timeout: int = NIOS_REBOOT_TIMEOUT, wapi_timeout: int = NIOS_WAPI_READY_TIMEOUT) -> str:
        ip, prefix = self._get_container_network()
        netmask = {16: "255.255.0.0", 24: "255.255.255.0"}.get(prefix, "255.255.0.0")
        gateway = ip.rsplit(".", 1)[0] + ".1"

        log.info(
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

        log.info("Network configured — waiting %ds for NIOS reboot", reboot_timeout)
        time.sleep(reboot_timeout)

        log.info("Polling WAPI (up to %ds)", wapi_timeout)
        self._wait_for_wapi(timeout=wapi_timeout)
        log.info("NIOS is back up at %s", ip)

        bridge = self._find_forwarding_bridge()
        if bridge:
            rc, _, err = self._transport.run_on_host(
                f"sudo ip route replace {ip}/32 dev {bridge}", check=False
            )
            if rc == 0:
                log.info("Route: %s/32 → %s", ip, bridge)
            else:
                log.warning("Could not add host route: %s", err.strip())
        else:
            log.warning("No forwarding bridge found — host route not added")

        return ip

    def __repr__(self) -> str:
        return f"LocalNiosContainer({self.container_name!r})"
