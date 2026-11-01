"""
Entry Planning CLI Tools - FunctionTool wrappers for entry planning.

Issue #366: OHLCV-Based Intraday Entry Plan
Related: CLI integration for A Stream features

This module wraps the entry_planning module functions as FunctionTool
instances for integration with the AutoGen agent architecture.

Original Implementation: src/trading/instruments/entry_planning.py
Pattern: Pure function wrappers → FunctionTool → Registry
"""

from datetime import date, timedelta
from typing import Tuple

import pandas as pd
from agent_framework import FunctionTool

from src.trading.instruments.data_fetch import fetch_market_data
from src.trading.instruments.entry_planning import (
    calculate_atr,
    calculate_entry_plan,
    calculate_volume_confirmation,
    find_support_resistance,
)
from src.trading.scheduling.scheduler_state import market_today
from src.utils.date_utils import add_business_days
from src.utils.market_hours import is_market_hours
from src.utils.safe_print import get_symbol

# Fewest completed daily bars the levels and volume averages are worked out from
MIN_BARS = 20
# Sessions requested beyond the lookback, for gaps in the vendor's data
FETCH_SLACK = 10


class _DataError(Exception):
    """Why a tool can't work out its numbers, worded for the user."""


def _completed_bars(
    symbol: str, lookback_days: int, minimum: int = MIN_BARS
) -> Tuple[pd.DataFrame, float, str]:
    """
    The latest lookback_days completed daily bars (capitalized OHLCV, oldest first), the
    latest price, and what that price is ("live", or "<date> close").

    During the session the last row is today's bar so far, or one made from the last
    trade with volume 0: its close is the latest price, and it is left out of the bars
    so it can't skew ATR, ranges or volume averages.

    Args:
        symbol: Stock ticker
        lookback_days: Trading days of completed bars wanted
        minimum: Fewest completed bars the caller can work from

    Raises:
        _DataError: lookback_days below minimum, no data, or too few completed bars
    """
    if lookback_days < minimum:
        raise _DataError(f"lookback_days must be at least {minimum}.")
    today = date.fromisoformat(market_today())
    start = add_business_days(today.isoformat(), -(lookback_days + FETCH_SLACK))
    # The request leaves out the end date's own bar, so it runs through tomorrow
    end = (today + timedelta(days=1)).isoformat()
    ohlcv = fetch_market_data(symbol.upper(), start, end, timeframe="1Day")
    if ohlcv is None or ohlcv.empty:
        raise _DataError(
            f"No daily bars for {symbol}: the data request failed or returned nothing "
            "(see the log)."
        )

    ohlcv, dates = _oldest_first(ohlcv)
    latest_price = float(ohlcv["Close"].iloc[-1])
    latest = dates[-1] if dates is not None else None
    if latest == today and _session_open():
        ohlcv = ohlcv[dates < today]
        as_of = "live"
    else:
        as_of = f"{latest} close" if latest else "latest close"

    bars = ohlcv.tail(lookback_days)
    if len(bars) < minimum:
        raise _DataError(
            f"Insufficient data for {symbol}: {len(bars)} completed daily bars, "
            f"need at least {minimum}."
        )
    return bars, latest_price, as_of


def _oldest_first(ohlcv: pd.DataFrame):
    """
    The bars oldest first (Alpha Vantage returns them newest first) and each bar's date in
    ET, from a datetime index or a date or timestamp column; the dates are None without
    either.
    """
    stamps = None
    if isinstance(ohlcv.index, pd.DatetimeIndex):
        stamps = ohlcv.index
    else:
        for column in ("date", "timestamp"):
            if column in ohlcv.columns:
                stamps = pd.DatetimeIndex(pd.to_datetime(ohlcv[column]))
                break
    if stamps is None:
        return ohlcv, None
    order = stamps.argsort(kind="stable")
    ohlcv, stamps = ohlcv.iloc[order], stamps[order]
    if stamps.tz is not None:
        stamps = stamps.tz_convert("America/New_York")
    return ohlcv, stamps.date


def _session_open() -> bool:
    """Whether the market is open now; True when the calendar can't say, so a bar dated
    today is left out rather than taken as complete."""
    try:
        return is_market_hours()
    except Exception:
        return True


# ============================================================================
# Pure Function Wrappers
# ============================================================================


def get_entry_plan(
    symbol: str,
    direction: str,
    lookback_days: int = 60,
    atr_multiplier: float = 2.0,
    risk_reward_ratio: float = 2.0,
) -> str:
    """
    Generate an entry plan for a symbol with ATR-based stops.

    Calculates optimal entry price, stop loss, and take profit
    using recent OHLCV data, ATR volatility, and support/resistance levels.

    Args:
        symbol: Stock ticker (e.g., 'AAPL', 'MSFT')
        direction: Trade direction - 'BUY' or 'SELL'
        lookback_days: Trading days of completed daily bars to analyze (default 60,
            at least 20)
        atr_multiplier: ATR multiplier for stop distance (default 2.0)
        risk_reward_ratio: Target/risk ratio (default 2.0)

    Returns:
        Formatted string with entry plan details

    Example:
        >>> get_entry_plan('AAPL', 'BUY')
        '📊 Entry Plan for AAPL (BUY)
        Entry: $185.50  Stop: $181.20  Target: $194.10
        ATR: $2.15  Quality: STRONG'
    """
    try:
        ohlcv, current_price, as_of = _completed_bars(symbol, lookback_days)

        # Generate entry plan
        plan = calculate_entry_plan(
            ohlcv=ohlcv,
            current_price=current_price,
            signal_direction=direction.upper(),
            atr_multiplier=atr_multiplier,
            risk_reward_ratio=risk_reward_ratio,
        )

        if plan["entry_price"] is None:
            return f"{get_symbol('ERROR')} Could not generate entry plan: {plan.get('plan_quality', 'Unknown error')}"

        # Format output
        output = f"{get_symbol('INFO')} Entry Plan for {symbol} ({direction.upper()})\n"
        output += "=" * 50 + "\n"
        output += f"  Price:  ${current_price:.2f} ({as_of})\n"
        output += f"  Entry:  ${plan['entry_price']:.2f}\n"
        output += f"  Stop:   ${plan['stop_loss']:.2f}\n"
        output += f"  Target: ${plan['take_profit']:.2f}\n"
        output += "-" * 50 + "\n"
        output += f"  ATR (14): ${plan['atr_value']:.2f}\n"
        output += f"  Support: ${plan['support']:.2f}\n"
        output += f"  Resistance: ${plan['resistance']:.2f}\n"
        output += f"  Volume Confirmation: {'Yes' if plan['volume_confirmation'] else 'No'}\n"
        output += f"  Plan Quality: {plan['plan_quality']}\n"

        # Calculate risk/reward details
        risk = abs(plan["entry_price"] - plan["stop_loss"])
        reward = abs(plan["take_profit"] - plan["entry_price"])
        risk_pct = (risk / plan["entry_price"]) * 100
        reward_pct = (reward / plan["entry_price"]) * 100

        output += "-" * 50 + "\n"
        output += f"  Risk: ${risk:.2f} ({risk_pct:.1f}%)\n"
        output += f"  Reward: ${reward:.2f} ({reward_pct:.1f}%)\n"
        output += f"  R:R Ratio: 1:{reward / risk:.1f}\n"

        return output

    except _DataError as e:
        return f"{get_symbol('ERROR')} {e}"
    except Exception as e:
        return f"{get_symbol('ERROR')} Error generating entry plan: {str(e)}"


def get_support_resistance(symbol: str, lookback_days: int = 60) -> str:
    """
    Show support and resistance levels for a symbol.

    Uses recent price data to identify key levels where price
    has historically found support (lows) or resistance (highs).

    Args:
        symbol: Stock ticker
        lookback_days: Trading days of completed daily bars (default 60, at least 20)

    Returns:
        Formatted string with S/R levels

    Example:
        >>> get_support_resistance('AAPL')
        '📈 Support/Resistance for AAPL
        Support: $178.50
        Resistance: $192.30
        Range: $13.80'
    """
    try:
        ohlcv, current_price, as_of = _completed_bars(symbol, lookback_days)

        levels = find_support_resistance(ohlcv["High"], ohlcv["Low"], lookback=20)

        output = f"{get_symbol('CHART')} Support/Resistance for {symbol}\n"
        output += "=" * 40 + "\n"
        output += f"  Current Price: ${current_price:.2f} ({as_of})\n"
        output += "-" * 40 + "\n"
        output += f"  Support:    ${levels['support']:.2f}"

        dist_to_support = ((levels["support"] - current_price) / current_price) * 100
        output += f" ({dist_to_support:+.1f}% from current)\n"

        output += f"  Resistance: ${levels['resistance']:.2f}"
        dist_to_resistance = ((levels["resistance"] - current_price) / current_price) * 100
        output += f" ({dist_to_resistance:+.1f}% from current)\n"

        output += f"  Range:      ${levels['range']:.2f}\n"

        return output

    except _DataError as e:
        return f"{get_symbol('ERROR')} {e}"
    except Exception as e:
        return f"{get_symbol('ERROR')} Error getting S/R levels: {str(e)}"


def get_volume_analysis(symbol: str, lookback_days: int = 60) -> str:
    """
    Analyze volume patterns for entry confirmation.

    Compares current volume to average to determine if
    there's sufficient conviction for a trade entry.

    Args:
        symbol: Stock ticker
        lookback_days: Trading days of completed daily bars (default 60, at least 20)

    Returns:
        Formatted string with volume analysis

    Example:
        >>> get_volume_analysis('AAPL')
        '📊 Volume Analysis for AAPL
        Current: 85.2M
        Average: 62.1M
        Ratio: 1.37x (Above Average)'
    """
    try:
        ohlcv, _, _ = _completed_bars(symbol, lookback_days)

        # Completed sessions only: today's volume so far would read as a collapse
        vol_data = calculate_volume_confirmation(ohlcv["Volume"], lookback=20)

        output = f"{get_symbol('INFO')} Volume Analysis for {symbol}\n"
        output += "=" * 40 + "\n"
        output += f"  Last Session:   {vol_data['current_volume']:,.0f}\n"
        output += f"  20-Day Average: {vol_data['avg_volume']:,.0f}\n"
        output += f"  Volume Ratio:   {vol_data['volume_ratio']:.2f}x\n"
        output += "-" * 40 + "\n"

        if vol_data["high_volume"]:
            output += f"  Status: {get_symbol('GREEN')} HIGH VOLUME (>1.5x average)\n"
            output += "  -> Strong conviction, good for entry\n"
        elif vol_data["above_average"]:
            output += f"  Status: {get_symbol('YELLOW')} ABOVE AVERAGE\n"
            output += "  -> Adequate volume for entry\n"
        else:
            output += f"  Status: {get_symbol('RED')} BELOW AVERAGE\n"
            output += "  -> Low conviction, consider waiting\n"

        return output

    except _DataError as e:
        return f"{get_symbol('ERROR')} {e}"
    except Exception as e:
        return f"{get_symbol('ERROR')} Error analyzing volume: {str(e)}"


def get_atr(symbol: str, period: int = 14, lookback_days: int = 60) -> str:
    """
    Get Average True Range (ATR) for volatility assessment.

    ATR measures price volatility and is used for:
    - Stop loss placement
    - Position sizing
    - Entry/exit timing

    Args:
        symbol: Stock ticker
        period: ATR calculation period (default 14)
        lookback_days: Trading days of completed daily bars (at least period + 1)

    Returns:
        Formatted string with ATR analysis

    Example:
        >>> get_atr('AAPL')
        '📉 ATR Analysis for AAPL
        Current ATR (14): $2.15
        As % of price: 1.16%
        → Low volatility environment'
    """
    try:
        ohlcv, current_price, as_of = _completed_bars(symbol, lookback_days, minimum=period + 1)

        atr_series = calculate_atr(ohlcv["High"], ohlcv["Low"], ohlcv["Close"], period=period)
        current_atr = float(atr_series.iloc[-1])
        atr_pct = (current_atr / current_price) * 100

        output = f"{get_symbol('INFO')} ATR Analysis for {symbol}\n"
        output += "=" * 40 + "\n"
        output += f"  Current Price: ${current_price:.2f} ({as_of})\n"
        output += f"  ATR ({period}):     ${current_atr:.2f}\n"
        output += f"  ATR as % price: {atr_pct:.2f}%\n"
        output += "-" * 40 + "\n"

        # Interpret volatility level
        if atr_pct < 1.5:
            output += f"  Volatility: {get_symbol('GREEN')} LOW\n"
            output += "  -> Tighter stops appropriate\n"
        elif atr_pct < 3.0:
            output += f"  Volatility: {get_symbol('YELLOW')} MODERATE\n"
            output += "  -> Standard stop distances\n"
        else:
            output += f"  Volatility: {get_symbol('RED')} HIGH\n"
            output += "  -> Wider stops needed, smaller position\n"

        # Suggest stop distances
        output += "-" * 40 + "\n"
        output += "  Suggested Stop Distances:\n"
        output += f"    1.5x ATR: ${current_atr * 1.5:.2f} ({atr_pct * 1.5:.1f}%)\n"
        output += f"    2.0x ATR: ${current_atr * 2.0:.2f} ({atr_pct * 2.0:.1f}%)\n"
        output += f"    2.5x ATR: ${current_atr * 2.5:.2f} ({atr_pct * 2.5:.1f}%)\n"

        return output

    except _DataError as e:
        return f"{get_symbol('ERROR')} {e}"
    except Exception as e:
        return f"{get_symbol('ERROR')} Error calculating ATR: {str(e)}"


# ============================================================================
# FunctionTool Definitions
# ============================================================================

get_entry_plan_tool = FunctionTool(
    func=get_entry_plan,
    name="get_entry_plan",
    description=(
        "Generate an entry plan for a stock with ATR-based stops, "
        "support/resistance levels, and volume confirmation. "
        "Returns entry price, stop loss, take profit, and plan quality."
    ),
)

get_support_resistance_tool = FunctionTool(
    func=get_support_resistance,
    name="get_support_resistance",
    description=(
        "Show support and resistance levels for a stock based on recent "
        "price action. Useful for identifying entry zones and stop placement."
    ),
)

get_volume_analysis_tool = FunctionTool(
    func=get_volume_analysis,
    name="get_volume_analysis",
    description=(
        "Analyze volume patterns to determine if there's sufficient "
        "conviction for a trade entry. Compares current vs average volume."
    ),
)

get_atr_tool = FunctionTool(
    func=get_atr,
    name="get_atr",
    description=(
        "Get Average True Range (ATR) for a stock to assess volatility "
        "and determine appropriate stop loss distances."
    ),
)


# ============================================================================
# Tool Collection for Registry
# ============================================================================

CLI_ENTRY_PLANNING_TOOLS = [
    get_entry_plan_tool,
    get_support_resistance_tool,
    get_volume_analysis_tool,
    get_atr_tool,
]

__all__ = [
    "get_entry_plan",
    "get_support_resistance",
    "get_volume_analysis",
    "get_atr",
    "get_entry_plan_tool",
    "get_support_resistance_tool",
    "get_volume_analysis_tool",
    "get_atr_tool",
    "CLI_ENTRY_PLANNING_TOOLS",
]
