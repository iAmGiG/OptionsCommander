# RAF and Weekly KAMA on the Live Rules

**Issues**: #532 (RAF as a third voter), #529 (weekly KAMA trend filter); original research #460, #467
**Status**: Complete. Neither claim reproduces. **Verdict: STOP both** (details below).
**Date**: 2026-11-01
**Results**: [results/raf_kama_live_rules.json](results/raf_kama_live_rules.json), [results/raf_kama_old_vs_new.json](results/raf_kama_old_vs_new.json), and the `entry_signals` section of [../02_gex_research/results/multiple_testing_audit.yaml](../02_gex_research/results/multiple_testing_audit.yaml)
**Code**: `src/backtesting/live_rules_signals.py`, `scripts/research/raf_kama_live_rules.py`, `scripts/research/raf_kama_old_vs_new.py`, tests in `tests/unit/backtesting/test_live_rules_signals.py`

## Question

# 532 proposes RAF (multi-stochastic) as a third voter on the strength of a 0.553 Sharpe and an "80% win rate". #529 proposes a weekly KAMA trend filter on the strength of Sharpe "~0.75". Both numbers predate the project's look-ahead and methodology fixes. Do they hold on the live rules at live timing, against the [#574 baseline](live_rules_baseline.md) and against holding the same symbols? This is for deciding whether an automated entry step is worth building.

## Summary

- **The #574 baseline reproduced** in the new runner: Sharpe 0.687 [0.205, 1.148] at 5 bps, out-of-sample 0.706, alpha +0.11% (t = 0.19), 4,111 trades. It matches the committed `live_rules_baseline.yaml` and `live_rules_baseline.md`.
- **RAF** (alone, or as a third voter) has a higher point-estimate Sharpe than the baseline (0.80 and 0.77 against 0.69 at 5 bps), but the paired differences (+0.11 [-0.20, +0.46] and +0.09 [-0.22, +0.40]) and the alphas against equal-weight buy-and-hold (+0.32%, t = 0.59; +0.29%, t = 0.56) are indistinguishable from zero. Equal-weight buy-and-hold of the same 19 symbols has Sharpe 1.12.
- **Weekly KAMA** alone is no better than the baseline under either parameter set. As a filter on the voter, the parameter set from the research note makes it significantly worse (-0.42 [-0.88, +0.05], Newey-West p = 0.007 before correction); the issue's set is indistinguishable from the baseline (+0.08 [-0.22, +0.39]).
- **Nothing survives correction.** Of 108 tests (6 candidates x 2 test types x 3 periods x 3 costs), 6 have raw p < 0.05 against 5.4 expected by chance, and all 6 are the research-parameter filter being worse. Holm and Benjamini-Hochberg reject none; the 5 bps full-period subset (12 tests) has the same result. Pooled with the 237 lag-1 GEX tests, BH rejects 2 of the entry tests, both in the negative direction (the filter is worse); Holm rejects none.
- **The old numbers were optimistic mainly because nothing compared them with holding.** Look-ahead fills were a smaller part than expected; see "Why the old numbers differ".

## What was tested

Everything ran through `live_rules_sim` at live timing: the signal for day d uses bars through d-1, the fill is day d's open, moderate sizing (2% of prior equity), 10 positions, 80% exposure, -5%/+8% bracket, SELL-signal exits on, 0/5/10 bps a side, 19 symbols, decisions 2009-01-02 to 2026-10-09 (data stops at the vendor break), in-sample 2009-2019, out-of-sample 2020 on. Split-adjusted prices, read-only DB, no dividends. Comparison is the baseline (live voter signal, same rules) and equal-weight buy-and-hold of the same symbols.

The rules were written into the runner before any result was seen. There was no parameter search. Where an issue left a choice, the plainest reading was used:

| # | Test | Rule |
| - | ---- | ---- |
| 1 | RAF alone | #532 pseudo-code: fast Stoch(5,3) %K > %D, medium Stoch(8,5) %K > 50, slow Stoch(17,5) %K > 50, close > TEMA(28), Fisher > 0 gives BUY; the mirror gives SELL; else HOLD |
| 2 | RAF as third voter | Live MACD and RSI voters plus RAF; the rule proposed in #532: two agree is a signal (two BUY votes BUY, two SELL votes SELL), three agree is STRONG, else HOLD |
| 3a | Weekly KAMA alone, research parameters | lookback 10, fast 2, slow 30; BULLISH = KAMA 6-week slope > 0, Fold-MA(5) 3-week slope > 0, price above both (the #467 profile). BUY when BULLISH, SELL when BEARISH |
| 3b | Weekly KAMA alone, issue parameters | lookback 6, fast 6, slow 7; BULLISH = weekly close > KAMA and KAMA rising over one week (the #529 Option A snippet) |
| 4a, 4b | Voter + weekly KAMA filter | A voter BUY goes through only while the weekly state is BULLISH; BEARISH and NEUTRAL hold off new entries; voter SELLs pass |

**Choices that the issues left open:**

- %K is the SMA(smooth) of the raw stochastic over the first number of bars, %D is SMA(3) of %K. The Fisher transform has no parameters in #532, so Ehlers' standard version (10 bars of median price) is used. These are not the old runner's rules: it had no %D cross, no Fisher, and used close +/- 1% for the high and low.
- #529 and `weekly_kama_research.md` disagree on the KAMA parameters (6/6/7 against 10/2/30 plus a Fold MA), so both were pre-declared as tests (3a/3b, 4a/4b).
- "Reduce size or exit" on a BEARISH week is not modelled: the simulator has one position size, and a bearish exit variant was not run.
- With three voters, a BUY needs two BUY votes. The live two-voter rule is looser (any BUY with no SELL acts), so test 2 changes the rule as well as adding a voter, as #532 proposes.
- RAF uses the last 400 calendar days of bars (about 275, enough for TEMA(28) to warm up); KAMA uses all history since 2008 (about one year of warm-up before 2009; 3a's KAMA converges more slowly than 3b's).

**No look-ahead, checked two ways.** A weekly value is used only once its week has closed: the decision on day d sees the Monday-Sunday weeks that ended before the week of d, so Monday to Friday bars of the current week never leak (a Friday close is used from the following Monday). Unit tests change the decision day's own bar, any later bar, and any bar of the unfinished week, and check the signal does not move; and change the last closed week, and check it can. The indicators are also tested to be causal (value at bar i equals the value computed on data cut at i). The baseline has no new code on its path: `compute_signals` gained optional `bars` (adds High/Low) and `frame.attrs["day"]`, and a close-only signal is unaffected.

## Results

5 bps a side, moderate sizing, full period 2009 to 2026-10-09 unless noted. Sharpe has a 95% 20-day block-bootstrap interval; alpha is against equal-weight buy-and-hold of the same symbols (Newey-West t); the paired columns are against the baseline (Sharpe difference with a paired bootstrap interval, and the Newey-West p of the mean daily return difference).

| Strategy | Sharpe [95% CI] | 2009-2019 | 2020 on [CI] | CAGR | Max DD | Trades (win %) | Turnover (x equity/yr) | Alpha full (t) | Alpha 2020 on (t) | Paired Sharpe diff [CI] | NW p |
| -------- | --------------- | --------- | ------------ | ---- | ------ | -------------- | ---------------------- | -------------- | ----------------- | ----------------------- | ---- |
| Baseline (#574) | 0.687 [0.205, 1.148] | 0.673 | 0.706 [-0.072, 1.502] | 1.98% | -6.9% | 4,111 (44.2) | 9.1 | +0.11% (0.19) | +0.34% (0.32) | | |
| 1. RAF alone | 0.799 [0.357, 1.261] | 0.797 | 0.790 [0.050, 1.527] | 2.25% | -6.4% | 2,810 (44.6) | 6.1 | +0.32% (0.59) | +0.64% (0.67) | +0.112 [-0.198, +0.460] | 0.59 |
| 2. MACD+RSI+RAF, 2 of 3 | 0.774 [0.331, 1.235] | 0.786 | 0.748 [-0.000, 1.472] | 2.08% | -5.4% | 2,661 (45.1) | 5.8 | +0.29% (0.56) | +0.50% (0.55) | +0.087 [-0.219, +0.400] | 0.84 |
| 3a. KAMA alone, 10/2/30 + Fold MA | 0.673 [0.232, 1.100] | 0.850 | 0.403 [-0.347, 1.122] | 1.86% | -5.5% | 2,226 (45.1) | 4.9 | +0.01% (0.02) | -0.38% (-0.39) | -0.014 [-0.504, +0.478] | 0.87 |
| 3b. KAMA alone, 6/6/7 | 0.784 [0.315, 1.269] | 0.843 | 0.686 [-0.082, 1.488] | 2.81% | -8.9% | 3,947 (43.9) | 8.7 | -0.10% (-0.17) | +0.09% (0.09) | +0.096 [-0.350, +0.570] | 0.28 |
| 4a. Voter + KAMA filter, 10/2/30 | 0.270 [-0.174, 0.721] | 0.334 | 0.181 [-0.606, 0.992] | 0.37% | -3.6% | 1,594 (45.4) | 3.4 | -0.21% (-0.69) | -0.30% (-0.46) | -0.418 [-0.878, +0.050] | 0.007 |
| 4b. Voter + KAMA filter, 6/6/7 | 0.771 [0.331, 1.201] | 0.821 | 0.701 [-0.049, 1.455] | 1.68% | -4.8% | 2,659 (46.4) | 5.8 | +0.44% (0.96) | +0.52% (0.61) | +0.084 [-0.217, +0.393] | 0.48 |
| Equal-weight buy-and-hold | 1.124 [0.695, 1.618] | | | 28.6% | -47.0% | | | | | | |
| SPY buy-and-hold | 0.764 | | | 12.7% | -34.1% | | | | | | |

- **Costs.** RAF alone: Sharpe 0.89 / 0.80 / 0.69 at 0 / 5 / 10 bps (baseline 0.84 / 0.69 / 0.52). Its lower turnover (6.1 against 9.1) is why it loses less from costs. Every paired interval includes zero at every cost and period except 4a, which is negative.
- **Out of sample.** Only RAF alone has a 2020-on Sharpe interval that excludes zero (0.790 [0.050, 1.527]) at 5 bps, and the paired difference against the baseline in that period is +0.08 [-0.48, +0.67]. The weekly KAMA 3a in-sample Sharpe (0.85) falls to 0.40 out of sample.
- **No dependence on a few symbols.** The three largest symbols account for 31% to 39% of total P&L for every candidate except 4a (baseline 33%), and 16 to 18 of 19 symbols are profitable (4a: 13 symbols, a total P&L of just 6.7% of capital, 59% of it from three symbols). The leading symbols are the universe's big winners (AAPL, NVDA, AMD, NFLX, GOOGL, META), as they are for the baseline. Per-symbol P&L is in the JSON (`by_symbol`, `concentration`).
- **Exposure and the benchmark.** The strategies hold 4% to 15% of equity on average (baseline 10.7%; 3b 15.2%, 4a 4.3%), and betas are 0.02 to 0.10. Their Sharpes come from holding a small slice of a universe that returned 29% a year, as the baseline's does, which is why alpha against the same symbols is the fair test. No candidate has an alpha with |t| above 1.

## Why the old numbers differ

The original RAF runner (`ready_aim_fire.py`, #460) and KAMA runner (`weekly_kama_strategy.py`, #467) were re-run unchanged on split-adjusted database prices, then one change at a time (`raf_kama_old_vs_new.py`, mean Sharpe over the symbols they used):

| RAF (QQQ, SPY, IWM, TQQQ, SOXL) | 2020-2025 | 2009 to 2026-10-09 |
| ------------------------------- | --------- | ------------------ |
| As published (synthetic high/low, same-close fill, no costs) | 0.48 | 0.38 |
| Fill at the next open | 0.59 | 0.45 |
| Real high and low | 0.60 | 0.42 |
| 5 bps a side | 0.55 | 0.37 |
| Buy-and-hold of the same symbols | 0.66 | 0.69 |

| Weekly KAMA (SPY, QQQ, AAPL, MSFT, NVDA, GOOGL, AMZN) | 2016-2024 | 2009 to 2026-10-09 |
| ----------------------------------------------------- | --------- | ------------------ |
| As published (one-week signal shift, 95% of cash) | 0.91 | 0.67 |
| 5 bps a side | 0.91 | 0.66 |
| Buy-and-hold of the same weekly series | 1.03 | 0.75 |

What this shows, and what it does not:

- **The old runs did not measure what the issues claim.** RAF's 0.553 and KAMA's 0.523 (or "~0.75", which the strategy README and #529 quote and `weekly_kama_research.md` does not contain) were compared with a weak in-house "VoterAgent" proxy (MACD histogram > 0, no 0.1 threshold, RSI band as a buy filter) or with weekly MACD, never with holding. Re-run on the same symbols, RAF is below buy-and-hold in 2020-2025 (0.55 to 0.60 against 0.66) and across the full period (0.37 to 0.45 against 0.69), and in four of five symbols individually. KAMA is below buy-and-hold in five of seven symbols in 2016-2024.
- **Look-ahead was not the main problem for RAF.** Moving the fill from the signal's close to the next open raised the old RAF Sharpe, not lowered it. The old `ready_aim_fire.py` backtest fills at the signal's own close, which is look-ahead in form, but the effect on these symbols is small and of the wrong sign for the story. The weekly KAMA runner already shifted its signal a week (#523), so it is conservative if anything.
- **Win rate "80%" is 4 symbols out of 5** where RAF's Sharpe beat the proxy's, not a trade win rate. Trade win rates in the old file are 42% to 60%; on the live rules RAF's is 44.6% against 44.2% for the baseline.
- **Other weaknesses:** 5 to 7 symbols, mostly index ETFs and 2020-2025 (a bull period with a strong 2022 drawdown), no costs, all-in 95% of cash in one symbol (so the Sharpe is of a different object than the live 2% positions), and for RAF a synthetic range for the stochastic (close +/- 1%). The rule in the issue's pseudo-code (a %D cross and a Fisher filter) was never what the runner tested.
- **Not reproduced exactly.** The old runs read a local sqlite copy of `options_daily_summary` prices that this checkout does not have. With database prices, SPY (0.593 against 0.591) and IWM (0.284 against 0.277) match; QQQ (0.689 against 0.737), TQQQ (0.503 against 0.713) and SOXL (0.333 against 0.450) do not, plausibly from the split handling of leveraged ETFs in the old prices. The conclusion does not depend on these.

## Verdicts

- **RAF (#532): STOP.** It is not worse than the baseline, and the point estimates favour it, but the paired and alpha intervals include zero, the benchmark (holding the symbols) is far better, and the baseline it would be added to has no edge itself. The implementation cost (five indicators, a three-voter rule that is also stricter than the live rule) is not justified by what the data shows. The 0.11 Sharpe difference is within the +/-0.3 the paired interval allows; a real effect of that size cannot be ruled out, and this data cannot confirm it.
- **Weekly KAMA (#529): STOP.** Alone, it matches the baseline (3a, -0.01) or sits within noise of it (3b, +0.10), and 3a's out-of-sample Sharpe is 0.40. As a filter, it cuts exposure and trades without improving risk-adjusted return: 4b is +0.08 [-0.22, +0.39]; 4a is -0.42, the only result with a raw p below 0.01, and it does not survive correction. Neither beats holding the symbols.

Measured: everything in the tables and the bridge. Inferred: that the old Sharpes were optimistic mainly through the missing buy-and-hold comparison and the small symbol set (the bridge shows the same rules do not beat holding; it cannot separate sampling noise from the data difference).

## Caveats

- **Power.** The paired intervals are about +/-0.3 to +/-0.5 Sharpe wide. "No detectable edge" is not "no edge"; an effect of 0.1 to 0.2 cannot be excluded.
- **One universe, chosen with hindsight.** The watchlist holds recent winners and leveraged ETFs (see the baseline's caveats). Alpha is measured against the same symbols.
- **The comparison is conditional on the live exits.** The -5%/+8% bracket and 2% sizing shape every result; a signal could look different with other exits. Trailing exits (#539) were not run.
- **Overlapping tests.** The 108 tests share the baseline, the universe, and periods and costs; Holm is valid, BH approximate.
- **Search budget.** Six candidates, the two KAMA parameter sets counted separately, were run once. The 4b parameters (6/6/7) are an odd reading of #529 (a nearly constant smoothing) and were run because the issue lists them.
- **Daily bars only.** The CLI's partial-day trading, IEX bars, dividends and GTC renewal are not modelled, as in the baseline.
