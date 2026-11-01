# The 09:20 Morning Raise with Real Pre-market Prices

**Issue**: #623 (follow-up to #539, #622)
**Status**: Complete. Measured: on real 09:20 prices the morning raise lowers the trailing exits' Sharpe, by 0.07 to 0.16 in every one of the 18 headline comparisons, but only 9 of 54 intervals exclude zero and none survives correction. Nothing in the data favours keeping it.
**Date**: 2026-11-01
**Results**: [results/morning_raise_real_prices.json](results/morning_raise_real_prices.json) and [results/morning_raise_real_prices.md](results/morning_raise_real_prices.md) (every table: three variants, 0/5/10 bps, three periods), [results/live_morning_raises.json](results/live_morning_raises.json)
**Code**: `scripts/research/fetch_premarket_bars.py`, `scripts/research/morning_raise_real_prices.py`, `scripts/research/live_morning_raises.py`, `LiveRules.morning_prices` in `src/backtesting/live_rules_sim.py`

**Decision (owner, 2026-11-01)**: evening only. The morning routine no longer raises stops, and its repairs keep a stop's level.

## Question

The 09:20 ET routine raises stops to the trailing level from the broker's price at that moment, a pre-market trade. The evening routine (15:50 ET) does the same from the afternoon price. The #539 study could not model the morning raise (daily bars have no 09:20 price) and stood the day's open in for it, which cost the trailing modes about 0.1 Sharpe. Is that so with the real 09:20 price? The owner has three options: keep the morning raise; raise in the morning only from the previous close; raise in the evening only.

## Summary (what was measured)

- **Data.** Alpaca SIP 5-minute bars, 2016-01-04 to 2026-10-09, 19 symbols (the #574 universe). The price is the close of the last 5-minute bar that started before 09:20 (the 09:15 bar when it traded). 99.4% of 48,930 symbol-days have one; the 09:15 bar traded on 89%; only 2% are over 30 minutes old. On the 24 live morning rows (QQQ and SPY, 10-15 to 10-30) this price is within 3.5 bps of the price the routine recorded (median absolute difference 0.5 bps).
- **The #539 numbers reproduce exactly.** The existing runner, unchanged, gives a results JSON identical to the committed one (0 differences apart from the timestamp).
- **Effect of the morning raise (R minus evening only, E).** Full period 2016 to 2026-10-09, 5 bps, paired Sharpe difference with 95% interval:

| Variant | trailing | trailing_target |
| ------- | -------- | --------------- |
| SELL exits off (matches live exits) | -0.141 [-0.291, +0.001] | -0.075 [-0.189, +0.037] |
| SELL exits kept, moderate sizing | -0.098 [-0.232, +0.028] | -0.091 [-0.185, -0.003] |
| SELL exits kept, conservative sizing | -0.090 [-0.234, +0.050] | -0.101 [-0.202, -0.007] |

  All 18 full-period comparisons (3 variants, 2 modes, 3 costs) are negative (-0.07 to -0.16); 52 of 54 across periods are. Nine of the 54 intervals exclude zero; the other 45 include it. At 10 bps the losses are slightly larger (-0.10 to -0.16).

- **It is concentrated after 2019.** With SELL exits off, trailing: 2016-2019 +0.010 [-0.18, +0.20]; 2020 on -0.207 [-0.42, -0.01]. trailing_target: -0.015 and -0.099.
- **How close the open proxy was.** Across the 54 comparisons the proxy (O, open) minus the real price (R) averages +0.03 Sharpe: the proxy slightly understated the loss in 41 of 54, and its error is inside the noise (O - R is -0.001 to +0.064 at 5 bps full period, every interval includes zero). Against E the proxy gave -0.034 to -0.083 (trailing) and -0.069 to -0.081 (trailing_target), against -0.090 to -0.141 and -0.075 to -0.101 for the real price. On the same 2016+ window the proxy reproduces the 2009+ #539 figures (-0.083 and -0.076 against -0.088 and -0.110). The 09:20 price is close to the open as a price: the correlation of the two gaps is 0.983, the median absolute difference is 13 bps (90th percentile 48), and on days the open gaps up more than 0.5% (14,845 symbol-days) the 09:20 price already holds 97% of the gap on average. A small price difference moves some exits across a stop, so the Sharpe effect is noisier than the price match suggests.
- **Alternatives tested.** Half the gap (H), real price only when the pre-market is liquid (L), real price only when within 2% of the previous close (G2): none beats evening only. H loses about half as much as R (-0.021 to -0.069); L is R (the pre-market is liquid on nearly all days); G2 is no better than R and worse for trailing with SELL exits off (-0.143 [-0.27, -0.03]).
- **Multiple testing.** The family is the 216 paired tests of R, H, L and G2 against E. Raw p below 0.05: 32 (10.8 expected by chance). **Holm: 0 rejections; Benjamini-Hochberg: 0.** Pooled with the GEX and #539 families (507 tests): Holm 0, BH 4. The four are trailing G2 - E with SELL exits off (2020 on at 0, 5 and 10 bps, and the full period at 10 bps; raw p 0.0009 to 0.0044), where G2 is the variant that skips big-gap raises and does worse than evening only. Pooling makes BH lenient because the GEX family holds many tiny p-values, and G2 is one of four variants of one idea, so this is a weak signal at most. It says nothing in favour of the morning raise.

## What this supports (inference)

| Option | In the simulation | Verdict |
| ------ | ----------------- | ------- |
| Keep the morning raise from the 09:20 price | Lower Sharpe in 18 of 18 headline comparisons, -0.07 to -0.16; more trail exits (1,436 against 1,231 for trailing) and a lower mean trade return (0.51% against 0.69%); no case of a gain | Not supported by anything measured. Point estimates are consistently negative; intervals mostly include zero. |
| Raise in the morning only from the previous close | Identical to evening only on daily bars (the evening raise already used the close), so the data cannot tell them apart | Supported as far as evening only is. It would keep a second chance if an evening raise failed, at no cost in the simulation. |
| Raise in the evening only | The baseline E | Supported by the point estimates; the gain over keeping the raise is -(-0.07 to -0.16) Sharpe and is not significant. |

The data does not say that removing the morning raise helps for certain: no interval is clearly away from zero after correction. It says the raise has no measured benefit and a consistent, mostly small, cost. The cost scales with how much of an overnight gap the 09:20 price carries (97%) and how tightly the tiers lock (75% of the gain above 10% profit, with the stop only 1% below the price at its closest).

The simulation lands every evening raise at once. A live one could fail, and an option that keeps a morning run only as a retry from the previous close would keep that safety net without the gap-up locking. The live record so far shows no failed evening raise. This is a design consideration, not something the simulation measures.

## Method

Same signals, sizing, limits, fills at the open, costs, tiers and bootstrap as #539 ([trailing_vs_bracket.md](trailing_vs_bracket.md)): circular block bootstrap (20-day blocks, 2000 replications) for Sharpe and paired Sharpe differences, Newey-West tests for mean daily return differences. Only the window changes: decisions from 2016-01-04 (the first day Alpaca has pre-market bars) to 2026-10-09; the sub-periods are 2016-2019 and 2020 on. The simulator is the #539 one with `LiveRules.morning_prices`: per symbol and date, the split-adjusted price the 09:20 routine sees. The morning raise happens before the day's range is checked; positions bought at that day's open are not held yet; a day without a price has no morning raise for that symbol.

| Key | Morning raise price |
| --- | ------------------- |
| E | none (evening raise only; also what a morning raise from the previous close comes to on daily bars) |
| R | the last pre-market trade before 09:20 (the live routine's price) |
| O | the day's open (the #539 proxy), every day |
| Om | the open, only on days R has a price (like for like; equals O to the reported precision) |
| Rf | R only if the 09:15 bar traded |
| H | previous close + 0.5 x (R - previous close) |
| L | R only if at least $500k traded in 09:00-09:20 |
| G2 | R only if within 2% of the previous close |

H, L, G2 and their thresholds were fixed before the run. The audit's family is R, H, L and G2 against E. The open-proxy and variant-against-R comparisons are reported but kept out of the family.

**Pre-market data.** Alpaca's SIP feed returns 5-minute bars from 04:00 ET for data older than 15 minutes on this paper account; IEX has no pre-market bars. Prices are raw (as traded). They are divided by the day's raw / adjusted close ratio from the same split calendar the daily series uses, so they sit on the daily series' scale. Check: of 48,645 symbol-days compared, none has the adjusted 09:20 price more than 15% from the same day's adjusted open, including the 22 split days in the window (AAPL, TSLA, NVDA, AMZN, GOOGL, NFLX, the leveraged ETFs). The pre-market series is consistent with the daily series.

**Coverage** (2016-01-04 to 2026-10-09; days with a price / 09:15 bar traded / price older than 30 minutes): AAPL, QQQ, SPY, TSLA, TQQQ, SQQQ, IWM, META, AMD, MSFT, NVDA, AMZN, SPXL, UPRO, PLTR, COIN, NFLX: 99.4% to 100%, 80% to 100%, 0% to 1.2% (COIN from 2021-04-15 and PLTR from 2020-10-01, their listing dates). The thin ones: GOOGL 98.3% with a price, 59% with the 09:15 bar, 8.8% older than 30 minutes (median 13 pre-market bars); VTI 92.0%, 30%, 24% (median 6 bars). Every other symbol has a median of at least 23 pre-market bars before 09:20. The full table is in the results JSON. A missing price means no morning raise that day; a stale one (last trade up to a few hours old) is used as the broker would show it. Restricting to the 09:15-traded days (Rf) changes nothing (within 0.01).

**Look-ahead and the open.** The price is from bars that start before 09:20, so nothing from later in the day enters the raise. The stop is raised before the day's range is checked, as live. A raised stop above the open fills at the open (`trail_gap`), as the existing simulator treats any gap through a stop; `tests/unit/backtesting/test_live_rules_morning_prices.py` pins this, the case of a 09:20 price below the open, a missing price, a price equal to the previous close (no change), and that a different high, low and close later in the day do not change the stop in force at the open. The #539 proxy itself is mildly look-ahead (the 09:30 open is not known at 09:20), by a median 13 bps.

## Results

SELL-signal exits off (the variant that matches live exits), moderate sizing, 5 bps, full period 2016 to 2026-10-09. Alpha is against equal-weight buy-and-hold of the same symbols (Newey-West t in brackets).

| Mode | Sharpe [95% CI] | CAGR | Max DD | Trades | Win rate | Mean hold (days) | Alpha (t) |
| ---- | --------------- | ---- | ------ | ------ | -------- | ---------------- | --------- |
| bracket | 0.886 [0.287, 1.505] | 2.87% | -8.8% | 2,199 | 45.2% | 13.4 | +0.35% (+0.47) |
| trailing, evening only (E) | 0.879 [0.296, 1.486] | 2.93% | -8.4% | 2,392 | 42.6% | 11.8 | +0.65% (+0.78) |
| trailing, morning raise at 09:20 (R) | 0.738 [0.147, 1.346] | 2.36% | -9.2% | 2,643 | 41.8% | 10.4 | +0.09% (+0.12) |
| trailing, morning raise at the open (O) | 0.796 [0.205, 1.403] | 2.56% | -8.0% | 2,619 | 42.7% | 10.5 | +0.28% (+0.36) |
| trailing_target, evening only (E) | 0.768 [0.156, 1.414] | 2.27% | -8.8% | 2,937 | 42.7% | 8.8 | +0.17% (+0.23) |
| trailing_target, morning raise at 09:20 (R) | 0.693 [0.072, 1.340] | 2.04% | -8.8% | 3,098 | 42.4% | 8.2 | -0.05% (-0.07) |
| trailing_target, morning raise at the open (O) | 0.692 [0.075, 1.339] | 2.04% | -8.7% | 3,085 | 42.7% | 8.2 | -0.06% (-0.08) |

The raise adds exits: for trailing, trail exits go from 1,231 to 1,436 and mean trade return from 0.69% to 0.51%. Against the fixed bracket (Sharpe difference, paired): trailing E -0.007 [-0.28, +0.25], trailing R -0.148 [-0.45, +0.14]; trailing_target E -0.118 [-0.34, +0.10], R -0.194 [-0.42, +0.04]. This restates #539's finding on a shorter window: no detectable difference from the bracket without the morning raise; with it, the point estimate leans against the trailing exits.

### Paired differences (5 bps, Sharpe difference [95% CI])

SELL exits off:

| Comparison | Full | 2016-2019 | 2020 on |
| ---------- | ---- | --------- | ------- |
| trailing: R - E | -0.141 [-0.29, +0.00] | +0.010 [-0.18, +0.20] | -0.207 [-0.42, -0.01] |
| trailing: O - E | -0.083 [-0.22, +0.05] | -0.047 [-0.28, +0.17] | -0.102 [-0.30, +0.07] |
| trailing: H - E | -0.035 [-0.13, +0.05] | +0.001 [-0.14, +0.14] | -0.049 [-0.17, +0.07] |
| trailing: G2 - E | -0.143 [-0.27, -0.03] | +0.068 [-0.12, +0.24] | -0.231 [-0.38, -0.09] |
| trailing_target: R - E | -0.075 [-0.19, +0.04] | -0.015 [-0.20, +0.17] | -0.099 [-0.25, +0.05] |
| trailing_target: O - E | -0.076 [-0.19, +0.04] | -0.077 [-0.27, +0.11] | -0.073 [-0.22, +0.07] |
| trailing_target: H - E | -0.025 [-0.10, +0.05] | -0.011 [-0.14, +0.13] | -0.027 [-0.12, +0.07] |
| trailing_target: G2 - E | -0.041 [-0.14, +0.06] | +0.070 [-0.10, +0.23] | -0.085 [-0.21, +0.04] |

SELL exits kept, moderate sizing:

| Comparison | Full | 2016-2019 | 2020 on |
| ---------- | ---- | --------- | ------- |
| trailing: R - E | -0.098 [-0.23, +0.03] | -0.123 [-0.39, +0.10] | -0.093 [-0.27, +0.08] |
| trailing: O - E | -0.034 [-0.15, +0.08] | -0.094 [-0.28, +0.06] | -0.013 [-0.17, +0.14] |
| trailing_target: R - E | -0.091 [-0.19, -0.00] | -0.071 [-0.24, +0.09] | -0.101 [-0.23, +0.01] |
| trailing_target: O - E | -0.069 [-0.16, +0.02] | -0.101 [-0.28, +0.07] | -0.058 [-0.17, +0.06] |

At 0 bps the full-period R - E runs -0.07 to -0.12 (trailing) and -0.08 to -0.10 (trailing_target); at 10 bps -0.11 to -0.16 and -0.10 to -0.12. Conservative sizing is in the results file.

### Proxy against the real price

| Measure (48,645 symbol-days, 2016-2026) | Value |
| --------------------------------------- | ----- |
| Median absolute open minus 09:20, bps (90th percentile) | 13 (48) |
| Correlation of the gap to the open and the gap to 09:20 | 0.983 |
| Slope of the 09:20 gap on the open gap | 0.97 |
| Open gaps up over 0.5%: share where 09:20 holds over half the gap | 95.5% |
| Same days: 09:20 price below the open | 55% |

## Live record (description only)

Two positions (QQQ 3 shares, SPY 14), 12 mornings from 2026-10-15 to 2026-10-30, read from `state/user.db` (read-only), `state/scheduler.log` and Alpaca daily bars.

- **Morning raises happened on two days.** 10-15 (the first raise to the tiers: QQQ 585.96 to 694.01, SPY 624.72 to 739.65, from 09:20 prices 719.75 and 767.00) and 10-16 (QQQ 694.01 to 698.65, SPY 739.65 to 744.34, from 725.94 and 773.26). No session low reached either level: the QQQ low on 10-16 was 707.23 against the stop of 698.65, the SPY low 760.64 against 744.34. On the other ten mornings the 09:20 price implied a level no higher than the stop in force, so no raise was due.
- **The only stop fill is QQQ on 2026-10-30**, 3 shares at 695.9967, 09:32 ET. The stop in force was 698.77, set by the **evening** raise of 10-21 (not by a morning raise). The 09:20 price was 696.99 and the open 697.21, both already below the stop. A morning routine cannot lower a stop, so the morning raise had no part in this exit, and an evening-only policy would have held a stop at or near the same level (700.33, had the evening raise existed on 10-15, see below). The fill was 17 bps below the open and two minutes late; the simulator fills a gap through a stop at the open, so on this one fill it is slightly optimistic.
- **No evening raise before 10-20.** On 10-15 at 15:50 ET both positions were at tier prices (QQQ 728.17: the tiers give 700.33, above the 694.01 in force; SPY 771.71: 743.18 above 739.65), and the report says "No change". That is expected: the evening routine had no raise step until #596 merged on 10-19, so the 10-16 morning routine made these raises, to 698.65 and 744.34. It is not a raise that failed, and it says nothing about how often an evening raise fails.
- The study's pre-market price matches the live routine's: 24 morning rows, the difference between the last pre-market 5-minute bar close and the recorded `current_price` is -2.8 to +3.5 bps.

Twelve mornings and two positions cannot say whether the raise helps. This section describes what the simulation's morning raise looks like in practice; it is not evidence about its value.

## Caveats

- **The window is shorter than #539's** (10.8 years against 17.8), so intervals are wider; the 2009 to 2015 years are not covered because pre-market bars start in 2016. Results for 2020 on carry most of the effect.
- **Stops fill exactly at the stop price** within the bar and at the open on a gap, as in #539. The live fill on QQQ 10-30 was 17 bps below the open; the extra near-the-price stop exits the morning raise causes would cost more under real slippage.
- **The price is a 5-minute bar close**, not the exact last trade at 09:20:04. The live check above bounds the difference at a few bps. The routine can run up to 5 minutes late (to 09:25); not modelled.
- **Conditional comparison**: the entry signal has no out-of-sample edge (#574) and the universe was chosen with hindsight, as in #539.
- **Multiple tests**: 216 tests in the family, 32 below 0.05 raw, none after Holm or BH. The proxy, Om, Rf and variant-against-R comparisons are reported but not in the family. The three variants, three costs and three periods overlap, so the tests are dependent; Holm stays valid, BH is approximate.
- **Reproducibility**: the committed JSON comes from one full run; a second run of the SELL-exits-off variant in fresh state and cache folders under another hash seed gave identical scenarios and paired rows (216 of 216). The #539 runner reproduced its committed JSON exactly.

## Not done

- Raising later in the session (for example at 09:35 or 10:00) is not tested: it needs intraday bars inside the simulator's day, which the simulator does not have.
- The evening raise's 15:50 price is still approximated by the 16:00 close, as in #539.
- Dividends, GTC renewal and raise deferral, as before.
