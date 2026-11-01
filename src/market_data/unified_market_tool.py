"""
Unified Market Data Tool using CacheAdapter.

Routes all market data requests through the unified cache system,
with Alpaca as the primary data source.
"""

import logging
from typing import Optional

import pandas as pd

from src.cache import cache_adapter

# Primary data source: Alpaca
try:
    from .alpaca_market_data import AlpacaMarketData

    ALPACA_AVAILABLE = True
except ImportError:
    ALPACA_AVAILABLE = False
    AlpacaMarketData = None
    logging.warning("Alpaca market data not available")

# Fallback source (optional). Polygon was removed in Oct 2026 (#607): its client is
# frozen and access ended; data cached from it earlier is still served.
try:
    from .alpha_vantage_market import AlphaVantageMarketTool

    ALPHA_VANTAGE_AVAILABLE = True
except ImportError:
    ALPHA_VANTAGE_AVAILABLE = False
    AlphaVantageMarketTool = None

logger = logging.getLogger(__name__)


def fetch_unified_market_data(
    symbol: str,
    start_date: str,
    end_date: str,
    source: str = "auto",
    timeframe: str = "1Day",
) -> pd.DataFrame:
    """
    Fetch market data using unified cache system with Alpaca as primary source.

    Args:
        symbol: Stock symbol
        start_date: Start date (YYYY-MM-DD)
        end_date: End date (YYYY-MM-DD)
        source: Data source ("auto", "alpaca", "alpha_vantage"); "polygon" reads only
            rows cached before Polygon was removed (#607)
        timeframe: Bar timeframe (e.g., "1Min", "5Min", "1Hour", "1Day")

    Returns:
        DataFrame with OHLCV data
    """
    if source == "polygon":
        # Removed as a live source (#607); rows cached from it earlier are still served
        cached_data = cache_adapter.get_market_data(symbol, start_date, end_date, source, timeframe)
        if cached_data is not None:
            return cached_data
        raise ValueError(
            "Polygon was removed as a data source (#607) and nothing is cached from it for "
            f"{symbol}; use 'auto', 'alpaca' or 'alpha_vantage'"
        )

    # Check if requesting current trading day - skip cache during market hours
    from src.utils.date_utils import get_datetime_now

    end_dt = pd.to_datetime(end_date).date()
    today = get_datetime_now().date()
    is_current_day = end_dt >= today

    # Skip cache for current trading day during market hours
    if not is_current_day:
        cached_data = cache_adapter.get_market_data(symbol, start_date, end_date, source, timeframe)
        if cached_data is not None:
            return cached_data

    # Cache miss - fetch from appropriate source
    if source == "auto":
        # Priority order: Alpaca -> Alpha Vantage
        data = _fetch_from_alpaca(symbol, start_date, end_date, timeframe)
        if data is not None and not data.empty:
            source = "alpaca"
        else:
            data = _fetch_from_alpha_vantage(symbol, start_date, end_date, timeframe)
            source = "alpha_vantage"
    elif source == "alpaca":
        data = _fetch_from_alpaca(symbol, start_date, end_date, timeframe)
    elif source == "alpha_vantage":
        data = _fetch_from_alpha_vantage(symbol, start_date, end_date, timeframe)
    else:
        raise ValueError(f"Unknown source: {source}")

    # Store in unified cache for future use
    if data is not None and not data.empty:
        cache_adapter.set_market_data(symbol, start_date, end_date, source, data, timeframe)

    return data if data is not None else pd.DataFrame()


def _fetch_from_alpaca(
    symbol: str, start_date: str, end_date: str, timeframe: str = "1Day"
) -> Optional[pd.DataFrame]:
    """Fetch from Alpaca Markets API (primary source)."""
    if not ALPACA_AVAILABLE:
        logger.debug("Alpaca not available")
        return None

    try:
        alpaca_client = AlpacaMarketData()
        data = alpaca_client.get_bars(
            symbols=[symbol],
            start=start_date,
            end=end_date,
            timeframe=timeframe,
            use_cache=False,  # Cache handled by unified cache
        )

        if not data.empty:
            # Normalize column names to match expected format
            if "close" in data.columns and "Close" not in data.columns:
                data["Close"] = data["close"]
            if "open" in data.columns and "Open" not in data.columns:
                data["Open"] = data["open"]
            if "high" in data.columns and "High" not in data.columns:
                data["High"] = data["high"]
            if "low" in data.columns and "Low" not in data.columns:
                data["Low"] = data["low"]
            if "volume" in data.columns and "Volume" not in data.columns:
                data["Volume"] = data["volume"]

            logger.info(f"Fetched {len(data)} bars from Alpaca for {symbol}")
            return data

        return None
    except Exception as e:
        logger.debug(f"Alpaca fetch failed for {symbol}: {e}")
        return None


def _fetch_from_alpha_vantage(
    symbol: str, start_date: str, end_date: str, timeframe: str = "1Day"
) -> Optional[pd.DataFrame]:
    """Fetch from Alpha Vantage API (fallback source).

    Note: Alpha Vantage has limited intraday support on free tier.
    """
    if not ALPHA_VANTAGE_AVAILABLE:
        logger.debug("Alpha Vantage not available")
        return None

    try:
        av_client = AlphaVantageMarketTool()
        # Note: Alpha Vantage may not support all timeframes on free tier
        data = av_client.fetch_stock_data(symbol, start_date, end_date)
        return data
    except Exception as e:
        logger.warning(f"Alpha Vantage fetch failed for {symbol}: {e}")
        return None
