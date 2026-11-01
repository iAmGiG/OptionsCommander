"""
Simulation of the live CLI trading rules, for the #574 paper-validation baseline.

The live path (RealVoterStrategy -> SimpleRiskManager -> AlpacaExecutionManager) is
reproduced as closely as daily bars allow:

- Signal: the live ``VotingSignal.evaluate_ranked_voting``, computed by ``VotingSignal``
  (MACD 13/34/8 histogram beyond +/-0.1, RSI 14 at 30/70) on a 90-calendar-day window,
  as RealVoterStrategy fetches.
- Timing: the signal for day *d* uses bars through *d - 1*, as a pre-market routine
  would; trades fill at day *d*'s open. The window is rescaled to the split level of
  day *d*, because the MACD threshold is in dollars and live bars are split-adjusted
  only up to the fetch (on a split day, they are already at the new level).
- Entries: a BUY with no position opens one at the open. Size is a share of the
  previous close's equity (the mode's risk_per_trade) in whole shares at the previous
  close, rounded at the raw price of the day as live orders are (so a slot smaller than
  one share is skipped). Blocked beyond 10 positions, 80% exposure or the cash held at
  the start of the day (SimpleRiskManager defaults). The decision is made before the
  open, so the position count, exposure and cash include positions that exit later
  that day, and a symbol held at the start of the day is not bought again until the
  next day.
- Exits: a SELL signal closes the position at the open. Otherwise the market bracket
  (stop -5%, target +8% from the fill, AlpacaExecutionManager defaults) is checked on
  the day's range. A gap through either level fills at the open, and a day that
  touches both counts as the stop (the conservative reading).
- Costs: ``cost_bps`` per side on every fill.

Exit modes (``LiveRules.exit_mode``, #539). Entries, sizing, limits and SELL-signal exits
are the same in all three; only the protective orders differ:

- ``bracket`` (default; the #574 baseline): fixed stop -5% / target +8% from the fill.
- ``trailing`` (the legacy positions, whose 8% take-profit legs lapsed at Alpaca's 90-day
  GTC limit and were not re-placed): the -5% stop, raised by the routines' trailing tiers,
  and no target.
- ``trailing_target`` (new CLI entries: they still go out as bracket orders with an 8%
  take-profit leg, which lives until day 90, longer than the 7-14 day holds simulated):
  trailing tiers plus the +8% target.

SELL-signal exits are a #574 baseline convention: nothing in the live system sells on a
signal, so ``signal_exit=False`` is the variant that matches live exits.

The raise is the live one: ``StateReconciler._trailing_level`` on a ``TrailingStopManager``
built as ``CostEfficientTradeCycle`` builds it (tiers, never down, ``MIN_STOP_GAP`` floor,
$0.01 rule). On daily bars it is one raise a day from the close, as the evening routine
(15:50 ET, ten minutes before the close) does it, and a raised stop applies from the next
session: the day's own range is checked against the stop set the evening before. A raised
stop that is hit exits as ``trail`` (``trail_gap`` on a gap), a stop never raised as
``stop`` / ``stop_gap``. ``signal_exit=False`` switches the SELL-signal exits off, so that
positions leave only through their stops and targets.

The morning routine (09:20 ET) raised too until #623 removed it, from the broker's price at
that time: a pre-market print, not the previous close, so on a gap day it differed from the
price the evening raise used. The default matches the routines and leaves it out.
``morning_raise=True`` is a sensitivity that
stands the day's open in for the 09:20 price: stops are raised from the open before the
day's range is checked, so a gap-up's gain is locked in ahead of the session and a
reversal can take it. The open is the case in which the gap is fully priced by 09:20; a
thin pre-market, or none, leaves the price nearer the previous close, where the raise adds
nothing. The omitted effect runs against the trailing modes (more trail exits, lower
Sharpe): scripts/research/trailing_vs_bracket.py measures it.

``morning_prices`` (#623) replaces the open with the real 09:20 price per symbol and day (the last
pre-market trade before 09:20, split-adjusted to the daily series' scale; see
scripts/research/morning_raise_real_prices.py). A day without a price has no morning raise for
that symbol. A raised stop above the open fills at the open, like any gap through a stop.

Prices are split-adjusted (ResearchBacktester); dividends are not included.
"""

from dataclasses import dataclass
from typing import Callable, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

SignalFn = Callable[[str, pd.DataFrame], dict]

EXIT_MODES = ("bracket", "trailing", "trailing_target")


@dataclass
class PartialPlan:
    """
    Partial exits (#584): a share of each position sells at a profit target, the rest keeps the
    trailing stop. The plan is the PartialExitManager's (two targets, 50/50, the first a limit
    at ``target_pct`` over the fill, the second "trailing"), in whole shares: the first target
    gets ``int(n * first_ratio)`` of the ``n`` shares, the last takes the remainder, and a
    position of one share has no plan. Needs ``exit_mode="trailing"`` (the plan supplies the
    only target).

    ``design`` is how the plan reaches the broker (the three designs of #584):

    1. shrink the stop: the target shares rest as a limit sell and have no stop; the others
       have the trailing stop. The limit fills on an intraday touch (at the open on a gap
       above it). If the stop shares are stopped out, ``unprotected`` says what happens to
       the target shares: ``"hold"`` keeps them resting (until the target, a SELL signal, or
       the end of the data), ``"exit_next_open"`` sells them at the next session's open (a
       routine finding a position with no stop).
    2. software targets: every share keeps the trailing stop; the target is seen only at
       the 09:20 routine (the open stands in) and the 15:50 routine (the close stands in),
       and sells at that price when the price is at or past the target. No intraday fill.
    3. per-tranche orders: every tranche has the trailing stop and the target shares also a
       limit sell (OCO); the limit fills on an intraday touch. A bar that touches both the
       stop and the target counts as the stop (``same_bar="stop"``, as the bracket does);
       ``"target"`` is the optimistic reading, for sensitivity.

    ``limit_through_bps`` makes a limit fill only when the high trades that many bps through
    the target (a touch is not always a fill); the fill is at the target.
    """

    design: int = 3
    target_pct: float = 0.04
    first_ratio: float = 0.5
    unprotected: str = "hold"
    same_bar: str = "stop"
    limit_through_bps: float = 0.0


@dataclass
class LiveRules:
    """Sizing, limits, exits and costs of the live path."""

    position_pct: float = 0.02  # notional per position, share of equity (risk_per_trade)
    max_positions: int = 10  # SimpleRiskManager risk.max_positions default
    max_exposure_pct: float = 0.80  # SimpleRiskManager risk.max_exposure_pct default
    stop_pct: float = 0.05  # market-bracket stop below the fill (the initial stop)
    target_pct: float = 0.08  # market-bracket target above the fill
    cost_bps: float = 0.0  # per side, every fill
    lookback_days: int = 90  # RealVoterStrategy lookback (calendar days)
    initial_capital: float = 100_000.0
    exit_mode: str = "bracket"  # bracket | trailing | trailing_target (module docstring)
    signal_exit: bool = True  # a SELL signal closes the position at the open
    morning_raise: bool = False  # sensitivity: also raise stops from the open (module docstring)
    # #623: per symbol, the split-adjusted price the 09:20 routine sees, by date (NaN or a missing
    # date: no morning raise for that symbol that day). With it, ``morning_raise`` raises from
    # these prices instead of the open. The prices must be known by 09:20 on their own day.
    morning_prices: Optional[Dict[str, pd.Series]] = None
    # #584: partial exits (None: every position is one block, as before)
    partial: Optional[PartialPlan] = None
    # #584 sensitivity: extra slippage on stop-market fills, bps below the fill price (every
    # stop fill, or only gaps through the stop). 0: stops fill at the stop / the open, as before.
    stop_slip_bps: float = 0.0
    stop_slip_gap_only: bool = False


def compute_signals(
    closes: Dict[str, pd.Series],
    raw_closes: Dict[str, pd.Series],
    signal_fn: SignalFn,
    start: str,
    end: str,
    lookback_days: int = 90,
    bars: Optional[Dict[str, pd.DataFrame]] = None,
) -> pd.DataFrame:
    """
    Signal for each symbol on each of its trading days in [start, end].

    The window holds the closes strictly before the day, within ``lookback_days``
    calendar days, rescaled to that day's split level (its raw / adjusted ratio, which
    depends only on the split calendar).

    ``bars`` (per symbol, split-adjusted ``high`` / ``low`` on the same dates as ``closes``)
    adds ``High`` and ``Low`` columns to the window, rescaled the same way, for signals that
    need the day's range. ``frame.attrs["day"]`` carries the decision day, so a signal can
    cut its own shorter window (the live voter's 90 calendar days) or tell which week is
    still open. Neither changes a signal that reads only ``Close``.

    Returns:
        DataFrame indexed by (date, symbol) with ``action`` and ``position_size``
    """
    rows = []
    for symbol, series in closes.items():
        dates = series.index
        ratio = (raw_closes[symbol].reindex(dates) / series).ffill().fillna(1.0)
        days = dates[(dates >= pd.Timestamp(start)) & (dates <= pd.Timestamp(end))]
        for day in days:
            end_i = dates.searchsorted(day)  # first index at or after `day`
            if end_i == 0:
                continue
            start_i = dates.searchsorted(day - pd.Timedelta(days=lookback_days))
            window = series.iloc[start_i:end_i]
            scale = ratio.iloc[end_i]
            frame = pd.DataFrame({"Close": window * scale})
            if bars is not None:
                frame["High"] = bars[symbol]["high"].iloc[start_i:end_i] * scale
                frame["Low"] = bars[symbol]["low"].iloc[start_i:end_i] * scale
            frame.attrs["day"] = day
            result = signal_fn(symbol, frame)
            rows.append(
                (day, symbol, result.get("action", "HOLD"), result.get("position_size", 0.0))
            )
    signals = pd.DataFrame(rows, columns=["date", "symbol", "action", "position_size"])
    return signals.set_index(["date", "symbol"]).sort_index()


def _bracket_exit(bar, stop: float, target: float, gap: bool) -> Tuple[Optional[float], str]:
    """Exit price and reason if the bracket triggers on this bar (gap: check the open)."""
    if gap and bar.open <= stop:
        return bar.open, "stop_gap"
    if gap and bar.open >= target:
        return bar.open, "target_gap"
    hit_stop, hit_target = bar.low <= stop, bar.high >= target
    if hit_stop:
        return stop, "stop"  # also when both are touched: the conservative reading
    if hit_target:
        return target, "target"
    return None, ""


class LiveTrailingStop:
    """
    The live routines' stop raise (#539), applied to simulated positions.

    It calls the live code, not a copy of it: ``StateReconciler._trailing_level`` (the
    function the morning and evening routines raise stops with) on a
    ``TrailingStopManager`` built as ``CostEfficientTradeCycle`` builds it: no broker, no
    ATR fetcher, the stock ``TradingConfig``. The live tiers then apply as they are
    (climb rate medium: under 2% profit no change, 2-4% breakeven, 4-6% lock 25% of the
    gain, 6-10% lock 50%, 10% and up lock 75%; never down; at least ``MIN_STOP_GAP``
    below the price; a raise needs more than $0.01). The manager's other adjustments
    (ATR, voter tightening, support/resistance) are off or unset on the live path, so
    they stay inert here.

    Simulated prices are split-adjusted, but the live rules round to the cent on the
    price the broker shows, so each call hands the live code the day's raw prices
    (adjusted x ``scale``) and scales the answer back.
    """

    def __init__(self, trailing_config=None):
        """``trailing_config`` replaces the live TrailingStopConfig (tests only)."""
        # Imported here: the default bracket mode needs none of the trading package
        from config_defaults.trading_config import TradingConfig

        from src.trading.orders.trailing_stop_manager import TrailingStopManager
        from src.trading.state.state_reconciler import StateReconciler

        config = TradingConfig()
        self.manager = TrailingStopManager(
            order_manager=None, config=config, trailing_config=trailing_config
        )
        self.reconciler = StateReconciler(
            config=config, trailing_stop_manager=self.manager, position_tracker=None
        )

    def raised_stop(
        self, symbol: str, entry: float, stop: float, price: float, scale: float = 1.0
    ) -> Optional[float]:
        """
        The level the live routines would raise a position's stop to, or None to leave it.

        Args:
            symbol: Ticker (keys the manager's state; no state is kept between calls)
            entry: Entry price (the open the position filled at)
            stop: The stop in force
            price: The price the routine sees (the close for the evening routine; the
                open stands in for the 09:20 pre-market price in the morning sensitivity)
            scale: Raw price per adjusted price on this day (1.0 if prices are raw)
        """
        # The live prices are Python floats, and so is their cent rounding: round(17.555, 2)
        # is 17.55 for the double just under it, where a numpy float rounds it to 17.56
        entry, stop, price, scale = float(entry), float(stop), float(price), float(scale)
        self.manager.stop_states.pop(symbol, None)  # each call registers the position afresh
        try:
            level = self.reconciler._trailing_level(
                symbol,
                {symbol: {"entry_price": entry * scale}},
                stop * scale,
                1,
                price * scale,
                adopt_order_id=symbol,
            )
        finally:
            self.manager.stop_states.pop(symbol, None)
        return None if level is None else level / scale


def simulate(  # noqa: C901
    prices: Dict[str, pd.DataFrame],
    signals: pd.DataFrame,
    rules: LiveRules,
    raw_closes: Optional[Dict[str, pd.Series]] = None,
) -> Tuple[pd.Series, pd.DataFrame]:
    """
    Run the live rules over the signal days.

    Args:
        prices: Per symbol, split-adjusted daily bars (open/high/low/close), date index
        signals: From compute_signals()
        rules: Sizing, limits, exits (``exit_mode``) and costs
        raw_closes: Per symbol, unadjusted closes; whole shares are counted at this
            price level. Without them, at the adjusted level.

    Returns:
        (equity at each close, trades with entry/exit dates, prices, reason and return).
        ``trades.attrs["blocked"]`` counts BUY signals not taken, by reason.
    """
    if rules.exit_mode not in EXIT_MODES:
        raise ValueError(f"exit_mode {rules.exit_mode!r} is not one of {EXIT_MODES}")
    if rules.morning_raise and rules.exit_mode == "bracket":
        raise ValueError("morning_raise needs a trailing exit mode: the bracket never raises")
    if rules.morning_prices is not None and not rules.morning_raise:
        raise ValueError("morning_prices are only used with morning_raise=True")
    plan = rules.partial
    if plan is not None:
        if rules.exit_mode != "trailing":
            raise ValueError("partial exits need exit_mode='trailing' (the plan sets the target)")
        if plan.design not in (1, 2, 3):
            raise ValueError(f"partial design {plan.design!r} is not 1, 2 or 3")
        if plan.unprotected not in ("hold", "exit_next_open"):
            raise ValueError(f"unprotected {plan.unprotected!r} is not hold or exit_next_open")
        if plan.same_bar not in ("stop", "target"):
            raise ValueError(f"same_bar {plan.same_bar!r} is not stop or target")
    trailing = LiveTrailingStop() if rules.exit_mode != "bracket" else None
    has_target = rules.exit_mode != "trailing"
    cost = rules.cost_bps / 1e4
    action = signals["action"].to_dict()
    size = signals["position_size"].to_dict()
    calendar = sorted(signals.index.get_level_values("date").unique())
    # Prior closes from before the first signal day (the warm-up), for first-day sizing
    last_close: Dict[str, float] = {}
    for symbol, frame in prices.items():
        before = frame["close"][frame.index < calendar[0]] if calendar else frame["close"][:0]
        if len(before):
            last_close[symbol] = float(before.iloc[-1])
    cash = rules.initial_capital
    equity_prev = cash
    positions: Dict[str, dict] = {}
    trades: List[dict] = []
    equity = {}
    blocked = {
        "in_position": 0,
        "max_positions": 0,
        "no_prior_close": 0,
        "under_one_share": 0,
        "exposure": 0,
        "cash": 0,
    }
    # raw shares per adjusted share on each day (the later splits' combined factor)
    share_scale = {
        s: (raw_closes[s].reindex(prices[s].index) / prices[s]["close"])
        .ffill()
        .fillna(1.0)
        .to_dict()
        for s in (raw_closes or {})
        if s in prices
    }

    def bar_on(symbol, day):
        frame = prices[symbol]
        return frame.loc[day] if day in frame.index else None

    def slipped(price, reason):
        """Extra slippage on a stop-market fill (#584 sensitivity; off by default)."""
        if not rules.stop_slip_bps:
            return price
        kinds = ("stop_gap",) if rules.stop_slip_gap_only else ("stop", "stop_gap")
        return price * (1 - rules.stop_slip_bps / 1e4) if reason in kinds else price

    def close_position(symbol, day, price, reason):
        nonlocal cash
        pos = positions.pop(symbol)
        price = slipped(price, reason)
        if pos["raised"] and reason in ("stop", "stop_gap"):
            reason = "trail" if reason == "stop" else "trail_gap"  # a raised stop
        fill = price * (1 - cost)
        cash += pos["qty"] * fill
        row = {
            "symbol": symbol,
            "entry_date": pos["entry_date"],
            "entry_price": pos["entry_price"],
            "exit_date": day,
            "exit_price": fill,
            "reason": reason,
            "qty": pos["qty"],
            "return": fill / pos["entry_price"] - 1,
        }
        if "pid" in pos:
            row["pid"] = pos["pid"]
        trades.append(row)

    def raise_stops(day, bars, price_field):
        """Each held position's stop moves to its trailing level at the bar's open or close."""
        for symbol, pos in positions.items():
            bar = bars[symbol]
            if bar is None or pos.get("no_stop"):  # no_stop: design 1, stop shares all sold
                continue
            if price_field == "morning":  # the supplied 09:20 price; none that day: no raise
                price = rules.morning_prices.get(symbol, {}).get(day, float("nan"))
                if not np.isfinite(price):
                    continue
            else:
                price = float(bar[price_field])
            level = trailing.raised_stop(
                symbol,
                pos["ref_price"],
                pos["stop"],
                float(price),
                share_scale.get(symbol, {}).get(day, 1.0),
            )
            if level is not None:
                pos["stop"] = level
                pos["raised"] = True

    next_pid = 0
    stats = {
        "with_plan": 0,
        "no_plan_one_share": 0,
        "same_bar_stop_and_target": 0,
        "orphan_target_shares": 0,
        "target_touched_not_seen": 0,
    }

    def sell_part(symbol, day, qty, price, reason):
        """Sell ``qty`` of a planned position at ``price`` (before costs); closes it at zero."""
        nonlocal cash
        pos = positions[symbol]
        qty = min(qty, pos["qty"])
        price = slipped(price, reason)
        if pos["raised"] and reason in ("stop", "stop_gap"):
            reason = "trail" if reason == "stop" else "trail_gap"
        fill = price * (1 - cost)
        cash += qty * fill
        pos["qty"] -= qty
        trades.append(
            {
                "symbol": symbol,
                "entry_date": pos["entry_date"],
                "entry_price": pos["entry_price"],
                "exit_date": day,
                "exit_price": fill,
                "reason": reason,
                "qty": qty,
                "return": fill / pos["entry_price"] - 1,
                "pid": pos["pid"],
            }
        )
        if pos["qty"] <= 1e-9:
            positions.pop(symbol)

    def partial_day(symbol, day, bar, entry_day):  # noqa: C901
        """
        One session of a position under the partial-exit plan (module docstring, PartialPlan).

        Order of events: the SELL signal and the 09:20 check at the open (not on the entry
        day: the position is bought at the open), a gap through the stop or the limit, the
        day's range against the stop and the limit, then the 15:50 check at the close
        (design 2). Only the open is used before the range, only the close after it.
        """
        pos = positions[symbol]
        design = plan.design
        stop, target = pos["stop"], pos.get("plan_target", float("inf"))
        through = target * (1 + plan.limit_through_bps / 1e4)
        if not entry_day:
            if pos.get("orphan"):  # design 1, exit_next_open: the routine finds no stop
                sell_part(symbol, day, pos["qty"], bar.open, "orphan_exit")
                return
            if design == 2 and pos["t1"] > 0 and bar.open >= target:  # the 09:20 routine
                sell_part(symbol, day, pos["t1"], bar.open, "target_check_open")
                pos["t1"] = 0.0
        t1 = pos["t1"]
        stop_qty = pos["qty"] - t1 if design == 1 else pos["qty"]  # shares the stop covers
        limit_live = design in (1, 3) and t1 > 0
        stopped = target_hit = False
        if not entry_day:  # gaps: the bar's open against the stop in force and the limit
            if stop_qty > 0 and bar.open <= stop:
                stopped = True
                sell_part(symbol, day, stop_qty, bar.open, "stop_gap")
            if limit_live and symbol in positions and bar.open >= target:
                target_hit = True
                sell_part(symbol, day, t1, bar.open, "target_gap")
        if symbol not in positions:
            return
        if not stopped and stop_qty > 0 and bar.low <= stop:
            stopped = True
            both = limit_live and not target_hit and bar.high >= through
            if both:
                stats["same_bar_stop_and_target"] += 1
            if design == 3 and both and plan.same_bar == "target":
                sell_part(symbol, day, t1, target, "target")  # optimistic: the limit came first
                target_hit = True
                sell_part(symbol, day, pos["qty"], stop, "stop")
            else:
                sell_part(symbol, day, stop_qty, stop, "stop")  # design 3: OCO takes the target
        # design 1: the limit is independent of the stop shares; design 3: the stop cancelled it
        if (
            limit_live
            and not target_hit
            and (design == 1 or not stopped)
            and symbol in positions
            and bar.high >= through
        ):
            target_hit = True
            sell_part(symbol, day, t1, target, "target")
        if symbol not in positions:
            return
        if target_hit:
            pos["t1"] = 0.0
        if design == 3 and stopped:
            return
        if design == 1 and stopped:
            # the stop shares are gone; the target shares (if any) rest with no stop
            pos["no_stop"] = True
            if pos["t1"] > 0:
                stats["orphan_target_shares"] += 1
                if plan.unprotected == "exit_next_open":
                    pos["orphan"] = True
            return
        if design == 2 and pos["t1"] > 0:
            if bar.close >= target:  # the 15:50 routine
                sell_part(symbol, day, pos["t1"], bar.close, "target_check_close")
                pos["t1"] = 0.0
            elif bar.high >= target and not pos.get("seen_touch"):
                pos["seen_touch"] = True  # touched in the session but at neither check
                stats["target_touched_not_seen"] += 1

    for day in calendar:
        bars = {sym: bar_on(sym, day) for sym in prices}
        if trailing is not None and rules.morning_raise:
            # The morning routine raises before the open, so the day's range meets the raised
            # stop. The open stands in for the pre-market price; the positions bought at this
            # open are not there yet
            raise_stops(day, bars, "open" if rules.morning_prices is None else "morning")
        # Book as the pre-market decision sees it: exits at or after the open come later
        book = {s: p["qty"] * last_close.get(s, p["entry_price"]) for s, p in positions.items()}
        buying_power = cash

        # Exits at the open on SELL signals, then bracket triggers through the day
        for symbol in list(positions):
            bar = bars[symbol]
            if bar is None:
                continue
            if rules.signal_exit and action.get((day, symbol)) == "SELL":
                close_position(symbol, day, bar.open, "signal")
                continue
            pos = positions[symbol]
            if "pid" in pos:
                partial_day(symbol, day, bar, entry_day=False)
                continue
            price, reason = _bracket_exit(bar, pos["stop"], pos["target"], gap=True)
            if price is not None:
                close_position(symbol, day, price, reason)

        # Entries at the open: strong signals first, then by symbol
        held_value = sum(book.values())
        n_held = len(book)
        buys = [s for s in prices if bars[s] is not None and action.get((day, s)) == "BUY"]
        candidates = [s for s in buys if s not in book]
        blocked["in_position"] += len(buys) - len(candidates)
        candidates.sort(key=lambda s: (-size.get((day, s), 0.0), s))
        for i, symbol in enumerate(candidates):
            if n_held >= rules.max_positions:
                blocked["max_positions"] += len(candidates) - i
                break
            prev_close = last_close.get(symbol)
            if not prev_close:
                blocked["no_prior_close"] += 1
                continue
            scale = share_scale.get(symbol, {}).get(day, 1.0)  # the day's split level
            n_whole = int(rules.position_pct * equity_prev / (prev_close * scale))
            qty = n_whole * scale
            if qty <= 0:
                blocked["under_one_share"] += 1
                continue
            if (held_value + qty * prev_close) / equity_prev > rules.max_exposure_pct:
                blocked["exposure"] += 1
                continue
            bar = bars[symbol]
            fill = bar.open * (1 + cost)
            if qty * fill > buying_power:
                blocked["cash"] += 1
                continue
            buying_power -= qty * fill
            cash -= qty * fill
            held_value += qty * prev_close
            n_held += 1
            positions[symbol] = {
                "qty": qty,
                "entry_price": fill,
                "entry_date": day,
                "ref_price": bar.open,  # the stop and the trailing tiers start from the fill
                "stop": bar.open * (1 - rules.stop_pct),
                "target": bar.open * (1 + rules.target_pct) if has_target else float("inf"),
                "raised": False,
            }
            if plan is not None:
                pos = positions[symbol]
                pos["pid"] = next_pid
                next_pid += 1
                if n_whole >= 2:
                    pos["t1"] = int(n_whole * plan.first_ratio) * scale
                    pos["plan_target"] = bar.open * (1 + plan.target_pct)
                    stats["with_plan"] += 1
                else:
                    pos["t1"] = 0.0
                    stats["no_plan_one_share"] += 1
                partial_day(symbol, day, bar, entry_day=True)
                continue
            price, reason = _bracket_exit(
                bar, positions[symbol]["stop"], positions[symbol]["target"], gap=False
            )
            if price is not None:
                close_position(symbol, day, price, reason)

        if trailing is not None:
            # The evening routine raises each stop from the close; the new stop is first
            # in force on the next session (the day's range was checked against the old one)
            raise_stops(day, bars, "close")

        for symbol, bar in bars.items():
            if bar is not None:
                last_close[symbol] = float(bar.close)
        equity_prev = cash + sum(p["qty"] * last_close[s] for s, p in positions.items())
        equity[day] = equity_prev

    for symbol, pos in positions.items():  # still open at the end: marked, not closed
        row = {
            "symbol": symbol,
            "entry_date": pos["entry_date"],
            "entry_price": pos["entry_price"],
            "exit_date": None,
            "exit_price": last_close[symbol],
            "reason": "open",
            "qty": pos["qty"],
            "return": last_close[symbol] / pos["entry_price"] - 1,
        }
        if "pid" in pos:
            row["pid"] = pos["pid"]
        trades.append(row)
    trades_frame = pd.DataFrame(trades)
    trades_frame.attrs["blocked"] = blocked
    if plan is not None:
        trades_frame.attrs["partial"] = stats
    return pd.Series(equity, name="equity"), trades_frame


def performance(equity: pd.Series) -> dict:
    """Annualized Sharpe (rf 0), CAGR, volatility and max drawdown of an equity curve."""
    returns = equity.pct_change().dropna()
    years = (equity.index[-1] - equity.index[0]).days / 365.25
    drawdown = equity / equity.cummax() - 1
    std = returns.std()
    return {
        "sharpe": float(np.sqrt(252) * returns.mean() / std) if std > 0 else 0.0,
        "cagr_pct": (
            float(((equity.iloc[-1] / equity.iloc[0]) ** (1 / years) - 1) * 100)
            if years > 0
            else 0.0
        ),
        "vol_pct": float(std * np.sqrt(252) * 100),
        "max_drawdown_pct": float(drawdown.min() * 100),
        "n_days": int(len(returns)),
    }


def sharpe_ci(
    returns: pd.Series, block: int = 20, reps: int = 2000, seed: int = 0
) -> Tuple[float, float]:
    """95% circular block-bootstrap interval for the annualized Sharpe ratio."""
    r = returns.dropna().to_numpy()
    n = len(r)
    if n < 2 * block:
        return float("nan"), float("nan")
    rng = np.random.default_rng(seed)
    starts = rng.integers(0, n, size=(reps, int(np.ceil(n / block))))
    offsets = np.arange(block)
    samples = r[((starts[:, :, None] + offsets) % n).reshape(reps, -1)[:, :n]]
    sharpes = np.sqrt(252) * samples.mean(axis=1) / samples.std(axis=1, ddof=1)
    low, high = np.percentile(sharpes, [2.5, 97.5])
    return float(low), float(high)


def paired_sharpe_diff_ci(
    returns_a: pd.Series, returns_b: pd.Series, block: int = 20, reps: int = 2000, seed: int = 0
) -> dict:
    """
    Sharpe(a) - Sharpe(b) with a paired 95% circular block-bootstrap interval (#539).

    Both strategies trade the same signals on the same days, so every replication
    resamples the same blocks of days from both return series: what they share (the
    market, most entries) cancels in the difference. Blocks, replications and seed are
    those of ``sharpe_ci``, so when both series cover the same days (as the runner's
    equity curves do) each Sharpe's own interval is drawn from the same days.

    Args:
        returns_a, returns_b: Daily returns indexed by date; only dates in both are used

    Returns:
        ``diff`` (observed Sharpe difference), ``ci95`` ([low, high]), ``p_value`` (two-sided
        bootstrap, twice the smaller share of replications on one side of zero, at least
        1 / reps) and ``n_days``; plus ``dropped_reps`` when a replication had no finite
        difference (a resample with zero variance), which is left out rather than counted.
    """
    both = pd.concat([returns_a, returns_b], axis=1).dropna().to_numpy()
    n = len(both)
    if n < 2 * block:
        return {
            "diff": float("nan"),
            "ci95": [float("nan")] * 2,
            "p_value": float("nan"),
            "n_days": n,
        }
    a, b = both[:, 0], both[:, 1]
    rng = np.random.default_rng(seed)
    starts = rng.integers(0, n, size=(reps, int(np.ceil(n / block))))
    picks = ((starts[:, :, None] + np.arange(block)) % n).reshape(reps, -1)[:, :n]

    def sharpe(samples):
        return np.sqrt(252) * samples.mean(axis=-1) / samples.std(axis=-1, ddof=1)

    with np.errstate(divide="ignore", invalid="ignore"):
        diffs = sharpe(a[picks]) - sharpe(b[picks])
    finite = diffs[np.isfinite(diffs)]
    if not len(finite):
        return {
            "diff": float("nan"),
            "ci95": [float("nan")] * 2,
            "p_value": float("nan"),
            "n_days": n,
        }
    low, high = np.percentile(finite, [2.5, 97.5])
    shares = (float((finite <= 0).mean()), float((finite >= 0).mean()))
    result = {
        "diff": float(sharpe(a) - sharpe(b)),
        "ci95": [float(low), float(high)],
        "p_value": min(max(2 * min(shares), 1 / len(finite)), 1.0),
        "n_days": n,
    }
    if len(finite) < reps:
        result["dropped_reps"] = reps - len(finite)
    return result
