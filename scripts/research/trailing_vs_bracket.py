"""
Trailing-stop exits against the fixed bracket, on the live rules (#539).

The #574 stage A baseline (live_rules_baseline.py) simulated the live MACD+RSI rules with a
fixed -5% / +8% bracket. The live positions exit by stop only: the legacy ones by trailing stop
(their take-profit legs lapsed at Alpaca's 90-day GTC limit), new CLI entries by trailing stop
plus the 8% leg until day 90. So this runs the same signals, sizing, limits and timing under three
exit modes and compares them on the same days:

- bracket: stop -5% / target +8% from the fill, fixed (the baseline)
- trailing: the -5% stop raised by the live trailing tiers, no target (legacy positions, whose
  take-profit leg lapsed)
- trailing_target: trailing tiers plus the +8% target (new CLI entries, which still go out as
  brackets with an 8% take-profit leg until day 90)

The raise is the live code (StateReconciler._trailing_level on the TrailingStopManager the
routines build): one raise a day from the close, in force from the next session. See
src/backtesting/live_rules_sim.py. The signals are computed once and shared by every run.

The morning routine (09:20 ET) raises too, from a pre-market print that daily bars do not have, so
the three modes above leave it out. A sensitivity adds it with the day's open standing in for that
price (LiveRules.morning_raise) to the two trailing modes, and reports it beside the main results:
the Sharpe and the paired differences against the bracket with it, and its own effect (with the
morning raise minus without). Those comparisons are a sensitivity of the same hypotheses, not new
tests, so they stay out of `paired_tests` and the multiple-testing audit.

Each mode is run at 0 / 5 / 10 bps a side, for moderate and conservative sizing, and for the
moderate size with SELL-signal exits switched off (positions then leave only by their stops
and targets). That variant is the one that matches the live exits: nothing in the live system
sells on a signal, SELL-signal exits are a #574 baseline convention. The Reading leads with it.

Paired comparisons, trailing - bracket and trailing_target - bracket, use the daily returns of
the two equity curves: the Sharpe difference with a paired circular block bootstrap (20-day
blocks, 2000 replications), and a Newey-West test of the mean daily return difference (the
test the multiple-testing audit collects).

Data and periods are the baseline's: split-adjusted equity_prices_daily (read-only), decisions
from 2009-01-02 to 2026-10-09, in-sample 2009-2019, out-of-sample 2020 onward.

Usage:
    ~/miniconda3/envs/OptionsCommander/bin/python scripts/research/trailing_vs_bracket.py
"""

import argparse
import atexit
import dataclasses
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
sys.path.insert(0, str(Path(__file__).resolve().parent))  # live_rules_baseline sits beside this

# Research never touches the user's state or cache: the voter's ranking manager opens
# state/user.db, and the trading package builds its market-data cache on first use. The
# scratch directories made here are removed at exit.
_SCRATCH_DIRS = []
for _var, _prefix in (
    ("AUTOTRADER_STATE_DIR", "trailing_state_"),
    ("AUTOTRADER_CACHE_DIR", "trailing_cache_"),
):
    if _var not in os.environ:
        os.environ[_var] = tempfile.mkdtemp(prefix=_prefix)
        _SCRATCH_DIRS.append(os.environ[_var])


@atexit.register
def _remove_scratch_dirs() -> None:
    for path in _SCRATCH_DIRS:
        shutil.rmtree(path, ignore_errors=True)


import live_rules_baseline as baseline  # noqa: E402

from src.backtesting.live_rules_sim import (  # noqa: E402
    EXIT_MODES,
    LiveRules,
    LiveTrailingStop,
    compute_signals,
    paired_sharpe_diff_ci,
    performance,
    simulate,
)
from src.backtesting.research_cli import paired_return_test  # noqa: E402

RESULTS_DIR = REPO_ROOT / "docs/08_research/03_strategy_research/results"
OUTPUT_STEM = "trailing_vs_bracket"
COSTS_BPS = baseline.COSTS_BPS
PERIODS = {
    "full": (None, None),
    "in_sample_2009_2019": (None, "2019-12-31"),
    "out_of_sample_2020_on": (baseline.SPLIT_DATE, None),
}
PERIOD_LABELS = {
    "full": "2009 to 2026-10-09",
    "in_sample_2009_2019": "2009-2019",
    "out_of_sample_2020_on": "2020 on",
}
TRADE_KEYS = {
    "full": "trades_full",
    "in_sample_2009_2019": "trades_in_sample",
    "out_of_sample_2020_on": "trades_out_of_sample",
}
# (exit mode compared, against)
COMPARISONS = [("trailing", "bracket"), ("trailing_target", "bracket")]
VARIANTS = {
    "moderate": {"position_pct": baseline.MODES["moderate"], "signal_exit": True},
    "conservative": {"position_pct": baseline.MODES["conservative"], "signal_exit": True},
    "moderate_no_signal_exit": {"position_pct": baseline.MODES["moderate"], "signal_exit": False},
}
VARIANT_LABELS = {
    "moderate": "Moderate sizing (2% of equity), SELL-signal exits kept",
    "conservative": "Conservative sizing (1% of equity), SELL-signal exits kept",
    "moderate_no_signal_exit": "Moderate sizing, SELL-signal exits off (stops and targets only; matches live exits)",
}
MODE_LABELS = {
    "bracket": "bracket (fixed -5% / +8%)",
    "trailing": "trailing (legacy positions, TP leg lapsed)",
    "trailing_target": "trailing + 8% target (new entries, TP leg until day 90)",
    "trailing+morning": "trailing + morning raise at the open",
    "trailing_target+morning": "trailing + 8% target + morning raise at the open",
}
# The sensitivity: the trailing modes again with the morning routine's raise, the open standing in
# for its 09:20 pre-market price. Keys carry a "+morning" suffix.
MORNING_MODES = ("trailing", "trailing_target")
MORNING_COMPARISONS = [(f"{mode}+morning", "bracket") for mode in MORNING_MODES]
MORNING_EFFECTS = [(f"{mode}+morning", mode) for mode in MORNING_MODES]
MORNING_COST = 5.0  # the cost the markdown shows the sensitivity at (the json has every cost)


def sliced_returns(equity: pd.Series, start=None, end=None) -> pd.Series:
    """Daily returns of an equity curve cut to a period before differencing, as period_stats does."""
    e = equity
    if start is not None:
        e = e[e.index >= pd.Timestamp(start)]
    if end is not None:
        e = e[e.index <= pd.Timestamp(end)]
    return e.pct_change().dropna()


def sliced_equity(equity: pd.Series, start=None, end=None) -> pd.Series:
    e = equity
    if start is not None:
        e = e[e.index >= pd.Timestamp(start)]
    if end is not None:
        e = e[e.index <= pd.Timestamp(end)]
    return e


def paired_rows(variant: str, equities: dict, comparisons=COMPARISONS) -> list:
    """Paired comparisons of each (mode, against) pair of curves, by cost and period."""
    rows = []
    for cost in COSTS_BPS:
        for mode, base in comparisons:
            for period, span in PERIODS.items():
                ra = sliced_returns(equities[(variant, mode, cost)], *span)
                rb = sliced_returns(equities[(variant, base, cost)], *span)
                boot = paired_sharpe_diff_ci(ra, rb)
                perf_a = performance(sliced_equity(equities[(variant, mode, cost)], *span))
                perf_b = performance(sliced_equity(equities[(variant, base, cost)], *span))
                rows.append(
                    {
                        "comparison": f"{mode} - {base}",
                        "variant": variant,
                        "period": period,
                        "cost_bps": cost,
                        "sharpe_diff": round(boot["diff"], 4),
                        "sharpe_diff_ci95": [round(x, 4) for x in boot["ci95"]],
                        "sharpe_diff_bootstrap_p": round(boot["p_value"], 4),
                        "cagr_diff_pp": round(perf_a["cagr_pct"] - perf_b["cagr_pct"], 4),
                        "max_drawdown_diff_pp": round(
                            perf_a["max_drawdown_pct"] - perf_b["max_drawdown_pct"], 4
                        ),
                        "return_test": paired_return_test(rb, ra),
                        "n_days": boot["n_days"],
                    }
                )
    return rows


def methodology() -> dict:
    tiers = dataclasses.asdict(LiveTrailingStop().manager.trailing_config)
    keep = (
        "enabled",
        "progressive_enabled",
        "progressive_breakeven_pct",
        "progressive_lock_25_pct",
        "progressive_trail_50_pct",
        "climb_rate",
        "never_move_stop_down",
        "volatility_aware",
    )
    return {
        "question": (
            "Do the live trailing-tier exits do better, worse, or no differently than the fixed "
            "-5% / +8% bracket of the #574 baseline, for the same entry signals at live timing?"
        ),
        "signal": baseline_methodology()["signal"],
        "timing": baseline_methodology()["timing"],
        "entries": baseline_methodology()["entries"],
        "exit_modes": {
            "bracket": "stop -5% / target +8% from the fill, fixed (the #574 baseline)",
            "trailing": (
                "initial stop -5% from the fill, raised by the live trailing tiers, no target "
                "(legacy positions, whose take-profit legs lapsed at the 90-day GTC limit)"
            ),
            "trailing_target": (
                "the same tiers plus the +8% target (new CLI entries: they still go out as "
                "brackets with an 8% take-profit leg, which lives until day 90, longer than the "
                "simulated holds)"
            ),
        },
        "signal_exit": "a SELL signal closes the position at the open, in every mode unless the variant switches it off",
        "trailing_raise": (
            "StateReconciler._trailing_level on a TrailingStopManager built as "
            "CostEfficientTradeCycle builds it (no broker, no ATR fetcher, stock TradingConfig); "
            "one raise a day from the close (the evening routine runs ten minutes before it), in "
            "force from the next session; MIN_STOP_GAP floor and the $0.01 rule are the "
            "reconciler's; run at the day's raw price level so the cent rounds as at the broker"
        ),
        "morning_raise": (
            "the morning routine (09:20 ET) raises stops from the broker's price then, a pre-market "
            "print that daily bars do not have, so the main results leave that raise out. The "
            "sensitivity (LiveRules.morning_raise) runs the two trailing modes again with each held "
            "position's stop also raised from the day's open, before the day's range is checked "
            "(positions bought at that open are not held yet), and the evening raise as before. "
            "The open is the fully priced gap: a thin pre-market leaves the 09:20 price nearer the "
            "previous close, where the raise adds little. The bracket never raises, so each "
            "sensitivity run is compared with the same bracket and with its own evening-only run; "
            "these comparisons are reported under morning_raise_sensitivity and are not in "
            "paired_tests or the multiple-testing audit"
        ),
        "trailing_config": {k: tiers[k] for k in keep},
        "inert_on_the_live_path": [
            "ATR (volatility_aware is off and the routines pass no atr_fetcher)",
            "voter tightening (update_voter_signal is never called by the routines)",
            "support/resistance levels (set_support_resistance is never called)",
        ],
        "stop_fills": "a session whose low reaches the stop fills at min(open, stop); both stop and target touched counts as the stop. Costs are a flat bps per side and an in-bar stop touch fills exactly at the stop price: any extra stop-market slippage falls harder on the trailing modes, which make many more near-the-price stop exits than the bracket, and the 0/5/10 bps grid cannot show that",
        "signal_exit_convention": "SELL-signal exits are a #574 baseline convention that the live system lacks (nothing in live sells on a signal); the moderate_no_signal_exit variant matches live exits and leads the Reading",
        "initial_capital": LiveRules().initial_capital,
        "variants": {
            name: {**spec, "label": VARIANT_LABELS[name]} for name, spec in VARIANTS.items()
        },
        "costs_bps_per_side": COSTS_BPS,
        "data": "equity_prices_daily, split-adjusted (gex.corporate_events), dividends excluded, read-only",
        "periods": {
            "warmup_from": baseline.WARMUP_START,
            "test": f"{baseline.TEST_START} to {baseline.TEST_END}",
            "out_of_sample_from": baseline.SPLIT_DATE,
        },
        "sharpe": "annualized, rf 0; 95% circular block bootstrap (20-day blocks, 2000 reps)",
        "paired_sharpe": (
            "Sharpe(a) - Sharpe(b) on the daily returns of the two equity curves; the same "
            "20-day blocks are drawn for both in each of 2000 replications (seed 0); p is twice "
            "the smaller share of replications on one side of zero"
        ),
        "paired_return_test": "Newey-West test of the mean daily return difference (research_cli.paired_return_test); this is the test the multiple-testing audit collects",
        "alpha": "daily OLS on equal-weight universe returns, annualized intercept, Newey-West (5 lags) t; cash earns 0",
        "mean_hold_days": "calendar days, closed trades by entry date",
        "not_modelled": [
            "deferral of raises on non-replaceable stop orders (live skips them until a later run; the sim lands every raise at once)",
            "stale local entry_price across a split (a known live quirk: live tiers on a position held through a split use the pre-split cost; the sim uses the split-adjusted entry; a separate live-code issue could refresh entry_price from the broker)",
            "the evening raise uses the 16:00 close where the live routine at 15:50 sees the 15:50 price: a ten-minute information advantage, Sharpe-level timing noise of about +/-0.04 that is not in the bootstrap interval",
            "stop-market slippage beyond the flat cost: it hurts the trailing modes more than the bracket (see stop_fills)",
            "the morning routine's raise from the pre-market price (09:20 ET): daily bars have no pre-market price, and on gap days it differs from the price of the evening raise; see morning_raise_sensitivity, which stands the day's open in for it",
            "intraday timing of the raise (the stop is in force from the next session, not for the last ten minutes of the day)",
            "intraday decisions (the CLI can also trade on a partial day)",
            "IEX vs consolidated bars",
            "GTC renewal after 80 days (assumed seamless, as #587 does it)",
            "the 90-day lapse of the take-profit leg in trailing_target (the target is kept as in the bracket)",
            "GTT sells (skipped while a broker stop holds the shares)",
            "dividends",
        ],
        "caveats": [
            "universe picked with hindsight; benchmark is buy-and-hold of the same symbols",
            "MACD threshold is in dollars, so signal frequency depends on price level",
            "exit modes change how long positions are held, so the 10-position cap and the exposure limit block different entries: the strategies share signals, not trades",
            "tiers and stops are measured from the open the position filled at, before costs",
            "GOOGL's 2-for-1 split of 2014-04-03 is missing from gex.corporate_events (only the 2022-07-18 20:1 split is listed), so the adjusted GOOGL series has a phantom -49.7% day there; shared with the #574 baseline, immaterial here (no GOOGL position was open across that date in any mode or variant), not patched; the gex repo could be told",
            "the flat cost grid understates stop-fill slippage for the trailing modes, so the intervals are wide and the point estimates lean negative for trailing under realistic stop slippage: the Reading is not 'no difference'",
            "the morning raise is left out of the main results and it runs against the trailing modes: the sensitivity (the open as the 09:20 price) lowers their Sharpe, so the main trailing - bracket differences are, if anything, too favourable to the trailing modes",
        ],
    }


def baseline_methodology() -> dict:
    return {
        "signal": "VotingSignal.evaluate_ranked_voting (MACD 13/34/8 hist beyond +/-0.1, RSI 14 at 30/70), 90-calendar-day window rescaled to the prior day's raw price level",
        "timing": "signal from bars through d-1; fills at day d open",
        "entries": "BUY with no position at the start of the day; whole shares (at the raw price) of position_pct x prior equity at the prior close; max 10 positions and 80% exposure counted before the open; cash",
    }


def fmt_sharpe(stats: dict) -> str:
    low, high = stats["sharpe_ci95"]
    return f"{stats['sharpe']:.3f} [{low:.3f}, {high:.3f}]"


def fmt_alpha(alpha: dict) -> str:
    return f"{alpha['alpha_ann_pct']:+.2f}% ({alpha['alpha_t']:+.2f})"


def scenario_rows(variant_results: dict, modes=EXIT_MODES, costs=None) -> list:
    """Long table rows: exit mode x period x cost (every cost unless costs is given)."""
    costs = COSTS_BPS if costs is None else costs
    lines = []
    for mode in modes:
        for period in PERIODS:
            for cost in costs:
                s = variant_results[mode][f"{int(cost)}bps"]
                stats, trades = s[period], s[TRADE_KEYS[period]]
                lines.append(
                    f"| {MODE_LABELS[mode]} | {PERIOD_LABELS[period]} | {cost:g} | "
                    f"{fmt_sharpe(stats)} | {stats['cagr_pct']:.2f}% | {stats['max_drawdown_pct']:.1f}% | "
                    f"{trades.get('n_trades', 0):,} | {trades.get('win_rate_pct', float('nan')):.1f}% | "
                    f"{trades.get('mean_hold_days', float('nan')):.1f} | "
                    f"{fmt_alpha(s['alpha_vs_equal_weight'][period])} |"
                )
    return lines


SCENARIO_HEADER = [
    "| Exit mode | Period | Cost (bps) | Sharpe [95% CI] | CAGR | Max DD | Trades | Win rate | Mean hold (days) | Alpha vs EW, % a year (t) |",
    "| --------- | ------ | ---------- | --------------- | ---- | ------ | ------ | -------- | ---------------- | ------------------------- |",
]
PAIRED_HEADER = [
    "| Comparison | Period | Cost (bps) | Sharpe difference [95% CI] | Bootstrap p | Mean return difference, % a year | NW t | NW p |",
    "| ---------- | ------ | ---------- | -------------------------- | ----------- | -------------------------------- | ---- | ---- |",
]


def paired_table(tests: list, variant: str, costs=None) -> list:
    lines = []
    for t in tests:
        if t["variant"] != variant or (costs is not None and t["cost_bps"] not in costs):
            continue
        low, high = t["sharpe_diff_ci95"]
        rt = t["return_test"] or {}
        diff_pct = rt.get("mean_daily_diff", float("nan")) * 252 * 100
        lines.append(
            f"| {t['comparison']} | {PERIOD_LABELS[t['period']]} | {t['cost_bps']:g} | "
            f"{t['sharpe_diff']:+.3f} [{low:+.3f}, {high:+.3f}] | {t['sharpe_diff_bootstrap_p']:.3f} | "
            f"{diff_pct:+.2f} | {rt.get('t_statistic', float('nan')):+.2f} | "
            f"{rt.get('p_value', float('nan')):.3f} |"
        )
    return lines


def interval_side(low: float, high: float) -> str:
    """Where a confidence interval sits against zero, in words."""
    return "includes zero" if low <= 0 <= high else ("above zero" if low > 0 else "below zero")


def reading(tests: list, variant: str) -> list:
    """One line per comparison at 5 bps, saying only whether the interval covers zero."""
    lines = []
    for t in tests:
        if t["variant"] != variant or t["cost_bps"] != 5.0:
            continue
        low, high = t["sharpe_diff_ci95"]
        lines.append(
            f"- {t['comparison']}, {PERIOD_LABELS[t['period']]}: {t['sharpe_diff']:+.3f} "
            f"[{low:+.3f}, {high:+.3f}], interval {interval_side(low, high)}"
        )
    return lines


def find_test(tests: list, comparison: str, variant: str, period: str = "full", cost=MORNING_COST):
    """The paired row of one comparison, variant, period and cost."""
    return next(
        t
        for t in tests
        if (t["comparison"], t["variant"], t["period"], t["cost_bps"])
        == (comparison, variant, period, cost)
    )


def morning_effects(sens: dict) -> list:
    """Every comparison of a morning-raise run with its own evening-only run."""
    return sens["effect_of_morning_raise"]


def morning_effect_range(sens: dict, cost=MORNING_COST) -> tuple:
    """Smallest and largest full-period Sharpe loss from the morning raise (positive numbers)."""
    losses = [
        -t["sharpe_diff"]
        for t in morning_effects(sens)
        if t["period"] == "full" and t["cost_bps"] == cost
    ]
    return min(losses), max(losses)


def morning_reading(output: dict, variant: str = "moderate") -> list:
    """The Reading's account of the morning-raise sensitivity (5 bps, one variant)."""
    sens = output.get("morning_raise_sensitivity")
    if not sens or variant not in sens["scenarios"]:
        return []
    cost_key = f"{int(MORNING_COST)}bps"
    moves = []
    for mode in MORNING_MODES:
        key = f"{mode}+morning"
        before = output["scenarios"][variant][mode][cost_key]["full"]["sharpe"]
        after = sens["scenarios"][variant][key][cost_key]["full"]["sharpe"]
        effect = find_test(morning_effects(sens), f"{key} - {mode}", variant)
        low, high = effect["sharpe_diff_ci95"]
        moves.append(
            f"{mode} from {before:.3f} to {after:.3f} ({effect['sharpe_diff']:+.3f} "
            f"[{low:+.3f}, {high:+.3f}] paired, interval {interval_side(low, high)})"
        )
    loss_low, loss_high = morning_effect_range(sens)
    effects = morning_effects(sens)
    lower = sum(t["sharpe_diff"] < 0 for t in effects)
    return [
        "Not in these lines is the morning routine's raise (09:20 ET). It prices off the broker's "
        "pre-market print, which on a gap day differs from the price the evening raise used, and "
        "daily bars have no pre-market price, so the tables leave it out. It is not redundant. "
        "With the day's open standing in for the 09:20 price (the gap fully priced; see the "
        "sensitivity below), the same comparisons at 5 bps become:",
        "",
        *reading(sens["paired_tests_vs_bracket"], variant),
        "",
        f"Over the full period the morning raise moves the Sharpe of {moves[0]} and of {moves[1]}. "
        f"Across the three runs it lowers the full-period Sharpe of the trailing modes by "
        f"{loss_low:.2f} to {loss_high:.2f} at 5 bps, and its effect is negative in {lower} of "
        f"{len(effects)} comparisons (variants, modes, costs and periods). The omitted effect "
        "runs against the trailing modes: the differences from the bracket in the main tables "
        "are, if anything, too favourable to them. No pre-market prices are available here, so "
        "its size in the live system is not measured: small where the 09:20 price is still near "
        "the previous close, about these figures where the gap is fully priced.",
    ]


def exit_reason_table(variant_results: dict, cost_key: str = "5bps", modes=EXIT_MODES) -> list:
    reasons = ["signal", "stop", "stop_gap", "trail", "trail_gap", "target", "target_gap"]
    lines = [
        "| Exit mode | " + " | ".join(reasons) + " | Closed trades |",
        "| --------- | " + " | ".join("---" for _ in reasons) + " | -------------- |",
    ]
    for mode in modes:
        t = variant_results[mode][cost_key]["trades_full"]
        counts = t["exit_reasons"]
        lines.append(
            f"| {MODE_LABELS[mode]} | "
            + " | ".join(f"{counts.get(r, 0):,}" for r in reasons)
            + f" | {t['n_trades']:,} |"
        )
    return lines


def morning_section(output: dict) -> list:
    """The morning-raise sensitivity: performance, exits and paired differences at 5 bps."""
    sens = output.get("morning_raise_sensitivity")
    if not sens:
        return []
    cost_key = f"{int(MORNING_COST)}bps"
    modes = [m for mode in MORNING_MODES for m in (mode, f"{mode}+morning")]
    loss_low, loss_high = morning_effect_range(sens)
    lines = [
        "## Morning-raise sensitivity",
        "",
        "The morning routine (09:20 ET) raises stops too, from the broker's price at that time. That "
        "is a pre-market print, not the previous close: on a gap day it differs from the price the "
        "evening raise used, so the morning raise is not redundant. Daily bars have no pre-market "
        "price, so the tables above leave it out. Here the day's open stands in for it. Each held "
        "position's stop is raised from the open before the day's range is checked (positions "
        "bought at that open are not held yet), and the evening raise runs as before.",
        "",
        "The open is the case in which the gap is fully priced by 09:20. A thin pre-market prices "
        "less of it and the raise does less, down to little where the 09:20 price is still near "
        "the previous close. There are no pre-market prices here to say which it is, so these "
        "figures show the size of the omitted effect if the gap is priced, not an estimate for "
        "the live system.",
        "",
        "The bracket never raises, so each run is compared with the same bracket as above and "
        "with its own evening-only run (`+morning` minus the mode without it). These comparisons "
        "re-test the hypotheses above under another assumption, not new ones: they are not in "
        "`paired_tests` or the multiple-testing audit. Tables are at "
        f"{MORNING_COST:g} bps; the json has every cost.",
        "",
        f"Full-period Sharpe loss from the morning raise at {MORNING_COST:g} bps: {loss_low:.2f} to "
        f"{loss_high:.2f} across the sizings and trailing modes.",
        "",
    ]
    for variant in VARIANTS:
        if variant not in sens["scenarios"]:
            continue
        merged = {**output["scenarios"][variant], **sens["scenarios"][variant]}
        compared = sens["paired_tests_vs_bracket"] + sens["effect_of_morning_raise"]
        lines += [
            f"### {VARIANT_LABELS[variant]}",
            "",
            f"#### Performance at {MORNING_COST:g} bps",
            "",
        ]
        lines += SCENARIO_HEADER + scenario_rows(merged, modes, [MORNING_COST]) + [""]
        lines += [f"#### Exits at {MORNING_COST:g} bps, all periods (closed trades)", ""]
        lines += exit_reason_table(merged, cost_key, modes) + [""]
        lines += [f"#### Paired differences at {MORNING_COST:g} bps", ""]
        lines += PAIRED_HEADER + paired_table(compared, variant, (MORNING_COST,)) + [""]
    return lines


def render_markdown(output: dict) -> str:
    results, tests = output["scenarios"], output["paired_tests"]
    lead = "moderate_no_signal_exit" if "moderate_no_signal_exit" in results else "moderate"
    lines = [
        "# Trailing Exits against the Fixed Bracket (#539)",
        "",
        f"Generated by `scripts/research/trailing_vs_bracket.py` on {output['run_timestamp'][:10]}. "
        "Numbers are in `trailing_vs_bracket.json`.",
        "",
        "The #574 baseline simulated the live rules with a fixed -5% / +8% bracket. The live positions exit "
        "by stop only: legacy ones by trailing stop (their take-profit legs lapsed), new CLI entries by "
        "trailing stop plus an 8% leg until day 90. This runs the same signals, sizing, limits and timing "
        "under three exit modes and compares them on the same days.",
        "",
        "| Exit mode | Protective orders | Stands for |",
        "| --------- | ----------------- | ---------- |",
        "| bracket | stop -5% and target +8% from the fill, fixed | the #574 baseline |",
        "| trailing | -5% stop raised by the live trailing tiers, no target | legacy positions (take-profit leg lapsed) |",
        "| trailing_target | the same tiers plus the +8% target | new CLI entries (bracket take-profit leg until day 90) |",
        "",
        "The tiers are the live ones (climb rate medium): under 2% profit no change, 2-4% breakeven, 4-6% "
        "lock 25% of the gain, 6-10% lock 50%, 10% and up lock 75%; never down; at least 1% below the price. "
        "The simulation calls the live code for the raise. It raises once a day from the close, and a raised "
        "stop applies from the next session. The morning routine's raise (from a pre-market price that "
        "daily bars lack) is left out of these results and added as a sensitivity, which runs against "
        "the trailing modes: see the Reading and the morning-raise sensitivity.",
        "",
        f"Universe: {len(output['universe'])} watchlist symbols. Decisions {baseline.TEST_START} to "
        f"{baseline.TEST_END}; in-sample 2009-2019, out-of-sample 2020 on. Costs are per side on every fill.",
        "",
        "## Reading",
        "",
        "SELL-signal exits are a #574 baseline convention that the live system lacks: nothing in live "
        "sells on a signal. The variant that matches live exits is the one with them off, so the Reading "
        f"leads with it. {VARIANT_LABELS[lead]}, 5 bps a side, paired Sharpe "
        "differences:",
        "",
        *reading(tests, lead),
        "",
        *(
            [
                f"With SELL-signal exits kept (the baseline convention), {VARIANT_LABELS['moderate']}:",
                "",
                *reading(tests, "moderate"),
                "",
            ]
            if lead != "moderate" and "moderate" in results
            else []
        ),
        "These intervals are wide and the flat cost grid understates stop-fill slippage, which falls harder "
        "on the trailing modes (many more near-the-price stop exits). Read the intervals containing zero "
        "as 'not detected', not as 'no difference': the point estimates lean negative for trailing once "
        "the omitted effects below are counted.",
        "",
        *morning_reading(output, lead),
        "",
    ]
    for variant in VARIANTS:
        if variant not in results:
            continue
        lines += [f"## {VARIANT_LABELS[variant]}", "", "### Performance by exit mode", ""]
        lines += SCENARIO_HEADER + scenario_rows(results[variant]) + [""]
        lines += ["### Exits at 5 bps, all periods (closed trades)", ""]
        lines += exit_reason_table(results[variant]) + [""]
        lines += ["### Paired differences against the bracket", ""]
        lines += PAIRED_HEADER + paired_table(tests, variant) + [""]
    lines += morning_section(output)
    bench = output["benchmarks"]
    lines += [
        "## Benchmarks",
        "",
        "| Benchmark | Period | Sharpe [95% CI] | CAGR | Max DD |",
        "| --------- | ------ | --------------- | ---- | ------ |",
    ]
    for name, periods in bench.items():
        for period in PERIODS:
            s = periods[period]
            lines.append(
                f"| {name} | {PERIOD_LABELS[period]} | {fmt_sharpe(s)} | {s['cagr_pct']:.1f}% | "
                f"{s['max_drawdown_pct']:.1f}% |"
            )
    lines += [
        "",
        "## Method and limits",
        "",
        "- Paired Sharpe difference: both equity curves' daily returns are resampled with the same 20-day "
        "blocks (2000 replications), so the shared market and most shared entries cancel. The p-value is "
        "twice the smaller share of replications on one side of zero. The Newey-West columns test the mean "
        "daily return difference; the multiple-testing audit collects those.",
        "- The strategies share signals, not trades: holding periods differ, so the 10-position cap and the "
        "exposure limit block different entries.",
        "- Alpha is the annualized intercept of a daily regression on equal-weight buy-and-hold returns of the "
        "same symbols, with Newey-West t-statistics (5 lags). Mean hold is in calendar days.",
        "- Tiers and stops are measured from the open the position filled at, before costs.",
        "- Data: GOOGL's 2-for-1 split of 2014-04-03 is missing from gex.corporate_events (only the "
        "2022-07-18 20:1 split is listed), so the adjusted GOOGL series has a phantom -49.7% day there. "
        "The #574 baseline shares it. It is immaterial here: no GOOGL position was open across that "
        "date in any mode or variant. Not patched; it can be reported to the gex repo.",
        "",
        "Not modelled: the morning routine's raise from its pre-market price (daily bars have none; the "
        "morning-raise sensitivity stands the open in for it and runs against the trailing modes); the last "
        "ten minutes of the day under the raised stop; the live deferral of raises on non-replaceable stop "
        "orders (the sim lands every raise at once); the stale local entry price across a split (a known "
        "live quirk: live tiers use the pre-split cost, the sim the adjusted one); the evening raise's "
        "16:00 close against the 15:50 price the routine sees (a ten-minute information advantage; "
        "Sharpe-level timing noise of about +/-0.04 that the bootstrap interval does not include); "
        "stop-market slippage beyond the flat cost, which falls harder on the trailing modes; "
        "intraday decisions; IEX vs consolidated bars; 80-day "
        "GTC renewal (assumed seamless); the 90-day lapse of the take-profit leg in `trailing_target` (the "
        "target is kept); GTT sells; dividends.",
        "",
    ]
    return "\n".join(lines)


def to_native(obj):
    """Python natives for JSON."""
    if isinstance(obj, dict):
        return {str(k): to_native(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [to_native(v) for v in obj]
    if isinstance(obj, np.integer):
        return int(obj)
    if isinstance(obj, np.floating):
        return float(obj)
    if isinstance(obj, np.ndarray):
        return obj.tolist()
    return obj


def run_modes(variant, spec, modes, inputs, morning=False):
    """
    Simulate one variant under the given exit modes at every cost.

    Args:
        variant: Key of VARIANTS
        spec: Its sizing and signal-exit setting
        modes: Exit modes to run
        inputs: (prices, raw_closes, signals, bench_equity)
        morning: Add the morning routine's raise (the sensitivity); the keys then end in "+morning"

    Returns:
        ({key: {"<cost>bps": summary}}, {(variant, key, cost): equity})
    """
    prices, raw_closes, signals, bench_equity = inputs
    scenarios, equities = {}, {}
    for mode in modes:
        key = f"{mode}+morning" if morning else mode
        scenarios[key] = {}
        for cost in COSTS_BPS:
            rules = LiveRules(
                position_pct=spec["position_pct"],
                cost_bps=cost,
                exit_mode=mode,
                signal_exit=spec["signal_exit"],
                morning_raise=morning,
            )
            equity, trades = simulate(prices, signals, rules, raw_closes=raw_closes)
            exposure = baseline.exposure_series(equity, trades, prices)
            s = baseline.summarize(equity, trades, exposure, bench_equity)
            s["trades_in_sample"] = baseline.trade_stats(trades, end="2019-12-31")
            s["buys_not_taken"] = trades.attrs["blocked"]
            scenarios[key][f"{int(cost)}bps"] = s
            equities[(variant, key, cost)] = equity
            oos = s["out_of_sample_2020_on"]
            print(
                f"  {variant:24} {key:24} {cost:4.0f} bps  full Sharpe {s['full']['sharpe']:+.3f}  "
                f"OOS {oos['sharpe']:+.3f}  CAGR {s['full']['cagr_pct']:.2f}%  "
                f"maxDD {s['full']['max_drawdown_pct']:.1f}%  "
                f"trades {s['trades_full']['n_trades']}  hold {s['trades_full']['mean_hold_days']}"
            )
    return scenarios, equities


def run_variants(variants, inputs):
    """
    Every variant under the three exit modes, then the morning-raise sensitivity.

    Returns:
        (scenarios, paired_tests, sensitivity): the sensitivity holds its own scenarios and
        paired rows, kept apart from ``paired_tests`` (the multiple-testing audit's family).
    """
    scenarios, paired, equities = {}, [], {}
    sensitivity = {"scenarios": {}, "paired_tests_vs_bracket": [], "effect_of_morning_raise": []}
    for variant in variants:
        spec = VARIANTS[variant]
        scenarios[variant], variant_equities = run_modes(variant, spec, EXIT_MODES, inputs)
        equities.update(variant_equities)
        paired += paired_rows(variant, equities)
        print(f"  Morning-raise sensitivity, {variant} (the open stands in for the 09:20 price):")
        sens_scenarios, sens_equities = run_modes(
            variant, spec, MORNING_MODES, inputs, morning=True
        )
        sensitivity["scenarios"][variant] = sens_scenarios
        equities.update(sens_equities)
        sensitivity["paired_tests_vs_bracket"] += paired_rows(
            variant, equities, MORNING_COMPARISONS
        )
        sensitivity["effect_of_morning_raise"] += paired_rows(variant, equities, MORNING_EFFECTS)
    return scenarios, paired, sensitivity


def print_differences(variants, paired, sensitivity):
    """Paired Sharpe differences at 5 bps, full / in-sample / out-of-sample."""
    print("\n  Paired Sharpe differences at 5 bps (full / in-sample / out-of-sample):")
    groups = [
        (paired, COMPARISONS),
        (sensitivity["paired_tests_vs_bracket"], MORNING_COMPARISONS),
        (sensitivity["effect_of_morning_raise"], MORNING_EFFECTS),
    ]
    for variant in variants:
        for rows, comparisons in groups:
            for mode, base in comparisons:
                cells = []
                for period in PERIODS:
                    t = find_test(rows, f"{mode} - {base}", variant, period, 5.0)
                    low, high = t["sharpe_diff_ci95"]
                    cells.append(f"{t['sharpe_diff']:+.3f} [{low:+.3f}, {high:+.3f}]")
                print(f"  {variant:24} {mode} - {base}: " + "  ".join(cells))


def analyze(prices, raw_closes, signals, variants, missing=()) -> dict:
    """
    Everything the report holds: scenarios, paired tests, the morning-raise sensitivity, benchmarks.

    Args:
        prices: Per symbol, split-adjusted daily bars
        raw_closes: Per symbol, unadjusted closes
        signals: From compute_signals(), shared by every run
        variants: Keys of VARIANTS to run
        missing: Watchlist symbols without enough history (reported only)

    Returns:
        The output dict, in JSON natives
    """
    calendar = pd.DatetimeIndex(sorted(signals.index.get_level_values("date").unique()))
    bench_equity = baseline.benchmarks(prices, calendar)
    scenarios, paired, sensitivity = run_variants(
        variants, (prices, raw_closes, signals, bench_equity)
    )
    print_differences(variants, paired, sensitivity)

    bench = {
        name: {period: baseline.period_stats(eq, *span) for period, span in PERIODS.items()}
        for name, eq in bench_equity.items()
    }
    return to_native(
        {
            "run_timestamp": datetime.datetime.now().isoformat(),
            "issue": "#539",
            "description": "Trailing-tier exits against the fixed -5% / +8% bracket on the live rules",
            "methodology": methodology(),
            "universe": sorted(prices),
            "missing_symbols": list(missing),
            "signal_counts": {k: int(v) for k, v in signals["action"].value_counts().items()},
            "scenarios": scenarios,
            "paired_tests": paired,
            "morning_raise_sensitivity": {
                "description": (
                    "The two trailing modes again with the morning routine's raise (09:20 ET), the "
                    "day's open standing in for its pre-market price; see methodology"
                ),
                "modes": [f"{mode}+morning" for mode in MORNING_MODES],
                **sensitivity,
            },
            "benchmarks": bench,
        }
    )


def main():
    parser = argparse.ArgumentParser(description="Trailing exits vs the fixed bracket (#539)")
    parser.add_argument("--output", default=OUTPUT_STEM, help="file stem in the results folder")
    parser.add_argument(
        "--variants", nargs="+", default=list(VARIANTS), choices=list(VARIANTS), help="runs to do"
    )
    args = parser.parse_args()
    logging.disable(logging.WARNING)  # the voter and the trailing manager log every call
    os.chdir(REPO_ROOT)  # the baseline's helpers read config_defaults/ relative to the repo

    print("=" * 60)
    print("  TRAILING EXITS VS FIXED BRACKET ON THE LIVE RULES (#539)")
    print("=" * 60)
    wanted = baseline.watchlist_symbols()
    prices, raw_closes = baseline.load_prices(wanted)
    missing = sorted(set(wanted) - set(prices))
    print(f"  Universe: {len(prices)} of {len(wanted)} watchlist symbols (missing: {missing})")

    print("  Computing live signals once (about 2 ms per symbol-day)...")
    signals = compute_signals(
        {s: p["close"] for s, p in prices.items()},
        raw_closes,
        baseline.live_signal_fn(),
        baseline.TEST_START,
        baseline.TEST_END,
    )
    counts = signals["action"].value_counts().to_dict()
    print(f"  Signals: {len(signals)} symbol-days {counts}")

    output = analyze(prices, raw_closes, signals, args.variants, missing)
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    json_path = RESULTS_DIR / f"{args.output}.json"
    with open(json_path, "w") as f:
        json.dump(output, f, indent=2)
        f.write("\n")
    md_path = RESULTS_DIR / f"{args.output}.md"
    with open(md_path, "w") as f:
        f.write(render_markdown(output))
    print(f"\n  Results saved to {json_path} and {md_path}")


if __name__ == "__main__":
    main()
