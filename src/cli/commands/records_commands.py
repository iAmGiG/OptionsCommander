"""
Read-only view of the daily records (#602): equity, fills and realized P&L.

Reads what the routines saved in state/user.db through DailyRecords. It makes no broker calls,
so it works offline, and shows the state as of the last routine.
"""

from typing import Any, Dict, List, Optional

from src.trading.state.daily_records import DailyRecords

DEFAULT_DAYS = 10
USAGE = "Usage: /records [days|all]  (default: the last 10 recorded days)"


def _money(value: Optional[float], signed: bool = False) -> str:
    """A dollar figure, or a dash for one that wasn't recorded."""
    if value is None:
        return "-"
    return f"{value:+,.2f}" if signed else f"${value:,.2f}"


def _equity_lines(series: List[Dict[str, Any]]) -> List[str]:
    lines = ["Equity", f"  {'Date':<12}{'Equity':>14}{'Day P&L':>12}"]
    for day in series:
        lines.append(
            f"  {day['market_date']:<12}{_money(day['equity']):>14}"
            f"{_money(day['day_pnl'], signed=True):>12}"
        )
    return lines


def _fill_lines(fills: List[Dict[str, Any]]) -> List[str]:
    if not fills:
        return ["Fills", "  None in this period"]
    lines = ["Fills"]
    for fill in fills:
        lines.append(
            f"  {fill['market_date']:<12}{fill['symbol']:<6} {fill['side']:<4} "
            f"{fill['filled_qty']:g} @ {_money(fill['filled_avg_price'])} "
            f"({fill['order_type'] or '?'})"
        )
    return lines


def _realized_lines(records: DailyRecords, fills: List[Dict[str, Any]]) -> List[str]:
    """Realized P&L of the sales in the period, by day, at average cost."""
    days = sorted({fill["market_date"] for fill in fills if fill["side"] == "sell"})
    sales = [(day, sale) for day in days for sale in records.realized_on(day)]
    if not sales:
        return ["Realized P&L", "  No sales in this period"]

    lines = ["Realized P&L (average cost)"]
    for day, sale in sales:
        pnl = "cost basis unknown" if sale["pnl"] is None else _money(sale["pnl"], signed=True)
        lines.append(f"  {day:<12}{sale['symbol']:<6} {sale['qty']:g} sold: {pnl}")

    known = [sale["pnl"] for _, sale in sales if sale["pnl"] is not None]
    unknown = len(sales) - len(known)
    total = f"  Total: {sum(known):+,.2f} over {len(known)} sale(s)"
    lines.append(total + (f"; {unknown} with unknown cost basis" if unknown else ""))
    return lines


def show_records(days: Optional[int] = DEFAULT_DAYS) -> str:
    """
    The daily records as text.

    Args:
        days: How many recorded days to show, the latest ones; None for all of them

    Returns:
        Equity by day with day P&L, the fills in that period, and their realized P&L
    """
    records = DailyRecords()
    series = records.equity_series()
    if not series:
        return "No daily records yet: the routines write them on each run."
    shown = series if days is None else series[-days:]

    # All records include fills older than the first run, recorded when records began
    start = None if days is None else shown[0]["market_date"]
    fills = [
        fill for fill in records.fills_after(None) if start is None or fill["market_date"] >= start
    ]

    span = "all" if days is None else f"last {len(shown)}"
    sections = [
        [f"Daily Records ({span} recorded days, as of the last routine)"],
        _equity_lines(shown),
        _fill_lines(fills),
        _realized_lines(records, fills),
    ]
    return "\n\n".join("\n".join(section) for section in sections)


def records_command(args: Optional[str] = None) -> str:
    """Text for /records [days|all]."""
    arg = (args or "").strip().lower()
    if not arg:
        return show_records()
    if arg == "all":
        return show_records(None)
    if arg.isdigit() and int(arg) > 0:
        return show_records(int(arg))
    return USAGE
