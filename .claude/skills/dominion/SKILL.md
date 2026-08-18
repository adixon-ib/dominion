---
name: dominion
description: "dominion Python library for deploying files to and running commands against
  NIOS appliances and NCC containers. Covers the 4 entry points (Appliance, LocalAppliance,
  NiosContainer, LocalNiosContainer), transport-level operations, deploy methods, interactive
  sessions, the test harness, and ticket test patterns. Use when writing test scripts,
  deploying to appliances/NCC via Python, or working with the dominion library."
---

# dominion

Python library for deploying files to and running commands against NIOS appliances and NCC containers. Same API works against a remote lab appliance (SSH) or a local NCC container (docker exec) by swapping the config.

## Setup

```bash
git clone <repo-url> dominion && cd dominion
pip install -e .
cp src/config.example.json src/config.json
# edit config.json: hostname, ip, chroot, worktree, container, log_level
```

After `pip install -e .`, import directly -- no `sys.path` hacking needed:

```python
from dominion import Appliance, ApplianceConfig, Run
```

## The 4 Entry Points

### 1. `Appliance` -- Remote SSH appliance

For lab appliances reachable via SSH. Auto-enables SSH on construction via `console_connect` if needed.

**Config**: `ApplianceConfig`

| Field | Type | Default | Notes |
|---|---|---|---|
| `hostname` | `str` | required | Used for `console_connect -H` |
| `ip` | `str \| None` | `None` | SSH target. Falls back to `hostname` if None |
| `chroot` | `str` | `"/local/91/aslan_chroot"` | Local chroot path for deploy source files |
| `worktree` | `str \| None` | `None` | Git worktree path. Required for `deploy_token_py()` and `deploy_token_stats_consumer()` |
| `container_name` | `str \| None` | `"nios"` | Docker container name on the appliance |
| `log_level` | `str` | `"info"` | `off`, `info`, `debug`, `verbose`, `verbose-ex` |
| `output_dir` | `str \| None` | `None` | Directory for log files |

**Construction**:

```python
from dominion import Appliance, ApplianceConfig, Run

run = Run()
app = Appliance.from_config(ApplianceConfig(
    hostname="vm-04-89",
    ip="10.35.211.4",
    log_level="info",
    output_dir=run.output_dir,
))
```

**Construction from `config.json`** via `Defaults.from_json()`. With no argument, reads `src/config.json` in the dominion repo. Pass a path to use a per-script config:

```python
from dominion import Appliance, ApplianceConfig, Defaults

d = Defaults.from_json()

app = Appliance.from_config(ApplianceConfig(
    hostname=d.hostname,
    ip=d.ip,
    chroot=d.chroot,
    worktree=d.worktree,
    log_level=d.log_level,
    output_dir=d.output_dir,
))
```

**Deploy methods** (forwarded from container):

```python
result = app.deploy_token_py()                   # all token Python files
result = app.deploy_token_py(groups={"wapi"})    # specific groups only
result = app.deploy_serial_console()             # binary from chroot
result = app.deploy_shared_lib()                 # libibonedb_util.so from chroot
result = app.deploy_token_stats_consumer()       # consumer script from worktree
app.revert()                                     # restart container, restore originals
```

**Sessions**:

```python
with app.shell_session() as shell:
    output = shell.run("pstree")

with app.console_session() as console:
    output = console.run("show status")
```

**Transport-level access**:

```python
transport = app.container._transport

rc, stdout, stderr = transport.run("cat /etc/resolv.conf")
rc, stdout, stderr = transport.run("test -f /some/file", check=False)
transport.upload("/local/path/file.py", "/container/path/file.py")
rc, stdout, stderr = transport.run_on_host("docker ps")
transport.upload_to_host("/local/path/binary", "/remote/host/path/binary")
```

### 2. `LocalAppliance` -- Local NCC container

For local NCC/slot containers. If `config.ip` is `None`, `from_config` runs `set_network()` (~5-7 min) to configure networking.

**Config**: `LocalApplianceConfig`

| Field | Type | Default | Notes |
|---|---|---|---|
| `container_name` | `str` | required | Slot name |
| `chroot` | `str` | `"/local/91/aslan_chroot"` | Local chroot path |
| `worktree` | `str \| None` | `None` | Git worktree path |
| `ip` | `str \| None` | `None` | Pass known IP to skip `set_network()` |
| `log_level` | `str` | `"info"` | |
| `output_dir` | `str \| None` | `None` | |

```python
from dominion import LocalAppliance, LocalApplianceConfig, Run

run = Run()
app = LocalAppliance.from_config(LocalApplianceConfig(
    container_name="myslot",
    chroot="/local/91/chroots/myslot",
    worktree="/path/to/my-branch",
    ip=None,
    log_level="info",
    output_dir=run.output_dir,
))
```

Same deploy/session/transport API as `Appliance`. Transport uses `docker exec` and `cat | tee` instead of SSH.

### 3. `NiosContainer` -- Direct SSH container access

Lower-level than `Appliance`. No SSH auto-enable. Access via `app.container`:

```python
container = app.container
result = container.deploy_token_py()
container.restart()
```

### 4. `LocalNiosContainer` -- Direct local container access

Local counterpart. Adds `set_network()` and `get_container_ip()`.

**Config**: `LocalContainerConfig`

| Field | Type | Default | Notes |
|---|---|---|---|
| `container_name` | `str` | required | |
| `chroot` | `str` | `"/local/91/aslan_chroot"` | |
| `worktree` | `str \| None` | `None` | |

```python
from dominion import LocalNiosContainer, LocalContainerConfig

container = LocalNiosContainer.from_config(LocalContainerConfig(
    container_name="myslot",
    chroot="/local/91/chroots/myslot",
    worktree="/path/to/my-branch",
))

ip = container.set_network()   # ~5-7 min: console + reboot + WAPI poll + host route
ip = container.get_container_ip()
```

## When to use which entry point

| Scenario | Entry point |
|---|---|
| Test against a lab appliance | `Appliance` |
| Test against a local NCC slot | `LocalAppliance` |
| Container ops only, SSH already guaranteed | `NiosContainer` (via `app.container`) |
| Container ops on local NCC, no appliance wrapper | `LocalNiosContainer` |

`Appliance` guarantees SSH works (auto-enables on construction). `LocalAppliance` guarantees a valid IP (runs `set_network()` if needed). The container variants skip those guarantees for speed.

## Transport API

Both `SSHDockerTransport` and `LocalDockerTransport` implement the same protocol:

| Method | What it does |
|---|---|
| `run(cmd, check=True)` | Execute inside the Docker container. Returns `(rc, stdout, stderr)`. Raises `CommandError` if `check=True` and rc != 0 |
| `run_on_host(cmd, check=True)` | Execute on the host (appliance or dev machine) |
| `upload(local, remote)` | Upload a file into the container |
| `upload_to_host(local, remote)` | Upload a file to the host filesystem |
| `checksum(path)` | md5sum of a file inside the container |
| `host_checksum(path)` | md5sum of a file on the host |
| `is_container_running()` | Check if the Docker container is running |

## Deploy Methods

All return `DeployResult(artifact, changed, local_hash, remote_hash, restarted, details)`.

| Method | Source | Restart |
|---|---|---|
| `deploy_token_py(groups, branding_source, skip_restart)` | Python files from worktree. Groups: `branding`, `ibap`, `pyabs`, `wapi` | Stops `admin-httpd` via pmc |
| `deploy_serial_console()` | Binary from chroot | `killall serial_console` |
| `deploy_shared_lib(local_subpath, dest, restart_target, backup)` | `.so` from chroot (default: `libibonedb_util.so`) | `killall <target>` |
| `deploy_token_stats_consumer(skip_restart)` | Consumer script from worktree | `token_stats_consumer_control` stop/start |
| `deploy_clusterd()` | Binary from chroot | `killall clusterd` |

- `deploy_token_py` and `deploy_token_stats_consumer` require `worktree` in the config.
- `deploy_serial_console`, `deploy_shared_lib`, and `deploy_clusterd` require a prior `make install`.

## Sessions

Sessions are pexpect-backed context managers.

### ShellSession

```python
with app.shell_session() as shell:
    output = shell.run("cat /etc/hostname")

    # Lower-level for interactive flows
    shell.sendline("some_command")
    shell.expect("Enter value:")
    shell.sendline("my_value")
```

### ConsoleSession

```python
with app.console_session() as console:
    output = console.run("show status")

    # Interactive multi-step flow
    console.sendline("set network")
    console.expect("Enter IPv4 address")
    console.sendline("10.0.0.1")

    # expect_one_of returns the index of which pattern matched
    idx = console.expect_one_of(["Enter DSCP", "Configure IPv6"])
```

Session `run()` does not accept `check=`. It returns the text between sending the command and seeing the next prompt. Assert on the returned text. For exit-code checking, use `transport.run()`.

## Test Harness (`Run`)

For standalone scripts (not pytest):

```python
from dominion import Appliance, ApplianceConfig, Run

run = Run()
app = Appliance.from_config(ApplianceConfig(
    hostname="vm-04-89", ip="10.35.211.4", output_dir=run.output_dir,
))
transport = app.container._transport

def my_test(t):
    rc, out, _ = t.run("pgrep -f some_process")
    assert out.strip(), "process not running"

run.test("check process", lambda: my_test(transport))
run.skip("other check", reason="not applicable")
run.summary(app)   # prints totals, writes summary.txt + junit.xml
```

`Run` output directory precedence (highest first): explicit `Run(output_dir=...)`, `DOMINION_OUTPUT_DIR` env var, `config.json` `output_dir` field, `DEFAULT_RESULTS_DIR` (repo-relative `results/`).

## Config via `Defaults.from_json()`

`src/config.json` is gitignored. Each user creates their own from the template:

```bash
cp src/config.example.json src/config.json
```

```python
from dominion import Defaults
d = Defaults.from_json()                           # reads src/config.json
d = Defaults.from_json("/path/to/my/config.json")  # or a custom path
```

Fields: `hostname`, `ip`, `chroot`, `worktree`, `container`, `log_level`, `output_dir`. All optional.

Scripts can keep their own `config.json` alongside themselves instead of sharing the repo-wide one.

## Grid and Build

### `Grid` -- Grid deployment

```python
from dominion import Grid, GridConfig

grid = Grid.from_config(GridConfig(
    topology="2member",
    file="/path/to/image.bin",
))
gm = grid.gm           # Appliance for the GM
members = grid.members  # list of Appliance for members
```

### `NiosBuild` -- Build wrapper

```python
from dominion import NiosBuild, BuildConfig

build = NiosBuild.from_config(BuildConfig(
    worktree="/path/to/my-branch",
    slot="myslot",
))
build.quick()   # touch skip_slow && make install (~5-15 min)
build.full()    # installclean + build-env + install (~60 min)
build.clean()   # make installclean
```

## Error Types

| Exception | When |
|---|---|
| `TransportError` | Transport-level failure (timeout, command not found) |
| `CommandError(TransportError)` | Non-zero exit code with `check=True`. Has `.cmd`, `.exit_code`, `.stdout`, `.stderr` |
| `DeployError(RuntimeError)` | Deploy failure (missing file, hash mismatch). Has `.artifact` |

## Tests

```bash
pytest unit_tests/                             # no live target needed
pytest integration_tests/ -m appliance         # needs a reachable appliance
pytest integration_tests/ -m ncc               # needs a running NCC container
pytest integration_tests/ -m destructive       # opt-in: deploys/reboots real targets
```

Integration test markers: `@pytest.mark.appliance`, `@pytest.mark.ncc`, `@pytest.mark.destructive`.

## Env Vars

| Variable | Effect |
|---|---|
| `SKIP_DEPLOY=1` | Skip deploy phases in standalone test scripts |
| `SKIP_CLEANUP=1` | Leave test artifacts on the appliance |
| `NIOS_IP=10.x.y.z` | Override target IP |
| `DOMINION_OUTPUT_DIR=/path` | Override results output directory |
