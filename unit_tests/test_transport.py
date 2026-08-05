"""Unit tests for transport.py: argv construction and output parsing.

subprocess.run is mocked throughout — no real ssh/scp/docker process is ever
spawned.
"""

import subprocess

import pytest

from nios_test import CommandError, LocalDockerTransport, SSHDockerTransport, TransportError, connect


class FakeCompletedProcess:
    def __init__(self, returncode=0, stdout="", stderr=""):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


def _install_fake_run(monkeypatch, module, returncode=0, stdout="", stderr=""):
    calls = []

    def fake_run(argv, **kwargs):
        calls.append(argv)
        return FakeCompletedProcess(returncode, stdout, stderr)

    monkeypatch.setattr(module.subprocess, "run", fake_run)
    return calls


# -- SSHDockerTransport ----------------------------------------------------------

def test_ssh_host_cmd_prefix_basic():
    t = SSHDockerTransport(host="10.35.211.4", container_name="nios")
    assert t.host_cmd_prefix == (
        "ssh -o StrictHostKeyChecking=no -o LogLevel=ERROR -o ConnectTimeout=10 root@10.35.211.4"
    )


def test_ssh_host_cmd_prefix_includes_port_and_key():
    t = SSHDockerTransport(host="10.35.211.4", port=2222, key_path="/home/x/.ssh/id_rsa")
    prefix = t.host_cmd_prefix
    assert "-p 2222" in prefix
    assert "-i /home/x/.ssh/id_rsa" in prefix


def test_ssh_run_builds_docker_exec_over_ssh(monkeypatch):
    import nios_test.transport as transport_mod
    calls = _install_fake_run(monkeypatch, transport_mod, stdout="myhost\n")
    t = SSHDockerTransport(host="10.35.211.4", container_name="nios")

    code, out, _ = t.run("hostname")

    assert code == 0
    assert out == "myhost\n"
    argv = calls[0]
    assert argv[0] == "ssh"
    assert argv[-1] == "docker exec nios hostname"


def test_ssh_run_on_host_skips_docker_exec(monkeypatch):
    import nios_test.transport as transport_mod
    calls = _install_fake_run(monkeypatch, transport_mod, stdout="ok\n")
    t = SSHDockerTransport(host="10.35.211.4")

    t.run_on_host("docker ps")

    assert calls[0][-1] == "docker ps"


def test_ssh_run_raises_command_error_on_nonzero_exit(monkeypatch):
    import nios_test.transport as transport_mod
    _install_fake_run(monkeypatch, transport_mod, returncode=1, stderr="boom")
    t = SSHDockerTransport(host="10.35.211.4")

    with pytest.raises(CommandError) as exc_info:
        t.run("false")
    assert exc_info.value.exit_code == 1
    assert exc_info.value.stderr == "boom"


def test_ssh_run_check_false_returns_nonzero_without_raising(monkeypatch):
    import nios_test.transport as transport_mod
    _install_fake_run(monkeypatch, transport_mod, returncode=1, stderr="boom")
    t = SSHDockerTransport(host="10.35.211.4")

    code, _, err = t.run("false", check=False)
    assert code == 1
    assert err == "boom"


def test_ssh_checksum_parses_md5sum_output(monkeypatch):
    import nios_test.transport as transport_mod
    _install_fake_run(monkeypatch, transport_mod, stdout="deadbeef00112233445566778899aabb  /etc/hostname\n")
    t = SSHDockerTransport(host="10.35.211.4")

    assert t.checksum("/etc/hostname") == "deadbeef00112233445566778899aabb"


def test_ssh_is_container_running_true(monkeypatch):
    import nios_test.transport as transport_mod
    _install_fake_run(monkeypatch, transport_mod, stdout="true\n")
    t = SSHDockerTransport(host="10.35.211.4")

    assert t.is_container_running() is True


def test_ssh_is_container_running_false_on_nonzero_exit(monkeypatch):
    import nios_test.transport as transport_mod
    _install_fake_run(monkeypatch, transport_mod, returncode=1, stdout="")
    t = SSHDockerTransport(host="10.35.211.4")

    assert t.is_container_running() is False


def test_ssh_exec_translates_timeout_to_transport_error(monkeypatch):
    import nios_test.transport as transport_mod

    def fake_run(argv, **kwargs):
        raise subprocess.TimeoutExpired(cmd=argv, timeout=1)

    monkeypatch.setattr(transport_mod.subprocess, "run", fake_run)
    t = SSHDockerTransport(host="10.35.211.4", timeout=1)

    with pytest.raises(TransportError):
        t.run("sleep 100")


def test_ssh_upload_scps_then_docker_cps_then_cleans_up(monkeypatch, tmp_path):
    import nios_test.transport as transport_mod
    local = tmp_path / "file.py"
    local.write_text("content")
    calls = _install_fake_run(monkeypatch, transport_mod)
    t = SSHDockerTransport(host="10.35.211.4", container_name="nios")

    t.upload(str(local), "/infoblox/one/lib/python/file.py")

    assert any(c[0] == "scp" for c in calls)
    assert any("docker cp" in " ".join(c) for c in calls)
    assert any("rm -f" in " ".join(c) for c in calls)


def test_ssh_upload_raises_when_local_file_missing():
    t = SSHDockerTransport(host="10.35.211.4")
    with pytest.raises(TransportError):
        t.upload("/does/not/exist.py", "/remote/path.py")


# -- LocalDockerTransport --------------------------------------------------------

def test_local_run_builds_docker_exec_bash_c(monkeypatch):
    import nios_test.transport as transport_mod
    calls = _install_fake_run(monkeypatch, transport_mod, stdout=b"myhost\n")
    t = LocalDockerTransport(container_name="balerion")

    code, out, _ = t.run("hostname")

    assert code == 0
    assert out == "myhost\n"
    assert calls[0] == ["docker", "exec", "balerion", "bash", "-c", "hostname"]


def test_local_run_on_host_uses_shlex_split(monkeypatch):
    import nios_test.transport as transport_mod
    calls = _install_fake_run(monkeypatch, transport_mod, stdout=b"")
    t = LocalDockerTransport(container_name="balerion")

    t.run_on_host("docker ps -a")

    assert calls[0] == ["docker", "ps", "-a"]


def test_local_run_raises_command_error_on_nonzero_exit(monkeypatch):
    import nios_test.transport as transport_mod
    _install_fake_run(monkeypatch, transport_mod, returncode=1, stdout=b"", stderr=b"boom")
    t = LocalDockerTransport(container_name="balerion")

    with pytest.raises(CommandError):
        t.run("false")


def test_local_upload_pipes_file_bytes_via_tee(monkeypatch, tmp_path):
    import nios_test.transport as transport_mod
    local = tmp_path / "file.py"
    local.write_text("hello")
    captured = {}

    def fake_run(argv, **kwargs):
        captured["argv"] = argv
        captured["input"] = kwargs.get("input")
        return FakeCompletedProcess(0, b"", b"")

    monkeypatch.setattr(transport_mod.subprocess, "run", fake_run)
    t = LocalDockerTransport(container_name="balerion")

    t.upload(str(local), "/infoblox/one/lib/python/file.py")

    assert captured["argv"] == ["docker", "exec", "-i", "balerion", "tee", "/infoblox/one/lib/python/file.py"]
    assert captured["input"] == b"hello"


def test_local_host_checksum_reads_real_file(tmp_path):
    local = tmp_path / "file.txt"
    local.write_bytes(b"some content")
    t = LocalDockerTransport(container_name="balerion")

    import hashlib
    expected = hashlib.md5(b"some content").hexdigest()
    assert t.host_checksum(str(local)) == expected


def test_local_checksum_parses_md5sum_output(monkeypatch):
    import nios_test.transport as transport_mod
    _install_fake_run(monkeypatch, transport_mod, stdout=b"cafebabe00112233445566778899aabb  /etc/hostname\n")
    t = LocalDockerTransport(container_name="balerion")

    assert t.checksum("/etc/hostname") == "cafebabe00112233445566778899aabb"


# -- connect() --------------------------------------------------------------------

def test_connect_no_host_returns_local_transport():
    t = connect(host=None, container_name="balerion")
    assert isinstance(t, LocalDockerTransport)
    assert t.container_name == "balerion"


def test_connect_with_host_returns_ssh_transport():
    t = connect(host="10.35.211.4", container_name="nios", user="root")
    assert isinstance(t, SSHDockerTransport)
    assert t.host == "10.35.211.4"


# -- CommandError -----------------------------------------------------------------

def test_command_error_message_includes_cmd_and_stderr():
    err = CommandError(cmd="false", exit_code=1, stdout="", stderr="permission denied")
    assert "false" in str(err)
    assert "permission denied" in str(err)
    assert err.exit_code == 1
