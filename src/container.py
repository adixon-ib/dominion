from __future__ import annotations

import os
import shlex
import time
from contextlib import contextmanager
from pathlib import Path
from typing import TYPE_CHECKING

from . import py_tests
from .config import LocalContainerConfig
from .constants import (
    ADMIN_HTTPD_WAIT_TIMEOUT,
    NIOS_CONSOLE_PASSWORD,
    NIOS_CONSOLE_USER,
    NIOS_REBOOT_TIMEOUT,
    NIOS_WAPI_POLL_INTERVAL,
    NIOS_WAPI_READY_TIMEOUT,
    PY_TESTS_TIMEOUT,
    WAPI_VERSION,
)
from .deploy import DeployError, DeployMixin
from .log import LogDump, LogMark, create_logger
from .network import LocalNetworkMixin
from .session import ConsoleSession, ShellSession
from .transport import CommandError, LocalDockerTransport, TransportError

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

    on_call = None  # optional hook, receives a record per sh() call

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
        self._disable_ssh_rate_limit()

    def _disable_ssh_rate_limit(self) -> None:
        self._transport.run(
            "iptables -F SSH_RATE_LIMIT && "
            "iptables -I SSH_RATE_LIMIT -j RETURN && "
            "touch /infoblox/var/debug_ssh_enabled",
            check=False,
            quiet=True,
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

    def sh(
        self, cmd: str, check: bool = True, timeout: int | None = None,
        report_lines: int | None = None,
    ) -> tuple[int, str, str]:
        """Run a shell command line in the container: (exit code, stdout, stderr).

        timeout (seconds) overrides the transport's default for this call.
        report_lines caps the stdout kept in the on_call record to its last
        lines, the returned stdout is complete."""
        opts = {} if timeout is None else {"timeout": timeout}
        try:
            rc, out, err = self._transport.run(
                f"sh -c {shlex.quote(cmd)}", check=check, **opts,
            )
        except CommandError as e:
            self._emit_sh(cmd, e.exit_code, e.stdout, e.stderr, report_lines)
            raise
        except TransportError as e:
            self._emit_sh(cmd, -1, "", str(e))
            raise
        self._emit_sh(cmd, rc, out, err, report_lines)
        return rc, out, err

    def _emit_sh(self, cmd, rc, out, err, report_lines=None) -> None:
        if not self.on_call:
            return
        if report_lines is not None:
            out = self._cap_for_report(out, None, report_lines)
        try:
            self.on_call({
                "kind": "sh", "cmd": cmd, "rc": rc, "stdout": out, "stderr": err,
            })
        except Exception:
            self._logger.exception("on_call hook failed")

    def upload_file(self, src: str, dest: str) -> str:
        """Upload src to dest in the container, clean stale .pyc/.pys next to it
        and verify the md5. Returns the hash; raises DeployError on mismatch."""
        local_h = self._local_md5(src)
        self._transport.upload(src, dest)
        self._clean_pycache(dest)
        remote_h = self._remote_md5(dest)
        self._emit_sh(f"upload {src} -> {dest}", 0, f"{remote_h}  {dest}\n", "")
        if remote_h is None:
            raise DeployError(
                f"md5sum failed or file missing: {dest}", artifact=dest,
            )
        if remote_h != local_h:
            raise DeployError(
                f"Hash mismatch for {os.path.basename(dest)}: "
                f"local={local_h} remote={remote_h}",
                artifact=dest,
            )
        return local_h

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

    INFOBLOX_LOG = "/infoblox/var/infoblox.log"
    REPORT_LOG_LINES = 200  # lines of a dumped log kept in the on_call record

    @classmethod
    def _cap_for_report(
        cls, text: str, dest: Path | None = None, keep: int | None = None,
    ) -> str:
        keep = cls.REPORT_LOG_LINES if keep is None else keep
        lines = text.splitlines(keepends=True)
        if len(lines) <= keep:
            return text
        note = f"... {len(lines) - keep} more lines"
        if dest is not None:
            note += f", full log in {dest}"
        return note + "\n" + "".join(lines[-keep:])

    def mark_log(self, log_path: str) -> LogMark:
        _, stdout, _ = self._transport.run(f"wc -l {shlex.quote(log_path)}")
        line_count = int(stdout.strip().split()[0])
        return LogMark(log_path=log_path, line_count=line_count)

    def mark_infoblox_log(self) -> LogMark:
        return self.mark_log(self.INFOBLOX_LOG)

    def _build_log_cmd(
        self, log_path: str, since: LogMark | None, grep: str | None,
        fixed: bool = False,
    ) -> str:
        if since is not None:
            cmd = f"tail -n +{since.line_count + 1} {shlex.quote(log_path)}"
        else:
            cmd = f"cat {shlex.quote(log_path)}"
        if grep is not None:
            cmd += f" | grep {'-F ' if fixed else ''}-- {shlex.quote(grep)}"
        return cmd

    def dump_log(
        self,
        log_path: str,
        *,
        dest: str | Path,
        since: LogMark | None = None,
        grep: str | None = None,
        fixed: bool = False,
    ) -> LogDump:
        dest = Path(dest)
        if dest.is_dir() or (not dest.suffix and not dest.exists()):
            dest.mkdir(parents=True, exist_ok=True)
            dest = dest / Path(log_path).name
        cmd = self._build_log_cmd(log_path, since, grep, fixed)
        rc, stdout, stderr = self._transport.run(cmd, check=False, quiet=True)
        self._emit_sh(cmd, rc, self._cap_for_report(stdout, dest), stderr)
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(stdout)
        self._logger.info("Log dumped to %s (%d bytes)", dest, len(stdout))
        return LogDump(path=dest, text=stdout)

    def dump_infoblox_log(
        self,
        *,
        dest: str | Path,
        since: LogMark | None = None,
        grep: str | None = None,
        fixed: bool = False,
    ) -> LogDump:
        return self.dump_log(
            self.INFOBLOX_LOG, dest=dest, since=since, grep=grep, fixed=fixed,
        )

    def count_log(
        self,
        log_path: str,
        pattern: str,
        *,
        since: LogMark | None = None,
    ) -> int:
        cmd = self._build_log_cmd(log_path, since, grep=None)
        cmd += f" | grep -c -- {shlex.quote(pattern)}"
        rc, stdout, stderr = self._transport.run(cmd, check=False)
        self._emit_sh(cmd, rc, stdout, stderr)
        return int(stdout.strip() or 0)

    def count_infoblox_log(
        self,
        pattern: str,
        *,
        since: LogMark | None = None,
    ) -> int:
        return self.count_log(self.INFOBLOX_LOG, pattern, since=since)

    ADMIN_HTTPD_WAIT_CMD = (
        "for i in $(seq 120); do pgrep -f [a]dmin-httpd.conf >/dev/null && exit 0; "
        "sleep 5; done; exit 1"
    )

    def wait_ready(self, timeout: int = NIOS_WAPI_READY_TIMEOUT) -> None:
        """Wait for admin-httpd, then for WAPI to answer. WAPI can hang instead of
        refusing right after a restart or factory reset, so a curl timeout or a
        not-ready answer is retried until timeout."""
        rc, _, _ = self.sh(
            self.ADMIN_HTTPD_WAIT_CMD, check=False, timeout=ADMIN_HTTPD_WAIT_TIMEOUT,
        )
        if rc != 0:
            raise RuntimeError(f"admin-httpd not running in {self.container_name}")
        deadline = time.time() + timeout
        while True:
            try:
                return self._wait_for_wapi(timeout=60)
            except (TransportError, RuntimeError):
                if time.time() >= deadline:
                    raise RuntimeError(
                        f"WAPI not answering after {timeout}s"
                    ) from None

    def run_py_tests(
        self,
        tests: str | list[str],
        *,
        keywords: str = "all",
        timeout: int = PY_TESTS_TIMEOUT,
        wait: bool = True,
    ) -> py_tests.PyTestResult:
        """Run NIOS unit tests with run_py_tests (always --no-check-licenses, the
        license check segfaults with the OneDB C extensions) and parse the result.

        tests is a module or class name (infoblox.test.one.wapi.test_x.Class) or a
        list, one -N each. keywords is the -k value (all, base, wapi, ...). wait
        waits for admin-httpd and WAPI first (wait_ready). Each run resets the
        box to factory defaults afterwards, so it takes minutes.

        Never run two at once on one container: they reset the box under each
        other. This raises RuntimeError if a run_py_tests process is already
        running in the container (a pgrep check, so it also catches another
        script, not only another thread). The check and the start are not atomic.

        The on_call record keeps the last 200 lines of the output. The result's
        raw has all of it. A non-zero rc does not raise: look at result.ok.
        """
        cmd = py_tests.command(tests, keywords)
        rc, _, _ = self.sh("pgrep -f '[r]un_py_tests'", check=False)
        if rc == 0:
            raise RuntimeError(
                f"run_py_tests is already running in {self.container_name}"
            )
        if wait:
            self.wait_ready()
        rc, out, _ = self.sh(
            cmd, check=False, timeout=timeout, report_lines=self.REPORT_LOG_LINES,
        )
        return py_tests.parse(out, rc)

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
            name=f"dominion.{config.container_name}",
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
