# Forward Testing Protocol

> **Historical protocol (Issue #324).** The forward-test code (`src/trading/forward_test_manager.py` and its runner scripts) was deleted in #435, so the imports and commands below no longer work. Nothing opens trades automatically; the 30-day paper signal run (#574 stage C) is deferred. See [the live-rules baseline](../08_research/03_strategy_research/live_rules_baseline.md).

**Issue #324**: 30-Day Forward Testing Validation Before Live Deployment

## Overview

The forward testing framework validates system performance before live trading deployment. This is a **standalone testing infrastructure**, not an interactive CLI feature.

**Key Distinction**:

- ✅ This framework: Background validation tool for maintainers
- ❌ NOT included: Interactive "backtest AAPL" CLI commands (separate feature)

## Purpose

Validate that the trading system can:

1. Generate profitable signals consistently
2. Execute trades reliably
3. Meet minimum performance criteria
4. Operate stably for 30 consecutive days

## Components

### 1. ForwardTestManager

**File**: `src/trading/forward_test_manager.py`

Tracks all forward testing operations:

- Signal generation and recording
- Trade entry and exit tracking
- Daily P&L calculation
- Test state persistence

### 2. PerformanceValidator

**File**: `src/trading/performance_validator.py`

Calculates performance metrics:

- Sharpe Ratio
- Win Rate
- Max Drawdown
- Cumulative Return
- Profit Factor
- Risk-adjusted returns

### 3. TestReporter

**File**: `src/trading/test_reporter.py`

Generates formatted reports:

- Daily summaries
- Weekly progress reports
- Final validation report with go/no-go recommendation

### 4. Forward Test Runner

**File**: `scripts/forward_test_runner.py`

Main execution script for running tests and generating reports.

## Usage

### Starting a New Test

```bash
# Initialize new 30-day forward test
python scripts/forward_test_runner.py \\
    --test-name production_validation_2025 \\
    --capital 10000
```

### Daily Reports

```bash
# Generate daily summary
python scripts/forward_test_runner.py \\
    --test-name production_validation_2025 \\
    --report-type daily
```

### Weekly Reports

```bash
# Generate week 1 report (after 7 days)
python scripts/forward_test_runner.py \\
    --test-name production_validation_2025 \\
    --report-type weekly \\
    --week 1
```

### Final Validation Report

```bash
# Generate final report after 30 days
python scripts/forward_test_runner.py \\
    --test-name production_validation_2025 \\
    --report-type final \\
    --benchmark-return 500.00
```

## Integration with Trading System

### Recording Signals

In your trading code (for example, where `VotingSignal` is called):

```python
from src.trading.forward_test_manager import ForwardTestManager, SignalType

# Initialize test manager
test_manager = ForwardTestManager("my_test")

# Record signal when generated
signal = test_manager.record_signal(
    symbol="AAPL",
    signal_type=SignalType.BUY,
    confidence=0.75,
    price=175.50,
    indicators={
        "macd": 1.2,
        "rsi": 45.0,
        "signal_line": 0.8
    }
)
```

### Recording Trades

When a trade is executed:

```python
from datetime import datetime

# Record trade entry
trade = test_manager.record_trade(
    trade_id="TR_001",
    symbol="AAPL",
    entry_time=datetime.now(),
    entry_price=175.50,
    quantity=10,
    side="buy",
    stop_price=167.00,
    target_price=189.00
)

# Later, when trade exits...
test_manager.close_trade(
    trade_id="TR_001",
    exit_time=datetime.now(),
    exit_price=189.00,
    outcome=TradeOutcome.CLOSED_WIN
)
```

## Testing Protocol

### Phase 1: Setup (Days 1-2)

1. Configure paper trading environment
2. Validate API connections
3. Test order placement without real money
4. Verify position tracking accuracy

### Phase 2: Signal Generation (Days 3-10)

1. Run daily signal generation
2. Compare signals to manual analysis
3. Track signal timing and accuracy
4. Document edge cases and failures

### Phase 3: Execution Testing (Days 11-20)

1. Full pipeline paper trading
2. Monitor order fills and slippage
3. Test trailing stop functionality
4. Validate risk management rules

### Phase 4: Performance Analysis (Days 21-30)

1. Statistical performance validation
2. Compare to benchmark (SPY buy-and-hold)
3. Stress test with volatile market conditions
4. Final go/no-go decision

## Acceptance Criteria

### Minimum Performance Requirements

| Metric | Minimum | Target |
|--------|---------|--------|
| **Trade Count** | 20+ | 30+ |
| **Win Rate** | ≥50% | 51.4% (match backtest) |
| **Cumulative Return** | Positive | Beat SPY |
| **Max Drawdown** | <15% | <10.1% (match backtest) |
| **Sharpe Ratio** | >0.5 | 0.856 (match backtest) |

> **Superseded (Oct 2026, #574):** the "match backtest" targets above come from experiment 293, which doesn't describe the live rules. The [live-rules baseline](../08_research/03_strategy_research/live_rules_baseline.md) expects about 35 trades in 30 trading days and a win rate near 43%. Over 30 days a Sharpe ratio has a standard error of about 2.9, so the paper run is judged on fidelity to a replay of the same rules (#574 stage C), not on performance.

### Operational Requirements

| Metric | Minimum |
|--------|---------|
| **System Uptime** | 99%+ during market hours |
| **Signal Latency** | <30 seconds from trigger |
| **Order Execution** | 95% fills within 1 minute |
| **Error Rate** | <1% failed operations |

## Go-Live Decision

### Approval Requirements

ALL of the following must be true:

- ✅ All technical requirements met
- ✅ All performance gates achieved
- ✅ 30 consecutive days successful operation
- ✅ Final validation report approved
- ✅ Risk management protocols validated

### Sample Final Report Output

```text
================================================================================
FINAL VALIDATION REPORT - 30-DAY FORWARD TEST
================================================================================

Test: production_validation_2025
Period: 2025-01-15 to 2025-02-14
Duration: 30 days

--------------------------------------------------------------------------------
FINAL PERFORMANCE METRICS
--------------------------------------------------------------------------------

Trade Statistics:
  Total Trades: 28
  Winning Trades: 15
  Losing Trades: 13
  Win Rate: 53.57%

Return Metrics:
  Initial Capital: $10,000.00
  Total Return: +$1,245.00
  Return %: +12.45%
  Average Win: $142.50
  Average Loss: $95.30
  Profit Factor: 1.95

Risk Metrics:
  Sharpe Ratio: 0.892
  Max Drawdown: $847.00 (8.47%)
  Avg Trade Duration: 2.3 days

Benchmark Comparison:
  Strategy Return: +$1,245.00
  Benchmark Return: +$425.00
  Excess Return: +$820.00

--------------------------------------------------------------------------------
ACCEPTANCE CRITERIA VALIDATION
--------------------------------------------------------------------------------

✓ PASS - 20+ Trades Generated: 28
✓ PASS - Win Rate ≥50%: 53.6%
✓ PASS - Positive Return: +$1,245.00
✓ PASS - Max Drawdown <15%: 8.47%
✓ PASS - Sharpe Ratio >0.5: 0.892

--------------------------------------------------------------------------------
GO-LIVE RECOMMENDATION
--------------------------------------------------------------------------------

✅ **APPROVED FOR LIVE TRADING**

All acceptance criteria have been met.
System has demonstrated reliable performance over 30-day test period.

Recommendation: Proceed to live trading deployment.

================================================================================
```

## Circuit Breakers

### Auto-Stop Conditions

Testing will automatically halt if:

- Drawdown > 20%
- Daily error rate > 5%
- System unavailable > 1 hour during market hours

### Manual Review Required

For:

- Unusual trading patterns
- Unexpected API errors
- Performance degradation
- Risk limit violations

## File Locations

### State Files

- `state/forward_tests/{test_name}_state.json`

### Reports

- Daily: `reports/forward_tests/daily_summary_{date}.txt`
- Weekly: `reports/forward_tests/week_{n}_report.txt`
- Final: `reports/forward_tests/final_validation_report_{date}.txt`

## Best Practices

### Do's

- ✅ Run tests in paper trading mode first
- ✅ Generate daily reports to track progress
- ✅ Compare to benchmark (SPY) regularly
- ✅ Document all unusual observations
- ✅ Review test results weekly

### Don'ts

- ❌ Skip to live trading without 30-day validation
- ❌ Ignore failing acceptance criteria
- ❌ Modify strategy during testing
- ❌ Cherry-pick favorable test periods
- ❌ Proceed if system shows instability

## Future Enhancements

Potential improvements (separate issues):

- Interactive CLI backtesting commands
- Real-time performance dashboard
- Automated email/SMS alerts
- Multi-strategy comparison testing
- Walk-forward optimization integration

---

**Related Issues**:

- #324 - Forward Testing Protocol (this document)
- TBD - Interactive CLI Backtesting (future enhancement)
