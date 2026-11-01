# OptionsCommander Overview

**Status**: Trading platform; the MACD+RSI VotingSignal runs on a paper account
**Foundation**: Microsoft Agent Framework (AutoGen until #606)
**Backtested Performance**: Sharpe 0.69 at 5 bps a side with the live rules, no edge over buy-and-hold ([live-rules baseline](../08_research/03_strategy_research/live_rules_baseline.md))

## What Is This System?

OptionsCommander (formerly RH2MAS; GitHub repository `iAmGiG/OptionsCommander`, formerly AutoTrader-AgentEdge) is an algorithmic trading platform whose agents run on Microsoft Agent Framework (they ran on AutoGen until #606). It uses ensemble voting (MACD+RSI), an approach motivated by academic research; backtested with the live rules and timing, it has no edge over buy-and-hold of the same symbols (#574).

### Core Purpose

This is a **human-guided trade management system**, not an autonomous trading bot:

- **Human decides**: External trade signals and opportunities from trader network
- **System provides**: Entry timing via MACD+RSI voting, risk management, execution
- **System manages**: Position monitoring, stop management, exit strategies
- **Goal**: Beat savings account returns (2-5% APY) with controlled risk

### Performance Benchmark

- **Comparison**: Savings account rates (2-5% APY), NOT buy-and-hold
- **Target**: 8-12% annual return = SUCCESS
- **Focus**: Risk-adjusted returns over raw returns

## System Capabilities

### ✅ Components in Use

**VotingSignal** - MACD+RSI voting, in use on the paper account (no backtested edge over buy-and-hold):

- Backtested Sharpe Ratio: 0.69 at 5 bps a side (live rules, 2009 to 2026-10-09)
- Win Rate: 44.2%
- Max Drawdown: -6.9%
- Configuration: Fibonacci MACD (13/34/8) + RSI(14)

**Position Manager** - Unified position and account tracking:

- Real-time account monitoring
- Position state management
- Integration with all agents via tools

**Order Manager** - Complete order placement and monitoring:

- Order types: Market, limit, stop, trailing, bracket
- Risk management: Market hours, daily limits, position validation
- Fill monitoring with automatic state transitions

**Trading Cycle** - Comprehensive position monitoring:

- Entry signal detection
- Position tracking during hold
- Stop management and exit execution

### ✅ Integration Complete

**Alpaca Markets**:

- Full paper and live trading support
- Real-time and historical market data
- Order execution and monitoring
- Account and position management

**Market Data**:

- Real-time data via Alpaca SDK
- Intelligent caching (>90% API call reduction)
- Multi-provider normalization (Alpha Vantage fallback)

**Order Types**:

- Market orders (immediate execution)
- Limit orders (price-specific)
- Stop orders (risk management)
- Trailing stops (profit protection)
- Bracket orders (OCO with stops)

### 🔄 In Development

**Multi-Agent Ecosystem** (Issue #310):

1. **Scanner Agent**: Multi-ticker market scanning
2. **Risk Agent**: Portfolio risk management
3. **Executor Agent**: Trade execution coordination
4. **Human Interface**: Decision presentation and CLI

## Architecture Highlights

### Multi-Agent Framework

The agents use Microsoft Agent Framework for LLM calls and tool calling:

- **Agent Communication**: Structured message passing between agents
- **Unified Tools**: Single source of truth accessible to all agents
- **Error Handling**: Comprehensive error recovery and logging
- **State Management**: Persistent trade state across restarts

### Layer Separation

Clean architectural boundaries:

1. **Trading Domain** (`src/trading/`): All trading operations (broker API, orders, positions, risk, scheduling, etc.)
   - **broker/**: Alpaca API integration and execution
   - **orders/**: Order lifecycle management
   - **positions/**: Position tracking and sizing
   - **risk/**: Risk management and checks
   - **state/**: State reconciliation
   - **scheduling/**: Trading cycles and automation
   - **accounts/**: Multi-account management
   - **instruments/**: Tickers, timeframes, indicators
   - **utils/**: Trading utilities
2. **Data Layer** (`src/market_data/`, `src/cache/`): Market data acquisition, caching and normalization
3. **CLI Layer** (`src/cli/`): Human-in-loop interfaces: slash commands and typed requests, on plain functions in `src/cli/tools/`

### Key Design Principles

1. **Separation of Concerns**: Integration ≠ Business Logic ≠ Decisions
2. **Unified Architecture**: Single codebase for paper and live trading
3. **Production Safety**: Multi-level confirmations, comprehensive validation
4. **Agent-Ready Design**: Agent Framework tool wrappers for all functionality

## Backtested Performance

### Experiment #293 Results

In experiment 293, the 2-way voting strategy (MACD + RSI) beat single indicators:

| Metric | MACD-Only | Voting (MACD+RSI) | Winner |
|--------|-----------|-------------------|--------|
| **Sharpe Ratio** | 0.841 | **0.856** | **Voting** |
| **Max Drawdown** | -10.58% | **-10.10%** | **Voting** |
| **Win Rate** | 31.9% | **51.4%** | **Voting** |
| **Volatility** | 16.58% | **15.30%** | **Voting** |

> **Superseded (Oct 2026, #574):** these are experiment 293's in-sample results (AAPL 2024, MACD 12/26/9, filled at the signal's own close, no costs). Backtested with the live rules and timing on the 19-symbol watchlist from 2009, MACD+RSI voting has a Sharpe of 0.69 at 5 bps a side and no alpha over buy-and-hold of the same symbols; see the [live-rules baseline](../08_research/03_strategy_research/live_rules_baseline.md).

### Extended Period Performance (2024-2025)

An earlier test, also superseded by the live-rules baseline:

- **Period**: 417 trading days
- **Sharpe Ratio**: 0.771
- **Total Return**: +36.6%
- **Key Finding**: Voting performs relatively better in volatile markets (11.2% improvement vs bull markets)

### System Reliability

- **Test Coverage**: 35/35 tests passing
- **Cache Performance**: >90% API call reduction
- **Error Recovery**: Comprehensive validation and recovery

## Research Foundation

The system architecture is grounded in peer-reviewed research:

1. **Ensemble Methods**: 70-90% accuracy potential (vs 45-65% single indicators)
2. **Multi-Indicator Integration**: 15% win rate improvement through signal confirmation
3. **Regime Adaptation**: 32% drawdown reduction via dynamic strategy adjustment
4. **LLM Trading Behavior**: Bias mitigation through regime-aware approaches

**Academic Support**:

- Machine Learning Ensemble Methods for Stock Market Forecasting (2020)
- Enhanced Trading Performance through Multi-Indicator Signal Integration (2024)
- Hierarchical Multi-Agent Trading with Dynamic Strategy Selection (2023)
- FINSABER: LLM Trading Behavior Analysis (2024)

See the [research documentation](../08_research/README.md) for what this project tested and stopped.

## Technology Stack

**Core Framework**:

- Microsoft Agent Framework (agents, tool calling)
- Python 3.12+ (primary language; CI also runs 3.14 and 3.15)

**Trading Integration**:

- Alpaca Markets API (broker integration)
- alpaca-py SDK (official Python SDK)

**Data & Analysis**:

- pandas (data manipulation)
- NumPy (numerical computations)
- TA-Lib / custom indicators (technical analysis)

**Caching & Performance**:

- TradingCacheManager (SQLite: 8-10x performance, 90%+ hit rate)
- ACID transactions with thread-safe concurrent access
- Smart expiration: Historical data (10 year TTL), Recent data (24 hour TTL)

## Current Development Status

### Phase 1-2: Complete ✅

- Core voting architecture implemented
- MACD+RSI implemented and tested (no edge at live timing, #574)
- One signal (VotingSignal) in use on paper

### Phase 3: In Progress 🚧

- Expanded ensemble with additional indicators (RSI, Bollinger Bands, Volume)
- Multi-agent coordination framework
- Scanner and Risk agents

### Phase 4-5: Planned 📋

- Weighted voting and confidence scoring
- Market regime detection and adaptation
- Production deployment with live trading

## Next Steps

For detailed information about specific aspects of the system:

- **How It Works**: See `02_phases_of_operation.md` for operational flow
- **Why It Exists**: See `03_system_context.md` for development philosophy
- **Technical Design**: See `../02_architecture/` for detailed architecture
- **Backtested Results**: See `../08_research/03_strategy_research/live_rules_baseline.md` (experiment 293 in `../03_reference/01_validation_results.md` is superseded)

---

*Multi-agent trading platform with ensemble voting and safety controls.*
