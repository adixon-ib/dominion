# Handoff — before releasing to coworkers

## Must do

- [ ] **Port/relocate loose scripts in `scripts/`** — decide destination per script:
  - `scripts/test_nios_lib.py`, `scripts/test_deploy_serial_console.py` — already use `dominion`, just sitting loose at the top level with their own local `test()` helper. Move into `dominion/` (e.g. an `examples/` dir) and switch to `Run`, or delete if superseded by `unit_tests/`/`integration_tests/`.
  - `scripts/appliance/*.sh` (`deploy_token_py.sh`, `deploy_libibonedb_util.sh`, `deploy_token_stats_consumer_to_appliance.sh`, `update_token_bundle.sh`, `nios_dns.sh`, `token_health_check.sh`) — decide per script: become a `deploy_*`/method on `Appliance`, or stay a standalone shell tool invoked via `subprocess` (current pattern, e.g. `sanity.py`'s `UPDATE_TOKEN_BUNDLE_SCRIPT`).
  - `scripts/lab/grid_deploy` — already wrapped via `subprocess` in `Grid`. Decide if it should be inlined into `grid.py` instead.

## After porting scripts

- [ ] **Rewrite `~/.claude/skills/nios-test/SKILL.md`**:
  - Add more worked examples per entry point + a "common gotchas" section
  - Add a references section (README.md, HANDOFF.md, `src/config.example.json`)
