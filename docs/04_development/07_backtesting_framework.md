# 07. Backtesting Framework

**Status**: In use ✅
**Version**: 1.0
**Date**: 2025-12-02

---

## Overview

Lightweight, in-house backtesting framework for validating trading strategies. Built by refactoring validated experiment_293 code.

**Key Features**:

- Works with `VotingSignal`
- Multi-symbol support
- Commission modeling (Alpaca-compatible)
- Extensible signal generator architecture
- CLI-ready design

---

## Quick Start

```python
from src.backtesting import BacktestEngine
from src.trading.instruments.voting_signal import VotingSignal

# Run backtest
voter = VotingSignal()
engine = BacktestEngine(initial_capital=10000)

results = engine.run(
    signal_generator=voter.evaluate_voting,
    symbol="AAPL",
    start_date="2024-01-01",
    end_date="2024-12-31"
)

print(results)  # Displays: Sharpe, return, drawdown, trades, etc.
```

---

## Architecture

```text
src/backtesting/
├── backtest_engine.py    # Main engine
├── portfolio.py          # Position/cash tracking
├── results.py            # Metrics calculation
└── signals/
    └── tsmom_signal.py   # Time series momentum generator
```

---

## Signal Generator Contract

All signal generators must return:

```python
{
    "action": "BUY" | "SELL" | "HOLD",
    "position_size": 0.0 to 1.0,  # Fraction of capital
    "confidence": 0.0 to 1.0,      # Optional
    "reasoning": "..."             # Optional
}
```

**Compatible Generators**:

- `VotingSignal.evaluate_voting()` - MACD+RSI voting
- `TSMOMSignalGenerator.generate_signal()` - Time series momentum
- Custom generators (implement contract)

---

## CLI Integration (Future)

### Target Usage

```bash
python main.py backtest

> backtest AAPL from 2024-01-01 to 2024-12-31 using voter
> backtest NVDA from 2020-01-01 using tsmom with 6-month lookback
```

### Implementation Plan

1. **CLI Command** (`src/cli/backtest_commands.py`):
   - Parse natural language requests (GPT-4o-mini)
   - Map strategy names → generators
   - Run in background thread
   - Stream progress updates

2. **Strategy Registry** (`src/backtesting/strategy_registry.py`):

   ```python
   STRATEGY_REGISTRY = {
       "voter": VotingSignal().evaluate_voting,
       "tsmom": TSMOMSignalGenerator().generate_signal,
       "tsmom-6m": TSMOMSignalGenerator(lookback_days=126).generate_signal,
   }
   ```

3. **Background Execution**: Long backtests run in separate thread while user continues CLI session

---

## API Reference

### BacktestEngine

```python
engine = BacktestEngine(
    initial_capital=10000,       # Starting capital
    commission_per_share=0.005   # Alpaca rate
)

# Single symbol
results = engine.run(
    signal_generator=callable,
    symbol="AAPL",
    start_date="2024-01-01",
    end_date="2024-12-31"
)

# Multiple symbols
results_dict = engine.run_multi_symbol(
    signal_generator=callable,
    symbols=["AAPL", "GOOGL", "MSFT"],
    start_date="2024-01-01",
    end_date="2024-12-31"
)
```

### BacktestResults

```python
results.symbol              # Ticker
results.total_return        # Percentage
results.sharpe_ratio        # Annualized
results.max_drawdown        # Percentage (negative)
results.win_rate            # Percentage
results.volatility          # Annualized
results.num_trades          # Count
results.trades              # List of trade dicts
results.returns_series      # Pandas Series
```

---

## Creating Custom Generators

### Example: SMA Crossover

```python
# src/backtesting/signals/sma_crossover.py

class SMASignalGenerator:
    def __init__(self, fast_period=50, slow_period=200):
        self.fast_period = fast_period
        self.slow_period = slow_period

    def generate_signal(self, symbol: str, data: pd.DataFrame) -> dict:
        prices = data["close"]

        if len(prices) < self.slow_period:
            return {"action": "HOLD", "position_size": 0.0}

        fast_sma = prices.rolling(self.fast_period).mean().iloc[-1]
        slow_sma = prices.rolling(self.slow_period).mean().iloc[-1]

        if fast_sma > slow_sma:
            return {
                "action": "BUY",
                "position_size": 1.0,
                "confidence": 0.7,
                "reasoning": f"{self.fast_period}-day > {self.slow_period}-day SMA"
            }
        elif fast_sma < slow_sma:
            return {
                "action": "SELL",
                "position_size": 1.0,
                "confidence": 0.7,
                "reasoning": f"{self.fast_period}-day < {self.slow_period}-day SMA"
            }
        else:
            return {"action": "HOLD", "position_size": 0.0}
```

### Usage:

```python
sma = SMASignalGenerator(fast_period=50, slow_period=200)
engine = BacktestEngine()
results = engine.run(sma.generate_signal, "AAPL", "2016-01-01", "2024-12-31")
```

---

## Performance Metrics

| Metric | Formula | Target |
|--------|---------|--------|
| **Sharpe Ratio** | `sqrt(252) * mean(returns) / std(returns)` | > 0.6 |
| **Max Drawdown** | Maximum peak-to-trough decline | < -20% |
| **Win Rate** | Percentage of profitable days | 45-55% |
| **Volatility** | `std(returns) * sqrt(252)` | < 30% |

---

## Data Requirements

### Populate Cache

```bash
python scripts/populate_historical_cache.py \
  --symbols AAPL GOOGL MSFT \
  --type stock \
  --start 2016-01-01 \
  --providers alpaca
```

### Required Columns

- `close` (required)
- `open`, `high`, `low`, `volume` (optional)
- Datetime index (sorted)

---

## Commission Modeling

**Alpaca Rates**: $0.005 per share (no minimum)

```python
# Toggle commission
engine = BacktestEngine(commission_per_share=0.0)    # Commission-free
engine = BacktestEngine(commission_per_share=0.005)  # Realistic
```

---

## Validation

### Framework Validation

- ✅ Validated on AAPL 2024
- ✅ Sharpe 1.315 (refactored engine)
- ✅ All metrics calculate correctly

### TSMOM Validation

- 🔜 Pending: AAPL 2016-2024 backtest
- Target: Sharpe > 0.6
- Command: `python -m src.backtesting.signals.tsmom_signal`

---

## Troubleshooting

### "No data available"

```bash
# Solution: Populate cache
python scripts/populate_historical_cache.py --symbols AAPL --type stock
```

### "Insufficient data"

- Signal generator requires longer lookback
- Use longer date range or shorter lookback period

---

## Roadmap

- ✅ Phase 1: Core Framework (Issue #425)
- 🔜 Phase 2: TSMOM Research (Issue #420)
- 🔮 Phase 3: CLI Integration
- 🔮 Phase 4: Walk-forward optimization, parameter search

---

## References

- Moskowitz et al. (2012) - "Time series momentum"
- Refactored from `tests/archived/experiments/experiment_293_macd_vs_voting.py`
- Reproduces experiment 293: 0.856 Sharpe (in-sample, AAPL 2024). For the live rules, see the [live-rules baseline](../08_research/03_strategy_research/live_rules_baseline.md)

---

**Last Updated**: 2025-12-02
**Status**: In use ✅
