"""
Market Context Tool for V4 Sentiment Enhancement
Fetches SPY/QQQ market context data to enhance sentiment analysis
"""

import logging
from typing import Any, Dict, List

import pandas as pd
from agent_framework import FunctionTool

from src.market_data.unified_market_tool import fetch_unified_market_data
from src.utils.agent_utils import load_agent_config

logger = logging.getLogger(__name__)

LOOKBACK_DAYS = 14  # calendar days fetched before the target date (enough for 5 bars)


def _load_daily_bars(symbol: str, date: str) -> pd.DataFrame:
    """Daily bars up to and including ``date`` via the unified data path.

    Uses cache first, then Alpaca and Alpha Vantage (``fetch_unified_market_data``).
    Normalizes column case, drops duplicate dates and excludes bars after ``date``.
    """
    end = pd.Timestamp(date)
    start = (end - pd.Timedelta(days=LOOKBACK_DAYS)).strftime("%Y-%m-%d")
    # Alpaca treats the end date as midnight (exclusive), so request one day past the target
    # and drop anything after it below.
    fetch_end = (end + pd.Timedelta(days=1)).strftime("%Y-%m-%d")
    data = fetch_unified_market_data(symbol, start, fetch_end, source="auto", timeframe="1Day")
    if data is None or data.empty:
        return pd.DataFrame()

    df = data.copy()
    if isinstance(df.index, pd.MultiIndex):
        df.index = df.index.get_level_values(-1)
    elif "timestamp" in df.columns and not isinstance(df.index, pd.DatetimeIndex):
        df = df.set_index("timestamp")
    df.columns = [str(c).lower() for c in df.columns]
    df = df.loc[:, ~df.columns.duplicated()]
    if "close" not in df.columns:
        return pd.DataFrame()

    idx = pd.to_datetime(df.index)
    if idx.tz is not None:
        idx = idx.tz_convert(None)
    df.index = idx.normalize()
    df = df[~df.index.duplicated(keep="last")].sort_index()
    return df[df.index <= end]


def fetch_market_context_data(date: str, symbols: List[str] = ["SPY", "QQQ"]) -> Dict[str, Any]:
    """
    Fetch market context data (SPY/QQQ) for enhanced sentiment analysis.

    Provides broader market context to complement individual stock analysis.
    Focus: Market direction and tech sector sentiment for LLM reasoning.

    Args:
        date: Date for market data (YYYY-MM-DD format)
        symbols: Market symbols to fetch (default: SPY, QQQ)

    Returns:
        Dict containing market context data for LLM analysis
    """
    try:
        logger.info(f"Fetching market context for {symbols} on {date}")

        market_data = {}

        for symbol in symbols:
            try:
                # Bars up to and including the target date (no later bars: no look-ahead)
                data = _load_daily_bars(symbol, date)
                data_source = "unified (cache, Alpaca, Alpha Vantage)"

                if data.empty:
                    logger.warning(f"No market data available for {symbol} up to {date}")
                    data_source = "None"

                if data is not None and len(data) > 0:
                    # Get the most recent data point
                    latest = data.iloc[-1] if len(data) > 0 else None
                    prev = data.iloc[-2] if len(data) > 1 else None

                    if latest is not None:
                        # Calculate daily change
                        daily_change = 0.0
                        if prev is not None:
                            daily_change = ((latest["close"] - prev["close"]) / prev["close"]) * 100

                        # Calculate recent trend (5-day if available)
                        trend_change = 0.0
                        if len(data) >= 5:
                            first = data.iloc[-5]
                            trend_change = (
                                (latest["close"] - first["close"]) / first["close"]
                            ) * 100

                        market_data[symbol] = {
                            "symbol": symbol,
                            "date_analyzed": date,
                            "current_price": float(latest["close"]),
                            "volume": int(latest["volume"]) if "volume" in latest else 0,
                            "daily_change_pct": round(daily_change, 2),
                            "trend_5day_pct": round(trend_change, 2),
                            "interpretation": _interpret_market_signal(
                                symbol, daily_change, trend_change
                            ),
                        }

                        logger.info(
                            f"Market context for {symbol}: {daily_change:+.2f}% daily, {trend_change:+.2f}% 5-day (source: {data_source})"
                        )
                    else:
                        logger.warning(f"No data available for {symbol} on {date}")
                        market_data[symbol] = _create_fallback_data(symbol, date)
                else:
                    logger.warning(f"No market data returned for {symbol}")
                    market_data[symbol] = _create_fallback_data(symbol, date)

            except Exception as e:
                logger.error(f"Error fetching data for {symbol}: {e}")
                market_data[symbol] = _create_fallback_data(symbol, date, str(e))

        # Create summary interpretation
        summary = _create_market_summary(market_data)

        result = {
            "market_context": market_data,
            "market_summary": summary,
            "date_analyzed": date,
            "symbols_analyzed": symbols,
            "tool_name": "market_context",
        }

        logger.info(f"Market context analysis complete: {summary['overall_sentiment']}")
        return result

    except Exception as e:
        logger.error(f"Market context tool error: {e}")
        return {
            "market_context": {},
            "market_summary": {"overall_sentiment": "UNKNOWN", "error": str(e)},
            "date_analyzed": date,
            "symbols_analyzed": symbols,
            "tool_name": "market_context",
        }


def _interpret_market_signal(  # noqa: C901
    symbol: str, daily_change: float, trend_change: float
) -> str:
    """Interpret market signal for LLM consumption."""

    if symbol == "SPY":
        base = "Market (S&P 500)"
    elif symbol == "QQQ":
        base = "Tech Sector (Nasdaq)"
    else:
        base = symbol

    # Interpret daily movement
    if abs(daily_change) < 0.5:
        daily_desc = "stable"
    elif daily_change > 1.0:
        daily_desc = "strong positive"
    elif daily_change > 0.5:
        daily_desc = "positive"
    elif daily_change < -1.0:
        daily_desc = "strong negative"
    else:
        daily_desc = "negative"

    # Interpret trend
    if abs(trend_change) < 1.0:
        trend_desc = "sideways"
    elif trend_change > 2.0:
        trend_desc = "strong uptrend"
    elif trend_change > 1.0:
        trend_desc = "uptrend"
    elif trend_change < -2.0:
        trend_desc = "strong downtrend"
    else:
        trend_desc = "downtrend"

    return f"{base}: {daily_desc} day ({daily_change:+.1f}%), {trend_desc} ({trend_change:+.1f}%)"


def _create_market_summary(market_data: Dict[str, Any]) -> Dict[str, Any]:
    """Create overall market sentiment summary for LLM."""

    spy_data = market_data.get("SPY", {})
    qqq_data = market_data.get("QQQ", {})

    # No data is not a flat market (#637): only what was asked for counts as missing
    missing = [symbol for symbol, data in market_data.items() if data.get("no_data")]
    available = [data for data in (spy_data, qqq_data) if data and not data.get("no_data")]
    if missing or not available:
        return {
            "overall_sentiment": "UNKNOWN",
            "market_daily_avg": None,
            "market_trend_avg": None,
            "spy_signal": spy_data.get("interpretation", "No data"),
            "qqq_signal": qqq_data.get("interpretation", "No data"),
            "interpretation": (
                f"Market sentiment unknown: no data for {', '.join(missing) or 'SPY or QQQ'}"
            ),
        }

    # Calculate overall sentiment
    daily_avg = sum(data.get("daily_change_pct", 0.0) for data in available) / len(available)
    trend_avg = sum(data.get("trend_5day_pct", 0.0) for data in available) / len(available)

    # Determine overall market sentiment
    if daily_avg > 1.0 and trend_avg > 2.0:
        overall = "STRONG_BULLISH"
    elif daily_avg > 0.5 and trend_avg > 1.0:
        overall = "BULLISH"
    elif daily_avg > -0.5 and trend_avg > -1.0:
        overall = "NEUTRAL"
    elif daily_avg < -0.5 and trend_avg < -1.0:
        overall = "BEARISH"
    else:
        overall = "STRONG_BEARISH"

    return {
        "overall_sentiment": overall,
        "market_daily_avg": round(daily_avg, 2),
        "market_trend_avg": round(trend_avg, 2),
        "spy_signal": spy_data.get("interpretation", "No data"),
        "qqq_signal": qqq_data.get("interpretation", "No data"),
        "interpretation": f"Market sentiment: {overall} (daily: {daily_avg:+.1f}%, trend: {trend_avg:+.1f}%)",
    }


def _create_fallback_data(symbol: str, date: str, error: str = None) -> Dict[str, Any]:
    """Create fallback data when market data is unavailable."""
    return {
        "symbol": symbol,
        "date_analyzed": date,
        "current_price": 0.0,
        "volume": 0,
        "daily_change_pct": 0.0,
        "trend_5day_pct": 0.0,
        "interpretation": f"{symbol}: No data available" + (f" ({error})" if error else ""),
        "error": error,
        "no_data": True,
    }


# Load description from YAML config with fallback
def _get_market_context_description():
    """Get market context tool description from YAML or use fallback."""
    default_desc = """Fetch market context data (SPY/QQQ) for enhanced sentiment analysis.

Provides broader market direction and tech sector sentiment to complement
individual stock analysis. Designed for LLM reasoning about market conditions.

Parameters:
- date: Target date for analysis (YYYY-MM-DD)
- symbols: Market symbols (default: ["SPY", "QQQ"])

Returns market context including:
- Daily price movements
- Short-term trends
- Market sentiment interpretation
- Overall market direction summary

Use this to understand if the broader market and tech sector are supportive
of individual stock decisions."""

    try:
        tools_config = load_agent_config("tools")
        desc = tools_config.get("market_context", {}).get("description", "")
        return desc.strip() if desc else default_desc
    except Exception:
        return default_desc


# The agents' FunctionTool (Microsoft Agent Framework, #606)
market_context_tool = FunctionTool(
    func=fetch_market_context_data,
    name="fetch_market_context_data",
    description=_get_market_context_description(),
)

# Set agent type compatibility
market_context_tool.agent_types = ["sentiment"]
