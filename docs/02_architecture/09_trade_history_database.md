# Trade History Database

**Issues**: [#373 Extension](https://github.com/iAmGiG/OptionsCommander/issues/373), [#444 Analysis Tracking](https://github.com/iAmGiG/OptionsCommander/issues/444)
**Commits**: 776e1d0 (trade_history), 782f8f2 (analysis_history)

## Overview

The trade history database implements a **hybrid storage approach** for trade lifecycle management:

- **Active Trades**: Stored in JSON files (fast, simple, reliable during market hours)
- **Completed Trades**: Archived to SQLite database (analytics, reporting, visualizations)
- **Analysis History**: Voter component details tracked for ML training (Issue #444)

This design provides optimal performance for live trading while enabling comprehensive historical analysis and machine learning capabilities.

## Architecture

### Database Schema

#### Trade History Table

```sql
CREATE TABLE trade_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,

    -- Trade identification
    trade_id TEXT NOT NULL UNIQUE,
    symbol TEXT NOT NULL,
    asset_type TEXT DEFAULT 'stock',

    -- Entry details
    entry_date TEXT NOT NULL,
    entry_price REAL NOT NULL,
    entry_order_id TEXT,
    quantity INTEGER NOT NULL,

    -- Exit details
    exit_date TEXT,
    exit_price REAL,
    exit_order_id TEXT,
    exit_reason TEXT,

    -- Risk management
    initial_stop_loss REAL,
    initial_take_profit REAL,
    final_stop_loss REAL,
    final_take_profit REAL,
    stop_adjustments INTEGER DEFAULT 0,

    -- Performance metrics
    realized_pnl REAL,
    realized_pnl_pct REAL,
    max_profit_pct REAL,
    max_drawdown_pct REAL,
    holding_period_hours REAL,

    -- Strategy attribution
    strategy_name TEXT,
    signal_strength TEXT,
    signal_confidence REAL,

    -- Execution quality
    entry_slippage_pct REAL,
    exit_slippage_pct REAL,
    commission_paid REAL,

    -- Broker tracking
    broker_account TEXT DEFAULT 'alpaca_paper',

    -- Metadata
    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
    notes TEXT  -- JSON field for additional details
)
```

**Indexes**:

- `idx_trade_symbol`: Symbol-based filtering
- `idx_trade_entry_date`: Time-based filtering
- `idx_trade_strategy`: Strategy performance analysis
- `idx_trade_exit_reason`: Exit reason analysis
- `idx_trade_broker`: Multi-broker tracking

#### Analysis History Table (Issue #444)

Tracks voter component details (MACD/RSI) for ML training and strategy analysis:

```sql
CREATE TABLE analysis_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,

    -- Analysis context
    timestamp TEXT NOT NULL,
    ticker TEXT NOT NULL,
    timeframe TEXT NOT NULL,

    -- MACD component
    macd_histogram REAL,
    macd_signal TEXT,  -- BUY/SELL/HOLD from MACD alone

    -- RSI component
    rsi_value REAL,
    rsi_signal TEXT,   -- BUY/SELL/HOLD from RSI alone

    -- Consensus result
    final_signal TEXT NOT NULL,  -- BUY/SELL/HOLD
    confidence REAL NOT NULL,

    -- Execution tracking
    action_taken TEXT NOT NULL,  -- executed/rejected/hold_signal/pending
    trade_id INTEGER,

    FOREIGN KEY (trade_id) REFERENCES trade_history(id)
)
```

**Indexes**:

- `idx_analysis_ticker`: Fast ticker lookups
- `idx_analysis_timestamp`: Time-based queries
- `idx_analysis_signal`: Signal frequency analysis
- `idx_analysis_timeframe`: Timeframe optimization studies

**Key Features**:

- Captures individual MACD and RSI signals (not just consensus)
- Links analyses to executed trades via foreign key
- Tracks rejected signals and HOLD decisions
- Enables ML training on component-level data
- Supports strategy optimization and pattern recognition

## API Reference

### TradingCacheManager Methods

#### `archive_trade(trade_data: Dict[str, Any]) -> bool`

Archive a completed trade to the database.

**Required Fields**:

- `trade_id`: Unique identifier
- `symbol`: Stock symbol
- `entry_date`: Entry datetime (ISO format)
- `entry_price`: Entry price
- `quantity`: Number of shares

**Optional Fields**:

- `exit_date`, `exit_price`, `exit_reason`
- `initial_stop_loss`, `initial_take_profit`
- `realized_pnl`, `realized_pnl_pct`
- `strategy_name`, `signal_strength`, `signal_confidence`
- `broker_account`
- `notes`: Dict (will be JSON serialized)

**Example**:

```python
from src.data_sources.cache import TradingCacheManager

cache = TradingCacheManager()

trade_data = {
    'trade_id': 'TQQQ_2024-01-15T10:30:00',
    'symbol': 'TQQQ',
    'entry_date': '2024-01-15T10:30:00',
    'entry_price': 50.25,
    'quantity': 100,
    'exit_date': '2024-01-15T15:45:00',
    'exit_price': 52.10,
    'exit_reason': 'take_profit',
    'realized_pnl': 185.0,
    'realized_pnl_pct': 3.68,
    'strategy_name': 'VotingSignal',
    'signal_strength': 'BULLISH',
    'signal_confidence': 0.85
}

cache.archive_trade(trade_data)
```

#### `get_trade_history(...) -> pd.DataFrame`

Query trade history with flexible filters.

**Parameters**:

- `symbol`: Filter by symbol (None = all)
- `start_date`: Entry date >= start_date (YYYY-MM-DD)
- `end_date`: Entry date <= end_date (YYYY-MM-DD)
- `strategy`: Filter by strategy name
- `broker_account`: Filter by broker account
- `limit`: Limit number of results

**Example**:

```python
# Get all TQQQ trades from 2024
trades = cache.get_trade_history(
    symbol="TQQQ",
    start_date="2024-01-01"
)

print(f"Total trades: {len(trades)}")
print(f"Win rate: {(trades['realized_pnl'] > 0).sum() / len(trades) * 100:.1f}%")
print(f"Total P&L: ${trades['realized_pnl'].sum():.2f}")

# Get VotingSignal trades from last month
import datetime
last_month = (datetime.datetime.now() - datetime.timedelta(days=30)).strftime('%Y-%m-%d')
voter_trades = cache.get_trade_history(
    strategy="VotingSignal",
    start_date=last_month
)
```

#### `get_trade_stats(...) -> Dict[str, Any]`

Get aggregated trading statistics.

**Parameters**:

- `symbol`: Filter by symbol (None = all)
- `strategy`: Filter by strategy (None = all)

**Returns**:

- `total_trades`: Total number of trades
- `winning_trades`: Number of profitable trades
- `losing_trades`: Number of losing trades
- `win_rate_pct`: Win rate percentage
- `total_pnl`: Total realized P&L
- `avg_pnl`: Average P&L per trade
- `avg_win`: Average winning trade
- `avg_loss`: Average losing trade
- `profit_factor`: Gross profit / gross loss
- `avg_holding_hours`: Average holding period

**Example**:

```python
# Overall stats
stats = cache.get_trade_stats()
print(f"System Performance:")
print(f"  Win Rate: {stats['win_rate_pct']:.1f}%")
print(f"  Profit Factor: {stats['profit_factor']:.2f}")
print(f"  Total P&L: ${stats['total_pnl']:.2f}")
print(f"  Avg Win: ${stats['avg_win']:.2f}")
print(f"  Avg Loss: ${stats['avg_loss']:.2f}")
print(f"  Avg Hold: {stats['avg_holding_hours']:.1f} hours")

# VotingSignal specific stats
voter_stats = cache.get_trade_stats(strategy="VotingSignal")
print(f"\nVotingSignal Performance:")
print(f"  Total Trades: {voter_stats['total_trades']}")
print(f"  Win Rate: {voter_stats['win_rate_pct']:.1f}%")
```

### AnalysisHistoryManager Methods (Issue #444)

Location: `src/data_sources/database/analysis_history_manager.py`

#### `record_analysis(...) -> bool`

Record voter component analysis details for ML training.

**Parameters**:

- `ticker`: Stock symbol
- `timeframe`: Analysis timeframe (1d, 1h, 5m, etc.)
- `macd_histogram`: MACD histogram value
- `macd_signal`: MACD signal (BUY/SELL/HOLD)
- `rsi_value`: RSI value (0-100)
- `rsi_signal`: RSI signal (BUY/SELL/HOLD)
- `final_signal`: Consensus signal (BUY/SELL/HOLD)
- `confidence`: Confidence percentage (0.0-1.0)
- `action_taken`: Status (executed/rejected/hold_signal/pending)
- `trade_id`: Optional link to executed trade

**Example**:

```python
from src.data_sources.database import AnalysisHistoryManager

manager = AnalysisHistoryManager()

# RealVoterStrategy records automatically, but you can also record manually
manager.record_analysis(
    ticker="SPY",
    timeframe="1d",
    macd_histogram=0.506458,
    macd_signal="BUY",
    rsi_value=55.7,
    rsi_signal="HOLD",
    final_signal="BUY",
    confidence=0.65,
    action_taken="executed"
)
```

#### `get_signal_statistics(ticker=None) -> Dict[str, Any]`

Get signal frequency statistics for analysis.

**Returns**:

- `total_analyses`: Total number of analyses
- `signal_counts`: Dict of final signal counts {BUY: X, SELL: Y, HOLD: Z}
- `macd_counts`: Dict of MACD signal counts
- `rsi_counts`: Dict of RSI signal counts
- `action_counts`: Dict of action taken counts
- `ticker`: Ticker filter (or None)

**Example**:

```python
# Get overall statistics
stats = manager.get_signal_statistics()
print(f"Total analyses: {stats['total_analyses']}")
print(f"MACD BUY signals: {stats['macd_counts']['BUY']}")
print(f"RSI BUY signals: {stats['rsi_counts']['BUY']}")

# Get SPY-specific statistics
spy_stats = manager.get_signal_statistics(ticker="SPY")
print(f"SPY MACD vs RSI agreement: {spy_stats['macd_counts']['BUY']} vs {spy_stats['rsi_counts']['BUY']}")
```

#### `get_ml_training_data(executed_only=False) -> List[Dict[str, Any]]`

Export analysis data for ML training.

**Parameters**:

- `executed_only`: If True, only return analyses that resulted in executed trades

**Returns**: List of dictionaries with all analysis features

**Example**:

```python
# Get all analyses for training
all_data = manager.get_ml_training_data(executed_only=False)
print(f"Total dataset size: {len(all_data)}")

# Get only executed trades for outcome-based training
executed_data = manager.get_ml_training_data(executed_only=True)
print(f"Executed trades: {len(executed_data)}")

# Convert to pandas for ML
import pandas as pd
df = pd.DataFrame(executed_data)

# Feature engineering
df['macd_rsi_agreement'] = df['macd_signal'] == df['rsi_signal']
df['signal_strength'] = df['confidence'] * df['macd_rsi_agreement'].astype(int)
```

#### `link_analysis_to_trade(analysis_id, trade_id) -> bool`

Link an analysis record to an executed trade.

**Example**:

```python
# Whatever records a trade's analysis calls this when the trade executes
manager.link_analysis_to_trade(
    analysis_id=123,
    trade_id=456  # From trade_history.id
)
```

## Integration

### TradeCycle Auto-Archival

The `TradeCycle` class automatically archives trades when positions are closed:

```python
# In trade_lifecycle.py
def close_position(self, reason: str = "Manual close") -> bool:
    # ... existing close logic ...

    if close_result['status'] == 'submitted':
        self.data.state = TradeState.EXIT_TRIGGERED.value
        self.save_state()

        # Automatic archival to database
        self._archive_to_database(reason=reason)

        return True
```

**Key Points**:

- ✅ Non-invasive: Single line addition to existing code
- ✅ Fail-safe: Won't break trade execution if DB unavailable
- ✅ Automatic: No manual intervention required
- ✅ Complete: Captures full trade lifecycle data

## Use Cases

### Performance Analysis

```python
from src.data_sources.cache import TradingCacheManager
import pandas as pd

cache = TradingCacheManager()

# Get all trades
all_trades = cache.get_trade_history()

# Calculate cumulative P&L
all_trades['cumulative_pnl'] = all_trades['realized_pnl'].cumsum()

# Plot equity curve
import matplotlib.pyplot as plt
plt.figure(figsize=(12, 6))
plt.plot(all_trades['entry_date'], all_trades['cumulative_pnl'])
plt.title('Equity Curve')
plt.xlabel('Date')
plt.ylabel('Cumulative P&L ($)')
plt.xticks(rotation=45)
plt.tight_layout()
plt.show()
```

### Strategy Comparison

```python
# Compare strategies
strategies = ['VotingSignal', 'TradeCycle', 'MACD+RSI']
comparison = []

for strategy in strategies:
    stats = cache.get_trade_stats(strategy=strategy)
    comparison.append({
        'Strategy': strategy,
        'Trades': stats['total_trades'],
        'Win Rate': f"{stats['win_rate_pct']:.1f}%",
        'Total P&L': f"${stats['total_pnl']:.2f}",
        'Profit Factor': f"{stats['profit_factor']:.2f}",
        'Avg P&L': f"${stats['avg_pnl']:.2f}"
    })

df_comparison = pd.DataFrame(comparison)
print(df_comparison.to_string(index=False))
```

### Exit Reason Analysis

```python
# Analyze exit reasons
all_trades = cache.get_trade_history()

# Group by exit reason
exit_analysis = all_trades.groupby('exit_reason').agg({
    'trade_id': 'count',
    'realized_pnl': ['sum', 'mean'],
    'holding_period_hours': 'mean'
})

print("\nExit Reason Analysis:")
print(exit_analysis)

# Win rate by exit reason
win_rate_by_exit = all_trades.groupby('exit_reason').apply(
    lambda x: (x['realized_pnl'] > 0).sum() / len(x) * 100
)
print("\nWin Rate by Exit Reason:")
print(win_rate_by_exit)
```

### Voter Component Analysis (Issue #444)

```python
from src.data_sources.database import AnalysisHistoryManager

manager = AnalysisHistoryManager()

# Analyze MACD vs RSI signal frequency
stats = manager.get_signal_statistics()

print("Voter Component Analysis:")
print(f"Total Analyses: {stats['total_analyses']}")
print(f"\nMACD Signals:")
for signal, count in stats['macd_counts'].items():
    print(f"  {signal}: {count} ({count/stats['total_analyses']*100:.1f}%)")

print(f"\nRSI Signals:")
for signal, count in stats['rsi_counts'].items():
    print(f"  {signal}: {count} ({count/stats['total_analyses']*100:.1f}%)")

# Find MACD/RSI disagreements for pattern analysis
all_analyses = manager.get_ml_training_data()
df = pd.DataFrame(all_analyses)

disagreements = df[df['macd_signal'] != df['rsi_signal']]
print(f"\nDisagreements: {len(disagreements)} ({len(disagreements)/len(df)*100:.1f}%)")
print(f"Most common disagreement: MACD={disagreements['macd_signal'].mode()[0]}, RSI={disagreements['rsi_signal'].mode()[0]}")

# Timeframe performance analysis
for timeframe in ['1d', '1h', '5m']:
    tf_stats = manager.get_signal_statistics()
    tf_data = [a for a in all_analyses if a['timeframe'] == timeframe]
    if tf_data:
        executed = len([a for a in tf_data if a['action_taken'] == 'executed'])
        print(f"\n{timeframe}: {len(tf_data)} analyses, {executed} executed ({executed/len(tf_data)*100:.1f}%)")
```

## Database Location

Trade history is stored in: `.cache/trading_cache.db`

Same database as options and market data for unified analytics.

## Future Enhancements

With this foundation, future features can include:

1. **TradingView-Style Dashboards**
   - Real-time equity curves
   - Drawdown visualization
   - Rolling performance metrics

2. **Advanced Analytics**
   - Monte Carlo simulations
   - Risk-adjusted returns (Sharpe, Sortino)
   - Trade correlation analysis

3. **Performance Attribution**
   - Strategy contribution to overall P&L
   - Symbol performance breakdown
   - Time-of-day/day-of-week analysis

4. **Execution Quality**
   - Slippage tracking
   - Fill rate analysis
   - Commission impact

5. **Reporting**
   - PDF performance reports
   - Email/Slack notifications
   - Automated trade journals

## Related Documentation

- [Issue #373](https://github.com/iAmGiG/OptionsCommander/issues/373): Multi-Provider Database Storage
- [Issue #444](https://github.com/iAmGiG/OptionsCommander/issues/444): Track Voter Component Details
- [sqlite_cache.py](../../src/data_sources/cache/sqlite_cache.py): Trade history database implementation
- [analysis_history_manager.py](../../src/data_sources/database/analysis_history_manager.py): Analysis history implementation
- [trade_lifecycle.py](../../src/trading/trade_lifecycle.py): TradeCycle integration
- [real_voter_strategy.py](../../src/strategies/real_voter_strategy.py): Auto-recording integration
