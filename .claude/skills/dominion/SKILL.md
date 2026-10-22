---
name: dominion
description: "Gotchas and guidelines for using and changing the dominion library (/blox/adixon1/scripts/dominion): running commands against NIOS appliances and NCC containers, writing scripts and ticket tests, and the review checklist for library changes."
when_to_use: "use dominion, write a ticket test, write an appliance script, deploy to appliance or NCC, add a dominion action or deploy method, review a dominion change"
---

# dominion

The code is the catalog. Before writing a helper, read `actions/__init__.py` (actions), `src/deploy.py` (deploy methods and file lists), `src/wapi.py`, `src/container.py` (log ops) and `references/`. Never duplicate what is there.

## Using the library

- Use the existing API for every appliance interaction: `app.wapi` for WAPI, `app.container._transport` `run()`/`upload()` for commands and files, `console_connect_session()` for the CLI and reboots, the `app.deploy_*` methods for deploys, `mark_infoblox_log()`/`dump_infoblox_log()` for logs. No hand-spawned `console_connect`, curl, ssh, scp or pexpect.
- Entry point: `Appliance` for lab appliances. `LocalNiosContainer` and `LocalAppliance` only when asked (NCC). Never construct `NiosContainer`. Build with `from_config()` and keep every value in an uppercase constant, never a bare literal in the `*Config` call.
- Do not add dominion to `sys.path`. It is pip -e installed. To run against another checkout, put the parent of a directory named `dominion` on `PYTHONPATH`.
- `ConsoleSession` (SSH) dies when NIOS reboots. For any flow that reboots (grid join, network change, reset), use `console_connect_session()`, close the session that sent the reboot command, then open a new one to confirm the box is back. Do not `expect()` through a reboot. `ConsoleConnectSession` needs a lab-registered `Appliance`.
- `app.wapi` defaults to `WAPI_VERSION` (v2.12). Objects added later need a newer version: set `app.wapi_version = "v2.15"` (the setter rebuilds the client; re-read `app.wapi` afterwards).
- WAPI errors raise `WapiError` (`.status`, body). Status 0 means no HTTP response. Non-JSON error bodies (HTML 5xx) are wrapped, not raised as JSON errors. Retry only status 0 or >= 500.
- After a reboot or restart, `app.wapi.wait_until_ready()` before WAPI calls. Do not poll with your own loop.
- Deploys hash-verify and return `DeployResult`. Python deploys need a worktree in the config. Binary deploys (`deploy_serial_console`, `deploy_shared_lib`, `deploy_clusterd`) need a prior `make install` in the chroot. The chroot builds binaries and runs NCC. Mentioning the chroot does not mean write an NCC script.
- `deploy_token_py` skips manifest files absent from the worktree and lists them in `result.details["skipped"]`. Check it when a file you need might be missing.
- Log levels (`log_level` in the config): `off`, `info` (default), `debug` (full commands, exit codes), `verbose` (pexpect internals).
- If unsure which entry point or target to use, ask.

## Scripts and ticket tests

Reusable scripts (`/blox/adixon1/scripts/appliance/`): CLI args or env vars for inputs, validated before building dominion objects. Exit 0 on success, non-zero on failure, messages with hostname, operation and the actual error. No `Run` harness. Reuse an action before writing logic.

Ticket tests (`/blox/adixon1/scripts/tickets/<TICKET>/`), split by role once past a few steps:

- `test_<ticket>.py`: thin main. Builds objects, `run = Run()` first, `run.add(target)` for every target, `output_dir=run.output_dir` in every config, `try/finally: run.summary()`.
- `impl.py`: the phase functions. Each returns True or raises. Cleanup phases log instead of failing.
- `common.py`: constants and config (hosts, worktree, bundle path, expected strings). Config lives here, not in env vars.

Phases:

- Numbered narrative names in execution order (`"3. preflight"`), zero-arg lambdas: `run.test("4. enable via CLI", lambda: enable_cli(lab))`.
- A phase whose prerequisite failed gets `run.skip(name, reason)`, not a run.
- Env vars are only toggles. Gate optional phases with `SKIP_X=1` (`SKIP_DEPLOY`, `SKIP_RESET`) and `run.skip("name", "SKIP_X set")`. Validate gate values are 0 or 1.
- Assertions show actual values: `assert x == y, f"expected {y!r}, got {x!r}"`. Collect several failures per phase when it helps.
- Document the exact run command (including any `PYTHONPATH`) in the main file docstring.
- Console expect patterns come from real NIOS output, not guesses. Timeouts are explicit (boot 300s, httpd 45s).

## Changing the library

Layout: `src/` (appliance, container, transport, session, wapi, deploy, harness, log, grid, build, config, constants), `actions/` (multi-step workflows over an `Appliance`), `unit_tests/`.

- New action: `actions/<name>.py`, takes `Appliance`/`LocalAppliance`, uses dominion primitives, logs via `app._logger` (no `print`), exported from `actions/__init__.py`.
- New deploy method: on `DeployMixin` in `src/deploy.py`. File lists are UPPERCASE constants at the top. Upload, hash-verify, restart if needed, return `DeployResult`. Clean `__pycache__` after Python deploys. Raise `DeployError` with both hashes on mismatch.
- Timeouts and versions go in `src/constants.py`. Public classes use typed Config dataclasses with defaults for new fields.
- Transport failures raise `TransportError` or `CommandError`. No bare `except Exception: pass`.
- New logic gets unit tests with fakes (`unit_tests/conftest.py`). Nothing touches a network or docker. Run from the repo: `cd unit_tests && python -m pytest`.

Review checklist (dominion-qa):

- Duplication (high): a new primitive, action, deploy method or helper that already exists.
- Error handling (high): deploy hash mismatch raises `DeployError`. WAPI errors keep status and body. Timeouts adequate. No swallowed exceptions.
- Conventions (high): `from_config()`, `console_connect_session()` for reboots, no raw curl, ssh, scp or pexpect, logger not `print`.
- Backwards compatibility (medium): new params have defaults, no renamed public names without aliases, all callers grepped (scripts and ticket tests too).
- Testing (medium): new logic has a unit test, edge cases covered (timeout, container not running, stale session, non-JSON response).
- Scripts and tests (medium): hardcoded values that belong in `common.py` or args, `sys.path` hacks, missing `try/finally: run.summary()`, guessed expect patterns.
