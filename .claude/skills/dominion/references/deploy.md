# Deploy API

All return `DeployResult(artifact, changed, local_hash, remote_hash, restarted, details)`.

| Method | Source | Restart |
|---|---|---|
| `deploy_token_py(groups, branding_source, skip_restart)` | Python files from worktree. Groups: `branding`, `ibap`, `pyabs`, `wapi` | Stops `admin-httpd` via pmc |
| `deploy_serial_console()` | Binary from chroot | `killall serial_console` |
| `deploy_shared_lib(local_subpath, dest, restart_target, backup)` | `.so` from chroot (default: `libibonedb_util.so`) | `killall <target>` |
| `deploy_token_stats_consumer(skip_restart)` | Consumer script from worktree | `token_stats_consumer_control` stop/start |
| `deploy_clusterd()` | Binary from chroot | `killall clusterd` |
| `deploy_nios_model_cloud_init()` | cloud-init module + config from worktree | None (module requests its own restart) |

- `deploy_token_py`, `deploy_token_stats_consumer`, and `deploy_nios_model_cloud_init` require `worktree` in the config.
- `deploy_serial_console`, `deploy_shared_lib`, and `deploy_clusterd` require a prior `make install`.
