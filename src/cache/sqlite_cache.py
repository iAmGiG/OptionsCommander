"""
SQLite-based cache for market data.

Replaces file-based caching with efficient relational storage.
Provides drop-in replacement for UnifiedCacheManager with better performance.

Issue #510: Split into mixins for options and trade cache functionality.
"""

import logging
import sqlite3
import threading
import uuid
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional

import pandas as pd

from src.cache.options_cache import OptionsCacheMixin
from src.cache.trade_cache import TradeCacheMixin
from src.utils.date_utils import get_datetime_now, now_iso, parse_date_string
from src.utils.market_hours import market_today, unfinished_session_day
from src.utils.state_paths import get_cache_db_path

LIVE_BAR_SOURCE = "alpaca_live"  # AlpacaMarketData's synthetic bar from the last trade


def _dated_rows(data: pd.DataFrame, timeframe: str) -> pd.DataFrame:
    """
    A copy of the bars with a string 'date' column (a day for daily and longer bars, a full
    timestamp for intraday ones), daily bars limited to the finished ones.

    Raises:
        ValueError: If data has no date column/index
    """
    df = data.copy()

    # Ensure we have a date column: an explicit one wins, else the DatetimeIndex
    # (whatever its name) becomes it
    if isinstance(df.index, pd.DatetimeIndex) and "date" not in df.columns:
        index_name = df.index.name or "index"
        df = df.reset_index().rename(columns={index_name: "date"})
    elif isinstance(df.index, pd.DatetimeIndex):
        df = df.reset_index(drop=True)
    elif "date" not in df.columns:
        raise ValueError("Data must have 'date' column or DatetimeIndex")

    if timeframe in ("1Day", "1Week", "1Month"):
        df["date"] = pd.to_datetime(df["date"]).dt.strftime("%Y-%m-%d")
    else:
        df["date"] = pd.to_datetime(df["date"]).dt.strftime("%Y-%m-%dT%H:%M:%S")
    return _finished_daily_bars(df) if timeframe == "1Day" else df


def _finished_daily_bars(df: pd.DataFrame) -> pd.DataFrame:
    """
    The daily bars that won't change any more (#620): not today's while the session is
    open, and never a synthetic bar made from the last trade. Either would otherwise be
    served later as that day's bar.
    """
    keep = pd.Series(True, index=df.index)
    if "source" in df.columns:
        keep &= df["source"] != LIVE_BAR_SOURCE
    forming = unfinished_session_day()
    if forming is not None:
        keep &= df["date"] != forming
    return df[keep]


logger = logging.getLogger(__name__)


class TradingCacheManager(OptionsCacheMixin, TradeCacheMixin):
    """
    SQLite-based market data cache.

    Features:
    - Efficient range queries (no file loading overhead)
    - ACID transactions for data integrity
    - Concurrent read/write safety
    - Futures-ready schema (asset_type column)
    - Smart expiration (historical data never expires)

    Example:
        >>> cache = TradingCacheManager()
        >>> df = cache.get("SPY", "2024-01-01", "2024-12-31", source="alpaca")
        >>> cache.set("SPY", market_data_df, source="alpaca")
    """

    def __init__(self, db_path: Optional[str] = None):
        """
        Initialize SQLite cache.

        Args:
            db_path: Path to SQLite database file (default: the cache dir's
                trading_data.db, see src/utils/state_paths.py)
        """
        self.db_path = Path(db_path or get_cache_db_path())
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.logger = logging.getLogger(self.__class__.__name__)
        self._write_lock = threading.Lock()  # Serialize write operations
        self._init_database()

    def _init_database(self):
        """Create tables and indexes if they don't exist."""
        with sqlite3.connect(self.db_path) as conn:
            # Create main market data table
            # Supports multiple timeframes: 1m, 5m, 15m, 30m, 1h, 4h, 1d, 1w, 1M, custom
            conn.execute("""
                CREATE TABLE IF NOT EXISTS market_cache (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,

                    -- Asset identification
                    asset_type TEXT NOT NULL DEFAULT 'stock',
                    symbol TEXT NOT NULL,
                    trading_date TEXT NOT NULL,  -- Trading date (YYYY-MM-DD)
                    timeframe TEXT NOT NULL DEFAULT '1Day',  -- e.g., '1Min', '5Min', '1Hour', '1Day', '1Week'
                    source TEXT NOT NULL,

                    -- Price data (OHLCV)
                    open REAL,
                    high REAL,
                    low REAL,
                    close REAL NOT NULL,
                    volume REAL,

                    -- Extended data (optional, for Polygon)
                    vwap REAL,
                    transactions INTEGER,

                    -- Metadata
                    cached_at TEXT NOT NULL,
                    expires_at TEXT NOT NULL,
                    metadata TEXT,

                    -- Unique constraint: one entry per symbol+timestamp+timeframe+source
                    UNIQUE(asset_type, symbol, trading_date, timeframe, source)
                )
            """)

            # Create technical indicators cache table (for pre-computed values)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS indicator_cache (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,

                    -- Key fields
                    symbol TEXT NOT NULL,
                    trading_date TEXT NOT NULL,
                    timeframe TEXT NOT NULL DEFAULT '1Day',
                    indicator_name TEXT NOT NULL,  -- 'macd', 'rsi', 'sma_20', 'ema_50', etc.

                    -- Value fields (flexible for different indicators)
                    value REAL NOT NULL,           -- Primary value (e.g., RSI value, SMA value)
                    value_2 REAL,                  -- Secondary (e.g., MACD signal line)
                    value_3 REAL,                  -- Tertiary (e.g., MACD histogram)

                    -- Metadata
                    params TEXT,                   -- JSON: {"period": 14, "fast": 13, "slow": 34}
                    cached_at TEXT NOT NULL,

                    UNIQUE(symbol, trading_date, timeframe, indicator_name, params)
                )
            """)

            # Create signal history table (for ML training and analysis)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS signal_history (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,

                    -- Signal identification
                    symbol TEXT NOT NULL,
                    signal_timestamp TEXT NOT NULL,
                    timeframe TEXT NOT NULL,

                    -- Signal details
                    signal_type TEXT NOT NULL,     -- 'BUY', 'SELL', 'HOLD'
                    confidence REAL,
                    entry_price REAL,
                    stop_price REAL,
                    target_price REAL,

                    -- Indicators that triggered the signal
                    macd_action TEXT,
                    macd_histogram REAL,
                    rsi_action TEXT,
                    rsi_value REAL,
                    signal_strength TEXT,          -- 'STRONG', 'WEAK', 'CONFLICT'

                    -- Outcome tracking (filled in after the fact)
                    was_executed INTEGER DEFAULT 0,  -- 0=no, 1=yes
                    execution_price REAL,
                    outcome TEXT,                  -- 'WIN', 'LOSS', 'PENDING', NULL
                    actual_return_pct REAL,
                    bars_to_outcome INTEGER,       -- How many bars until stop/target hit

                    -- Metadata
                    strategy_name TEXT,
                    created_at TEXT DEFAULT CURRENT_TIMESTAMP
                )
            """)

            # Create execution quality table (slippage tracking)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS execution_quality (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,

                    -- Order details
                    order_id TEXT NOT NULL,
                    symbol TEXT NOT NULL,
                    order_type TEXT NOT NULL,      -- 'MARKET', 'LIMIT', 'STOP'
                    side TEXT NOT NULL,            -- 'BUY', 'SELL'

                    -- Price comparison
                    expected_price REAL,
                    actual_price REAL,
                    slippage_pct REAL,             -- (actual - expected) / expected * 100

                    -- Timing
                    order_submitted_at TEXT,
                    order_filled_at TEXT,
                    fill_latency_ms INTEGER,

                    -- Market context
                    market_spread REAL,            -- bid-ask spread at time of order
                    market_volume REAL,            -- volume at time of order

                    created_at TEXT DEFAULT CURRENT_TIMESTAMP
                )
            """)

            # Create raw options chain table (Issue #373)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS raw_options_chain (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,

                    -- Asset identification
                    symbol TEXT NOT NULL,
                    trading_date TEXT NOT NULL,

                    -- Option contract details
                    strike REAL NOT NULL,
                    option_type TEXT NOT NULL CHECK(option_type IN ('call', 'put')),
                    expiration TEXT NOT NULL,

                    -- Pricing data
                    bid REAL,
                    ask REAL,
                    last REAL,
                    volume INTEGER,
                    open_interest INTEGER,

                    -- Greeks
                    implied_volatility REAL,
                    delta REAL,
                    gamma REAL,
                    theta REAL,
                    vega REAL,
                    rho REAL,

                    -- Metadata
                    contract_symbol TEXT,
                    underlying_price REAL,
                    source TEXT NOT NULL DEFAULT 'unknown',
                    cached_at TEXT NOT NULL,
                    modified_at TEXT,
                    created_at TEXT DEFAULT CURRENT_TIMESTAMP,

                    -- Provider tracking & data quality
                    data_quality_score REAL DEFAULT 1.0,
                    provider_metadata TEXT,

                    -- Unique constraint
                    UNIQUE(symbol, trading_date, strike, option_type, expiration, source)
                )
            """)

            # Create trade history table (Issue #373 extension - hybrid trade storage)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS trade_history (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,

                    -- Trade identification
                    trade_id TEXT NOT NULL UNIQUE,
                    symbol TEXT NOT NULL,
                    asset_type TEXT DEFAULT 'stock',

                    -- Entry details
                    entry_date TEXT NOT NULL,
                    entry_price REAL NOT NULL,
                    entry_order_id TEXT,
                    quantity INTEGER NOT NULL,

                    -- Exit details
                    exit_date TEXT,
                    exit_price REAL,
                    exit_order_id TEXT,
                    exit_reason TEXT,

                    -- Risk management
                    initial_stop_loss REAL,
                    initial_take_profit REAL,
                    final_stop_loss REAL,
                    final_take_profit REAL,
                    stop_adjustments INTEGER DEFAULT 0,

                    -- Performance metrics
                    realized_pnl REAL,
                    realized_pnl_pct REAL,
                    max_profit_pct REAL,
                    max_drawdown_pct REAL,
                    holding_period_hours REAL,

                    -- Strategy attribution
                    strategy_name TEXT,
                    signal_strength TEXT,
                    signal_confidence REAL,

                    -- Execution quality
                    entry_slippage_pct REAL,
                    exit_slippage_pct REAL,
                    commission_paid REAL,

                    -- Broker tracking
                    broker_account TEXT DEFAULT 'alpaca_paper',

                    -- Metadata
                    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                    notes TEXT
                )
            """)

            # Create indexes for market_cache
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_lookup
                ON market_cache(asset_type, symbol, trading_date)
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_symbol_range
                ON market_cache(symbol, trading_date)
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_expiry
                ON market_cache(expires_at)
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_symbol
                ON market_cache(symbol)
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_source
                ON market_cache(source)
            """)

            # Create indexes for raw_options_chain (Issue #373)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_options_symbol_date
                ON raw_options_chain(symbol, trading_date)
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_options_expiration
                ON raw_options_chain(expiration)
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_options_strike
                ON raw_options_chain(symbol, trading_date, strike)
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_options_source
                ON raw_options_chain(source)
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_options_quality
                ON raw_options_chain(data_quality_score)
            """)

            # Create indexes for trade_history (Issue #373 extension)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_trade_symbol
                ON trade_history(symbol)
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_trade_entry_date
                ON trade_history(entry_date)
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_trade_strategy
                ON trade_history(strategy_name)
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_trade_exit_reason
                ON trade_history(exit_reason)
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_trade_broker
                ON trade_history(broker_account)
            """)

            conn.commit()

            # Migration: Add timeframe column to existing market_cache if missing
            # Run after all tables are created
            self._migrate_add_timeframe(conn)

        self.logger.debug(f"SQLite cache initialized: {self.db_path}")

    def get(
        self,
        symbol: str,
        start: str,
        end: str,
        source: str = None,
        asset_type: str = "stock",
        timeframe: str = "1Day",
    ) -> Optional[pd.DataFrame]:
        """
        Get cached data for date range.

        Automatically filters out expired data. Returns None if no valid cache found.

        Args:
            symbol: Ticker symbol (e.g., "SPY")
            start: Start date in YYYY-MM-DD format (or ISO timestamp for intraday)
            end: End date in YYYY-MM-DD format (or ISO timestamp for intraday)
            source: Data source filter (None = any source)
            asset_type: Asset type (default: "stock")
            timeframe: Bar timeframe (default: "1Day", options: "1Min", "5Min", "1Hour", etc.)

        Returns:
            DataFrame with OHLCV data, or None if not found/expired

        Example:
            >>> df = cache.get("SPY", "2024-01-01", "2024-03-31", source="alpaca")
            >>> if df is not None:
            ...     print(f"Loaded {len(df)} days of data")
        """
        try:
            # Build query with timeframe support
            query = """
                SELECT trading_date, open, high, low, close, volume, vwap, transactions
                FROM market_cache
                WHERE asset_type = ?
                  AND symbol = ?
                  AND trading_date >= ?
                  AND trading_date <= ?
                  AND timeframe = ?
                  AND datetime(expires_at) > datetime('now')
            """
            params = [asset_type, symbol, start, end, timeframe]

            if source:
                query += " AND source = ?"
                params.append(source)

            query += " ORDER BY trading_date ASC"

            with sqlite3.connect(self.db_path) as conn:
                df = pd.read_sql_query(query, conn, params=params)

            if df.empty:
                self.logger.debug(f"Cache miss: {symbol} ({start} to {end})")
                return None

            # Convert date column to datetime and set as index
            df["trading_date"] = pd.to_datetime(df["trading_date"])
            df = df.set_index("trading_date")
            df.index.name = "date"  # Rename index to match expected format

            # Drop columns that are all NULL (e.g., vwap/transactions from Alpha Vantage)
            df = df.dropna(axis=1, how="all")

            self.logger.debug(
                f"Cache hit: {symbol} ({start} to {end}) - {len(df)} days from {source or 'any source'}"
            )
            return df

        except Exception as e:
            self.logger.error(f"Error reading cache for {symbol}: {e}")
            return None

    def set(
        self,
        symbol: str,
        data: pd.DataFrame,
        source: str,
        asset_type: str = "stock",
        ttl_hours: int = None,
        timeframe: str = "1Day",
    ):
        """
        Cache market data.

        Stores OHLCV data with smart expiration:
        - Historical data (>2 days old): 10 years (effectively never expires)
        - Recent data (≤2 days): 24 hours (needs fresh updates)

        Args:
            symbol: Ticker symbol
            data: DataFrame with OHLCV data (must have 'date' column or DatetimeIndex)
            source: Data source ("alpaca", "polygon", "alpha_vantage")
            asset_type: Asset type (default: "stock")
            ttl_hours: Custom TTL in hours (None = auto-calculate)
            timeframe: Bar timeframe (default: "1Day", options: "1Min", "5Min", "1Hour", etc.)

        Raises:
            ValueError: If data has no date column/index

        Example:
            >>> df = pd.DataFrame({
            ...     'date': ['2024-01-01', '2024-01-02'],
            ...     'close': [100.0, 101.0],
            ...     'volume': [1000, 1100]
            ... })
            >>> cache.set("SPY", df, source="alpaca")
        """
        if data.empty:
            self.logger.debug(f"Skipping empty data for {symbol}")
            return

        try:
            df = _dated_rows(data, timeframe)
            if df.empty:
                self.logger.debug(f"Nothing finished to cache for {symbol}")
                return

            # Add metadata columns
            df["asset_type"] = asset_type
            df["symbol"] = symbol
            df["source"] = source
            df["timeframe"] = timeframe  # Multi-timeframe support
            df["cached_at"] = now_iso()

            # Calculate smart expiration for each row
            if ttl_hours:
                # Custom TTL
                expires_at = get_datetime_now() + timedelta(hours=ttl_hours)
                df["expires_at"] = expires_at.isoformat()
            else:
                # Smart TTL based on data recency
                df["expires_at"] = df["date"].apply(
                    lambda d: self._calculate_expiration(d, d).isoformat()
                )

            # Rename date column to trading_date for DB
            df = df.rename(columns={"date": "trading_date"})

            # Select columns that exist in schema
            base_columns = [
                "asset_type",
                "symbol",
                "trading_date",
                "timeframe",
                "source",
                "cached_at",
                "expires_at",
            ]
            price_columns = ["open", "high", "low", "close", "volume", "vwap", "transactions"]

            # Include only columns that exist in the data
            columns_to_save = base_columns + [col for col in price_columns if col in df.columns]
            df_to_save = df[columns_to_save]

            # Ensure close column exists (required)
            if "close" not in df_to_save.columns:
                raise ValueError("Data must have 'close' column")

            # Bulk insert using pandas to_sql (much faster than row-by-row)
            # Use lock to prevent concurrent write conflicts with temp table
            with self._write_lock:
                # Use unique temp table name to avoid conflicts
                temp_table = f"market_cache_temp_{uuid.uuid4().hex[:8]}"

                with sqlite3.connect(self.db_path) as conn:
                    # Use INSERT OR REPLACE for idempotent caching
                    df_to_save.to_sql(temp_table, conn, if_exists="replace", index=False)

                    # Build column list for INSERT (exclude id which is auto-increment)
                    columns_list = ", ".join(columns_to_save)

                    # Copy to main table with INSERT OR REPLACE
                    conn.execute(f"""
                        INSERT OR REPLACE INTO market_cache
                        ({columns_list})
                        SELECT {columns_list} FROM {temp_table}
                    """)  # nosec B608

                    # Drop temp table
                    conn.execute(f"DROP TABLE {temp_table}")
                    conn.commit()

            self.logger.debug(
                f"Cached {len(df_to_save)} days for {symbol} ({df_to_save['trading_date'].min()} to {df_to_save['trading_date'].max()}) from {source}"
            )

        except Exception as e:
            self.logger.error(f"Error caching data for {symbol}: {e}", exc_info=True)
            raise

    def exists(
        self, symbol: str, start: str, end: str, source: str = None, asset_type: str = "stock"
    ) -> bool:
        """
        Check if data exists (even if expired).

        Args:
            symbol: Ticker symbol
            start: Start date (YYYY-MM-DD)
            end: End date (YYYY-MM-DD)
            source: Data source filter (None = any source)
            asset_type: Asset type (default: "stock")

        Returns:
            True if any data exists for the range (expired or not)
        """
        try:
            query = """
                SELECT COUNT(*) as count
                FROM market_cache
                WHERE asset_type = ?
                  AND symbol = ?
                  AND trading_date >= ?
                  AND trading_date <= ?
            """
            params = [asset_type, symbol, start, end]

            if source:
                query += " AND source = ?"
                params.append(source)

            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.execute(query, params)
                count = cursor.fetchone()[0]

            return count > 0

        except Exception as e:
            self.logger.error(f"Error checking existence for {symbol}: {e}")
            return False

    def delete(
        self,
        symbol: str,
        start: str = None,
        end: str = None,
        source: str = None,
        asset_type: str = "stock",
    ) -> int:
        """
        Delete cached data.

        Args:
            symbol: Ticker symbol
            start: Start date (None = all dates)
            end: End date (None = all dates)
            source: Data source filter (None = all sources)
            asset_type: Asset type (default: "stock")

        Returns:
            Number of rows deleted
        """
        try:
            query = "DELETE FROM market_cache WHERE asset_type = ? AND symbol = ?"
            params = [asset_type, symbol]

            if start:
                query += " AND trading_date >= ?"
                params.append(start)

            if end:
                query += " AND trading_date <= ?"
                params.append(end)

            if source:
                query += " AND source = ?"
                params.append(source)

            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.execute(query, params)
                deleted = cursor.rowcount
                conn.commit()

            self.logger.debug(f"Deleted {deleted} rows for {symbol}")
            return deleted

        except Exception as e:
            self.logger.error(f"Error deleting data for {symbol}: {e}")
            return 0

    def cleanup_expired(self) -> int:
        """
        Remove expired cache entries.

        Returns:
            Number of rows deleted
        """
        try:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.execute("""
                    DELETE FROM market_cache
                    WHERE datetime(expires_at) <= datetime('now')
                """)
                deleted = cursor.rowcount
                conn.commit()

            if deleted > 0:
                self.logger.info(f"Cleaned up {deleted} expired cache entries")

            return deleted

        except Exception as e:
            self.logger.error(f"Error cleaning up expired cache: {e}")
            return 0

    def get_stats(self) -> Dict[str, Any]:
        """
        Get cache statistics.

        Returns:
            Dictionary with cache metrics:
            - total_entries: Total number of cached days
            - unique_symbols: Number of unique symbols
            - sources: Breakdown by data source
            - asset_types: Breakdown by asset type
            - date_range: Min and max dates
            - db_size_mb: Database file size in MB
        """
        try:
            with sqlite3.connect(self.db_path) as conn:
                # Total entries
                total = conn.execute("SELECT COUNT(*) FROM market_cache").fetchone()[0]

                # Unique symbols
                unique_symbols = conn.execute(
                    "SELECT COUNT(DISTINCT symbol) FROM market_cache"
                ).fetchone()[0]

                # Breakdown by source
                sources = {}
                for row in conn.execute(
                    "SELECT source, COUNT(*) as count FROM market_cache GROUP BY source"
                ):
                    sources[row[0]] = row[1]

                # Breakdown by asset type
                asset_types = {}
                for row in conn.execute(
                    "SELECT asset_type, COUNT(*) as count FROM market_cache GROUP BY asset_type"
                ):
                    asset_types[row[0]] = row[1]

                # Date range
                date_range = conn.execute(
                    "SELECT MIN(trading_date), MAX(trading_date) FROM market_cache"
                ).fetchone()

                # Expired entries
                expired = conn.execute("""
                    SELECT COUNT(*) FROM market_cache
                    WHERE datetime(expires_at) <= datetime('now')
                """).fetchone()[0]

            # Database file size
            db_size_mb = self.db_path.stat().st_size / (1024 * 1024) if self.db_path.exists() else 0

            return {
                "total_entries": total,
                "unique_symbols": unique_symbols,
                "sources": sources,
                "asset_types": asset_types,
                "date_range": (
                    {"min_date": date_range[0], "max_date": date_range[1]}
                    if date_range[0]
                    else None
                ),
                "expired_entries": expired,
                "db_size_mb": round(db_size_mb, 2),
                "db_path": str(self.db_path),
            }

        except Exception as e:
            self.logger.error(f"Error getting cache stats: {e}")
            return {}

    def _calculate_expiration(self, start_date: str, end_date: str) -> datetime:
        """
        Calculate smart expiration based on data recency.

        Historical data (>2 days old): 10 years (effectively never expires)
        Recent data (≤2 days): 24 hours (needs fresh updates)

        Args:
            start_date: Start date (YYYY-MM-DD)
            end_date: End date (YYYY-MM-DD)

        Returns:
            Expiration datetime
        """
        try:
            end_dt = parse_date_string(end_date)
            today = pd.Timestamp(market_today()).date()  # the ET date (#620)

            # Historical data should never practically expire
            if end_dt.date() < today - timedelta(days=2):
                return get_datetime_now() + timedelta(days=365 * 10)  # 10 years

            # Recent data needs fresh updates
            else:
                return get_datetime_now() + timedelta(hours=24)

        except ValueError:
            # Fallback to short expiration if date parsing fails
            return get_datetime_now() + timedelta(hours=24)

    def get_symbols(self, asset_type: str = "stock") -> List[str]:
        """
        Get list of all cached symbols.

        Args:
            asset_type: Asset type filter (default: "stock")

        Returns:
            List of unique symbols
        """
        try:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.execute(
                    "SELECT DISTINCT symbol FROM market_cache WHERE asset_type = ? ORDER BY symbol",
                    [asset_type],
                )
                return [row[0] for row in cursor.fetchall()]

        except Exception as e:
            self.logger.error(f"Error getting symbols: {e}")
            return []

    def vacuum(self):
        """
        Optimize database (reclaim space after deletions).

        Run this after cleanup_expired() or large deletions.
        """
        try:
            with sqlite3.connect(self.db_path) as conn:
                conn.execute("VACUUM")

            self.logger.info("Database optimized (VACUUM completed)")

        except Exception as e:
            self.logger.error(f"Error vacuuming database: {e}")

    # === OPTIONS AND TRADE METHODS: See OptionsCacheMixin and TradeCacheMixin (Issue #510) ===

    def _migrate_add_timeframe(self, conn: sqlite3.Connection):
        """
        Migrate existing market_cache table to add timeframe support.

        This migration:
        1. Adds 'timeframe' column if missing (default: '1Day')
        2. Renames 'trading_date' to 'bar_timestamp' if needed
        3. Creates new indexes for timeframe-aware queries

        Args:
            conn: Active database connection
        """
        try:
            # Check if timeframe column exists
            cursor = conn.execute("PRAGMA table_info(market_cache)")
            columns = {row[1] for row in cursor.fetchall()}

            if "timeframe" not in columns:
                self.logger.info("Migrating market_cache: adding timeframe column...")
                conn.execute(
                    "ALTER TABLE market_cache ADD COLUMN timeframe TEXT NOT NULL DEFAULT '1Day'"
                )
                self.logger.info("Added 'timeframe' column with default '1Day'")

            # Check if we need to rename trading_date to bar_timestamp
            if "trading_date" in columns and "bar_timestamp" not in columns:
                # SQLite doesn't support column rename easily, so we use bar_timestamp as alias
                # The column stays as trading_date but new code uses bar_timestamp
                self.logger.debug("trading_date column will be treated as bar_timestamp")

            # Create new indexes for timeframe-aware queries
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_timeframe_lookup
                ON market_cache(symbol, timeframe, trading_date)
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_timeframe_source
                ON market_cache(symbol, timeframe, source)
            """)

            # Create indexes for new tables
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_indicator_lookup
                ON indicator_cache(symbol, timeframe, indicator_name, trading_date)
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_signal_lookup
                ON signal_history(symbol, timeframe, signal_timestamp)
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_signal_outcome
                ON signal_history(outcome, strategy_name)
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_execution_order
                ON execution_quality(order_id)
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_execution_symbol
                ON execution_quality(symbol, order_submitted_at)
            """)

            conn.commit()

        except Exception as e:
            self.logger.error(f"Migration error: {e}")
            # Don't raise - allow operation to continue with existing schema
