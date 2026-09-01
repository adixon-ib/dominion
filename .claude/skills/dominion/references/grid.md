# GridDeploy

`Grid` deploys a full lab grid (via `grid_deploy`/`config_grid` under the hood)
and hands back ready-to-use `Appliance` objects for the GM and each member.
Prefer `standalone` and `gm-sa` for now. Others can be used if required.

```python
from dominion import Grid, GridConfig

grid = Grid.from_config(GridConfig(
    topology=TOPOLOGY,
    member=MEMBER_HOSTNAME,
    file=IMAGE_FILE,
))
gm = grid.gm            # Appliance, or None for standalone (no GM)
members = grid.members  # list[Appliance]
```

Preferred topology values:

- `standalone` — 1 appliance, lab-assigned IPs. Requires `member`.
- `gm-sa` — GM + 1 standalone member (both virtual or both physical). Requires `gm`, `member`.

Image source: pass `file` when every host in the topology is the same type
(all-virtual or all-physical); pass `ova`/`bin` separately when a topology mixes
virtual and physical hosts (e.g. `gm-sa-physical`).
