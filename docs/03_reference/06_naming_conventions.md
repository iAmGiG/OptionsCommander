# Naming Conventions

This document establishes consistent naming patterns for the AutoTrader trading system.

## File Naming

### Core Trading Components

- `src/trading/position_manager.py` - Position and account management
- `src/trading/order_manager.py` - Order placement and tracking
- `src/trading/trade_lifecycle.py` - Individual trade state machine
- `src/trading/trading_cycle.py` - Daily trading routines
- `src/trading/market_scanner.py` - Market opportunity scanning
- `src/trading/technical_scanner.py` - Technical analysis scanning
- `src/trading/simple_signals.py` - Signal generation
- `src/trading/error_handling.py` - Error handling utilities

### Data Components

- `src/trading/unified_price_fetcher.py` - Centralized price data
- `src/trading/unified_state_manager.py` - Centralized state management

### Integration Components

- `src/trading/broker/alpaca_trading_client.py` - Alpaca broker integration
- `src/trading/llm_trading_assistant.py` - LLM trading interface

## Class Naming

### Core Classes

- `PositionManager` - Manages positions and account data
- `OrderManager` - Handles order placement and tracking
- `TradeCycle` - Individual trade lifecycle
- `TradingCycle` - Daily trading operations
- `MarketScanner` - Scans for opportunities
- `TechnicalScanner` - Technical analysis scanning
- `SimpleSignalGenerator` - Signal generation

### Support Classes

- `UnifiedPriceFetcher` - Price data fetching
- `UnifiedStateManager` - State management
- `AlpacaOrderManager` - Alpaca order management
- `LLMTradingAssistant` - LLM interface

## Method Naming

### Action Methods

- `place_order()` - Place trading orders
- `monitor_fills()` - Check for order fills
- `update_positions()` - Update position data
- `scan_market()` - Scan for opportunities
- `generate_signals()` - Create trading signals

### Query Methods

- `get_positions()` - Retrieve position data
- `get_account_info()` - Get account information
- `check_order_status()` - Check order status
- `evaluate_signals()` - Evaluate trading signals

### State Methods

- `save_state()` - Persist state data
- `load_state()` - Load state data
- `refresh_cache()` - Update cached data

## Variable Naming

### Financial Data

- `symbol` - Ticker symbol (e.g., "AAPL")
- `price` - Current price
- `entry_price` - Entry price for position
- `stop_price` - Stop loss price
- `target_price` - Take profit price
- `quantity` - Number of shares
- `market_value` - Current market value

### Trading State

- `trade_state` - Current trade state
- `order_id` - Broker order identifier
- `fill_price` - Actual fill price
- `confidence` - Signal confidence (0.0-1.0)
- `signal_action` - Trading action ('BUY', 'SELL', 'HOLD')

### Technical Indicators

- `macd_line` - MACD line value
- `macd_signal` - MACD signal line
- `rsi_value` - RSI indicator value
- `sma_value` - Simple moving average

## Directory Structure

```bash
src/trading/
├── position_manager.py        # Position & account management
├── order_manager.py           # Order placement & tracking
├── trade_lifecycle.py         # Individual trade states
├── trading_cycle.py           # Daily routines
├── market_scanner.py          # Market scanning
├── technical_scanner.py       # Technical analysis
├── simple_signals.py          # Signal generation
├── error_handling.py          # Error utilities
├── unified_price_fetcher.py   # Price data
├── unified_state_manager.py   # State management
├── alpaca_trading_client.py   # Broker integration
└── llm_trading_assistant.py   # LLM interface

examples/
├── trading_system_demo.py     # System demonstration
├── trading_assistant_demo.py  # Assistant demonstration
└── basic_voting_demo.py       # Signal demonstration

tests/
├── test_position_manager.py   # Position manager tests
├── test_order_manager.py      # Order manager tests
└── test_signal_generation.py  # Signal tests
```

## Anti-Patterns to Avoid

### Bad File Names

❌ `critical_fixes.py` - Too generic, unclear purpose
❌ `cost_efficient_cycle.py` - Redundant qualifiers
❌ `useful_scanner.py` - Meaningless qualifier
❌ `trading_fix.py` - Temporary-sounding name
❌ `demo_something.py` - Should be in examples/

### Bad Class Names

❌ `CostEfficientTradeCycle` - Too verbose
❌ `UsefulScanner` - Meaningless qualifier
❌ `CriticalFixer` - Not descriptive
❌ `UtilityHelper` - Too generic

### Bad Method Names

❌ `do_trading_stuff()` - Not descriptive
❌ `fix_things()` - Too generic
❌ `handle_request()` - Too broad
❌ `process_data()` - Not specific

## Best Practices

1. **Descriptive Names**: Names should clearly indicate purpose
2. **Consistent Patterns**: Use established patterns across the codebase
3. **No Temporary Names**: Avoid "fix", "temp", "test" in production code
4. **Clear Hierarchy**: File/class/method names should form logical hierarchy
5. **Domain Language**: Use trading terminology consistently
6. **Avoid Redundancy**: Don't repeat information in hierarchical names

## Examples

### Good Naming

```python
# Files
position_manager.py
order_manager.py
trade_lifecycle.py

# Classes
class PositionManager:
    def get_positions(self):
    def update_position(self):

# Methods
def place_market_order(symbol: str, quantity: int):
def monitor_order_fills() -> List[Dict]:
def calculate_position_size(account_balance: float) -> int:
```

### Bad Naming

```python
# Files
critical_fixes.py
useful_stuff.py
trading_helper.py

# Classes  
class CriticalTradingUtilityHelper:
    def do_stuff(self):
    def fix_things(self):

# Methods
def handle_order_stuff(data):
def process_trading_request(req):
def do_market_analysis():
```

This naming convention ensures the system feels unified and professional.
