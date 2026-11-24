# Research Scripts

Organized research and experiment scripts.

## Structure

```text
research/
├── configuration_system/    # Config system demos
└── exit_strategy_analysis/  # Exit strategy research
```

## Directories

### configuration_system/

Configuration management demonstrations for trading parameters.

### exit_strategy_analysis/

Exit strategy performance analysis. Key finding: Balanced exits (8% TP / 5% SL) optimal.

## Regime study (#672)

- `wk_means_replication.py`: Wasserstein k-means (`src/backtesting/regimes/`) on the paper's synthetic paths and on SPY in sample (#673). Write-up: `docs/08_research/03_strategy_research/wk_means_regimes.md`.
- `fetch_hourly_bars.py`: Alpaca hourly bars (read-only, SIP, raw) for the hourly part, saved to a folder outside the repo.

## Related

- Validation experiments: `docs/archived/experiments/`
- Analysis tools: `scripts/analysis/`
