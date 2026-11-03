# Position Management Enhancements (Issue #306)

## Overview

This document describes the enhancements made to the position management system to provide better exit alerts and dynamic stop integration for live trading monitoring.

User-set price alerts and portfolio overrides were removed in #657 (#331). For a price alert on a held symbol use `/gtt create` or `/gtt oco`; GTT triggers are checked at the morning and evening routines only, and only for symbols you hold.

## Enhancements Summary

### 1. Exit Alert System

**Location**: `src/trading/positions/position_tracker.py`

#### New Alert Types

Distances are fractions of the current price: the fall (or rise) from here that reaches the level.

- `APPROACHING_STOP_LOSS` - Price within 2% above the stop
- `LOSS_THRESHOLD_REACHED` - Price at or below the stop while the position is still open (the stop didn't fill)
- `APPROACHING_TAKE_PROFIT` - Price within 2% below the target (only for positions with a target)
- `PROFIT_TARGET_REACHED` - Price at or above the target while the position is still open
- `STOP_ADJUSTED` - Stop loss order has been adjusted

#### Alert Severity Levels

- **INFO** (📊) - Informational alerts
- **WARNING** (⚠️) - Warning alerts (approaching exit levels)
- **CRITICAL** (🚨) - Critical alerts (exit levels reached)

#### Alert Features

1. **Alert History Tracking** - Each position keeps its latest 50 alerts
2. **Alert Cooldown** - Limits how often an alert is added to the history and logged (default: 5 minutes); every check still reports the current condition
3. **Formatted Messages** - Human-readable alert messages with emojis for quick scanning
4. **Alert Summary** - Portfolio-wide alert summaries with counts by severity

#### How Alerts Are Triggered

**Alert Destination:** Console/terminal output, log files, and daily reports only. No email, SMS, or webhooks in this implementation.

**Manual Triggering:**

```bash
# Check alerts from the interactive CLI
python main.py
> check my alerts
```

**Automatic Triggering (Daily Scheduler, Issue #287):**
Alerts run automatically in each routine:

- **9:20 AM ET** - Morning routine checks positions
- **3:50 PM ET** - Evening routine checks positions

Every open long position with an entry and a stop is checked (#599,
`StateReconciler.check_position_alerts`), after the stop raise, against the stop it ends the run
with. Take-profit alerts need a target; the live positions exit by trailing stop only and get stop
alerts. An alert for a position with no open sell stop says "no live stop order".

The tracker keeps one entry per open position, keyed by its symbol and synced to the live levels each
run; entries for positions no longer held are dropped. (Before #599, each run saved a new entry under a
timestamp key that was never looked up, so no alert was ever raised. The first save after the fix copies
the old state file to `state/backups/cost_efficient_positions.<UTC time>.pre_tracker_prune.json`.)

Deployment options:

```bash
# Option A (recommended): crontab (the scheduler decides what is due, in ET, on trading days)
*/5 * * * * cd /path/to/repo && /path/to/python main.py --run-due

# Option B: systemd daemon (runs continuously)
sudo systemctl start autogen-trading-scheduler
```

#### Example Usage (Programmatic)

```python
from src.trading.positions.position_tracker import PositionTracker

# Initialize tracker with custom cooldown
tracker = PositionTracker(
    take_profit_pct=0.08,
    stop_loss_pct=0.05,
    alert_cooldown_seconds=300  # 5 minutes between repeated alerts
)

# Track a position at its live levels (no target: trailing-stop-only exits)
position = tracker.sync_position("TQQQ", entry_price=50.0, quantity=100, stop_loss_price=52.0)

# The current condition, or None: here 52.80 is 1.52% above the stop
result = tracker.check_exit_conditions(position.position_id, current_price=52.8)
print(result["severity"], result["message"])  # WARNING ⚠️ TQQQ is 1.52% above its stop $52.00 ...

# Get alert summary
summary = tracker.get_alert_summary()
print(f"Total alerts: {summary['counts']['total']}")
print(f"Critical: {summary['counts']['critical']}")

# Get position-specific alerts
alerts = tracker.get_position_alerts(position.position_id)
for alert in alerts:
    print(alert.format_message())
```

#### Alert Output Locations

**1. Log Files (Persistent):** an alert added to a position's history is logged at its level.

```text
# state/scheduler.log
2026-10-27 13:20:06,180 - src.trading.positions.position_tracker - WARNING - ⚠️ SPY is 0.74% above its stop $744.47 (price $750.00)
```

**2. Daily Reports (Markdown):** every alert found by the routine, in its own section.

```markdown
# reports/daily/2026-10-27_morning.md

## Position Alerts
Total Alerts: 1 (0 Critical, 1 Warning, 0 Info)

⚠️ SPY is 0.74% above its stop $744.47 (price $750.00)
```

**3. CLI:** "check my alerts" runs the same check against an in-memory reconcile (nothing is saved).

```text
🔔 Position Alerts (1)

🟡 SPY: near stop @ $750.00
   • level $744.47, 0.74% away
```

**4. Saved State:** each position's alert history (latest 50) is saved with the tracker in
`state/cost_efficient_positions.json` (`position_tracker_state`).

### 2. Dynamic Stop Integration

**Location**: `src/trading/scheduling/trading_cycle.py`, `src/trading/state/state_reconciler.py`, `src/trading/orders/trailing_stop_manager.py`

#### Enhanced Stop Adjustment Logic

The progressive stop adjustment system now includes detailed logging at every step. Stops only move up; by profit since entry:

1. **Under 2% Profit** → No change
2. **2-4% Profit** → Move stop to breakeven
3. **4-6% Profit** → Lock in 25% of gains
4. **6-10% Profit** → Lock in 50% of gains
5. **10%+ Profit** → Lock in 75% of gains

The level is capped 1% below the current price and starts from the broker's live stop.

#### Logging Enhancements

- **DEBUG Level**: Detailed order IDs, price calculations, adjustment decisions
- **INFO Level**: Stop adjustment recommendations, execution results
- **WARNING Level**: Missing stop orders, failed modifications

#### Example Log Output

```text
INFO: TQQQ: Moving stop to breakeven at 3.2% profit
INFO: TQQQ: Stop adjustment - $47.50 → $50.00 (Move to breakeven (3.2% profit))
INFO: Found 1 stop adjustments needed
INFO: [1/1] Modifying TQQQ stop order...
INFO: ✅ TQQQ: Stop adjusted to $50.00 (locking +$0.00 profit)
INFO: Stop adjustment batch complete: 1/1 successful, 0 errors
```

### 3. Trading Cycle Integration

**Location**: `src/trading/scheduling/trading_cycle.py`, `main.py`

#### New Workflow Steps

The trading cycle now includes alert checking:

1. Fetch broker state and record the day (account, positions, fills)
2. Reconcile local state, and re-place missing or expiring stops
3. Check GTT triggers
4. Raise stops to their trailing level (evening: only while the market is open)
5. **Check position alerts**, against the stops after the raise (#599). Errors here are reported
   in the report and never fail the routine.
6. Save local state (with the tracker) and generate the report with alerts

#### Report Format

Reports now include a Position Alerts section:

```markdown
## Position Alerts

Total Alerts: 2 (1 Critical, 1 Warning, 0 Info)

⚠️ TQQQ is 1.52% above its stop $52.00 (price $52.80)
🚨 SPY is at or below its stop $744.47 (price $740.00), still open - no live stop order
```

### 4. Main Trading Interface

**Location**: `src/cli/tools/alert_tools.py`

"check my alerts" in the CLI shows the current alerts (see Alert Output Locations above). A
breached level reads, for example:

```text
🔴 SPY: stop breached, position still open @ $740.00
   • level $744.47, 0.60% through it
   • no live stop order
```

## Configuration

### Position Tracker Configuration

```python
tracker = PositionTracker(
    take_profit_pct=0.08,           # 8% take profit (create_position defaults)
    stop_loss_pct=0.05,             # 5% stop loss (create_position defaults)
    alert_cooldown_seconds=300,     # 5 minutes between history entries
    warning_pct=0.02,               # alert within 2% of a level, measured from the price
)
```

### Stop Adjustment Thresholds

These are the `TrailingStopConfig` defaults (`config_defaults/trading_config.py`, `climb_rate: medium`); a `trailing_stops` section in `trading_config.yaml` overrides them:

- **Minimum profit for adjustment**: 2%
- **Breakeven threshold**: 2-4% profit
- **Lock 25% threshold**: 4-6% profit
- **Lock 50% threshold**: 6-10% profit
- **Lock 75% threshold**: 10%+ profit
- **Cap**: at least 1% below the current price
- **Minimum stop movement**: $0.01

## API Reference

### PositionTracker Class

#### Methods

- `sync_position(ticker, entry_price, quantity, stop_loss_price, take_profit_price=None)` - Create or update the entry for an open position (key: the symbol) with its live levels
- `prune(open_tickers)` - Drop entries for positions no longer held
- `create_position(ticker, entry_price, quantity)` - Create a position at the default levels
- `check_exit_conditions(position_id, current_price)` - The current condition (near or through the stop or target) or None; the cooldown only limits the history
- `get_all_alerts(since=None)` - Get all alerts (optionally filtered)
- `get_position_alerts(position_id)` - Get alerts for specific position
- `get_alert_summary()` - Get portfolio-wide alert summary
- `get_position_summary(current_prices)` - Get position summary with P&L

### CostEfficientTradeCycle Class

#### New Methods

- `check_position_alerts(broker_state)` - Check all positions for alerts
- Returns `List[PositionAlertSummary]`; the routines keep the latest run's in `last_alerts`

#### Enhanced Methods

- `generate_routine_report()` - Now includes alerts parameter
- `morning_routine()` - Includes alert checking
- `evening_routine()` - Includes alert checking

## Testing

```bash
python -m pytest tests/unit/trading/test_position_alerts.py tests/unit/trading/test_local_state_backup.py tests/unit/cli/test_alert_tools.py -v
```

Test coverage (#599):

- ✅ Stop alerts for positions without a target; target alerts only with a target
- ✅ Distances measured from the price; breaches are CRITICAL and say when no stop order is open
- ✅ One tracker entry per open position, refreshed every run; old and closed entries dropped
- ✅ Alerts measured after the stop raise; a failing check is reported, not retried
- ✅ The cooldown limits history only; the history is capped
- ✅ The state file backup before the first prune, and atomic saves
- ✅ The CLI alert tools

## Alert Execution Flow

### Step-by-Step Process

```text
1. Trigger Event
   ├─> Manual: "check my alerts" in the CLI
   └─> Auto: Daily scheduler at 9:20 AM / 3:50 PM ET (Issue #287)

2. Trading Cycle Execution
   └─> morning_routine() / evening_routine()

3. Broker State Fetch (1 API call)
   └─> Get all positions with current prices

4. Alert Check (after the stop raise)
   For each open long position with an entry and a stop:
   ├─> Sync the tracker entry to the live stop (and target, if any)
   ├─> At or below the stop → LOSS_THRESHOLD_REACHED (CRITICAL)
   ├─> Within 2% above the stop → APPROACHING_SL (WARNING)
   ├─> With a target: at or above it → PROFIT_TARGET_REACHED (CRITICAL)
   └─> With a target: within 2% below it → APPROACHING_TP (WARNING)
   Then drop tracker entries for positions no longer held.

5. Alert History (Anti-Spam)
   ├─> Has 5+ minutes passed since the last history entry for this position?
   ├─> Yes: Add to history, log it, update last_alert_time
   └─> No: Not added (the routine's report still lists the alert)

6. Alert Storage & Display
   ├─> Add to position.alert_history[]
   ├─> Log to console (INFO/WARNING/CRITICAL level)
   ├─> Write to state/scheduler.log (scheduler runs)
   └─> Include in daily report markdown

7. Continue Trading Cycle
   └─> Calculate stop adjustments, execute modifications, etc.
```

### Alert Frequency

**Manual:**

- "check my alerts" in the CLI
- On-demand basis

**Daily Scheduler (Issue #287):**

- Twice daily automatically (9:20 AM, 3:50 PM ET)
- Plus manual runs anytime
- Alert history persists across runs

### Alert Cooldown Behavior

**Problem:** Stock price fluctuates near threshold

```text
09:30:00 - Price: $52.80 (1.52% above the stop) → ⚠️ reported, added to history
09:30:30 - Price: $52.70 (1.33% above the stop) → ⚠️ reported, not added (cooldown)
09:35:01 - Price: $52.60 (1.14% above the stop) → ⚠️ reported, added to history (5 min elapsed)
```

**Result:** Every check reports the current condition; the history and log get at most one entry
per position every 5 minutes. The routines run twice a day, so for them the cooldown rarely matters.

## Benefits for Live Trading

1. **Early Warning System** - Get notified before positions reach exit levels
2. **No Alert Spam** - Cooldown prevents repeated alerts for the same condition
3. **Complete History** - Track all alerts for post-trade analysis
4. **Clear Visibility** - Human-readable messages with severity indicators
5. **Enhanced Logging** - Detailed logs for debugging stop adjustments
6. **Portfolio Overview** - See all alerts across positions at a glance

## Limitations & Future Enhancements

### Current Limitations

**❌ No External Notifications:**

- Alerts are **console/log output only**
- No email, SMS, or push notifications
- Must actively check logs or run system to see alerts
- If daemon crashes, alerts are not sent

**⚠️ Twice-Daily Checks Only (with GTC):**

- Alerts checked at 9:20 AM and 3:50 PM ET only
- Positions could reach exit levels between checks without alert
- Not real-time monitoring (by design for API efficiency)

**⚠️ Manual Monitoring Required:**

- You must view console output or check log files
- No proactive notification mechanism
- Alert history in memory is lost on restart (unless persisted)

### Recommended Workflow

**For Active Monitoring:**

```bash
# Check alerts ad-hoc during the trading day
python main.py      # then: check my alerts

# View recent logs
tail -f state/scheduler.log

# Check daily reports
cat reports/daily/$(TZ=America/New_York date +%Y-%m-%d)_morning.md
```

**For Automated Monitoring (with GTC #287):**

```bash
# Recommended: cron entry (see 02_gtc_scheduler_quickstart.md)
crontab -l | grep "main.py --run-due"

# Monitor logs in real-time (cron and daemon runs both write here)
tail -f state/scheduler.log

# Cron errors
cat state/scheduler-cron.err
```

### Future Enhancements

Potential improvements for future iterations:

1. **Email/SMS Notifications** - Send critical alerts via external channels (Twilio, SendGrid)
2. **Alert Webhooks** - Integrate with external monitoring systems (PagerDuty, Slack)
3. **Custom Alert Rules** - User-defined alert conditions beyond 2% threshold
4. **Alert Analytics** - Statistical analysis of alert patterns
5. **Mobile App Integration** - Push notifications to mobile devices
6. **Real-Time WebSocket Alerts** - Continuous monitoring instead of scheduled checks
7. **Alert Dashboard** - Web UI for viewing alert history and current status

## Changelog

### Issue #599 (Oct 2026)

- Positions without a target get stop alerts; distances are measured from the price
- Breaches (price through a level with the position still open) are reported as CRITICAL
- One tracker entry per open position (the old timestamp keys were never looked up, so no alert
  was raised); stale entries dropped after a one-time backup of the state file
- Alerts are checked once per routine, after the stop raise; atomic state saves

### v1.0.0 (Issue #306)

- ✅ Added comprehensive exit alert system
- ✅ Enhanced position tracking with alert history
- ✅ Integrated alerts into trading cycle
- ✅ Added detailed logging for stop adjustments
- ✅ Updated main.py to display alerts
- ✅ Created test suite for alert functionality
- ✅ Documentation complete

## Related Files

- `src/trading/positions/position_tracker.py` - Core alert system
- `src/trading/scheduling/trading_cycle.py` - Trading cycle integration
- `src/trading/positions/position_manager.py` - Position management
- `main.py` - Main trading interface

## References

- Issue #306: Position Management Enhancements
- Issue #321: Dynamic trailing stop logic
- [Project status](../04_development/02_project_status.md): System development roadmap
