# Live-Rules Baseline (Paper Validation Stage A)

**Issue**: #574 (stage A)
**Status**: Complete. No edge over holding the same symbols. Stage C (the 30-day paper signal run) and automated entries are deferred.
**Date**: 2026-10-15
**Results**: [results/live_rules_baseline.yaml](results/live_rules_baseline.yaml)
**Code**: `src/backtesting/live_rules_sim.py`, `scripts/research/live_rules_baseline.py`

## Summary

This backtests the rules the live CLI trades with, timed the way a pre-market routine would trade them. It replaces the 0.856 Sharpe claim (experiment 293). That figure was in-sample on AAPL in 2024, used MACD 12/26/9, filled at the close that produced the signal, and had no costs.

- **At 5 bps a side**, Sharpe is 0.69 over 2009 to 2026-10-09 and 0.71 out-of-sample (2020 onward). The out-of-sample 95% interval includes zero.
- **The Sharpe comes from holding a small slice of a universe whose equal-weight portfolio returned 29% a year.** Positions average 11% of equity in moderate mode and 5% in conservative mode. Regressed on equal-weight buy-and-hold of the same 19 symbols, the strategy's alpha is +0.11% a year (t = 0.19) over the full period and +0.34% (t = 0.32) out-of-sample. Beta is 0.067.
- **Holding 7% of the universe and the rest in cash would have done better.** Rebalanced daily, that position has the universe's Sharpe of 1.12, against 0.69 for the strategy. On this evidence the signal's timing adds risk and costs, not return.
- **Breakeven cost is about 6 bps a side** over the full period, and about 8 bps out-of-sample. Alpha is negative in both modes at 10 bps.

**Decision point (stage A of #574):** out-of-sample shows no edge over buy-and-hold of the same symbols. Stage B and the paper run can still test that the live system executes its rules faithfully. They can't be evidence for going live (#311), which the #574 plan says needs positive out-of-sample evidence from this stage.

## Rules Simulated

| Part | Live source | Simulation |
| ---- | ----------- | ---------- |
| Signal | `RealVoterStrategy` calls `VotingSignal.evaluate_ranked_voting` | The same call, on a 90-calendar-day close window |
| Indicators | MACD 13/34/8, histogram beyond ±0.1 (dollars); RSI 14 at 30/70, on bars split-adjusted as of the fetch | Unchanged; the window is rescaled to the decision day's split level, since the threshold is in dollars |
| Timing | Pre-market decision | Bars through day d-1; fills at day d's open |
| Size | `SimpleRiskManager`: risk_per_trade × equity, whole shares (signal strength ignored) | 2% (moderate) or 1% (conservative) of the prior close's equity, whole shares at the raw price |
| Limits | 10 positions, 80% exposure, buying power | The same, counted before the open: positions that exit later that day still count, and their proceeds can't fund that day's entries |
| Exits | Market bracket, stop -5% / target +8% (GTC; #587 renews and re-places stops, not targets) | SELL signal at the open; otherwise the bracket on the day's range. Gaps fill at the open, and a day that touches both counts as the stop |
| Costs | None modelled live | 0, 5 and 10 bps per side on every fill |

> **Live exits differ (Oct 2026):** the live positions' +8% take-profit legs lapsed at Alpaca's 90-day GTC limit, and nothing re-places them (the routines re-place stops only), so those positions exit only by their trailing stop. The simulation keeps the fixed -5% / +8% bracket.

**Data and periods:**

- **Prices:** `equity_prices_daily`, split-adjusted from `gex.corporate_events`, no dividends, read-only.
- **Periods:** a 2008 warm-up, then decisions from 2009-01-02 to 2026-10-09. In-sample is 2009-2019 and out-of-sample is 2020 onward.
- **Universe:** the scanner's default watchlist, 19 of 20 symbols. CRM is not in the database.
- **Sharpe:** annualized, with a risk-free rate of 0. The 95% intervals are a circular block bootstrap (20-day blocks).

## Results

| Scenario | Full Sharpe | 2009-2019 | 2020 on | CAGR | Max DD | Alpha vs EW, full / 2020 on (t) |
| -------- | ----------- | --------- | ------- | ---- | ------ | ------------------------------- |
| Moderate, 0 bps | 0.84 [0.36, 1.29] | 0.82 [0.28, 1.42] | 0.87 [0.09, 1.66] | 2.4% | -6.3% | +0.55% (0.96) / +0.89% (0.82) |
| Moderate, 5 bps | 0.69 [0.20, 1.15] | 0.67 [0.13, 1.27] | 0.71 [-0.07, 1.50] | 2.0% | -6.9% | +0.11% (0.19) / +0.34% (0.32) |
| Moderate, 10 bps | 0.52 [0.03, 0.98] | 0.53 [-0.02, 1.13] | 0.51 [-0.27, 1.31] | 1.5% | -7.5% | -0.38% (-0.65) / -0.32% (-0.29) |
| Conservative, 0 bps | 0.81 [0.33, 1.27] | 0.80 [0.25, 1.40] | 0.84 [0.06, 1.63] | 1.1% | -3.1% | +0.24% (0.88) / +0.40% (0.76) |
| Conservative, 5 bps | 0.68 [0.20, 1.14] | 0.67 [0.12, 1.28] | 0.70 [-0.08, 1.50] | 1.0% | -3.3% | +0.06% (0.21) / +0.17% (0.32) |
| Conservative, 10 bps | 0.53 [0.05, 0.99] | 0.52 [-0.03, 1.14] | 0.54 [-0.24, 1.33] | 0.7% | -3.5% | -0.15% (-0.55) / -0.09% (-0.17) |
| Equal-weight buy-and-hold | 1.12 [0.69, 1.62] | 1.24 [0.67, 1.83] | 1.01 [0.30, 1.81] | 28.6% | -47.0% | |
| SPY buy-and-hold | 0.76 [0.32, 1.26] | 0.78 [0.24, 1.35] | 0.75 [0.07, 1.54] | 12.7% | -34.1% | |

Alpha is the annualized intercept of a daily regression on equal-weight returns, with Newey-West t-statistics (5 lags). Cash earns nothing, in the strategy and the benchmarks alike.

### Trades (moderate, 5 bps)

| | 2009 to 2026-10-09 | 2020 on |
| - | ------------------ | ------- |
| Closed trades | 4,111 | 1,972 (1.2 per trading day) |
| Win rate | 44.2% | 43.0% |
| Mean / median trade | +0.46% / -1.10% | +0.43% / -1.26% |
| Mean hold | 8.8 days | 7.2 days |
| Exits: signal / stop / target | 1,427 / 1,447 / 1,237 | 633 / 718 / 621 |

Gap fills are counted with their stop or target. The ten positions still open at the end are left out.

The signal fires often: of 76,974 symbol-days, 28% were BUY and 34% SELL. The 10-position cap blocked about 3,050 BUY signals that weren't already held. The simulation breaks ties alphabetically; the stage B entry step will need its own rule. A slot smaller than one share blocked 164 entries at 2% and 540 at 1%. These are AMZN and GOOGL before their 2022 splits, plus NFLX and TSLA at 1%.

## What This Means for the Paper Run

The paper run (#574 stage C, deferred) should be judged on fidelity, not performance:

- **Thirty trading days can't measure a Sharpe of 0.7.** The standard error over 30 days is about 2.9.
- **Expected activity:** about 35 trades over 30 days, a win rate near 43%, and most exits by stop or by signal. A replay of these rules on the same days gives the exact comparison.
- **Targets to drop:** the forward-test protocol's "match backtest" targets (51.4% win rate, 0.856 Sharpe, -10.1% drawdown) came from experiment 293 and don't describe these rules.

## Not Modelled

- **Trailing stops:** both routines' trailing-stop raises (morning, and evening while the market is open), and live exits by trailing stop only once a take-profit has lapsed.
- **Intraday decisions:** the CLI can also trade during the day.
- **Bar source:** live bars come from IEX, not consolidated data.
- **Dividends:** they would add a little to both the strategy and the benchmarks.

## Caveats

- **Hindsight universe:** the watchlist holds recent winners, including leveraged ETFs, and was chosen with hindsight. That flatters both the strategy and equal-weight buy-and-hold. The alpha regression compares them on the same symbols, so it's the fairer test.
- **Price-dependent signal:** the MACD threshold is in dollars. A $0.10 histogram is a much smaller move for a $900 stock than for a $20 one, so expensive stocks signal more often.
