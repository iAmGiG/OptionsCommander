# CLI Integration Status - New Features (#364, #395, #407, #483, #405)

> **Historical status (2025-12-22).** It names the voter agent, the scanner agent and `TradingOrchestrator` paths that were retired in #331 (`src/autogen_agents/` is gone). The signal is `VotingSignal` (`src/trading/instruments/voting_signal.py`); `MultiTimeframeVoter` and `scanner_config` are in `src/trading/instruments/`. Current commands are in [the command reference](../03_reference/03_commands.md).

**Last Updated**: 2025-12-22
**Scope**: Analysis of how new trading features integrate with CLI session
**Status**: CLI commands added, pipeline wiring in progress

---

## Executive Summary

CLI slash commands have been added for most features. **Next step is wiring commands to the trading pipeline** so they affect actual trade decisions.

| Feature | Implemented | Tested | CLI Exposed | Pipeline Wired | Notes |
|---------|:---:|:---:|:---:|:---:|---------|
| **#364 Ranked Voting** | ✅ | ✅ | ✅ /voter | ❌ #504 | Command works, needs pipeline wire |
| **#395 Multi-Timeframe Voting** | ✅ | ✅ | ✅ /timeframe | ❌ #505 | Command works, needs pipeline wire |
| **#407 Custom Timeframe Builder** | ✅ | ✅ | ✅ /tf validate | ✅ | Validation works |
| **#405 Tiered Watchlist** | ✅ | ✅ | ⚠️ | ✅ | Needs /watchlist command #507 |
| **#483 DB Backup/Migration** | ✅ | ✅ | ✅ /backup | ✅ | Fully working |
| **#340 GTT Orders** | ✅ | ✅ | ⚠️ | ✅ | Needs /gtt command #506 |
| **Partial Exits** | ✅ | ✅ | ❌ | ✅ | Needs /partial command #508 |

## Recent Updates (2025-12-22)

- ✅ Added `/timeframe` command (#489) - presets, single, validate
- ✅ Added `/backup` command (#490) - list, create, restore, export
- ✅ Added `/voter` command (#488) - presets, promote, demote
- 🔜 #504 Wire ranked voting to RealVoterStrategy
- 🔜 #505 Wire multi-TF voter to pipeline
- 🔜 #506 Add /gtt slash command
- 🔜 #507 Add /watchlist slash command
- 🔜 #508 Add /partial slash command

---

## Current CLI Architecture

### Entry Point Flow

```text
main.py
  ├─→ trade_assist(account_id) [Line 144]
  │    ├─→ OrchestratorFactory.create()
  │    │    └─→ Creates TradingOrchestrator with RealVoterStrategy
  │    └─→ CLISession(orchestrator)
  │         └─→ Interactive REPL with /help, /status, etc.
  │
  └─→ Daemon mode (scheduler)
       └─→ DailyScheduler
```text

### RealVoterStrategy Integration

```text
CLISession.process_user_input()
  └─→ TradingOrchestrator.process_request()
       └─→ RealVoterStrategy.analyze()
            └─→ VoterAgent.evaluate_voting()  [CURRENT: Uses basic voting]
                 ├─→ Option A: evaluate_voting() [Line 118 - Current]
                 └─→ Option B: evaluate_ranked_voting() [NOT USED]
```text

**Key Finding**: CLI uses `voter.evaluate_voting()` which is the original voting logic. The new ranked voting system (`evaluate_ranked_voting()`) exists but isn't being called.

---

## Feature-by-Feature Integration Status

### 1. Ranked Voting System (#364)

**What Exists**:

- `src/core/ranked_voter_config.py` - RankedVoterManager with YAML+SQLite persistence
- `src/trading/instruments/indicator_registry.py` - Pluggable indicator system
- `config_defaults/voters_config.yaml` - Configuration with presets
- `src/autogen_agents/agents/voter_agent.py:evaluate_ranked_voting()` - Implementation

**How to Use Programmatically**:

```python
from src.core.ranked_voter_config import get_ranked_voter_manager
from src.autogen_agents.agents.voter_agent import VoterAgent

# Get manager
voter_mgr = get_ranked_voter_manager()

# Apply preset
voter_mgr.apply_preset("macd_primary")

# Get active voters
active = voter_mgr.get_active_voters()

# Use in VoterAgent
voter = VoterAgent()
result = voter.evaluate_ranked_voting(symbol, price_data)
```text

**CLI Status**: ❌ **NOT EXPOSED**

- No CLI commands to switch voting modes
- No `/set voter preset` or similar commands
- No config reload mechanism in CLI session

**Config Status**: ✅ **LOADED** (but not used)

- `voters_config.yaml` exists with voter definitions
- RankedVoterManager can load and persist to SQLite
- Presets available: default, macd_primary, rsi_primary

**Recommendation**: Add `/voter` command group to CLI:

```bash
> /voter list              # Show active/review voters
> /voter preset default    # Switch preset
> /voter promote MACD      # Promote to active
> /voter demote RSI        # Move to review
```text

---

### 2. Multi-Timeframe Voting (#395)

**What Exists**:

- `src/autogen_agents/agents/multi_timeframe_voter.py` - MultiTimeframeVoter class
- `config_defaults/voters_config.yaml:multi_timeframe` section with 4 presets:
  - `trend_following` (50% 1d, 30% 4h, 20% 1h)
  - `intraday` (50% 1h, 30% 15m, 20% 5m)
  - `position` (40% 1w, 40% 1d, 20% 4h)
  - `scalping` (60% 5m, 30% 1m, 10% 30s)
- Singleton accessor: `get_multi_timeframe_voter()`

**How to Use Programmatically**:

```python
from src.autogen_agents.agents.multi_timeframe_voter import get_multi_timeframe_voter

# Get voter with preset
voter = get_multi_timeframe_voter(preset="trend_following")

# Evaluate multi-timeframe
result = await voter.evaluate_multi_timeframe(symbol, price_data)

# Switch preset
voter.set_preset("intraday")
```text

**CLI Status**: ❌ **NOT EXPOSED**

- No CLI commands for multi-timeframe voting
- No way to enable/disable multi-timeframe analysis
- No preset selection in interactive CLI

**Config Status**: ✅ **DEFINED** (but not loaded in CLI)

- Config section exists in `voters_config.yaml`
- Default preset should be determined by trading mode

**Recommendation**: Integrate with existing timeframe management:

```bash
> /timeframe set multi 1h,4h,1d            # Multi-timeframe mode
> /timeframe preset trend_following        # Use preset
> /timeframe single 1d                      # Back to single
> set mode trend_following                  # Auto-set multi-tf preset
```text

---

### 3. Custom Timeframe Builder (#407)

**What Exists**:

- `src/trading/instruments/custom_timeframe.py` - Full implementation
- `TimeframeParser` - Parses: 65m, 1.5h, 2d, 2w, etc.
- `CustomTimeframeBuilder` - Aggregates data from lower resolutions
- Exported from `src/trading/instruments/__init__.py`
- Convenience functions: `build_custom_bars()`, `validate_timeframe()`

**How to Use Programmatically**:

```python
from src.trading.instruments.custom_timeframe import (
    build_custom_bars,
    validate_timeframe,
    get_custom_timeframe_builder
)

# Validate
if validate_timeframe("65m"):
    # Build
    df = build_custom_bars("AAPL", "65m", "2024-01-01", "2024-12-31")

# Or use builder directly
builder = get_custom_timeframe_builder()
df = builder.build_custom_bars("SPY", "1.5h", "2024-01-01", "2024-12-31")
```text

**CLI Status**: ⚠️ **PARTIALLY EXPOSED**

- TimeframeParser can validate any notation
- But custom timeframe **creation** requires backend processing
- Native Alpaca timeframes work via existing `/timeframe set` command
- Custom timeframes (65m, 1.5h, 2d) would need async data fetching

**Config Status**: ⚠️ **MINIMAL**

- No config file yet (could add to timeframe_config.yaml)
- Parser has hardcoded native list and validation rules

**Recommendation**: Add custom timeframe CLI command:

```bash
> /timeframe build 65m                     # Build 65-minute bars
> /timeframe build 1.5h 2024-01-01         # Build from specific date
> /timeframe validate 89m                  # Check if valid
> /timeframe info 2d                       # Get info (type, minutes, etc)
```text

This would require async processing in CLI session to fetch and aggregate data.

---

### 4. Tiered Watchlist (#405)

**Status**: ✅ **WORKING** (bug was fixed)

**What Exists**:

- `src/autogen_agents/agents/scanner_agent.py` - Already fully implemented
- 4-tier priority system:
  1. Positions (existing holdings)
  2. Pending Orders (unfilled orders)
  3. Strategy-based (algorithm picks)
  4. Discovery (new opportunities)
- Config in `config_defaults/scanner_config.yaml`:

  ```yaml
  tiered_watchlist:
    tier_limits:
      positions: 20
      pending_orders: 50
      strategy: 100
      discovery: 200
  ```

**How It's Used**:

- ScannerAgent reads watchlist on startup
- Loads trading modes from `trading_modes.yaml`
- Each mode specifies `watchlist_strategy` (balanced, momentum, wheel_strategy)
- Watchlist updates during trading cycle

**CLI Status**: ✅ **WORKING**

- `/portfolio` shows current positions (tier 1)
- `/orders` shows pending orders (tier 2)
- Strategies automatically filter symbols based on mode

**Fix Applied**: Changed CONFIG_DIR from 3 levels to 4 levels in scanner_agent.py so it can find `config_defaults/` folder

**Recommendation**: May want to expose tier management:

```bash
> /watchlist show                          # Show all tiers
> /watchlist tier positions                # Show tier 1
> /watchlist limits                        # Show tier limits
> /watchlist refresh                       # Rescan market
```text

---

### 5. Database Backup/Migration (#483)

**What Exists**:

- `src/utils/db_backup.py` - DBBackupManager (~750 lines)
- Features: backup/restore, JSON export/import, cleanup old data
- Supports: `state/user.db`, `.cache/trading_data.db`
- Schema versioning via DBMigrator

**How to Use Programmatically**:

```python
from src.utils.db_backup import DBBackupManager

manager = DBBackupManager()

# Backup database
result = manager.backup_database("state/user.db")

# Export table to JSON
manager.export_table("state/user.db", "voter_ranking_history",
                     output_path="backups/voter_history.json")

# Import from backup
manager.import_table("backups/backup_20241215.db",
                     "state/user.db", "trader_state")

# Cleanup old data (keep last 90 days)
cleaned = manager.cleanup_old_data("state/user.db", days=90)
```text

**CLI Status**: ❌ **NOT EXPOSED**

- No `/backup`, `/restore`, or `/export` commands
- Manual Python script required for DB operations
- Perfect for daemon mode scheduled tasks

**Config Status**: N/A

- No configuration needed (uses defaults)

**Recommendation**: Add backup CLI commands:

```bash
> /backup database                         # Backup state/user.db
> /backup list                             # Show available backups
> /restore backup_20241215.db              # Restore specific backup
> /export voters voters_20241215.json      # Export table to JSON
```text

Or create scheduled task in daemon mode:

```yaml
# config/scheduler_config.yaml
schedules:
  - name: daily_backup
    time: "22:00"
    action: backup_database
```text

---

## Configuration Loading Pipeline

### Current Status

```text
main.py (entry)
  └─→ OrchestratorFactory.create()
       ├─→ RealVoterStrategy()
       │    └─→ VoterAgent(use_config_file=True)
       │         ├─→ TradingConfig()
       │         │    ├─→ trading_config.yaml  ✅
       │         │    └─→ MACD/RSI params loaded
       │         └─→ voters_config.yaml NOT YET LOADED ❌
       │
       ├─→ get_mode_manager()
       │    └─→ trading_modes.yaml loaded  ✅
       │         └─→ watchlist_strategy per mode
       │
       └─→ ScannerAgent()
            └─→ scanner_config.yaml loaded  ✅
                 └─→ tiered_watchlist
```text

### What's Missing

1. **voters_config.yaml not loaded in CLI init** - File exists but RankedVoterManager only loads if explicitly called
2. **Multi-timeframe preset not selected** - Should default based on trading mode
3. **CustomTimeframeBuilder not available in CLI** - Would need async task system
4. **DB backup not scheduled** - No daemon mode integration

---

## Integration Checklist

### Phase 1: Minor CLI Additions (Recommended)

**GitHub Issues Created:**

- #488 [CLI] Add /voter command group for ranked voting management
- #489 [CLI] Enhance /timeframe commands for multi-timeframe voting presets
- #490 [CLI] Add /backup command group for database management

**Checklist:**

- [ ] Add `/voter` command group for ranked voter management (#488)
  - [ ] `/voter list` - Show active/review voters
  - [ ] `/voter preset <name>` - Switch preset
  - [ ] `/voter promote <name>` - Promote voter

- [ ] Enhance `/timeframe` command for multi-timeframe mode (#489)
  - [ ] `/timeframe preset <preset>` - Use multi-tf preset
  - [ ] `/timeframe single <tf>` - Back to single timeframe
  - [ ] `/timeframe validate <tf>` - Check if valid

- [ ] Load voters_config.yaml on CLI startup (#488)
  - [ ] Call `get_ranked_voter_manager()` in OrchestratorFactory
  - [ ] Apply default preset from config

### Phase 2: Medium Effort Additions

- [ ] Add `/backup` command group for DB management (#490)
  - [ ] `/backup database` - Create backup
  - [ ] `/backup list` - Show available backups
  - [ ] `/backup restore <name>` - Restore from backup
  - [ ] `/export <table> [output]` - Export to JSON

- [ ] Create custom timeframe CLI command
  - [ ] `/timeframe build <spec> [start_date]` - Build custom bars
  - [ ] Requires async data fetching + aggregation
  - [ ] Persist resulting timeframe in cache

- [ ] Integrate multi-timeframe voting into RealVoterStrategy
  - [ ] Detect mode → determine multi-tf preset
  - [ ] Use MultiTimeframeVoter.evaluate_multi_timeframe() instead of single-tf
  - [ ] Blend results with confidence weighting

### Phase 3: Advanced Integration

- [ ] Auto-apply multi-timeframe voting based on trading mode
  - [ ] conservative → position preset (1w/1d/4h)
  - [ ] moderate → trend_following preset (1d/4h/1h)
  - [ ] aggressive → intraday preset (1h/15m/5m)

- [ ] Schedule DB backups in daemon mode
  - [ ] Daily backup at end of trading day
  - [ ] Weekly export of analysis history
  - [ ] Monthly cleanup of old data

- [ ] Add "advanced analysis" option in CLI
  - [ ] Show ranked voter consensus
  - [ ] Display multi-timeframe votes by timeframe
  - [ ] Explain why signal changed

---

## Testing Commands

To verify features work outside CLI:

```bash
# Test ranked voting
python -c "
from src.core.ranked_voter_config import get_ranked_voter_manager
from src.autogen_agents.agents.voter_agent import VoterAgent

manager = get_ranked_voter_manager()
manager.apply_preset('macd_primary')
voters = manager.get_active_voters()
print(f'Active voters: {[v.name for v in voters]}')
"

# Test multi-timeframe voting
python -c "
from src.autogen_agents.agents.multi_timeframe_voter import get_multi_timeframe_voter
voter = get_multi_timeframe_voter(preset='trend_following')
print(f'Preset: {voter.preset_name}')
print(f'Timeframes: {list(voter.config.keys())}')
"

# Test custom timeframe builder
python -c "
from src.trading.instruments.custom_timeframe import validate_timeframe
tests = ['65m', '1.5h', '2d', '2w', 'invalid', '13m']
for tf in tests:
    valid = validate_timeframe(tf)
    print(f'{tf:10} -> {\"VALID\" if valid else \"INVALID\"}')"

# Test DB backup
python -c "
from src.utils.db_backup import DBBackupManager
manager = DBBackupManager()
result = manager.backup_database('state/user.db')
print(f'Backup: {result.success} ({result.backup_path})')
"
```text

---

## Recommendations Summary

### Immediate (Next Sprint)

1. ✅ Features are implemented and tested
2. ⚠️ Add Phase 1 CLI commands for ranked voting (/voter group)
3. ⚠️ Load voters_config.yaml on CLI initialization
4. ✅ Tiered watchlist already works (bug was fixed)

### Medium Term

1. Integrate multi-timeframe voting into RealVoterStrategy based on mode
2. Add custom timeframe builder CLI command
3. Expose DB backup/restore in CLI

### Long Term

1. Auto-enable multi-timeframe voting based on trading mode
2. Schedule DB backups in daemon mode
3. Add advanced analysis display showing voter consensus

---

## Files to Update

To complete Phase 1 integration:

| File | Change | Effort |
|------|--------|--------|
| `src/cli/commands/voter_commands.py` | NEW: Add /voter command group | 30 min |
| `src/cli/commands/timeframe_commands.py` | ENHANCE: Add multi-tf and build support | 45 min |
| `src/core/factory.py` | MODIFY: Load voters_config on init | 15 min |
| `config_defaults/voters_config.yaml` | Already complete | N/A |
| `src/strategies/real_voter_strategy.py` | ENHANCE: Call evaluate_ranked_voting optionally | 20 min |

**Total Effort**: ~2 hours for Phase 1 CLI integration

---

## See Also

- VoterAgent Implementation
- [Ranked Voter Config](../../src/core/ranked_voter_config.py)
- Multi-Timeframe Voter
- [Custom Timeframe Builder](../../src/trading/instruments/custom_timeframe.py)
- [DB Backup Manager](../../src/utils/db_backup.py)
