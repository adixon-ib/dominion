from __future__ import annotations

import hashlib
import shlex
import subprocess
from pathlib import Path
from typing import TYPE_CHECKING

from .log import create_logger

if TYPE_CHECKING:
    from .log import NiosLogger

log = create_logger(name=__name__)


class TransportError(Exception):
    pass


class CommandError(TransportError):
    def __init__(self, cmd: str, exit_code: int, stdout: str, stderr: str):
        self.cmd = cmd
        self.exit_code = exit_code
        self.stdout = stdout
        self.stderr = stderr
        super().__init__(f"Command failed (exit {exit_code}): {cmd}\n{stderr.strip()}")


class _BaseTransport:

    def __init__(
        self,
        container_name: str,
        timeout: int = 30,
        logger: NiosLogger | None = None,
    ):
        self.container_name = container_name
        self.timeout = timeout
        self._logger = logger or log

    def _summarize(self, argv: list[str]) -> str:
        return argv[0] if argv else "<empty>"

    def _log_command(self, argv, stdout, stderr, returncode, quiet):
        # Info: terse command + stdout. Debug: full argv + exit/stdout/stderr.
        # quiet=True demotes the info lines to debug (used for internal
        # housekeeping commands like iptables setup that aren't worth surfacing).
        if quiet:
            self._logger.debug("%s", self._summarize(argv))
        else:
            self._logger.info("%s", self._summarize(argv))
            if stdout.strip():
                self._logger.info("%s", stdout.strip())
        self._logger.debug("%s", " ".join(argv))
        self._logger.debug("  exit=%d", returncode)
        if stdout.strip():
            self._logger.debug("  stdout=%s", stdout.strip())
        if stderr.strip():
            self._logger.debug("  stderr=%s", stderr.strip())

    def _exec(
        self,
        argv: list[str],
        check: bool = True,
        stdin_data: bytes | None = None,
        timeout: int | None = None,
        quiet: bool = False,
    ) -> tuple[int, str, str]:
        try:
            result = subprocess.run(
                argv,
                capture_output=True,
                timeout=timeout or self.timeout,
                input=stdin_data,
            )
        except subprocess.TimeoutExpired as exc:
            raise TransportError(
                f"Command timed out after {timeout or self.timeout}s: {' '.join(argv)}"
            ) from exc
        except FileNotFoundError as exc:
            raise TransportError(f"Command not found: {argv[0]}") from exc

        stdout = result.stdout.decode(errors="replace") if isinstance(result.stdout, bytes) else result.stdout
        stderr = result.stderr.decode(errors="replace") if isinstance(result.stderr, bytes) else result.stderr

        self._log_command(argv, stdout, stderr, result.returncode, quiet)

        if check and result.returncode != 0:
            raise CommandError(
                cmd=" ".join(argv),
                exit_code=result.returncode,
                stdout=stdout,
                stderr=stderr,
            )
        return result.returncode, stdout, stderr

    def checksum(self, remote_path: str) -> str:
        _, stdout, _ = self.run(f"md5sum {remote_path}")
        md5 = stdout.split()[0]
        self._logger.debug("Checksum %s = %s", remote_path, md5)
        return md5

    def is_container_running(self) -> bool:
        rc, stdout, _ = self.run_on_host(
            f"docker inspect --format='{{{{.State.Running}}}}' {self.container_name}",
            check=False,
        )
        running = rc == 0 and "true" in stdout.lower()
        self._logger.debug("Container %s running: %s", self.container_name, running)
        return running


class SSHDockerTransport(_BaseTransport):

    SSH_OPTS = [
        "-o", "StrictHostKeyChecking=no",
        "-o", "LogLevel=ERROR",
        "-o", "ConnectTimeout=10",
    ]

    def __init__(
        self,
        host: str,
        container_name: str = "nios",
        user: str = "root",
        key_path: str | None = None,
        port: int = 22,
        timeout: int = 30,
        logger: NiosLogger | None = None,
    ):
        super().__init__(container_name, timeout, logger)
        self.host = host
        self.user = user
        self.key_path = key_path
        self.port = port

    def _summarize(self, argv: list[str]) -> str:
        parts = []
        skip_next = False
        for arg in argv:
            if skip_next:
                skip_next = False
                continue
            if arg.startswith("-"):
                if arg in ("-o", "-i", "-p", "-P"):
                    skip_next = True
                continue
            parts.append(arg)
        return " ".join(parts)

    def build_exec_cmd(self, cmd: str, tty: bool = False) -> str:
        """Wrap cmd so it runs on this host over SSH.

        Args:
            tty: Force a pty allocation (ssh -tt). Needed for interactive
                commands, e.g. a session's docker exec -it.
        """
        ssh = self._ssh_base.copy()
        if tty:
            ssh.insert(1, "-tt")
        return " ".join(ssh) + f" {cmd}"

    @property
    def _ssh_base(self) -> list[str]:
        cmd = ["ssh"] + self.SSH_OPTS
        if self.port != 22:
            cmd += ["-p", str(self.port)]
        if self.key_path:
            cmd += ["-i", self.key_path]
        cmd.append(f"{self.user}@{self.host}")
        return cmd

    @property
    def _scp_base(self) -> list[str]:
        cmd = ["scp"] + self.SSH_OPTS
        if self.port != 22:
            cmd += ["-P", str(self.port)]
        if self.key_path:
            cmd += ["-i", self.key_path]
        return cmd

    def run(self, cmd: str, check: bool = True, quiet: bool = False) -> tuple[int, str, str]:
        docker_cmd = f"docker exec {self.container_name} {cmd}"
        argv = self._ssh_base + [docker_cmd]
        return self._exec(argv, check=check, quiet=quiet)

    def run_on_host(self, cmd: str, check: bool = True, quiet: bool = False) -> tuple[int, str, str]:
        argv = self._ssh_base + [cmd]
        return self._exec(argv, check=check, quiet=quiet)

    def upload(self, local_path: str, remote_path: str) -> None:
        """Put a file inside the NIOS container (Python files, configs)."""
        local = Path(local_path)
        if not local.is_file():
            raise TransportError(f"Local file not found: {local_path}")

        filename = local.name
        host_tmp = f"/tmp/{filename}"
        self._logger.debug("Uploading %s → %s:%s (via %s)", local_path, self.host, remote_path, host_tmp)

        scp_argv = self._scp_base + [str(local), f"{self.user}@{self.host}:{host_tmp}"]
        self._exec(scp_argv)

        docker_cp = f"docker cp {host_tmp} {self.container_name}:{remote_path}"
        code, _, err = self.run_on_host(docker_cp, check=False)
        if code != 0:
            # docker cp can fail to resolve an otherwise-existing container
            # path (observed against a real appliance for a directory under
            # /var/lib/cloud/handlers) even though the directory is present
            # and other docker cp calls in the same session succeed. Fall
            # back to piping the file through docker exec's stdin, which
            # doesn't hit whatever path-resolution issue docker cp has.
            self._logger.debug(
                "docker cp failed (%s) — falling back to docker exec tee",
                err.strip(),
            )
            tee_cmd = f"docker exec -i {self.container_name} tee {remote_path} > /dev/null"
            argv = self._ssh_base + [tee_cmd]
            self._exec(argv, stdin_data=local.read_bytes())

        self.run_on_host(f"rm -f {host_tmp}", check=False)

    def upload_to_host(self, local_path: str, remote_path: str) -> None:
        """Put a file on the host filesystem, outside the container (binaries, .so)."""
        local = Path(local_path)
        if not local.is_file():
            raise TransportError(f"Local file not found: {local_path}")

        self._logger.debug("Uploading %s → %s:%s (host-level)", local_path, self.host, remote_path)
        scp_argv = self._scp_base + [str(local), f"{self.user}@{self.host}:{remote_path}"]
        self._exec(scp_argv)

    def host_checksum(self, remote_path: str) -> str:
        _, stdout, _ = self.run_on_host(f"md5sum {remote_path}")
        md5 = stdout.split()[0]
        self._logger.debug("Host checksum %s = %s", remote_path, md5)
        return md5


class LocalDockerTransport(_BaseTransport):

    def _summarize(self, argv: list[str]) -> str:
        if len(argv) > 2 and argv[0] == "docker" and argv[1] == "exec":
            i = 2
            while i < len(argv) and argv[i].startswith("-"):
                i += 1
            container = argv[i] if i < len(argv) else "?"
            cmd_parts = argv[i + 1:]
            if len(cmd_parts) >= 3 and cmd_parts[0] == "bash" and cmd_parts[1] == "-c":
                return f"docker@{container}: {cmd_parts[2]}"
            return f"docker@{container}: {' '.join(cmd_parts)}"
        return " ".join(argv)

    def build_exec_cmd(self, cmd: str, tty: bool = False) -> str:
        """No SSH hop needed. cmd already targets the local Docker daemon."""
        return cmd

    def run(self, cmd: str, check: bool = True, quiet: bool = False) -> tuple[int, str, str]:
        argv = ["docker", "exec", self.container_name, "bash", "-c", cmd]
        return self._exec(argv, check=check, quiet=quiet)

    def run_on_host(self, cmd: str, check: bool = True, quiet: bool = False) -> tuple[int, str, str]:
        argv = shlex.split(cmd)
        return self._exec(argv, check=check, quiet=quiet)

    def upload(self, local_path: str, remote_path: str) -> None:
        """Put a file inside the NIOS container (Python files, configs)."""
        local = Path(local_path)
        if not local.is_file():
            raise TransportError(f"Local file not found: {local_path}")

        self._logger.debug("Uploading %s → container:%s (cat|tee)", local_path, remote_path)
        file_data = local.read_bytes()
        argv = ["docker", "exec", "-i", self.container_name, "bash", "-c", f"tee {remote_path} > /dev/null"]
        self._exec(argv, stdin_data=file_data)

    def upload_to_host(self, local_path: str, remote_path: str) -> None:
        """Put a file on the host filesystem, outside the container (binaries, .so)."""
        local = Path(local_path)
        if not local.is_file():
            raise TransportError(f"Local file not found: {local_path}")
        self._logger.debug("Copying %s → %s (host-level)", local_path, remote_path)
        import shutil
        shutil.copy2(str(local), remote_path)

    def host_checksum(self, remote_path: str) -> str:
        h = hashlib.md5(Path(remote_path).read_bytes()).hexdigest()
        self._logger.debug("Host checksum %s = %s", remote_path, h)
        return h
