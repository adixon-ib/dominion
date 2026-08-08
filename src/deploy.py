from __future__ import annotations

import hashlib
import logging
import os
import shutil
import tempfile
import time
from dataclasses import dataclass, field

log = logging.getLogger(__name__)


class DeployError(RuntimeError):
    def __init__(self, message: str, artifact: str | None = None):
        super().__init__(message)
        self.artifact = artifact


@dataclass
class DeployResult:
    artifact: str
    changed: bool
    local_hash: str = ""
    remote_hash: str = ""
    restarted: bool = False
    details: dict = field(default_factory=dict)

    def __str__(self) -> str:
        parts = [self.artifact, "changed" if self.changed else "unchanged"]
        if self.restarted:
            parts.append("restarted")
        files = self.details.get("files")
        if files:
            parts.append(f"{len(files)} file(s) deployed")
        skipped = self.details.get("skipped")
        if skipped:
            parts.append(f"{len(skipped)} skipped")
        return f"DeployResult({', '.join(parts)})"


PYABS_FILES = [
    "grid_token_pool.py",
    "member_token.py",
    "token_audit_log.py",
    "token_common.py",
    "token_gridwide.py",
    "token_overage.py",
    "token_sync_status.py",
]

WAPI_FILES = [
    "grid_token_pool.py",
    "member_token.py",
    "token_audit_log.py",
    "token_common.py",
    "token_gridwide.py",
    "token_overage.py",
    "token_alert_threshold.py",
    "token_sync_status.py",
]

IBAP_FILES = ["tokenobj.py", "tokenfuncs.py"]

CONTAINER_PYABS = "/infoblox/one/lib/python/infoblox/one"
CONTAINER_WAPI = "/infoblox/one/lib/python/infoblox/one/wapi"
CONTAINER_IBAP = "/infoblox/one/lib/python/infoblox/one/ibap"

PYABS_SUBPATH = "products/one/server/src/pyabs"
WAPI_SUBPATH = "products/one/server/src/wapi"
IBAP_SUBPATH = "products/one/server/src/ibap"

CONSUMER_SRC = "products/one/server/src/bin/util/token_stats_consumer.py"
CONSUMER_DEST = "/infoblox/one/bin/token_stats_consumer"
CONSUMER_CONTROL = "/infoblox/one/bin/token_stats_consumer_control"
CONSUMER_PIDFILE = "/infoblox/var/token_stats_consumer.pid"


class DeployMixin:

    # -- path resolution ---------------------------------------------------

    def _resolve_chroot(self, subpath: str) -> str:
        if os.path.isabs(subpath):
            return subpath
        return os.path.join(self.chroot, subpath.lstrip("/"))

    def _resolve_worktree(self, subpath: str) -> str:
        if not self.worktree:
            raise DeployError("No worktree configured", artifact="worktree")
        if os.path.isabs(subpath):
            return subpath
        return os.path.join(self.worktree, subpath.lstrip("/"))

    # -- hashing -----------------------------------------------------------

    @staticmethod
    def _local_md5(path: str) -> str:
        h = hashlib.md5()
        with open(path, "rb") as f:
            for chunk in iter(lambda: f.read(1 << 20), b""):
                h.update(chunk)
        return h.hexdigest()

    def _remote_md5(self, path: str) -> str | None:
        code, out, _ = self._transport.run(f"md5sum {path} 2>/dev/null", check=False)
        if code != 0 or not out.strip():
            return None
        return out.split()[0]

    @staticmethod
    def _local_md5_body(path: str) -> str:
        h = hashlib.md5()
        with open(path, "rb") as f:
            f.readline()
            for chunk in iter(lambda: f.read(1 << 20), b""):
                h.update(chunk)
        return h.hexdigest()

    def _remote_md5_body(self, path: str) -> str | None:
        code, out, _ = self._transport.run(
            f"tail -n +2 {path} | md5sum", check=False
        )
        if code != 0 or not out.strip():
            return None
        return out.split()[0]

    # -- pycache cleanup ---------------------------------------------------

    def _clean_pycache(self, container_path: str) -> None:
        base = os.path.splitext(os.path.basename(container_path))[0]
        cdir = os.path.dirname(container_path)
        self._transport.run(
            f"rm -f {cdir}/{base}.pyc {cdir}/{base}.pys", check=False
        )
        self._transport.run(
            f"rm -f {cdir}/__pycache__/{base}.cpython-*.pyc", check=False
        )

    # -- deploy: serial_console --------------------------------------------

    def deploy_serial_console(self) -> DeployResult:
        local_path = self._resolve_chroot("infoblox/one/bin/serial_console")
        dest = "/infoblox/one/bin/serial_console"
        staging = "/tmp/serial_console"

        if not os.path.isfile(local_path):
            raise DeployError(f"Local binary not found: {local_path}", artifact="serial_console")

        pre_hash = self._transport.host_checksum(dest)
        local_h = self._local_md5(local_path)

        # SCP to host /tmp/, then host-level cp into place
        # /infoblox/ is accessible from the host on NIOS appliances
        self._transport.upload_to_host(local_path, staging)

        # rm before cp avoids "text file busy" on a running binary
        code, _, err = self._transport.run_on_host(
            f"rm -f {dest} && cp {staging} {dest} && chmod +x {dest} && rm -f {staging}",
            check=False,
        )
        if code != 0:
            raise DeployError(f"Replace serial_console failed: {err.strip()}", artifact="serial_console")

        post_hash = self._transport.host_checksum(dest)
        if post_hash != local_h:
            raise DeployError(
                f"Post-deploy hash mismatch: local={local_h} remote={post_hash}",
                artifact="serial_console",
            )

        changed = pre_hash != post_hash
        restarted = False
        if changed:
            self._transport.run_on_host("killall serial_console 2>/dev/null || true", check=False)
            restarted = True
            log.info("serial_console killed — process_manager will respawn")

        return DeployResult(
            artifact="serial_console",
            changed=changed,
            local_hash=local_h,
            remote_hash=post_hash,
            restarted=restarted,
        )

    # -- deploy: shared library (.so) --------------------------------------

    def deploy_shared_lib(
        self,
        local_subpath: str = "infoblox/one/lib64/libibonedb_util.so",
        dest: str = "/infoblox/one/lib64/libibonedb_util.so",
        restart_target: str = "serial_console",
        backup: bool = True,
    ) -> DeployResult:
        local_path = self._resolve_chroot(local_subpath)
        staging = f"/tmp/{os.path.basename(dest)}"

        if not os.path.isfile(local_path):
            raise DeployError(f"Local library not found: {local_path}", artifact=dest)

        pre_hash = self._transport.host_checksum(dest)
        local_h = self._local_md5(local_path)

        self._transport.upload_to_host(local_path, staging)

        cmds = []
        if backup:
            cmds.append(f"cp {dest} {dest}.bak 2>/dev/null || true")
        cmds.append(f"cp {staging} {dest}")
        cmds.append(f"chmod 755 {dest}")
        cmds.append(f"rm -f {staging}")

        code, _, err = self._transport.run_on_host(" && ".join(cmds), check=False)
        if code != 0:
            raise DeployError(f"Replace {os.path.basename(dest)} failed: {err.strip()}", artifact=dest)

        post_hash = self._transport.host_checksum(dest)
        if post_hash != local_h:
            raise DeployError(
                f"Post-deploy hash mismatch: local={local_h} remote={post_hash}",
                artifact=dest,
            )

        changed = pre_hash != post_hash
        restarted = False
        if changed and restart_target:
            self._transport.run_on_host(f"killall {restart_target} 2>/dev/null || true", check=False)
            restarted = True
            log.info("%s killed — will respawn with new %s", restart_target, os.path.basename(dest))

        return DeployResult(
            artifact=os.path.basename(dest),
            changed=changed,
            local_hash=local_h,
            remote_hash=post_hash,
            restarted=restarted,
        )

    # -- deploy: token Python files ----------------------------------------

    def deploy_token_py(
        self,
        groups: set[str] | None = None,
        branding_source: str | None = None,
        skip_restart: bool = False,
    ) -> DeployResult:
        if groups is None:
            groups = {"branding", "ibap", "pyabs", "wapi"}

        manifest: list[tuple[str, str]] = []

        if "branding" in groups:
            src = branding_source or self._resolve_worktree(f"{PYABS_SUBPATH}/token_branding.py")
            manifest.append((src, f"{CONTAINER_PYABS}/token_branding.py"))

        if "ibap" in groups:
            for f in IBAP_FILES:
                manifest.append((
                    self._resolve_worktree(f"{IBAP_SUBPATH}/{f}"),
                    f"{CONTAINER_IBAP}/{f}",
                ))

        if "pyabs" in groups:
            for f in PYABS_FILES:
                manifest.append((
                    self._resolve_worktree(f"{PYABS_SUBPATH}/{f}"),
                    f"{CONTAINER_PYABS}/{f}",
                ))

        if "wapi" in groups:
            for f in WAPI_FILES:
                manifest.append((
                    self._resolve_worktree(f"{WAPI_SUBPATH}/{f}"),
                    f"{CONTAINER_WAPI}/{f}",
                ))

        if not manifest:
            raise DeployError("No files in manifest", artifact="token_py")

        # Skip (rather than fail) files absent from this worktree — some
        # branches predate newer files in the manifest (e.g. a feature added
        # after this branch was cut). Missing files are logged, not fatal.
        found = []
        skipped = []
        for local_path, dest in manifest:
            if os.path.isfile(local_path):
                found.append((local_path, dest))
            else:
                skipped.append(local_path)
                log.warning("Skipping missing file: %s", local_path)
        manifest = found

        if not manifest:
            raise DeployError(
                "No files in manifest were found on disk", artifact="token_py"
            )

        file_results = []
        for local_path, dest in manifest:
            local_h = self._local_md5(local_path)
            self._transport.upload(local_path, dest)
            self._clean_pycache(dest)

            remote_h = self._remote_md5(dest)
            if remote_h != local_h:
                raise DeployError(
                    f"Hash mismatch for {os.path.basename(dest)}: local={local_h} remote={remote_h}",
                    artifact=dest,
                )
            file_results.append({"file": os.path.basename(dest), "hash": local_h})

        restarted = False
        if not skip_restart:
            self._transport.run(
                "/infoblox/one/bin/pmc command stop admin-httpd 2>/dev/null || true",
                check=False,
            )
            # Don't rely on process_manager to auto-respawn admin-httpd —
            # after repeated stop/start cycles in a short window (e.g.
            # iterating on a hot-deploy loop), it can hit a crash-loop
            # backoff and stop auto-respawning entirely, leaving admin-httpd
            # down until something explicitly starts it again.
            time.sleep(2)
            self._transport.run(
                "/infoblox/one/bin/pmc command start admin-httpd 2>/dev/null || true",
                check=False,
            )
            restarted = True
            log.info("admin-httpd restarted")

        return DeployResult(
            artifact="token_py",
            changed=True,
            restarted=restarted,
            details={
                "files": file_results,
                "groups": sorted(groups),
                "skipped": skipped,
            },
        )

    # -- deploy: token_stats_consumer --------------------------------------

    def deploy_token_stats_consumer(
        self,
        skip_restart: bool = False,
    ) -> DeployResult:
        local_path = self._resolve_worktree(CONSUMER_SRC)

        if not os.path.isfile(local_path):
            raise DeployError(f"Source not found: {local_path}", artifact="token_stats_consumer")

        # Read existing shebang from container
        code, shebang, _ = self._transport.run(f"head -1 {CONSUMER_DEST} 2>/dev/null", check=False)
        if code != 0 or not shebang.strip().startswith("#!"):
            shebang = "#!/usr/bin/python3 -u"
        else:
            shebang = shebang.strip()

        # Create staging copy with the container's shebang
        with tempfile.NamedTemporaryFile(mode="w", suffix=".py", delete=False) as tmp:
            tmp_path = tmp.name
            with open(local_path) as src:
                src.readline()
                tmp.write(shebang + "\n")
                shutil.copyfileobj(src, tmp)

        try:
            self._transport.upload(tmp_path, CONSUMER_DEST)
            self._transport.run(f"chmod 755 {CONSUMER_DEST}", check=False)
        finally:
            os.unlink(tmp_path)

        local_body = self._local_md5_body(local_path)
        remote_body = self._remote_md5_body(CONSUMER_DEST)
        if local_body != remote_body:
            raise DeployError(
                f"Content mismatch (excl. shebang): local={local_body} remote={remote_body}",
                artifact="token_stats_consumer",
            )

        restarted = False
        pid = None
        if not skip_restart:
            self._transport.run(f"{CONSUMER_CONTROL} -c stop 2>/dev/null || true", check=False)
            time.sleep(2)

            code, _, err = self._transport.run(f"{CONSUMER_CONTROL} -c start", check=False)
            if code != 0:
                raise DeployError(f"Consumer start failed: {err.strip()}", artifact="token_stats_consumer")
            time.sleep(3)

            code, pid_str, _ = self._transport.run(f"cat {CONSUMER_PIDFILE} 2>/dev/null", check=False)
            if code != 0 or not pid_str.strip():
                raise DeployError("No PID file after restart", artifact="token_stats_consumer")

            pid = pid_str.strip()
            code, _, _ = self._transport.run(f"kill -0 {pid} 2>/dev/null", check=False)
            if code != 0:
                raise DeployError(f"Consumer PID {pid} is not alive", artifact="token_stats_consumer")

            restarted = True
            log.info("token_stats_consumer restarted (PID %s)", pid)

        return DeployResult(
            artifact="token_stats_consumer",
            changed=True,
            local_hash=local_body,
            remote_hash=remote_body,
            restarted=restarted,
            details={"pid": pid} if pid else {},
        )
