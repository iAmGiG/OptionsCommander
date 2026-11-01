"""
Why the old RAF (#460) and weekly KAMA (#467) numbers differ: a bridge, one change at a time.

The old runners (ready_aim_fire.py, weekly_kama_strategy.py) are run unchanged on split-adjusted
equity_prices_daily (read-only; they read a local sqlite copy of options_daily_summary that this
checkout does not have), for the symbols and window they used, and then one change at a time:

RAF (old rule: Stoch 5/8/17 + TEMA(28) state machine, buy on a signal above 0.5, sell below -0.3,
95% of cash in one symbol, daily bars, Sharpe of the daily equity curve):
  1. as published: high / low synthesized as close +/- 1%, filled at the close that made the
     signal (look-ahead), no costs
  2. + fill at the next open
  3. + real daily highs and lows
  4. + 5 bps a side
  and buy-and-hold of the same symbol over the same window (the old comparison was against a
  different rule, never against holding).

Weekly KAMA (old rule: 10/2/30 + Fold MA(5), weekly closes, one-week signal shift, fill at the
following weekly close, 95% of cash, Sharpe of the weekly equity curve, sqrt(52)):
  1. as published on weekly closes of Sunday-labelled weeks
  2. + 5 bps a side
  and buy-and-hold of the same weekly series.

Usage:
    ~/miniconda3/envs/OptionsCommander/bin/python -s scripts/research/raf_kama_old_vs_new.py
"""

import argparse
import json
import logging
import os
import sys
import tempfile
from pathlib import Path

import numpy as np
import pandas as pd

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))
os.environ.setdefault("AUTOTRADER_STATE_DIR", tempfile.mkdtemp(prefix="bridge_state_"))
os.environ.setdefault("AUTOTRADER_CACHE_DIR", tempfile.mkdtemp(prefix="bridge_cache_"))

import ready_aim_fire as old_raf  # noqa: E402
import weekly_kama_strategy as old_kama  # noqa: E402

from src.backtesting.research_backtester import (  # noqa: E402
    LAST_VALIDATED_DATE,
    ResearchBacktester,
)

RESULTS_DIR = REPO_ROOT / "docs/08_research/03_strategy_research/results"
RAF_SYMBOLS = ["QQQ", "SPY", "IWM", "TQQQ", "SOXL"]  # old runner's list
KAMA_SYMBOLS = ["SPY", "QQQ", "AAPL", "MSFT", "NVDA", "GOOGL", "AMZN"]  # its DEFAULT_SYMBOLS
RAF_WINDOWS = {
    "2020-2025 (old data)": ("2020-01-01", "2025-12-31"),
    "2009 to 2026-10-09": ("2008-01-02", LAST_VALIDATED_DATE),
}
KAMA_WINDOWS = {
    "2016-2024 (old window)": ("2016-01-01", "2024-12-31"),
    "2009 to 2026-10-09": ("2008-01-02", LAST_VALIDATED_DATE),
}


def load(symbols, start, end):
    bt = ResearchBacktester()
    try:
        frame = bt.fetch_equity_prices(symbols, start, end)
    finally:
        bt.close()
    out = {}
    for s in symbols:
        g = frame[frame["symbol"] == s].drop_duplicates("trading_date", keep="last")
        if len(g) > 300:
            out[s] = (
                g.set_index("trading_date")[["open", "high", "low", "close"]]
                .astype(float)
                .sort_index()
            )
            out[s].index = pd.to_datetime(out[s].index)
    return out


def sharpe(values: pd.Series, periods: int) -> float:
    r = values.pct_change().dropna()
    return float(np.sqrt(periods) * r.mean() / r.std()) if r.std() > 0 else 0.0


def raf_backtest(df, signal, fill, cost_bps, buy_above=0.5, sell_below=-0.3):
    """
    The old backtest's logic with a choice of fill: ``same_close`` (the signal's own close, as
    published) or ``next_open`` (the open after the signal's close); 95% of cash, costs per side.
    """
    cost = cost_bps / 1e4
    close, opn = df["close"].to_numpy(), df["open"].to_numpy()
    sig = signal.to_numpy()
    cash, hold = 10_000.0, 0.0
    values, trades = [], 0
    for i in range(len(df)):
        if fill == "next_open" and i > 0:
            price, s = opn[i], sig[i - 1]
        elif fill == "same_close":
            price, s = close[i], sig[i]
        else:
            price, s = None, 0.0
        if price is not None:
            if s > buy_above and hold == 0:
                shares = cash * 0.95 / (price * (1 + cost))
                cash -= shares * price * (1 + cost)
                hold, trades = shares, trades + 1
            elif s < sell_below and hold > 0:
                cash += hold * price * (1 - cost)
                hold = 0.0
        values.append(cash + hold * close[i])
    return pd.Series(values, index=df.index), trades


def raf_bridge(windows):
    out = {}
    for label, (start, end) in windows.items():
        data = load(RAF_SYMBOLS, start, end)
        rows = {}
        for symbol, bars in data.items():
            bars = bars[(bars.index >= start) & (bars.index <= end)]
            synth = bars.copy()
            synth["high"], synth["low"] = synth["close"] * 1.01, synth["close"] * 0.99
            sig_synth = old_raf.generate_raf_signals(synth)
            sig_real = old_raf.generate_raf_signals(bars)
            steps = {
                "1_as_published": (synth, sig_synth, "same_close", 0.0),
                "2_next_open": (synth, sig_synth, "next_open", 0.0),
                "3_real_high_low": (bars, sig_real, "next_open", 0.0),
                "4_5bps": (bars, sig_real, "next_open", 5.0),
            }
            row = {}
            for name, (frame, signal, fill, bps) in steps.items():
                equity, trades = raf_backtest(frame, signal, fill, bps)
                row[name] = {"sharpe": round(sharpe(equity, 252), 3), "trades": trades}
            row["buy_and_hold_sharpe"] = round(sharpe(bars["close"], 252), 3)
            rows[symbol] = row
        out[label] = {
            "per_symbol": rows,
            "mean_sharpe": {
                step: round(float(np.mean([r[step]["sharpe"] for r in rows.values()])), 3)
                for step in ("1_as_published", "2_next_open", "3_real_high_low", "4_5bps")
            }
            | {
                "buy_and_hold": round(
                    float(np.mean([r["buy_and_hold_sharpe"] for r in rows.values()])), 3
                )
            },
        }
    return out


def kama_bridge(windows):
    out = {}
    for label, (start, end) in windows.items():
        data = load(KAMA_SYMBOLS, start, end)
        rows = {}
        for symbol, bars in data.items():
            bars = bars[(bars.index >= start) & (bars.index <= end)]
            weekly = bars["close"].resample("W").last().dropna().to_frame("close")
            result = old_kama.generate_kama_signals(weekly)
            published = old_kama.backtest_strategy(result, "signal")
            # the old backtest again with 5 bps a side, same shifted-signal logic
            shifted = result["signal"].shift(1).fillna(0).to_numpy()
            cash, hold, values = 10_000.0, 0.0, []
            price = weekly["close"].to_numpy()
            for i in range(len(weekly)):
                if shifted[i] > 0.5 and hold == 0:
                    shares = cash * 0.95 / (price[i] * 1.0005)
                    cash -= shares * price[i] * 1.0005
                    hold = shares
                elif shifted[i] < -0.5 and hold > 0:
                    cash += hold * price[i] * 0.9995
                    hold = 0.0
                values.append(cash + hold * price[i])
            rows[symbol] = {
                "as_published": round(float(published.sharpe_ratio), 3),
                "5bps": round(sharpe(pd.Series(values, index=weekly.index), 52), 3),
                "trades_per_year": round(float(published.trades_per_year), 2),
                "buy_and_hold_sharpe": round(sharpe(weekly["close"], 52), 3),
            }
        keys = ("as_published", "5bps", "buy_and_hold_sharpe")
        out[label] = {
            "per_symbol": rows,
            "mean": {k: round(float(np.mean([r[k] for r in rows.values()])), 3) for k in keys},
        }
    return out


def main():
    parser = argparse.ArgumentParser(description="Old vs new RAF and weekly KAMA numbers")
    parser.add_argument("--output", default="raf_kama_old_vs_new")
    args = parser.parse_args()
    logging.disable(logging.WARNING)
    output = {"raf": raf_bridge(RAF_WINDOWS), "weekly_kama": kama_bridge(KAMA_WINDOWS)}
    for name, block in output.items():
        for label, res in block.items():
            print(f"  {name} {label}: {res.get('mean_sharpe') or res.get('mean')}")
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    path = RESULTS_DIR / f"{args.output}.json"
    with open(path, "w") as f:
        json.dump(output, f, indent=2)
        f.write("\n")
    print(f"  Results saved to {path}")


if __name__ == "__main__":
    main()
