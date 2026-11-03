# Cache System Developer Guide

**Audience**: Developers integrating with or extending the cache system
**Last Updated**: December 2025 (Issue #469, Multi-timeframe support)

---

## Quick Start

### Basic Usage

```python
from src.cache import TradingCacheManager

# Initialize cache
cache = TradingCacheManager()

# Store market data (daily by default)
cache.set(
    symbol="AAPL",
    data=df,  # pandas DataFrame with OHLCV data
    source="alpaca",
    asset_type="stock"
)

# Store multi-timeframe data
cache.set("AAPL", df_minute, source="alpaca", timeframe="1Min")
cache.set("AAPL", df_hourly, source="alpaca", timeframe="1Hour")
cache.set("AAPL", df_weekly, source="alpaca", timeframe="1Week")

# Retrieve cached data (daily by default)
df = cache.get(
    symbol="AAPL",
    start="2025-01-01",
    end="2025-01-31",
    source="alpaca",  # Optional: filter by source
    asset_type="stock"
)

# Retrieve specific timeframe
df_1m = cache.get("AAPL", "2025-01-01", "2025-01-31", timeframe="1Min")
df_1h = cache.get("AAPL", "2025-01-01", "2025-01-31", timeframe="1Hour")

# Check cache statistics
stats = cache.get_stats()
print(f"Total entries: {stats['total_entries']}")
print(f"Database size: {stats['db_size_mb']} MB")
```

### Using CacheAdapter (Backward Compatibility)

```python
from src.cache import cache_adapter

# Get market data (tries cache first, falls back to legacy)
df = cache_adapter.get_market_data(
    symbol="SPY",
    start_date="2025-10-01",
    end_date="2025-10-31",
    source="any",  # or specific: "alpaca", "polygon", etc.
    timeframe="1Day"  # Default; also supports "1Min", "5Min", "1Hour", "1Week"
)

# Get minute data
df_1m = cache_adapter.get_market_data(
    symbol="SPY",
    start_date="2025-10-01",
    end_date="2025-10-31",
    timeframe="1Min"
)

# Set market data with timeframe
cache_adapter.set_market_data(
    symbol="SPY",
    start_date="2025-10-01",
    end_date="2025-10-31",
    source="polygon",
    data=df,
    timeframe="1Day"
)
```

### Using UnifiedBrokerCache (Issue #469)

For broker state caching (account, positions, orders) with database-first architecture:

```python
from src.cache import unified_broker_cache

# Get cached account (fetches if stale, returns from DB)
account = unified_broker_cache.get_account(
    account_id="paper_main",
    fetcher=lambda: alpaca_monitor.get_account_status(),
    max_age_seconds=60
)

# Get cached positions
positions = unified_broker_cache.get_positions(
    account_id="paper_main", 
    fetcher=lambda: alpaca_monitor.get_positions()
)

# Store position snapshots for historical tracking
unified_broker_cache.store_position_snapshot("paper_main", positions)

# Audit display events
unified_broker_cache.audit_display(
    display_type="portfolio",
    data=positions,
    cache_source="cached",
    cache_age_seconds=30.5
)

# Get cache info
info = unified_broker_cache.get_cache_info("paper_main")
print(f"Account cache: {info['cache_entries'].get('account', {})}")
```

See [Database-First Caching Design](../design/469-database-first-caching.md) for architecture details.

---

## API Reference

### TradingCacheManager

**Constructor:**

```python
TradingCacheManager(
    db_path: str = ".cache/trading_data.db"
)
```

**Parameters:**

- `db_path`: Path to SQLite database file (default: `.cache/trading_data.db`)

---

#### `get()` - Retrieve Cached Data

```python
def get(
    self,
    symbol: str,
    start: str,
    end: str,
    source: str = None,
    asset_type: str = "stock",
    timeframe: str = "1Day"
) -> Optional[pd.DataFrame]:
```

**Parameters:**

- `symbol`: Stock ticker (e.g., "AAPL", "SPY")
- `start`: Start date in YYYY-MM-DD format
- `end`: End date in YYYY-MM-DD format
- `source`: Optional data source filter ("alpaca", "polygon", "alpha_vantage")
- `asset_type`: Asset type ("stock", "option", "future")
- `timeframe`: Bar timeframe (default: "1Day"). Supported values:
  - `"1Min"`, `"5Min"`, `"15Min"`, `"30Min"` - Minute bars
  - `"1Hour"`, `"2Hour"`, `"4Hour"` - Hourly bars
  - `"1Day"` - Daily bars (default)
  - `"1Week"`, `"1Month"` - Weekly/monthly bars

**Returns:**

- `pd.DataFrame` with OHLCV data if found
- `None` if cache miss

**Example:**

```python
# Get daily data (default)
df = cache.get("AAPL", "2025-01-01", "2025-01-31", source="alpaca")
if df is not None:
    print(f"Loaded {len(df)} days from cache")
else:
    print("Cache miss - fetch from API")

# Get minute data for intraday analysis
df_1m = cache.get("AAPL", "2025-01-15", "2025-01-15", timeframe="1Min")

# Get weekly data for longer-term trends
df_weekly = cache.get("AAPL", "2024-01-01", "2025-01-31", timeframe="1Week")
```

---

#### `set()` - Store Data in Cache

```python
def set(
    self,
    symbol: str,
    data: pd.DataFrame,
    source: str,
    asset_type: str = "stock",
    ttl_hours: int = None,
    timeframe: str = "1Day"
) -> None:
```

**Parameters:**

- `symbol`: Stock ticker
- `data`: DataFrame with OHLCV columns (must have 'close' column minimum)
- `source`: Data source ("alpaca", "polygon", "alpha_vantage")
- `asset_type`: Asset type ("stock", "option", "future")
- `ttl_hours`: Optional custom TTL (overrides smart expiration)
- `timeframe`: Bar timeframe (default: "1Day"). Supported values:
  - `"1Min"`, `"5Min"`, `"15Min"`, `"30Min"` - Minute bars
  - `"1Hour"`, `"2Hour"`, `"4Hour"` - Hourly bars
  - `"1Day"` - Daily bars (default)
  - `"1Week"`, `"1Month"` - Weekly/monthly bars

**Required DataFrame Columns:**

- `close` (required)
- `open`, `high`, `low`, `volume` (recommended)
- `vwap`, `transactions` (optional)

**Example:**

```python
# DataFrame must have a datetime index
cache.set("AAPL", df, source="alpaca", asset_type="stock")

# Store minute data
cache.set("AAPL", df_1m, source="alpaca", timeframe="1Min")

# Store weekly data
cache.set("AAPL", df_weekly, source="alpaca", timeframe="1Week")

# Custom TTL (48 hours)
cache.set("AAPL", df, source="alpaca", ttl_hours=48)
```

---

#### `delete()` - Remove Cached Data

```python
def delete(
    self,
    symbol: str,
    start: str = None,
    end: str = None,
    source: str = None,
    asset_type: str = "stock"
) -> int:
```

**Parameters:**

- `symbol`: Stock ticker
- `start`: Optional start date filter
- `end`: Optional end date filter
- `source`: Optional source filter
- `asset_type`: Asset type

**Returns:**

- Number of entries deleted

**Examples:**

```python
# Delete all AAPL data
deleted = cache.delete("AAPL")

# Delete specific date range
deleted = cache.delete("AAPL", "2025-01-01", "2025-01-31")

# Delete specific source
deleted = cache.delete("AAPL", source="alpaca")
```

---

#### `get_stats()` - Cache Statistics

```python
def get_stats(self) -> Dict[str, Any]:
```

**Returns:**

```python
{
    'db_path': str,              # Database file path
    'db_size_mb': float,         # Size in megabytes
    'total_entries': int,        # Total cached entries
    'unique_symbols': int,       # Number of unique symbols
    'expired_entries': int,      # Entries past expiration
    'date_range': {
        'min_date': str,         # Earliest date in cache
        'max_date': str          # Latest date in cache
    },
    'by_source': {
        'alpaca': int,           # Entries per source
        'polygon': int,
        ...
    },
    'by_asset_type': {
        'stock': int,            # Entries per asset type
        'option': int,
        ...
    }
}
```

**Example:**

```python
stats = cache.get_stats()
print(f"Cache contains {stats['unique_symbols']} symbols")
print(f"Date range: {stats['date_range']['min_date']} to {stats['date_range']['max_date']}")
```

---

#### `cleanup_expired()` - Remove Expired Entries

```python
def cleanup_expired(self) -> int:
```

**Returns:**

- Number of expired entries deleted

**Example:**

```python
deleted = cache.cleanup_expired()
print(f"Removed {deleted} expired entries")
```

---

#### `vacuum()` - Optimize Database

```python
def vacuum(self) -> None:
```

Reclaims unused space after deletions.

**Example:**

```python
cache.cleanup_expired()
cache.vacuum()  # Reclaim space
```

---

#### `get_symbols()` - List Cached Symbols

```python
def get_symbols(self, asset_type: str = "stock") -> List[str]:
```

**Parameters:**

- `asset_type`: Asset type filter

**Returns:**

- List of unique symbols

**Example:**

```python
symbols = cache.get_symbols(asset_type="stock")
print(f"Cached symbols: {', '.join(symbols)}")
```

---

## Smart Expiration Logic

The cache automatically manages TTL based on data age:

| Data Age | TTL | Rationale |
|----------|-----|-----------|
| < 2 days old | 24 hours | Recent data changes frequently |
| ≥ 2 days old | 10 years | Historical data is immutable |

**Examples:**

```python
# Recent data (expires in 24 hours)
today = datetime.now()
recent_df = fetch_data("AAPL", today - timedelta(days=1), today)
cache.set("AAPL", recent_df, source="alpaca")

# Historical data (expires in 10 years)
historical_df = fetch_data("AAPL", "2024-01-01", "2024-12-31")
cache.set("AAPL", historical_df, source="alpaca")

# Custom TTL (overrides smart logic)
cache.set("AAPL", df, source="alpaca", ttl_hours=48)
```

---

## Multi-Asset Support

### Stocks (Current)

```python
cache.set("AAPL", df, source="alpaca", asset_type="stock")
df = cache.get("AAPL", start, end, asset_type="stock")
```

### Options (Schema Ready)

```python
cache.set(
    symbol="AAPL_20250117C150",  # Option symbol format
    data=option_df,
    source="alpaca",
    asset_type="option"
)

df = cache.get(
    symbol="AAPL_20250117C150",
    start="2025-01-01",
    end="2025-01-17",
    asset_type="option"
)
```

### Futures (Schema Ready)

```python
cache.set(
    symbol="ES_202503",  # Futures contract
    data=futures_df,
    source="alpaca",
    asset_type="future"
)

df = cache.get(
    symbol="ES_202503",
    start="2025-01-01",
    end="2025-03-20",
    asset_type="future"
)
```

---

## Integration Patterns

### Data Source Integration

```python
from src.cache import TradingCacheManager

class MyDataSource:
    def __init__(self):
        self.cache = TradingCacheManager()

    def fetch_data(self, symbol, start, end):
        # Check cache first
        cached = self.cache.get(symbol, start, end, source="my_source")
        if cached is not None:
            logger.info(f"Cache hit for {symbol}")
            return cached

        # Fetch from API
        df = self._fetch_from_api(symbol, start, end)

        # Cache for future use
        self.cache.set(symbol, df, source="my_source")

        return df
```

### Using CacheAdapter for Backward Compatibility

```python
from src.cache import cache_adapter

# Old code continues to work
df = cache_adapter.get_market_data("AAPL", "2025-01-01", "2025-01-31")
```

---

## Performance Optimization

### 1. Use Range Queries

Let SQL filter dates instead of loading everything:

```python
# Good: Efficient SQL query
df = cache.get("AAPL", "2025-01-01", "2025-01-31")

# Bad: Loading more than needed
df = cache.get("AAPL", "2024-01-01", "2025-12-31")
df = df[df.index >= "2025-01-01"]  # Filtering in Python
```

### 2. Periodic Maintenance

```python
# Weekly cleanup (cron job)
cache.cleanup_expired()
cache.vacuum()
```

### 3. Monitor Database Size

```python
stats = cache.get_stats()
if stats['db_size_mb'] > 100:  # 100 MB threshold
    logger.warning(f"Cache size: {stats['db_size_mb']} MB")
    cache.cleanup_expired()
    cache.vacuum()
```

---

## Troubleshooting

### Cache Miss (Expected)

```python
df = cache.get("AAPL", "2025-01-01", "2025-01-31")
if df is None:
    # Reasons:
    # 1. Data not cached yet (first request)
    # 2. Data expired (check TTL)
    # 3. Symbol/date range not available
    df = fetch_from_api("AAPL", "2025-01-01", "2025-01-31")
    cache.set("AAPL", df, source="api_name")
```

### Database Locked Error

SQLite serializes writes:

```python
# If you see "database is locked":
# - Reads are concurrent (OK)
# - Writes are serialized (one at a time)
# - Default timeout: 5 seconds

# The cache manager already handles write serialization
# with threading.Lock() - you shouldn't see this error
```

### Corrupted Database

```bash
# Check integrity
sqlite3 .cache/trading_data.db "PRAGMA integrity_check;"

# Rebuild from backup (if backup exists)
mv .cache/trading_data.db .cache/trading_data.db.corrupt
# Restore from .cache/backup_TIMESTAMP/ if available, or delete and let cache rebuild naturally
```

---

## Administrative CLI

### View Statistics

```bash
python scripts/utilities/cache_manager.py stats
```

### Cleanup Expired Entries

```bash
python scripts/utilities/cache_manager.py cleanup
```

### Optimize Database

```bash
python scripts/utilities/cache_manager.py vacuum
```

### List Cached Symbols

```bash
python scripts/utilities/cache_manager.py symbols
```

### Export Data

```bash
python scripts/utilities/cache_manager.py export SPY --start 2025-01-01 --end 2025-12-31
```

### Query Cache

```bash
python scripts/utilities/cache_manager.py query SPY --start 2025-10-01 --end 2025-10-31
```

### Clear Cache

```bash
# Clear specific symbol
python scripts/utilities/cache_manager.py clear --symbol SPY --confirm

# Clear all data (dangerous!): no --symbol means everything
python scripts/utilities/cache_manager.py clear --confirm
```

---

## Best Practices

1. **Always specify `asset_type`** when working with options/futures
2. **Use `source` parameter** to distinguish data from different providers
3. **Let smart expiration work** - only use custom TTL when necessary
4. **Run periodic cleanup** in production (weekly cron job)
5. **Monitor database size** and vacuum when >1GB
6. **Use CacheAdapter** during migration for backward compatibility

---

## Migration from Legacy Cache

> **Note**: The migration script `migrate_cache_to_sqlite.py` has been removed (Issue #435) after completing its purpose. The SQLite cache is now the standard. If you have legacy JSON cache files, simply delete them - the SQLite cache will rebuild automatically from API calls.

### Legacy Migration (Historical Reference)

The migration script performed these operations:

- Backed up all JSON files to `.cache/backup_TIMESTAMP/`
- Converted 3 cache formats: UnifiedCacheManager, MarketDataCache, and raw JSON
- Removed duplicates automatically
- Created `.cache/trading_data.db` SQLite database
- Preserved all metadata (sources, timestamps)

### Step 2: Update Your Code

**Quick migration (minimal changes):**

```python
# Before (UnifiedCacheManager)
from src.data_sources.cache import UnifiedCacheManager
cache = UnifiedCacheManager()
cache.set_market_data(symbol, start, end, source, df)
df = cache.get_market_data(symbol, start, end, source)

# After (TradingCacheManager)
from src.cache import TradingCacheManager
cache = TradingCacheManager()
cache.set(symbol, df, source=source)
df = cache.get(symbol, start, end, source=source)

# Or use CacheAdapter (no code changes)
from src.cache import cache_adapter
df = cache_adapter.get_market_data(symbol, start, end, source)
```

### Step 3: Update Data Sources

**Pattern for data source integration:**

```python
from src.cache import TradingCacheManager

class AlpacaMarketData:
    def __init__(self, cache_manager: Optional[TradingCacheManager] = None):
        self.cache = cache_manager or TradingCacheManager()

    def get_bars(self, symbols, start, end):
        # New cache API
        cached = self.cache.get(symbol, start, end, source="alpaca")

        # After fetching...
        # Remove 'symbol' and 'source' columns before caching
        cache_data = df.drop(columns=['symbol', 'source'], errors='ignore')
        self.cache.set(symbol, cache_data, source="alpaca")
```

**Important:** Drop 'symbol' and 'source' columns before caching (stored as metadata).

### Step 4: Rollback

No longer possible: the file-based `UnifiedCacheManager` has been removed, and `cache_adapter` keeps its old API on top of the SQLite cache.

---

## Testing

### Run Tests

```bash
pytest tests/unit/data_sources/test_sqlite_cache.py tests/unit/test_cache_manager_script.py
```

### Test Coverage

- Basic operations (set/get/filtering)
- Edge cases (empty data, duplicates, date handling)
- Expiration logic (TTL, cleanup)
- Multi-asset support
- Cache management operations
- Concurrent writes (thread safety)
- The admin CLI (`scripts/utilities/cache_manager.py`) against a temporary database

---

## Performance Benchmarks

Query performance (production data):

| Operation | File Cache | SQLite Cache | Improvement |
|-----------|------------|--------------|-------------|
| Single symbol query | 45ms | 5ms | **9x faster** |
| Multi-symbol (10) | 225ms | 25ms | **9x faster** |
| Range query | 180ms | 8ms | **22x faster** |
| Write operation | 12ms | 3ms | **4x faster** |
| Storage size | 2.1 MB (54 files) | 0.5 MB | **4x smaller** |

---

## Multi-Timeframe Support

The cache system supports storing and retrieving data across multiple timeframes for comprehensive technical analysis.

### Supported Timeframes

| Timeframe | Description | Use Case |
|-----------|-------------|----------|
| `1Min` | 1-minute bars | Scalping, intraday |
| `5Min` | 5-minute bars | Fast intraday |
| `15Min` | 15-minute bars | Intraday swing |
| `30Min` | 30-minute bars | Intraday swing |
| `1Hour` | 1-hour bars | Medium-term |
| `2Hour` | 2-hour bars | Swing trading |
| `4Hour` | 4-hour bars | Position trading |
| `1Day` | Daily bars (default) | Position trading |
| `1Week` | Weekly bars | Intermediate-term |
| `1Month` | Monthly bars | Long-term |

### Multi-Timeframe Analysis Example

```python
from src.cache import TradingCacheManager

cache = TradingCacheManager()

# Store data for multiple timeframes
cache.set("AAPL", df_daily, source="alpaca", timeframe="1Day")
cache.set("AAPL", df_hourly, source="alpaca", timeframe="1Hour")
cache.set("AAPL", df_weekly, source="alpaca", timeframe="1Week")

# Multi-timeframe analysis
daily = cache.get("AAPL", "2025-01-01", "2025-01-31", timeframe="1Day")
hourly = cache.get("AAPL", "2025-01-01", "2025-01-31", timeframe="1Hour")
weekly = cache.get("AAPL", "2024-01-01", "2025-01-31", timeframe="1Week")

# Analyze trends across timeframes
print(f"Daily bars: {len(daily)}")
print(f"Hourly bars: {len(hourly)}")
print(f"Weekly bars: {len(weekly)}")
```

### Schema: market_cache Table

The `market_cache` table includes a `timeframe` column for multi-timeframe storage:

```sql
CREATE TABLE market_cache (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    asset_type TEXT NOT NULL DEFAULT 'stock',
    symbol TEXT NOT NULL,
    bar_timestamp TEXT NOT NULL,
    timeframe TEXT NOT NULL DEFAULT '1Day',  -- Multi-timeframe support
    source TEXT NOT NULL,
    open REAL,
    high REAL,
    low REAL,
    close REAL NOT NULL,
    volume INTEGER,
    vwap REAL,
    transactions INTEGER,
    cached_at TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    UNIQUE(asset_type, symbol, bar_timestamp, timeframe, source)
);
```

**Note:** The unique constraint includes `timeframe`, allowing the same symbol/date to have different bars for different timeframes (e.g., daily close vs hourly bars).

---

## Additional Tables (Issue #373, Future Features)

The cache database includes additional tables for analytics and future features:

### Raw Options Chain Storage

```python
from src.cache import TradingCacheManager

cache = TradingCacheManager()

# Store raw options data
cache.store_raw_options(
    symbol="SPY",
    trading_date="2024-01-15",
    options_df=options_data,
    source="polygon"
)

# Retrieve options
options = cache.get_raw_options("SPY", "2024-01-15")
```

### Trade History Analytics

```python
# Archive completed trade
cache.archive_trade({
    'trade_id': 'AAPL_2024-01-15T10:30:00',
    'symbol': 'AAPL',
    'entry_date': '2024-01-15T10:30:00',
    'entry_price': 185.50,
    'quantity': 100,
    'exit_price': 188.20,
    'realized_pnl': 270.00,
    'strategy_name': 'VotingSignal'
})

# Query trade history
trades = cache.get_trade_history(strategy="VotingSignal")

# Get statistics
stats = cache.get_trade_stats(strategy="VotingSignal")
print(f"Win rate: {stats['win_rate_pct']:.1f}%")
```

See [Trade History Database](../02_architecture/trade_history_database.md) for complete API reference.

### Indicator Cache (Future Feature)

Pre-computed technical indicators for faster analysis and multi-timeframe voting:

```sql
CREATE TABLE indicator_cache (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    symbol TEXT NOT NULL,
    timeframe TEXT NOT NULL,           -- '1Min', '1Hour', '1Day', etc.
    indicator_type TEXT NOT NULL,      -- 'MACD', 'RSI', 'SMA', etc.
    bar_timestamp TEXT NOT NULL,
    parameters TEXT,                   -- JSON: {"fast":13,"slow":34,"signal":8}
    value_1 REAL,                      -- Primary value (e.g., MACD line)
    value_2 REAL,                      -- Secondary value (e.g., Signal line)
    value_3 REAL,                      -- Tertiary value (e.g., Histogram)
    signal TEXT,                       -- 'BUY', 'SELL', 'HOLD'
    strength REAL,                     -- 0-100 signal strength
    computed_at TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    UNIQUE(symbol, timeframe, indicator_type, bar_timestamp, parameters)
);
```

**Use cases:**

- Pre-compute MACD/RSI for multiple timeframes
- Multi-timeframe voting strategies
- Faster CLI display with cached indicators
- Reduce redundant calculations

### Signal History (Future Feature)

Track all generated signals for ML training and strategy analysis:

```sql
CREATE TABLE signal_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    signal_id TEXT UNIQUE NOT NULL,    -- UUID for signal tracking
    symbol TEXT NOT NULL,
    timeframe TEXT NOT NULL,
    signal_type TEXT NOT NULL,         -- 'BUY', 'SELL', 'HOLD'
    strategy_name TEXT NOT NULL,       -- 'VotingSignal', 'MACD_RSI', etc.
    confidence REAL,                   -- 0-1 confidence score
    indicators_json TEXT,              -- Full indicator snapshot
    price_at_signal REAL,
    generated_at TEXT NOT NULL,
    executed BOOLEAN DEFAULT FALSE,
    execution_price REAL,
    outcome TEXT,                      -- 'WIN', 'LOSS', 'PENDING', 'SKIPPED'
    outcome_pnl REAL,
    outcome_recorded_at TEXT
);
```

**Use cases:**

- Track signal accuracy over time
- ML model training data
- Strategy backtesting validation
- A/B testing different parameter sets

### Execution Quality (Future Feature)

Track slippage and execution quality for strategy refinement:

```sql
CREATE TABLE execution_quality (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    trade_id TEXT NOT NULL,
    symbol TEXT NOT NULL,
    order_type TEXT,                   -- 'MARKET', 'LIMIT', 'BRACKET'
    expected_price REAL,
    actual_price REAL,
    slippage_pct REAL,
    fill_time_ms INTEGER,
    broker TEXT,                       -- 'alpaca_paper', 'alpaca_live'
    market_condition TEXT,             -- 'NORMAL', 'VOLATILE', 'LOW_LIQUIDITY'
    recorded_at TEXT NOT NULL
);
```

**Use cases:**

- Measure execution quality by order type
- Identify high-slippage conditions
- Optimize order routing
- Broker performance comparison

---

## Related Documentation

- **[Cache System Architecture](../02_architecture/04_cache_system.md)** - Technical design
- **[Trade History Database](../02_architecture/trade_history_database.md)** - Trade analytics API
- **[Troubleshooting](../03_reference/04_troubleshooting.md)** - Common issues
- **Issue #336** - SQLite cache implementation
- **Issue #373** - Multi-provider options and trade history storage

---

*Developer guide for SQLite cache system integration and usage.*
