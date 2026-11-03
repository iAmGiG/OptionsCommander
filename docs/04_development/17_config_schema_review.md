# Configuration & Schema Review - New Features

> **Historical review (2025-12-15).** Where it names `ScannerAgent` or `VoterAgent`, those were retired in #331; the configs are read by `VotingSignal` and the watchlist and scanner tools. See [the codebase structure](01_codebase_structure.md).

**Date**: 2025-12-15
**Scope**: YAML configs and DB schemas for #364, #395, #407, #483, #405
**Status**: ✅ All configs complete and properly structured

---

## Executive Summary

All configuration files are **in use** with comprehensive structure:

| Config File | Purpose | Status | Notes |
|-------------|---------|--------|-------|
| **voters_config.yaml** | Ranked voting + multi-TF | ✅ Complete | 195 lines, 2 sections |
| **scanner_config.yaml** | Tiered watchlist | ✅ Complete | Used by ScannerAgent |
| **trading_modes.yaml** | Risk modes + watchlist link | ✅ Complete | Links to scanner |
| **state/user.db** | Voter ranking persistence | ✅ Schema ready | 1 table, 5 columns |

---

## 1. voters_config.yaml (195 lines)

**Location**: `config_defaults/voters_config.yaml`

### Structure

```yaml
# Section 1: Ranked Voting System (#364)
active_voters: 2
indicators: {...}
default_ranking: [...]
voting: {...}
presets: {...}

# Section 2: Multi-Timeframe Voting (#395)
multi_timeframe:
  enabled: true
  default_preset: trend_following
  presets: {...}
  consensus: {...}
```text

### Section 1: Ranked Voting (#364)

#### Indicators (Lines 13-31)

```yaml
indicators:
  MACD:
    description: "Moving Average Convergence Divergence"
    default_params:
      fast: 13          # Fibonacci optimized
      slow: 34          # Fibonacci optimized
      signal: 8         # Fibonacci optimized
      threshold: 0.1    # Histogram threshold
    required_periods: 42
    validated: true     # Backtested (live-rules baseline, #574)

  RSI:
    description: "Relative Strength Index"
    default_params:
      period: 14
      oversold: 30
      overbought: 70
    required_periods: 24
    validated: true
```text

**Purpose**: Define available indicators with validated parameters
**Usage**: IndicatorRegistry reads this to create indicator instances
**Extensible**: Commented examples for BollingerBands, Stochastic

#### Default Ranking (Lines 54-63)

```yaml
default_ranking:
  - name: MACD
    rank: 1
    params: {}          # Use defaults
    role: active        # Active voter

  - name: RSI
    rank: 2
    params: {}
    role: active
```text

**Purpose**: Initial voter priority on system startup
**Roles**: `active` = participates in decisions, `review` = informational only

#### Voting Logic (Lines 74-89)

```yaml
voting:
  consensus_mode: unanimous       # unanimous, majority, weighted
  consensus_boost: 0.15           # +15% confidence when all agree
  weak_signal_boost: 0.10         # +10% for single-voter signals
  conflict_penalty: 0.20          # -20% when voters disagree

  strong_signal_size: 1.0         # Full position on consensus
  weak_signal_size: 0.5           # Half position on single voter
  conflict_size: 0.0              # No position on conflict

  min_data_points: 42             # Minimum bars for MACD
```text

**Purpose**: Control how signals combine and affect position sizing
**Key Insight**: `consensus_mode: unanimous` requires all active voters to agree for strong signals

#### Presets (Lines 92-112)

```yaml
presets:
  default:
    description: "MACD+RSI voting (the live CLI default)"
    active_voters: 2
    ranking:
      - {name: MACD, rank: 1, role: active}
      - {name: RSI, rank: 2, role: active}

  macd_primary:
    description: "MACD-focused with RSI confirmation"
    active_voters: 1
    ranking:
      - {name: MACD, rank: 1, role: active}
      - {name: RSI, rank: 2, role: review}

  rsi_primary:
    description: "RSI-focused with MACD confirmation"
    active_voters: 1
    ranking:
      - {name: RSI, rank: 1, role: active}
      - {name: MACD, rank: 2, role: review}
```text

**Purpose**: Quick switching between voting strategies
**CLI Integration**: Will enable `/voter preset macd_primary` (#488)

### Section 2: Multi-Timeframe Voting (#395)

#### Presets (Lines 137-180)

```yaml
multi_timeframe:
  enabled: true
  default_preset: trend_following

  presets:
    trend_following:
      description: "Trend direction with entry timing (default)"
      timeframes:
        1d: 0.5     # Trend direction (highest weight)
        4h: 0.3     # Intermediate momentum
        1h: 0.2     # Entry timing
      min_data_days:
        1d: 60
        4h: 10
        1h: 3

    intraday:
      description: "Intraday swing trading with precise entries"
      timeframes:
        4h: 0.4     # Swing trend
        1h: 0.35    # Setup confirmation
        15m: 0.25   # Precise entry
      min_data_days:
        4h: 10
        1h: 3
        15m: 1

    position:
      description: "Long-term position trading"
      timeframes:
        1w: 0.5     # Long-term trend
        1d: 0.35    # Daily momentum
        4h: 0.15    # Entry refinement
      min_data_days:
        1w: 180
        1d: 60
        4h: 10

    scalping:
      description: "Fast scalping with micro trends"
      timeframes:
        1h: 0.4     # Short-term trend
        15m: 0.35   # Setup
        5m: 0.25    # Entry
      min_data_days:
        1h: 3
        15m: 1
        5m: 1
```text

**Purpose**: Different multi-timeframe blends for different trading styles
**Weight Logic**: Sum must equal 1.0 (e.g., 0.5 + 0.3 + 0.2 = 1.0)
**CLI Integration**: Will enable `/timeframe preset trend_following` (#489)

#### Consensus Settings (Lines 183-194)

```yaml
consensus:
  require_unanimous_for_strong: true
  min_alignment_for_moderate: 0.66      # 2/3 agreement
  non_unanimous_penalty: 0.2            # -20% confidence
  conflict_action: HOLD
```text

**Purpose**: How to handle disagreement across timeframes
**Example**:

- All 3 timeframes say BUY → STRONG BUY
- 2/3 say BUY → MODERATE BUY
- 1 BUY, 1 SELL, 1 HOLD → HOLD (conflict)

---

## 2. scanner_config.yaml (91 lines)

**Location**: `config_defaults/scanner_config.yaml`

### Tiered Watchlist System (Lines 14-36)

```yaml
tiered_watchlist:
  enabled: true
  max_symbols_per_scan: 25

  tier_limits:
    positions: 10       # Tier 0: Active positions (broker API)
    pending_orders: 5   # Tier 1: Pending/recent orders (broker API)
    strategy: 8         # Tier 2: Strategy-specific watchlist
    discovery: 5        # Tier 3: Discovery/research tickers

  refresh_intervals:
    positions: 60       # Every 1 min
    pending_orders: 300 # Every 5 min
    strategy: 900       # Every 15 min
    discovery: 3600     # Every hour

  fallback_to_config: true
```text

**Purpose**: Priority-based symbol allocation (#405)
**Logic**:

1. Scan positions first (most important)
2. Then pending orders
3. Then strategy tickers (mode-specific)
4. Finally discovery tickers

**Tier 0-1 Sources**: Broker API (AlpacaTradingClient)
**Tier 2 Source**: trading_modes.yaml → watchlist_strategy
**Tier 3 Source**: default_watchlist in this file

### Default Watchlist (Lines 42-74)

```yaml
default_watchlist:
  core_etfs: [SPY, QQQ, IWM, VTI]
  leverage_etfs: [TQQQ, SQQQ, UPRO, SPXL]
  tech_giants: [AAPL, MSFT, NVDA, TSLA, META, GOOGL, AMZN]
  growth_stocks: [PLTR, COIN, AMD, CRM, NFLX]
```text

**Purpose**: Tier 3 discovery list, also used as fallback
**Total**: 23 symbols across 4 categories

### Scanner Settings (Lines 76-90)

```yaml
scanner_settings:
  cache_dir: ".cache/scanner_data"
  batch_fetch_days: 3
  max_concurrent_scans: 10
  scan_timeout_seconds: 30
  min_opportunity_confidence: 0.65
```text

**Purpose**: Operational parameters for ScannerAgent

---

## 3. trading_modes.yaml (150 lines)

**Location**: `config_defaults/trading_modes.yaml`

### Mode Structure

Each mode (conservative, moderate, aggressive) defines:

1. **Watchlist Strategy** (Issue #405 integration)
2. **Position Sizing**
3. **Exit Strategy**
4. **Trailing Stops** (Issue #414)
5. **Risk Metrics**
6. **Partial Exits**

### Watchlist Strategy Integration

```yaml
conservative:
  watchlist_strategy: balanced        # Links to scanner config

moderate:
  watchlist_strategy: momentum

aggressive:
  watchlist_strategy: wheel_strategy
```text

**Purpose**: Each trading mode selects which symbols to prioritize in Tier 2
**Files**: Loads from `config_defaults/watchlists/{watchlist_strategy}.yaml`
**Status**: ⚠️ Watchlist YAML files not yet created (future work)

### Position Sizing Comparison

| Parameter | Conservative | Moderate | Aggressive |
|-----------|--------------|----------|------------|
| max_position_pct | 5% | 10% | 20% |
| max_position_value | $2,500 | $5,000 | $10,000 |
| max_portfolio_pct | 10% | 20% | 40% |
| max_positions | 5 | 10 | 15 |

### Exit Strategy Comparison

| Parameter | Conservative | Moderate | Aggressive |
|-----------|--------------|----------|------------|
| stop_loss | 2% | 5% | 8% |
| take_profit | 5% | 10% | 20% |
| risk_per_trade | 1% | 2% | 3% |
| min_confidence | 0.75 | 0.65 | 0.55 |

### Trailing Stops (#414 Advanced Features)

```yaml
conservative:
  trailing_stops:
    climb_rate: slow              # slow/medium/fast
    volatility_aware: true        # ATR-based adjustments
    atr_multiplier: 2.0           # Wider stops
    profit_zone_start_pct: 0.015  # 1.5% = enter profit zone

moderate:
  trailing_stops:
    climb_rate: medium
    atr_multiplier: 1.5

aggressive:
  trailing_stops:
    climb_rate: fast
    atr_multiplier: 1.0           # Tighter stops
```text

**Purpose**: Mode determines risk tolerance for trailing stop management

---

## 4. state/user.db Schema

**Location**: `state/user.db`
**Created by**: `RankedVoterManager` on first use

### Table: voter_ranking_history

```sql
CREATE TABLE voter_ranking_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp TEXT NOT NULL,
    ranking_json TEXT NOT NULL,
    preset_name TEXT,
    reason TEXT
);
```text

**Columns**:

| Column | Type | Purpose |
|--------|------|---------|
| id | INTEGER | Auto-increment primary key |
| timestamp | TEXT | ISO 8601 timestamp (e.g., "2025-12-15T14:32:01") |
| ranking_json | TEXT | JSON array of voter configs |
| preset_name | TEXT | Name of preset applied (default, macd_primary, etc.) |
| reason | TEXT | Human-readable reason for change |

**Example Row**:

```json
{
  "id": 1,
  "timestamp": "2025-12-15T14:30:00",
  "ranking_json": "[{\"name\":\"MACD\",\"rank\":1,\"role\":\"active\"},{\"name\":\"RSI\",\"rank\":2,\"role\":\"active\"}]",
  "preset_name": "default",
  "reason": "System initialization"
}
```text

**Purpose**: Audit trail for voter ranking changes
**Retention**: Keep all history (no auto-cleanup)
**Query**: Most recent ranking = `SELECT * FROM voter_ranking_history ORDER BY timestamp DESC LIMIT 1`

### Usage in RankedVoterManager

```python
from src.core.ranked_voter_config import get_ranked_voter_manager

manager = get_ranked_voter_manager()

# Apply preset → creates DB entry
manager.apply_preset("macd_primary")

# Get ranking → reads from DB + YAML
current = manager.get_ranking()

# Get history
history = manager.get_ranking_history(limit=10)
```text

---

## Configuration Loading Pipeline

### Current State

```text
main.py
  └─→ OrchestratorFactory.create()
       ├─→ RealVoterStrategy()
       │    └─→ VoterAgent(use_config_file=True)
       │         ├─→ TradingConfig()
       │         │    └─→ trading_config.yaml ✅ (basic MACD/RSI params)
       │         │
       │         └─→ voters_config.yaml ❌ NOT YET LOADED
       │
       ├─→ get_mode_manager()
       │    └─→ trading_modes.yaml ✅
       │         └─→ watchlist_strategy (per mode)
       │
       └─→ ScannerAgent()
            └─→ scanner_config.yaml ✅
                 └─→ tiered_watchlist
```text

### What's Missing

#### voters_config.yaml not loaded automatically

Currently:

- `VoterAgent` loads basic `trading_config.yaml` ✅
- But does NOT call `get_ranked_voter_manager()` ❌
- Must explicitly call: `manager = get_ranked_voter_manager()`

**Fix for #488**:

```python
# In src/core/factory.py:OrchestratorFactory.create()
from src.core.ranked_voter_config import get_ranked_voter_manager

# Load voters config on startup
voter_manager = get_ranked_voter_manager()
voter_manager.apply_preset("default")  # Or read from config
```text

---

## Multi-Timeframe + Trading Mode Integration

### Proposed Auto-Mapping (Phase 3)

```yaml
# In trading_modes.yaml - future enhancement
conservative:
  multi_timeframe_preset: position      # Weekly + daily focus

moderate:
  multi_timeframe_preset: trend_following  # Daily + 4h focus

aggressive:
  multi_timeframe_preset: intraday      # Hourly + 15m focus
```text

**Logic**: Trading mode automatically determines multi-timeframe blend
**Implementation**: Phase 3 (not in #488, #489, #490)

---

## Validation & Testing

### Config Validation

All configs are **valid YAML** and load successfully:

```bash
# Test YAML loading
python -c "
import yaml
from pathlib import Path

configs = [
    'config_defaults/voters_config.yaml',
    'config_defaults/scanner_config.yaml',
    'config_defaults/trading_modes.yaml'
]

for config_path in configs:
    with open(config_path) as f:
        data = yaml.safe_load(f)
    print(f'✅ {config_path}: {len(data)} top-level keys')
"
```text

### DB Schema Validation

```bash
# Test DB access
python -c "
from src.core.ranked_voter_config import get_ranked_voter_manager

manager = get_ranked_voter_manager()
print(f'Active voters: {len(manager.get_active_voters())}')
print(f'Review voters: {len(manager.get_review_voters())}')
print(f'✅ Database schema working')
"
```text

---

## Integration Checklist

### Phase 1 (#488, #489, #490)

- [ ] Load voters_config.yaml on CLI startup
  - [ ] Call `get_ranked_voter_manager()` in OrchestratorFactory
  - [ ] Apply default preset

- [ ] Create CLI commands to expose configs
  - [ ] `/voter list` - Show active/review voters
  - [ ] `/voter preset <name>` - Switch preset
  - [ ] `/timeframe preset <name>` - Multi-TF preset
  - [ ] `/backup database` - DB operations

### Phase 2 (Future)

- [ ] Create watchlist YAML files
  - [ ] `config_defaults/watchlists/balanced.yaml`
  - [ ] `config_defaults/watchlists/momentum.yaml`
  - [ ] `config_defaults/watchlists/wheel_strategy.yaml`

- [ ] Auto-select multi-timeframe preset based on trading mode

### Phase 3 (Advanced)

- [ ] Add `multi_timeframe_preset` field to trading_modes.yaml
- [ ] Update RealVoterStrategy to use MultiTimeframeVoter
- [ ] Show multi-timeframe consensus in CLI output

---

## Config File Summary

| File | Lines | Sections | Used By | Status |
|------|-------|----------|---------|--------|
| voters_config.yaml | 195 | 2 (ranked, multi-tf) | RankedVoterManager, MultiTimeframeVoter | ✅ Complete |
| scanner_config.yaml | 91 | 3 (tiered, default, settings) | ScannerAgent | ✅ Complete |
| trading_modes.yaml | 150 | 3 modes | TradingModeManager | ✅ Complete |

**Total Config Coverage**: 436 lines across 3 files
**DB Tables**: 1 (voter_ranking_history)
**Ready for CLI Integration**: ✅ Yes

---

## Next Steps

1. Implement #488 - `/voter` CLI commands
2. Implement #489 - `/timeframe` multi-TF commands
3. Implement #490 - `/backup` DB commands
4. Load voters_config.yaml on OrchestratorFactory init
5. Test end-to-end CLI workflow with new features

---

## Profile vs Preset Architecture (NEW - #492)

### Key Distinction

| Concept | Location | Mutability | Purpose |
|---------|----------|------------|---------|
| **Preset** | `config_defaults/*.yaml` | Read-only | Factory defaults, reset source |
| **Profile** | `state/user.db` | User-modifiable | Custom configurations |

### Workflow

1. **System starts** → Load default preset from YAML
2. **User modifies** → Create profile in DB (based on preset)
3. **User resets** → Deactivate profile, return to preset

### New DB Tables (Phase 2)

```sql
-- voter_profiles: Custom voter configurations
CREATE TABLE voter_profiles (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    profile_name TEXT NOT NULL UNIQUE,
    base_preset TEXT NOT NULL,
    ranking_json TEXT NOT NULL,
    is_active BOOLEAN DEFAULT FALSE
);

-- watchlist_profiles: Custom symbol lists
CREATE TABLE watchlist_profiles (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    profile_name TEXT NOT NULL UNIQUE,
    symbols_json TEXT NOT NULL,
    is_active BOOLEAN DEFAULT FALSE
);
```

See: [20_profile_preset_architecture.md](20_profile_preset_architecture.md)

---

## Timeframe Warning System (NEW - #493)

### Warning Indicators (* System)

| Input | Display | Warning |
|-------|---------|---------|
| `30s` | `1m*` | Sub-minute not available, using 1m |
| `65m` | `65m*` | Custom timeframe (aggregated from 1m) |
| `2w` | `2w*` | Multi-week (aggregated from 1w) |
| `NEWIPO @ 1d` | `1d*` | Limited data: Only 10d of history |

### API Methods

```python
from src.trading.instruments.custom_timeframe import get_custom_timeframe_builder

builder = get_custom_timeframe_builder()

# Get info with warnings
info = builder.get_timeframe_info("30s")
# info["display"] = "1m*"
# info["warnings"] = ["Sub-minute (30s) not available, using 1m"]

# Check data sufficiency
result = builder.check_data_sufficiency("NEWIPO", "2w", available_days=10)
# result["sufficient"] = False
# result["display_label"] = "2w*"
```

### Alpaca API Timeframe Limits

- **Minimum**: 1 minute (no sub-minute bars)
- **Maximum**: 1 month
- **Extended hours**: Included by default (no server-side filter)
- **Free tier**: ~9 hour delay on historical data

---

## See Also

- [16_cli_integration_status_phase2.md](16_cli_integration_status_phase2.md) - Detailed CLI integration guide (phase 1: [15_cli_integration_status_phase1.md](15_cli_integration_status_phase1.md))
- [20_profile_preset_architecture.md](20_profile_preset_architecture.md) - Profile system design
- [09_core_features_gameplan.md](./09_core_features_gameplan.md) - Feature roadmap
- GitHub Issues: #488, #489, #490, #492, #493
