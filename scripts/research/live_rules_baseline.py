"""
Live-rules baseline for paper validation (#574, stage A).

Backtests the rules the live CLI actually runs (see src/backtesting/live_rules_sim.py):
the live VotingSignal.evaluate_ranked_voting signal on a 90-day window, decided on the
previous close and filled at the next open, with the risk manager's sizing and limits
and the 5% / 8% market brackets. It replaces the 0.856 Sharpe claim (experiment 293:
in-sample AAPL 2024, MACD 12/26/9, filled at the signal's own close, no costs).

Universe: the scanner's default watchlist (config_defaults/scanner_config.yaml) that the
gex database covers. It was picked with hindsight (large winners), so the benchmark is
buy-and-hold of the same symbols.

Data: split-adjusted equity_prices_daily (read-only), 2008 warm-up, decisions from
2009-01-02 to 2026-10-09 (vendor break). In-sample 2009-2019, out-of-sample 2020 onward.

Usage:
    ~/miniconda3/envs/AutoGex/bin/python scripts/research/live_rules_baseline.py
"""

import argparse
import datetime
import logging
import os
import sys
import tempfile
from pathlib import Path

import pandas as pd
import statsmodels.api as sm
import yaml

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

# Research never touches the user's state: the voter's ranking manager opens state/user.db
os.environ.setdefault("AUTOTRADER_STATE_DIR", tempfile.mkdtemp(prefix="baseline_state_"))

from src.backtesting.live_rules_sim import (  # noqa: E402
    LiveRules,
    compute_signals,
    performance,
    sharpe_ci,
    simulate,
)
from src.backtesting.research_backtester import (  # noqa: E402
    LAST_VALIDATED_DATE,
    ResearchBacktester,
)

WARMUP_START = "2008-01-02"
TEST_START = "2009-01-02"
TEST_END = LAST_VALIDATED_DATE
SPLIT_DATE = "2020-01-01"
MODES = {"moderate": 0.02, "conservative": 0.01}  # trading_modes.yaml risk_per_trade
COSTS_BPS = [0.0, 5.0, 10.0]
RESULTS_DIR = Path("docs/08_research/03_strategy_research/results")


def watchlist_symbols() -> list:
    with open("config_defaults/scanner_config.yaml") as f:
        cfg = yaml.safe_load(f)
    groups = cfg["default_watchlist"].values()
    return [s for group in groups if isinstance(group, list) for s in group if isinstance(s, str)]


def load_prices(symbols: list):
    """Split-adjusted OHLC per symbol, and raw closes for the as-of price level."""
    bt = ResearchBacktester()
    try:
        adjusted = bt.fetch_equity_prices(symbols, WARMUP_START, TEST_END)
        raw = bt.fetch_equity_prices(symbols, WARMUP_START, TEST_END, adjust_splits=False)
    finally:
        bt.close()
    prices, raw_closes = {}, {}
    for symbol in symbols:
        a = adjusted[adjusted["symbol"] == symbol].drop_duplicates("trading_date", keep="last")
        r = raw[raw["symbol"] == symbol].drop_duplicates("trading_date", keep="last")
        if len(a) < 300:
            continue
        frame = a.set_index("trading_date")[["open", "high", "low", "close"]].astype(float)
        prices[symbol] = frame.sort_index()
        raw_closes[symbol] = r.set_index("trading_date")["close"].astype(float).sort_index()
    return prices, raw_closes


def live_signal_fn():
    """
    The live CLI's signal: VotingSignal.evaluate_ranked_voting, computed by VotingSignal
    (no LLM agent or client, no events; #606).
    """
    from src.trading.instruments.voting_signal import VotingSignal

    return VotingSignal().evaluate_ranked_voting


def trade_stats(trades: pd.DataFrame, start=None, end=None) -> dict:
    """Closed-trade statistics by entry date; positions still open are only counted."""
    t = trades
    if start is not None:
        t = t[t["entry_date"] >= pd.Timestamp(start)]
    if end is not None:
        t = t[t["entry_date"] <= pd.Timestamp(end)]
    closed = t[t["reason"] != "open"]
    if closed.empty:
        return {"n_trades": 0, "n_open_at_end": int(len(t))}
    held = (pd.to_datetime(closed["exit_date"]) - pd.to_datetime(closed["entry_date"])).dt.days
    return {
        "n_trades": int(len(closed)),
        "n_open_at_end": int(len(t) - len(closed)),
        "win_rate_pct": round(float((closed["return"] > 0).mean() * 100), 1),
        "mean_return_pct": round(float(closed["return"].mean() * 100), 3),
        "median_return_pct": round(float(closed["return"].median() * 100), 3),
        "mean_hold_days": round(float(held.mean()), 1),
        "exit_reasons": {k: int(v) for k, v in closed["reason"].value_counts().items()},
    }


def period_stats(equity: pd.Series, start=None, end=None) -> dict:
    e = equity
    if start is not None:
        e = e[e.index >= pd.Timestamp(start)]
    if end is not None:
        e = e[e.index <= pd.Timestamp(end)]
    stats = performance(e)
    low, high = sharpe_ci(e.pct_change())
    stats["sharpe_ci95"] = [round(low, 3), round(high, 3)]
    return {k: (round(v, 3) if isinstance(v, float) else v) for k, v in stats.items()}


def benchmarks(prices: dict, calendar: pd.DatetimeIndex) -> dict:
    """Equal-weight (daily rebalanced) and SPY buy-and-hold equity on the same days."""
    closes = pd.DataFrame({s: p["close"] for s, p in prices.items()}).reindex(calendar)
    returns = closes.pct_change()
    ew = (1 + returns.mean(axis=1).fillna(0)).cumprod()
    out = {"equal_weight": ew}
    if "SPY" in closes:
        out["spy"] = (1 + returns["SPY"].fillna(0)).cumprod()
    return out


def alpha_vs(equity: pd.Series, bench: pd.Series, start=None, end=None) -> dict:
    """Daily OLS of strategy on benchmark returns: annualized alpha, beta, Newey-West t."""
    data = pd.concat([equity, bench], axis=1, keys=["s", "b"])
    if start is not None:  # sliced before differencing, as in period_stats
        data = data[data.index >= pd.Timestamp(start)]
    if end is not None:
        data = data[data.index <= pd.Timestamp(end)]
    data = data.pct_change().dropna()
    fit = sm.OLS(data["s"], sm.add_constant(data["b"])).fit(cov_type="HAC", cov_kwds={"maxlags": 5})
    return {
        "alpha_ann_pct": round(float(fit.params["const"] * 252 * 100), 3),
        "alpha_t": round(float(fit.tvalues["const"]), 2),
        "alpha_p": round(float(fit.pvalues["const"]), 3),
        "beta": round(float(fit.params["b"]), 3),
        "r2": round(float(fit.rsquared), 3),
    }


def summarize(equity: pd.Series, trades: pd.DataFrame, exposure: pd.Series, bench: dict) -> dict:
    periods = {
        "full": (None, None),
        "in_sample_2009_2019": (None, "2019-12-31"),
        "out_of_sample_2020_on": (SPLIT_DATE, None),
    }
    return {
        **{name: period_stats(equity, *span) for name, span in periods.items()},
        "alpha_vs_equal_weight": {
            name: alpha_vs(equity, bench["equal_weight"], *span) for name, span in periods.items()
        },
        "mean_exposure_pct": round(float(exposure.mean() * 100), 2),
        "trades_full": trade_stats(trades),
        "trades_out_of_sample": trade_stats(trades, start=SPLIT_DATE),
    }


def exposure_series(equity: pd.Series, trades: pd.DataFrame, prices: dict) -> pd.Series:
    """Share of equity invested at each close."""
    invested = pd.Series(0.0, index=equity.index)
    for t in trades.itertuples(index=False):
        held = equity.index >= t.entry_date
        if pd.notna(t.exit_date):  # NaT: still open at the end
            held &= equity.index < t.exit_date
        days = equity.index[held]
        closes = prices[t.symbol]["close"].reindex(days).ffill()
        invested.loc[days] += t.qty * closes.to_numpy()
    return invested / equity


def main():  # noqa: C901
    parser = argparse.ArgumentParser(description="Live-rules baseline (#574 stage A)")
    parser.add_argument("--output", default="live_rules_baseline.yaml")
    args = parser.parse_args()
    logging.disable(logging.WARNING)  # the voter logs every call

    print("=" * 60)
    print("  LIVE-RULES BASELINE FOR PAPER VALIDATION (#574)")
    print("=" * 60)
    wanted = watchlist_symbols()
    prices, raw_closes = load_prices(wanted)
    missing = sorted(set(wanted) - set(prices))
    print(f"  Universe: {len(prices)} of {len(wanted)} watchlist symbols (missing: {missing})")

    print("  Computing live signals (about 2 ms per symbol-day)...")
    signals = compute_signals(
        {s: p["close"] for s, p in prices.items()},
        raw_closes,
        live_signal_fn(),
        TEST_START,
        TEST_END,
    )
    counts = signals["action"].value_counts().to_dict()
    print(f"  Signals: {len(signals)} symbol-days {counts}")

    calendar = pd.DatetimeIndex(sorted(signals.index.get_level_values("date").unique()))
    bench_equity = benchmarks(prices, calendar)
    scenarios = {}
    for mode, pct in MODES.items():
        for cost in COSTS_BPS:
            rules = LiveRules(position_pct=pct, cost_bps=cost)
            equity, trades = simulate(prices, signals, rules, raw_closes=raw_closes)
            exposure = exposure_series(equity, trades, prices)
            key = f"{mode}_{int(cost)}bps"
            scenarios[key] = summarize(equity, trades, exposure, bench_equity)
            scenarios[key]["buys_not_taken"] = trades.attrs["blocked"]
            oos = scenarios[key]["out_of_sample_2020_on"]
            oos_alpha = scenarios[key]["alpha_vs_equal_weight"]["out_of_sample_2020_on"]
            print(
                f"  {key:20} full Sharpe {scenarios[key]['full']['sharpe']:+.3f}  "
                f"OOS {oos['sharpe']:+.3f} {oos['sharpe_ci95']}  "
                f"OOS alpha {oos_alpha['alpha_ann_pct']:+.2f}% (t={oos_alpha['alpha_t']:+.2f})  "
                f"trades {scenarios[key]['trades_full']['n_trades']}  "
                f"exposure {scenarios[key]['mean_exposure_pct']}%"
            )

    bench = {}
    for name, eq in bench_equity.items():
        bench[name] = {
            "full": period_stats(eq),
            "in_sample_2009_2019": period_stats(eq, end="2019-12-31"),
            "out_of_sample_2020_on": period_stats(eq, start=SPLIT_DATE),
        }
        print(
            f"  {name:20} full Sharpe {bench[name]['full']['sharpe']:+.3f}  OOS {bench[name]['out_of_sample_2020_on']['sharpe']:+.3f}"
        )

    output = {
        "run_timestamp": datetime.datetime.now().isoformat(),
        "issue": "#574",
        "description": "Live CLI rules backtested with live timing (replaces the 0.856 Sharpe claim)",
        "methodology": {
            "signal": "VotingSignal.evaluate_ranked_voting (MACD 13/34/8 hist beyond +/-0.1, RSI 14 at 30/70), 90-calendar-day window rescaled to the prior day's raw price level",
            "timing": "signal from bars through d-1; fills at day d open",
            "entries": "BUY with no position at the start of the day; whole shares (at the raw price) of position_pct x prior equity at the prior close; max 10 positions and 80% exposure counted before the open; cash",
            "initial_capital": LiveRules().initial_capital,
            "exits": "SELL signal at the open; else bracket stop -5% / target +8% from the fill on the day's range; gaps fill at the open; both touched counts as the stop",
            "costs_bps_per_side": COSTS_BPS,
            "modes_position_pct": MODES,
            "data": "equity_prices_daily, split-adjusted (gex.corporate_events), dividends excluded, read-only",
            "periods": {
                "warmup_from": WARMUP_START,
                "test": f"{TEST_START} to {TEST_END}",
                "out_of_sample_from": SPLIT_DATE,
            },
            "sharpe": "annualized, rf 0; 95% circular block bootstrap (20-day blocks, 2000 reps)",
            "alpha": "daily OLS on equal-weight universe returns, annualized intercept, Newey-West (5 lags) t; cash earns 0",
            "not_modelled": [
                "trailing-stop adjustments from the morning routine",
                "intraday decisions (the CLI can also trade on a partial day)",
                "IEX vs consolidated bars",
                "GTC renewal (assumed, as #587 now does)",
            ],
            "caveats": [
                "universe picked with hindsight; benchmark is buy-and-hold of the same symbols",
                "MACD threshold is in dollars, so signal frequency depends on price level",
            ],
        },
        "universe": sorted(prices),
        "missing_symbols": missing,
        "signal_counts": {k: int(v) for k, v in counts.items()},
        "scenarios": scenarios,
        "benchmarks": bench,
    }
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    path = RESULTS_DIR / args.output
    with open(path, "w") as f:
        yaml.dump(output, f, default_flow_style=False, sort_keys=False)
    print(f"\n  Results saved to {path}")


if __name__ == "__main__":
    main()
