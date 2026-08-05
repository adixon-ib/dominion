# Plan: Support Multiple Concurrent Grid Deployments

## Problem

`grid_deploy` hardcodes `SETUP_DIR="/tmp/my_setup"` and `TAG="mygrid"`. Every deployment writes to the same directory and tag file, so only one `Grid.from_config()` can run at a time.

## Solution: Add `--setup-dir` and `--tag` flags to `grid_deploy`

### 1. `scripts/lab/grid_deploy`

Add two optional flags to `parse_flags()`:

```bash
--setup-dir)  SETUP_DIR="$2";  shift 2 ;;
--tag)        TAG="$2";        shift 2 ;;
```

Defaults stay the same (`/tmp/my_setup`, `mygrid`), so existing callers are unaffected.

### 2. `nios_test/src/config.py` — `GridConfig`

Add two fields with matching defaults:

```python
setup_dir: str = "/tmp/my_setup"
tag: str = "mygrid"
```

### 3. `nios_test/src/grid.py`

- Remove the module-level `TAG_FILE` constant.
- In `_build_cmd`, append `["--setup-dir", config.setup_dir, "--tag", config.tag]`.
- In `from_config`, derive tag file path from config: `os.path.join(config.setup_dir, f"{config.tag}.xml")` and pass it to `_parse_tag_file`.

### 4. Auto-generate unique dir/tag per `Grid` (optional but recommended)

In `GridConfig.__post_init__`, if `setup_dir` is still the default, auto-assign a unique one:

```python
import uuid
if self.setup_dir == "/tmp/my_setup" and self.tag == "mygrid":
    uid = uuid.uuid4().hex[:8]
    self.tag = f"grid-{uid}"
    self.setup_dir = f"/tmp/my_setup-{uid}"
```

This way callers that don't pass `setup_dir`/`tag` get isolation automatically. Callers that pass explicit values get exactly what they asked for.

## Verification

```bash
# Two terminals simultaneously — should not collide
IMAGE_FILE=nios-9.2.0.ova python3 scripts/nios_test/tests/test_grid.py &
IMAGE_FILE=nios-9.2.0.ova GRID_MEMBER=vm-03-89 python3 scripts/nios_test/tests/test_grid.py &
```

Check that two separate tag files exist under `/tmp/my_setup-<uid>/`.
