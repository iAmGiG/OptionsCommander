# State Directory

Local state, logs and notices for the scheduler and the CLI. Everything here except this file is gitignored. The broker is the truth: local state is reconciled to it on every routine.

`$AUTOTRADER_STATE_DIR` moves this directory (`src/utils/state_paths.py`). Tests set it to a temp dir, so they never touch these files.

## Files

| File | Written by | Holds |
| ---- | ---------- | ----- |
| `user.db` | Several managers (SQLite) | Daily records (`account_daily`, `position_daily`, `fills`), GTT triggers and trailing-stop state (`gtt_triggers`), scheduler flags and run history (`scheduler_state`, `scheduler_history`), trading mode changes (`trading_mode_history`); other tables, such as watchlists, are created on first use |
| `cost_efficient_positions.json` | `LocalStateManager` | Each position's entry, quantity and stop as last reconciled; saved atomically |
| `positions.json` | `PositionManager` | A cache of the broker's positions (the broker is read first) |
| `notices.json`, `notices.lock` | Notices (#618) | Failed, missed or stuck routines, stop breaches and positions without a stop, until acknowledged (`/schedule`, then `notices` and `ack`) |
| `scheduler.log` | The scheduler | The routines' log, rotated |
| `scheduler-cron.err` | The cron entry | Errors from `main.py --run-due` only; normally empty |
| `scheduler.lock` | The scheduler | The `flock` that stops overlapping runs |
| `scheduler_execution_log.json` | The scheduler | Recent run records |
| `partial_exits.json` | `PartialExitManager` | Partial exit plans, once any are registered |
| `backups/` | `/backup` (`DBBackupManager`) | Database backups (`user_*.db`, `cache_*.db`); `/backup cleanup` removes only `.db` backups past retention, keeping the 5 newest of each |
| `exports/` | `/backup export` | Table exports as JSON |

Reports are in `reports/daily/`, and the market-data cache is `.cache/trading_data.db` (`$AUTOTRADER_CACHE_DIR`), not here.
