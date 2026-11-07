# Issue #287: GTC Daily Execution System

**Status**: ✅ COMPLETED
**Priority**: P2
**Size**: Medium
**Foundation**: Issue #313 (Order Management System)

## Overview

Automated "set it and forget it" daily routines for open positions (they don't open trades) with:

- **Daily Scheduling**: Morning (9:20 AM ET) and evening (3:50 PM ET) routines, NYSE trading days only
- **Retry Logic**: Exponential backoff with configurable retries
- **Error Handling**: Comprehensive exception management and recovery
- **GTC Stops**: Protective stops held by the broker, raised to their trailing level, re-placed when
  missing and renewed before Alpaca's 90-day GTC limit
- **Minimal API Calls**: a broker snapshot (3 calls) and 1-2 closed-order fetches per routine, a fresh snapshot after a stop is placed or renewed or a GTT order goes out, and one call per stop placed, renewed or raised

## Architecture

### Components

```text
┌─────────────────────────────────────────────────────────┐
│                   Daily Scheduler                        │
│  • Task scheduling and execution                        │
│  • Retry logic with exponential backoff                 │
│  • Execution logging and monitoring                     │
└─────────────────┬───────────────────────────────────────┘
                  │
                  ▼
┌─────────────────────────────────────────────────────────┐
│              Trading Cycle (Issue #313)                  │
│  • Morning routine: reconcile, adjust stops             │
│  • Evening routine: EOD review, adjust stops            │
│  • GTC order management                                 │
└─────────────────┬───────────────────────────────────────┘
                  │
                  ▼
┌─────────────────────────────────────────────────────────┐
│              Order Management (Issue #313)               │
│  • GTC limit orders                                     │
│  • Bracket orders with stops/targets                    │
│  • Position tracking and reconciliation                 │
└─────────────────────────────────────────────────────────┘
```

### File Structure

```text
src/trading/
├── scheduling/
│   ├── daily_scheduler.py     # Main scheduler with retry logic, run window and lock
│   ├── scheduler_state.py     # Saved flags and run history (state/user.db)
│   ├── notices.py             # Local notices: failures visible without reading logs
│   └── trading_cycle.py       # Morning/evening routines (Issue #313)
├── state/
│   ├── state_reconciler.py    # Broker-as-truth reconcile, stop checks and raises
│   └── daily_records.py       # account_daily, position_daily, fills
└── orders/
    ├── order_manager.py       # Order lifecycle for the CLI (the routines use AlpacaOrderManager in broker/)
    └── trailing_stop_manager.py  # Trailing-stop levels

config_defaults/
└── scheduler_config.yaml      # Scheduling configuration (scheduler_config.json is a fallback)

scripts/deployment/
├── setup_cron.sh                     # crontab entry (recommended)
├── autogen-trading-scheduler.service  # systemd service (alternative)
└── install_scheduler.sh              # systemd installation

tests/unit/trading/
└── test_daily_scheduler.py    # Scheduler tests
```

## Features

### 1. Daily Scheduling

**Morning Routine (9:20 AM ET)**:

1. Fetch broker state (positions, open orders with bracket legs, account)
2. Daily records (account, positions, new fills)
3. Reconcile local state with the broker
4. Re-place missing stops and renew aging GTC stops, at the trailing level when higher (refetch if any were placed or renewed)
5. Position alerts
6. GTT triggers, with sells capped at the shares free to sell (`qty_available`); refetch if a GTT order or cancel went out
7. Raise stops to their trailing level; the broker's stop is the truth, levels stay at least 1% below the price, and orders Alpaca can't replace yet are skipped
8. Sync trailing stops to GTT, save state
9. Report: `reports/daily/<ET date>_morning.md`

**Evening Routine (3:50 PM ET)**:

- The same steps; stops are raised only while the market is open (run after the close, the raise waits for the morning), and only the morning routine saves the discrepancy list to local state
- Report: `reports/daily/<ET date>_evening.md`

The trailing levels are listed in the [quick start](02_gtc_scheduler_quickstart.md#trailing-stop-levels). Take-profit orders are not re-placed: a bracket's GTC take-profit lapses at 90 days, and the position then exits by its trailing stop.

### 2. Comprehensive Retry Logic

```python
# Exponential backoff with jitter
for attempt in range(1, max_attempts + 1):
    try:
        execute_routine()
        break  # Success
    except Exception as e:
        if attempt < max_attempts:
            delay = base_delay * (2 ** (attempt - 1))
            jitter = delay * 0.1
            sleep(delay + jitter)
        else:
            log_failure()
```

**Features**:

- Configurable max retries (default: 3)
- Exponential backoff (60s, 120s, 240s)
- 10% jitter to prevent thundering herd
- Detailed error logging

### 3. Error Handling

**Levels**:

1. **Task-level**: Catch and retry individual routine failures
2. **Execution-level**: Log all attempts and outcomes
3. **System-level**: Daemon continues despite errors
4. **Recovery**: Crash recovery rebuilds state from broker

**Error Types**:

- API errors → Retry with backoff
- Network failures → Retry with backoff
- Invalid data → Log and skip
- Broker discrepancies → Auto-reconcile

### 4. Unattended Robustness (#259)

#### Request timeouts

alpaca-py sends its requests without a timeout, so a broker that accepted a connection and
never answered would hang the cron check, and the run lock with it, until someone killed it.
`src/utils/http_timeouts.py` gives the three Alpaca clients (trading, market calendar, market
data) a default of 10 s to connect and 30 s to read for every request that does not pass its
own; the Alpha Vantage requests pass the same values.

#### Run watchdog

`main.py --run-due` (cron only; the daemon and the interactive CLI are unchanged) arms a
wall-clock limit before the scheduler runs: `RUN_DUE_TIMEOUT_SECONDS` = 900 s, retries and
their sleeps included, or `run_due_timeout_seconds` in the scheduler config. When it passes,
SIGALRM raises `RunDueTimeoutError`, which derives from `BaseException` so that the retry loop
and the routines' contained steps cannot retry or swallow it. Then:

- A routine in progress is recorded `timed_out` ("timed out after N s") in `scheduler_history`
  and the execution log, its `routine_failed` notice is raised, and the process exits 1. Unlike
  `failed`, a timeout does not settle the day: while the catch-up window is open the next tick
  runs the routine again, up to `MAX_TIMEOUTS_PER_DAY` = 2 timeouts. A run that had already
  recorded `completed` when the alarm landed keeps that result and raises no failure notice.
- A timeout anywhere else (the outcome check, say) leaves one `run_due_error` notice and exits 1.
- The run lock is released as the process ends, and the alarm and the previous handler are
  restored on the way out. Without `SIGALRM` (Windows) there is no limit.

The 900 s is the real bound on a check: the request timeouts are per read, not per call (a server
that dribbles bytes, or alpaca-py's own 429/504 retries, can take longer), so a degraded broker
can use all of it inside the first attempt. When a routine starts, the scheduler logs a warning
if its retry sleeps alone (60 + 120 + 240 s with the defaults) could pass the limit.

#### Catch-up window

A routine that has no `completed` or `failed` row today (`timed_out` rows do not count) may
start late, until its *run
deadline* (`DailyScheduler.run_deadline`), never earlier than 5 minutes after its time:

| Routine | Deadline |
|---------|----------|
| Morning | its time + 60 minutes (10:20 ET) |
| Evening | the close minus 1 minute (15:59 ET; 12:59 ET on a 13:00 early close) |

So the 15:55 tick catches a lost 15:50 tick, and the evening routine still starts before the
close. A late start is logged at INFO. `scheduler_history` still guarantees one run a day, and
a dry run keeps the 5-minute window, since it never counts as the day's run. So does a routine
that was switched on (or the scheduler enabled) after its 5-minute window: enabling it late
does not start a late run on the paper broker. `next_run` (never earlier than now) and the
missed-routine notice use the same deadline: a missed morning is reported after 10:25 ET, an
evening after 16:04 ET, and a miss is excused only if the routine was switched on after the
deadline.

#### State files and the calendar

The execution log (`state/scheduler_execution_log.json`) and the positions backup are written
to a temporary file and renamed, so a crash leaves the previous file whole. An empty answer
from Alpaca's calendar on a day the NYSE rules call a trading day is logged as a WARNING (the
day is still treated as closed). Syncing an existing GTT trailing-stop trigger moves its
expiration to 90 days from now, so a trigger synced twice a day never lapses.

The cron path does not import the CLI (`src/cli`, `core.factory`, the agent framework): only
the interactive and `--account` paths load it.

### 5. Configuration System

`config_defaults/scheduler_config.yaml`:

```yaml
enabled: false
market_timezone: America/New_York
morning_routine_time: '09:20:00'
evening_routine_time: '15:50:00'
morning_routine_enabled: true
evening_routine_enabled: true
max_retries: 3
retry_delay_seconds: 60
enable_notifications: false
dry_run: false
# run_due_timeout_seconds: 900   # wall-clock limit for one cron check (optional)
```

The enable flags are defaults: flags saved from the CLI, the config editor or the setup wizard (in `state/user.db`) take precedence.

## Usage

### Quick Start

```bash
# Run the morning routine now
python -m src.trading.scheduling.daily_scheduler --mode once --task morning_routine

# View status
python -m src.trading.scheduling.daily_scheduler --mode status

# Run as daemon (development)
python main.py --daemon

# Run whatever is due now, then exit (what cron runs every 5 minutes)
python main.py --run-due
```

### Deployment

#### Option 1: crontab (Recommended)

```bash
# Set PROJECT_ROOT and PYTHON_BIN at the top of the script, then add the entry
./scripts/deployment/setup_cron.sh

# The entry (every 5 minutes; the scheduler decides what is due):
# */5 * * * * cd $PROJECT_ROOT && $PYTHON_BIN main.py --run-due > /dev/null 2>> $PROJECT_ROOT/state/scheduler-cron.err

# View crontab
crontab -l

# Logs
tail -f state/scheduler.log
cat state/scheduler-cron.err
```

#### Option 2: systemd (Alternative, runs the daemon)

```bash
# Edit the paths in autogen-trading-scheduler.service and install_scheduler.sh first
sudo scripts/deployment/install_scheduler.sh

# Start service
sudo systemctl start autogen-trading-scheduler

# Logs (the service's stdout is discarded)
tail -f state/scheduler.log

# Status
sudo systemctl status autogen-trading-scheduler
```

### Integration with VotingSignal

Automated entries from `VotingSignal` signals are deferred (#574): the routines manage existing positions only.

## Monitoring

### Execution Logs

```json
{
  "task_name": "morning_routine",
  "scheduled_time": "09:20:00",
  "actual_start_time": "2025-01-15T09:20:05",
  "actual_end_time": "2025-01-15T09:20:32",
  "status": "completed",
  "attempt": 1,
  "error_message": null,
  "report_path": "reports/daily/2025-01-15_morning.md",
  "api_calls_used": 3
}
```

### Status Reports

```bash
# Generate status report
python -m src.trading.scheduling.daily_scheduler --mode status
```

Output:

```text
# Daily Scheduler Status Report
Generated: 2025-01-15 16:00:00

## Recent Executions (Last 7 Days)

### morning_routine
Total executions: 5
✅ Completed: 5
❌ Failed: 0
Success rate: 100.0%

### evening_routine
Total executions: 5
✅ Completed: 5
❌ Failed: 0
Success rate: 100.0%
```

### Log Files

- **Scheduler log**: `state/scheduler.log` (rotated at 5 MB, five kept; written by the daemon and cron runs)
- **Daemon crashes**: `state/scheduler-daemon.err`
- **Execution log**: `state/scheduler_execution_log.json` and `scheduler_history` in `state/user.db`
- **Notices**: `state/notices.json` (with `notices.lock` beside it), see [Notices](#notices)
- **Daily records**: `account_daily`, `position_daily` and `fills` in `state/user.db` (see `src/trading/state/daily_records.py`)
- **Broker snapshots**: `position_snapshots` and `order_snapshots` in `.cache/trading_data.db`
- **Morning reports**: `reports/daily/*_morning.md`
- **Evening reports**: `reports/daily/*_evening.md`
- **systemd logs**: `journalctl -u autogen-trading-scheduler` (start and stop only; output goes to `state/scheduler.log`)
- **Cron errors**: `state/scheduler-cron.err` (cron's stdout repeats `state/scheduler.log`, so it is dropped)

### Notices

A failure in the unattended loop leaves a *notice* in `state/notices.json`, so it is visible
without reading logs (#342). Notices are local: they involve no network call (CRITICAL ones
are also emailed when [email is set up](#email-and-heartbeat)). There is one notice per condition per day, so a condition seen
again only updates the notice's count and last-seen time.

| Key | Severity | Raised when |
|-----|----------|-------------|
| `routine_failed:<routine>:<date>` | CRITICAL | the routine failed after its retries, or the cron watchdog cut it off |
| `routine_missed:<routine>:<date>` | CRITICAL | no run is recorded 5 minutes after the routine's run deadline (see [catch-up window](#catch-up-window)) |
| `routine_stuck:<routine>:<date>` | CRITICAL | a run started over 30 minutes ago and never finished |
| `run_due_error:<date>:<Exception>` | CRITICAL | `main.py --run-due` crashed, or timed out outside a routine (the traceback still goes to `state/scheduler-cron.err`) |
| `stop_breach:<SYM>:<date>` | CRITICAL | a position is at or below its stop and still open |
| `target_breach:<SYM>:<date>` | WARNING | a position is at or above its target and still open |
| `unprotected:<SYM>` | CRITICAL | a position was left without a protective stop (until a run finds it protected) |
| `state_db_error:<date>` | CRITICAL | the saved flags or run history in `state/user.db` can't be read |
| `notices_file_reset:<date>` | WARNING | `notices.json` was unreadable; it was moved aside as `notices.json.corrupt-<time>` |

How they are found:

- **A run's own outcome** (`DailyScheduler._run_task`): a failure after the retries, the day's
  failed, missed and stuck notices resolved once a run completes, and, for a real run, the
  position notices from the routine's `last_alerts` (CRITICAL alerts only) and
  `last_discrepancies` (a missing stop left for review, or one that could not be placed). A dry
  run raises only the failure. The CLI's `test morning|evening` calls the routine directly, so it
  raises none.
- **The history, on every cron tick** (`DailyScheduler.check_routine_outcomes()`, run at the end
  of each `run_scheduled_tasks()`, lock or not): for today, once a routine's 5-minute run window
  and 5 more minutes have passed (a routine with no run at all waits for its run deadline plus 5
  minutes instead, since it may still start late), and for the previous trading day, a completed or dry-run row
  is fine, a failed row is a failure, a row still `running` after 30 minutes is stuck, and no row
  is a miss. It skips a disabled scheduler or routine, excuses a miss when the routine was
  switched on only after its run deadline (`morning_enabled_at` / `evening_enabled_at` in
  `scheduler_state`; a save that changes a time or the other routine doesn't count), and makes no broker or calendar call of its own: today's calendar
  lookup is the one `should_run_task` made, and the previous day is read from the NYSE rules (a
  one-off closure that neither the rules nor `config_defaults/market_hours.yaml` list would show
  as a miss the next day). An unreadable history is a `state_db_error` notice; the check never
  raises into the tick.

Notices stay open until a person runs `ack` in the scheduler CLI (`notices` lists them, `status`
shows the latest, each routine report ends with an "Open Notices" section, and
`get_scheduler_status` carries the count). A resolved notice is marked `resolved` but is not
acknowledged for you. If its condition returns, it opens again. Notices are dropped 30 days after
they were acknowledged or resolved, unless the condition was seen again since, and a report
lists the latest 10. A notices file that can't be read (no access, an I/O error) is left alone
and reported as unavailable; only a broken one is set aside. Titles and details are cleaned of API keys, account numbers, ids and equity and
cut to 300 characters; the file is replaced whole on every write, under a lock, so a cron run and
the CLI can use it together.

## Testing

```bash
# Run tests
python -m pytest tests/unit/trading/test_daily_scheduler.py -v
python -m pytest tests/unit/trading/test_notices.py tests/unit/trading/test_scheduler_notices.py -v
```

## Performance

### API Call Optimization

**Traditional approach**: 50+ API calls per day

- Check positions: 10-15 calls
- Update each stop: 3-5 calls per position
- Check order status: 10+ calls

**GTC approach**: 3-5 API calls per routine

- Single call to get all positions
- Single call to get all orders
- Batch modify stops: 1 call per modification

**Savings**: ~90% fewer API calls

### Execution Time

- **Morning routine**: 10-30 seconds
- **Evening routine**: 10-20 seconds
- **Total daily overhead**: < 1 minute

## Configuration

### Scheduler Settings

| Parameter | Default | Description |
|-----------|---------|-------------|
| `enabled` | `false` | Master enable/disable (default until flags are saved from the CLI) |
| `morning_routine_enabled` | `true` | Morning routine enable (same precedence) |
| `evening_routine_enabled` | `true` | Evening routine enable (same precedence) |
| `market_timezone` | `America/New_York` | Timezone of the routine times, independent of the host |
| `morning_routine_time` | `09:20:00` | Morning execution time |
| `evening_routine_time` | `15:50:00` | Evening execution time |
| `max_retries` | `3` | Maximum retry attempts |
| `retry_delay_seconds` | `60` | Base retry delay |
| `dry_run` | `false` | Routines send no orders and change no saved state; see [Dry run](#dry-run) |

### Dry run

With `dry_run: true` (or `test morning dry` / `test evening dry` in the scheduler CLI) a
routine fetches the broker state and works out everything it would do, but:

- **No orders are sent.** The order manager is wrapped in `DryRunOrderManager`
  (`src/trading/orders/dry_run.py`): the reads listed in its `READS` pass through, and every
  other call is logged and returns status `dry_run`. Missing or expiring stops are reported as
  `DRY_RUN_NOT_SENT`, and stop raises as "would raise stop $X -> $Y".
- **Nothing is saved.** No local state (`state/cost_efficient_positions.json`) and no daily
  records are written. GTT triggers are evaluated without saving trailing highs; those that
  fire are reported, not executed, and the trailing stops are not synced.
- **The report is marked.** It starts with a DRY RUN banner and is saved as
  `reports/daily/<date>_<routine>_dry_run.md`.

A scheduled dry run is recorded with status `dry_run` (🧪 in `history`), not `completed`, so
it doesn't count as the day's routine. Leave `dry_run` off on the live loop: while it is on,
no stop is placed, renewed or raised. The `status` command says so. Crash recovery in a dry
run rebuilds the state in memory and reports it, but saves nothing.
The `timeout_seconds` setting was removed in #601; no routine was ever time-limited.

### systemd Service

| Setting | Value | Purpose |
|---------|-------|---------|
| `Restart` | `on-failure` | Auto-restart on crashes |
| `RestartSec` | `30` | Wait 30s before restart |
| `CPUQuota` | `50%` | Limit CPU usage |
| `MemoryMax` | `1G` | Limit memory usage |

## Troubleshooting

### Service won't start

```bash
# Check logs
sudo journalctl -u autogen-trading-scheduler -n 50

# Verify configuration
python -m src.trading.scheduling.daily_scheduler --mode status

# Test manually
python -m src.trading.scheduling.daily_scheduler --mode once --task morning_routine
```

### Tasks not executing

1. Check the day and time: routines run on NYSE trading days only, at their ET time or up to 5 minutes after (the host timezone doesn't matter). On early closes the evening routine moves back with the close.
2. Verify schedule: `cat config_defaults/scheduler_config.yaml`
3. Check if already executed: Review `state/scheduler_execution_log.json`
4. Verify enabled: `status` in the CLI's `/schedule` mode shows the overall flag in force (a saved flag overrides the config file); `next` reflects the per-routine flags
5. Check for an overlapping run: only one run at a time holds `state/scheduler.lock`
6. Check for an earlier failure: a routine that failed after its retries waits for the next trading day (run it manually to retry)

### API errors

1. Check credentials: `config/config.json`
2. Verify API limits: Alpaca dashboard
3. Review error logs: `state/scheduler.log`
4. Test connection: `python main.py`, then `show portfolio`

### High failure rate

1. Review execution log: `state/scheduler_execution_log.json`
2. Increase retry count: `max_retries` in config
3. Increase retry delay: `retry_delay_seconds` in config
4. Check network stability

### Email and heartbeat

`enable_notifications` in `scheduler_config.yaml` gates nothing. Email and the heartbeat are set
in `config/config.json` (gitignored), under `notifications`:

| Key | Meaning |
| --- | --- |
| `notifications.email.smtp_host`, `smtp_port` | The mail server |
| `notifications.email.use_ssl` | `true`: SMTP over SSL (usually port 465); `false`: STARTTLS (usually 587) |
| `notifications.email.username`, `password` | Login (an app password, not your main one) |
| `notifications.email.from_addr` | Sender |
| `notifications.email.to_addrs` | A list of recipients (a single string works too) |
| `notifications.heartbeat_url` | A URL pinged with a GET after each healthy cron tick |

Email is on only when every email key is set; otherwise it is off and silent (`status` names
the missing keys). One message goes out for the open CRITICAL notices not yet sent; WARNING
notices are never emailed. A failed send is retried after 15 minutes, up to 3 attempts per
notice; at most 10 emails go out per market day (`state/email_log.json`). A notice that
reopens emails again. A crashed `--run-due` check is emailed in the same tick. The password,
user name, host and heartbeat URL are never logged. Check the settings with `notify test`.

The heartbeat is for a service that alerts when the pings stop (the cron entry or the host is
down). `--run-due` pings after each tick that ends without error, and the daemon at most once
every 5 minutes; a failed ping is only logged.

## Future Enhancements

### Planned (Future Issues)

- [x] Local notices for failures (done, see [Notices](#notices))
- [x] Email for CRITICAL notices, and a dead-man heartbeat for a cron that stops (done, see [Email and heartbeat](#email-and-heartbeat))
- [ ] SMS notifications
- [ ] Telegram bot integration
- [x] Skip weekends and NYSE holidays; move the evening routine on early closes (done)
- [ ] Multi-account support
- [ ] Performance analytics dashboard
- [ ] Machine learning for optimal execution times
- [ ] Dynamic retry strategy based on error type

### Integration Points

- **Issue #308**: Human-in-loop CLI for trade approval
- **Issue #310**: Multi-agent coordination
- **Issue #324**: Forward testing protocol
- **Issue #321**: Dynamic trailing stop logic

## Value Delivered

✅ **"Set it and forget it" automation**: No manual intervention needed
✅ **Reliability**: Comprehensive retry and error handling
✅ **Cost-efficient**: 90% fewer API calls
✅ **Deployment**: cron entry (recommended), or a systemd service running the daemon
✅ **Observable**: Detailed logging and status reports

## Related Documentation

- [Quick Start](02_gtc_scheduler_quickstart.md) - Setup, routine steps and trailing-stop levels
- [Scheduler CLI Reference](07_scheduler_cli_reference.md) - `/schedule` commands
- [Integration APIs](../02_architecture/05_integration_apis.md) - Alpaca orders and GTC handling
- Routine code: `src/trading/scheduling/trading_cycle.py`; settings: `config_defaults/scheduler_config.yaml`

---

**Issue #287 Status**: ✅ COMPLETED
**Last Updated**: 2026-10-20
**Implemented By**: Claude (AutoGen Trading System)
