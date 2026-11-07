# Quick Start: GTC Daily Execution (Issue #287)

## What is it?

Automated "set it and forget it" daily routines for open positions. They don't generate signals
or open new positions (GTT triggers are checked only for symbols you hold, so a GTT order you set
up on one of them can still fire). They:

- Run twice daily (9:20 AM and 3:50 PM ET, whatever the host's timezone), on NYSE trading days only;
  on early-close days the evening routine moves back with the close (12:50 PM for a 1 PM close)
- Raise stops as profit grows (see [Trailing Stop Levels](#trailing-stop-levels))
- Re-place missing stops and renew GTC stops before Alpaca's 90-day limit
- Retry failed runs automatically
- Use a few API calls per routine: a broker snapshot (3 calls) and 1-2 closed-order fetches for the daily records, a fresh snapshot after a stop is placed or renewed or a GTT order goes out, and one call per stop placed, renewed or raised

## 5-Minute Setup

### 1. Test the Scheduler

```bash
# Navigate to project root
cd ~/OptionsCommander

# Activate environment
conda activate AutoTrader

# Run the morning routine now (it manages stops on the paper account)
python -m src.trading.scheduling.daily_scheduler --mode once --task morning_routine
```

Expected output:

```text
✅ morning_routine completed successfully
Task: morning_routine | Status: completed | Duration: 12.3s | Attempt: 1 | API Calls: 3
```

### 2. Review Configuration

```bash
# View configuration
cat config_defaults/scheduler_config.yaml
```

The file's `enabled`, `morning_routine_enabled` and `evening_routine_enabled` are defaults. Once the
scheduler is enabled or disabled from the CLI (`/schedule`, then `enable` or `disable`), the config
editor or the setup wizard, those saved flags (in `state/user.db`) take precedence. Times always come
from the file.

Edit if needed:

```yaml
enabled: false                    # default only; flags saved from the CLI take precedence
morning_routine_time: '09:20:00'  # adjust timing if needed
evening_routine_time: '15:50:00'
max_retries: 3
```

### 3. Choose Deployment Method

#### Option A: crontab (Recommended)

```bash
# Set PROJECT_ROOT and PYTHON_BIN at the top of the script, then add the entry
./scripts/deployment/setup_cron.sh

# It adds one entry every 5 minutes; the scheduler decides what is due:
# */5 * * * * cd $PROJECT_ROOT && $PYTHON_BIN main.py --run-due > /dev/null 2>> $PROJECT_ROOT/state/scheduler-cron.err

# Verify
crontab -l

# View logs
tail -f state/scheduler.log      # routine log (rotated at 5 MB)
cat state/scheduler-cron.err     # errors only (cron's stdout repeats the log)
```

A lock (`state/scheduler.lock`) keeps a cron run from overlapping a long retry, the daemon or a
manual run.

#### Option B: systemd (Alternative, runs the daemon)

```bash
# Edit the install paths, User and Group in scripts/deployment/autogen-trading-scheduler.service
# (WorkingDirectory, ExecStart, Environment=PYTHONPATH, StandardError, ReadWritePaths; with
# ProtectSystem=strict a ReadWritePaths entry that doesn't exist stops the unit from starting)
# and PROJECT_ROOT, USER and GROUP in install_scheduler.sh, then:
sudo scripts/deployment/install_scheduler.sh

# Start service
sudo systemctl start autogen-trading-scheduler

# Check status
sudo systemctl status autogen-trading-scheduler

# View live logs
tail -f state/scheduler.log
```

#### Option C: Manual (For testing)

```bash
# Run as daemon in terminal
python main.py --daemon
```

### 4. Monitor Execution

**View status report:**

```bash
python -m src.trading.scheduling.daily_scheduler --mode status
```

**Check execution log:**

```bash
cat state/scheduler_execution_log.json | jq '.'
```

**View daily reports:**

```bash
ls -lh reports/daily/
cat reports/daily/$(ls -t reports/daily/ | head -1)
```

## What Happens Automatically?

### Morning Routine (9:20 AM ET)

1. ✅ Fetch positions, open orders (bracket legs included) and the account from the broker
2. ✅ Record the account, positions and any orders filled since the last run (daily records)
3. ✅ Reconcile local state with the broker (the broker is the truth)
4. ✅ Re-place missing stops and renew GTC stops near Alpaca's 90-day limit, at the trailing level
   when that is higher; fetch broker state again if any were placed
5. ✅ Check position alerts
6. ✅ Check GTT triggers (GTT sell orders are capped at the shares free to sell, Alpaca's
   `qty_available`); fetch broker state again if a GTT order or cancel went out
7. ✅ Raise stops to their trailing level (one modify call per stop that moves)
8. ✅ Sync trailing stops to GTT and save local state
9. ✅ Write `reports/daily/<ET date>_morning.md`

### Evening Routine (3:50 PM ET)

The same steps, with two differences:

- Stops are raised only while the market is open, so an overnight gap can't take the day's gains
  before the next morning run. Run by hand after the close, the raise is left to the morning (thin
  after-hours prints shouldn't set the stop).
- The discrepancy list isn't saved to local state (only the morning routine saves it).

The report is `reports/daily/<ET date>_evening.md`; a second run the same day adds `_2`.

### Trailing Stop Levels

Stops only move up. By profit since entry:

- Under 2%: no change
- 2-4%: move to breakeven
- 4-6%: lock 25% of the gain
- 6-10%: lock 50% of the gain
- 10% and over: lock 75% of the gain

The level is capped 1% below the current price, and a stop moves only when the new level is above the
broker's live stop (the broker's stop is the truth, so a raise it refused is worked out again next
run). Stop orders Alpaca can't replace yet (`accepted` or `pending_*`, e.g. placed after the close)
are skipped until a later run. These are the defaults (`climb_rate: medium` in `TrailingStopConfig`,
`config_defaults/trading_config.py`); a `trailing_stops` section in `trading_config.yaml` overrides
them.

### Exits

The routines renew and re-place stops, not take-profit orders. Alpaca cancels GTC orders after 90
days, so a bracket's take-profit leg lapses (an aging one is flagged in the report for review) and the
position then exits by its trailing stop. The live positions' take-profit legs have lapsed, so they
exit only by trailing stop.

### Daily Records

Each routine stores, in `state/user.db`:

- `account_daily`: equity, the previous close's equity (`last_equity`), cash and buying power
- `position_daily`: each position's quantity and average cost
- `fills`: every filled order and bracket leg (stop or target), by ET trading day

The report's **Daily Records** section shows the equity and day P&L, the fills since the last
run, and the day's realized P&L (average cost; a sale of a position bought before records began
is flagged as having an unknown cost basis).

## Error Handling

The system automatically handles:

- **API errors**: Retries with exponential backoff (60s, 120s, 240s)
- **Network failures**: Retries up to 3 times
- **Broker discrepancies**: Auto-reconciles with broker-as-truth
- **Crashed state**: Recovery mode rebuilds from broker

If all retries fail, you get:

- Detailed error log in `state/scheduler.log`
- Failed execution entry in `state/scheduler_execution_log.json` and `state/user.db`
- System continues and tries again next scheduled time

A routine also counts as failed (and is retried) when a protective stop couldn't be checked,
placed or renewed. Its report is still written. A routine that fails after its retries isn't
started again that day; run it manually once the cause is fixed.

## Stopping/Pausing

**Temporary pause (keep service running):**

In the CLI, `/schedule` then `disable` turns the scheduler off (the setup wizard, `setup`, can turn
off a single routine). Cron keeps running `--run-due`, which then skips the routines.

**Stop service:**

```bash
# cron
crontab -e  # Delete the OptionsCommander lines

# systemd
sudo systemctl stop autogen-trading-scheduler

# daemon
Ctrl+C in terminal
```

## Cost Comparison

| Approach | API Calls/Day | Monthly Cost* |
|----------|---------------|---------------|
| Traditional polling | 1,000+ | $50-100 |
| GTC with daily checks | 6-10 | $5-10 |
| **This system** | **6-10** | **$5-10** |

*Estimated based on typical API pricing

## Troubleshooting

**Scheduler not running:**

```bash
# cron: is the entry there, and did it log errors?
crontab -l | grep "main.py --run-due"
cat state/scheduler-cron.err
tail -n 50 state/scheduler.log

# systemd
sudo systemctl status autogen-trading-scheduler
cat state/scheduler-daemon.err
```

**Tasks not executing:**

```bash
# Times are ET whatever the host timezone, and routines skip weekends and NYSE holidays.
# Check that the scheduler and the routine are enabled
python -m src.trading.scheduling.daily_scheduler --mode status

# Check if already ran today
cat state/scheduler_execution_log.json | jq '.[-5:]'
```

**Need to run manually:**

```bash
# Morning routine
python -m src.trading.scheduling.daily_scheduler --mode once --task morning_routine

# Evening routine
python -m src.trading.scheduling.daily_scheduler --mode once --task evening_routine

# Whatever is due now (what cron runs)
python main.py --run-due
```

## Next Steps

1. **Monitor for 1 week**: Let it run and review daily reports
2. **Verify stop adjustments**: Check that stops move correctly
3. **Review execution log**: Ensure 100% success rate
4. **Customize schedule**: Adjust times in `scheduler_config.yaml` if needed

Automated entries from `VotingSignal` signals are deferred (#574): the routines manage existing
positions only.

## Advanced Usage

### Notifications

`enable_notifications` in `scheduler_config.yaml` gates nothing. Email for CRITICAL notices and
a heartbeat URL are set in `config/config.json` (see
[Email and heartbeat](03_gtc_scheduler_technical.md#email-and-heartbeat)):

```json
{
  "notifications": {
    "email": {
      "smtp_host": "smtp.example.com",
      "smtp_port": 465,
      "use_ssl": true,
      "username": "user@example.com",
      "password": "app-password",
      "from_addr": "user@example.com",
      "to_addrs": ["you@example.com"]
    },
    "heartbeat_url": "https://hc-ping.example.com/your-uuid"
  }
}
```

Then run `notify test` in the scheduler CLI.

## Documentation

- **Full documentation**: [03_gtc_scheduler_technical.md](03_gtc_scheduler_technical.md)
- **Trading cycle details**: `src/trading/scheduling/trading_cycle.py`
- **Scheduler implementation**: `src/trading/scheduling/daily_scheduler.py`
- **Tests**: `tests/unit/trading/test_daily_scheduler.py`

## Support

Issues or questions:

- GitHub Issues: <https://github.com/iAmGiG/OptionsCommander/issues>
- Documentation: [03_gtc_scheduler_technical.md](03_gtc_scheduler_technical.md)
- Examples: `scripts/deployment/`

---

The scheduler now manages stops and daily records on your account. It does not open trades.

Monitor the logs and review the reports.
