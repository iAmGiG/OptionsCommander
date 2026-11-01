"""
The 09:20 morning stop raise, measured with real 09:20 prices (#623, follows #539).

The #539 study could only stand the day's open in for the price the 09:20 ET routine raises from.
This replaces it with the last pre-market trade before 09:20, from Alpaca's extended-hours 5-minute
bars (fetch_premarket_bars.py; SIP feed, available from 2016), and compares, on the same days and
the same signals, for the two trailing exit modes and the three #539 variants at 0 / 5 / 10 bps:

- E    evening raise only (the #539 headline). Also what "raise only from the previous close" and
       "raise in the evening only" come to on daily bars: the evening raise already uses the close.
- R    morning raise from the real 09:20 price (the live routines)
- O    morning raise from the day's open (the #539 proxy), every day
- Om   the open, but only on the days R has a price (like for like with R)
- Rf   R, but only when the 09:15 bar traded (a price at most 5 minutes old)
- H    morning raise from half the gap: previous close + 0.5 x (R - previous close)
- L    R only when at least $500k traded in the bars 09:00-09:20 (a liquid pre-market)
- G2   R only when the 09:20 price is within 2% of the previous close (do not raise off big gaps)

A day without a 09:20 price (no pre-market trade) has no morning raise for that symbol, which is
the same as E for it. Pre-market prices are raw: they are divided by the split calendar's
raw / adjusted ratio of the day, so they sit on the daily series' split-adjusted scale.

The simulator is src/backtesting/live_rules_sim.py with LiveRules.morning_prices (new). Statistics
are the #539 ones: circular block bootstrap (20-day blocks, 2000 replications) for Sharpe and for
paired Sharpe differences, Newey-West tests of mean daily return differences. The window is
2016-01-04 (the first day with pre-market bars) to 2026-10-09; in-sample here is 2016-2019 and
out-of-sample 2020 on. Data is read-only; no orders, no trading client.

Usage:
    ~/miniconda3/envs/OptionsCommander/bin/python -s scripts/research/morning_raise_real_prices.py \
        --bars <folder from fetch_premarket_bars.py>
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
    ("AUTOTRADER_STATE_DIR", "morning_state_"),
    ("AUTOTRADER_CACHE_DIR", "morning_cache_"),
):
    if _var not in os.environ:
        os.environ[_var] = tempfile.mkdtemp(prefix=_prefix)
        _SCRATCH_DIRS.append(os.environ[_var])


@atexit.register
def _remove_scratch_dirs() -> None:
    for path in _SCRATCH_DIRS:
        shutil.rmtree(path, ignore_errors=True)


import live_rules_baseline as baseline  # noqa: E402
import trailing_vs_bracket as tvb  # noqa: E402

from src.backtesting.live_rules_sim import (  # noqa: E402
    LiveRules,
    compute_signals,
    paired_sharpe_diff_ci,
    performance,
    simulate,
)
from src.backtesting.research_cli import paired_return_test  # noqa: E402

RESULTS_DIR = REPO_ROOT / "docs/08_research/03_strategy_research/results"
OUTPUT_STEM = "morning_raise_real_prices"
WINDOW_START = "2016-01-04"
COSTS_BPS = baseline.COSTS_BPS
PERIODS = {
    "full": (None, None),
    "2016_2019": (None, "2019-12-31"),
    "2020_on": (baseline.SPLIT_DATE, None),
}
PERIOD_LABELS = {"full": "2016 to 2026-10-09", "2016_2019": "2016-2019", "2020_on": "2020 on"}
MODES = ("trailing", "trailing_target")
VARIANTS = tvb.VARIANTS
VARIANT_LABELS = tvb.VARIANT_LABELS
LIQUID_DOLLARS = 500_000.0
GAP_CAP = 0.02
SOURCES = {
    "E": "evening raise only",
    "R": "morning raise from the real 09:20 price",
    "O": "morning raise from the open (the #539 proxy), every day",
    "Om": "morning raise from the open, only on days R has a price",
    "Rf": "R when the 09:15 bar traded (price at most 5 minutes old)",
    "H": "half the gap: previous close + 0.5 x (R - previous close)",
    "L": "R when at least $500k traded in 09:00-09:20",
    "G2": "R when within 2% of the previous close",
}
# (a, b): the paired difference "a - b". MAIN are the tests of the audit's family.
MAIN_COMPARISONS = [("R", "E"), ("H", "E"), ("L", "E"), ("G2", "E")]
EXTRA_COMPARISONS = [
    ("O", "E"),
    ("Om", "E"),
    ("O", "R"),
    ("Om", "R"),
    ("Rf", "E"),
    ("H", "R"),
    ("L", "R"),
    ("G2", "R"),
]
REPORT_COST = 5.0


# ---------------------------------------------------------------------------
# Pre-market prices
# ---------------------------------------------------------------------------


def load_bars(bars_dir: Path) -> pd.DataFrame:
    frames = []
    for path in sorted(bars_dir.glob("*.parquet")):
        symbol = path.stem.rsplit("_", 1)[0]
        f = pd.read_parquet(path)
        if len(f):
            f.insert(0, "symbol", symbol)
            frames.append(f)
    return pd.concat(frames, ignore_index=True)


def daily_morning_table(bars: pd.DataFrame) -> pd.DataFrame:
    """
    Per symbol and date, the last pre-market trade before 09:20 ET.

    Columns: raw 09:20 price (the close of the last 5-minute bar that started before 09:20),
    its age in minutes (0 when the 09:15 bar traded), pre-market bar count, and the dollars
    traded in the bars 09:00-09:20.
    """
    b = bars.copy()
    b["date"] = b["et"].dt.normalize()
    b["start_min"] = b["et"].dt.hour * 60 + b["et"].dt.minute
    b["dollars"] = b["close"] * b["volume"]
    b = b.sort_values(["symbol", "et"])
    last = b.groupby(["symbol", "date"]).tail(1).set_index(["symbol", "date"])
    table = pd.DataFrame(
        {
            "raw_price": last["close"],
            "age_min": 9 * 60 + 20 - (last["start_min"] + 5),
            "last_bar_volume": last["volume"],
        }
    )
    table["n_bars"] = b.groupby(["symbol", "date"]).size()
    late = b[b["start_min"] >= 9 * 60]
    table["dollars_0900_0920"] = late.groupby(["symbol", "date"])["dollars"].sum()
    table["dollars_0900_0920"] = table["dollars_0900_0920"].fillna(0.0)
    return table.reset_index()


def morning_series(table, prices, raw_closes, symbols) -> dict:
    """
    Per symbol, a frame by trading date: adjusted 09:20 price and the pieces the variants use.

    ``adj`` is the raw pre-market price divided by the day's raw / adjusted close ratio (the
    split level of that day), the scale the simulator's daily bars are on.
    """
    out = {}
    for symbol in symbols:
        daily = prices[symbol]
        ratio = (raw_closes[symbol].reindex(daily.index) / daily["close"]).ffill().fillna(1.0)
        t = table[table["symbol"] == symbol].set_index("date").reindex(daily.index)
        frame = pd.DataFrame(index=daily.index)
        frame["adj"] = t["raw_price"] / ratio
        frame["age_min"] = t["age_min"]
        frame["dollars"] = t["dollars_0900_0920"]
        frame["n_bars"] = t["n_bars"]
        frame["prev_close"] = daily["close"].shift(1)
        frame["open"] = daily["open"]
        out[symbol] = frame
    return out


def build_sources(morning: dict) -> dict:
    """Source name -> {symbol: adjusted price series (NaN: no morning raise)}; E is absent."""
    src = {k: {} for k in ("R", "O", "Om", "Rf", "H", "L", "G2")}
    for symbol, f in morning.items():
        real = f["adj"]
        has = real.notna()
        src["R"][symbol] = real
        src["O"][symbol] = f["open"]
        src["Om"][symbol] = f["open"].where(has)
        src["Rf"][symbol] = real.where(f["age_min"] == 0)
        src["H"][symbol] = f["prev_close"] + 0.5 * (real - f["prev_close"])
        src["L"][symbol] = real.where(f["dollars"] >= LIQUID_DOLLARS)
        gap = (real / f["prev_close"] - 1).abs()
        src["G2"][symbol] = real.where(gap <= GAP_CAP)
    return src


def coverage_report(morning: dict, start: str, end: str) -> dict:
    rows = {}
    for symbol, f in morning.items():
        w = f[(f.index >= pd.Timestamp(start)) & (f.index <= pd.Timestamp(end))]
        n = len(w)
        has = w["adj"].notna()
        rows[symbol] = {
            "days": int(n),
            "with_price": int(has.sum()),
            "share_with_price": round(float(has.mean()), 4),
            "share_09_15_bar_traded": round(float((w["age_min"] == 0).mean()), 4),
            "share_stale_over_30_min": round(float((w["age_min"] > 30).sum() / n), 4),
            "median_premarket_bars": float(w["n_bars"].median()),
            "median_dollars_0900_0920": float(w["dollars"].median()),
            "first_day_with_price": str(w.index[has][0].date()) if has.any() else None,
        }
    return rows


def proxy_closeness(morning: dict, start: str, end: str) -> dict:
    """How close the open is to the 09:20 price, on days both exist."""
    parts = []
    for symbol, f in morning.items():
        w = f[(f.index >= pd.Timestamp(start)) & (f.index <= pd.Timestamp(end))].dropna(
            subset=["adj", "open", "prev_close"]
        )
        parts.append(w.assign(symbol=symbol))
    d = pd.concat(parts)
    gap_open = d["open"] / d["prev_close"] - 1
    gap_real = d["adj"] / d["prev_close"] - 1
    diff_bps = (d["open"] / d["adj"] - 1) * 1e4
    up = gap_open > 0.005
    both_up = up & (gap_real > 0)
    slope = float(np.polyfit(gap_open, gap_real, 1)[0])

    def share(mask_num, mask_den):
        return round(float(mask_num.sum() / max(mask_den.sum(), 1)), 4)

    return {
        "n_symbol_days": int(len(d)),
        "open_minus_0920_bps_median": round(float(diff_bps.median()), 2),
        "open_minus_0920_bps_mean": round(float(diff_bps.mean()), 2),
        "abs_open_minus_0920_bps_median": round(float(diff_bps.abs().median()), 2),
        "abs_open_minus_0920_bps_p90": round(float(diff_bps.abs().quantile(0.9)), 2),
        "abs_gap_open_pct_mean": round(float(gap_open.abs().mean() * 100), 3),
        "abs_gap_0920_pct_mean": round(float(gap_real.abs().mean() * 100), 3),
        "corr_gap_open_gap_0920": round(float(gap_open.corr(gap_real)), 4),
        "slope_gap_0920_on_gap_open": round(slope, 4),
        "open_gaps_up_over_0.5pct_days": int(up.sum()),
        "of_those_0920_price_also_above_prev_close": share(both_up, up),
        "of_those_0920_price_below_open": share(up & (d["adj"] < d["open"]), up),
        "of_those_gap_0920_over_half_the_open_gap": share(up & (gap_real > 0.5 * gap_open), up),
        "mean_share_of_up_gap_priced_at_0920": round(
            float((gap_real[up] / gap_open[up]).clip(-2, 2).mean()), 4
        ),
    }


def data_sanity(morning: dict, start: str, end: str) -> dict:
    """Pre-market adjusted price against the day's adjusted open: outliers flag scale errors."""
    big = []
    n = 0
    for symbol, f in morning.items():
        w = f[(f.index >= pd.Timestamp(start)) & (f.index <= pd.Timestamp(end))].dropna(
            subset=["adj", "open"]
        )
        r = w["adj"] / w["open"] - 1
        n += len(w)
        for day, v in r[r.abs() > 0.15].items():
            big.append(
                {
                    "symbol": symbol,
                    "date": str(day.date()),
                    "adj_0920_vs_open_pct": round(float(v) * 100, 1),
                }
            )
    return {"symbol_days_compared": n, "over_15pct_from_open": big}


# ---------------------------------------------------------------------------
# Simulation and statistics
# ---------------------------------------------------------------------------


def paired(variant: str, equities: dict, comparisons, tag: str) -> list:
    rows = []
    for cost in COSTS_BPS:
        for mode in MODES:
            for a, b in comparisons:
                for period, span in PERIODS.items():
                    ea, eb = (
                        equities[(variant, f"{mode}|{a}", cost)],
                        equities[(variant, f"{mode}|{b}", cost)],
                    )
                    ra, rb = tvb.sliced_returns(ea, *span), tvb.sliced_returns(eb, *span)
                    boot = paired_sharpe_diff_ci(ra, rb)
                    rows.append(
                        {
                            "comparison": f"{mode}|{a} - {mode}|{b}",
                            "mode": mode,
                            "a": a,
                            "b": b,
                            "group": tag,
                            "variant": variant,
                            "period": period,
                            "cost_bps": cost,
                            "sharpe_diff": round(boot["diff"], 4),
                            "sharpe_diff_ci95": [round(x, 4) for x in boot["ci95"]],
                            "sharpe_diff_bootstrap_p": round(boot["p_value"], 4),
                            "cagr_diff_pp": round(
                                performance(tvb.sliced_equity(ea, *span))["cagr_pct"]
                                - performance(tvb.sliced_equity(eb, *span))["cagr_pct"],
                                4,
                            ),
                            "return_test": paired_return_test(rb, ra),
                            "n_days": boot["n_days"],
                        }
                    )
    return rows


def summarize(equity, trades, exposure, bench) -> dict:
    s = {name: baseline.period_stats(equity, *span) for name, span in PERIODS.items()}
    s["alpha_vs_equal_weight"] = {
        name: baseline.alpha_vs(equity, bench["equal_weight"], *span)
        for name, span in PERIODS.items()
    }
    s["mean_exposure_pct"] = round(float(exposure.mean() * 100), 2)
    s["trades_full"] = baseline.trade_stats(trades)
    s["trades_2020_on"] = baseline.trade_stats(trades, start=baseline.SPLIT_DATE)
    return s


def run_all(prices, raw_closes, signals, sources, variants, bench_equity):
    scenarios, equities = {}, {}
    for variant in variants:
        spec = VARIANTS[variant]
        scenarios[variant] = {}
        keys = [("bracket", None)] + [(f"{m}|{s}", (m, s)) for m in MODES for s in SOURCES]
        for key, ms in keys:
            scenarios[variant][key] = {}
            for cost in COSTS_BPS:
                if ms is None:
                    rules = LiveRules(
                        position_pct=spec["position_pct"],
                        cost_bps=cost,
                        exit_mode="bracket",
                        signal_exit=spec["signal_exit"],
                    )
                else:
                    mode, src = ms
                    rules = LiveRules(
                        position_pct=spec["position_pct"],
                        cost_bps=cost,
                        exit_mode=mode,
                        signal_exit=spec["signal_exit"],
                        morning_raise=src != "E",
                        morning_prices=None if src == "E" else sources[src],
                    )
                equity, trades = simulate(prices, signals, rules, raw_closes=raw_closes)
                exposure = baseline.exposure_series(equity, trades, prices)
                scenarios[variant][key][f"{int(cost)}bps"] = summarize(
                    equity, trades, exposure, bench_equity
                )
                equities[(variant, key, cost)] = equity
                if cost == REPORT_COST:
                    print(
                        f"  {variant:24} {key:22} full Sharpe "
                        f"{scenarios[variant][key]['5bps']['full']['sharpe']:+.3f}  "
                        f"trades {scenarios[variant][key]['5bps']['trades_full']['n_trades']}",
                        flush=True,
                    )
    return scenarios, equities


# ---------------------------------------------------------------------------
# Output
# ---------------------------------------------------------------------------


def find(rows, a, b, mode, variant, period="full", cost=REPORT_COST):
    return next(
        r
        for r in rows
        if (r["a"], r["b"], r["mode"], r["variant"], r["period"], r["cost_bps"])
        == (a, b, mode, variant, period, cost)
    )


def fmt_diff(r) -> str:
    lo, hi = r["sharpe_diff_ci95"]
    return f"{r['sharpe_diff']:+.3f} [{lo:+.3f}, {hi:+.3f}]"


def render_markdown(out: dict) -> str:
    sc, rows = out["scenarios"], out["paired_tests"] + out["paired_extra"]
    lines = [
        "# The 09:20 Morning Raise with Real Pre-market Prices (#623)",
        "",
        f"Generated by `scripts/research/morning_raise_real_prices.py` on "
        f"{out['run_timestamp'][:10]}. Numbers are in `{OUTPUT_STEM}.json`.",
        "",
        "Sources of the morning raise price: "
        + "; ".join(f"**{k}** {v}" for k, v in SOURCES.items())
        + ".",
        "",
        f"Window {out['window']['start']} to {out['window']['end']}; universe "
        f"{len(out['universe'])} symbols. Paired differences are Sharpe(a) - Sharpe(b) with a paired "
        "20-day-block bootstrap 95% interval.",
        "",
    ]
    for variant in VARIANTS:
        if variant not in sc:
            continue
        lines += [f"## {VARIANT_LABELS[variant]}", ""]
        lines += ["### Sharpe by morning-raise price, full period [95% CI], 5 bps", ""]
        lines += [
            "| Mode | " + " | ".join(SOURCES) + " | bracket |",
            "| --- |" + " --- |" * (len(SOURCES) + 1),
        ]
        for mode in MODES:
            cells = []
            for s in SOURCES:
                st = sc[variant][f"{mode}|{s}"]["5bps"]["full"]
                lo, hi = st["sharpe_ci95"]
                cells.append(f"{st['sharpe']:.3f} [{lo:.2f}, {hi:.2f}]")
            b = sc[variant]["bracket"]["5bps"]["full"]
            cells.append(
                f"{b['sharpe']:.3f} [{b['sharpe_ci95'][0]:.2f}, {b['sharpe_ci95'][1]:.2f}]"
            )
            lines.append(f"| {mode} | " + " | ".join(cells) + " |")
        lines += ["", "### Paired Sharpe differences at 5 bps", ""]
        lines += [
            "| Comparison | Full | 2016-2019 | 2020 on |",
            "| --- | --- | --- | --- |",
        ]
        for mode in MODES:
            for a, b in MAIN_COMPARISONS + EXTRA_COMPARISONS:
                cells = [fmt_diff(find(rows, a, b, mode, variant, p)) for p in PERIODS]
                lines.append(f"| {mode}: {a} - {b} | " + " | ".join(cells) + " |")
        lines.append("")
    return "\n".join(lines)


def main():  # noqa: C901
    parser = argparse.ArgumentParser(description="Morning raise with real 09:20 prices (#623)")
    parser.add_argument("--bars", required=True, help="folder from fetch_premarket_bars.py")
    parser.add_argument("--output", default=OUTPUT_STEM)
    parser.add_argument("--variants", nargs="+", default=list(VARIANTS), choices=list(VARIANTS))
    args = parser.parse_args()
    logging.disable(logging.WARNING)
    os.chdir(REPO_ROOT)

    wanted = baseline.watchlist_symbols()
    prices, raw_closes = baseline.load_prices(wanted)
    symbols = sorted(prices)
    bars = load_bars(Path(args.bars))
    bars = bars[bars["symbol"].isin(symbols)]
    morning = morning_series(daily_morning_table(bars), prices, raw_closes, symbols)
    end = baseline.TEST_END
    coverage = coverage_report(morning, WINDOW_START, end)
    closeness = proxy_closeness(morning, WINDOW_START, end)
    sanity = data_sanity(morning, WINDOW_START, end)
    print(
        f"  coverage: {len(coverage)} symbols; sanity outliers: {len(sanity['over_15pct_from_open'])}"
    )

    signals = compute_signals(
        {s: p["close"] for s, p in prices.items()},
        raw_closes,
        baseline.live_signal_fn(),
        WINDOW_START,
        end,
    )
    calendar = pd.DatetimeIndex(sorted(signals.index.get_level_values("date").unique()))
    bench_equity = baseline.benchmarks(prices, calendar)
    sources = build_sources(morning)
    scenarios, equities = run_all(prices, raw_closes, signals, sources, args.variants, bench_equity)

    main_rows, extra_rows = [], []
    for variant in args.variants:
        main_rows += paired(variant, equities, MAIN_COMPARISONS, "main")
        extra_rows += paired(variant, equities, EXTRA_COMPARISONS, "extra")
    vs_bracket = []
    for variant in args.variants:
        for cost in COSTS_BPS:
            for mode in MODES:
                for src in ("E", "R"):
                    for period, span in PERIODS.items():
                        ra = tvb.sliced_returns(equities[(variant, f"{mode}|{src}", cost)], *span)
                        rb = tvb.sliced_returns(equities[(variant, "bracket", cost)], *span)
                        boot = paired_sharpe_diff_ci(ra, rb)
                        vs_bracket.append(
                            {
                                "comparison": f"{mode}|{src} - bracket",
                                "variant": variant,
                                "period": period,
                                "cost_bps": cost,
                                "sharpe_diff": round(boot["diff"], 4),
                                "sharpe_diff_ci95": [round(x, 4) for x in boot["ci95"]],
                            }
                        )
    output = tvb.to_native(
        {
            "run_timestamp": datetime.datetime.now().isoformat(),
            "issue": "#623",
            "description": "The 09:20 morning raise with real pre-market prices",
            "window": {"start": WINDOW_START, "end": end},
            "universe": symbols,
            "sources": SOURCES,
            "liquid_dollars": LIQUID_DOLLARS,
            "gap_cap": GAP_CAP,
            "coverage": coverage,
            "proxy_closeness": closeness,
            "data_sanity": sanity,
            "signal_counts": {k: int(v) for k, v in signals["action"].value_counts().items()},
            "scenarios": scenarios,
            "paired_tests": main_rows,
            "paired_extra": extra_rows,
            "vs_bracket": vs_bracket,
        }
    )
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    with open(RESULTS_DIR / f"{args.output}.json", "w") as f:
        json.dump(output, f, indent=2)
        f.write("\n")
    with open(RESULTS_DIR / f"{args.output}.md", "w") as f:
        f.write(render_markdown(output))
    print(f"  saved {args.output}.json/.md")


if __name__ == "__main__":
    main()
