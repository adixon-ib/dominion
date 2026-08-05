"""Unit tests for build.py: NiosBuild._run() command-string construction only.

`make install` is never actually invoked — subprocess.Popen is mocked. Real
build verification is intentionally left to manual/integration use (see the
module docstring in build.py): builds are destructive and slow, so there is
no integration test for this class either.
"""

import pytest

import nios_test.build as build_mod
from nios_test import BuildConfig, NiosBuild


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


def _build():
    cfg = BuildConfig(worktree="/blox/adixon1/my-branch", slot="balerion")
    return NiosBuild.from_config(cfg)


def test_quick_runs_touch_skip_slow_and_make_install(monkeypatch):
    captured = _install_fake_popen(monkeypatch)
    _build().quick()
    bash = captured["argv"][-1]
    assert "touch skip_slow && make install" in bash


def test_full_runs_installclean_build_env_install(monkeypatch):
    captured = _install_fake_popen(monkeypatch)
    _build().full()
    bash = captured["argv"][-1]
    assert "make installclean && make build-env && make install" in bash


def test_clean_runs_installclean_only(monkeypatch):
    captured = _install_fake_popen(monkeypatch)
    _build().clean()
    bash = captured["argv"][-1]
    assert bash.rstrip().endswith("make installclean")


def test_bash_command_includes_worktree_and_chroot(monkeypatch):
    captured = _install_fake_popen(monkeypatch)
    _build().quick()
    bash = captured["argv"][-1]
    assert "cd /blox/adixon1/my-branch &&" in bash
    assert "export ASLAN_INSTALL_ROOT=/local/91/chroots/balerion" in bash


def test_nonzero_returncode_raises_runtime_error(monkeypatch):
    _install_fake_popen(monkeypatch, returncode=1)
    with pytest.raises(RuntimeError):
        _build().quick()
