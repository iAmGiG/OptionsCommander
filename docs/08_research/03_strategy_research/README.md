# Strategy Research

Research documents and experimental results for trading strategy validation.

See [../README.md](../README.md) for consolidated research status (STOP/DONE/CONTINUE).

---

## Research Status Summary (Dec 2025, updated Oct 2026)

| Research | Issue | Status | Finding |
| -------- | ----- | ------ | ------- |
| TSMOM+GEX Hybrid | #516 | 🛑 STOP | Median -2.9% (worse than pure TSMOM) |
| MACD Parameter Optimization | #518 | 🛑 STOP | No robust edge, IS→OOS decay |
| Academic TSMOM (12-mo) | #519 | 🛑 STOP | 19% pass rate, -0.259 avg net Sharpe |
| Weekly KAMA | #467, #529 | 🛑 STOP | Nov 2026 re-test on the live rules: no edge over the baseline or buy-and-hold; as a filter, one parameter set is worse |
| MACD+RSI Voting (cost robustness) | #519 | ✅ DONE | 44% pass rate vs TSMOM, integrated in VoterAgent; no edge at live timing (#574, below) |
| Ready-Aim-Fire (RAF) | #460, #532 | 🛑 STOP | Nov 2026 re-test on the live rules: Sharpe 0.80 vs 0.69, paired difference not distinguishable from zero, alpha t = 0.59 |
| Live-rules baseline (Oct 2026) | #574 | 🛑 STOP | Live rules at live timing: Sharpe 0.69 at 5 bps, no alpha over buy-and-hold of the same symbols |
| Trailing exits vs bracket (Oct 2026) | #539 | ✅ DONE | No detectable difference (paired Sharpe -0.04 to 0.00, intervals include zero); morning raise would cost trailing about 0.1 |

---

## Research Documents

| Document | Issue | Description | Status |
| -------- | ----- | ----------- | ------ |
| [biggunz_research.md](biggunz_research.md) | #461 | BIGGUNZ Triple-Consensus Signal | Completed |
| [raf_research.md](raf_research.md) | #460 | Ready-Aim-Fire Multi-Stochastic (original, superseded) | 🛑 STOP |
| [weekly_kama_research.md](weekly_kama_research.md) | #467 | Weekly KAMA MA Crossover (original, superseded) | 🛑 STOP |
| [raf_kama_live_rules.md](raf_kama_live_rules.md) | #532, #529 | RAF and weekly KAMA re-tested on the live rules | ✅ Complete |
| [methodology_validation_summary.md](methodology_validation_summary.md) | #523, #495 | Methodology fixes and validation | ✅ DONE |
| [live_rules_baseline.md](live_rules_baseline.md) | #574 | Baseline of the live CLI rules for paper validation | ✅ Complete |
| [trailing_vs_bracket.md](trailing_vs_bracket.md) | #539 | Live trailing-stop exits against the fixed bracket | ✅ Complete |
| [partial_exits.md](partial_exits.md) | #584 | Partial exits against the live trailing exit, three designs | ✅ Complete (no reliable gain) |

## Results

Experimental results are stored as YAML files in the [results/](results/) folder:

| Results File | Issue | Status | Description |
| ------------ | ----- | ------ | ----------- |
| [macd_stability_results.yaml](results/macd_stability_results.yaml) | #518 | 🛑 STOP | MACD parameter stability - no robust edge |
| [transaction_cost_results.yaml](results/transaction_cost_results.yaml) | #519 | ✅ DONE | Transaction cost methodology validated |
| [tsmom_gex_hybrid_results.yaml](results/tsmom_gex_hybrid_results.yaml) | #516 | 🛑 STOP | TSMOM + GEX hybrid - hypothesis invalidated |
| [tsmom_multi_asset_results.yaml](results/tsmom_multi_asset_results.yaml) | #498 | 🛑 STOP | Multi-asset TSMOM - poor after costs |
| [walk_forward_results.yaml](results/walk_forward_results.yaml) | #523 | ✅ DONE | Walk-forward validation methodology |
| [weekly_kama_results.yaml](results/weekly_kama_results.yaml) | #467 | 🛑 STOP | Weekly KAMA, original (superseded by raf_kama_live_rules) |
| [biggunz_results.yaml](results/biggunz_results.yaml) | #461 | Archived | BIGGUNZ signal results |
| [raf_results.yaml](results/raf_results.yaml) | #460 | 🛑 STOP | RAF, original (superseded by raf_kama_live_rules) |
| [raf_kama_live_rules.json](results/raf_kama_live_rules.json) | #532, #529 | ✅ DONE | Six candidates + baseline on the live rules, 0/5/10 bps, paired tests, per-symbol P&L |
| [raf_kama_old_vs_new.json](results/raf_kama_old_vs_new.json) | #532, #529 | ✅ DONE | The old RAF and KAMA runners, one methodology change at a time |
| [live_rules_baseline.yaml](results/live_rules_baseline.yaml) | #574 | 🛑 STOP | Live CLI rules, 2009 to 2026-10-09, two modes, three cost levels |
| [trailing_vs_bracket.json](results/trailing_vs_bracket.json) | #539 | ✅ DONE | Exit modes bracket, trailing, trailing_target; sizing, costs, paired tests |
| [partial_exits.json](results/partial_exits.json) | #584 | ✅ DONE | Four partial-exit implementations at +4% / +5%, sizing, costs, paired tests, fills, sensitivities, per symbol |

## Key Findings (Dec 2025, updated Oct 2026)

### What Works (DONE/CONTINUE)

1. **MACD+RSI Voting** (#519): 44% pass rate vs 19% for TSMOM - ✅ Integrated in VoterAgent. More cost-robust than TSMOM, but see #574 below: the live rules have no edge over buy-and-hold
2. ~~**Weekly KAMA** (#467)~~: no edge on the live rules (Nov 2026), see [raf_kama_live_rules.md](raf_kama_live_rules.md)
3. ~~**Ready-Aim-Fire (RAF)** (#460)~~: no detectable edge on the live rules (Nov 2026), see [raf_kama_live_rules.md](raf_kama_live_rules.md)
4. **Methodology Standards**: Look-ahead prevention, turnover-proportional costs - ✅ Applied

### What Doesn't Work (STOP)

1. **TSMOM+GEX Hybrid** (#516): Median -2.9% improvement (makes it worse)
2. **MACD Optimization** (#518): Best OOS Sharpe -0.223 ("least unprofitable")
3. **Academic TSMOM** (#519): Poor after realistic transaction costs
4. **Live MACD+RSI rules** (#574 stage A): Sharpe 0.69 at 5 bps a side, alpha +0.11% a year (t = 0.19) against buy-and-hold of the same symbols - see [live_rules_baseline.md](live_rules_baseline.md)

See [02_project_status.md](../../04_development/02_project_status.md) for integration roadmap.
