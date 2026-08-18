# dominion

Python library for deploying files to and running commands against NIOS appliances and NCC containers. Same code works against a remote lab appliance (SSH) or a local NCC/slot container (docker exec) by swapping the config.

## Setup

```bash
git clone <repo-url> dominion && cd dominion
pip install -e .              # one time per Python env
pip install junit2html        # optional, for HTML test reports
cp src/config.example.json src/config.json
# edit config.json: hostname, ip, chroot, worktree, container, log_level
```

## Quickstart

```python
from dominion import Appliance, ApplianceConfig, Run

run = Run()
app = Appliance.from_config(ApplianceConfig(
    hostname="vm-04-89",
    ip="10.35.211.4",
    log_level="info",
    output_dir=run.output_dir,
))

with app.shell_session() as shell:
    print(shell.run("hostname"))

app.deploy_token_py()
```

## Entry points

| Class | Target |
|---|---|
| `Appliance` | Remote appliance over SSH |
| `LocalAppliance` | Local NCC/slot container |
| `NiosContainer` | Direct SSH container access |
| `LocalNiosContainer` | Direct local container access |

Each has `shell_session()`/`console_session()` for interactive commands and named `deploy_*` methods (e.g. `deploy_token_py()`) for pushing files.

## Test harness

For standalone scripts (not part of the pytest suite):

```python
from dominion import Run
run = Run()
run.test("my check", lambda: my_check(app))
run.summary(app)  # prints totals, writes summary.txt + junit.xml, exits
```

## Integration tests

```bash
pytest integration_tests/ -m appliance   # needs a reachable appliance
pytest integration_tests/ -m ncc         # needs a running NCC container
pytest integration_tests/ -m destructive # opt-in, deploys/reboots real targets
```

Unit tests (no live target needed): `pytest unit_tests/`

## Example: multi-grid token mode sanity test

`examples/sanity.py` is a real end-to-end test that exercises most of dominion's capabilities in a single run. It deploys two standalone grids, enables token mode, joins one into the other as a member, and verifies the full lifecycle.

### Running the sanity test

**Fresh deploy** (images two appliances from scratch, then runs the full test):

```bash
GM_HOSTNAME=vm-04-89 GM_IP=10.35.211.4 \
MEMBER_HOSTNAME=vm-09-89 MEMBER_IP=10.35.211.9 \
IMAGE_FILE=/path/to/nios.ova \
python3 examples/sanity.py
```

**Skip deploy** (appliances are already imaged and SSH-reachable):

```bash
GM_HOSTNAME=vm-04-89 GM_IP=10.35.211.4 \
MEMBER_HOSTNAME=vm-09-89 MEMBER_IP=10.35.211.9 \
SKIP_DEPLOY=1 \
python3 examples/sanity.py
```

All four env vars (`GM_HOSTNAME`, `GM_IP`, `MEMBER_HOSTNAME`, `MEMBER_IP`) are required. `IMAGE_FILE` is required unless `SKIP_DEPLOY=1` is set.

The full run takes roughly 50 minutes (most of that is the 45-minute post-join grid sync wait). Results land in `results/<timestamp>/`.

**What it uses:**

- `Grid.from_config()` to deploy two fresh standalone appliances from an image file (in parallel via threads)
- `Appliance.from_config()` to connect to each appliance over SSH
- `console_session()` to drive the Infoblox CLI: enable token mode (`set nios_usage_mode IPc`), join a member to a grid (`set membership`)
- `shell_session()` to verify processes are running (`pgrep -f token_aggregator`)
- `container._transport.run()` for WAPI calls (pre-register the member on the master via `POST member`, clean up stale records)
- `Run()` harness to track pass/fail for each step and emit JUnit XML

**Test flow:**

```
1. Deploy two standalone grids (parallel)
2. Verify token mode is OFF on both
3. Enable token mode on grid1 (GM)
4. Verify token_aggregator is running on GM
5. Join grid2 into grid1 as a member (WAPI pre-register + CLI join)
6. Wait for grid sync (~45 min)
7. Verify token mode propagated to the member
8. Verify token_aggregator runs only on the GM, not the member
```

**Simplified version of the join logic:**

```python
from dominion import Appliance, ApplianceConfig, Run, WAPI_VERSION

run = Run()
master = Appliance.from_config(ApplianceConfig(hostname="vm-09-89", ip="10.35.211.9"))
member = Appliance.from_config(ApplianceConfig(hostname="vm-02-89", ip="10.35.211.2"))

# Pre-register the member on the master via WAPI
master_transport = master.container._transport
member_transport = member.container._transport

# Read the member's own identity from its local WAPI
_, out, _ = member_transport.run(
    f'curl -sk -u admin:infoblox https://localhost/wapi/{WAPI_VERSION}/member'
    f'?_return_fields=host_name,vip_setting,config_addr_type,platform'
)

# POST that identity to the master's WAPI to register it
_, out, _ = master_transport.run(
    f'curl -sk -X POST -H "Content-Type: application/json"'
    f' -d \'{{"host_name": "vm-02-89.mygrid.infoblox.com", ...}}\''
    f' -u admin:infoblox https://localhost/wapi/{WAPI_VERSION}/member'
)

# Drive the CLI join flow on the member
with member.console_session() as console:
    console.sendline("set membership")
    console.expect("Master VIP")
    console.sendline(master.ip)
    console.expect("Grid Name")
    console.sendline("Infoblox")
    console.expect("Shared Secret")
    console.sendline("test")
    console.expect("Is this correct")
    console.sendline("y")
    console.expect("Are you sure")
    console.sendline("y")
```

## Using with Claude Code

dominion ships with a Claude Code skill (`.claude/skills/dominion/SKILL.md`) that teaches Claude the full API. Reference it with `/dominion` in your prompt to get scripts that use the library correctly.

**Sample prompt:**

> Use /dominion to create a script that will join two appliances. The GM will be vm-04-89 with IP 10.35.211.4 and the member will be vm-09-89 with IP 10.35.211.9. I've already confirmed that they're SSH reachable. I want you to give me options for running this script. I want one to be a fresh deploy, and an option to skip deploy if they're already setup. I will pass the .ova file in as an environment variable in the case of a fresh deploy.

This prompt gives Claude enough context to generate a working script that:
- Uses `Grid.from_config()` for the fresh deploy path and `Appliance.from_config()` for the skip-deploy path
- Reads `IMAGE_FILE` from the environment and gates the deploy phase on `SKIP_DEPLOY=1`
- Pre-registers the member on the GM via WAPI, then drives `set membership` through `console_session()`
- Wraps each step in `run.test()` so you get pass/fail tracking and JUnit XML output

The `/dominion` skill reference is key. Without it, Claude doesn't know the library exists. With it, Claude uses the right entry points, config patterns, and conventions instead of generating raw `subprocess` or `paramiko` calls.

## Results and logging

Every `Run()` creates a timestamped directory under `results/`. Each appliance or container gets its own isolated log file with the exact commands run against it, their output, and timing.

```
============================================================
RESULTS: 3 passed, 0 failed, 0 skipped out of 3
============================================================
Logs: /home/you/dominion/results/2026-08-18_11-14-14/
  junit.xml
  summary.txt
  vm-04-89.log
  vm-09-89.log
```

- `vm-04-89.log` -- every transport command, upload, and deploy action run against the GM
- `vm-09-89.log` -- every transport command run against the member
- `summary.txt` -- human-readable pass/fail breakdown
- `junit.xml` -- machine-readable results for CI or reporting tools

When debugging a failure, the per-appliance log shows you exactly what was run and what came back, without interleaving output from other targets.

## More detail

See `.claude/skills/dominion/SKILL.md` for full API reference, config field tables, and conventions.
