"""Unit tests for build.py: NiosBuild._run() command-string construction only.

`make install` is never actually invoked — subprocess.Popen is mocked. Real
build verification is intentionally left to manual/integration use (see the
module docstring in build.py): builds are destructive and slow, so there is
no integration test for this class either.
"""

import os
from unittest.mock import Mock

import pytest

import dominion.src.build as build_mod
from dominion import BuildConfig, NiosBuild


class FakeProc:
    def __init__(self, returncode=0, lines=()):
        self.stdout = list(lines)
        self.returncode = returncode

    def wait(self):
        return self.returncode


def _install_fake_popen(monkeypatch, returncode=0):
    captured = {}

    def fake_popen(argv, **kwargs):
        captured["argv"] = argv
        return FakeProc(returncode=returncode)

    monkeypatch.setattr(build_mod.subprocess, "Popen", fake_popen)
    return captured


def _install_container_not_running(monkeypatch):
    original_run = build_mod.subprocess.run

    def fake_run(cmd, *args, **kwargs):
        if cmd and "docker" in cmd[0]:
            return Mock(returncode=0, stdout="false\n", stderr="")
        return original_run(cmd, *args, **kwargs)

    monkeypatch.setattr(build_mod.subprocess, "run", fake_run)


@pytest.fixture
def worktree(tmp_path):
    wt = tmp_path / "my-branch"
    wt.mkdir()
    return str(wt)


def _build(worktree_path=None):
    cfg = BuildConfig(
        worktree=worktree_path or "/blox/adixon1/my-branch",
        slot="balerion",
    )
    return NiosBuild.from_config(cfg)


def test_quick_runs_touch_skip_slow_and_make_install(monkeypatch, worktree):
    _install_container_not_running(monkeypatch)
    captured = _install_fake_popen(monkeypatch)
    _build(worktree).quick()
    bash = captured["argv"][-1]
    assert "touch skip_slow && make install" in bash


def test_full_runs_installclean_build_env_install(monkeypatch, worktree):
    _install_container_not_running(monkeypatch)
    captured = _install_fake_popen(monkeypatch)
    _build(worktree).full()
    bash = captured["argv"][-1]
    assert "make installclean && make build-env && make install" in bash


def test_clean_runs_installclean_only(monkeypatch, worktree):
    _install_container_not_running(monkeypatch)
    captured = _install_fake_popen(monkeypatch)
    _build(worktree).clean()
    bash = captured["argv"][-1]
    assert bash.rstrip().endswith("make installclean")


def test_bash_command_includes_worktree_and_chroot(monkeypatch, worktree):
    _install_container_not_running(monkeypatch)
    captured = _install_fake_popen(monkeypatch)
    _build(worktree).quick()
    bash = captured["argv"][-1]
    assert f"cd {worktree} &&" in bash
    assert "export ASLAN_INSTALL_ROOT=/local/91/chroots/balerion" in bash


def test_nonzero_returncode_raises_runtime_error(monkeypatch, worktree):
    _install_container_not_running(monkeypatch)
    _install_fake_popen(monkeypatch, returncode=1)
    with pytest.raises(RuntimeError):
        _build(worktree).quick()


# -- Safety check tests -------------------------------------------------------


def test_run_rejects_nonexistent_worktree(monkeypatch):
    _install_container_not_running(monkeypatch)
    _install_fake_popen(monkeypatch)
    with pytest.raises(RuntimeError, match="Worktree does not exist"):
        _build("/nonexistent/path").quick()


def test_run_rejects_container_running(monkeypatch, worktree):
    monkeypatch.setattr(
        build_mod.subprocess, "run",
        lambda cmd, **kw: Mock(returncode=0, stdout="true\n", stderr=""),
    )
    _install_fake_popen(monkeypatch)
    with pytest.raises(RuntimeError, match="container is running"):
        _build(worktree).quick()


def test_is_container_running_false(monkeypatch, worktree):
    monkeypatch.setattr(
        build_mod.subprocess, "run",
        lambda cmd, **kw: Mock(returncode=0, stdout="false\n", stderr=""),
    )
    build = _build(worktree)
    assert build._is_container_running() is False


def test_is_container_running_true(monkeypatch, worktree):
    monkeypatch.setattr(
        build_mod.subprocess, "run",
        lambda cmd, **kw: Mock(returncode=0, stdout="true\n", stderr=""),
    )
    build = _build(worktree)
    assert build._is_container_running() is True
