"""
Alpaca Market Data API integration using official alpaca-py SDK.

This implementation replaces the raw REST approach with the official SDK
for better error handling, automatic pagination, and proper data parsing.
"""

import logging
from typing import Any, Dict, List, Optional

import pandas as pd

# Smart cache logic: Live data is priority for current trading day
from src.utils.date_utils import format_data_status
from src.utils.market_hours import is_market_hours, market_today

try:
    from alpaca.data import StockHistoricalDataClient
    from alpaca.data.models import Bar, Quote, Snapshot, Trade
    from alpaca.data.requests import (
        StockBarsRequest,
        StockLatestQuoteRequest,
        StockLatestTradeRequest,
        StockSnapshotRequest,
    )
    from alpaca.data.timeframe import TimeFrame, TimeFrameUnit

    ALPACA_AVAILABLE = True
except ImportError:
    StockHistoricalDataClient = None
    StockBarsRequest = None
    StockLatestQuoteRequest = None
    StockLatestTradeRequest = None
    StockSnapshotRequest = None
    TimeFrame = None
    TimeFrameUnit = None
    Bar = None
    Quote = None
    Trade = None
    Snapshot = None
    ALPACA_AVAILABLE = False
    logger = logging.getLogger(__name__)
    logger.warning(
        "alpaca-py SDK not installed. Alpaca market data source will be unavailable. "
        "Install with: pip install alpaca-py"
    )

from src.cache.sqlite_cache import TradingCacheManager
from src.utils.config_loader import ConfigLoader
from src.utils.http_timeouts import with_default_timeout

logger = logging.getLogger(__name__)


class AlpacaMarketData:
    """
    Alpaca market data manager using official alpaca-py SDK.

    Provides unified market data retrieval with intelligent caching (SQLite),
    proper error handling, and automatic pagination via the official SDK.
    """

    # Map string timeframes to Alpaca TimeFrame objects (initialized lazily)
    TIMEFRAME_MAP = None

    def __init__(self, cache_manager: Optional[TradingCacheManager] = None):
        """
        Initialize with official Alpaca SDK.

        Args:
            cache_manager: Optional SQLite cache manager (creates new if None)
        """
        if not ALPACA_AVAILABLE:
            raise ImportError(
                "alpaca-py SDK is required for AlpacaMarketData. "
                "Install with: pip install alpaca-py"
            )

        # Initialize TIMEFRAME_MAP now that we know alpaca is available
        # Includes Fibonacci sequences (1, 2, 3, 5, 8, 13, 21, 34, 55) for technical analysis
        if AlpacaMarketData.TIMEFRAME_MAP is None:
            AlpacaMarketData.TIMEFRAME_MAP = {
                # Minute intervals (Alpaca supports 1-59 minutes)
                "1Min": TimeFrame(1, TimeFrameUnit.Minute),
                "2Min": TimeFrame(2, TimeFrameUnit.Minute),
                "3Min": TimeFrame(3, TimeFrameUnit.Minute),
                "5Min": TimeFrame(5, TimeFrameUnit.Minute),
                "8Min": TimeFrame(8, TimeFrameUnit.Minute),
                "13Min": TimeFrame(13, TimeFrameUnit.Minute),
                "15Min": TimeFrame(15, TimeFrameUnit.Minute),
                "21Min": TimeFrame(21, TimeFrameUnit.Minute),
                "30Min": TimeFrame(30, TimeFrameUnit.Minute),
                "34Min": TimeFrame(34, TimeFrameUnit.Minute),
                "45Min": TimeFrame(45, TimeFrameUnit.Minute),
                "55Min": TimeFrame(55, TimeFrameUnit.Minute),
                # Hour intervals (Alpaca supports 1-23 hours)
                "1Hour": TimeFrame(1, TimeFrameUnit.Hour),
                "2Hour": TimeFrame(2, TimeFrameUnit.Hour),
                "3Hour": TimeFrame(3, TimeFrameUnit.Hour),
                "4Hour": TimeFrame(4, TimeFrameUnit.Hour),
                "5Hour": TimeFrame(5, TimeFrameUnit.Hour),
                "8Hour": TimeFrame(8, TimeFrameUnit.Hour),
                "13Hour": TimeFrame(13, TimeFrameUnit.Hour),
                # Day/Week/Month
                "1Day": TimeFrame(1, TimeFrameUnit.Day),
                "1Week": TimeFrame(1, TimeFrameUnit.Week),
                "1Month": TimeFrame(1, TimeFrameUnit.Month),
            }

        config_loader = ConfigLoader()
        api_key = config_loader.get("ALPACA_PAPER_API_KEY")
        secret_key = config_loader.get("ALPACA_PAPER_SECRET")

        if not api_key or not secret_key:
            raise ValueError(
                "Alpaca API credentials required. Check ALPACA_PAPER_API_KEY "
                "and ALPACA_PAPER_SECRET in config.json"
            )

        # Initialize the official SDK client
        self.client = with_default_timeout(
            StockHistoricalDataClient(
                api_key=api_key,
                secret_key=secret_key,
                raw_data=False,  # Get parsed models instead of raw dicts
            )
        )

        self.cache = cache_manager or TradingCacheManager()
        logger.info("Alpaca market data manager initialized with official SDK and SQLite cache")

    def get_bars(  # noqa: C901
        self,
        symbols: List[str],
        start: str,  # YYYY-MM-DD format
        end: str,
        timeframe: str = "1Day",
        adjustment: str = "all",
        feed: str = "iex",  # Paper accounts must use IEX
        use_cache: bool = True,
    ) -> pd.DataFrame:
        """
        Fetch OHLCV bars using official SDK.

        Args:
            symbols: List of stock symbols
            start: Start date (YYYY-MM-DD)
            end: End date (YYYY-MM-DD)
            timeframe: Bar timeframe (1Min, 5Min, 15Min, 30Min, 1Hour, 1Day)
            adjustment: Adjustment type (raw, split, dividend, all)
            feed: Data feed (iex for paper accounts, sip requires subscription)
            use_cache: Whether to use cache

        Returns:
            DataFrame with OHLCV data

        Note: Paper accounts should use feed='iex' for free data.
        Using feed='sip' requires a subscription and won't work with paper accounts.
        """
        # Check cache first
        cached_data = []
        symbols_to_fetch = []

        end_dt_check = pd.to_datetime(end).date()
        today = pd.Timestamp(market_today()).date()  # the ET date, not the host's (#620)
        is_current_day = end_dt_check >= today

        # Debug-level logging for cache decision details
        logger.debug(
            f"Cache decision: end_date={end_dt_check}, today={today}, "
            f"is_current={is_current_day}, symbols={symbols}"
        )

        # Determine data source and show clean status message
        if is_current_day:
            # Live data is priority for current trading day
            for symbol in symbols:
                logger.info(format_data_status(symbol, "live"))
        else:
            # Historical data uses cache
            for symbol in symbols:
                logger.info(format_data_status(symbol, "cache", include_time=False))

        if use_cache and not is_current_day:
            # Only use cache for historical data (not today)
            for symbol in symbols:
                cached = self.cache.get(symbol, start, end, source="alpaca", timeframe=timeframe)

                if cached is not None and not cached.empty:
                    # Ensure required columns exist
                    if "symbol" not in cached.columns:
                        cached["symbol"] = symbol
                    if "source" not in cached.columns:
                        cached["source"] = "alpaca"
                    cached_data.append(cached)
                    logger.debug(f"Cache hit for {symbol}")
                else:
                    symbols_to_fetch.append(symbol)
        else:
            symbols_to_fetch = symbols

        # Fetch missing data using SDK
        fetched_data = []
        if symbols_to_fetch:
            try:
                # Convert string dates to datetime
                start_dt = pd.to_datetime(start)
                end_dt = pd.to_datetime(end)

                # Get TimeFrame object
                tf = self.TIMEFRAME_MAP.get(timeframe)
                if not tf:
                    raise ValueError(
                        f"Invalid timeframe: {timeframe}. "
                        f"Must be one of: {list(self.TIMEFRAME_MAP.keys())}"
                    )

                # Create request using SDK
                request_params = StockBarsRequest(
                    symbol_or_symbols=symbols_to_fetch,
                    timeframe=tf,
                    start=start_dt,
                    end=end_dt,
                    adjustment=adjustment,
                    feed=feed,  # Use 'iex' for paper accounts
                    asof=None,
                    page_limit=10000,
                )

                # Fetch bars using SDK - automatically handles pagination
                logger.info(f"Fetching bars for {symbols_to_fetch} using SDK...")
                bars_response = self.client.get_stock_bars(request_params)

                # Convert to DataFrame
                if bars_response and bars_response.data:
                    # The SDK returns a BarSet with .data attribute
                    # containing symbol -> list of Bar objects
                    all_bars = []
                    for symbol, bars_list in bars_response.data.items():
                        for bar in bars_list:
                            # Bar object has attributes: timestamp, open, high,
                            # low, close, volume, trade_count, vwap
                            bar_dict = {
                                "timestamp": bar.timestamp,
                                "symbol": symbol,
                                "open": float(bar.open),
                                "high": float(bar.high),
                                "low": float(bar.low),
                                "close": float(bar.close),
                                "volume": int(bar.volume) if bar.volume else 0,
                                "vwap": float(bar.vwap) if bar.vwap else float(bar.close),
                                "trade_count": int(bar.trade_count) if bar.trade_count else 0,
                                "source": "alpaca",
                            }
                            all_bars.append(bar_dict)

                    if all_bars:
                        df = pd.DataFrame(all_bars)
                        df["date"] = df["timestamp"]
                        df = df.set_index("timestamp")
                        df = df.sort_index()

                        # Cache individual symbols
                        if use_cache:
                            for symbol in symbols_to_fetch:
                                symbol_data = df[df["symbol"] == symbol].copy()
                                if not symbol_data.empty:
                                    # Remove 'symbol' and 'source' columns before caching
                                    # (TradingCacheManager stores these separately)
                                    cache_data = symbol_data.drop(
                                        columns=["symbol", "source"], errors="ignore"
                                    )
                                    self.cache.set(
                                        symbol, cache_data, source="alpaca", timeframe=timeframe
                                    )

                        fetched_data.append(df)
                        logger.info(
                            f"Fetched {len(df)} bars for "
                            f"{len(symbols_to_fetch)} symbols from Alpaca"
                        )

            except Exception as e:
                logger.debug(f"Failed to fetch bars from Alpaca: {e}")
                if not cached_data:
                    raise

        # Combine all data
        all_data = cached_data + fetched_data

        # Add live price for current day during market hours if today's bar is missing
        if is_current_day and timeframe == "1Day":
            if is_market_hours():
                # Check if today's bar exists in the data
                today_date = today
                has_today = False

                if all_data:
                    combined_check = pd.concat(all_data, ignore_index=False)
                    # Check if any bar is from today
                    if hasattr(combined_check.index, "date"):
                        has_today = any(d.date() == today_date for d in combined_check.index)
                    else:
                        # Fallback: check if any timestamp contains today's date string
                        has_today = any(str(today_date) in str(idx) for idx in combined_check.index)

                if not has_today:
                    # Fetch live prices and create synthetic daily bar
                    logger.debug("Today's bar missing, fetching live prices")
                    live_bars = []
                    for symbol in symbols:
                        try:
                            # Get latest trade for current price
                            trade = self.get_latest_trade(symbol)
                            if trade and "trade" in trade and "p" in trade["trade"]:
                                price = float(trade["trade"]["p"])
                                timestamp = pd.Timestamp.now(tz="UTC")

                                # Create synthetic bar with current price
                                live_bar = {
                                    "timestamp": timestamp,
                                    "symbol": symbol,
                                    "open": price,
                                    "high": price,
                                    "low": price,
                                    "close": price,
                                    "volume": 0,  # Unknown during day
                                    "vwap": price,
                                    "trade_count": 0,
                                    "source": "alpaca_live",
                                    "date": timestamp,
                                }
                                live_bars.append(live_bar)
                                # Clean status message for live price fetch
                                logger.info(format_data_status(symbol, f"Live @ ${price:.2f}"))
                        except Exception as e:
                            logger.warning(f"Could not fetch live price for {symbol}: {e}")

                    if live_bars:
                        df = pd.DataFrame(live_bars)
                        df = df.set_index("timestamp")
                        all_data.append(df)
                        logger.info(
                            f"✅ Added live prices for {len(live_bars)} symbols to existing data"
                        )

        if not all_data:
            return pd.DataFrame()

        combined_df = pd.concat(all_data, ignore_index=False)
        combined_df = combined_df.sort_index()
        combined_df = combined_df.drop_duplicates()

        return combined_df

    def get_latest_quote(self, symbol: str) -> Dict[str, Any]:
        """
        Get latest quote using SDK.

        Args:
            symbol: Stock symbol

        Returns:
            Dictionary with latest quote data
        """
        try:
            request = StockLatestQuoteRequest(
                symbol_or_symbols=symbol,
                feed="iex",  # Use IEX for paper accounts
            )
            quote_response = self.client.get_stock_latest_quote(request)

            if symbol in quote_response:
                quote = quote_response[symbol]
                return {
                    "symbol": symbol,
                    "quote": {
                        "bp": float(quote.bid_price) if quote.bid_price else None,
                        "bs": int(quote.bid_size) if quote.bid_size else None,
                        "ap": float(quote.ask_price) if quote.ask_price else None,
                        "as": int(quote.ask_size) if quote.ask_size else None,
                        "t": quote.timestamp.isoformat() if quote.timestamp else None,
                    },
                }
            return {}

        except Exception as e:
            logger.error(f"Failed to get latest quote for {symbol}: {e}")
            return {}

    def get_latest_trade(self, symbol: str) -> Dict[str, Any]:
        """
        Get latest trade using SDK.

        Args:
            symbol: Stock symbol

        Returns:
            Dictionary with latest trade data
        """
        try:
            request = StockLatestTradeRequest(
                symbol_or_symbols=symbol,
                feed="iex",  # Use IEX for paper accounts
            )
            trade_response = self.client.get_stock_latest_trade(request)

            if symbol in trade_response:
                trade = trade_response[symbol]
                return {
                    "symbol": symbol,
                    "trade": {
                        "p": float(trade.price) if trade.price else None,
                        "s": int(trade.size) if trade.size else None,
                        "t": trade.timestamp.isoformat() if trade.timestamp else None,
                    },
                }
            return {}

        except Exception as e:
            logger.error(f"Failed to get latest trade for {symbol}: {e}")
            return {}

    def get_snapshot(self, symbols: List[str]) -> Dict[str, Any]:
        """
        Get snapshot using SDK.

        Args:
            symbols: List of stock symbols

        Returns:
            Dictionary with snapshot data for each symbol
        """
        try:
            request = StockSnapshotRequest(
                symbol_or_symbols=symbols,
                feed="iex",  # Use IEX for paper accounts
            )
            snapshots = self.client.get_stock_snapshot(request)

            result = {}
            for symbol, snapshot in snapshots.items():
                result[symbol] = {
                    "latest_trade": (
                        {
                            "p": (
                                float(snapshot.latest_trade.price)
                                if snapshot.latest_trade and snapshot.latest_trade.price
                                else None
                            ),
                            "s": (
                                int(snapshot.latest_trade.size)
                                if snapshot.latest_trade and snapshot.latest_trade.size
                                else None
                            ),
                            "t": (
                                snapshot.latest_trade.timestamp.isoformat()
                                if snapshot.latest_trade and snapshot.latest_trade.timestamp
                                else None
                            ),
                        }
                        if snapshot.latest_trade
                        else None
                    ),
                    "latest_quote": (
                        {
                            "bp": (
                                float(snapshot.latest_quote.bid_price)
                                if snapshot.latest_quote and snapshot.latest_quote.bid_price
                                else None
                            ),
                            "ap": (
                                float(snapshot.latest_quote.ask_price)
                                if snapshot.latest_quote and snapshot.latest_quote.ask_price
                                else None
                            ),
                            "bs": (
                                int(snapshot.latest_quote.bid_size)
                                if snapshot.latest_quote and snapshot.latest_quote.bid_size
                                else None
                            ),
                            "as": (
                                int(snapshot.latest_quote.ask_size)
                                if snapshot.latest_quote and snapshot.latest_quote.ask_size
                                else None
                            ),
                        }
                        if snapshot.latest_quote
                        else None
                    ),
                    "minute_bar": (
                        {
                            "o": (
                                float(snapshot.minute_bar.open)
                                if snapshot.minute_bar and snapshot.minute_bar.open
                                else None
                            ),
                            "h": (
                                float(snapshot.minute_bar.high)
                                if snapshot.minute_bar and snapshot.minute_bar.high
                                else None
                            ),
                            "l": (
                                float(snapshot.minute_bar.low)
                                if snapshot.minute_bar and snapshot.minute_bar.low
                                else None
                            ),
                            "c": (
                                float(snapshot.minute_bar.close)
                                if snapshot.minute_bar and snapshot.minute_bar.close
                                else None
                            ),
                            "v": (
                                int(snapshot.minute_bar.volume)
                                if snapshot.minute_bar and snapshot.minute_bar.volume
                                else None
                            ),
                        }
                        if snapshot.minute_bar
                        else None
                    ),
                    "daily_bar": (
                        {
                            "o": (
                                float(snapshot.daily_bar.open)
                                if snapshot.daily_bar and snapshot.daily_bar.open
                                else None
                            ),
                            "h": (
                                float(snapshot.daily_bar.high)
                                if snapshot.daily_bar and snapshot.daily_bar.high
                                else None
                            ),
                            "l": (
                                float(snapshot.daily_bar.low)
                                if snapshot.daily_bar and snapshot.daily_bar.low
                                else None
                            ),
                            "c": (
                                float(snapshot.daily_bar.close)
                                if snapshot.daily_bar and snapshot.daily_bar.close
                                else None
                            ),
                            "v": (
                                int(snapshot.daily_bar.volume)
                                if snapshot.daily_bar and snapshot.daily_bar.volume
                                else None
                            ),
                        }
                        if snapshot.daily_bar
                        else None
                    ),
                }

            return result

        except Exception as e:
            logger.error(f"Failed to get snapshot for {symbols}: {e}")
            return {}


# Tool wrapper for AutoGen integration
def create_alpaca_market_data_tool(
    cache_manager: Optional[TradingCacheManager] = None,
) -> "AlpacaMarketDataTool":
    """
    Create Alpaca market data tool using SDK for AutoGen agents.

    Args:
        cache_manager: Optional cache manager instance

    Returns:
        AlpacaMarketDataTool instance
    """
    return AlpacaMarketDataTool(cache_manager)


class AlpacaMarketDataTool:
    """Tool wrapper for Alpaca market data access by AutoGen agents using SDK."""

    def __init__(self, cache_manager: Optional[TradingCacheManager] = None):
        """Initialize the Alpaca market data tool with SDK."""
        self.alpaca_client = AlpacaMarketData(cache_manager)
        self.name = "alpaca_market_data"
        self.description = (
            "Fetch real-time and historical market data (bars, quotes, trades, snapshots) "
            "from Alpaca Markets using official SDK with intelligent caching"
        )

    def get_bars(
        self, symbols: List[str], start: str, end: str, timeframe: str = "1Day", **kwargs
    ) -> Dict[str, Any]:
        """Get OHLCV bars for symbols using SDK."""
        df = self.alpaca_client.get_bars(symbols, start, end, timeframe, **kwargs)
        return {
            "bars": df.to_dict(orient="records") if not df.empty else [],
            "symbols": symbols,
            "timeframe": timeframe,
            "start": start,
            "end": end,
            "count": len(df),
        }

    def get_latest_quotes(self, symbols: List[str]) -> Dict[str, Any]:
        """Get latest quotes for symbols using SDK."""
        quotes = {}
        for symbol in symbols:
            quotes[symbol] = self.alpaca_client.get_latest_quote(symbol)
        return quotes

    def get_latest_trades(self, symbols: List[str]) -> Dict[str, Any]:
        """Get latest trades for symbols using SDK."""
        trades = {}
        for symbol in symbols:
            trades[symbol] = self.alpaca_client.get_latest_trade(symbol)
        return trades

    def get_snapshots(self, symbols: List[str]) -> Dict[str, Any]:
        """Get snapshots for symbols using SDK."""
        return self.alpaca_client.get_snapshot(symbols)
