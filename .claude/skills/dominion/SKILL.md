---
name: dominion
description: "dominion is a Python library for running commands against NIOS appliances
  and NCC containers. This skill documents the library's API and the best practices for
  using it."
when_to_use: "deploy to appliance, deploy to NCC, write a ticket test,
  fresh install a grid, use an NCC container, write a script to use NIOS,
  create an alias or function in bashrc"
---

# dominion

dominion is Python library for deploying files to and running commands against NIOS
appliances and NCC containers. The same API targets a remote lab appliance or a local
NCC container.

Covers: picking an entry point, deploying files, interactive sessions, the
test harness, WAPI calls, and how to structure and reuse generated code.

## Pick an entry point

There are three entry points you construct directly. Never construct `NiosContainer`
yourself. It is only ever handed back to you via `appliance.container`.

- **`Appliance`** — the one you'll use most. Targets a real lab appliance over SSH.
- **`LocalNiosContainer`** — use this whenever working with NCC
  Talks to the container directly via `docker exec`.
- **`LocalAppliance`** — used sparingly. The user should specify specifically when this
  should be used.

### Constructing `Appliance` and `LocalNiosContainer`

When using inline `from_config` declare each value as an uppercase variable
near the top of the script/common file. Never a bare string literal into the
`*Config` call.

## Example:`Appliance` via `from_config`:

```python
from dominion import Appliance, ApplianceConfig

HOSTNAME = "vm-04-89"
IP = "10.35.211.4"
WORKTREE = "/path/to/my-branch"

app = Appliance.from_config(ApplianceConfig(
    hostname=HOSTNAME,
    ip=IP,
    worktree=WORKTREE,
))
```

## Example: `LocalNiosContainer` via `from_config`:

```python
from dominion import LocalNiosContainer, LocalContainerConfig

CHROOT = "/path/to/chroot"
WORKTREE = "/path/to/my-branch"

container = LocalNiosContainer.from_config(LocalContainerConfig(
    chroot=CHROOT,
    worktree=WORKTREE,
))
```

## Sessions

`ShellSession` and `ConsoleSession` both require SSH reachability, so they work
on both `Appliance` and `LocalNiosContainer`. `ConsoleConnectSession` only works
on `Appliance` objects, since it goes through the lab console server rather than
SSH. There's no equivalent for a local NCC container. Prefer `ConsoleConnectSession`
over `ConsoleSession` when working with an `Appliance`.

### ShellSession

A bash shell inside the container. Use for anything that isn't the Infoblox CLI.
Arbitrary commands, interactive prompts from non-CLI tools, etc.

```python
with app.shell_session() as shell:
    output = shell.run("cat /etc/hostname")

    # Lower-level primitives for interactive flows
    shell.sendline("some_command")
    shell.expect("Enter value:")
    shell.sendline("my_value")
```

### ConsoleSession

The Infoblox CLI, connected via SSH. Simple and fast, but the SSH connection dies
if NIOS reboots mid-session — don't use it for flows that reboot the appliance.

```python
with app.console_session() as console:
    output = console.run("show status")

    console.sendline("set network")
    console.expect("Enter IPv4 address")
    console.sendline("10.0.0.1")
```

### ConsoleConnectSession

The Infoblox CLI, connected out-of-band via the lab console server
(`console_connect -H <hostname>`) instead of SSH. Use it for anything that
reboots the appliance mid-flow — grid joins, network changes, membership
wizards. Only works for lab-registered appliances.

When a reboot is coming, don't try to keep one session open and `expect()`
through the reboot. Close the session that sent the reboot-triggering command,
then open a **new** session afterward and confirm the appliance came back (e.g.
`show status`). Example using a grid join:

```python
with member.console_connect_session() as console:
    console.sendline("set membership")
    console.expect("Master VIP")
    console.sendline(master_ip)
    console.expect("Grid Name")
    console.sendline(grid_name)
    console.expect("Shared Secret")
    console.sendline(shared_secret)
    console.expect("Is this correct")
    console.sendline("y")
    console.expect("Are you sure")
    console.sendline("y")
# session closed here; member is now rebooting

# Fresh session to validate the member rebooted and joined.
with member.console_connect_session(timeout=600) as console:
    output = console.run("show status")
    assert "Grid Master IP" in output, f"Member didn't join grid: {output}"
```

`ConsoleConnectSession._open()` has stale-data-draining logic built
specifically for reconnecting cleanly. Opening a new session after a reboot
gets you that clean-reconnect handling for free.

## The `Run` test harness

`Run` is the structured runner for standalone scripts. It tracks
pass/fail/skip, writes `summary.txt` + `junit.xml`, and dumps `infoblox.log` for
any target you register. **Use Run:** standalone for scripts with multiple steps you
want tracked as pass/fail/skip, with auto summary/junit/log-dump.
**Don't use Run:** for utility scripts (deploy/configure/query) with no real steps
to track or useful scripts you'll reuse day-to-day. Just call the API directly.

Best practices:

- **Construct `run = Run()` once, near the top of the main script**, before building
  any `Appliance`/`LocalNiosContainer`
- **Register every target with `run.add(...)` right after constructing it**
- **Name phases as a narrative, numbered in execution order** — Ex: `"Phase 3a WAPI: connected"`
- **Gate optional phases with an env var and `run.skip(name, reason)`**, checked
  as `os.environ.get("SKIP_X", "0") == "1"` (`SKIP_DEPLOY`, `SKIP_CLEANUP`, `SKIP_SETUP`, ...).
- **`run.test("name", lambda: fn(arg))`** — prefer to use zero-arg lambda
- Wrap the whole body in `try/finally` to ensure proper test results if there is an error

```python
run = Run()
app = Appliance.from_config(ApplianceConfig(..., output_dir=run.output_dir))
run.add(app)

try:
    ref, before, after = run.test("Phase 1: read baseline", lambda: get_baseline(transport))
    run.test("Phase 2: verify change", lambda: verify_change(transport, ref, before, after))
finally:
    run.summary()
```

## Reuse code, split files by role

Before writing a new helper, check whether it already exists:
- **`dominion/actions/`** — reusable, ticket-independent operations
- **Another ticket's `common.py`/`*_ops.py`** — a similar-enough problem
  (polling for SSH/WAPI readiness, WAPI query helpers, deploy-and-verify
  patterns) has often already been solved in a sibling `scripts/tickets/*/`
  directory.

When a scripts's logic grows past a single file or is becoming large, split
by role rather than growing one flat script.

- **Main**: Thin entry point.
  Builds the `Appliance`/`LocalNiosContainer`, constructs `Run`, and wires
  phases together via `run.test(...)`. Imports its logic from the other two
  files rather than defining it inline.
- **Implementation**: The actual step functions the main file calls.
  Can be split further by concern (e.g. `NIOSRFE-10458` has both `ops.py`
  for container operations and `wapi_ops.py` for WAPI verification).
- **Common** (`common.py`) — constants and shared data used by both main and
  implementation files: `HOSTNAME`, `WORKTREE`, endpoint URLs, test fixtures.
  Keeps configuration in one place instead of scattered across files.

## WAPI API

`Appliance` exposes a `.wapi` property returning a `WapiClient` for making WAPI calls inside the container
Always prefer using this API over manually constructing it:

```python
from dominion import Appliance, ApplianceConfig, WapiClient

app = Appliance.from_config(ApplianceConfig(hostname=HOSTNAME, ip=IP))

result = app.wapi.get("/grid")
result = app.wapi.post("/record:host", data={"name": "test.example.com", "ipv4addrs": [{"ipv4addr": "10.0.0.1"}]})
result = app.wapi.put("/record:host/ref", data={"name": "updated.example.com"})
result = app.wapi.delete("/record:host/ref")
```

Read the full implemenation when working with WAPI for more info.

## Transport API

Both `SSHDockerTransport` and `LocalDockerTransport` implement the same protocol.
`transport.run()` is the workhorse for most scripts — extract it via
`app.container._transport` and use it directly for low-level container commands.

| Method | What it does |
|---|---|
| `run(cmd, check=True)` | Execute inside the Docker container. Returns `(rc, stdout, stderr)`. Raises `CommandError` if `check=True` and rc != 0 |
| `run_on_host(cmd, check=True)` | Execute on the host (appliance or dev machine) |
| `upload(local, remote)` | Upload a file into the container |
| `upload_to_host(local, remote)` | Upload a file to the host filesystem |
| `checksum(path)` | md5sum of a file inside the container |
| `host_checksum(path)` | md5sum of a file on the host |
| `is_container_running()` | Check if the Docker container is running |

## Log Levels

Set via the `log_level` field on `ApplianceConfig`, `LocalApplianceConfig`,
`LocalContainerConfig`, etc. Four levels, each a superset of the last:

- **`off`** — nothing.
- **`info`** (default) — commands being run (summarized) and their stdout.
  Enough to see what dominion is doing without noise.
- **`debug`** — full command strings, exit codes, stdout/stderr, checksums,
  upload paths. Use when troubleshooting a failing deploy or command.
- **`verbose`** — everything in `debug` plus pexpect session internals
  (pattern matches, byte counts). Use sparingly, for deep debugging.

## Further reference

- **`references/deploy.md`** — full Deploy API table.
- **`references/log-operations.md`** — mark/dump/count infloblox.log / generic
  log API. Always check before hand-rolling a grep/tail shell command for logs.
- **`references/grid.md`** — `Grid`/`GridConfig` for deploying a lab grid.
