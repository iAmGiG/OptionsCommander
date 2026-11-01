# Partial Exits against the Live Trailing Exit

**Issue**: #584
**Status**: Complete (measurement). No design shows a reliable gain over the live trailing exit. With SELL exits off, which matches live exits, every design lowers CAGR. Nothing survives multiple-testing correction.
**Date**: 2026-11-01
**Results**: [results/partial_exits.json](results/partial_exits.json) and [results/partial_exits.md](results/partial_exits.md) (every table: three variants, 0/5/10 bps, three periods, fills, sensitivities, by symbol)
**Code**: `scripts/research/partial_exits.py`, `PartialPlan` / `LiveRules.partial` in `src/backtesting/live_rules_sim.py`, the #584 family in `scripts/research/multiple_testing_audit.py`

The owner decides whether to build anything. This is the measurement.

## Question

Issue #584 asks: when a part of each position sells at a profit target and the rest keeps the trailing stop, does that improve on the live exit (the trailing stop, raised by the evening routine only since #649)? And does it matter how the plan reaches the broker? Alpaca rejects a partial limit sell while a stop holds all the shares, so the issue lists three designs:

1. **Shrink the stop**: the target shares rest as a limit sell and have no stop; only the other shares have the trailing stop.
2. **Software targets**: every share keeps its trailing stop; the 09:20 and 15:50 routines sell the target shares at the market when the price is at or past the target.
3. **Per-tranche orders**: each tranche has its own stop, and the target tranche also a limit sell (one cancels the other).

## Summary (what was measured)

- **The #539 baseline reproduced exactly.** The unchanged `trailing_vs_bracket.py` on the unchanged simulator gives a JSON identical to the committed one (0 differences apart from the timestamp). After the simulator change, the three #539 exit modes inside the new runner match the committed scenarios in all 243 compared blocks (3 variants, 3 modes, 3 costs, summary blocks).
- **Plan, fixed before the results**: `PartialExitManager` and `trading_modes.yaml` define two targets, a 50/50 split, the first a limit sell at `target_1_pct` over the entry, the second "trailing" (the rest keeps the trailing stop). `trading_modes.yaml` sets `target_1_pct` to 4% (conservative), 5% (moderate, the live mode) and 6% (aggressive), but `from_mode_manager`, the only constructor that takes a mode, ignores it and hard-codes 4%, as does the class default. So 4% is what would be registered (**T4**) and 5% is what the moderate config describes (**T5**). Both were run, nothing else. Whole shares: the first target gets `int(n / 2)` of `n` shares, the rest the remainder, a one-share position has no plan.
- **Headline** (SELL exits off, which matches live exits; moderate sizing; 5 bps a side; 2009 to 2026-10-09; Sharpe difference from the baseline with the paired 95% interval):

| Plan | Design 1, unprotected shares keep resting (D1h) | Design 1, sold at the next open (D1x) | Design 2, software (D2) | Design 3, per tranche (D3) |
| ---- | ----- | ----- | ----- | ----- |
| T4 (+4%) | +0.062 [-0.284, +0.434] | +0.049 [-0.111, +0.209] | +0.054 [-0.037, +0.151] | -0.013 [-0.109, +0.088] |
| T5 (+5%) | +0.152 [-0.194, +0.519] | +0.078 [-0.079, +0.248] | +0.045 [-0.045, +0.137] | -0.022 [-0.116, +0.076] |

  The baseline's Sharpe is 0.808 [0.334, 1.291]. Every one of these intervals includes zero.

- **Across all 216 tests** (8 plans, 3 variants, 3 costs, 3 periods): the point estimate is above zero in 166, but only 18 intervals exclude zero: 17 above (D1h 11, D2 6) and 1 below (D3 T4, moderate, SELL exits kept). Seventeen of the 18 are in the two SELL-exits-kept variants (12 in conservative sizing); only one is in the SELL-exits-off variant that matches live exits (D2 T4). At 5 bps full period, D1h T4 is +0.186 [+0.010, +0.358] (moderate, SELL exits kept) and +0.197 [+0.022, +0.366] (conservative).
- **The return falls in every design.** Compared with the baseline, CAGR is lower by 0.36 to 0.88 pp (D2, D1x, D3, D1h at T4, SELL exits off), mean return per position is lower for D1x, D2 and D3 (0.570%, 0.571%, 0.501% against 0.659%; D1h's 2.165% is of a few, long-held positions), and total dollar P&L on the $100,000 account is $58,497 for the baseline against $36,048 (D1h), $45,364 (D1x), $48,967 (D2) and $42,030 (D3). A Sharpe gain, where there is one, comes with lower exposure and volatility, not more profit. With SELL exits off, CAGR is below the baseline in all 24 full-period rows (both targets, every cost). The exception is with SELL exits kept: D1h at 10 bps is 0.01 to 0.07 pp above it (4 of all 72 full-period rows).
- **Which design.** D3 is nominally the worst: the mean daily return difference from the baseline is negative at raw p = 0.005 (T4, SELL exits off, 5 bps), and D3 is worse than D2 by -0.067 [-0.113, -0.021] (T4; not part of the corrected family). D1h looks best on Sharpe, but it is the design that leaves shares with no stop, ties up capital and rests on a hindsight-picked universe; see below.
- **Correction.** The family is the 216 paired tests (Newey-West, as #539 and #623). Raw p below 0.05: 29 (10.8 expected by chance), **Holm: 0 rejections; Benjamini-Hochberg: 0**. Pooled with the GEX, #539 and #623 families (723 tests): Holm 0, BH 0.

## What this supports (inference)

| Option | In the simulation | Verdict |
| ------ | ----------------- | ------- |
| Park #584 | No design has an interval away from zero after correction; with SELL exits off, every design lowers CAGR, and total P&L in the headline run; the live exit is already a trailing stop with a 1% floor | **Supported.** The data gives no reason to build partial exits for performance. |
| Build design 2 (software targets) | +0.054 [-0.037, +0.151] (T4); the simplest to build: no broker order changes, no clash with the stop; lowers the drawdown by 0.7 pp (8.4% to 7.7%); costs 0.36 pp of CAGR | Weakly supported at most. Null on Sharpe; if built for a reason other than performance (smaller drawdown, banking gains), it is the least risky of the three. |
| Build design 3 (per-tranche orders) | -0.013 [-0.109, +0.088]; the lowest mean return of the four; worse than design 2 by -0.067 [-0.113, -0.021] | Not supported. It is the most complex (a stop and a limit per tranche, OCO, renewals) and measures no better. |
| Build design 1 (shrink the stop) | D1h: +0.062 [-0.284, +0.434], but half of the planned positions (393 of 784) end with target shares and no stop, held 64 days on average (12 for the baseline), and entries fall from 3,709 to 824 closed positions; D1x (sell them at the next open): +0.049 [-0.111, +0.209] | D1h not supported: it leaves shares unprotected and its Sharpe is not trustworthy. D1x is design 2 with a worse fill and a more fragile live mechanism (see Caveats). |

The data does not say partial exits hurt: the Sharpe differences are small and uncertain. It says no design improves the strategy beyond noise, and that the return is lower in all of them. The entry signal has no demonstrated edge (#574), so this is a measurement of exits on a signal that is itself at about the buy-and-hold level; a better entry signal would change what partial exits are worth.

## Method

Same signals, sizing, limits, timing, fills at the open, costs, tiers, variants, periods, benchmark and statistics as #539 and #623 ([trailing_vs_bracket.md](trailing_vs_bracket.md), [morning_raise_real_prices.md](morning_raise_real_prices.md)): the live MACD+RSI voting signal on the 19-symbol #574 universe (CRM has no data), decisions from 2009-01-02 to 2026-10-09, split-adjusted prices, 0 / 5 / 10 bps a side, the three #539 variants, circular block bootstrap (20-day blocks, 2000 replications) for Sharpe and paired Sharpe differences, Newey-West tests of the mean daily return difference. Baseline: `trailing`, the evening raise only (no morning raise, #649). The fixed bracket and the old `trailing_target` (8% on all shares) are references.

| Key | Implementation |
| --- | -------------- |
| D1h | Target shares: limit sell at the target, no stop. Other shares: trailing stop. Limit fills on an intraday touch at the limit price, at the open on a gap above it. If the stop shares are stopped out, the target shares keep resting until the target, a SELL signal, or the end of the data. |
| D1x | As D1h, but once the stop shares are gone the target shares are sold at the next session's open (a routine finds a position with no stop). |
| D2 | Every share has the trailing stop. The target is seen at the open (the 09:20 routine, the open as proxy) and at the close (the 15:50 routine, the close as proxy): at or past the target, the target shares sell at that price. No intraday fill; no open check on the entry day. |
| D3 | Every share has the trailing stop (the same levels). The target shares also have a limit sell. Limit fills on an intraday touch. A bar that touches both counts as the stop, all shares (the conservative order of `_bracket_exit`). |

The trailing levels are the live code's, as in #539. The first target is measured from the fill (the open), as the stop and the old bracket target are.

**Counting.** A position is one trade (its legs combined, quantity-weighted), so trades, win rate and mean return per trade are comparable with the baseline's. Turnover is (dollars bought + dollars sold) / 2 per year over mean equity. "Target reached" is the share of positions with a target fill.

**Look-ahead.** D2's open check uses only the open; its close check uses only the close (the high and low of the session never trigger a sale, and a touch that neither check sees is counted: 707 positions in D2 T4). The target price is fixed at entry. Unit tests on hand-built bars (`tests/unit/backtesting/test_live_rules_partial_exits.py`) pin the fill price and reason of every design: limit at the limit price, gap at the open, stop first when both are touched, the optimistic reading as an option, the open check that cannot see the day's close, the close check that does not use the high or low, the entry-day rules, the unprotected shares of D1, costs on every leg, and slippage and limit-through as options. Two properties are tested on random walks: with an unreachable target, designs 2 and 3 reproduce plain trailing exactly; with the options off, the simulator's output is identical to before (also no extra columns).

**Simulator changes** are optional (`LiveRules.partial`, `stop_slip_bps`, `stop_slip_gap_only`; default off). The #539 modes ran through the changed simulator match the committed #539 results in all 243 blocks compared.

**Family.** The 216 tests are each plan against the baseline. Design-against-design, against-bracket and sensitivity comparisons are reported but not in the family.

## Results

SELL-signal exits off (the variant that matches live exits), moderate sizing, 5 bps, full period 2009 to 2026-10-09. Alpha is against equal-weight buy-and-hold of the same symbols (Newey-West t in brackets).

| Configuration | Sharpe [95% CI] | Sharpe diff vs baseline [95% CI] | CAGR | Max DD | Positions | Win rate | Mean return per position | Turnover (x equity / yr) | Target reached | Alpha (t) |
| ------------- | --------------- | -------------------------------- | ---- | ------ | --------- | -------- | ------------------------ | ------------------------ | -------------- | --------- |
| trailing, evening raise only (baseline, live) | 0.808 [0.334, 1.291] | (baseline) | 2.61% | -8.4% | 3,709 | 42.5% | 0.659% | 4.10 | | +0.34% (+0.55) |
| bracket (fixed -5% / +8%), reference | 0.833 [0.363, 1.300] | | 2.62% | -9.1% | 3,376 | 44.9% | 0.733% | 3.74 | | +0.12% (+0.22) |
| trailing + 8% target on all shares, reference | 0.791 [0.312, 1.272] | -0.017 [-0.193, +0.163] | 2.28% | -9.0% | 4,513 | 42.7% | 0.486% | 4.98 | | +0.20% (+0.38) |
| D1h, first target +4% | 0.870 [0.434, 1.333] | +0.062 [-0.284, +0.434] | 1.73% | -4.4% | 824 | 64.6% | 2.165% | 0.88 | 92.8% | -0.31% (-1.65) |
| D1x, first target +4% | 0.857 [0.383, 1.361] | +0.049 [-0.111, +0.209] | 2.11% | -6.4% | 3,453 | 56.8% | 0.570% | 3.79 | 53.4% | +0.32% (+0.70) |
| D2, first target +4% | 0.863 [0.383, 1.349] | +0.054 [-0.037, +0.151] | 2.25% | -7.7% | 3,703 | 46.0% | 0.571% | 4.08 | 45.6% | +0.39% (+0.78) |
| D3, first target +4% | 0.795 [0.305, 1.291] | -0.013 [-0.109, +0.088] | 1.98% | -8.1% | 3,706 | 50.9% | 0.501% | 4.07 | 52.2% | +0.19% (+0.40) |
| D1h, first target +5% | 0.960 [0.522, 1.429] | +0.152 [-0.194, +0.519] | 1.92% | -3.8% | 701 | 69.6% | 2.754% | 0.74 | 91.3% | -0.14% (-0.70) |
| D1x, first target +5% | 0.886 [0.414, 1.373] | +0.078 [-0.079, +0.248] | 2.28% | -6.5% | 3,425 | 54.0% | 0.630% | 3.77 | 45.7% | +0.42% (+0.87) |
| D2, first target +5% | 0.853 [0.378, 1.334] | +0.045 [-0.045, +0.137] | 2.29% | -7.7% | 3,704 | 43.7% | 0.580% | 4.08 | 37.8% | +0.37% (+0.73) |
| D3, first target +5% | 0.786 [0.297, 1.284] | -0.022 [-0.116, +0.076] | 2.04% | -8.5% | 3,706 | 46.2% | 0.519% | 4.07 | 43.9% | +0.17% (+0.35) |

Buy-and-hold of the same symbols (equal weight, daily rebalanced): Sharpe 1.124 [0.695, 1.618], CAGR 28.6%, max drawdown -47.0%. SPY: Sharpe 0.764, CAGR 12.7%. Every configuration above is far below it in return; alpha against it is not distinguishable from zero in any row.

### By period and variant (5 bps, Sharpe difference from the baseline, T4)

| Variant | Plan | Full | 2009-2019 | 2020 on |
| ------- | ---- | ---- | --------- | ------- |
| SELL exits off | D1h | +0.062 [-0.284, +0.434] | +0.125 [-0.289, +0.552] | -0.048 [-0.644, +0.556] |
| SELL exits off | D1x | +0.049 [-0.111, +0.209] | -0.000 [-0.221, +0.213] | +0.110 [-0.128, +0.354] |
| SELL exits off | D2 | +0.054 [-0.037, +0.151] | +0.095 [-0.033, +0.232] | +0.004 [-0.127, +0.150] |
| SELL exits off | D3 | -0.013 [-0.109, +0.088] | +0.043 [-0.082, +0.170] | -0.089 [-0.239, +0.076] |
| SELL exits kept, moderate | D1h | +0.186 [+0.010, +0.358] | +0.140 [-0.126, +0.372] | +0.241 [-0.006, +0.493] |
| SELL exits kept, moderate | D1x | +0.066 [-0.086, +0.218] | -0.008 [-0.208, +0.188] | +0.158 [-0.074, +0.394] |
| SELL exits kept, moderate | D2 | +0.050 [-0.041, +0.145] | +0.098 [-0.032, +0.233] | -0.013 [-0.139, +0.129] |
| SELL exits kept, moderate | D3 | -0.015 [-0.112, +0.084] | +0.063 [-0.070, +0.196] | -0.123 [-0.268, +0.037] |
| SELL exits kept, conservative | D1h | +0.197 [+0.022, +0.366] | +0.111 [-0.152, +0.347] | +0.303 [+0.059, +0.556] |
| SELL exits kept, conservative | D1x | +0.056 [-0.093, +0.205] | -0.020 [-0.205, +0.160] | +0.149 [-0.074, +0.388] |
| SELL exits kept, conservative | D2 | +0.049 [-0.041, +0.144] | +0.095 [-0.032, +0.228] | -0.013 [-0.144, +0.130] |
| SELL exits kept, conservative | D3 | -0.011 [-0.108, +0.090] | +0.044 [-0.087, +0.176] | -0.088 [-0.230, +0.070] |

The sign is stable across sizings for D2 (+0.05) and D3 (about -0.01). D2 is positive before 2020 and about zero after; D3 is positive before 2020 and negative after. The T5 rows and the 0 and 10 bps rows are in the results file. At 0 bps D2 is +0.092 [-0.004, +0.184] and at 10 bps +0.033 [-0.062, +0.125] (T4, SELL exits off, full period).

### How the plans exit (T4, SELL exits off, 5 bps, all periods)

| Configuration | Target fills (limit / gap / check) | Stop and trail exits (legs) | Positions with a target fill | Other |
| ------------- | ---------------------------------- | -------------------- | ---------------------------- | ----- |
| Baseline | | 3,709 | | |
| D1h | 622 / 152 / n.a. | 834 (253 stop, 440 trail, 48 stop gap, 93 trail gap) | 774 of 834 (92.8%) | 393 positions had their stop shares stopped out with target shares left, no stop; mean hold 64.3 days |
| D1x | 1,517 / 333 / n.a. | 3,453 positions; 1,542 orphan exits at the next open | 1,850 of 3,463 (53.4%) | |
| D2 | n.a. / n.a. / 615 at the open, 1,077 at the close | 3,703 positions | 1,692 of 3,713 (45.6%) | 707 positions touched the target in a session without either check seeing it |
| D3 | 1,576 / 363 / n.a. | 3,706 positions | 1,939 of 3,716 (52.2%) | 34 bars touched stop and target |

Positions here count D1h's resting ones: they cut the entries from 3,709 to 824 because a held symbol cannot be bought again and the 10-position cap fills with unprotected shares (mean exposure 8.0% against 13.9%).

### Same-bar ambiguity and fills

- **Same bar (stop and target in one daily bar).** D3, T4: 34 of 3,716 positions (0.9%); D3 T5 and D1 counts are in the results file. Reading them as the target first (the optimistic order) moves D3's Sharpe difference from -0.013 to +0.031 [-0.068, +0.133] (T4) and from -0.022 to +0.023 [-0.075, +0.125] (T5), a change of +0.043 and +0.045. The conservative order is the headline.
- **Gap fills.** The shared simulator fills a gap through the stop at the open. In the baseline 661 of 3,709 exits (18%) are gap fills. A 17 bps slippage on those alone (the one live fill, QQQ on 2026-10-30, was 17 bps below the open) lowers the baseline's Sharpe from 0.808 to 0.763. The differences from the baseline hardly move for D2 (+0.054 to +0.055) and D3 (-0.013 to -0.005); D1h moves +0.062 to +0.101 and D1x +0.049 to +0.068, because the partial plans make fewer stop exits.
- **17 bps on every stop fill** (not only gaps) lowers the baseline to 0.586. The differences become D2 +0.058, D3 -0.018, D1x +0.121, D1h +0.242 (T4). So the larger the stop slippage, the better the plans that sell part by limit or market instead look against the baseline; none of this changes D2 or D3 materially. Under this assumption D1x T5 is +0.175 [+0.022, +0.346], the only interval that excludes zero among the 22 sensitivity rows, and it is a sensitivity of a design whose orphan exits are market sells that the sensitivity does not slip, which flatters it.
- **Limit fills need a 10 bps trade-through** (a touch does not always fill): D3 T4 goes from -0.013 to -0.045 [-0.139, +0.052] and D1h from +0.062 to +0.051. The limit-touch assumption therefore favours designs 1 and 3 slightly. The trade-through applies to intraday touches only: a gap to or past the target still fills at the open.

In short, the signs and sizes of the D2 and D3 results depend little on these assumptions (0.00 to 0.05 Sharpe); D1's depend on them more because its stop exits are fewer.

### By symbol (T4, SELL exits off, 5 bps, full period)

- **The loss in dollars is spread over most symbols and concentrated in the big winners.** Total P&L difference from the baseline: D1h -$22,449 (4 symbols better, 15 worse), D2 -$9,530 (6 better, 13 worse), D3 -$16,467 (5 better, 14 worse). The top three symbols hold 46.6%, 42.5% and 41.2% of the gross absolute difference: TSLA (-$7,485 for D1h, -$2,825 for D2, -$4,066 for D3), AAPL, NFLX and NVDA lose from selling half early; SQQQ gains (+$5,878, +$3,222, +$1,821) because the baseline loses $6,838 on it.
- **The Sharpe differences do not depend on one symbol.** Leaving each symbol out in turn (point estimates, the whole universe re-simulated), the full-period Sharpe difference stays within +0.021 to +0.100 for D2 (+0.054 with all), -0.033 to +0.015 for D3 (-0.013) and +0.015 to +0.225 for D1h (+0.062). The per-symbol table is in the results file.

## Caveats

- **Universe and signal.** The universe was picked with hindsight (large winners) and the entry signal has no demonstrated edge (#574). Shares left with no stop (D1h) mostly recover on such a universe (target reached eventually in 92.8% of positions): this favours D1h in a way a live portfolio cannot count on. Inverse and leveraged ETFs (SQQQ, TQQQ, UPRO, SPXL) are in the universe and decay, so their unprotected shares do not behave like the stocks'.
- **Sharpe against return.** The Sharpe differences compare strategies with different exposure (8.0% to 13.9% of equity on average). Cash earns 0 here. Lower exposure lifts the ratio without a gain in profit; the CAGR, mean return and dollar P&L comparisons above show the cost.
- **Entries differ.** Strategies share signals, not trades: a partial exit frees the position cap and cash at other times, so the entries taken differ (D1h most).
- **Design 1 in the live system.** D1h assumes the target shares can rest with no stop. The reconciler would find a position with no stop and try to place one; Alpaca refuses a stop while the limit holds the shares. D1x sells at the next open what the reconciler cannot protect; how the live routines would actually treat that position is not modelled.
- **Limit orders** fill on a touch with no queue; partial fills, the 90-day GTC lapse of the limit orders and rejections are not modelled. D2 and D1x market sells fill at the open or close price (the proxy for the 09:20 and 15:50 prices) plus the flat cost; the close stands in for the 15:50 price (a ten-minute information advantage, as in #539), and #623's data starts in 2016, so the open proxy for 09:20 is checked only from 2016 (it was close enough there: a median 13 bps from the 09:20 price).
- **Fills.** Stops fill at the stop or the open (shared with #539); the one live stop fill was 17 bps below the open. See the sensitivity above. Costs are a flat bps per side.
- **Multiple tests.** 216 tests, dependent (shared signals, overlapping periods, costs, variants and plans): Holm stays valid, BH is approximate. The design-against-design comparison (D3 - D2 = -0.067 [-0.113, -0.021]) is one of ten per target and is not corrected.
- Everything else in the #539 `not_modelled` list applies (deferral of raises on non-replaceable stop orders, evening raise from the close, dividends, IEX vs consolidated bars, GTC renewals, the GOOGL 2014 split gap in the corporate-events table).

## Reproducing

```bash
PY=~/miniconda3/envs/OptionsCommander/bin/python
$PY -s scripts/research/partial_exits.py                 # about 40 minutes with 32 workers
$PY -s scripts/research/multiple_testing_audit.py        # adds the #584 family
```

The runner picks scratch state and cache folders unless `AUTOTRADER_STATE_DIR` and `AUTOTRADER_CACHE_DIR` are set. The gex database is read through the read-only research session.
