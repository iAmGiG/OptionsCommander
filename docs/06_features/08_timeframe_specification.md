# Timeframe Specification (Issue #365)

**Status**: ✅ **IMPLEMENTED** - Multi-timeframe analysis support with CLI integration
**Implementation Phase**: Phase 1 - Core Configuration and CLI Tools
**Author**: Claude Code
**Date**: 2025-11-28

## Overview

Issue #365 implements comprehensive timeframe support for the AutoGen-Trader system, enabling traders to analyze and trade on different timeframes from scalping (1-minute) to long-term positioning (monthly). This document covers the complete implementation, configuration, and usage.

## What is a Timeframe?

A timeframe (or interval) represents the time period of each candle in technical analysis:

- **1m** = 1 minute (60 candles per hour)
- **5m** = 5 minutes (12 candles per hour)
- **15m** = 15 minutes (4 candles per hour)
- **30m** = 30 minutes (2 candles per hour)
- **1h** = 1 hour (24 candles per day)
- **4h** = 4 hours (6 candles per day)
- **1d** = 1 day (daily chart, ~252 candles per year)
- **1w** = 1 week (weekly chart)
- **1M** = 1 month (monthly chart)

### Timeframe Categories

| Strategy | Timeframes | Use Case | Risk Level |
|----------|-----------|----------|-----------|
| **Scalping** | 1m, 5m | Micro trends, EA signals | High |
| **Day Trading** | 15m, 30m | Intraday swings, trend following | Medium-High |
| **Swing Trading** | 1h, 2h, 4h | Medium-term trends | Medium |
| **Position Trading** | 1d (recommended) | Daily trends, strong signals | Medium-Low |
| **Intermediate** | 1w | Weekly trends, major support/resistance | Low |
| **Long-term** | 1M | Monthly trends, institutional moves | Very Low |

## Architecture

### Core Components

#### 1. **TimeframeConfig Dataclass** (`config_defaults/trading_config.py`)

```python
@dataclass
class TimeframeConfig:
    """Timeframe configuration for multi-timeframe analysis."""

    default: str = "1d"  # Default timeframe
    enabled_timeframes: list = None  # List of enabled timeframes

    def is_valid(self, timeframe: str) -> bool:
        """Check if a timeframe is valid."""

    def validate(self) -> bool:
        """Validate entire configuration."""
```

**Features:**

- Default timeframe set to "1d" (the only backtested timeframe)
- 10 enabled timeframes by default (1m, 5m, 15m, 30m, 1h, 2h, 4h, 1d, 1w, 1M)
- Validation methods for both individual timeframes and complete configuration
- Easy to customize: enable/disable timeframes, change default

#### 2. **TimeframeManager** (`src/trading/timeframe_tools.py`)

Core class for managing timeframe state:

```python
class TimeframeManager:
    def __init__(self):
        """Initialize timeframe manager."""

    def get_current_timeframe(self) -> str:
        """Get currently active timeframe."""

    def set_timeframe(self, timeframe: str) -> Dict:
        """Set active timeframe with validation."""

    def get_available_timeframes(self) -> Dict:
        """Get timeframes grouped by trading style."""

    def validate_timeframe(self, timeframe: str) -> Dict:
        """Validate a timeframe string."""

    def list_timeframes(self, verbose: bool = False) -> Dict:
        """List all available timeframes."""
```

#### 3. **Agent Tools** (`src/trading/timeframe_tools.py`)

AutoGen-compatible function tools for agents:

| Tool | Purpose | Parameters | Returns |
|------|---------|-----------|---------|
| `get_current_timeframe()` | Get active timeframe | None | `{"current_timeframe": "1d"}` |
| `set_current_timeframe(tf)` | Change timeframe | `timeframe: str` | Status dict |
| `list_available_timeframes()` | List all TFs | `include_descriptions: bool` | List of timeframes |
| `validate_timeframe(tf)` | Validate TF | `timeframe: str` | `{"valid": bool, ...}` |
| `get_timeframe_recommendations()` | Get strategy recommendations | None | Grouped recommendations |

#### 4. **CLI Commands** (`src/cli/timeframe_commands.py`)

User-facing CLI interface:

```python
class TimeframeCommands:
    def list_timeframes(self, verbose: bool) -> str
    def set_timeframe(self, timeframe: str) -> str
    def show_current_timeframe(self) -> str
    def show_timeframe_recommendations() -> str
    def validate_and_info(self, timeframe: str) -> str
```

#### 5. **CLI Integration** (`src/cli/cli_session.py`)

Natural language command routing:

- Detects timeframe-related user input
- Routes to `_handle_timeframe_request()`
- Supports flexible natural language phrasing

### Data Flow

```text
User Input (Natural Language)
    ↓
CLI Intent Classification (_classify_intent)
    ↓
Intent = "timeframe_management" ?
    ├─ YES → _handle_timeframe_request() → TimeframeCommands
    │         ↓
    │    Extract timeframe from input (regex)
    │         ↓
    │    Call appropriate command method
    │         ↓
    │    Display formatted output
    │
    └─ NO → Other request handlers
```

## Configuration

### Default Configuration

**File:** `config_defaults/trading_config.yaml`

```yaml
strategy_parameters:
  timeframe:
    default: "1d"
    enabled_timeframes:
      - "1m"    # 1 minute - scalping/EA signals
      - "5m"    # 5 minutes - fast intraday trading
      - "15m"   # 15 minutes - standard intraday
      - "30m"   # 30 minutes - intraday swing trading
      - "1h"    # 1 hour - medium-term trading
      - "2h"    # 2 hours - medium-term swing
      - "4h"    # 4 hours - swing/position trading
      - "1d"    # 1 day - DEFAULT (backtested)
      - "1w"    # 1 week - intermediate-term trends
      - "1M"    # 1 month - long-term positioning
```

### Custom Configuration

To customize timeframes in `trading_config.yaml`:

```yaml
strategy_parameters:
  timeframe:
    default: "4h"  # Change default
    enabled_timeframes:
      - "1h"
      - "4h"
      - "1d"       # Reduced set for faster backtesting
```

## Usage

### 1. CLI Commands

#### List Available Timeframes

```bash
> list timeframes
> show available timeframes
> list available
```

**Output:**

```text
📊 Available Timeframes:
==================================================
  '1m', '5m', '15m', '30m', '1h', '2h', '4h', '1d', '1w', '1M'

--------------------------------------------------
📍 Current: 1d
```

#### List with Descriptions

```bash
> list timeframes verbose
> show timeframes detailed
```

#### Change Timeframe

```bash
> change timeframe to 1h
> set timeframe 4h
> switch to 5m
```

**Output:**

```text
✅ Timeframe changed to 1h
```

#### Show Current Timeframe

```bash
> current timeframe
> show current timeframe
> what is the current timeframe
```

**Output:**

```text
📍 Current Timeframe
==================================================
  1d: 1 day - DEFAULT - the backtested timeframe
```

#### Get Recommendations

```bash
> timeframe recommendations
> suggest a timeframe
> best timeframe for swing trading
```

**Output:**

```text
📈 Timeframe Recommendations
==================================================

🎯 Scalping (Aggressive):
   1m, 5m

📊 Day Trading (Intraday):
   15m, 30m
...
```

### 2. Agent Integration

#### Using with AutoGen Agents

```python
from src.trading.timeframe_tools import TIMEFRAME_TOOLS

# Register tools with agent
agent = SomeAgent(tools=TIMEFRAME_TOOLS)

# Agent can then call:
# - get_current_timeframe()
# - set_current_timeframe("4h")
# - list_available_timeframes()
# - validate_timeframe("1h")
# - get_timeframe_recommendations()
```

#### Example Agent Usage

```python
# Agent decides to use 4-hour timeframe for swing trading
response = agent.call_function("set_current_timeframe", {"timeframe": "4h"})
# Returns: {"success": true, "message": "Timeframe changed to 4h", "current_timeframe": "4h"}

# Agent lists available for user
recs = agent.call_function("get_timeframe_recommendations")
# Returns: {"recommendations": {...}, "default_recommended": "1d"}
```

### 3. VoterAgent Integration

VotingSignal takes a timeframe. To use it:

```python
from src.trading.instruments.voting_signal import VotingSignal

# With a custom timeframe
signal = VotingSignal(timeframe="4h")  # Instead of default "1d"

# Or use the config-based default
signal = VotingSignal()  # Uses "1d" from config
```

### 4. Indicator Functions

Technical indicators now accept timeframe parameter for documentation:

```python
from src.trading_tools.indicators import calculate_macd, calculate_rsi

# The timeframe parameter is informational
macd_data = calculate_macd(prices, fast=13, slow=34, signal=8, timeframe="1h")
rsi_data = calculate_rsi(prices, period=14, oversold=30, overbought=70, timeframe="1h")
```

**Note:** Actual timeframe aggregation should be done on the data before calling indicators.

## API Reference

### TimeframeManager

```python
from src.trading.timeframe_tools import TimeframeManager

manager = TimeframeManager()

# Get current
current = manager.get_current_timeframe()  # Returns: "1d"

# Set new timeframe
result = manager.set_timeframe("1h")
# Returns: {"success": True, "message": "...", "current_timeframe": "1h"}

# Validate
validation = manager.validate_timeframe("4h")
# Returns: {"timeframe": "4h", "valid": True, ...}

# Get recommendations grouped by strategy
recs = manager.get_available_timeframes()
# Returns: {"scalping": [...], "day_trading": [...], ...}
```

### TimeframeConfig (from trading_config)

```python
from config_defaults.trading_config import get_config

config = get_config()
tf_config = config.get_timeframe_config()

# Check if valid
if tf_config.is_valid("1h"):
    print("1h is enabled")

# List enabled
print(tf_config.enabled_timeframes)

# Validate entire config
if tf_config.validate():
    print("Config is valid")
```

## Best Practices

### 1. **Choose Right Timeframe for Strategy**

| If You Want To | Use This | Reason |
|---|---|---|
| Scalp (quick in/out) | 1m, 5m | See micro trends |
| Day trade (within day) | 15m, 30m | Balance noise vs signals |
| Swing trade (multi-day) | 1h, 4h | Capture medium trends |
| Position trade (hold weeks) | 1d | Strong signals, less noise |
| Institutional tracking | 1w, 1M | See major moves |

### 2. **Default is Backtested**

- `1d` (daily) is the default and the only timeframe backtested (see the [live-rules baseline](../08_research/03_strategy_research/live_rules_baseline.md))
- Use custom timeframes only if backtesting proves better results
- Don't arbitrarily change without testing

### 3. **Consider Data Requirements**

Shorter timeframes require:

- More frequent market data updates
- More computational resources
- More market noise/whipsaws
- Higher slippage costs

Longer timeframes require:

- More historical data for indicators
- More patience (fewer trades)
- But: fewer false signals

### 4. **Multi-Timeframe Analysis**

For professional trading, analyze multiple timeframes:

```python
# Example: Trend on daily, entry on 1-hour
daily_trend = analyze(symbol, timeframe="1d")
entry_signal = analyze(symbol, timeframe="1h")

if daily_trend.is_bullish() and entry_signal.is_bullish():
    # High probability setup
    place_buy_order()
```

## Testing

Unit tests are provided in `tests/unit/trading/test_timeframe.py`:

```bash
# Run all timeframe tests
pytest tests/unit/trading/test_timeframe.py -v

# Run specific test class
pytest tests/unit/trading/test_timeframe.py::TestTimeframeConfig -v

# Run with coverage
pytest tests/unit/trading/test_timeframe.py --cov=src.trading.timeframe_tools
```

### Test Coverage

- ✅ TimeframeConfig initialization and validation
- ✅ TimeframeManager state management
- ✅ Agent tool function calls
- ✅ CLI command output formatting
- ✅ Natural language intent routing
- ✅ Invalid input handling
- ✅ Integration between components

## Performance Considerations

### Memory

- TimeframeManager maintains minimal state (single current timeframe)
- Configuration is loaded once at startup
- No per-symbol timeframe storage

### Speed

- Timeframe validation: O(1) - list lookup
- Current timeframe access: O(1)
- Agent tool calls: < 1ms

## Future Enhancements (Phase 2+)

Potential future improvements (not in Phase 1):

1. **Per-Symbol Timeframes** (#366)
   - Different symbols trade better on different timeframes
   - Auto-detection based on historical performance

2. **Timeframe Weighting** (#367)
   - Weight signals from multiple timeframes
   - Higher-timeframe signals weighted more heavily

3. **Synchronized Multi-Timeframe** (#368)
   - Aggregate data from multiple timeframes simultaneously
   - Ensure all candles close together for fair analysis

4. **Timeframe-Adaptive Indicators** (#369)
   - Auto-adjust indicator periods based on timeframe
   - e.g., RSI period = 14 for 1d, but 7 for 1h

## Technical Implementation Details

### Global TimeframeManager Instance (Issue #445 Fix)

**Important:** All components must use the same global `TimeframeManager` instance to share state:

```python
# CORRECT - Use the global getter
from src.trading.timeframe_tools import _get_timeframe_manager

manager = _get_timeframe_manager()  # Gets global singleton

# INCORRECT - Creates separate instance (won't share state)
from src.trading.timeframe_tools import TimeframeManager

manager = TimeframeManager()  # DON'T DO THIS
```

The CLI's `TimeframeCommands` and the strategy's `get_current_timeframe()` both use `_get_timeframe_manager()` to ensure they share the same state.

### Data Fetching with Timeframe

The `fetch_unified_market_data()` function accepts a timeframe parameter:

```python
from src.data_sources.tools import fetch_unified_market_data

# Fetch hourly bars
data = fetch_unified_market_data(
    symbol="SPY",
    start_date="2025-01-01",
    end_date="2025-01-31",
    timeframe="1Hour"  # Alpaca format: 1Min, 5Min, 1Hour, 1Day
)
```

### Timeframe Format Conversion

User format ("1h") must be converted to Alpaca format ("1Hour"):

```python
from src.trading.timeframe_tools import convert_to_alpaca_timeframe

user_tf = "1h"
alpaca_tf = convert_to_alpaca_timeframe(user_tf)  # Returns "1Hour"
```

### Cache Keys Include Timeframe

Different timeframes are cached separately:

```python
# Cache key: symbol + date_range + source + timeframe
# "SPY_2025-01-01_2025-01-31_alpaca_1Hour"
# "SPY_2025-01-01_2025-01-31_alpaca_1Day"
```

## Troubleshooting

### "Invalid timeframe" Error

```text
❌ Invalid timeframe '99h'. Valid options: 1m, 5m, 15m, 30m, 1h, 2h, 4h, 1d, 1w, 1M
```

**Solution:** Check spelling and use only supported timeframes. Enable custom ones in config if needed.

### Timeframe Not Changing

```bash
> change timeframe to 1h
❌ Invalid timeframe '1h'. Valid options: 1d, 1w
```

**Cause:** Custom `enabled_timeframes` in config doesn't include "1h"

**Solution:** Edit `config_defaults/trading_config.yaml` to add "1h" to `enabled_timeframes`

### MACD/RSI Values Same Across Timeframes (Fixed in #445)

**Issue:** Changed timeframe but MACD/RSI values remain identical

**Cause (fixed):** CLI and strategy were using different `TimeframeManager` instances

**Solution:** Update to latest code - all components now use global `_get_timeframe_manager()`

### Indicator Not Respecting Timeframe

**Issue:** Indicator output doesn't match expected candle frequency

**Cause:** Timeframe parameter on indicator functions is informational only - actual data aggregation happens upstream

**Solution:** Ensure data is aggregated to target timeframe BEFORE calling indicator functions

## Related Issues

- **#323** - Trading Pipeline (uses timeframe from config)
- **#364** - Ranked Voter System (future multi-timeframe voting)
- **#366** - OHLCV Entry Planning (per-symbol timeframes)
- **#400** - Trading Modes (risk adjusted per timeframe)
- **#401** - Multi-Account (account-level timeframe settings)
- **#402** - Security Architecture (secure timeframe storage)
- **#445** - Timeframe affecting MACD/RSI analysis (fixed)

## Summary

Issue #365 provides a complete, extensible timeframe system:

✅ **Phase 1 Complete:**

- Configuration management (TimeframeConfig)
- State management (TimeframeManager)
- Agent tools (5 functions, AutoGen-ready)
- CLI commands (natural language support)
- VoterAgent integration
- Comprehensive testing (10+ test classes)
- Full documentation

**Status:** Implemented
**Default:** 1d (the only backtested timeframe)
**Supported:** 1m, 5m, 15m, 30m, 1h, 2h, 4h, 1d, 1w, 1M
**Customizable:** Yes, via YAML configuration
