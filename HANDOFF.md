# nios_test — Library Handoff

## What this library does

Python library for deploying files to and running commands against NIOS appliances and NCC containers. Replaces the collection of bash deploy scripts in `scripts/appliance/` with a single API. Separates *what* a test does from *how* it reaches the appliance — same code works against a remote lab appliance or a local NCC container by changing the config.

Two core capabilities:
- **Deploy**: Upload files, verify checksums, restart services. Named methods per artifact type (serial_console, shared lib, token Python files, token_stats_consumer).
- **Sessions**: Interactive shell and console (CLI) connections with send/expect/run primitives. Lets callers build interactive flows (IPc enablement, model selection menus, etc.) without raw expect scripts.

## Package location

`/blox/adixon1/scripts/nios_test/`

## Package structure

| File | What it provides |
|---|---|
| `__init__.py` | Public API — see exports table below |
| `constants.py` | NIOS timing constants + default credentials + WAPI version |
| `config.py` | All 4 config dataclasses — `ApplianceConfig`, `LocalApplianceConfig`, `ContainerConfig`, `LocalContainerConfig` |
| `transport.py` | Transport protocol, `SSHDockerTransport` (remote), `LocalDockerTransport` (NCC) |
| `deploy.py` | `DeployMixin` with 4 deploy methods, `DeployResult`, `DeployError` |
| `session.py` | `BaseSession`, `ShellSession`, `ConsoleSession` — pexpect-backed |
| `container.py` | `NiosContainer` (remote, inherits `DeployMixin`), `LocalNiosContainer` (NCC — adds `set_network()`, `get_container_ip()`) |
| `appliance.py` | `_ApplianceBase`, `Appliance` (SSH, auto-enables SSH on construction), `LocalAppliance` (NCC, valid IP is a construction invariant) |
| `log.py` | `NiosLogger`, formatters, `resolve_log_file`, `create_logger`, 5 verbosity levels |

### Public API (`__init__.py` exports)

| Name | Type | Description |
|---|---|---|
| `Appliance` | class | SSH appliance wrapper — auto-enables SSH on construction |
| `ApplianceConfig` | dataclass | Config for remote SSH `Appliance` |
| `LocalAppliance` | class | NCC appliance wrapper — valid IP is a construction invariant; `from_config` calls `container.set_network()` when `config.ip` is None |
| `LocalApplianceConfig` | dataclass | Config for local NCC `LocalAppliance` |
| `NiosContainer` | class | Direct SSH container access (deploy + sessions) |
| `ContainerConfig` | dataclass | Config for `NiosContainer.from_config()` |
| `LocalNiosContainer` | class | Direct local container access (deploy + sessions + `set_network()`) |
| `LocalContainerConfig` | dataclass | Config for `LocalNiosContainer.from_config()` |
| `ShellSession` | class | Interactive bash session |
| `ConsoleSession` | class | Interactive Infoblox CLI session |
| `DeployResult` | dataclass | Return value from deploy methods |
| `DeployError` | exception | Raised on deploy failure |
| `TransportError` | exception | Raised on transport-level failure |
| `CommandError` | exception | Raised when a command exits non-zero (check=True) |
| `SSHDockerTransport` | class | Remote transport (ssh + docker exec) |
| `LocalDockerTransport` | class | Local transport (docker exec) |
| `connect` | function | Factory: returns Local or SSH transport based on host arg |
| `create_logger` | function | Create a `NiosLogger` with file + console handlers |
| `resolve_log_file` | function | Resolve output_dir → timestamped log file path |

## Key design decisions

- **Four entry points**: `Appliance.from_config(ApplianceConfig(...))` for SSH remote appliances. `LocalAppliance.from_config(LocalApplianceConfig(...))` for NCC containers via the appliance API. `NiosContainer.from_config(ContainerConfig(...))` for direct SSH container access. `LocalNiosContainer.from_config(LocalContainerConfig(...))` for direct local container access.
- **`_ApplianceBase` / `Appliance` / `LocalAppliance` split**: `_ApplianceBase` holds the container reference and all forwarding methods. `Appliance` adds `_ensure_ssh()` in `__init__` — SSH is a guaranteed invariant of every `Appliance` instance. `LocalAppliance` adds IP-as-invariant: `from_config` calls `container.set_network()` when `config.ip is None`, making a valid IP a guarantee of every instance. There is no runtime `local` flag; the split is structural.
- **`LocalNiosContainer` vs `NiosContainer`**: Same split at the container layer. `LocalNiosContainer` overrides `console_session()` to use `docker exec` (not SSH) and adds `set_network()` / `get_container_ip()`.
- **Configs are pure data, separate from implementation**: `config.py` contains all four config dataclasses; their only dependency is `log.py` (`resolve_log_file`). Implementation files (`appliance.py`, `container.py`) import from `config.py`.
- **Named constants for NIOS timing and credentials**: `constants.py` holds `NIOS_REBOOT_TIMEOUT`, `NIOS_WAPI_READY_TIMEOUT`, `NIOS_WAPI_POLL_INTERVAL`, `NIOS_SSH_READY_DELAY`, `ADDKEYS_TIMEOUT`, `NIOS_CONSOLE_USER`, `NIOS_CONSOLE_PASSWORD`, `WAPI_VERSION`. All platform-specific values are defined once.
- **Transport handles both hops**: `SSHDockerTransport` wraps `ssh host 'docker exec container ...'`. `LocalDockerTransport` wraps `docker exec container ...`. Container only calls transport methods.
- **Host-level vs container-level operations**: Binary deploys (serial_console, .so files) use `upload_to_host()` and `run_on_host()` because `/infoblox/` is directly accessible from the appliance host. Python file deploys use `upload()` (docker cp / cat|tee) because they go into the container's overlay filesystem.
- **send/expect/run session primitives**: Sessions backed by pexpect. `run(cmd)` is sugar for sendline + expect(prompt). `send()`, `sendline()`, `expect()`, `expect_one_of()` available for interactive multi-step flows.
- **md5 checksums**: Matches every existing bash deploy script. Not security-sensitive — purely integrity verification.
- **Python stdlib logging**: 5 levels (off/info/debug/verbose/verbose-ex) mapped to Python logging. Dual output (screen + file) via logger hierarchy — module loggers propagate to `nios_test` root which has a FileHandler when `output_dir` is set.
- **Prompt tracking in sessions**: `_last_prompt` is updated after every `expect()` call. `sendline()` logs `{last_prompt} {cmd}` so session logs show the real prompt before each command.
- **`set_network()` is local-only**: `LocalNiosContainer.set_network()` only exists on the local container variant. No runtime guard needed — calling it on `NiosContainer` is a compile-time error. `LocalAppliance` does not expose `set_network()` publicly; `from_config` calls it internally when `config.ip is None`.

## Session logging

Each session log line is at INFO level from `session.py`. Format (with verbose level):

```
12:27:00 session.py:39  Opening session: docker exec -it balerion /bin/bash
12:27:00 session.py:48  Session ready
12:27:00 session.py:75  root@infoblox:/# echo hello
12:27:00 session.py:121 hello
12:27:00 session.py:123 root@infoblox:/#
12:27:00 session.py:52  Closing session
```

Console sessions show `Infoblox > show status` because `ConsoleSession.open()` sets `_last_prompt` from the matched prompt text after the login sequence.

## Appliance SSH auto-enable

When an `Appliance` is constructed (always SSH), `_ensure_ssh()` runs automatically in `__init__`:

1. Probes SSH: `ssh -o BatchMode=yes -o ConnectTimeout=5 root@<ip> echo ok` (10s subprocess timeout)
2. If SSH responds → returns immediately, no side effects
3. If SSH fails → calls `_enable_ssh_via_console()`:
   - Spawns `console_connect -H <hostname>` with a 10s timeout
   - If another process already holds the console, the timeout fires and a warning is logged: `"Enable SSH manually, or close any open console session and retry."` — returns without completing
   - Handles the login sequence (login/password prompts or already at `Infoblox >`)
   - Runs `set remote_console` (two `y` confirmations)
   - Runs `set support_access` (two `y` confirmations)
   - Sleeps `NIOS_SSH_READY_DELAY` (15s) for SSH to come up
   - Runs `addkeys <ip>` with `ADDKEYS_TIMEOUT` (20s) — if it fails or times out, logs a warning: `"SSH is enabled but key push failed. You may not be able to SSH in."`
4. After `_enable_ssh_via_console()` returns, SSH is probed a second time. If still unreachable, `RuntimeError` is raised — construction fails rather than returning a broken object.

Dependencies: `console_connect` at `/import/tools/lab/bin/console_connect`, `addkeys` at `/import/tools/qa/bin/addkeys` — both are in PATH on dev machines.

## Test infrastructure

### Location

`/blox/adixon1/scripts/nios_test/tests/`

### Targets

- **Appliance**: vm-04-89 (10.35.211.4) — SSH transport. Uses `ApplianceConfig` + `Appliance.from_config()`.
- **NCC**: balerion slot — local transport. Uses `LocalApplianceConfig` + `LocalAppliance.from_config()`, or `LocalContainerConfig` + `LocalNiosContainer.from_config()`.
- **Worktree**: `/blox/adixon1/AURORA-2295-wapi-ipc-audit-log` (assigned to balerion)
- **Chroot**: `/local/91/chroots/balerion/`

### Running

```bash
# All tests, both targets
python3 scripts/nios_test/tests/run_all.py --target all

# Override log output directory
python3 scripts/nios_test/tests/run_all.py --target all --output-dir /other/path

# Individual file
NIOS_TEST_TARGET=ncc python3 scripts/nios_test/tests/test_container.py
```

Default log level is `info`, controlled by the `LOG_LEVEL` constant in helpers.py — change it there to affect all test output at once.

### Test files

| File | Tests | What it covers |
|---|---|---|
| `test_transport.py` | 11 | run, run_on_host, is_container_running, checksum, host_checksum, upload — SSH + local |
| `test_session.py` | 5 | ShellSession echo + hostname (SSH), ConsoleSession show status (SSH + local), ShellSession echo (local) |
| `test_container.py` | 10 | ContainerConfig validation, NiosContainer.from_config() transport/logger, shell/console sessions — SSH + local |
| `test_appliance.py` | 9 | ApplianceConfig + LocalApplianceConfig validation, Appliance + LocalAppliance construction and transport routing, repr — unit tests + NCC + SSH integration |
| `test_deploy.py` | 5 + 1 skip | deploy_serial_console + deploy_token_py + deploy_token_stats_consumer (SSH appliance); deploy_token_py + deploy_token_stats_consumer (NCC); serial_console on NCC skipped (OverlayFS limitation) |
| `local_appliance.py` | 7 | get_container_ip, appliance_ip (validates IP + transport type), host route, WAPI from host, shell + console via networked LocalAppliance, deploy_token_py on networked NCC |

`local_appliance.py` is not in `run_all.py` — it's destructive and slow. Run manually:

```bash
python3 scripts/nios_test/tests/local_appliance.py
```

No environment variables required. If balerion is already networked, the pre-check detects it and skips `set_network()`.

### helpers.py

Constants:
- `DEFAULT_HOSTNAME = "vm-04-89"`, `DEFAULT_IP = "10.35.211.4"`, `DEFAULT_CHROOT`, `DEFAULT_CONTAINER`, `DEFAULT_WORKTREE`
- `LOG_LEVEL = "info"` — controls console and file log format for all test factories. Edit this one constant to change log verbosity across all tests.
- `OUTPUT_DIR` — per-run subdirectory: `_RESULTS_BASE/<YYYY-MM-DD_HH-MM-SS>/`. Base defaults to `/blox/adixon1/scripts/nios_test/results`, overridable via `NIOS_TEST_OUTPUT_DIR` env. Timestamp is fixed at import time — all loggers in one run share the same subdirectory.
- `WAPI_VERSION`, `NIOS_CONSOLE_USER`, `NIOS_CONSOLE_PASSWORD` — imported from `nios_test.constants` and re-exported; test files import these from helpers rather than from `nios_test.constants` directly.

Factories (all default to `log_level=LOG_LEVEL`):
- `appliance_config(log_level=LOG_LEVEL)` → `ApplianceConfig` for vm-04-89
- `local_appliance_config(log_level=LOG_LEVEL, ip=None)` → `LocalApplianceConfig` for balerion; pass `ip=get_ncc_ip()` to skip `set_network()`
- `local_container_config(log_level=LOG_LEVEL)` → `LocalContainerConfig` for balerion
- `container_config(log_level=LOG_LEVEL)` → `ContainerConfig` for NiosContainer SSH access (vm-04-89's `nios` container)

Helpers:
- `get_ncc_ip(retries=10, interval=5) -> str | None` — pre-check: gets the container IP via `docker inspect`, then polls WAPI inside the container (curl via `docker exec`). Returns the IP if WAPI responds, `None` if it doesn't within `retries` attempts. Use this before constructing `LocalAppliance` to avoid triggering `set_network()` when the network is already configured. Retry logic handles the brief window where httpd is restarting after a deploy.
- `should_run_appliance()` / `should_run_ncc()` — target filtering via `NIOS_TEST_TARGET`
- `test(name, fn)`, `skip(name, reason)`, `summary()` — test harness
- `resolve_ip()` — reads argv (skipping `--` args), then `NIOS_IP` env, then default IP

Test files import nios_test types (`Appliance`, `NiosContainer`, etc.) directly from `nios_test`, not via helpers. Helpers only exports constants, factories, and harness utilities.

## set_network() — NCC networking

`LocalNiosContainer.set_network(reboot_timeout=NIOS_REBOOT_TIMEOUT, wapi_timeout=NIOS_WAPI_READY_TIMEOUT) -> str`

Ports `slot set_network` from bash to Python. Full flow:

1. `docker inspect <container>` → parses JSON to get `ncc_lan1` IP + prefix
2. Derives netmask (prefix→netmask map) and gateway (`ip.rsplit('.', 1)[0] + ".1"`)
3. Opens `ConsoleSession` via `docker exec -it <container> /infoblox/one/bin/serial_console`
4. Runs `set network` interactive flow — expects `"Enter IPv4 address"` (not `"Enter IP address:"`)
5. Handles optional DSCP prompt via `expect_one_of(["Enter DSCP", "Configure IPv6", "Become grid member"])`
6. Waits `reboot_timeout` seconds (`NIOS_REBOOT_TIMEOUT = 300`) for NIOS reboot
7. Polls WAPI inside container every `NIOS_WAPI_POLL_INTERVAL` (5s) until it responds or `wapi_timeout` expires
8. Calls `_find_forwarding_bridge()` — walks container iflink indices → matches veth on host → finds bridge in forwarding state
9. Runs `sudo ip route replace <ip>/32 dev <bridge>` on host
10. Returns IP string

`LocalAppliance.from_config` calls `container.set_network()` when `config.ip is None`, then passes the returned IP to the constructor. `LocalAppliance.ip` is always a valid string — there is no optional-IP state.

### Pre-check pattern

Any test that constructs `LocalAppliance` should call `get_ncc_ip()` first and pass the result as `ip`:

```python
_appliance = LocalAppliance.from_config(local_appliance_config(ip=get_ncc_ip()))
```

`get_ncc_ip()` polls WAPI inside the container (no host route required). If WAPI responds, the IP is returned and `set_network()` is skipped. If not (e.g. immediately after `killall httpd`), it retries up to 10 times (50s max) before giving up and returning `None`. When `None` is passed as `ip`, `from_config` falls through to `container.set_network()` (~5-7 min).

### Known NCC networking facts

- After `set_network()`, NIOS is accessible via HTTPS/WAPI at the `ncc_lan1` IP (e.g. `172.20.x.x`)
- **SSH (port 22) is NOT exposed** on the NCC container's LAN IP — no SSH daemon listens there. Console and shell are only accessible via `docker exec`.
- `slot shell <name>` = `docker exec -it <name> /bin/bash` — same as what `LocalDockerTransport` already does.

---

## Ticket tests

Ticket-specific tests live separately from the library test suite:

```
scripts/
  nios_test/          ← library + library tests (evergreen)
  tickets/
    NIOSRFE-10314/
      test_aggregator_lifecycle.py
```

**Convention**: `tickets/<TICKET-ID>/test_<description>.py`

**Pattern**: deploy the changed files first, then assert the specific behaviour the ticket fixes. Tests are scenario-based (not layer-based), may have manual operator steps (using `input()` to pause), and are tied to a specific worktree. They are never added to `run_all.py`.

**NIOSRFE-10314** (`test_aggregator_lifecycle.py`): Verifies `token_aggregator_service` starts/stops correctly across token mode transitions. Deploys `token_service_monitor` + `token_aggregator_service` from `DEFAULT_CHROOT`, then walks through 4 phases (Licensed baseline → enable token mode → reset all → re-enable). Phases 2–4 pause for operator input. Set `SKIP_DEPLOY=1` to skip binary deployment and run assertions only.

```bash
python3 scripts/tickets/NIOSRFE-10314/test_aggregator_lifecycle.py
SKIP_DEPLOY=1 python3 scripts/tickets/NIOSRFE-10314/test_aggregator_lifecycle.py
```

---

## What comes next

Ongoing refactoring and improvement of the library. Open areas:

- **`local` flag redundancy in `ConsoleSession`**: The `local: bool` parameter in `ConsoleSession.__init__` is redundant — passing a non-None `container_name` is sufficient to take the `docker exec` path. The flag adds no behavior but could mislead readers. Consider removing it and relying solely on `container_name`.
- **`constants.py` not exported from `__init__.py`**: Constants are internal. `WAPI_VERSION`, `NIOS_CONSOLE_USER`, and `NIOS_CONSOLE_PASSWORD` are now re-exported from `helpers.py` for test use. Decide whether any constants belong in the public `__init__.py` API for library consumers outside the test suite.
- **Session timeout defaults not using constants**: Group B timeouts (30s shell, 60s console, 15s password prompt) are still hardcoded in `session.py` and `container.py`. These could move to `constants.py` alongside Group A — lower priority since they're not NIOS-specific domain knowledge.
- **Test coverage for `LocalNiosContainer`**: `test_container.py` tests `NiosContainer` but may not fully cover `LocalNiosContainer`-specific methods (`get_container_ip`, `set_network`, `_find_forwarding_bridge`). Those paths are exercised by `local_appliance.py` but not in the structured test suite.

---

## Bugs fixed

1. **helpers.py missing Appliance/NiosContainer imports** — test files imported `Appliance` and `NiosContainer` from helpers but helpers only exported config types. Fixed by adding them to the import.

2. **resolve_ip() picked up --output-dir as IP** — `sys.argv[1]` was `--output-dir` when tests were run with CLI flags. Fixed by skipping argv entries that start with `-`.

3. **Logger root level NOTSET** — `_setup_root_file_handler` used `min(root.level, VERBOSE_EX)`. Since root.level was NOTSET (0), `min(0, 6) = 0` suppressed all debug messages. Fixed: `root.setLevel(VERBOSE_EX)` unconditionally.

4. **expect() logged echoed command + output (duplicate)** — `expect()` logged `before + after` which included the echoed command already shown by `sendline()`. Fixed: removed logging from `expect()`, added to `run()` after echo-stripping.

5. **ConsoleSession.open() didn't set _last_prompt** — `ConsoleSession` overrides `open()` for its login sequence but never set `_last_prompt`, so all console commands logged `>>> cmd` instead of `Infoblox > cmd`. Fixed: added `self._last_prompt = _ANSI_RE.sub("", self._child.after or "").strip()` before the "session ready" debug log.

6. **SSH to NCC IP for console test** — After `set_network()`, port 22 is not exposed on the NCC container's LAN IP. `ConsoleSession(host=ip, ...)` timed out. Fixed: test uses `container.console_session()` (docker exec path) instead.

7. **`set network` console prompt mismatch** — `container.py` expected `"Enter IP address:"` but the actual NIOS prompt is `"Enter IPv4 address [Default: <ip>]:"`. Fixed: pattern changed to `"Enter IPv4 address"`.

8. **`appliance_config()` missing worktree** — `helpers.py` `appliance_config()` didn't pass `worktree`, causing `deploy_token_py` and `deploy_token_stats_consumer` to fail with `DeployError: No worktree configured` against the SSH appliance. Fixed: added `worktree=DEFAULT_WORKTREE`.

9. **`local_appliance.py` missing `output_dir`** — Inline `ApplianceConfig` instances in `local_appliance.py` didn't pass `output_dir`, so no log files were written even when `NIOS_TEST_OUTPUT_DIR` was set. Fixed: added `output_dir=OUTPUT_DIR` to both configs.

10. **`_wapi_up()` failed without host route** — Pre-check used host-side curl to test if WAPI was up, which failed if the host route didn't exist (e.g. after a reboot). Replaced with `_wapi_up_in_container()` which runs curl inside the container via `docker exec` — works regardless of host route state.

11. **`test_session.py` / `test_transport.py` / `test_deploy.py` imported non-existent `container_config`** — These files imported `container_config` from helpers but it wasn't defined there. They also used `NiosContainer.from_config(container_config())` for NCC sections when the correct type is `LocalNiosContainer`. Fixed: added `container_config()` to helpers.py; fixed NCC sections in all three files to use `LocalNiosContainer.from_config(local_container_config())`.

12. **`test_appliance.py` NCC section triggered `set_network()` unnecessarily** — NCC section called `LocalAppliance.from_config(local_appliance_config())` with no `ip`, so `set_network()` ran even when the network was already configured. Fixed: NCC section now passes `ip=get_ncc_ip()` so the pre-check runs first.

13. **Second run of `local_appliance.py` triggered `set_network()`** — `deploy_token_py` kills httpd, causing WAPI to be briefly unavailable. A single pre-check attempt failed, so `get_ncc_ip()` returned `None` and `set_network()` re-ran. Fixed: added 10-retry loop with 5s intervals (50s max) to `get_ncc_ip()`.

14. **`LocalAppliance` unit tests triggered `set_network()`** — After `LocalAppliance` required a valid IP at construction, unit tests that passed no `ip` in `LocalApplianceConfig` would fall through to `container.set_network()`. Fixed: unit tests pass `ip=DEFAULT_IP` as a dummy value — the transport is never used so no actual connection is made.

15. **Log file always used `VerboseFormatter` regardless of `LOG_LEVEL`** — `_setup_root_file_handler` hardcoded `VerboseFormatter()`. When `LOG_LEVEL = "info"`, the console used `InfoFormatter` but the file still showed timestamps and line numbers. Fixed: `_setup_root_file_handler` now accepts a `level` parameter and uses `_FORMATTER_FOR_LEVEL[level]`, matching the console formatter.

---

## Dominion

Bash aliases for working with nios_test from the command line. Defined in `~/.bashrc`. Scripts live in `nios_test/bin/`.

| Command | What it does |
|---|---|
| `dominion` | Dashboard: slot status + last 5 test runs with pass/fail counts |
| `reckoning [N]` | Full run history table. Optional count arg: `reckoning 5` |
| `verdict [run_id]` | Colorized detail for a run. Default: latest. Accepts prefix: `verdict 2026-08-03_14` |
| `invoke <test>` | Run a test file directly. `.py` extension optional: `invoke test_sc_hello` |

Typical workflow:
```
invoke test_sc_hello   # run a test
dominion               # check result alongside slot status
verdict                # read full breakdown if something failed
```

Run IDs shown in `reckoning` are the directory names under `nios_test/results/`.
