# Plan: Split nios_test into unit_tests/ and integration_tests/

## Current state

`scripts/nios_test/tests/` mixes two different kinds of tests under one
custom harness (`helpers.py`: `test()`/`skip()`/`summary()`, invoked via
`python3 -m nios_test.tests.test_X [IP]` or the `invoke` bashrc alias):

| File | Contents |
|---|---|
| `test_transport.py` | Pure integration: real SSH + `docker exec` calls against vm-04-89 / balerion |
| `test_session.py` | Pure integration: real ShellSession/ConsoleSession against real targets |
| `test_container.py` | **Mixed** — `ContainerConfig`/`LocalContainerConfig` validation and `from_config()` wiring (no I/O, already unit-shaped) + real NCC/SSH session integration tests |
| `test_appliance.py` | **Mixed** — same pattern as above for `ApplianceConfig`/`Appliance` |
| `test_deploy.py` | Pure integration: real (destructive) deploys to appliance/NCC |
| `test_grid.py` | Pure integration: real `grid_deploy` subprocess against lab VMs |
| `test_sc_hello.py` | Pure E2E: build → deploy → verify on vm-04-89 |
| `local_appliance.py` | Pure integration: real `set_network()` flow (not in `run_all.py`, not `test_`-prefixed) |

Every "integration" file does real work (SSH, `docker exec`, pexpect spawns,
subprocess) **at module import time**, gated by `if should_run_appliance()
and is_appliance_up(): ...`. This is why they can't be pointed at directly by
pytest — collection would execute network I/O immediately, including on
files never meant to run automatically (`test_deploy.py` is destructive).

Nothing in the current suite tests the library's actual *logic* in
isolation — hash-mismatch detection in `deploy.py`, manifest construction
per group, `docker inspect` JSON parsing in `container.py`, ANSI-stripping
in `session.py`, tag-file XML parsing in `grid.py`, etc. That logic only
gets exercised as a side effect of hitting a real appliance.

## Decision: two test suites, two tools

- **`unit_tests/`** — pytest. No real transport, no docker, no pexpect
  spawn, no network. Fakes/mocks stand in for `Transport`, `pexpect.spawn`,
  and `subprocess`. Fast, safe to run anywhere, safe to run in CI.
- **`integration_tests/`** — keep the existing custom harness
  (`helpers.py` + `test()`/`skip()`/`summary()`), **do not** migrate to
  pytest.

  Why not pytest for integration: the harness's `summary()` writes
  `results/<timestamp>/summary.txt`, which `dominion`, `reckoning`, and
  `verdict` (in `bin/`) parse directly. Migrating to pytest would mean
  either reimplementing that reporting via a pytest plugin (junit-xml +
  custom formatting) or rewriting three CLI tools — disproportionate to
  the goal here. The custom harness's target-gating
  (`should_run_appliance()`/`should_run_ncc()`) and destructive-test
  warnings are also hand-rolled behavior pytest's fixture/marker system
  would need to reproduce, not simplify.

Naming: **`unit_tests/`** and **`integration_tests/`** (underscores, not
hyphens) — confirmed with Adam. Hyphenated directories aren't valid Python
package names, which would break the existing `python3 -m
nios_test.tests.test_X` invocation style and pytest's package-based
collection of `unit_tests/`.

## Target layout

```
scripts/nios_test/
  __init__.py, appliance.py, build.py, config.py, constants.py,
  container.py, deploy.py, grid.py, log.py, session.py, transport.py   (unchanged)

  unit_tests/
    __init__.py
    conftest.py            # fake Transport, fake pexpect child, tmp-path helpers
    pytest.ini             # testpaths = . ; keeps pytest from ever wandering into integration_tests/
    test_config.py         # ApplianceConfig, LocalApplianceConfig, ContainerConfig,
                            # LocalContainerConfig, GridConfig, BuildConfig
    test_transport.py      # SSHDockerTransport, LocalDockerTransport, connect(), CommandError
    test_deploy.py         # DeployMixin (via a minimal host class), DeployResult, DeployError
    test_container.py      # NiosContainer, LocalNiosContainer (wiring + pure parsing logic)
    test_appliance.py      # Appliance, LocalAppliance, ssh_reachable()
    test_session.py        # BaseSession, ShellSession, ConsoleSession
    test_log.py            # NiosLogger, formatters, resolve_log_file, create_logger
    test_grid.py           # Grid (_build_cmd, _parse_tag_file, appliances property)
    test_build.py           # optional — NiosBuild._run() command-string construction only

  integration_tests/        # renamed from tests/, contents otherwise unchanged
    __init__.py
    helpers.py
    run_all.py
    test_transport.py
    test_session.py
    test_container.py       # trimmed: unit-shaped tests removed (now in unit_tests/test_container.py)
    test_appliance.py       # trimmed: same
    test_deploy.py
    test_grid.py
    test_sc_hello.py
    local_appliance.py

  bin/                      # unchanged (dominion, reckoning, verdict, conquest, _common.py)
  results/                   # unchanged — already lives at nios_test/results, not under tests/
```

## Per-class unit test breakdown

Every class in the library, and how to test it without touching a real
appliance/NCC/network:

**`config.py`** — `ContainerConfig`, `LocalContainerConfig`,
`ApplianceConfig`, `LocalApplianceConfig`, `GridConfig`, `BuildConfig`.
Pure dataclasses; test `__post_init__` defaulting (`container_name` →
`"nios"`, `chroot` from `slot`, `log_file` resolution from `output_dir`,
`GridConfig`'s member/gm/topology label priority). Already partly covered
in the current mixed files — carry those over verbatim as pytest
functions.

**`transport.py`** — `SSHDockerTransport`, `LocalDockerTransport`,
`CommandError`/`TransportError`, `connect()`. Mock `subprocess.run` to
assert the exact argv built for `run()` (docker exec wrapped in ssh),
`run_on_host()`, `upload()` (SSH: scp → docker cp → rm sequence; Local:
`docker exec -i ... tee` with stdin bytes), `checksum()`/`host_checksum()`
hash-string parsing, `is_container_running()` true/false parsing,
`CommandError` message formatting. `connect()`: host=None → Local,
host set → SSH.

**`deploy.py`** — `DeployMixin` (mixed into `NiosContainer`), `DeployResult`,
`DeployError`. This is the highest-value target: real branching logic that
today is only exercised as a side effect of a live deploy.
Build a fake transport double (records calls, returns canned
hashes/output) and drive a real `NiosContainer` instance against it (no
network — transport is fully faked). Cover: `_resolve_chroot`/
`_resolve_worktree` (absolute vs relative, missing-worktree error),
`_local_md5`/`_local_md5_body` against real temp files (no mocking
needed), manifest construction per `groups` combination in
`deploy_token_py`, hash-mismatch → `DeployError` for each deploy method,
`skip_restart` behavior, `deploy_shared_lib`'s `backup` flag, the shebang-
preservation logic in `deploy_token_stats_consumer`.

**`container.py`** — `NiosContainer`, `LocalNiosContainer`. `from_config()`
wiring (transport type selection) is already covered in the current mixed
file — carry over. New coverage: `_wait_for_wapi` timeout vs success (fake
transport + `monkeypatch` on `time.sleep`), `restart()` orchestration
(asserts `docker restart` then `_wait_for_wapi` called). `LocalNiosContainer`
gets real unit coverage for the first time: `_get_container_network`
parsing of `docker inspect` JSON (missing `ncc_lan1` network, missing IP),
`_find_forwarding_bridge` text-parsing over canned `/sys/class/net`, `ip -o
link show`, and `bridge link show` output — several edge cases (no bridge
found, link not in forwarding state, no `master` match). These paths are
currently only exercised end-to-end via `local_appliance.py`; per
`HANDOFF.md` they've been an explicit coverage gap.

**`appliance.py`** — `Appliance`, `LocalAppliance`, `ssh_reachable()`.
`ssh_reachable()`: mock `subprocess.run`. `Appliance._ensure_ssh`/
`_enable_ssh_via_console`: mock `pexpect.spawn` to return a fake child with
scripted `.expect()` results — covers the login/password/already-at-prompt
branches and the "still unreachable after enabling" `RuntimeError`. Medium
effort, real payoff (this is the trickiest branching logic in the file and
has never been tested). `LocalAppliance.from_config`: the ip-is-a-
construction-invariant behavior (`ip` provided → skip `set_network()`;
`ip=None` → `set_network()` called) via a mocked `LocalNiosContainer`.

**`session.py`** — `BaseSession`, `ShellSession`, `ConsoleSession`.
`BaseSession._clean()` is a pure static method (ANSI stripping, CRLF
normalization) — trivial direct unit tests. `expect()`/`run()` echo-
stripping and exception mapping (`pexpect.TIMEOUT` → `TimeoutError`,
`pexpect.EOF` → `RuntimeError`) can be tested by injecting a duck-typed
fake child (an object with `.expect()`/`.before`/`.after`/`.sendline()`)
in place of `self._child` — no real pexpect spawn required.
`ShellSession`/`ConsoleSession.__init__` spawn-command string construction
given various `transport_cmd_prefix` inputs (empty, SSH, with `-tt`
inserted) is pure string-building — test directly.

**`log.py`** — `NiosLogger`, `InfoFormatter`/`DebugFormatter`/
`VerboseFormatter`, `resolve_log_file()`, `create_logger()`.
`resolve_log_file()` is pure. `create_logger()`: level validation
(`ValueError` on bad level), correct formatter attached per level, file
handler only added when `log_file` given. `NiosLogger.verbose`/
`verbose_ex`: attach a capturing handler and assert records emitted only
when the level is enabled, and that `verbose_ex`'s `variables` dict renders
into the message.

**`grid.py`** — `Grid`. `_build_cmd()` is pure — table-test every optional
`GridConfig` field's effect on the argv list. `_parse_tag_file()` is pure
XML parsing — write real temporary tag-file XML fixtures (master only,
master + N members, missing fields) and assert the parsed dict. `Grid.
appliances` property: table-test gm-only / gm+members / no-gm. `_make_
appliance()`: mock `Appliance.from_config` to avoid a real SSH probe.

**`build.py`** — `NiosBuild`. The module's own comment says no test file
exists "intentionally — builds are destructive and slow," which is correct
for *integration* coverage. Optionally add a narrow unit test for `_run()`'s
generated bash command string only (mock `subprocess.Popen`, never
actually invoke `make`) — pure string construction, zero risk. Marked
optional since it's the one class where "if possible" may reasonably mean
"skip it."

## Migrating the mixed files

For `test_container.py` and `test_appliance.py`, split by content rather
than rewriting from scratch:
1. Copy the config-validation and `from_config()`-wiring functions
   (`test_config_ssh_requires_host`, `test_from_config_local_creates_local_
   transport`, `test_appliance_repr`, etc.) into the new `unit_tests/`
   files, converting from `test("name", fn)` registration calls to plain
   `def test_*():` pytest functions.
2. Delete those functions from the `integration_tests/` copies, leaving
   only the real NCC/SSH session blocks.
3. Everything else (`test_transport.py`, `test_session.py`, `test_deploy.py`,
   `test_grid.py`, `test_sc_hello.py`, `local_appliance.py`) moves to
   `integration_tests/` unchanged.

## pytest collection safety

Two files can legitimately share a basename (`unit_tests/test_transport.py`
vs `integration_tests/test_transport.py`). Since both directories will have
`__init__.py`, pytest's default (`rootdir`-relative package import) resolves
them as distinct modules (`unit_tests.test_transport` vs
`integration_tests.test_transport`) — no collision. The real risk is pytest
being pointed at the repo root and *also* collecting `integration_tests/`,
which would execute real SSH/docker/pexpect calls at import time. Guard
against this explicitly:
- `unit_tests/pytest.ini` (or a root `pyproject.toml` `[tool.pytest.ini_options]`
  with `testpaths = ["scripts/nios_test/unit_tests"]`) so a bare `pytest` run
  never wanders into `integration_tests/`.
- Never add `test_` prefix stripping or pytest markers to
  `integration_tests/` files — keep them structurally unrecognizable to
  pytest's default discovery, as a second line of defense.

## Tooling updates required (found while reviewing, not optional)

- `bin/conquest`: `TESTS_DIR="$SCRIPTS_DIR/nios_test/tests"` → `nios_test/
  integration_tests`. Also add a "unit tests" menu section that just shells
  out to `pytest unit_tests/`.
- `~/.bashrc`: `_NIOS_TEST_TESTS="/blox/adixon1/scripts/nios_test/tests"` →
  `.../integration_tests`. The `invoke()` function reads this var, so it
  updates automatically once the var changes.
- `tests/run_all.py` → `integration_tests/run_all.py`: `TEST_FILES` list is
  unaffected by the rename, still lists integration files only. Consider
  (separately, not required) adding `local_appliance.py` to it now that
  it's colocated, though it stays out today because it's destructive/slow
  by design — same reasoning as `test_deploy.py`'s NCC skip.
- `docstrings` in each moved file that say `python3 -m nios_test.tests.
  test_X` → `python3 -m nios_test.integration_tests.test_X`.

## Follow-up (do after implementing)

- Update `~/.claude/skills/nios-test/SKILL.md`'s "Test Harness" and
  "Writing a ticket test" sections to reflect `integration_tests/` instead
  of `tests/`, and add a short "Unit Tests" section describing `unit_tests/`
  + how to run it (`pytest scripts/nios_test/unit_tests/`).
- Update `HANDOFF.md`'s "Test coverage for LocalNiosContainer" open item —
  this plan closes that gap via `unit_tests/test_container.py`.

## Sequencing (for whoever implements this)

1. Create `unit_tests/` with `conftest.py` (fake transport, fake pexpect
   child fixtures) and `pytest.ini`.
2. Write `test_config.py`, `test_transport.py`, `test_log.py` first — no
   dependencies on other new fixtures, quick wins, validates the fake-
   transport/fake-child approach before reusing it everywhere else.
3. Write `test_deploy.py`, `test_container.py`, `test_session.py` — reuse
   the fakes from step 2.
4. Write `test_appliance.py` (pexpect-mock is the most involved piece,
   deliberately last) and `test_grid.py`.
5. `git mv tests/ integration_tests/`, then trim the migrated unit-shaped
   functions out of `integration_tests/test_container.py` and
   `test_appliance.py`.
6. Update `bin/conquest`, `~/.bashrc`, docstrings, `run_all.py` header
   comment.
7. Run `pytest scripts/nios_test/unit_tests/` (fast, no infra needed) and
   `python3 -m nios_test.integration_tests.run_all` (needs balerion +
   vm-04-89 up) to confirm both suites pass post-migration.
8. Update the `nios-test` skill file and `HANDOFF.md` per the Follow-up
   section above.
