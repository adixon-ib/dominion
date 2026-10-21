"""Multi-step NIOS workflows built on dominion's core building blocks.

Each function takes one or more Appliance/Container objects and drives a
complete operation (enable token mode, join a member to a grid, etc.).

    from dominion.actions import enable_token_mode, join_grid, leave_grid
"""

from .appliance_reset_all import reset_all
from .fresh_install import fresh_install
from .grid_join import join_grid
from .grid_leave import leave_grid
from .licenses import ipc_preflight, licenses
from .quick_deploy import quick_deploy
from .token_enable import enable_token_mode, enable_token_mode_wapi

__all__ = [
    "enable_token_mode",
    "enable_token_mode_wapi",
    "fresh_install",
    "ipc_preflight",
    "join_grid",
    "leave_grid",
    "licenses",
    "quick_deploy",
    "reset_all",
]
