# Scheduler CLI Reference

## Overview

The Scheduler CLI provides a dedicated interactive interface for managing the daily trading scheduler. It's accessible via the `/schedule` command from the main trading CLI, or can be run standalone.

## Access Methods

### From Main Trading CLI

```bash
python main.py
> /schedule
```

### Standalone Mode

```bash
python -m src.cli.scheduler_cli
```

## Commands

### Status & Monitoring

#### `status`

Show detailed scheduler status including:

- **Config status** (the overall flag in force: a saved flag, else the config file)
- **Service status** (`CRON` when this checkout's `main.py --run-due` entry is in the crontab, `RUNNING` when the daemon runs, else `NOT RUNNING`)
- Configuration summary (times, retries, timezone)
- Today's execution count
- Recent execution results
- **Open notices**: the count and the latest five, or `No open notices` (see [`notices`](#notices))

**IMPORTANT:** "Config: ENABLED" does NOT mean the scheduler is running automatically!
Automatic execution needs the cron entry (recommended, see the
[quick start](02_gtc_scheduler_quickstart.md#option-a-crontab-recommended)) or the daemon.

**Example (config enabled, under cron):**

```text
🟢 Config: ENABLED
✅ Service: CRON (main.py --run-due every 5 minutes runs routines when due)
```

**Example (config enabled, neither cron nor daemon):**

```text
Scheduler> status

⏰ Scheduler Status
======================================================================

🟢 Config: ENABLED
❌ Service: NOT RUNNING (no automatic execution)

💡 Config is enabled but nothing runs it.
   To start automatic execution, exit this CLI and either:
   - add the cron entry: ./scripts/deployment/setup_cron.sh (recommended)
   - or run the daemon: python main.py --daemon

   Or use 'test morning/evening' to run now (acts on the broker), or 'test morning dry' to send no orders

⚙️  Configuration:
   Morning: 09:20:00 ET
   Evening: 15:50:00 ET
   Timezone: America/New_York
   Max Retries: 3
   Dry Run: False

📋 No executions today
   (Neither cron nor the daemon is running - no automatic executions)

✅ No open notices
```

**Example (config enabled, daemon RUNNING):**

```text
Scheduler> status

⏰ Scheduler Status
======================================================================

🟢 Config: ENABLED
✅ Service: RUNNING (automatic execution active)

⚙️  Configuration:
   Morning: 09:20:00 ET
   Evening: 15:50:00 ET
   Timezone: America/New_York
   Max Retries: 3
   Dry Run: False

📋 Today's Executions: 2
   ✅ morning_routine - completed
   ✅ evening_routine - completed

✅ No open notices
```

#### `history`

View execution history (last 20 runs, 7 days)

**Example:**

```text
Scheduler> history

📜 Execution History
======================================================================

Showing last 20 executions (7 days):

✅ morning_routine        completed     2025-11-11 09:20
✅ evening_routine        completed     2025-11-11 15:50
✅ morning_routine        completed     2025-11-10 09:20
❌ evening_routine        failed        2025-11-10 15:50
   ⚠️  Connection timeout to Alpaca API...
```

#### `next`

Calculate and show next scheduled run with countdown

**Example:**

```text
Scheduler> next

🔮 Next Scheduled Run
======================================================================

⏰ Evening Routine
   Time: 03:50 PM ET
   Countdown: 2h 15m
   Date: 2025-11-11
```

#### `logs`

Show the last lines of `state/scheduler.log` (default 50; `logs 100` for more). Alias: `log`.

#### `notices`

List the open notices: the failures and warnings the unattended loop raised, so they are visible
without reading logs (see [Notices](03_gtc_scheduler_technical.md#notices)). `notices all` adds
the acknowledged ones. A notice stays open until it is acknowledged; one whose condition has
cleared is marked `resolved` but still listed.

```text
Scheduler> notices

⚠️ Notices (open)
======================================================================

#1 CRITICAL Morning routine failed after 4 attempts (first seen 2026-10-27 13:25 UTC)
     Attempt 4 failed: Morning routine failed: protective stops not secured (SPY)
     Report: reports/daily/2026-10-27_morning.md

#2 CRITICAL SPY has no protective stop (first seen 2026-10-27 13:25 UTC, seen 2 times, resolved 2026-10-27 19:50 UTC)
     no stop level below the current price to place

'ack <number>' or 'ack all' acknowledges; a condition that returns reopens it
```

#### `ack`

`ack <number>` acknowledges one notice, `ack all` every open one. An acknowledged notice leaves
`status`, `notices` and the routine reports; `notices all` still shows it for 30 days. Only a
person acknowledges: the CLI's `get_scheduler_status` shows the open-notice count, and no tool
acknowledges.

### Configuration Management

#### `config`

Display current scheduler configuration file contents

**Example:**

```text
Scheduler> config

⚙️  Scheduler Configuration
======================================================================

Config file: config_defaults/scheduler_config.yaml

enabled: true
market_timezone: "America/New_York"
morning_routine_time: "09:20:00"
evening_routine_time: "15:50:00"
max_retries: 3
...
```

#### `edit`

Interactive configuration editor

**Features:**

- Edit all scheduler settings interactively
- Input validation (time format, ranges)
- Save and reload without restart
- Cancel without changes

**Example Session:**

```text
Scheduler> edit

📝 Scheduler Configuration Editor
======================================================================

Current settings:
  1. Enabled: True
  2. Morning routine: 09:20:00
  3. Evening routine: 15:50:00
  4. Max retries: 3
  5. Dry run mode: False
  0. Save and exit
  q. Cancel

Edit setting (1-5, 0 to save, q to cancel): 2
Morning routine time (HH:MM:SS): 09:15:00

Edit setting (1-5, 0 to save, q to cancel): 0
✅ Configuration saved!
🔄 Reloading scheduler...
✅ Scheduler reloaded
```

### Control Commands

#### `setup`

First-time setup wizard (alias `wizard`): enables the scheduler, sets each routine's time and
on/off flag, and saves the flags to `state/user.db`. It then offers to start the daemon, which
isn't needed with the cron entry.

#### `start` / `stop`

Start the daemon (`main.py --daemon`) in the background, or stop it. Not needed with the cron
entry, which runs `main.py --run-due` every 5 minutes. A routine starts within 5 minutes of its
time; if no tick ran it, it still starts late until its run deadline (morning: 60 minutes
after its time; evening: a minute before the close), see the
[catch-up window](03_gtc_scheduler_technical.md#catch-up-window), except that enabling the
scheduler or a routine after its 5-minute window starts no late run that day. A cron check that runs over
15 minutes is cut off and leaves a notice.

#### `enable`

Enable scheduler (sets `enabled: true` in config and saves the flag to `state/user.db`)

```text
Scheduler> enable
✅ Scheduler enabled
```

#### `disable`

Disable scheduler (sets `enabled: false` in config and saves the flag to `state/user.db`)

```text
Scheduler> disable
❌ Scheduler disabled
```

### Testing

#### `test morning`

Run the morning routine now instead of waiting for its scheduled time. It is the real
routine, run against the broker: it can place, renew and raise stops. Add `dry`
(`test morning dry`) to send no orders and save nothing: the report, saved as
`reports/daily/<date>_morning_dry_run.md`, shows what the routine would have done. With
`dry_run: true` in the config, `test morning` is a dry run too.

**Example:**

```text
Scheduler> test morning

🧪 Testing morning routine (acts on the broker)...

✅ Morning routine completed

Report preview:
----------------------------------------------------------------------
# Morning Routine Report - 2025-11-11 09:20

## Broker State
- Positions: 2
- Open Orders: 4
- Buying Power: $50,000.00

## Alerts
⚠️ SPY approaching take profit (2.1% away)
✅ TQQQ position healthy

## Actions Taken
- Adjusted 1 stop loss order
...
----------------------------------------------------------------------

Full report saved to: reports/daily/
```

#### `test evening`

Run the evening routine now. As with `test morning`, it acts on the broker: it can
place and renew stops, and raises them only while the market is open. `test evening dry`
sends no orders and saves nothing.

```text
Scheduler> test evening

🧪 Testing evening routine (acts on the broker)...

✅ Evening routine completed

Report preview:
----------------------------------------------------------------------
# Evening Routine Report - 2025-11-11 15:50

## End of Day Summary
- P/L Today: +$125.50 (+0.25%)
- Positions: 2 open, 1 closed
- Orders: 3 pending overnight
...
----------------------------------------------------------------------

Full report saved to: reports/daily/
```

### Report Details

Reports are saved to `reports/daily/` with human-readable filenames, named by the market (ET) date whatever the host's timezone:

- First run: `2025-11-11_morning.md`
- Multiple runs: `2025-11-11_morning_2.md`, `2025-11-11_morning_3.md`

**Report Contents**:

1. **Account Summary**
   - Portfolio value, available cash, buying power

2. **Active Positions Table**

   ```text
   | Symbol | Entry | Current | Stop | Target | P&L | Action |
   |--------|-------|---------|------|--------|-----|--------|
   | META   | $628  | $625    | $597 | $678   | -$24 | No change |
   | SPY    | $658  | $683    | $625 | $710   | +$350| No change |
   ```

   - **Stop/Target prices** synced from Alpaca GTC orders (not $0.00)
   - **P&L** calculated from current price vs entry
   - **Action** shows if stops were adjusted

3. **Discrepancies** (if any)
   - Positions at broker but not in local state
   - Quantity mismatches (auto-fixed)
   - Ghost positions (removed)

4. **Stop Adjustments** (both routines; the evening routine only while the market is open)
   - Which stops were moved higher
   - New stop prices and modification results

5. **Alerts** (both routines)
   - Positions approaching stop loss
   - Positions approaching take profit (only positions with a target price)
   - Unusual P&L movements

6. **Open Notices** (both routines, when there are any)
   - Number, severity, title and when first seen; resolved ones are marked `resolved`
   - The latest 10; `notices` in the scheduler CLI lists them all

**Note**: Stop and target prices are extracted from your actual GTC orders on Alpaca (bracket legs included). The routines re-place missing stops but not take-profit orders: a GTC take-profit lapses after Alpaca's 90-day limit, so a position held that long shows no target and exits by its trailing stop.

### Other Commands

#### `help`

Show command list (same as welcome message)

#### `exit` / `quit` / `q`

Exit scheduler CLI and return to main CLI

## Configuration Fields

Editable via `edit` command:

| Field | Type | Description | Example |
|-------|------|-------------|---------|
| `enabled` | boolean | Enable/disable scheduler | `true` |
| `morning_routine_time` | time | Morning execution time (ET) | `09:20:00` |
| `evening_routine_time` | time | Evening execution time (ET) | `15:50:00` |
| `max_retries` | integer (1-10) | Retry attempts on failure | `3` |
| `dry_run` | boolean | Routines send no orders and change no saved state; reports show what they would do. While on, stops are not maintained | `false` |

**Read-only fields** (edit config file directly):

- `market_timezone`: Always `America/New_York`
- `retry_delay_seconds`: Initial retry delay (exponential backoff)
- `run_due_timeout_seconds`: Wall-clock limit for one cron check, retries included (default 900)
- `enable_notifications`: Not used yet (reserved for email). Local [notices](#notices) are always on

## Use Cases

### Quick Status Check

```bash
python main.py
> /schedule
Scheduler> status
Scheduler> next
Scheduler> exit
```

### Change Execution Times

```bash
Scheduler> edit
# Change morning time from 09:20 to 09:15
# Save and exit
```

### Troubleshoot Failed Execution

```bash
Scheduler> notices
# What the loop flagged: failures, missed or stuck runs, positions without a stop
Scheduler> history
# Check error messages
Scheduler> test morning
# Test routine to reproduce issue
```

### Enable/Disable Quickly

```bash
# Disable for maintenance
Scheduler> disable

# Re-enable after maintenance
Scheduler> enable
```

## Best Practices

1. **Always test after editing config**

   ```text
   Scheduler> edit
   # Make changes
   Scheduler> test morning dry
   Scheduler> test evening dry
   ```

2. **Check history before troubleshooting**

   ```text
   Scheduler> history
   # Look for patterns in failures
   ```

3. **Remember that tests without `dry` are real runs**

   `test morning` and `test evening` place, renew and raise stops on the broker, as the
   scheduled routines do. Add `dry` to see what they would do without sending anything.

4. **Monitor next run before leaving**

   ```text
   Scheduler> next
   # Confirm expected time before exiting
   ```

## Integration with Main CLI

The scheduler CLI is fully integrated:

- Shares same `DailyScheduler` instance
- Changes take effect immediately
- No restart needed for config changes
- Seamlessly return to main CLI with `exit`

**Workflow:**

```text
Main CLI> /schedule
# Enter scheduler mode
Scheduler> edit
# Make changes
Scheduler> exit
# Return to main CLI
Main CLI> show portfolio
# Continue trading
```

## Limitations

- systemd or cron setup is done outside the CLI (`scripts/deployment/`)
- Execution history limited to 7 days (configurable in future)

## Troubleshooting

### Config Editor Error: "yaml referenced before assignment"

**Problem**: `UnboundLocalError: local variable 'yaml' referenced before assignment` when running `edit` command.

**Fix**: Updated in recent version. If you still see this error, ensure you're on the latest code (post-2025-11-11).

**Root Cause**: Redundant `import yaml` statement inside function conflicted with module-level import.

### Reports Show $0.00 for Stop/Target Prices

**Problem**: Morning/evening reports show `$0.00` in Stop column despite take-profit orders existing on Alpaca.

**Example Report**:

```text
| Symbol | Entry | Current | Stop | Target | P&L | Action |
| META | $628.07 | $627.00 | $0.00 | $634.19 | $-9 | No change |
| SPY | $657.60 | $683.00 | $0.00 | $712.80 | +$356 | No change |
```

> **Resolved:** broker state now reads open orders with `nested=True`, so bracket stop-loss legs (including "held" ones) come back under their parent, and the routines check each position's live broker stop and re-place a missing one (#586). The history below is kept for reference.

**Root Cause** (2025-11-11): Alpaca's API does NOT return bracket order legs with status "held" (stop-loss orders).

According to [Alpaca forum discussion](https://forum.alpaca.markets/t/half-of-bracket-order-held/2727/5):
> "When a bracket order is submitted, once the entry order is filled, two exit orders are submitted. **Only one of those two orders will be active at a time**. The other will have a status of 'held'."

**The Problem**:

- Alpaca implements OCO (One-Cancels-Other) with one active exit order and one "held" order
- `get_orders()` API (even with `nested=True`) **does not return orders with status="held"**
- Stop-loss orders exist on Alpaca but are invisible to the API
- Attempts to fetch via `get_order_by_id()` also failed - leg IDs return empty

**Pragmatic Solution Implemented** (2025-11-11):

Since Alpaca hides stop orders from the API, the system uses a **"carbon copy" approach**:

1. **Priority 1: Use saved stop** from local state (the actual value sent when placing order)

   ```python
   # Try to use saved stop from local state first
   if symbol in self.local_state.get("positions", {}):
       saved_stop = self.local_state["positions"][symbol].get("stop_price")
   ```

2. **Priority 2: Calculate from entry** if no saved value exists

   ```python
   # Last resort: calculate from entry price × (1 - stop_loss_pct)
   calculated_stop = round(entry_price * 0.95, 2)
   ```

3. **Extract take-profit** prices from API (visible as active LIMIT orders)

4. **Display warning** in reports:

   ```markdown
   ⚠️ **Note**: Stop prices from local state (Alpaca hides bracket order stop-loss legs from API).
   Verify stop orders exist on Alpaca dashboard. See Issue #355 for details.
   ```

**Manual Sync Required**: For AUTO_DISCOVERED positions (not placed by this system), manually update `state/cost_efficient_positions.json` with actual stop prices from Alpaca dashboard.

**Report Example**:

```text
| Symbol | Entry | Current | Stop | Target | P&L | Action |
| META | $628.07 | $627.00 | $609.00 | $634.19 | $-9 | No change |
| SPY | $657.60 | $683.00 | $627.00 | $712.80 | +$356 | No change |

⚠️ Note: Stop prices from local state (Alpaca hides bracket order stop-loss legs).
```

**User Responsibility**:

1. Verify stop orders exist on Alpaca web dashboard
2. For AUTO_DISCOVERED positions, manually update stop prices in `state/cost_efficient_positions.json`
3. For new orders placed by this system, stop/target will be saved automatically (when Order Manager integration complete)

**Future Enhancement**: Issue #353 proposes automatically saving stop/target when placing orders via Order Manager integration (requires SQLite #336 for proper persistence).

### Report Filenames with Timestamps

**Old Format** (before 2025-11-11): `20251111_1339_morning.md` (hard to read)

**New Format**: `2025-11-11_morning.md` (human-readable)

- Uses ISO date format with dashes
- Multiple runs same day: `morning_2.md`, `morning_3.md`
- Applies to morning, evening, and recovery reports

### Daemon Not Running Despite "Config: ENABLED"

**Problem**: `status` shows `Config: ENABLED` but scheduler not executing automatically.

**Solution**: "Config: ENABLED" means the flags in force say `enabled`, but something still has to
run the scheduler. Add the cron entry (recommended), or start the daemon:

```bash
# Recommended: cron entry, every 5 minutes
./scripts/deployment/setup_cron.sh

# Alternative: daemon in the background
python main.py --daemon
```

The `status` command shows both:

- **Config status** (the flags in force: saved flags, else the YAML file)
- **Service status** (`CRON`, `RUNNING` for the daemon, or `NOT RUNNING`)

## Future Enhancements

See pending issues for:

- Self-terminating background processes
- Real-time execution monitoring
- Custom routine scheduling
- Email/webhook notifications

---

**Related Documentation:**

- [GTC Scheduler Quickstart](02_gtc_scheduler_quickstart.md)
- [GTC Scheduler Technical](03_gtc_scheduler_technical.md)
- [Interactive CLI Test Plan](05_interactive_cli_test_plan.md)
