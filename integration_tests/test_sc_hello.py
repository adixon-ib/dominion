"""End-to-end test: sc_platform.c change prints "Hi there." on show nios_usage_mode.

Flow:
  0. Revert appliance: docker restart nios (undoes any binary left over from a prior run)
  1. Quick build (assumes balerion container is stopped)
  2. Pre-deploy: assert "Hi there." NOT in show nios_usage_mode output
  3. Deploy serial_console binary to vm-04-89
  4. Post-deploy: assert "Hi there." IN show nios_usage_mode output

Destructive (rebuilds + deploys a real binary) — excluded by default. Usage:

    pytest integration_tests/test_sc_hello.py -m destructive

Stop balerion before running:
    slot stop balerion

Order-sensitive: pytest runs test functions top-to-bottom within a file by
default (no reordering plugin installed here), which this test relies on —
each step depends on the previous one having actually happened.
"""

import pytest

from helpers import LOG_LEVEL, build_config
from nios_test import NiosBuild

pytestmark = [pytest.mark.destructive, pytest.mark.appliance]

SHOW_CMD = "show nios_usage_mode"
EXPECTED = "Hi there."


def _show_nios_usage_mode(appliance) -> str:
    with appliance.console_session() as console:
        return console.run(SHOW_CMD)


def test_step0_revert_appliance(ssh_appliance):
    ssh_appliance.revert()


def test_step1_quick_build():
    build = NiosBuild.from_config(build_config(log_level=LOG_LEVEL))
    build.quick()


def test_step2_pre_deploy_hi_there_absent(ssh_appliance):
    out = _show_nios_usage_mode(ssh_appliance)
    assert EXPECTED not in out, (
        f"'{EXPECTED}' appeared before deploy — old binary already on appliance?"
    )


def test_step3_deploy_serial_console(ssh_appliance):
    result = ssh_appliance.deploy_serial_console()
    assert result.artifact == "serial_console"


def test_step4_post_deploy_hi_there_present(ssh_appliance):
    out = _show_nios_usage_mode(ssh_appliance)
    assert EXPECTED in out, f"'{EXPECTED}' not found after deploy.\nFull output: {out}"
