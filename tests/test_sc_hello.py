#!/usr/bin/env python3
"""End-to-end test: sc_platform.c change prints "Hi there." on show nios_usage_mode.

Flow:
  0. Revert appliance: docker restart nios (undoes any binary left over from a prior run)
  1. Quick build (assumes balerion container is stopped)
  2. Pre-deploy: assert "Hi there." NOT in show nios_usage_mode output
  3. Deploy serial_console binary to vm-04-89
  4. Post-deploy: assert "Hi there." IN show nios_usage_mode output

Usage:
    python3 test_sc_hello.py

Stop balerion before running:
    slot stop balerion
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from helpers import (
    LOG_LEVEL,
    OUTPUT_DIR,
    appliance_config,
    build_config,
    summary,
    test,
)
from nios_test import Appliance, NiosBuild

SHOW_CMD = "show nios_usage_mode"
EXPECTED = "Hi there."

_appliance = Appliance.from_config(appliance_config())


def _show_nios_usage_mode() -> str:
    with _appliance.console_session() as console:
        return console.run(SHOW_CMD)


# ── Step 0: Revert appliance to original binary ──────────────────────────────

def step_revert():
    _appliance.revert()

test("revert appliance: docker restart nios", step_revert)

# ── Step 1: Quick build ────────────────────────────────────────────────────────

def step_build():
    build = NiosBuild.from_config(build_config(log_level=LOG_LEVEL))
    build.quick()

test("quick build", step_build)

# ── Step 2: Pre-deploy check ──────────────────────────────────────────────────

def step_pre_deploy_check():
    out = _show_nios_usage_mode()
    print(f"  output: {out!r}")
    assert EXPECTED not in out, (
        f"'{EXPECTED}' appeared before deploy — old binary already on appliance?"
    )

test("pre-deploy: 'Hi there.' absent", step_pre_deploy_check)

# ── Step 3: Deploy ────────────────────────────────────────────────────────────

def step_deploy():
    result = _appliance.deploy_serial_console()
    print(f"  artifact: {result.artifact}")
    print(f"  details:  {result.details}")

test("deploy serial_console to vm-04-89", step_deploy)

# ── Step 4: Post-deploy check ─────────────────────────────────────────────────

def step_post_deploy_check():
    out = _show_nios_usage_mode()
    print(f"  output: {out!r}")
    assert EXPECTED in out, (
        f"'{EXPECTED}' not found after deploy.\nFull output: {out}"
    )

test("post-deploy: 'Hi there.' present", step_post_deploy_check)

summary()
