"""
Multiple-testing audit of the lag-1 GEX research results (#576).

Collects every hypothesis test of a GEX effect from the lag-1 result YAMLs and treats them
as one family, then applies:

- Holm (family-wise error rate; valid under any dependence between tests)
- Benjamini-Hochberg (false discovery rate; assumes independence or positive dependence)
- Harvey-Liu-Zhu style |t| > 3 hurdle

Included: lag-1 tests of a GEX effect (regime return gaps, overlay vs baseline returns, HAR
regime terms, out-of-sample Diebold-Mariano and VaR tick-loss comparisons).
Excluded: lag-0 runs (look-ahead), VaR calibration tests (Kupiec / Christoffersen test the
model, not GEX), cointegration tests, and files without ``regime_lag_days: 1``.

Full-sample and half-sample tests overlap, so the family has dependent tests; Holm stays
valid, BH is approximate.

The #539 exit-rule comparisons (trailing exits against the fixed bracket, in the live-rules
simulator) are tests of a trading rule, not of a GEX effect, so they are corrected as a family of
their own and reported under ``exit_rules``; the GEX family's numbers do not change. The same
tests pooled with the GEX family are reported too.

The #532 / #529 entry-signal candidates (RAF alone and as a third voter, weekly KAMA alone and as a
trend filter, in the live-rules simulator) are corrected as a third family, reported under
``entry_signals``: for each of the six candidates, a Newey-West test of its daily return difference
against the #574 baseline and its alpha against equal-weight buy-and-hold of the same symbols
(every period and cost, 108 tests; the headline subset is 5 bps, full period, 12 tests). Every comparison in the runner's
``paired_tests`` is in the family (periods, costs, sizings and the no-signal-exit variant overlap,
so Holm stays valid and BH is approximate). The runner's morning-raise sensitivity runs the same
hypotheses again under another assumption (the open standing in for the morning routine's
pre-market price), so its comparisons are not tests of their own and stay out of the family.

The #623 morning-raise comparisons (real 09:20 prices, and three variants of the raise, each
against the evening-only run) are a third family, corrected on its own and pooled with the other
two, and reported under ``morning_raise``.

Usage:
    ~/miniconda3/envs/OptionsCommander/bin/python scripts/research/multiple_testing_audit.py
"""

import datetime
import json
import math
from pathlib import Path

import pandas as pd
import yaml
from statsmodels.stats.multitest import multipletests

RESULTS_DIR = Path("docs/08_research/02_gex_research/results")
STRATEGY_RESULTS_DIR = Path("docs/08_research/03_strategy_research/results")
ALPHA = 0.05
T_HURDLE = 3.0


def _load(name: str):
    path = RESULTS_DIR / name
    if not path.exists():
        print(f"  missing: {name}")
        return None
    data = yaml.safe_load(open(path))
    lag = (data.get("methodology") or {}).get("regime_lag_days")
    if lag != 1:
        print(f"  skipped (regime_lag_days={lag}): {name}")
        return None
    return data


def _row(study, source, test, t, p, effect=None):
    return {
        "study": study,
        "source": source,
        "test": test,
        "effect": effect,
        "t_statistic": t,
        "p_value": p,
    }


def collect_macd(rows):
    name = "macd_rsi_regime_expanded_lag1.yaml"
    data = _load(name)
    for r in (data or {}).get("results", []):
        nw = r.get("newey_west_test")
        if nw:
            rows.append(
                _row(
                    "#531 MACD+RSI regime gap",
                    name,
                    f"{r['symbol']} bull-bear mean return (NW)",
                    nw["t_statistic"],
                    nw["p_value"],
                    nw["diff_mean"],
                )
            )


def collect_overlays(rows):
    files = {
        "delta_neutral_expanded_lag1.yaml": "#544 delta-neutral overlay, 2011-2026",
        "dispersion_expanded_lag1.yaml": "#545 dispersion overlay, 2011-2026",
        "pairs_trading_expanded_lag1.yaml": "#546 pairs overlay, 2011-2026",
        "delta_neutral_results.yaml": "#544 delta-neutral overlay, 2022-2023",
        "dispersion_results.yaml": "#545 dispersion overlay, 2021-2023",
        "pairs_trading_results.yaml": "#546 pairs overlay, 2022-2023",
    }
    for name, study in files.items():
        data = _load(name)
        for r in (data or {}).get("results", []):
            t = r.get("paired_return_test")
            if not t:
                continue
            label = r.get("symbol") or r.get("pair") or "index"
            rows.append(
                _row(
                    study,
                    name,
                    f"{label} overlay-baseline daily return (NW)",
                    t["t_statistic"],
                    t["p_value"],
                    r.get("sharpe_improvement"),
                )
            )


def collect_volatility(rows):
    name = "var_vol_baseline_lag1.yaml"
    data = _load(name)
    for r in (data or {}).get("results", []):
        sym = r["symbol"]
        for target, res in r["targets"].items():
            for part in ("full", "pre_2020", "from_2020"):
                fit = res.get(part, {})
                d = fit.get("bearish_minus_bullish")
                if d:
                    rows.append(
                        _row(
                            "#543 HAR regime term (in-sample)",
                            name,
                            f"{sym} {target} {part} bear-bull",
                            d["t_statistic"],
                            d["p_value"],
                            d["coef"],
                        )
                    )
            oos = res.get("out_of_sample", {})
            for loss in ("mse", "qlike"):
                dm = (oos.get(loss) or {}).get("diebold_mariano")
                if dm:
                    rows.append(
                        _row(
                            "#543 HAR out-of-sample forecast",
                            name,
                            f"{sym} {target} {loss} DM",
                            dm["t_statistic"],
                            dm["p_value"],
                            dm["mean_loss_diff"],
                        )
                    )
        for level in ("var_95", "var_99"):
            dm = ((r.get("var_backtest") or {}).get(level) or {}).get("quantile_loss_dm")
            if dm:
                rows.append(
                    _row(
                        "#543 VaR tick-loss comparison",
                        name,
                        f"{sym} {level} tick-loss DM",
                        dm["t_statistic"],
                        dm["p_value"],
                        dm["mean_loss_diff"],
                    )
                )


def collect_panel(rows):
    name = "gex_vol_panel_lag1.yaml"
    data = _load(name)
    if not data:
        return
    for sym, res in data["per_symbol"].items():
        for m, r in res.items():
            c = r["in_sample"][m]
            rows.append(
                _row(
                    f"Panel per-symbol {m} (in-sample, leverage controls)",
                    name,
                    f"{sym} {m}",
                    c["t_statistic"],
                    c["p_value"],
                    c["coef"],
                )
            )
            o = r["out_of_sample_qlike"]
            rows.append(
                _row(
                    f"Panel per-symbol {m} (out-of-sample QLIKE)",
                    name,
                    f"{sym} {m} QLIKE DM",
                    o["dm_t_statistic"],
                    o["dm_p_value"],
                    round(o["har"] - o["har_gex"], 5),
                )
            )
    for m, o in (data.get("pooled_out_of_sample") or {}).items():
        rows.append(
            _row(
                "Panel pooled out-of-sample",
                name,
                f"{m} QLIKE DM",
                o["dm_t_statistic"],
                o["dm_p_value"],
                round(o["qlike_base"] - o["qlike_gex"], 5),
            )
        )
    imp = data.get("impact_out_of_sample")
    if imp:
        for label, r in (("all rows", imp), ("high-impact rows", imp["only_high_impact"])):
            rows.append(
                _row(
                    "Panel impact out-of-sample",
                    name,
                    f"impact QLIKE DM, {label}",
                    r["dm_t_statistic"],
                    r["dm_p_value"],
                    r["mean_loss_diff"],
                )
            )
    for flag, r in (data.get("liquidity_split") or {}).items():
        for key in (f"neg_gex_x_{flag}", f"neg_gex_when_{flag}"):
            c = r[key]
            rows.append(
                _row("Panel liquidity split", name, key, c["t_statistic"], c["p_value"], c["coef"])
            )
    for m, specs in data["pooled"].items():
        c = specs["no_vix"][m]
        rows.append(
            _row("Panel pooled", name, f"{m} (leverage)", c["t_statistic"], c["p_value"], c["coef"])
        )
        ix = specs["with_vix"][f"{m}_x_highvix"]
        rows.append(
            _row(
                "Panel pooled", name, f"{m} x highVIX", ix["t_statistic"], ix["p_value"], ix["coef"]
            )
        )


def collect_intraday(rows):
    name = "intraday_gamma_momentum.yaml"
    data = _load(name)
    for m, res in (data or {}).get("results", {}).items():
        for sample in ("all", "ex_leveraged", "SPY", "QQQ"):
            r = res.get(sample)
            if r:
                ix = r["interaction"]
                rows.append(
                    _row(
                        "#530 intraday momentum x GEX sign",
                        name,
                        f"{sample} {m} interaction",
                        ix["t_statistic"],
                        ix["p_value"],
                        ix["coef"],
                    )
                )
        t = res.get("timing_strategy_all")
        if t:
            rows.append(
                _row(
                    "#530 intraday timing strategy",
                    name,
                    f"{m} timing",
                    t["t_statistic"],
                    t["p_value"],
                    t["mean_daily_bps_gross"],
                )
            )


def collect_pinning(rows):
    name = "expiry_pinning.yaml"
    data = _load(name)
    for outcome, r in (data or {}).get("results", {}).items():
        for key, label in (
            ("expiry_x_long_gamma", "expiry x long gamma"),
            ("expiry_effect_when_long_gamma", "expiry effect, long gamma"),
        ):
            c = r[key]
            rows.append(
                _row(
                    "Expiry-day pinning",
                    name,
                    f"{outcome} {label}",
                    c["t_statistic"],
                    c["p_value"],
                    c["coef"],
                )
            )


def collect_trailing_exits(rows):
    """#539: paired daily-return tests of each trailing exit mode against the fixed bracket."""
    name = "trailing_vs_bracket.json"
    path = STRATEGY_RESULTS_DIR / name
    if not path.exists():
        print(f"  missing: {name}")
        return
    with open(path) as f:
        data = json.load(f)
    for r in data["paired_tests"]:
        t = r.get("return_test")
        if t and not all(math.isfinite(t[k]) for k in ("t_statistic", "p_value")):
            print(f"  skipped a non-finite test: {r['comparison']} {r['period']}")
            continue
        if t:
            rows.append(
                _row(
                    f"#539 {r['comparison']} ({r['variant']})",
                    name,
                    f"{r['period']} {r['cost_bps']:g} bps daily return (NW)",
                    t["t_statistic"],
                    t["p_value"],
                    t["mean_daily_diff"],
                )
            )


def collect_morning_raise(rows):
    """#623: paired daily-return tests of the morning raise (real 09:20 prices) and its variants."""
    name = "morning_raise_real_prices.json"
    path = STRATEGY_RESULTS_DIR / name
    if not path.exists():
        print(f"  missing: {name}")
        return
    with open(path) as f:
        data = json.load(f)
    for r in data["paired_tests"]:  # the main group; the proxy comparisons are in paired_extra
        t = r.get("return_test")
        if t and all(math.isfinite(t[k]) for k in ("t_statistic", "p_value")):
            rows.append(
                _row(
                    f"#623 {r['comparison']} ({r['variant']})",
                    name,
                    f"{r['period']} {r['cost_bps']:g} bps daily return (NW)",
                    t["t_statistic"],
                    t["p_value"],
                    t["mean_daily_diff"],
                )
            )


def collect_entry_signals(rows):
    """#532 / #529: each candidate's paired test against the baseline and alpha against equal weight."""
    name = "raf_kama_live_rules.json"
    path = STRATEGY_RESULTS_DIR / name
    if not path.exists():
        print(f"  missing: {name}")
        return
    with open(path) as f:
        data = json.load(f)
    labels = data["methodology"]["candidates"]
    for r in data["paired_tests"]:
        t = r.get("return_test")
        if not t:
            continue
        rows.append(
            _row(
                f"#532/#529 {labels[r['candidate']]} vs baseline",
                name,
                f"{r['period']} {r['cost_bps']:g} bps daily return difference (NW)",
                t["t_statistic"],
                t["p_value"],
                t["mean_daily_diff"],
            )
        )
    for cand, label in labels.items():
        for cost_key, res in data["scenarios"][cand].items():
            for period, a in res["alpha_vs_equal_weight"].items():
                rows.append(
                    _row(
                        f"#532/#529 {label} alpha vs equal weight",
                        name,
                        f"{period} {cost_key} alpha (NW)",
                        a["alpha_t"],
                        a["alpha_p"],
                        a["alpha_ann_pct"],
                    )
                )


def entry_signal_family(entry_rows, gex_rows):
    """The #532 / #529 tests as a family of their own, a headline subset, and pooled with GEX."""
    df, summary, by_study = _correct(entry_rows)
    headline_rows = [
        r for r in entry_rows if r["test"].startswith("full ") and " 5 bps" in r["test"]
    ] + [r for r in entry_rows if r["test"].startswith("full 5bps")]
    head, head_summary, _ = _correct(headline_rows)
    pooled, _, _ = _correct(gex_rows + entry_rows)
    ours = pooled[pooled["study"].str.startswith("#532/#529")]
    summary["headline_5bps_full_period"] = head_summary
    summary["pooled_with_gex_family"] = {
        "n_tests": len(pooled),
        "entry_tests_holm_rejections": int(ours["holm_reject"].sum()),
        "entry_tests_bh_rejections": int(ours["bh_reject"].sum()),
    }
    print("\n  #532 / #529 entry-signal family (corrected on its own):")
    print(f"  {summary}")
    print(by_study.to_string(index=False))
    keep = ["study", "test", "t_statistic", "p_value", "effect", "holm_p", "bh_q"]
    print("\n  Smallest p-values:")
    print(df[keep].head(6).to_string(index=False))
    print("\n  Headline subset (5 bps, full period), smallest p-values:")
    print(head[keep].head(6).to_string(index=False))
    for frame in (df, head):
        for col in ("holm_p", "bh_q"):
            frame[col] = frame[col].round(4)
    return {
        "issues": "#532, #529",
        "description": "RAF and weekly KAMA candidates against the #574 baseline (live-rules simulator)",
        "family": "for each of six candidates: Newey-West test of the daily return difference "
        "against the baseline and of the alpha against equal weight, by period (full, 2009-2019, "
        "2020 on) and cost (0, 5, 10 bps)",
        "dependence_note": "the candidates share the universe, days and the baseline; periods and "
        "costs overlap; Holm valid, BH approximate",
        "summary": summary,
        "by_study": by_study.to_dict(orient="records"),
        "tests": df.to_dict(orient="records"),
        "headline_tests": head.to_dict(orient="records"),
    }


def _correct(rows):
    """Holm, Benjamini-Hochberg and |t| > 3 over one family: (tests, summary, by_study)."""
    df = pd.DataFrame(rows)
    n = len(df)
    p = df["p_value"].clip(lower=1e-12).to_numpy()
    df["holm_reject"], df["holm_p"], _, _ = multipletests(p, alpha=ALPHA, method="holm")
    df["bh_reject"], df["bh_q"], _, _ = multipletests(p, alpha=ALPHA, method="fdr_bh")
    df["raw_reject"] = df["p_value"] < ALPHA
    df["t_above_3"] = df["t_statistic"].abs() > T_HURDLE
    df = df.sort_values("p_value").reset_index(drop=True)

    summary = {
        "n_tests": n,
        "raw_p_below_0.05": int(df["raw_reject"].sum()),
        "expected_by_chance_at_0.05": round(n * ALPHA, 1),
        "holm_rejections": int(df["holm_reject"].sum()),
        "bh_rejections": int(df["bh_reject"].sum()),
        "abs_t_above_3": int(df["t_above_3"].sum()),
    }
    by_study = (
        df.groupby("study")
        .agg(
            n=("p_value", "size"),
            raw=("raw_reject", "sum"),
            holm=("holm_reject", "sum"),
            bh=("bh_reject", "sum"),
            t3=("t_above_3", "sum"),
        )
        .reset_index()
    )
    return df, summary, by_study


def exit_rule_family(exit_rows, gex_rows):
    """The #539 tests as a family of their own, and pooled with the GEX family."""
    df, summary, by_study = _correct(exit_rows)
    pooled, _, _ = _correct(gex_rows + exit_rows)
    ours = pooled[pooled["study"].str.startswith("#539")]
    summary["pooled_with_gex_family"] = {
        "n_tests": len(pooled),
        "exit_tests_holm_rejections": int(ours["holm_reject"].sum()),
        "exit_tests_bh_rejections": int(ours["bh_reject"].sum()),
    }
    print("\n  #539 exit-rule family (corrected on its own, and pooled with the GEX family):")
    print(f"  {summary}")
    print(by_study.to_string(index=False))
    keep = ["study", "test", "t_statistic", "p_value", "holm_p", "bh_q"]
    print("\n  Smallest p-values:")
    print(df[keep].head(5).to_string(index=False))
    for col in ("holm_p", "bh_q"):
        df[col] = df[col].round(4)
    return {
        "issue": "#539",
        "description": "Trailing exits against the fixed bracket (live-rules simulator)",
        "family": "every comparison in paired_tests of trailing_vs_bracket.json (Newey-West test "
        "of the mean daily return difference); its morning-raise sensitivity repeats the same "
        "hypotheses under another assumption and is not in the family",
        "dependence_note": "the comparisons share signals and overlap in periods, costs and "
        "sizings; Holm valid, BH approximate",
        "summary": summary,
        "by_study": by_study.to_dict(orient="records"),
        "tests": df.to_dict(orient="records"),
    }


def morning_raise_family(morning_rows, exit_rows, gex_rows):
    """The #623 tests as a family of their own, and pooled with the GEX and #539 families."""
    df, summary, by_study = _correct(morning_rows)
    pooled, _, _ = _correct(gex_rows + exit_rows + morning_rows)
    ours = pooled[pooled["study"].str.startswith("#623")]
    summary["pooled_with_gex_and_539_families"] = {
        "n_tests": len(pooled),
        "morning_tests_holm_rejections": int(ours["holm_reject"].sum()),
        "morning_tests_bh_rejections": int(ours["bh_reject"].sum()),
    }
    print("\n  #623 morning-raise family (corrected on its own, and pooled):")
    print(f"  {summary}")
    print(by_study.to_string(index=False))
    keep = ["study", "test", "t_statistic", "p_value", "holm_p", "bh_q"]
    print("\n  Smallest p-values:")
    print(df[keep].head(8).to_string(index=False))
    for col in ("holm_p", "bh_q"):
        df[col] = df[col].round(4)
    return {
        "issue": "#623",
        "description": "The 09:20 morning raise from real pre-market prices (live-rules simulator)",
        "family": "the 'main' comparisons of morning_raise_real_prices.json: morning raise from "
        "the real 09:20 price, and three variants (half gap, liquid pre-market only, gap within "
        "2% only), each against the evening-only run (Newey-West test of the mean daily return "
        "difference); the open-proxy and variant-against-raise comparisons repeat these under "
        "another assumption and are not in the family",
        "dependence_note": "the comparisons share signals and overlap in periods, costs, sizings "
        "and variants; Holm valid, BH approximate",
        "summary": summary,
        "by_study": by_study.to_dict(orient="records"),
        "tests": df.to_dict(orient="records"),
    }


def main():
    print("=" * 60)
    print("  MULTIPLE-TESTING AUDIT, LAG-1 GEX TESTS (#576)")
    print("=" * 60)
    rows = []
    collect_macd(rows)
    collect_overlays(rows)
    collect_volatility(rows)
    collect_panel(rows)
    collect_intraday(rows)
    collect_pinning(rows)
    print(f"  Tests in family: {len(rows)}")
    df, summary, by_study = _correct(rows)

    print(f"\n  {summary}")
    print("\n  By study:")
    print(by_study.to_string(index=False))
    print("\n  Tests surviving BH:")
    keep = ["study", "test", "t_statistic", "p_value", "holm_p", "bh_q", "t_above_3"]
    print(df.loc[df["bh_reject"], keep].to_string(index=False))

    for col in ("holm_p", "bh_q"):
        df[col] = df[col].round(4)
    output = {
        "run_timestamp": datetime.datetime.now().isoformat(),
        "issue": "#576",
        "description": "Multiple-testing audit of lag-1 GEX hypothesis tests",
        "methodology": {
            "family": "all lag-1 tests of a GEX effect in the result YAMLs",
            "excluded": "lag-0 runs, VaR calibration tests, cointegration tests",
            "corrections": ["holm", "benjamini_hochberg", "abs_t > 3"],
            "alpha": ALPHA,
            "dependence_note": "full and half-sample tests overlap; Holm valid, BH approximate",
        },
        "summary": summary,
        "by_study": by_study.to_dict(orient="records"),
        "tests": df.to_dict(orient="records"),
    }
    exit_rows = []
    collect_trailing_exits(exit_rows)
    if exit_rows:
        output["exit_rules"] = exit_rule_family(exit_rows, rows)
    morning_rows = []
    collect_morning_raise(morning_rows)
    if morning_rows:
        output["morning_raise"] = morning_raise_family(morning_rows, exit_rows, rows)
    entry_rows = []
    collect_entry_signals(entry_rows)
    if entry_rows:
        output["entry_signals"] = entry_signal_family(entry_rows, rows)
    path = RESULTS_DIR / "multiple_testing_audit.yaml"
    with open(path, "w") as f:
        yaml.dump(
            yaml.safe_load(pd.Series([output]).to_json(orient="records"))[0],
            f,
            default_flow_style=False,
            sort_keys=False,
            width=100,
        )
    print(f"\n  Results saved to {path}")


if __name__ == "__main__":
    main()
