"""Unit tests for deploy.py: DeployMixin logic, driven against a real
NiosContainer wired to a FakeTransport (no docker/ssh/network involved) plus
real (throwaway) files on disk for the "local" side.
"""

import logging

import pytest

from nios_test import DeployError, NiosContainer
from nios_test.src.deploy import CONSUMER_CONTROL, CONSUMER_DEST, CONSUMER_PIDFILE

LOGGER = logging.getLogger("nios_test.unit_test")


def make_container(transport, chroot, worktree):
    return NiosContainer(
        container_name="fake",
        transport=transport,
        chroot=str(chroot),
        worktree=str(worktree),
        logger=LOGGER,
    )


# -- path resolution -------------------------------------------------------------

def test_resolve_chroot_joins_relative_subpath(
    chroot_dir, worktree_dir, fake_transport,
):
    c = make_container(fake_transport, chroot_dir, worktree_dir)
    assert c._resolve_chroot("infoblox/one/bin/serial_console") == str(
        chroot_dir / "infoblox/one/bin/serial_console"
    )


def test_resolve_chroot_absolute_path_passthrough(
    chroot_dir, worktree_dir, fake_transport,
):
    c = make_container(fake_transport, chroot_dir, worktree_dir)
    assert c._resolve_chroot("/already/absolute") == "/already/absolute"


def test_resolve_worktree_raises_without_worktree(chroot_dir, fake_transport):
    c = NiosContainer(
        container_name="fake", transport=fake_transport,
        chroot=str(chroot_dir), logger=LOGGER,
    )
    with pytest.raises(DeployError):
        c._resolve_worktree("some/relative/path.py")


# -- hashing ------------------------------------------------------------------

def test_local_md5_matches_hashlib(tmp_path, chroot_dir, worktree_dir, fake_transport):
    import hashlib
    f = tmp_path / "x.txt"
    f.write_bytes(b"hello world")
    c = make_container(fake_transport, chroot_dir, worktree_dir)
    assert c._local_md5(str(f)) == hashlib.md5(b"hello world").hexdigest()


def test_local_md5_body_skips_first_line(
    tmp_path, chroot_dir, worktree_dir, fake_transport,
):
    import hashlib
    f = tmp_path / "x.py"
    f.write_bytes(b"#!/usr/bin/python3\nbody line 1\nbody line 2\n")
    c = make_container(fake_transport, chroot_dir, worktree_dir)
    expected = hashlib.md5(b"body line 1\nbody line 2\n").hexdigest()
    assert c._local_md5_body(str(f)) == expected


# -- deploy_serial_console -----------------------------------------------------

def test_deploy_serial_console_success(chroot_dir, worktree_dir, fake_transport):
    c = make_container(fake_transport, chroot_dir, worktree_dir)
    result = c.deploy_serial_console()

    assert result.artifact == "serial_console"
    assert result.changed is True
    assert result.restarted is True
    assert result.local_hash == result.remote_hash
    assert any(
        "killall serial_console" in call[1]
        for call in fake_transport.calls if call[0] == "run_on_host"
    )


def test_deploy_serial_console_raises_on_hash_mismatch(
    chroot_dir, worktree_dir, fake_transport_factory,
):
    transport = fake_transport_factory(corrupt_uploads=True)
    c = make_container(transport, chroot_dir, worktree_dir)
    with pytest.raises(DeployError):
        c.deploy_serial_console()


def test_deploy_serial_console_missing_binary_raises(
    tmp_path, worktree_dir, fake_transport,
):
    empty_chroot = tmp_path / "empty_chroot"
    empty_chroot.mkdir()
    c = make_container(fake_transport, empty_chroot, worktree_dir)
    with pytest.raises(DeployError):
        c.deploy_serial_console()


# -- deploy_shared_lib ----------------------------------------------------------

def test_deploy_shared_lib_success_kills_restart_target(
    chroot_dir, worktree_dir, fake_transport,
):
    c = make_container(fake_transport, chroot_dir, worktree_dir)
    result = c.deploy_shared_lib(restart_target="serial_console")

    assert result.artifact == "libibonedb_util.so"
    assert result.changed is True
    assert result.restarted is True
    assert any(
        "killall serial_console" in call[1]
        for call in fake_transport.calls if call[0] == "run_on_host"
    )


def test_deploy_shared_lib_backup_flag_issues_cp_bak(
    chroot_dir, worktree_dir, fake_transport,
):
    c = make_container(fake_transport, chroot_dir, worktree_dir)
    c.deploy_shared_lib(backup=True)
    assert any(
        ".bak" in call[1] for call in fake_transport.calls if call[0] == "run_on_host"
    )


def test_deploy_shared_lib_no_restart_target_skips_kill(
    chroot_dir, worktree_dir, fake_transport,
):
    c = make_container(fake_transport, chroot_dir, worktree_dir)
    result = c.deploy_shared_lib(restart_target="")
    assert result.restarted is False
    assert not any(
        "killall" in call[1]
        for call in fake_transport.calls if call[0] == "run_on_host"
    )


# -- deploy_token_py -------------------------------------------------------------

def test_deploy_token_py_default_groups_uploads_all_categories(
    chroot_dir, worktree_dir, fake_transport,
):
    c = make_container(fake_transport, chroot_dir, worktree_dir)
    result = c.deploy_token_py()

    assert result.artifact == "token_py"
    assert sorted(result.details["groups"]) == ["branding", "ibap", "pyabs", "wapi"]
    uploaded_dests = {call[2] for call in fake_transport.calls if call[0] == "upload"}
    assert any("wapi/grid_token_pool.py" in d for d in uploaded_dests)
    assert any(d.endswith("/tokenobj.py") for d in uploaded_dests)
    assert any(d.endswith("/token_branding.py") for d in uploaded_dests)


def test_deploy_token_py_restricted_groups_only_uploads_those(
    chroot_dir, worktree_dir, fake_transport,
):
    c = make_container(fake_transport, chroot_dir, worktree_dir)
    result = c.deploy_token_py(groups={"pyabs"})

    assert result.details["groups"] == ["pyabs"]
    uploaded_dests = {call[2] for call in fake_transport.calls if call[0] == "upload"}
    assert not any("/wapi/" in d for d in uploaded_dests)
    assert not any(d.endswith("tokenobj.py") for d in uploaded_dests)


def test_deploy_token_py_stops_admin_httpd_unless_skip_restart(
    chroot_dir, worktree_dir, fake_transport,
):
    c = make_container(fake_transport, chroot_dir, worktree_dir)
    result = c.deploy_token_py(groups={"pyabs"}, skip_restart=True)
    assert result.restarted is False
    assert not any(
        "admin-httpd" in call[1] for call in fake_transport.calls if call[0] == "run"
    )


def test_deploy_token_py_missing_local_file_raises(
    chroot_dir, tmp_path, fake_transport,
):
    empty_worktree = tmp_path / "empty_worktree"
    empty_worktree.mkdir()
    c = make_container(fake_transport, chroot_dir, empty_worktree)
    with pytest.raises(DeployError):
        c.deploy_token_py(groups={"pyabs"})


def test_deploy_token_py_raises_on_hash_mismatch(
    chroot_dir, worktree_dir, fake_transport_factory,
):
    transport = fake_transport_factory(corrupt_uploads=True)
    c = make_container(transport, chroot_dir, worktree_dir)
    with pytest.raises(DeployError):
        c.deploy_token_py(groups={"pyabs"})


def test_deploy_token_py_cleans_pycache_per_file(
    chroot_dir, worktree_dir, fake_transport,
):
    c = make_container(fake_transport, chroot_dir, worktree_dir)
    c.deploy_token_py(groups={"pyabs"})
    run_cmds = [call[1] for call in fake_transport.calls if call[0] == "run"]
    assert any("__pycache__" in cmd for cmd in run_cmds)


# -- deploy_token_stats_consumer -------------------------------------------------

def test_deploy_token_stats_consumer_preserves_container_shebang(
    chroot_dir, worktree_dir, fake_transport_factory,
):
    transport = fake_transport_factory(
        responses={f"head -1 {CONSUMER_DEST}": (0, "#!/usr/bin/python3.11 -u\n", "")},
    )
    c = make_container(transport, chroot_dir, worktree_dir)
    # Restart flow needs a PID file present after "start".
    transport.responses[CONSUMER_PIDFILE] = (0, "1234\n", "")
    transport.responses["kill -0 1234"] = (0, "", "")

    result = c.deploy_token_stats_consumer()

    assert result.artifact == "token_stats_consumer"
    assert result.local_hash == result.remote_hash
    uploaded = transport.uploaded_content[CONSUMER_DEST]
    assert uploaded.startswith(b"#!/usr/bin/python3.11 -u\n")


def test_deploy_token_stats_consumer_defaults_shebang_when_absent(
    chroot_dir, worktree_dir, fake_transport_factory,
):
    transport = fake_transport_factory()
    transport.responses[CONSUMER_PIDFILE] = (0, "1234\n", "")
    transport.responses["kill -0 1234"] = (0, "", "")
    c = make_container(transport, chroot_dir, worktree_dir)

    c.deploy_token_stats_consumer()

    uploaded = transport.uploaded_content[CONSUMER_DEST]
    assert uploaded.startswith(b"#!/usr/bin/python3 -u\n")


def test_deploy_token_stats_consumer_skip_restart_does_not_touch_control(
    chroot_dir, worktree_dir, fake_transport,
):
    c = make_container(fake_transport, chroot_dir, worktree_dir)
    result = c.deploy_token_stats_consumer(skip_restart=True)
    assert result.restarted is False
    assert not any(
        CONSUMER_CONTROL in call[1] for call in fake_transport.calls if call[0] == "run"
    )


def test_deploy_token_stats_consumer_no_pidfile_raises(
    chroot_dir, worktree_dir, fake_transport,
):
    c = make_container(fake_transport, chroot_dir, worktree_dir)
    with pytest.raises(DeployError):
        c.deploy_token_stats_consumer()


def test_deploy_token_stats_consumer_dead_pid_raises(
    chroot_dir, worktree_dir, fake_transport_factory,
):
    transport = fake_transport_factory()
    transport.responses[CONSUMER_PIDFILE] = (0, "1234\n", "")
    transport.responses["kill -0 1234"] = (1, "", "no such process")
    c = make_container(transport, chroot_dir, worktree_dir)
    with pytest.raises(DeployError):
        c.deploy_token_stats_consumer()
