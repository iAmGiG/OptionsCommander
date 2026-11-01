# Research Documentation

Research documents, experimental results, and validation studies for OptionsCommander (formerly RH2MAS) strategies.

This research tests practitioner hypotheses and documents what works (and doesn't) to help the main project narrow scope.

## Contents

| Path | Description |
| ------ | ------------- |
| [01_broker_api_comparison_2025.md](01_broker_api_comparison_2025.md) | Broker API feature comparison and evaluation |
| [02_gex_research/](02_gex_research/) | Gamma Exposure (GEX) research and experiments |
| [03_strategy_research/](03_strategy_research/) | Trading strategy validation and backtests |
| [99_archived/](99_archived/) | Completed and deprecated experiments |

---

## Research Status Legend

| Status | Meaning | Action |
| -------- | --------- | -------- |
| 🛑 **STOP** | Hypothesis invalidated, research concluded | Do not pursue further |
| ✅ **DONE** | Findings validated and integrated | No further work needed |
| 🔄 **CONTINUE** | Promising results, worth pursuing | Allocate resources |
| ⚠️ **GAP** | Critical issue blocking production | Must address before launch |

---

## Research Summary (Oct 2026)

GEX re-validation on 2008 to 2026-10-09 (#576). Full write-up: [02_gex_research/regime_lookahead_revalidation.md](02_gex_research/regime_lookahead_revalidation.md).

| Hypothesis | Issue | Status | Evidence |
| ------------ | ------- | -------- | ---------- |
| MACD+RSI performs better in bullish gamma | #531 | 🛑 STOP | Same-day regime look-ahead; lag 1 SPY 0.81 vs 0.81 Sharpe (p=0.25) |
| GEX overlay improves dispersion trading | #545 | 🛑 STOP | +0.108 was a backtester artifact; fixed and split-adjusted +0.061 (p=0.36) |
| GEX overlay improves delta neutral / pairs | #544, #546 | 🛑 STOP | Null in every window after fixes |
| Bearish regime VaR 4.3x bullish | #543 | 🛑 STOP | 2.1x at lag 1; regime makes 99% VaR breach too often |
| GEX sign switches intraday momentum / reversal | #530 | 🛑 STOP | Pooled interaction p=0.067, wrong sign; timing strategy -0.5 bps/day |
| Negative GEX goes with higher next-day variance | #576 | ✅ DONE | 40 symbols: +7% beyond HAR and leverage, high-VIX only; descriptive, no out-of-sample forecast gain |

**Key insight**: the regime label is computed from end-of-day options data, so it must be lagged one day. With that and a corrected backtester, no GEX trading overlay or intraday effect survives multiple-testing correction (237 tests).

**Live trading rules (#574 stage A)**: [03_strategy_research/live_rules_baseline.md](03_strategy_research/live_rules_baseline.md) backtests the rules the CLI trades with, at live timing, on the 19-symbol watchlist. At 5 bps a side the Sharpe is 0.69 (0.71 from 2020, interval includes zero), and alpha against buy-and-hold of the same symbols is +0.11% a year (t = 0.19). This replaces the 0.856 Sharpe of experiment 293.

**Trailing exits vs the fixed bracket (#539)**: [03_strategy_research/trailing_vs_bracket.md](03_strategy_research/trailing_vs_bracket.md) runs the same signals under the live trailing-stop tiers. At 5 bps the full-period Sharpe is 0.83 (bracket), 0.81 (trailing) and 0.79 (trailing plus the 8% leg); every paired interval includes zero, and the omitted morning raise would lower the trailing modes by about 0.1.

---

## Research Summary (Dec 2025)

### Scope Narrowing — 🛑 STOP

Hypotheses tested and found **not actionable**. Saves the main project from pursuing dead ends.

| Hypothesis | Issue | Status | Evidence |
| ------------ | ------- | -------- | ---------- |
| GEX overnight swing filter (Close→Close+1) | #516 | 🛑 STOP | Median improvement -2.9% (makes it worse) |
| MACD parameters can be optimized for robustness | #518 | 🛑 STOP | Best OOS Sharpe -0.223 ("least unprofitable") |
| Academic TSMOM (12-mo return) is tradeable | #519 | 🛑 STOP | 19% pass rate, -0.259 avg net Sharpe |
| GEX + TSMOM hybrid (overnight hold) | #516 | 🛑 STOP | Hybrid underperforms pure strategies |

**Key Insight**: Most parameter optimization shows IS→OOS decay (overfitting). GEX as overnight/swing filter does not work.

**Important Caveat**: The intraday GEX use case (Open→Close, practitioner approach) was **never tested** - see #530.

### Critique of TSMOM Scope (Why it failed here vs Academia)

Similar to GEX, the "Academic TSMOM" tests (#519) may have failed due to implementation gaps relative to the literature (Moskowitz et al., 2012).

| Feature | Academic TSMOM | OptionsCommander Implementation |
| :--- | :--- | :--- |
| **Universe** | 58 instruments (Commodities, Bonds, FX, Equities) | Mostly Equity ETFs (SPY, QQQ) |
| **Sizing** | **Volatility Scaled** (Target 40% Ann. Vol) | Capital Allocation (Equal Weight) |
| **Mechanism** | Diversification across uncorrelated trends | Single-asset directional prediction |

**The Gap**: TSMOM is primarily a *portfolio construction technique*, not a *stock picking signal*. It relies on the fact that *something* is usually trending somewhere (e.g., Long Oil, Short Bonds). Testing it on SPY alone removes the diversification benefit and exposes the strategy to single-asset whipsaws.

**Status**: 🛑 STOP remains correct for *this project's current scope* (single-asset equity trading), but the strategy itself is likely valid for multi-asset portfolios.

---

### High Value Leads — ✅ DONE / 🔄 CONTINUE

Findings that show **promise**. Some integrated into production, others warrant further investigation.

| Finding | Issue | Status | Action |
| --------- | ------- | -------- | -------- |
| MACD+RSI voting more robust than TSMOM | #519 | ✅ DONE | Integrated in VoterAgent. The 0.856 Sharpe is superseded: no edge over buy-and-hold at live timing (#574) |
| VoterAgent validated | Exp 293 | 🛑 STOP | In-sample, same-close fills, no costs; superseded by the [live-rules baseline](03_strategy_research/live_rules_baseline.md) |
| Ready-Aim-Fire (RAF) outperforms VoterAgent | #460, #532 | 🛑 STOP | Re-tested Nov 2026 on the live rules: Sharpe 0.80 vs 0.69 for the baseline, paired difference +0.11 [-0.20, +0.46], alpha +0.32%/yr (t = 0.59). No detectable edge; see [raf_kama_live_rules.md](03_strategy_research/raf_kama_live_rules.md) |
| Weekly KAMA outperforms weekly MACD | #467, #529 | 🛑 STOP | Re-tested Nov 2026 on the live rules: alone or as a trend filter, no detectable edge over the baseline or buy-and-hold (one parameter set makes the filter significantly worse). See [raf_kama_live_rules.md](03_strategy_research/raf_kama_live_rules.md) |
| Path-dependent simulation reveals wick risk | #528 | 🔄 CONTINUE | Apply framework to all strategies |
| MACD+RSI in positive gamma (short-term) | #531 | 🛑 STOP | Re-validated Oct 2026: look-ahead artifact (see Oct 2026 summary) |

**Conclusion**: VoterAgent is integrated, but its live rules have no backtested edge over buy-and-hold (#574). RAF and weekly KAMA, re-tested on the live rules in Nov 2026, show no detectable edge either (no result survives Holm or BH correction).

---

### Critical Research Gaps — ⚠️ GAP

Issues that **must be addressed** before production deployment.

| Gap | Impact | Status | Resolution |
| ----- | -------- | -------- | ------------ |
| Wick Risk | Vectorized backtests overestimate Sharpe | ⚠️ GAP | Path-dependent simulation (#528) |
| Strategy Definitions | Multiple TSMOM/MACD definitions across scripts | ⚠️ GAP | Canonical implementations needed |
| Portfolio Correlation | Symbols analyzed in isolation | ⚠️ GAP | Portfolio-level simulation |
| Data Granularity | Some scripts estimate High/Low | ⚠️ GAP | Use actual OHLC data |

**Conclusion**: These gaps affect result reliability. Address before trusting backtest numbers for production sizing.

---

### Research Contradiction (Resolved)

| Script | Momentum Definition | GEX Finding |
| -------- | --------------------- | ------------- |
| `tsmom_vs_gex_analysis.py` (#421) | MACD+RSI (short-term) | Better in POSITIVE_GAMMA |
| `tsmom_gex_hybrid.py` (#516) | 12-month return (academic) | Tested NEGATIVE_GAMMA (failed) |

**Resolution**: These are different strategies. The contradiction arose from inconsistent terminology ("TSMOM" used for both). The #421 finding for MACD+RSI was re-validated in Oct 2026 (#531, #576) and did not hold once the regime was lagged.

---

## Methodology Standards

All research scripts now follow these standards:

1. **Look-ahead prevention**: `signals.shift(1)`, `regimes.shift(1)` for t+1 execution
2. **Transaction costs**: Turnover-proportional (not fixed deduction)
3. **Div-by-zero protection**: EPSILON = 1e-9
4. **Reporting**: Median alongside mean for outlier-robust metrics
5. **YAML serialization**: Explicit `float()`, `int()` casts to avoid numpy types
6. **GEX regime lag**: `GEXOverlay` returns the regime known before the queried date (`lag_days=1`); end-of-day options data cannot classify the same day (#576)
7. **Daily-signal backtests**: the signal for row *i* uses data through day *i − 1*; P&L is direction × size × the day's change (`run_daily_signal_strategy`)
8. **Multiple testing**: count every test run and report Holm and Benjamini-Hochberg results (`scripts/research/multiple_testing_audit.py`)
9. **Data end date**: research stops at 2026-10-09 (options vendor break on 2026-10-12)

---

## Quick Reference: What to Work On

### Stop Working On

- GEX regime as a filter or sizing overlay for any daily strategy (#531, #544-#546, #576)
- GEX as overnight/swing filter (Close→Close+1)
- Academic TSMOM (12-month lookback)
- MACD parameter optimization for Sharpe
- GEX + momentum hybrid strategies (overnight holds)

### Continue Working On

- Path-dependent wick simulation (#528)
- Portfolio-level correlation analysis

### Untested (Optional Future Research)

- Real option P&L for dispersion and delta neutral (current P&L is a toy proxy)

### Already Complete

- VoterAgent live-rules baseline (#574 stage A): no edge; Exp 293 superseded
- Expiry-day pinning by dealer gamma (#576): real but tiny, about 0.7 bps (BH only)
- MACD+RSI voting integration (#519)
- Methodology corrections (look-ahead, costs)

---

## Quick Links

- **Strategy Results**: [03_strategy_research/results/](03_strategy_research/results/)
- **GEX Research**: [02_gex_research/](02_gex_research/)
- **Archived Experiments**: [99_archived/](99_archived/)
