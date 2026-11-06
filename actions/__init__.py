"""Multi-step NIOS workflows built on dominion's core building blocks.

Each function takes one or more Appliance/Container objects and drives a
complete operation (enable token mode, join a member to a grid, etc.).

    from dominion.actions import enable_token_mode, join_grid, leave_grid
"""

from .appliance_reset_all import reset_all
from .grid_join import join_grid
from .grid_leave import leave_grid
from .token_enable import (
    BUNDLE_OBJECT,
    enable_token_mode,
    enable_token_mode_wapi,
    patched_bundle,
    upload_bundle_token,
)

__all__ = [
    "BUNDLE_OBJECT",
    "enable_token_mode",
    "enable_token_mode_wapi",
    "join_grid",
    "leave_grid",
    "patched_bundle",
    "reset_all",
    "upload_bundle_token",
]
