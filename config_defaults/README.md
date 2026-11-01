# Configuration System

This directory contains **non-sensitive default configuration files** for OptionsCommander (formerly RH2MAS; repository `iAmGiG/OptionsCommander`, formerly AutoTrader-AgentEdge).

## Directory Structure

- **`config_defaults/`** (this directory) - Non-sensitive YAML configuration files (tracked in git)
- **`config/`** - Sensitive API keys and credentials (`.gitignore`d, not tracked in git)

## Configuration Files

### Trading & Strategy Configuration

- **`trading_config.yaml`** - Trading strategy parameters, risk management, and backtested performance metrics (reference only)
  - Timeframe specification for technical analysis (Issue #365)
  - MACD/RSI indicator settings
  - Exit strategies (stop loss, take profit)
  - Position sizing and risk management
  - Technical indicator confidence calculations (Issue #358)
  - Position management trailing stops (Issue #358)

- **`trading_modes.yaml`** - Risk profile configurations (Issue #400)
  - Conservative/Moderate/Aggressive preset modes
  - Per-mode position sizing, stop loss, take profit parameters
  - Trailing stop configurations per mode
  - Natural language accessible via CLI

### Market Scanner Configuration

- **`scanner_config.yaml`** - Market scanner watchlists and operational settings (Issue #358)
  - Default watchlist by category (ETFs, tech giants, growth stocks)
  - Scanner operational settings (timeouts, concurrency, cache)

### File Paths Configuration

- **`paths_config.yaml`** - Centralized file and directory paths (Issue #358)
  - Base directories (state, reports, logs, cache)
  - State file locations
  - Report templates with placeholders
  - Component-specific cache directories

### CLI Messages Configuration

- **`message_loader.py`** & **`cli_messages.yaml`** - CLI user interface messages and formatting
  - Welcome banners and help text
  - Trade suggestion formatting
  - Error messages and confirmations
  - Emoji configuration for cross-platform support

## Usage

### Basic Usage

```python
import yaml

# Load trading config
with open("config_defaults/trading_config.yaml") as f:
    config = yaml.safe_load(f)

stop_loss_pct = config["strategy_parameters"]["exits"]["balanced"]["stop_loss"]
timeframe = config["strategy_parameters"]["timeframe"]  # "1d" (Issue #365)
```

### In Class Constructors

```python
import yaml
import os

class MarketScanner:
    def __init__(self, watchlist=None):
        # Load scanner config
        config_path = os.path.join("config_defaults", "scanner_config.yaml")
        with open(config_path) as f:
            config = yaml.safe_load(f)

        # Use provided watchlist or load from config
        if watchlist is None:
            # Flatten all watchlist categories
            watchlist_config = config["default_watchlist"]
            watchlist = []
            for category in watchlist_config.values():
                watchlist.extend(category)

        self.watchlist = watchlist
        self.settings = config["scanner_settings"]
```

### Path Configuration Usage

```python
import yaml
from datetime import datetime

# Load paths config
with open("config_defaults/paths_config.yaml") as f:
    paths = yaml.safe_load(f)

# Use template placeholders
report_template = paths["report_templates"]["daily_routine"]
report_path = report_template.format(
    date=datetime.now().strftime("%Y-%m-%d"),
    routine_type="morning"
)
# Result: "reports/daily/2025-01-27_morning.md"
```

### Trailing Stop Configuration (Issue #321)

```python
from config_defaults.trading_config import TradingConfig

# Load trailing stop configuration
config = TradingConfig()
trailing = config.get_trailing_stop_config()

# Progressive thresholds (defaults; climb_rate medium locks 25% / 50% / 75% of gains)
print(f"Breakeven at: {trailing.progressive_breakeven_pct:.0%} profit")  # 2%
print(f"Lock 25% at: {trailing.progressive_lock_25_pct:.0%} profit")     # 4%
print(f"Lock 50% at: {trailing.progressive_trail_50_pct:.0%} profit")    # 6% (75% from 10%)

# Safety settings
print(f"Rate limit: {trailing.min_update_interval_seconds}s between updates")
print(f"Never move down: {trailing.never_move_stop_down}")
```

**Progressive Stop Logic**:

| Profit Level | Action | Description |
|--------------|--------|-------------|
| < 2% | Hold | No stop adjustment (avoid whipsaws) |
| 2-4% | Breakeven | Move stop to entry price (protect capital) |
| 4-6% | Lock 25% | Stop at entry + 25% of gains |
| 6-10% | Lock 50% | Stop at entry + 50% of gains |
| 10%+ | Lock 75% | Stop at entry + 75% of gains |

Stops only move up, stay at least 1% below the current price, and start from the broker's live stop.
These are the `TrailingStopConfig` defaults (`climb_rate: medium`; `slow` locks 20/40/60%, `fast`
33/60/80%; the 10% step is fixed in `trailing_stop_manager.py`).

**Override in `trading_config.yaml`** (it has no `trailing_stops` section, so the defaults apply):

```yaml
trailing_stops:
  enabled: true
  progressive_enabled: true
  progressive_breakeven_pct: 0.02  # 2%
  progressive_lock_25_pct: 0.04    # 4%
  progressive_trail_50_pct: 0.06   # 6%
  min_update_interval_seconds: 60
  never_move_stop_down: true
```

### Timeframe Configuration (Issue #365)

```python
from src.trading.instruments.voting_signal import VotingSignal

# Default: loads "1d" from config
voter = VotingSignal(use_config_file=True)

# Explicit timeframe for intraday trading
voter_1h = VotingSignal(timeframe="1h", use_config_file=False)

# Reconfigure timeframe dynamically
voter.reconfigure(timeframe="15m")

# Voting result includes timeframe context
result = voter.evaluate_voting("AAPL", price_data)
print(f"Signal: {result['action']} on {result['timeframe']} timeframe")
# Output: "Signal: BUY on 15m timeframe"
```

**Supported Timeframes**:

- `1m, 5m, 15m, 30m` - Scalping/day trading
- `1h, 2h, 4h` - Intraday swing trading
- `1d` - Daily swing/position trading (default, the backtested timeframe)
- `1w, 1M` - Position/long-term trading

### Trading Modes Configuration (Issue #400)

Trading modes are accessible via **natural language** in the CLI:

```bash
> buy SPY aggressively           # Uses aggressive position sizing
> conservative buy AAPL 10       # Uses conservative risk settings
> set risk mode to moderate      # Change session default
```

**Using Trading Modes Programmatically**:

```python
from src.core.trading_modes import TradingMode, get_mode_manager

# Get the global mode manager
mode_manager = get_mode_manager()

# Change mode
mode_manager.set_mode(TradingMode.AGGRESSIVE)

# Get current parameters
params = mode_manager.get_parameters()
print(f"Max position: {params.max_position_pct:.0%}")  # 20%
print(f"Stop loss: {params.stop_loss:.0%}")           # 8%
print(f"Take profit: {params.take_profit:.0%}")       # 20%
```

**Mode Parameters**:

| Mode | Position | Stop | Target | Description |
|------|----------|------|--------|-------------|
| Conservative | 5% | 2% | 5% | Capital preservation |
| Moderate | 10% | 5% | 10% | Balanced (default) |
| Aggressive | 20% | 8% | 20% | Maximum growth |

**Configuration in `trading_modes.yaml`**:

```yaml
default_mode: moderate

modes:
  conservative:
    max_position_pct: 0.05
    stop_loss: 0.02
    take_profit: 0.05
    trailing_stops:
      progressive_breakeven_pct: 0.02
      progressive_lock_25_pct: 0.03
      progressive_trail_50_pct: 0.04

  moderate:
    max_position_pct: 0.10
    stop_loss: 0.05
    take_profit: 0.10

  aggressive:
    max_position_pct: 0.20
    stop_loss: 0.08
    take_profit: 0.20
```

## Sensitive Configuration (config/)

The `config/` directory contains sensitive API keys and credentials:

```json
{
  "ALPACA_API_KEY": "...",
  "ALPACA_SECRET_KEY": "...",
  "OPENAI_API_KEY": "...",
  "ALPHA_VANTAGE_KEY": "..."
}
```

**Security Notes:**

- The `config/` directory is gitignored and **never committed to the repository**
- API keys should be stored in `config/config.json`
- Use environment variables for production deployments

## Benefits

1. **Security** - API keys separated from default configuration (never committed)
2. **Version Control** - Default configs tracked in git for team collaboration
3. **Environment Flexibility** - Different API keys for dev/test/prod
4. **Easy Customization** - Edit YAML files without touching code
5. **Consistency** - Shared parameters across components
6. **Maintainability** - Central location for all system constants

## Issue #358 - Configuration Externalization

This configuration system addresses Issue #358 by externalizing hardcoded values:

### Phase 1: Config Files Created ✅

- `trading_config.yaml` - Technical indicators, trailing stops
- `scanner_config.yaml` - Watchlist, scanner settings
- `paths_config.yaml` - File paths and directories

### Phase 2: Code Integration ✅

- ✅ Update `market_scanner.py` to load from `scanner_config.yaml`
- ✅ Update `trading_cycle.py` to load from `trading_config.yaml` and `paths_config.yaml`
- ✅ Update `daily_scheduler.py` to load from `paths_config.yaml`
- ✅ Update `position_manager.py` to load from `paths_config.yaml`

### Phase 3: Testing ✅

- ✅ Verify config loading with valid files (`test_config_loading.py`)
- ✅ Test graceful fallback to hardcoded defaults
- ✅ Validate all config values are used correctly

## Configuration Best Practices

1. **Never commit API keys** - Keep sensitive data in `config/config.json`
2. **Use descriptive comments** - Explain what each parameter does
3. **Include default values** - Provide sensible defaults for all parameters
4. **Document units** - Specify if values are percentages, dollars, seconds, etc.
5. **Group related settings** - Organize configs by functional area
6. **Validate on load** - Check for required fields and valid ranges
