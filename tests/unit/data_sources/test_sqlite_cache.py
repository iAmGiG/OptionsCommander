"""
Unit tests for TradingCacheManager (SQLite Cache - Issue #408 Priority 5).

Tests SQLite-based market data caching:
- Database initialization
- Get/set operations
- Cache expiration
- Data retrieval with range queries
- Cache statistics
- Cleanup operations
"""

from datetime import datetime, timedelta
from pathlib import Path

import pandas as pd
import pytest


class TestTradingCacheManager:
    """Tests for TradingCacheManager class."""

    @pytest.fixture
    def temp_db(self, tmp_path):
        """Create a temporary database path."""
        return str(tmp_path / "test_cache.db")

    @pytest.fixture
    def cache_manager(self, temp_db):
        """Create a TradingCacheManager with temporary database."""
        from src.cache.sqlite_cache import TradingCacheManager

        return TradingCacheManager(db_path=temp_db)

    @pytest.fixture
    def sample_dataframe(self):
        """Create sample OHLCV data."""
        dates = pd.date_range(start="2024-01-01", periods=5, freq="D")
        return pd.DataFrame(
            {
                "date": dates,
                "open": [100.0, 101.0, 102.0, 103.0, 104.0],
                "high": [105.0, 106.0, 107.0, 108.0, 109.0],
                "low": [99.0, 100.0, 101.0, 102.0, 103.0],
                "close": [104.0, 105.0, 106.0, 107.0, 108.0],
                "volume": [1000000, 1100000, 1200000, 1300000, 1400000],
            }
        )


class TestDatabaseInitialization(TestTradingCacheManager):
    """Tests for database initialization."""

    def test_creates_database_file(self, temp_db):
        """Test that database file is created."""
        from src.cache.sqlite_cache import TradingCacheManager

        TradingCacheManager(db_path=temp_db)

        assert Path(temp_db).exists()

    def test_creates_parent_directories(self, tmp_path):
        """Test that parent directories are created."""
        from src.cache.sqlite_cache import TradingCacheManager

        db_path = str(tmp_path / "nested" / "dir" / "test.db")
        TradingCacheManager(db_path=db_path)

        assert Path(db_path).exists()

    def test_creates_market_cache_table(self, cache_manager, temp_db):
        """Test that market_cache table is created."""
        import sqlite3

        with sqlite3.connect(temp_db) as conn:
            cursor = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='market_cache'"
            )
            result = cursor.fetchone()

        assert result is not None
        assert result[0] == "market_cache"

    def test_creates_raw_options_chain_table(self, cache_manager, temp_db):
        """Test that raw_options_chain table is created."""
        import sqlite3

        with sqlite3.connect(temp_db) as conn:
            cursor = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='raw_options_chain'"
            )
            result = cursor.fetchone()

        assert result is not None

    def test_creates_trade_history_table(self, cache_manager, temp_db):
        """Test that trade_history table is created."""
        import sqlite3

        with sqlite3.connect(temp_db) as conn:
            cursor = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='trade_history'"
            )
            result = cursor.fetchone()

        assert result is not None

    def test_creates_indexes(self, cache_manager, temp_db):
        """Test that indexes are created."""
        import sqlite3

        with sqlite3.connect(temp_db) as conn:
            cursor = conn.execute("SELECT name FROM sqlite_master WHERE type='index'")
            indexes = [row[0] for row in cursor.fetchall()]

        assert "idx_lookup" in indexes
        assert "idx_symbol_range" in indexes
        assert "idx_expiry" in indexes


class TestSetOperation(TestTradingCacheManager):
    """Tests for set (cache) operation."""

    def test_set_dataframe_with_date_column(self, cache_manager, sample_dataframe):
        """Test caching DataFrame with date column."""
        cache_manager.set("SPY", sample_dataframe, source="alpaca")

        result = cache_manager.get("SPY", "2024-01-01", "2024-01-05", source="alpaca")

        assert result is not None
        assert len(result) == 5

    @pytest.mark.parametrize("index_name", ["timestamp", "trading_date", "date"])
    def test_a_named_datetime_index_becomes_the_date(self, cache_manager, index_name):
        dates = pd.date_range(start="2024-01-01", periods=3, freq="D", name=index_name)
        df = pd.DataFrame({"close": [100.0, 101.0, 102.0]}, index=dates)

        cache_manager.set("AAPL", df, source="alpaca")

        result = cache_manager.get("AAPL", "2024-01-01", "2024-01-03", source="alpaca")
        assert result is not None and list(result["close"]) == [100.0, 101.0, 102.0]

    def test_a_date_column_next_to_a_date_index_is_used(self, cache_manager):
        """A frame read back from get() (index named "date") with a date column added."""
        dates = pd.date_range(start="2024-01-01", periods=2, freq="D", name="date")
        df = pd.DataFrame({"close": [100.0, 101.0]}, index=dates)
        df["date"] = ["2024-01-01", "2024-01-02"]

        cache_manager.set("AAPL", df, source="alpaca")

        result = cache_manager.get("AAPL", "2024-01-01", "2024-01-02", source="alpaca")
        assert result is not None and list(result["close"]) == [100.0, 101.0]

    def test_set_dataframe_with_datetime_index(self, cache_manager):
        """Test caching DataFrame with DatetimeIndex."""
        dates = pd.date_range(start="2024-01-01", periods=3, freq="D")
        df = pd.DataFrame(
            {
                "close": [100.0, 101.0, 102.0],
                "volume": [1000, 1100, 1200],
            },
            index=dates,
        )

        cache_manager.set("AAPL", df, source="polygon")

        result = cache_manager.get("AAPL", "2024-01-01", "2024-01-03", source="polygon")

        assert result is not None
        assert len(result) == 3

    def test_set_empty_dataframe_skipped(self, cache_manager):
        """Test that empty DataFrame is skipped."""
        df = pd.DataFrame()

        # Should not raise an error
        cache_manager.set("SPY", df, source="alpaca")

        # No data should be cached
        assert not cache_manager.exists("SPY", "2024-01-01", "2024-12-31")

    def test_set_without_close_column_raises(self, cache_manager):
        """Test that missing close column raises error."""
        df = pd.DataFrame(
            {
                "date": ["2024-01-01"],
                "open": [100.0],
                "volume": [1000],
            }
        )

        with pytest.raises(ValueError, match="close"):
            cache_manager.set("SPY", df, source="alpaca")

    def test_set_without_date_raises(self, cache_manager):
        """Test that missing date column/index raises error."""
        df = pd.DataFrame(
            {
                "close": [100.0],
                "volume": [1000],
            }
        )

        with pytest.raises(ValueError, match="date"):
            cache_manager.set("SPY", df, source="alpaca")

    def test_set_overwrites_existing_data(self, cache_manager):
        """Test that setting data overwrites existing entries."""
        df1 = pd.DataFrame(
            {
                "date": ["2024-01-01"],
                "close": [100.0],
            }
        )
        df2 = pd.DataFrame(
            {
                "date": ["2024-01-01"],
                "close": [200.0],
            }
        )

        cache_manager.set("SPY", df1, source="alpaca")
        cache_manager.set("SPY", df2, source="alpaca")

        result = cache_manager.get("SPY", "2024-01-01", "2024-01-01", source="alpaca")

        assert result is not None
        assert result["close"].iloc[0] == 200.0

    def test_set_with_custom_ttl(self, cache_manager, sample_dataframe):
        """Test caching with custom TTL."""
        # Use a long TTL to ensure data is not expired during test
        cache_manager.set("SPY", sample_dataframe, source="alpaca", ttl_hours=24 * 365)

        result = cache_manager.get("SPY", "2024-01-01", "2024-01-05", source="alpaca")

        assert result is not None

    def test_set_with_vwap_and_transactions(self, cache_manager):
        """Test caching DataFrame with extended columns."""
        df = pd.DataFrame(
            {
                "date": ["2024-01-01"],
                "open": [100.0],
                "high": [105.0],
                "low": [99.0],
                "close": [104.0],
                "volume": [1000000],
                "vwap": [102.5],
                "transactions": [5000],
            }
        )

        cache_manager.set("SPY", df, source="polygon")

        result = cache_manager.get("SPY", "2024-01-01", "2024-01-01", source="polygon")

        assert result is not None
        assert "vwap" in result.columns
        assert "transactions" in result.columns


class TestGetOperation(TestTradingCacheManager):
    """Tests for get (retrieve) operation."""

    def test_get_existing_data(self, cache_manager, sample_dataframe):
        """Test retrieving existing cached data."""
        cache_manager.set("SPY", sample_dataframe, source="alpaca")

        result = cache_manager.get("SPY", "2024-01-01", "2024-01-05", source="alpaca")

        assert result is not None
        assert len(result) == 5
        assert "close" in result.columns

    def test_get_nonexistent_data_returns_none(self, cache_manager):
        """Test retrieving non-existent data returns None."""
        result = cache_manager.get("NONEXISTENT", "2024-01-01", "2024-12-31")

        assert result is None

    def test_get_partial_range(self, cache_manager, sample_dataframe):
        """Test retrieving partial date range."""
        cache_manager.set("SPY", sample_dataframe, source="alpaca")

        result = cache_manager.get("SPY", "2024-01-02", "2024-01-04", source="alpaca")

        assert result is not None
        assert len(result) == 3

    def test_get_with_source_filter(self, cache_manager, sample_dataframe):
        """Test retrieving with source filter."""
        cache_manager.set("SPY", sample_dataframe, source="alpaca")

        # Should find data with matching source
        result_alpaca = cache_manager.get("SPY", "2024-01-01", "2024-01-05", source="alpaca")
        assert result_alpaca is not None

        # Should not find data with different source
        result_polygon = cache_manager.get("SPY", "2024-01-01", "2024-01-05", source="polygon")
        assert result_polygon is None

    def test_get_without_source_filter(self, cache_manager, sample_dataframe):
        """Test retrieving without source filter."""
        cache_manager.set("SPY", sample_dataframe, source="alpaca")

        result = cache_manager.get("SPY", "2024-01-01", "2024-01-05", source=None)

        assert result is not None

    def test_get_result_has_datetime_index(self, cache_manager, sample_dataframe):
        """Test that result has DatetimeIndex."""
        cache_manager.set("SPY", sample_dataframe, source="alpaca")

        result = cache_manager.get("SPY", "2024-01-01", "2024-01-05", source="alpaca")

        assert isinstance(result.index, pd.DatetimeIndex)
        assert result.index.name == "date"

    def test_get_drops_null_columns(self, cache_manager):
        """Test that columns with all nulls are dropped."""
        df = pd.DataFrame(
            {
                "date": ["2024-01-01"],
                "close": [100.0],
                "volume": [1000],
            }
        )

        cache_manager.set("SPY", df, source="alpaca")

        result = cache_manager.get("SPY", "2024-01-01", "2024-01-01", source="alpaca")

        # vwap and transactions columns should be dropped (all null)
        assert "vwap" not in result.columns


class TestExistsOperation(TestTradingCacheManager):
    """Tests for exists operation."""

    def test_exists_returns_true_for_cached_data(self, cache_manager, sample_dataframe):
        """Test exists returns True for cached data."""
        cache_manager.set("SPY", sample_dataframe, source="alpaca")

        assert cache_manager.exists("SPY", "2024-01-01", "2024-01-05")

    def test_exists_returns_false_for_missing_data(self, cache_manager):
        """Test exists returns False for missing data."""
        assert not cache_manager.exists("NONEXISTENT", "2024-01-01", "2024-12-31")

    def test_exists_with_source_filter(self, cache_manager, sample_dataframe):
        """Test exists with source filter."""
        cache_manager.set("SPY", sample_dataframe, source="alpaca")

        assert cache_manager.exists("SPY", "2024-01-01", "2024-01-05", source="alpaca")
        assert not cache_manager.exists("SPY", "2024-01-01", "2024-01-05", source="polygon")

    def test_exists_includes_expired_data(self, cache_manager):
        """Test exists includes expired data."""
        df = pd.DataFrame(
            {
                "date": ["2024-01-01"],
                "close": [100.0],
            }
        )

        # Set with very short TTL
        cache_manager.set("SPY", df, source="alpaca", ttl_hours=1)

        # Manually expire the data
        import sqlite3

        with sqlite3.connect(cache_manager.db_path) as conn:
            conn.execute("UPDATE market_cache SET expires_at = '2020-01-01T00:00:00'")
            conn.commit()

        # exists should still return True (it checks existence, not validity)
        assert cache_manager.exists("SPY", "2024-01-01", "2024-01-01")


class TestDeleteOperation(TestTradingCacheManager):
    """Tests for delete operation."""

    def test_delete_all_data_for_symbol(self, cache_manager, sample_dataframe):
        """Test deleting all data for a symbol."""
        cache_manager.set("SPY", sample_dataframe, source="alpaca")

        deleted = cache_manager.delete("SPY")

        assert deleted == 5
        assert not cache_manager.exists("SPY", "2024-01-01", "2024-01-05")

    def test_delete_with_date_range(self, cache_manager, sample_dataframe):
        """Test deleting data within date range."""
        cache_manager.set("SPY", sample_dataframe, source="alpaca")

        deleted = cache_manager.delete("SPY", start="2024-01-01", end="2024-01-02")

        assert deleted == 2
        assert cache_manager.exists("SPY", "2024-01-03", "2024-01-05")

    def test_delete_with_source_filter(self, cache_manager, sample_dataframe):
        """Test deleting data with source filter."""
        cache_manager.set("SPY", sample_dataframe, source="alpaca")

        deleted = cache_manager.delete("SPY", source="polygon")
        assert deleted == 0

        deleted = cache_manager.delete("SPY", source="alpaca")
        assert deleted == 5

    def test_delete_nonexistent_returns_zero(self, cache_manager):
        """Test deleting non-existent data returns 0."""
        deleted = cache_manager.delete("NONEXISTENT")

        assert deleted == 0


class TestCleanupExpired(TestTradingCacheManager):
    """Tests for cleanup_expired operation."""

    def test_cleanup_removes_expired_entries(self, cache_manager):
        """Test cleanup removes expired entries."""
        import sqlite3

        df = pd.DataFrame(
            {
                "date": ["2024-01-01", "2024-01-02"],
                "close": [100.0, 101.0],
            }
        )

        cache_manager.set("SPY", df, source="alpaca")

        # Manually expire one entry
        with sqlite3.connect(cache_manager.db_path) as conn:
            conn.execute(
                "UPDATE market_cache SET expires_at = '2020-01-01T00:00:00' WHERE trading_date = '2024-01-01'"
            )
            conn.commit()

        deleted = cache_manager.cleanup_expired()

        assert deleted == 1

    def test_cleanup_keeps_valid_entries(self, cache_manager, sample_dataframe):
        """Test cleanup keeps valid (non-expired) entries."""
        cache_manager.set("SPY", sample_dataframe, source="alpaca", ttl_hours=24 * 365)

        deleted = cache_manager.cleanup_expired()

        assert deleted == 0
        assert cache_manager.exists("SPY", "2024-01-01", "2024-01-05")


class TestGetStats(TestTradingCacheManager):
    """Tests for get_stats operation."""

    def test_get_stats_empty_cache(self, cache_manager):
        """Test stats for empty cache."""
        stats = cache_manager.get_stats()

        assert stats["total_entries"] == 0
        assert stats["unique_symbols"] == 0

    def test_get_stats_with_data(self, cache_manager, sample_dataframe):
        """Test stats with cached data."""
        cache_manager.set("SPY", sample_dataframe, source="alpaca")

        stats = cache_manager.get_stats()

        assert stats["total_entries"] == 5
        assert stats["unique_symbols"] == 1
        assert "alpaca" in stats["sources"]
        assert stats["sources"]["alpaca"] == 5

    def test_get_stats_multiple_symbols(self, cache_manager, sample_dataframe):
        """Test stats with multiple symbols."""
        cache_manager.set("SPY", sample_dataframe, source="alpaca")
        cache_manager.set("QQQ", sample_dataframe, source="alpaca")

        stats = cache_manager.get_stats()

        assert stats["unique_symbols"] == 2
        assert stats["total_entries"] == 10

    def test_get_stats_multiple_sources(self, cache_manager, sample_dataframe):
        """Test stats with multiple sources."""
        cache_manager.set("SPY", sample_dataframe, source="alpaca")
        cache_manager.set("SPY", sample_dataframe, source="polygon")

        stats = cache_manager.get_stats()

        assert "alpaca" in stats["sources"]
        assert "polygon" in stats["sources"]

    def test_get_stats_includes_db_size(self, cache_manager, sample_dataframe):
        """Test stats include database size."""
        cache_manager.set("SPY", sample_dataframe, source="alpaca")

        stats = cache_manager.get_stats()

        assert "db_size_mb" in stats
        assert stats["db_size_mb"] >= 0


class TestGetSymbols(TestTradingCacheManager):
    """Tests for get_symbols operation."""

    def test_get_symbols_empty_cache(self, cache_manager):
        """Test get_symbols with empty cache."""
        symbols = cache_manager.get_symbols()

        assert symbols == []

    def test_get_symbols_returns_unique(self, cache_manager, sample_dataframe):
        """Test get_symbols returns unique symbols."""
        cache_manager.set("SPY", sample_dataframe, source="alpaca")
        cache_manager.set("QQQ", sample_dataframe, source="alpaca")
        cache_manager.set("AAPL", sample_dataframe, source="alpaca")

        symbols = cache_manager.get_symbols()

        assert len(symbols) == 3
        assert "SPY" in symbols
        assert "QQQ" in symbols
        assert "AAPL" in symbols

    def test_get_symbols_sorted(self, cache_manager, sample_dataframe):
        """Test get_symbols returns sorted list."""
        cache_manager.set("QQQ", sample_dataframe, source="alpaca")
        cache_manager.set("AAPL", sample_dataframe, source="alpaca")
        cache_manager.set("SPY", sample_dataframe, source="alpaca")

        symbols = cache_manager.get_symbols()

        assert symbols == ["AAPL", "QQQ", "SPY"]


class TestVacuum(TestTradingCacheManager):
    """Tests for vacuum operation."""

    def test_vacuum_completes_without_error(self, cache_manager, sample_dataframe):
        """Test vacuum completes without error."""
        cache_manager.set("SPY", sample_dataframe, source="alpaca")
        cache_manager.delete("SPY")

        # Should not raise
        cache_manager.vacuum()


class TestCacheExpiration(TestTradingCacheManager):
    """Tests for smart cache expiration logic."""

    def test_historical_data_long_expiration(self, cache_manager):
        """Test historical data gets long expiration."""
        # Data from 30 days ago
        old_date = (datetime.now() - timedelta(days=30)).strftime("%Y-%m-%d")
        df = pd.DataFrame(
            {
                "date": [old_date],
                "close": [100.0],
            }
        )

        cache_manager.set("SPY", df, source="alpaca")

        # Data should not be expired
        result = cache_manager.get("SPY", old_date, old_date, source="alpaca")
        assert result is not None

    def test_recent_data_short_expiration(self, cache_manager):
        """Test recent data gets shorter expiration."""
        # Yesterday in ET: recent (at most 2 days old) and always a finished session. Today's
        # row is dropped until that day's session closes (#620), so "today" made this test fail
        # on every trading day before 16:00 ET.
        from src.utils.market_hours import market_today

        day = (pd.Timestamp(market_today()) - pd.Timedelta(days=1)).strftime("%Y-%m-%d")
        df = pd.DataFrame(
            {
                "date": [day],
                "close": [100.0],
            }
        )

        cache_manager.set("SPY", df, source="alpaca")

        # Data should exist
        result = cache_manager.get("SPY", day, day, source="alpaca")
        assert result is not None


class TestAssetTypes(TestTradingCacheManager):
    """Tests for different asset types."""

    def test_stock_asset_type(self, cache_manager, sample_dataframe):
        """Test caching with stock asset type."""
        cache_manager.set("SPY", sample_dataframe, source="alpaca", asset_type="stock")

        result = cache_manager.get(
            "SPY", "2024-01-01", "2024-01-05", source="alpaca", asset_type="stock"
        )

        assert result is not None

    def test_different_asset_types_separate(self, cache_manager, sample_dataframe):
        """Test different asset types are stored separately."""
        cache_manager.set("SPY", sample_dataframe, source="alpaca", asset_type="stock")
        cache_manager.set("SPY", sample_dataframe, source="alpaca", asset_type="futures")

        stock_result = cache_manager.get(
            "SPY", "2024-01-01", "2024-01-05", source="alpaca", asset_type="stock"
        )
        futures_result = cache_manager.get(
            "SPY", "2024-01-01", "2024-01-05", source="alpaca", asset_type="futures"
        )

        assert stock_result is not None
        assert futures_result is not None


class TestThreadSafety(TestTradingCacheManager):
    """Tests for thread safety."""

    def test_concurrent_writes(self, cache_manager):
        """Test concurrent write operations."""
        import threading

        df = pd.DataFrame(
            {
                "date": ["2024-01-01"],
                "close": [100.0],
            }
        )

        errors = []

        def write_data(symbol):
            try:
                for _ in range(5):
                    cache_manager.set(symbol, df, source="alpaca")
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=write_data, args=(f"SYM{i}",)) for i in range(5)]

        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert len(errors) == 0
