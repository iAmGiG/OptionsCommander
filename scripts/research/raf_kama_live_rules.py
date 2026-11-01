"""
RAF (#532) and weekly KAMA (#529) re-tested on the live rules (#574 baseline is the bar).

Both claims came from research before the look-ahead and methodology fixes: signals executed at the
close that produced them, no costs, synthetic high/low for the stochastics, 5-7 symbols, 2020-2025.
This runs them through ``live_rules_sim`` at live timing (signal from bars through d - 1, fill at
day d's open, the risk manager's sizing and limits, the -5% / +8% bracket, 0 / 5 / 10 bps a side) on
the #574 universe, 2009 to 2026-10-09, in-sample 2009-2019 and out-of-sample 2020 on.

RULES, FIXED BEFORE ANY RESULT WAS SEEN (no parameter search). Where an issue leaves a choice, the
plainest reading is used and listed here:

1. RAF alone (#532 pseudo-code, parameters of #532 / raf_research.md): BUY when fast Stoch(5,3)
   %K > %D, medium Stoch(8,5) %K > 50, slow Stoch(17,5) %K > 50, close > TEMA(28) and Fisher > 0;
   SELL on the mirror image; else HOLD. Choices: %K = SMA(smooth) of the raw stochastic over the
   first number of bars, %D = SMA(3) of %K; Fisher is Ehlers' (10 bars, median price), the issue
   gives no parameters; real daily highs and lows. The old research did not use the issue's rule
   (no %D cross, no Fisher, synthetic high/low), so this is not a replication of its code.
2. RAF as third voter: votes of the live MACD and RSI voters (live code, 90-day window) plus RAF.
   Proposed rule (#532): 3 agree = STRONG, 2 agree = MODERATE, fewer = HOLD. Two BUY votes are a
   BUY, two SELL votes a SELL (the sim sizes every entry the same way; the strength only orders
   same-day entries).
3. Weekly KAMA alone, BUY when BULLISH, SELL when BEARISH, else HOLD. #529 and weekly_kama_research.md
   disagree on parameters, so both are run as pre-declared tests:
   3a "research": lookback 10, fast 2, slow 30; BULLISH = KAMA 6-week slope > 0, Fold-MA(5, idx 2)
      3-week slope > 0, weekly close above both (the #467 profile, indicators.py defaults).
   3b "issue": lookback 6, fast 6, slow 7; BULLISH = weekly close > KAMA and KAMA rising over one
      week (the Option A snippet).
   A weekly value is used only once its week has closed: the decision on day d sees the weeks that
   ended before the Monday-Sunday week of d (so Monday through Friday of the current week never
   leak, and a Friday close is used from the next Monday).
4. Weekly KAMA as a trend filter on the live voter: a voter BUY goes through only while the weekly
   state is BULLISH (BEARISH and NEUTRAL hold off new entries); voter SELLs pass. "Reduce size or
   exit" on BEARISH is not modelled (the sim has one size; the exit variant is not run). 4a uses
   the research parameters, 4b the issue's.

Comparison: the #574 baseline (live voter signal, moderate sizing 2%, SELL exits on, bracket), same
days. Statistics as live_rules_baseline.py: Sharpe with a 20-day block-bootstrap interval, alpha
against equal-weight buy-and-hold of the same symbols (Newey-West t), win rate, max drawdown, trade
count, turnover; a paired bootstrap of the Sharpe difference and a Newey-West test of the mean
daily return difference against the baseline (the test the multiple-testing audit collects).

Usage:
    ~/miniconda3/envs/OptionsCommander/bin/python -s scripts/research/raf_kama_live_rules.py
"""

import argparse
import atexit
import datetime
import json
import logging
import os
import shutil
import sys
import tempfile
from pathlib import Path

import numpy as np
import pandas as pd

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

_SCRATCH_DIRS = []
for _var, _prefix in (
    ("AUTOTRADER_STATE_DIR", "rafkama_state_"),
    ("AUTOTRADER_CACHE_DIR", "rafkama_cache_"),
):
    if _var not in os.environ:
        os.environ[_var] = tempfile.mkdtemp(prefix=_prefix)
        _SCRATCH_DIRS.append(os.environ[_var])


@atexit.register
def _remove_scratch_dirs() -> None:
    for path in _SCRATCH_DIRS:
        shutil.rmtree(path, ignore_errors=True)


import live_rules_baseline as baseline  # noqa: E402

from src.backtesting.live_rules_signals import (  # noqa: E402
    KAMA_SPECS,
    kama_filter_fn,
    kama_signal_fn,
    raf_signal_fn,
    third_voter_fn,
)
from src.backtesting.live_rules_sim import (  # noqa: E402
    LiveRules,
    compute_signals,
    paired_sharpe_diff_ci,
    simulate,
)
from src.backtesting.research_cli import paired_return_test  # noqa: E402

RESULTS_DIR = REPO_ROOT / "docs/08_research/03_strategy_research/results"
OUTPUT_STEM = "raf_kama_live_rules"
COSTS_BPS = baseline.COSTS_BPS
HEADLINE_COST = 5.0
PERIODS = {
    "full": (None, None),
    "in_sample_2009_2019": (None, "2019-12-31"),
    "out_of_sample_2020_on": (baseline.SPLIT_DATE, None),
}
LONG_WINDOW_DAYS = 4000  # every signal cuts the window it needs; the weekly KAMA uses all history

CANDIDATES = {
    "raf_alone": "1. RAF alone",
    "raf_third_voter": "2. MACD + RSI + RAF, 2 of 3",
    "kama_research_alone": "3a. Weekly KAMA alone (10/2/30 + Fold MA)",
    "kama_issue_alone": "3b. Weekly KAMA alone (6/6/7)",
    "kama_research_filter": "4a. Voter + weekly KAMA filter (10/2/30 + Fold MA)",
    "kama_issue_filter": "4b. Voter + weekly KAMA filter (6/6/7)",
}


def candidate_fns(live_fn) -> dict:
    return {
        "raf_alone": raf_signal_fn(),
        "raf_third_voter": third_voter_fn(live_fn),
        "kama_research_alone": kama_signal_fn("research"),
        "kama_issue_alone": kama_signal_fn("issue"),
        "kama_research_filter": kama_filter_fn(live_fn, "research"),
        "kama_issue_filter": kama_filter_fn(live_fn, "issue"),
    }


def slice_returns(equity: pd.Series, start=None, end=None) -> pd.Series:
    e = equity
    if start is not None:
        e = e[e.index >= pd.Timestamp(start)]
    if end is not None:
        e = e[e.index <= pd.Timestamp(end)]
    return e.pct_change().dropna()


def turnover(trades: pd.DataFrame, equity: pd.Series, start=None) -> dict:
    """Traded notional (buys plus sells, closed and open legs) per year, in multiples of equity."""
    t = trades if start is None else trades[trades["entry_date"] >= pd.Timestamp(start)]
    e = equity if start is None else equity[equity.index >= pd.Timestamp(start)]
    years = (e.index[-1] - e.index[0]).days / 365.25
    notional = (t["qty"] * t["entry_price"]).sum() + (
        t.loc[t["reason"] != "open", "qty"] * t.loc[t["reason"] != "open", "exit_price"]
    ).sum()
    return {"annual_turnover_x_equity": round(float(notional / e.mean() / years), 3)}


def symbol_table(trades: pd.DataFrame, initial_capital: float) -> dict:
    """Per symbol: trades, win rate, dollar P&L (open trades marked) as % of initial capital."""
    t = trades.copy()
    t["pnl"] = t["qty"] * t["entry_price"] * t["return"]
    out = {}
    for symbol, g in t.groupby("symbol"):
        closed = g[g["reason"] != "open"]
        out[symbol] = {
            "n_trades": int(len(closed)),
            "win_rate_pct": round(float((closed["return"] > 0).mean() * 100), 1)
            if len(closed)
            else None,
            "pnl_pct_of_capital": round(float(g["pnl"].sum() / initial_capital * 100), 2),
        }
    return out


def concentration(table: dict) -> dict:
    pnl = pd.Series({s: v["pnl_pct_of_capital"] for s, v in table.items()}).sort_values(
        ascending=False
    )
    total = float(pnl.sum())
    top3 = pnl.head(3)
    return {
        "total_pnl_pct_of_capital": round(total, 2),
        "symbols_with_profit": int((pnl > 0).sum()),
        "n_symbols_traded": int(len(pnl)),
        "top3": {s: float(v) for s, v in top3.items()},
        "top3_share_of_total": round(float(top3.sum() / total), 2) if total > 0 else None,
        "total_without_top1": round(float(total - pnl.iloc[0]), 2),
        "total_without_top3": round(float(total - top3.sum()), 2),
    }


def run_one(prices, raw_closes, signals, bench_equity, cost):
    rules = LiveRules(position_pct=baseline.MODES["moderate"], cost_bps=cost)
    equity, trades = simulate(prices, signals, rules, raw_closes=raw_closes)
    exposure = baseline.exposure_series(equity, trades, prices)
    out = baseline.summarize(equity, trades, exposure, bench_equity)
    out["buys_not_taken"] = trades.attrs["blocked"]
    out["trades_in_sample"] = baseline.trade_stats(trades, end="2019-12-31")
    out["turnover_full"] = turnover(trades, equity)
    out["turnover_out_of_sample"] = turnover(trades, equity, baseline.SPLIT_DATE)
    table = symbol_table(trades, rules.initial_capital)
    out["by_symbol"] = table
    out["concentration"] = concentration(table)
    return out, equity


def paired_vs_baseline(name, equities):
    rows = []
    for cost in COSTS_BPS:
        for period, span in PERIODS.items():
            ra = slice_returns(equities[(name, cost)], *span)
            rb = slice_returns(equities[("baseline", cost)], *span)
            boot = paired_sharpe_diff_ci(ra, rb)
            rows.append(
                {
                    "candidate": name,
                    "comparison": f"{name} - baseline",
                    "period": period,
                    "cost_bps": cost,
                    "sharpe_diff": round(boot["diff"], 4),
                    "sharpe_diff_ci95": [round(x, 4) for x in boot["ci95"]],
                    "sharpe_diff_bootstrap_p": round(boot["p_value"], 4),
                    "return_test": paired_return_test(rb, ra),
                    "n_days": boot["n_days"],
                }
            )
    return rows


def signal_summary(signals: pd.DataFrame) -> dict:
    counts = signals["action"].value_counts()
    n = len(signals)
    return {k: int(counts.get(k, 0)) for k in ("BUY", "SELL", "HOLD")} | {
        "buy_share_pct": round(float(counts.get("BUY", 0) / n * 100), 1),
        "sell_share_pct": round(float(counts.get("SELL", 0) / n * 100), 1),
    }


def to_native(obj):
    if isinstance(obj, dict):
        return {str(k): to_native(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [to_native(v) for v in obj]
    if isinstance(obj, (np.integer,)):
        return int(obj)
    if isinstance(obj, (np.floating, float)):
        return None if not np.isfinite(obj) else float(obj)
    if isinstance(obj, (np.bool_,)):
        return bool(obj)
    return obj


def main():
    parser = argparse.ArgumentParser(description="RAF and weekly KAMA on the live rules")
    parser.add_argument("--output", default=OUTPUT_STEM)
    args = parser.parse_args()
    logging.disable(logging.WARNING)
    os.chdir(REPO_ROOT)

    print("=" * 60)
    print("  RAF (#532) AND WEEKLY KAMA (#529) ON THE LIVE RULES")
    print("=" * 60)
    wanted = baseline.watchlist_symbols()
    prices, raw_closes = baseline.load_prices(wanted)
    missing = sorted(set(wanted) - set(prices))
    print(f"  Universe: {len(prices)} of {len(wanted)} (missing: {missing})")
    closes = {s: p["close"] for s, p in prices.items()}
    live_fn = baseline.live_signal_fn()

    all_signals = {}
    print("  Baseline signals (live voter, 90-day window)...")
    all_signals["baseline"] = compute_signals(
        closes, raw_closes, live_fn, baseline.TEST_START, baseline.TEST_END
    )
    for name, fn in candidate_fns(live_fn).items():
        print(f"  Signals: {name}...", flush=True)
        all_signals[name] = compute_signals(
            closes,
            raw_closes,
            fn,
            baseline.TEST_START,
            baseline.TEST_END,
            lookback_days=LONG_WINDOW_DAYS,
            bars=prices,
        )
    calendar = pd.DatetimeIndex(
        sorted(all_signals["baseline"].index.get_level_values("date").unique())
    )
    bench_equity = baseline.benchmarks(prices, calendar)

    scenarios, equities = {}, {}
    for name, signals in all_signals.items():
        scenarios[name] = {}
        for cost in COSTS_BPS:
            result, equity = run_one(prices, raw_closes, signals, bench_equity, cost)
            scenarios[name][f"{int(cost)}bps"] = result
            equities[(name, cost)] = equity
        h = scenarios[name]["5bps"]
        a = h["alpha_vs_equal_weight"]["full"]
        print(
            f"  {name:22} 5bps Sharpe {h['full']['sharpe']:+.3f} {h['full']['sharpe_ci95']} "
            f"OOS {h['out_of_sample_2020_on']['sharpe']:+.3f}  alpha {a['alpha_ann_pct']:+.2f}% "
            f"(t={a['alpha_t']:+.2f})  trades {h['trades_full'].get('n_trades')}"
        )

    paired = []
    for name in CANDIDATES:
        paired += paired_vs_baseline(name, equities)
    for r in paired:
        if r["cost_bps"] == HEADLINE_COST and r["period"] == "full":
            print(
                f"  {r['comparison']:34} paired Sharpe diff {r['sharpe_diff']:+.3f} "
                f"{r['sharpe_diff_ci95']}  NW p={r['return_test']['p_value'] if r['return_test'] else None}"
            )

    bench = {
        name: {period: baseline.period_stats(eq, *span) for period, span in PERIODS.items()}
        for name, eq in bench_equity.items()
    }
    output = to_native(
        {
            "run_timestamp": datetime.datetime.now().isoformat(),
            "issue": "#532, #529",
            "description": "RAF and weekly KAMA on the live rules (#574 baseline) at live timing",
            "methodology": {
                "doc": __doc__,
                "kama_specs": KAMA_SPECS,
                "candidates": CANDIDATES,
                "sizing": "moderate, 2% of equity per position, SELL-signal exits on, bracket -5%/+8%",
                "costs_bps_per_side": COSTS_BPS,
                "headline_cost_bps": HEADLINE_COST,
                "data": f"equity_prices_daily, split-adjusted, read-only, {baseline.WARMUP_START} to {baseline.TEST_END}",
                "periods": {
                    "decisions": f"{baseline.TEST_START} to {baseline.TEST_END}",
                    "in_sample": "2009-2019",
                    "out_of_sample": "2020 on",
                },
            },
            "universe": sorted(prices),
            "missing_symbols": missing,
            "signal_summary": {n: signal_summary(s) for n, s in all_signals.items()},
            "scenarios": scenarios,
            "paired_tests": paired,
            "benchmarks": bench,
        }
    )
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    path = RESULTS_DIR / f"{args.output}.json"
    with open(path, "w") as f:
        json.dump(output, f, indent=2)
        f.write("\n")
    print(f"\n  Results saved to {path}")


if __name__ == "__main__":
    main()
