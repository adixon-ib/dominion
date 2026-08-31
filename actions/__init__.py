"""Multi-step NIOS workflows built on dominion's core building blocks.

Each function takes one or more Appliance/Container objects and drives a
complete operation (enable token mode, join a member to a grid, etc.).

    from dominion.actions import enable_token_mode, join_grid, leave_grid
"""

from .appliance_reset_all import reset_all
from .grid_join import join_grid
from .grid_leave import leave_grid
from .token_enable import enable_token_mode

__all__ = [
    "enable_token_mode",
    "join_grid",
    "leave_grid",
    "reset_all",
]
