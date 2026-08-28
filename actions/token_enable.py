"""Token mode (IPc) actions, driven against an Appliance."""

from __future__ import annotations

import os
import subprocess
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from dominion import Appliance

_TOOLS_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "tools")
UPDATE_TOKEN_BUNDLE_SCRIPT = os.path.join(_TOOLS_DIR, "update_token_bundle.sh")


def enable_token_mode(app: Appliance) -> None:
    """Enable IPc (token) mode on an air-gapped appliance.

    Runs update_token_bundle.sh to rebuild and upload the token bundle,
    then drives the console wizard to set nios_usage_mode to IPc.

    Args:
        app: The appliance to enable token mode on.

    Raises:
        subprocess.CalledProcessError: If update_token_bundle.sh fails.
        RuntimeError: If the console does not confirm IPc mode enabled.
    """
    subprocess.run(
        [UPDATE_TOKEN_BUNDLE_SCRIPT, app.ip],
        input="y\ny\n",
        text=True,
        check=True,
    )

    with app.console_session() as console:
        console.sendline("set nios_usage_mode IPc")
        console.expect(r"Do you want to proceed\? \(y or n\)")
        console.sendline("y")
        output = console.expect("IPc mode enabled successfully")
        if "IPc mode enabled successfully" not in output:
            raise RuntimeError("Failed to enable IPc mode")

    app._logger.debug("IPc mode enabled")
