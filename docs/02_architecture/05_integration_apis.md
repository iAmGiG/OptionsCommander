# Alpaca Markets Integration

**Provider**: Alpaca Markets (alpaca.markets)
**SDK**: Official alpaca-py Python SDK
**Status**: Supports paper and live trading (the system runs on paper)
**Capabilities**: Market data + Order management

## Overview

Complete integration with Alpaca Markets providing both market data acquisition and order execution capabilities. Built on the official alpaca-py SDK with intelligent caching, comprehensive error handling, and tools for the agents and the CLI (Microsoft Agent Framework; AutoGen until #606).

**Integrated Capabilities**:

1. **Market Data**: Real-time and historical OHLCV bars, quotes, trades, snapshots
2. **Order Management**: All order types with advanced risk management
3. **Account Monitoring**: Real-time account status and position tracking
4. **CLI Tools**: plain functions in `src/cli/tools/`, behind the slash commands

---

## Part 1: Market Data Integration

### Status

✅ **Complete** (Issue #312)

- **File**: `src/market_data/alpaca_market_data.py`
- **Tests**: 3/3 passing
- **Cache Integration**: >90% API call reduction

### Features Implemented

#### Official SDK Integration

- **alpaca-py SDK**: Uses official Python SDK from Alpaca Markets
- **Automatic Pagination**: SDK handles pagination transparently
- **Type Safety**: Proper data model validation and type checking
- **Better Error Handling**: SDK provides comprehensive error management
- **Future Proof**: Maintained by Alpaca team with regular updates

#### Core Data Retrieval

- **OHLCV Bars**: Historical and intraday data with multiple timeframes
- **Latest Quotes**: Real-time bid/ask prices via IEX feed
- **Latest Trades**: Most recent trade execution data
- **Snapshots**: Combined latest trade, quote, and bar data
- **Paper Account Support**: Works with IEX feed for paper trading

#### Intelligent Caching (SQLite-Based)

- Integrates with `TradingCacheManager` (SQLite backend)
- Cache-first retrieval reduces API calls by >90%
- 8-10x faster query performance vs. file-based cache
- Automatic cache key generation based on symbols, timeframes, and date ranges
- Smart expiration handling: Historical data (10 year TTL), Recent data (24 hour TTL)
- Thread-safe concurrent access with ACID guarantees

#### Data Normalization

- Converts SDK data models to consistent schema
- Unified format across all data providers (Alpaca, Alpha Vantage)
- Automatic timezone handling and timestamp conversion
- Standardized column names and data types

### API Capabilities

#### Supported Endpoints

- `/v2/stocks/bars` - OHLCV historical data
- `/v2/stocks/{symbol}/quotes/latest` - Latest bid/ask
- `/v2/stocks/{symbol}/trades/latest` - Latest trade
- `/v2/stocks/{symbol}/snapshot` - Combined snapshot

#### Parameters

- **Symbols**: Single or multiple tickers
- **Timeframes**: 1Min, 5Min, 15Min, 30Min, 1Hour, 1Day
- **Date Ranges**: YYYY-MM-DD format
- **Feeds**: SIP (subscription required), IEX (free tier)
- **Adjustments**: raw, split, dividend, all

### Usage Examples - Market Data

#### Basic Usage (Official SDK)

```python
from src.data_sources.sources.market.alpaca_market_data import AlpacaMarketData
from src.data_sources.cache import TradingCacheManager

# Initialize with SQLite cache (SDK implementation)
cache = TradingCacheManager()
alpaca = AlpacaMarketData(cache)

# Get daily bars using SDK
df = alpaca.get_bars(
    symbols=["SPY", "QQQ"],
    start="2024-01-15",
    end="2024-01-31",
    timeframe="1Day",
    feed="iex"  # Required for paper accounts
)

# Get latest quote (SDK)
quote = alpaca.get_latest_quote("AAPL")
print(f"Bid: ${quote['quote']['bp']}, Ask: ${quote['quote']['ap']}")

# Get latest trade (SDK)
trade = alpaca.get_latest_trade("AAPL")
print(f"Price: ${trade['trade']['p']}, Size: {trade['trade']['s']}")
```

#### Agent Usage - Market Data

```python
from src.market_data.alpaca_market_data import create_alpaca_market_data_tool

# Create tool for agents (SDK-powered)
tool = create_alpaca_market_data_tool()

# Use from the CLI or your own script
result = tool.get_bars(["SPY"], "2024-01-25", "2024-01-31", "1Day", feed="iex")
bars_data = result['bars']  # List of OHLCV records
count = result['count']     # Number of bars retrieved
```

### Limitations & Considerations - Market Data

#### Paper Account Restrictions

- **SIP Data**: Paper accounts can't access recent SIP data (requires subscription)
- **Solution**: Use `feed="iex"` for free IEX data
- **Date Range**: Recent data may be restricted, use dates >2 weeks old for testing

#### Rate Limiting

- **Free Tier**: 200 requests/minute
- **Paid Tier**: Higher limits available
- **Caching Helps**: Reduces API usage by caching results

#### WebSocket Streaming

- **Status**: Placeholder implementation
- **Future**: Real-time streaming for live trading
- **Current**: Polling-based updates via REST API

---

## Part 2: Order Management Integration

### Status

✅ **Complete** (Issue #313)

- **File**: `src/trading/broker/alpaca_trading_client.py`
- **Tests**: 29/29 passing
- **CLI tools**: `src/cli/tools/` (plain functions)

### Architecture

```bash
src/trading/broker/
└── alpaca_trading_client.py     # Core order management classes
src/cli/tools/                   # Plain functions (orders, account, stops, ...)

Classes:
├── AlpacaTradingClient          # Base client with unified live/paper support
├── AlpacaAccountMonitor         # Account status and position monitoring
└── AlpacaOrderManager           # Complete order management with risk controls
```

### Core Features

#### Order Types

- **Market Orders**: Immediate execution at current market price
- **Limit Orders**: Execute at specified price or better (GTC/DAY support)
- **Stop Orders**: Trigger market order when price reaches stop level
- **Trailing Stop Orders**: Dynamic stop that follows price movement
- **Bracket Orders**: Entry + take profit + stop loss combinations

#### Risk Management

- **Market Hours Validation**: Timezone-aware trading session checking
- **Daily Trade Limits**: Configurable maximum orders per market day
- **Position Validation**: Automatic position requirement checks for sell orders
- **Buying Power Checks**: Real-time buying power validation
- **Order Size Limits**: Configurable maximum order size enforcement

#### Safety Features

- **Unified Live/Paper Architecture**: Single codebase, different credentials
- **Multi-level Confirmations**: Extra safety for live trading operations
- **Mode-aware Logging**: Clear indicators for live vs paper operations
- **Comprehensive Validation**: Multiple validation layers before order submission

### Usage Examples - Order Management

#### Basic Order Placement

```python
from src.trading.alpaca_trading_client import AlpacaOrderManager

# Initialize with risk limits
manager = AlpacaOrderManager(
    mode="paper",  # or "live"
    risk_limits={
        'max_order_size': 1000,
        'max_daily_trades': 50
    }
)

# Market order
result = manager.place_market_order("AAPL", 100, "buy")
print(f"Order status: {result['status']}")

# Limit order
result = manager.place_limit_order_gtc("AAPL", 100, "buy", 150.00)
print(f"Order ID: {result['order_id']}")
```

#### Advanced Order Types

```python
# Stop order
result = manager.place_stop_order("AAPL", 100, "sell", 140.00)

# Trailing stop (5% trail)
result = manager.place_trailing_stop_order(
    "AAPL", 100, "sell", trail_percent=0.05
)

# Bracket order (entry + profit target + stop loss)
result = manager.place_bracket_order(
    symbol="AAPL",
    qty=100,
    side="buy",
    entry_limit_price=150.00,  # None for market entry
    take_profit_price=160.00,
    stop_loss_price=140.00
)
```

#### Order Management

```python
# Modify existing order
result = manager.modify_order(
    order_id="abc123",
    qty=150,  # Change quantity
    limit_price=155.00  # Change price
)

# Cancel single order
result = manager.cancel_order("abc123")

# Cancel all orders for symbol
result = manager.cancel_all_orders(symbol="AAPL")

# Cancel all open orders
result = manager.cancel_all_orders()
```

### Configuration

#### API Credentials

**Location**: `config/config.json` (gitignored)

```json
{
    "ALPACA_PAPER_API_KEY": "your_paper_key",
    "ALPACA_PAPER_SECRET": "your_paper_secret",
    "ALPACA_LIVE_API_KEY": "your_live_key",
    "ALPACA_LIVE_SECRET": "your_live_secret"
}
```

#### Risk Limits

```python
risk_limits = {
    'max_order_size': 1000,        # Maximum shares per order
    'max_daily_trades': 50,        # Maximum orders per market day
    'max_position_percent': 0.10   # Maximum position size (10% of portfolio)
}
```

#### Market Hours Behavior

- **Regular Hours**: 9:30 AM - 4:00 PM ET
- **Extended Hours**: 4:00 AM - 8:00 PM ET (configurable)
- **Closed Market**: Orders queued with warnings
- **Validation Mode**: `warn_only=True` (default) or `warn_only=False` (block)

### Error Handling

All methods return standardized response format:

```python
{
    'status': 'submitted|error|cancelled',
    'message': 'Human-readable description',
    'order_id': 'alpaca_order_id',  # If successful
    'symbol': 'AAPL',
    'qty': 100.0,
    'mode': 'paper|live'
}
```

---

## Testing

### Test Results: 32/32 Passing

**Market Data Tests** (3/3):

- ✅ API Connection - Authentication working
- ✅ Market Data Retrieval - Retrieved bars for SPY/QQQ
- ✅ Data Normalization - Alpaca format → standard schema

**Order Management Tests** (29/29):

- ✅ Basic Orders (9/9) - Market, limit, validation, risk
- ✅ Advanced Orders (6/6) - Stop, trailing, bracket
- ✅ Advanced Features (14/14) - Modification, cancellation, market hours

### Run Tests

```bash
# Market data tests
python tests/test_alpaca_connection.py
python tests/test_alpaca_market_data.py

# Order management tests
python tests/test_alpaca_order_manager.py
python tests/test_alpaca_advanced_orders.py
```

---

## Production Deployment

### Paper Trading (Default)

- Uses paper account credentials
- Full feature compatibility
- No confirmations required
- Safe for development and testing

### Live Trading Setup

1. **Add live credentials** to `config/config.json`:

   ```json
   {
     "ALPACA_LIVE_API_KEY": "your_live_key",
     "ALPACA_LIVE_SECRET": "your_live_secret"
   }
   ```

2. **Initialize with live mode**:

   ```python
   # Market data
   alpaca = AlpacaMarketData(cache, mode="live")

   # Order management
   manager = AlpacaOrderManager(mode="live")
   ```

3. **Safety Confirmations**: All live operations require explicit confirmation

---

## Natural-Language Parsing

`src/parsers/llm_parser.py` is the one LLM call: it turns typed requests into a `TradeRequest` through a forced tool call on Microsoft Agent Framework's OpenAI client. Everything else (signals, orders, stops) is plain Python. The agents that used to wrap these were retired (#331).

---

## Dependencies

- **alpaca-py**: Official Alpaca Markets Python SDK
- **pytz**: Timezone handling for market hours
- **pandas**: Data manipulation for OHLCV data
- **typing**: Type hints and optional parameters
- **logging**: Comprehensive operation logging

---

## Next Steps

1. **Implement WebSocket Streaming** (#316 Event Bus integration)
2. **Real-time Agent Integration** (#310 Complete the agents)
3. **Paper Trading Deployment** (#315 Paper Trading Enhancement)
4. **Live Trading Validation** (Post-paper trading success)

---

*Complete Alpaca Markets integration providing market data acquisition and order execution with safety checks, validation, and Agent Framework tools.*
