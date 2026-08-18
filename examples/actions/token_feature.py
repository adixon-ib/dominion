"""Token mode (IPc) actions, driven against an Appliance."""

from __future__ import annotations

import logging
import os
import subprocess
import sys

SCRIPTS_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, SCRIPTS_DIR)

from dominion import Appliance  # noqa: E402

log = logging.getLogger(__name__)

UPDATE_TOKEN_BUNDLE_SCRIPT = os.path.join(SCRIPTS_DIR, "appliance", "update_token_bundle.sh")


def enable_token_mode(app: Appliance) -> None:
    """Enable IPc (token) mode on an air-gapped appliance.

    update_token_bundle.sh rebuilds token_bundle.tar with app's lpc_uid,
    scp's it to /storage/tmp/token_bundle.tar, and touches airgapped_skip --
    required before "set nios_usage_mode IPc" will succeed on an air-gapped
    grid. It prompts for both steps; answer "y" to each.
    """
    subprocess.run(
        [UPDATE_TOKEN_BUNDLE_SCRIPT, app.ip],
        input="y\ny\n",
        text=True,
        check=True,
    )

    with app.console_session() as console:
        # "set nios_usage_mode IPc" asks "Do you want to proceed?" once before enabling.
        console.sendline("set nios_usage_mode IPc")
        console.expect(r"Do you want to proceed\? \(y or n\)")
        console.sendline("y")
        output = console.expect("IPc mode enabled successfully")
        if "IPc mode enabled successfully" not in output:
            raise RuntimeError("Failed to enable IPc mode")

    log.info("IPc mode enabled on %s", app.ip)
