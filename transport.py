from __future__ import annotations

import hashlib
import logging
import shlex
import subprocess
from pathlib import Path
from typing import Protocol, runtime_checkable

log = logging.getLogger(__name__)


class TransportError(Exception):
    pass


class CommandError(TransportError):
    def __init__(self, cmd: str, exit_code: int, stdout: str, stderr: str):
        self.cmd = cmd
        self.exit_code = exit_code
        self.stdout = stdout
        self.stderr = stderr
        super().__init__(f"Command failed (exit {exit_code}): {cmd}\n{stderr.strip()}")


@runtime_checkable
class Transport(Protocol):
    container_name: str
    is_local: bool
    host_cmd_prefix: str

    def run(self, cmd: str, check: bool = True) -> tuple[int, str, str]: ...
    def run_on_host(self, cmd: str, check: bool = True) -> tuple[int, str, str]: ...
    def upload(self, local_path: str, remote_path: str) -> None: ...
    def upload_to_host(self, local_path: str, remote_path: str) -> None: ...
    def checksum(self, remote_path: str) -> str: ...
    def host_checksum(self, remote_path: str) -> str: ...
    def is_container_running(self) -> bool: ...


class SSHDockerTransport:

    SSH_OPTS = [
        "-o", "StrictHostKeyChecking=no",
        "-o", "LogLevel=ERROR",
        "-o", "ConnectTimeout=10",
    ]

    is_local = False

    def __init__(
        self,
        host: str,
        container_name: str = "nios",
        user: str = "root",
        key_path: str | None = None,
        port: int = 22,
        timeout: int = 30,
    ):
        self.host = host
        self.container_name = container_name
        self.user = user
        self.key_path = key_path
        self.port = port
        self.timeout = timeout

    @property
    def host_cmd_prefix(self) -> str:
        parts = ["ssh"] + self.SSH_OPTS
        if self.port != 22:
            parts += ["-p", str(self.port)]
        if self.key_path:
            parts += ["-i", self.key_path]
        parts.append(f"{self.user}@{self.host}")
        return " ".join(parts)

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

    def _exec(
        self,
        argv: list[str],
        check: bool = True,
        timeout: int | None = None,
    ) -> tuple[int, str, str]:
        log.debug("Running: %s", " ".join(argv))
        try:
            result = subprocess.run(
                argv,
                capture_output=True,
                text=True,
                timeout=timeout or self.timeout,
            )
        except subprocess.TimeoutExpired as exc:
            raise TransportError(
                f"Command timed out after {timeout or self.timeout}s: {' '.join(argv)}"
            ) from exc
        except FileNotFoundError as exc:
            raise TransportError(f"Command not found: {argv[0]}") from exc

        log.debug("  exit=%d stdout=%s", result.returncode, result.stdout.strip()[:200] or "(empty)")
        if result.stderr.strip():
            log.debug("  stderr=%s", result.stderr.strip()[:200])

        if check and result.returncode != 0:
            raise CommandError(
                cmd=" ".join(argv),
                exit_code=result.returncode,
                stdout=result.stdout,
                stderr=result.stderr,
            )
        return result.returncode, result.stdout, result.stderr

    def run(self, cmd: str, check: bool = True) -> tuple[int, str, str]:
        docker_cmd = f"docker exec {self.container_name} {cmd}"
        argv = self._ssh_base + [docker_cmd]
        return self._exec(argv, check=check)

    def run_on_host(self, cmd: str, check: bool = True) -> tuple[int, str, str]:
        argv = self._ssh_base + [cmd]
        return self._exec(argv, check=check)

    def upload(self, local_path: str, remote_path: str) -> None:
        local = Path(local_path)
        if not local.is_file():
            raise TransportError(f"Local file not found: {local_path}")

        filename = local.name
        host_tmp = f"/tmp/{filename}"
        log.debug("Uploading %s → %s:%s (via %s)", local_path, self.host, remote_path, host_tmp)

        scp_argv = self._scp_base + [str(local), f"{self.user}@{self.host}:{host_tmp}"]
        self._exec(scp_argv)

        docker_cp = f"docker cp {host_tmp} {self.container_name}:{remote_path}"
        self.run_on_host(docker_cp)

        self.run_on_host(f"rm -f {host_tmp}", check=False)

    def upload_to_host(self, local_path: str, remote_path: str) -> None:
        local = Path(local_path)
        if not local.is_file():
            raise TransportError(f"Local file not found: {local_path}")

        log.debug("Uploading %s → %s:%s (host-level)", local_path, self.host, remote_path)
        scp_argv = self._scp_base + [str(local), f"{self.user}@{self.host}:{remote_path}"]
        self._exec(scp_argv)

    def checksum(self, remote_path: str) -> str:
        _, stdout, _ = self.run(f"md5sum {remote_path}")
        md5 = stdout.split()[0]
        log.debug("Checksum %s = %s", remote_path, md5)
        return md5

    def host_checksum(self, remote_path: str) -> str:
        _, stdout, _ = self.run_on_host(f"md5sum {remote_path}")
        md5 = stdout.split()[0]
        log.debug("Host checksum %s = %s", remote_path, md5)
        return md5

    def is_container_running(self) -> bool:
        rc, stdout, _ = self.run_on_host(
            f"docker inspect --format='{{{{.State.Running}}}}' {self.container_name}",
            check=False,
        )
        running = rc == 0 and "true" in stdout.lower()
        log.debug("Container %s running: %s", self.container_name, running)
        return running


class LocalDockerTransport:

    is_local = True
    host_cmd_prefix = ""

    def __init__(self, container_name: str, timeout: int = 30):
        self.container_name = container_name
        self.timeout = timeout

    def _exec(
        self,
        argv: list[str],
        check: bool = True,
        stdin_data: bytes | None = None,
        timeout: int | None = None,
    ) -> tuple[int, str, str]:
        log.debug("Running: %s", " ".join(argv))
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

        log.debug("  exit=%d stdout=%s", result.returncode, stdout.strip()[:200] or "(empty)")
        if stderr.strip():
            log.debug("  stderr=%s", stderr.strip()[:200])

        if check and result.returncode != 0:
            raise CommandError(
                cmd=" ".join(argv),
                exit_code=result.returncode,
                stdout=stdout,
                stderr=stderr,
            )
        return result.returncode, stdout, stderr

    def run(self, cmd: str, check: bool = True) -> tuple[int, str, str]:
        argv = ["docker", "exec", self.container_name, "bash", "-c", cmd]
        return self._exec(argv, check=check)

    def run_on_host(self, cmd: str, check: bool = True) -> tuple[int, str, str]:
        argv = shlex.split(cmd)
        return self._exec(argv, check=check)

    def upload(self, local_path: str, remote_path: str) -> None:
        local = Path(local_path)
        if not local.is_file():
            raise TransportError(f"Local file not found: {local_path}")

        log.debug("Uploading %s → container:%s (cat|tee)", local_path, remote_path)
        file_data = local.read_bytes()
        argv = ["docker", "exec", "-i", self.container_name, "tee", remote_path]
        self._exec(argv, stdin_data=file_data)

    def upload_to_host(self, local_path: str, remote_path: str) -> None:
        local = Path(local_path)
        if not local.is_file():
            raise TransportError(f"Local file not found: {local_path}")
        log.debug("Copying %s → %s (host-level)", local_path, remote_path)
        import shutil
        shutil.copy2(str(local), remote_path)

    def checksum(self, remote_path: str) -> str:
        _, stdout, _ = self.run(f"md5sum {remote_path}")
        md5 = stdout.split()[0]
        log.debug("Checksum %s = %s", remote_path, md5)
        return md5

    def host_checksum(self, remote_path: str) -> str:
        h = hashlib.md5(Path(remote_path).read_bytes()).hexdigest()
        log.debug("Host checksum %s = %s", remote_path, h)
        return h

    def is_container_running(self) -> bool:
        rc, stdout, _ = self.run_on_host(
            f"docker inspect --format='{{{{.State.Running}}}}' {self.container_name}",
            check=False,
        )
        running = rc == 0 and "true" in stdout.lower()
        log.debug("Container %s running: %s", self.container_name, running)
        return running


def connect(
    host: str | None = None,
    container_name: str = "nios",
    user: str = "root",
    key_path: str | None = None,
    port: int = 22,
    timeout: int = 30,
) -> SSHDockerTransport | LocalDockerTransport:
    if host is None:
        return LocalDockerTransport(container_name=container_name, timeout=timeout)
    return SSHDockerTransport(
        host=host,
        container_name=container_name,
        user=user,
        key_path=key_path,
        port=port,
        timeout=timeout,
    )
