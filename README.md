# dominion

Python library for deploying files to and running commands against NIOS appliances and NCC containers. Same code works against a remote lab appliance (SSH) or a local NCC/slot container (docker exec) by swapping the config.

## Setup

```bash
git clone <repo-url> dominion && cd dominion
pip install -e .   # one time per Python env
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

## More detail

See `~/.claude/skills/nios-test/SKILL.md` for full API reference, config field tables, and ticket-test conventions.
