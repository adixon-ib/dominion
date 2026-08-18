"""Shared fakes for dominion unit tests.

Nothing here touches a network, docker, or a real pexpect spawn. `FakeTransport`
stands in for SSHDockerTransport/LocalDockerTransport — it tracks real file
*content* (not just hashes) for anything "uploaded", so it can honestly answer
the `md5sum`/`tail -n +2 ... | md5sum` shell commands deploy.py issues, and
`corrupt_uploads=True` lets a test deliberately trigger the hash-mismatch
branch of a deploy method. `FakeChild` stands in for a pexpect.spawn child.
"""

from __future__ import annotations

import hashlib
import os
import re
import sys

SCRIPTS_DIR = os.path.dirname(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
)
if SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, SCRIPTS_DIR)

import pytest

from dominion import CommandError
from dominion.src.deploy import (
    CONSUMER_SRC,
    IBAP_FILES,
    IBAP_SUBPATH,
    NIOS_MODEL_CLOUDINIT_MANIFEST,
    PYABS_FILES,
    PYABS_SUBPATH,
    WAPI_FILES,
    WAPI_SUBPATH,
)


class FakeTransport:
    """Duck-typed stand-in for SSHDockerTransport/LocalDockerTransport."""

    _MD5SUM_RE = re.compile(r"^md5sum\s+(\S+)")
    _TAIL_MD5SUM_RE = re.compile(r"^tail -n \+2\s+(\S+)\s*\|\s*md5sum")
    _CP_RE = re.compile(r"\bcp\s+(\S+)\s+(\S+)")

    def __init__(
        self,
        container_name="fake",
        responses=None,
        default_response=(0, "", ""),
        corrupt_uploads=False,
    ):
        self.container_name = container_name
        self.calls: list[tuple] = []
        self.responses = responses or {}
        self.default_response = default_response
        self.corrupt_uploads = corrupt_uploads
        self.uploaded_content: dict[str, bytes] = {}   # container-side path -> bytes
        self.host_content: dict[str, bytes] = {}       # host-side path -> bytes
        self.container_running = True

    @staticmethod
    def _hash(data: bytes) -> str:
        return hashlib.md5(data).hexdigest()

    def _respond(self, cmd: str) -> tuple[int, str, str]:
        for key, resp in self.responses.items():
            if key in cmd:
                return resp
        return self.default_response

    def run(self, cmd: str, check: bool = True) -> tuple[int, str, str]:
        self.calls.append(("run", cmd))
        m = self._MD5SUM_RE.match(cmd)
        if m and m.group(1) in self.uploaded_content:
            digest = self._hash(self.uploaded_content[m.group(1)])
            return 0, f"{digest}  {m.group(1)}\n", ""
        m = self._TAIL_MD5SUM_RE.match(cmd)
        if m and m.group(1) in self.uploaded_content:
            parts = self.uploaded_content[m.group(1)].split(b"\n", 1)
            rest = parts[1] if len(parts) > 1 else b""
            return 0, f"{self._hash(rest)}  -\n", ""
        rc, out, err = self._respond(cmd)
        if check and rc != 0:
            raise CommandError(cmd=cmd, exit_code=rc, stdout=out, stderr=err)
        return rc, out, err

    def run_on_host(self, cmd: str, check: bool = True) -> tuple[int, str, str]:
        self.calls.append(("run_on_host", cmd))
        for m in self._CP_RE.finditer(cmd):
            src, dst = m.group(1), m.group(2)
            if src in self.host_content:
                self.host_content[dst] = self.host_content[src]
        rc, out, err = self._respond(cmd)
        if check and rc != 0:
            raise CommandError(cmd=cmd, exit_code=rc, stdout=out, stderr=err)
        return rc, out, err

    def _read(self, local_path: str) -> bytes:
        with open(local_path, "rb") as f:
            data = f.read()
        if self.corrupt_uploads:
            data += b"\x00corrupt"
        return data

    def upload(self, local_path: str, remote_path: str) -> None:
        self.calls.append(("upload", local_path, remote_path))
        self.uploaded_content[remote_path] = self._read(local_path)

    def upload_to_host(self, local_path: str, remote_path: str) -> None:
        self.calls.append(("upload_to_host", local_path, remote_path))
        self.host_content[remote_path] = self._read(local_path)

    def checksum(self, remote_path: str) -> str:
        self.calls.append(("checksum", remote_path))
        data = self.uploaded_content.get(remote_path)
        return self._hash(data) if data is not None else "0" * 32

    def host_checksum(self, remote_path: str) -> str:
        self.calls.append(("host_checksum", remote_path))
        data = self.host_content.get(remote_path)
        return self._hash(data) if data is not None else "0" * 32

    def is_container_running(self) -> bool:
        return self.container_running


@pytest.fixture
def fake_transport() -> FakeTransport:
    return FakeTransport()


@pytest.fixture
def fake_transport_factory():
    return FakeTransport


@pytest.fixture
def worktree_dir(tmp_path):
    """A tmp worktree with dummy files at every path deploy_token_py/
    deploy_token_stats_consumer expect, so os.path.isfile() checks and
    _local_md5() reads succeed against real (throwaway) files."""
    wt = tmp_path / "worktree"
    for subpath, files in (
        (PYABS_SUBPATH, PYABS_FILES),
        (WAPI_SUBPATH, WAPI_FILES),
        (IBAP_SUBPATH, IBAP_FILES),
    ):
        d = wt / subpath
        d.mkdir(parents=True, exist_ok=True)
        for f in files:
            (d / f).write_text(f"# dummy {f}\n")
    (wt / PYABS_SUBPATH / "token_branding.py").write_text("# dummy branding\n")

    consumer = wt / CONSUMER_SRC
    consumer.parent.mkdir(parents=True, exist_ok=True)
    consumer.write_text("#!/usr/bin/python3 -u\nprint('consumer')\n")

    for src, _dest in NIOS_MODEL_CLOUDINIT_MANIFEST:
        f = wt / src
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(f"# dummy {os.path.basename(src)}\n")

    return wt


@pytest.fixture
def chroot_dir(tmp_path):
    """A tmp chroot with dummy binary/lib files for deploy_serial_console
    and deploy_shared_lib."""
    c = tmp_path / "chroot"
    sc = c / "infoblox/one/bin/serial_console"
    sc.parent.mkdir(parents=True, exist_ok=True)
    sc.write_bytes(b"FAKE_SERIAL_CONSOLE_BINARY")

    lib = c / "infoblox/one/lib64/libibonedb_util.so"
    lib.parent.mkdir(parents=True, exist_ok=True)
    lib.write_bytes(b"FAKE_SHARED_LIB")

    return c


class FakeChild:
    """Duck-typed stand-in for pexpect.spawn.

    `script` is a list consumed in order by `expect()`: each item is either
    an `(index, before, after)` tuple, or an exception instance to raise
    (e.g. `pexpect.TIMEOUT("...")`, `pexpect.EOF("...")`).
    """

    def __init__(self, script=None):
        self._script = list(script or [])
        self.sent: list[tuple[str, str]] = []
        self.before = ""
        self.after = ""
        self.match_index = None
        self._alive = True

    def expect(self, pattern, timeout=None):
        if not self._script:
            raise AssertionError("FakeChild script exhausted")
        item = self._script.pop(0)
        if isinstance(item, BaseException):
            raise item
        idx, before, after = item
        self.before = before
        self.after = after
        self.match_index = idx
        return idx

    def send(self, text):
        self.sent.append(("send", text))

    def sendline(self, text):
        self.sent.append(("sendline", text))

    def isalive(self):
        return self._alive

    def close(self, force=False):
        self._alive = False


@pytest.fixture
def fake_child_factory():
    return FakeChild
