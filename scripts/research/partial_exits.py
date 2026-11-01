"""
Partial exits against the live trailing exit, three implementations (#584).

#584 asks whether selling part of a position at a profit target improves on the live exit (the
trailing stop, raised by the evening routine only since #649), and whether it matters how the
plan reaches the broker. Alpaca rejects a partial limit sell while a stop holds all the shares, so
the issue offers three designs:

1. shrink the stop: the target shares rest as a limit sell with no stop, the others have the
   trailing stop;
2. software targets: every share keeps the trailing stop, and the 09:20 and 15:50 routines sell
   the target shares at the market when the price is at or past the target;
3. per-tranche orders: each tranche has its own stop and, for the target shares, a limit sell.

The plan is fixed here before any result is read, from the code and config the project already
has: ``PartialExitManager`` (two targets, split [0.5, 0.5], the first a limit sell at
``target_1_pct`` over the entry, the second "trailing", i.e. the rest keeps the trailing stop).
``TradingModeManager`` configures the same split for every mode and ``target_1_pct`` of 4%
(conservative), 5% (moderate, the live mode) and 6% (aggressive); but ``from_mode_manager``,
the only constructor that takes a mode, ignores the mode's value and hard-codes 4%, as does the
class default. So the plan that would be registered is 4%, and the one the moderate config
describes is 5%: both are run (T4 and T5), nothing else. Shares are whole: the first target
gets ``int(n / 2)`` of ``n`` shares, the rest the remainder; a one-share position has no plan.
The 8% target of the old bracket is not part of the plan.

Designs, the rules the live system would plausibly apply, and the fill assumptions are in
``src/backtesting/live_rules_sim.PartialPlan``. Variants tested (a small stated set):

- D1h: design 1; if the stop shares are stopped out, the target shares stay as resting limits
  (until the target, a SELL signal, or the end of the data)
- D1x: design 1; the next routine finds a position with no stop and sells the target shares at
  the next open
- D2: design 2 (checks at the open and the close)
- D3: design 3 (the conservative same-bar reading: a bar touching stop and target is the stop)

Everything else is the #539 setup: the same signals, sizing, limits, timing, costs (0/5/10 bps a
side, 5 the headline), the three variants (SELL exits off matches live), periods, bootstrap and
tests. The baseline is the evening-only trailing exit (`trailing`); the fixed bracket and the old
`trailing_target` are shown for reference.

Sensitivities (SELL exits off, 5 bps; not tests of their own): same-bar order in design 3,
extra stop slippage (17 bps, the live QQQ fill, on gap fills only and on every stop fill), and
limits that need the high to trade 10 bps through the target.

Usage:
    ~/miniconda3/envs/OptionsCommander/bin/python -s scripts/research/partial_exits.py
"""

import argparse
import atexit
import datetime
import json
import logging
import multiprocessing
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
    ("AUTOTRADER_STATE_DIR", "partial_state_"),
    ("AUTOTRADER_CACHE_DIR", "partial_cache_"),
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
    PartialPlan,
    compute_signals,
    paired_sharpe_diff_ci,
    simulate,
)

RESULTS_DIR = REPO_ROOT / "docs/08_research/03_strategy_research/results"
OUTPUT_STEM = "partial_exits"
COSTS_BPS = baseline.COSTS_BPS
PERIODS = tvb.PERIODS
PERIOD_LABELS = tvb.PERIOD_LABELS
VARIANTS = tvb.VARIANTS
VARIANT_LABELS = tvb.VARIANT_LABELS
REPORT_COST = 5.0
LEAD_VARIANT = "moderate_no_signal_exit"
SLIP_BPS = 17.0  # the one live stop fill so far: 17 bps below the open (QQQ, 2026-10-30)
THROUGH_BPS = 10.0

TARGETS = {"T4": 0.04, "T5": 0.05}
DESIGNS = {
    "D1h": {"design": 1, "unprotected": "hold"},
    "D1x": {"design": 1, "unprotected": "exit_next_open"},
    "D2": {"design": 2},
    "D3": {"design": 3},
}
DESIGN_LABELS = {
    "D1h": "design 1, shrink the stop (unprotected shares keep resting)",
    "D1x": "design 1, shrink the stop (unprotected shares sold at the next open)",
    "D2": "design 2, software targets (checks at the open and the close)",
    "D3": "design 3, per-tranche stop + limit",
}
BASELINE = "trailing"
REFERENCES = ("bracket", "trailing_target")
PLANNED = [f"{d}_{t}" for t in TARGETS for d in DESIGNS]  # D1h_T4, D1x_T4, D2_T4, D3_T4, ...
ALL_KEYS = [BASELINE, *REFERENCES, *PLANNED]
LABELS = {
    "bracket": "bracket (fixed -5% / +8%), reference",
    "trailing": "trailing, evening raise only (baseline, live)",
    "trailing_target": "trailing + 8% target on all shares, reference",
    **{
        f"{d}_{t}": f"{DESIGN_LABELS[d]}, first target +{int(p * 100)}%"
        for t, p in TARGETS.items()
        for d in DESIGNS
    },
}
# Paired comparisons; the first group is the audit's family.
FAMILY = [(key, BASELINE) for key in PLANNED]
EXTRA = (
    [(key, "bracket") for key in PLANNED]
    + [("trailing_target", BASELINE)]
    + [
        (f"{a}_{t}", f"{b}_{t}")
        for t in TARGETS
        for a, b in (("D3", "D2"), ("D3", "D1h"), ("D2", "D1h"), ("D1x", "D1h"), ("D3", "D1x"))
    ]
)

# Sensitivities: name -> (rules overrides, plan overrides, keys run)
SENSITIVITIES = {
    "same_bar_target_first": ({}, {"same_bar": "target"}, ["D3_T4", "D3_T5"]),
    "slip_gap_only": (
        {"stop_slip_bps": SLIP_BPS, "stop_slip_gap_only": True},
        {},
        [BASELINE, *PLANNED],
    ),
    "slip_all_stops": (
        {"stop_slip_bps": SLIP_BPS, "stop_slip_gap_only": False},
        {},
        [BASELINE, *PLANNED],
    ),
    "limit_through_10bps": (
        {},
        {"limit_through_bps": THROUGH_BPS},
        ["D1h_T4", "D3_T4", "D1h_T5", "D3_T5"],
    ),
}

_WORLD = {}  # prices, raw_closes, signals, bench: set before the pool forks


def make_rules(key, spec, cost, rules_extra=None, plan_extra=None):
    """The LiveRules of one configuration (key of ALL_KEYS), variant spec and cost."""
    common = dict(
        position_pct=spec["position_pct"],
        cost_bps=cost,
        signal_exit=spec["signal_exit"],
        **(rules_extra or {}),
    )
    if key in ("bracket", "trailing", "trailing_target"):
        return LiveRules(exit_mode=key, **common)
    design, target = key.split("_")
    plan = PartialPlan(target_pct=TARGETS[target], **DESIGNS[design], **(plan_extra or {}))
    return LiveRules(exit_mode="trailing", partial=plan, **common)


def _work(job):
    variant, key, cost, rules_extra, plan_extra, keep_trades = job
    prices, raw_closes, signals = _WORLD["prices"], _WORLD["raw_closes"], _WORLD["signals"]
    drop = _WORLD.get("drop")
    if drop:
        prices = {s: p for s, p in prices.items() if s != drop}
    rules = make_rules(key, VARIANTS[variant], cost, rules_extra, plan_extra)
    equity, trades = simulate(prices, signals, rules, raw_closes=raw_closes)
    attrs = dict(trades.attrs)
    return (variant, key, cost, rules_extra, plan_extra), equity, trades, attrs


def positions_of(trades: pd.DataFrame) -> pd.DataFrame:
    """One row per position: legs of a planned position combined (quantity-weighted)."""
    if "pid" not in trades.columns:
        return trades
    rows = []
    for _, g in trades.groupby("pid", sort=False):
        qty = g["qty"].sum()
        still_open = (g["reason"] == "open").any()
        last = g.iloc[-1] if still_open else g.loc[g["exit_date"].idxmax()]
        rows.append(
            {
                "symbol": g["symbol"].iloc[0],
                "entry_date": g["entry_date"].iloc[0],
                "entry_price": g["entry_price"].iloc[0],
                "exit_date": None if still_open else last["exit_date"],
                "exit_price": float((g["qty"] * g["exit_price"]).sum() / qty),
                "reason": "open" if still_open else last["reason"],
                "qty": qty,
                "return": float((g["qty"] * g["return"]).sum() / qty),
            }
        )
    return pd.DataFrame(rows)


def summary_of(equity, trades, bench_equity, prices) -> dict:
    exposure = baseline.exposure_series(equity, trades, prices)
    pos = positions_of(trades)
    s = baseline.summarize(equity, pos, exposure, bench_equity)
    s["trades_in_sample"] = baseline.trade_stats(pos, end="2019-12-31")
    s["buys_not_taken"] = trades.attrs.get("blocked", {})
    s["legs"] = leg_stats(trades, equity)
    return s


def leg_stats(trades: pd.DataFrame, equity: pd.Series) -> dict:
    """Legs (fills), turnover and, for planned runs, how often the target was reached."""
    closed = trades[trades["reason"] != "open"]
    years = (equity.index[-1] - equity.index[0]).days / 365.25
    bought = (trades["qty"] * trades["entry_price"]).sum()
    sold = (closed["qty"] * closed["exit_price"]).sum()
    out = {
        "n_legs": int(len(closed)),
        "turnover_per_year_x_equity": round(float((bought + sold) / 2 / equity.mean() / years), 3),
        "leg_reasons": {k: int(v) for k, v in closed["reason"].value_counts().items()},
    }
    if "pid" in trades.columns:
        reached = trades[trades["reason"].str.startswith("target")]["pid"].nunique()
        n_pos = trades["pid"].nunique()
        out["positions"] = int(n_pos)
        out["positions_with_target_leg"] = int(reached)
        out["target_reached_pct"] = round(100 * reached / n_pos, 1)
        out.update({k: int(v) for k, v in trades.attrs.get("partial", {}).items()})
    return out


def run_jobs(jobs, workers):
    ctx = multiprocessing.get_context("fork")
    with ctx.Pool(workers) as pool:
        return list(pool.imap_unordered(_work, jobs, chunksize=1))


def per_symbol(trades_by_key: dict) -> dict:
    """Per symbol: positions, P&L (legs, open ones marked) and mean position return."""
    out = {}
    for key, trades in trades_by_key.items():
        pnl = (trades["qty"] * (trades["exit_price"] - trades["entry_price"])).groupby(
            trades["symbol"]
        )
        pos = positions_of(trades)
        out[key] = {
            sym: {
                "positions": int(len(g)),
                "pnl": round(float(pnl.sum()[sym]), 2),
                "mean_return_pct": round(float(g["return"].mean() * 100), 3),
            }
            for sym, g in pos.groupby("symbol")
        }
    return out


def concentration(by_symbol: dict, key: str, base: str = BASELINE) -> dict:
    """How much of the P&L difference from the baseline sits in a few symbols."""
    syms = sorted(set(by_symbol[key]) | set(by_symbol[base]))
    diff = {
        s: by_symbol[key].get(s, {"pnl": 0.0})["pnl"] - by_symbol[base].get(s, {"pnl": 0.0})["pnl"]
        for s in syms
    }
    total = sum(diff.values())
    ordered = sorted(diff.items(), key=lambda kv: -abs(kv[1]))
    gross = sum(abs(v) for v in diff.values())
    return {
        "total_pnl_diff": round(total, 2),
        "n_symbols_better": int(sum(v > 0 for v in diff.values())),
        "n_symbols_worse": int(sum(v < 0 for v in diff.values())),
        "top3_share_of_gross_abs_diff_pct": round(
            100 * sum(abs(v) for _, v in ordered[:3]) / gross, 1
        )
        if gross
        else 0.0,
        "top3": [(s, round(v, 2)) for s, v in ordered[:3]],
        "diff_by_symbol": {s: round(v, 2) for s, v in diff.items()},
    }


def sharpe_of(equity: pd.Series) -> float:
    r = equity.pct_change().dropna()
    return float(np.sqrt(252) * r.mean() / r.std())


def leave_one_out(symbols, keys, workers, spec_variant=LEAD_VARIANT, cost=REPORT_COST):
    """Full-period Sharpe difference from the baseline with each symbol left out (point only)."""
    result = {}
    for sym in symbols:
        _WORLD["drop"] = sym
        jobs = [(spec_variant, k, cost, None, None, False) for k in (BASELINE, *keys)]
        got = {r[0][1]: r[1] for r in run_jobs(jobs, workers)}
        result[sym] = {k: round(sharpe_of(got[k]) - sharpe_of(got[BASELINE]), 4) for k in keys}
    _WORLD["drop"] = None
    return result


def methodology() -> dict:
    tm = tvb.methodology()
    return {
        "question": (
            "Do partial exits (part of each position sold at a profit target, the rest on the live "
            "trailing stop) improve on the live exit, and does it matter whether they are built as "
            "design 1 (shrink the stop), 2 (software targets) or 3 (per-tranche orders)?"
        ),
        "baseline": "trailing, evening raise only (what the routines do since #649); the bracket and the old trailing_target are references",
        "plan": {
            "source": "PartialExitManager defaults and from_mode_manager; trading_modes.yaml partial_exits",
            "targets": 2,
            "split": [0.5, 0.5],
            "first_target": "limit sell at entry x (1 + target_1_pct)",
            "second_target": "trailing: the rest keeps the trailing stop (no fixed price)",
            "target_1_pct_run": {
                "T4": "0.04: PartialExitManager default and the value from_mode_manager hard-codes for every mode (what would be registered)",
                "T5": "0.05: trading_modes.yaml, moderate (the live mode); from_mode_manager does not read it",
            },
            "whole_shares": "first target int(n * 0.5) of n shares, the rest to the second; one share: no plan",
            "entry_reference": "the fill (the open), before costs, as the stop and the bracket target use",
        },
        "designs": {
            "D1h": "limit sell for the target shares, no stop on them; trailing stop on the others; limit fills on an intraday touch at the limit (open on a gap); if the stop shares are stopped out the target shares keep resting (until target, SELL signal or end of data), no stop",
            "D1x": "as D1h, but the target shares are sold at the next open once the stop shares are gone (a routine finds a position with no stop)",
            "D2": "every share has the trailing stop; the target is checked at the open (09:20 routine, open as proxy) and at the close (15:50 routine, close as proxy): at or past the target, the target shares sell at that price. No intraday fill. No open check on the entry day (bought at the open)",
            "D3": "every share has the trailing stop (same levels); the target shares also a limit sell, one cancelling the other; limit fills on an intraday touch; a bar touching both counts as the stop (all shares, as live_rules_sim does for the bracket)",
        },
        "same_bar": "stop first (conservative), as live_rules_sim._bracket_exit; counted in each run; the optimistic reading is a sensitivity (D3)",
        "fills": "stop touch at the stop price, gap at the open (shared with #539); limit touch at the limit price, gap above at the open; D2 at the open or close price; flat bps per side on every fill",
        "slippage_sensitivity": f"{SLIP_BPS:g} bps below the fill on gap-through-stop fills only, and on every stop fill; limit fills need the high {THROUGH_BPS:g} bps through the target",
        "variants": {n: {**v, "label": VARIANT_LABELS[n]} for n, v in VARIANTS.items()},
        "costs_bps_per_side": COSTS_BPS,
        "periods": tm["periods"],
        "sharpe": tm["sharpe"],
        "paired_sharpe": tm["paired_sharpe"],
        "paired_return_test": tm["paired_return_test"],
        "family": "the 8 planned configurations against the baseline trailing exit, x 3 variants x 3 costs x 3 periods = 216 Newey-West tests (audit family). Design-against-design, against-bracket and sensitivity comparisons are reported, not in the family",
        "trade_counting": "a position is one trade (its legs combined, quantity-weighted); legs are the fills. Win rate, trades and mean return per trade are by position, comparable with the baseline's",
        "turnover": "(shares bought + shares sold) / 2 in dollars, per year, as a multiple of mean equity",
        "not_modelled": tm["not_modelled"]
        + [
            "queue priority: a limit fills when the price touches it (sensitivity: needs 10 bps through)",
            "partial fills, the 90-day GTC lapse of the limit orders, and Alpaca's rejection of a limit sell while a stop holds the shares (design 1 and 3 are what the designs would need to avoid it)",
            "design 1's live behaviour for unprotected shares: the reconciler would try to re-place a stop and be refused while the limit holds the shares",
            "the 09:20 and 15:50 routines run up to 5 minutes late; the open and close stand in for their prices",
        ],
        "caveats": tm["caveats"][:2]
        + [
            "the strategies share signals, not trades: a partial exit frees the position cap and cash earlier or later, so entries differ",
            "no-edge entry signal (#574): this measures exits on a signal without demonstrated edge",
        ],
    }


def to_native(obj):
    return tvb.to_native(obj)


def fmt_s(s):
    return tvb.fmt_sharpe(s)


def diff_cell(t):
    low, high = t["sharpe_diff_ci95"]
    return f"{t['sharpe_diff']:+.3f} [{low:+.3f}, {high:+.3f}]"


def build_rows(variant, equities, comparisons):
    return tvb.paired_rows(variant, equities, comparisons)


def render_markdown(out):  # noqa: C901
    sc, tests = out["scenarios"], out["paired_tests"]
    extra = out["paired_extra"]
    lines = [
        "# Partial Exits against the Live Trailing Exit (#584)",
        "",
        f"Generated by `scripts/research/partial_exits.py` on {out['run_timestamp'][:10]}. "
        "Numbers are in `partial_exits.json`.",
        "",
        "Plan (fixed before the run): two targets, 50/50; the first a limit sell at +4% (T4: the "
        "`PartialExitManager` default and what `from_mode_manager` registers) or +5% (T5: "
        "`trading_modes.yaml`, moderate), the rest keeps the trailing stop. Baseline: the live "
        "trailing exit with the evening raise only. D1h / D1x: shrink the stop (unprotected shares "
        "rest / are sold at the next open); D2: software targets checked at the open and the close; "
        "D3: per-tranche stop + limit.",
        "",
        "## Headline: 5 bps a side, full period",
        "",
    ]
    for variant in VARIANTS:
        if variant not in sc:
            continue
        lines += [f"### {VARIANT_LABELS[variant]}", ""]
        lines += [
            "| Configuration | Sharpe [95% CI] | Sharpe diff vs baseline [95% CI] | CAGR | Max DD | Positions | Win rate | Mean return per position | Turnover (x equity / yr) | Target reached | Alpha vs EW, % a year (t) |",
            "| ------------- | --------------- | -------------------------------- | ---- | ------ | --------- | -------- | ------------------------ | ------------------------ | -------------- | ------------------------- |",
        ]
        for key in ALL_KEYS:
            s = sc[variant][key][f"{int(REPORT_COST)}bps"]
            t = s["trades_full"]
            leg = s["legs"]
            row = next(
                (
                    r
                    for r in tests + extra
                    if r["comparison"] == f"{key} - {BASELINE}"
                    and r["variant"] == variant
                    and r["period"] == "full"
                    and r["cost_bps"] == REPORT_COST
                ),
                None,
            )
            cell = diff_cell(row) if row else "(baseline)" if key == BASELINE else ""
            reach = f"{leg['target_reached_pct']:.1f}%" if "target_reached_pct" in leg else ""
            lines.append(
                f"| {LABELS[key]} | {fmt_s(s['full'])} | {cell} | {s['full']['cagr_pct']:.2f}% | "
                f"{s['full']['max_drawdown_pct']:.1f}% | {t['n_trades']:,} | {t['win_rate_pct']:.1f}% | "
                f"{t['mean_return_pct']:.3f}% | {leg['turnover_per_year_x_equity']:.2f} | {reach} | "
                f"{tvb.fmt_alpha(s['alpha_vs_equal_weight']['full'])} |"
            )
        lines.append("")
    bench = out["benchmarks"]
    lines += [
        "### Buy-and-hold of the same symbols",
        "",
        "| Benchmark | Period | Sharpe [95% CI] | CAGR | Max DD |",
        "| --------- | ------ | --------------- | ---- | ------ |",
    ]
    for name, periods in bench.items():
        for period in PERIODS:
            s = periods[period]
            lines.append(
                f"| {name} | {PERIOD_LABELS[period]} | {fmt_s(s)} | {s['cagr_pct']:.1f}% | "
                f"{s['max_drawdown_pct']:.1f}% |"
            )
    lines.append("")
    for variant in VARIANTS:
        if variant not in sc:
            continue
        lines += [
            f"## Paired Sharpe differences against the baseline: {VARIANT_LABELS[variant]}",
            "",
        ]
        lines += tvb.PAIRED_HEADER + tvb.paired_table(tests, variant) + [""]
        lines += [
            f"## Design against design and against the bracket (reference): {VARIANT_LABELS[variant]}",
            "",
        ]
        lines += tvb.PAIRED_HEADER + tvb.paired_table(extra, variant) + [""]
    for variant in VARIANTS:
        if variant not in sc:
            continue
        lines += [f"## Exits at 5 bps, all periods (fills): {VARIANT_LABELS[variant]}", ""]
        reasons = sorted(
            {r for k in ALL_KEYS for r in sc[variant][k]["5bps"]["legs"]["leg_reasons"]}
        )
        lines += [
            "| Configuration | "
            + " | ".join(reasons)
            + " | Legs | Same bar (stop and target) | Unprotected target shares | Touched, not seen |",
            "| ------------- | "
            + " | ".join("---" for _ in reasons)
            + " | ---- | --- | --- | --- |",
        ]
        for key in ALL_KEYS:
            leg = sc[variant][key]["5bps"]["legs"]
            lines.append(
                f"| {LABELS[key]} | "
                + " | ".join(f"{leg['leg_reasons'].get(r, 0):,}" for r in reasons)
                + f" | {leg['n_legs']:,} | {leg.get('same_bar_stop_and_target', '')} | "
                f"{leg.get('orphan_target_shares', '')} | {leg.get('target_touched_not_seen', '')} |"
            )
        lines.append("")
    lines += [
        "## Performance by configuration, all costs and periods",
        "",
    ]
    for variant in VARIANTS:
        if variant not in sc:
            continue
        lines += [f"### {VARIANT_LABELS[variant]}", ""]
        lines += tvb.SCENARIO_HEADER
        for key in ALL_KEYS:
            for period in PERIODS:
                for cost in COSTS_BPS:
                    s = sc[variant][key][f"{int(cost)}bps"]
                    stats, trades = s[period], s[tvb.TRADE_KEYS[period]]
                    lines.append(
                        f"| {LABELS[key]} | {PERIOD_LABELS[period]} | {cost:g} | {fmt_s(stats)} | "
                        f"{stats['cagr_pct']:.2f}% | {stats['max_drawdown_pct']:.1f}% | "
                        f"{trades.get('n_trades', 0):,} | {trades.get('win_rate_pct', float('nan')):.1f}% | "
                        f"{trades.get('mean_hold_days', float('nan')):.1f} | "
                        f"{tvb.fmt_alpha(s['alpha_vs_equal_weight'][period])} |"
                    )
        lines.append("")
    sens = out.get("sensitivities", {})
    if sens:
        lines += [
            "## Fill and ordering sensitivities",
            "",
            f"{VARIANT_LABELS[LEAD_VARIANT]}, 5 bps, full period. 'Diff' is the configuration "
            "minus the baseline run under the same assumption (the slippage runs slip the "
            "baseline's stop fills too). 'Change' is that difference minus the main run's.",
            "",
            "| Sensitivity | Configuration | Sharpe | Diff vs baseline [95% CI] | Main diff | Change |",
            "| ----------- | ------------- | ------ | ------------------------- | --------- | ------ |",
        ]
        main = {
            r["comparison"].split(" - ")[0]: r
            for r in tests
            if r["variant"] == LEAD_VARIANT
            and r["period"] == "full"
            and r["cost_bps"] == REPORT_COST
        }
        for name, block in sens.items():
            for key, row in block["rows"].items():
                d = row["paired_vs_baseline"]
                lines.append(
                    f"| {name} | {key} | {row['sharpe']:.3f} | {diff_cell(d) if d else ''} | "
                    f"{main[key]['sharpe_diff']:+.3f} | {d['sharpe_diff'] - main[key]['sharpe_diff']:+.3f} |"
                    if d
                    else f"| {name} | {key} | {row['sharpe']:.3f} | (baseline) | | |"
                )
        lines.append("")
    ps = out.get("per_symbol")
    if ps:
        lines += [
            "## By symbol (SELL exits off, 5 bps, full period)",
            "",
            "P&L in dollars on the $100,000 account (open positions marked), difference from the "
            "baseline for each headline configuration; the last columns are the full-period Sharpe "
            "difference from the baseline with the symbol left out of the universe (point values).",
            "",
        ]
        heads = ps["headline_keys"]
        lines += [
            "| Symbol | Baseline P&L | Positions | "
            + " | ".join(f"{k} diff" for k in heads)
            + " | "
            + " | ".join(f"{k} Sharpe diff w/o symbol" for k in heads)
            + " |",
            "| ------ | ------------ | --------- | "
            + " | ".join("---" for _ in heads)
            + " | "
            + " | ".join("---" for _ in heads)
            + " |",
        ]
        for sym in sorted(ps["pnl"][BASELINE]):
            base = ps["pnl"][BASELINE][sym]
            lines.append(
                f"| {sym} | {base['pnl']:,.0f} | {base['positions']} | "
                + " | ".join(
                    f"{ps['concentration'][k]['diff_by_symbol'].get(sym, 0):+,.0f}" for k in heads
                )
                + " | "
                + " | ".join(f"{ps['leave_one_out'][sym][k]:+.3f}" for k in heads)
                + " |"
            )
        lines += [
            "",
            "| Configuration | Total P&L diff | Symbols better / worse | Top 3 share of gross abs diff | Top 3 |",
            "| --- | --- | --- | --- | --- |",
        ]
        for k in heads:
            c = ps["concentration"][k]
            lines.append(
                f"| {k} | {c['total_pnl_diff']:+,.0f} | {c['n_symbols_better']} / {c['n_symbols_worse']} | "
                f"{c['top3_share_of_gross_abs_diff_pct']:.1f}% | {c['top3']} |"
            )
        lo = ps["leave_one_out_range"]
        lines += [
            "",
            "Leave-one-out Sharpe difference range (min, max over the 19 symbols): "
            + "; ".join(f"{k} {v[0]:+.3f} to {v[1]:+.3f}" for k, v in lo.items()),
            "",
        ]
    return "\n".join(lines) + "\n"


def main():  # noqa: C901
    parser = argparse.ArgumentParser(description="Partial exits vs the live trailing exit (#584)")
    parser.add_argument("--output", default=OUTPUT_STEM)
    parser.add_argument("--workers", type=int, default=24)
    parser.add_argument("--variants", nargs="+", default=list(VARIANTS), choices=list(VARIANTS))
    parser.add_argument("--smoke", action="store_true", help="5 symbols, 2018-2021 (code check)")
    args = parser.parse_args()
    logging.disable(logging.WARNING)
    os.chdir(REPO_ROOT)

    wanted = baseline.watchlist_symbols()
    if args.smoke:
        wanted = wanted[:5]
    prices, raw_closes = baseline.load_prices(wanted)
    missing = sorted(set(wanted) - set(prices))
    print(f"  Universe: {len(prices)} of {len(wanted)} (missing {missing})", flush=True)
    start, end = (
        ("2018-01-02", "2021-12-31") if args.smoke else (baseline.TEST_START, baseline.TEST_END)
    )
    signals = compute_signals(
        {s: p["close"] for s, p in prices.items()},
        raw_closes,
        baseline.live_signal_fn(),
        start,
        end,
    )
    print(f"  Signals: {len(signals)} {signals['action'].value_counts().to_dict()}", flush=True)
    calendar = pd.DatetimeIndex(sorted(signals.index.get_level_values("date").unique()))
    bench_equity = baseline.benchmarks(prices, calendar)
    _WORLD.update(prices=prices, raw_closes=raw_closes, signals=signals, drop=None)

    jobs = [
        (variant, key, cost, None, None, True)
        for variant in args.variants
        for key in ALL_KEYS
        for cost in COSTS_BPS
    ]
    print(f"  Main grid: {len(jobs)} simulations on {args.workers} workers", flush=True)
    results = run_jobs(jobs, args.workers)
    equities, scenarios, trades_main = {}, {}, {}
    for (variant, key, cost, _, _), equity, trades, attrs in results:
        trades.attrs.update(attrs)
        equities[(variant, key, cost)] = equity
        scenarios.setdefault(variant, {}).setdefault(key, {})[f"{int(cost)}bps"] = summary_of(
            equity, trades, bench_equity, prices
        )
        if cost == REPORT_COST:
            trades_main[(variant, key)] = trades
    for variant in args.variants:
        for key in ALL_KEYS:
            s = scenarios[variant][key]["5bps"]
            print(
                f"  {variant:24} {key:16} Sharpe {s['full']['sharpe']:+.3f} CAGR {s['full']['cagr_pct']:.2f}% "
                f"pos {s['trades_full']['n_trades']} reach {s['legs'].get('target_reached_pct', '')}",
                flush=True,
            )

    paired, extra = [], []
    for variant in args.variants:
        paired += build_rows(variant, equities, FAMILY)
        extra += build_rows(variant, equities, EXTRA)

    output = {
        "run_timestamp": datetime.datetime.now().isoformat(),
        "issue": "#584",
        "description": "Partial exits against the live trailing exit, three designs",
        "methodology": methodology(),
        "universe": sorted(prices),
        "missing_symbols": missing,
        "signal_counts": {k: int(v) for k, v in signals["action"].value_counts().items()},
        "scenarios": scenarios,
        "paired_tests": paired,
        "paired_extra": extra,
        "benchmarks": {
            name: {p: baseline.period_stats(eq, *span) for p, span in PERIODS.items()}
            for name, eq in bench_equity.items()
        },
    }

    if LEAD_VARIANT in args.variants:
        # Sensitivities
        sens_jobs, sens_meta = [], {}
        for name, (rules_extra, plan_extra, keys) in SENSITIVITIES.items():
            run_keys = list(keys) if BASELINE in keys else [BASELINE, *keys]
            if name == "same_bar_target_first" or name == "limit_through_10bps":
                run_keys = [k for k in run_keys if k != BASELINE]
            for key in run_keys:
                sens_jobs.append(
                    (LEAD_VARIANT, key, REPORT_COST, rules_extra or None, plan_extra or None, False)
                )
            sens_meta[name] = run_keys
        sens_results = run_jobs(sens_jobs, args.workers)
        # group
        by = {}
        for (variant, key, cost, rules_extra, plan_extra), equity, trades, attrs in sens_results:
            by[
                (
                    key,
                    json.dumps(rules_extra, sort_keys=True),
                    json.dumps(plan_extra, sort_keys=True),
                )
            ] = (
                equity,
                attrs,
            )
        sens_out = {}
        for name, (rules_extra, plan_extra, keys) in SENSITIVITIES.items():
            re_, pe_ = (
                json.dumps(rules_extra or None, sort_keys=True),
                json.dumps(plan_extra or None, sort_keys=True),
            )
            rows = {}
            base_key = (BASELINE, re_, json.dumps(None))
            base_eq = (
                by[base_key][0]
                if base_key in by
                else equities[(LEAD_VARIANT, BASELINE, REPORT_COST)]
            )
            for key in sens_meta[name]:
                eq, attrs = by[(key, re_, pe_)]
                if key == BASELINE:
                    rows[key] = {"sharpe": round(sharpe_of(eq), 3), "paired_vs_baseline": None}
                    continue
                ra = tvb.sliced_returns(eq)
                rb = tvb.sliced_returns(base_eq)
                boot = paired_sharpe_diff_ci(ra, rb)
                rows[key] = {
                    "sharpe": round(sharpe_of(eq), 3),
                    "paired_vs_baseline": {
                        "sharpe_diff": round(boot["diff"], 4),
                        "sharpe_diff_ci95": [round(x, 4) for x in boot["ci95"]],
                        "sharpe_diff_bootstrap_p": round(boot["p_value"], 4),
                    },
                    "same_bar_stop_and_target": attrs.get("partial", {}).get(
                        "same_bar_stop_and_target"
                    ),
                }
            sens_out[name] = {
                "rules": rules_extra,
                "plan": plan_extra,
                "rows": rows,
                "note": "baseline for the paired difference: the baseline run under the same slippage"
                if BASELINE in sens_meta[name]
                else "baseline for the paired difference: the main baseline run",
            }
        output["sensitivities"] = sens_out

        # per symbol and leave-one-out, headline configurations
        heads = ["D1h_T4", "D2_T4", "D3_T4"]
        trades_for = {k: trades_main[(LEAD_VARIANT, k)] for k in (BASELINE, *PLANNED)}
        pnl = per_symbol(trades_for)
        conc = {k: concentration(pnl, k) for k in PLANNED}
        loo = leave_one_out(sorted(prices), heads, args.workers)
        lo_range = {
            k: [min(v[k] for v in loo.values()), max(v[k] for v in loo.values())] for k in heads
        }
        output["per_symbol"] = {
            "variant": LEAD_VARIANT,
            "cost_bps": REPORT_COST,
            "headline_keys": heads,
            "pnl": pnl,
            "concentration": conc,
            "leave_one_out": loo,
            "leave_one_out_range": lo_range,
        }

    output = to_native(output)
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    json_path = RESULTS_DIR / f"{args.output}.json"
    with open(json_path, "w") as f:
        json.dump(output, f, indent=2)
        f.write("\n")
    with open(RESULTS_DIR / f"{args.output}.md", "w") as f:
        f.write(render_markdown(output))
    print(f"  Results saved to {json_path}", flush=True)


if __name__ == "__main__":
    main()
