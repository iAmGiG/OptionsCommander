# OptionsCommander Project History & Evolution

> **Note (Oct 2026, #574):** the 0.856 Sharpe below is experiment 293's in-sample result and is superseded. With the live rules and timing, MACD+RSI voting has no edge over buy-and-hold; see the [live-rules baseline](../08_research/03_strategy_research/live_rules_baseline.md).

## Project Evolution

**OptionsCommander** (formerly RH2MAS, called AutoTrader in earlier docs) evolved from a research framework into a trading platform that runs on a paper account. This document chronicles the key decisions, experiments, and architectural shifts that shaped the current system.

## Phase 1: Initial Research Framework (Early Development)

### Foundation

- Built as an exploration of AI-driven trading strategies
- Started with simple technical indicators (MACD, RSI)
- Explored adding sentiment analysis to improve signal quality

### What We Learned

- Pure technical indicators often outperform complex sentiment analysis
- Infrastructure complexity should scale with proven benefits
- Backtesting framework essential for validation

---

## Phase 2: V0-V4 Sentiment Experiment (2024)

### Overview

Tested five iterations of the trading system with increasing complexity:

- **V0 (Baseline)**: Pure MACD strategy with fixed sentiment = 1.0
- **V1 (NLP)**: Added VADER sentiment analysis from Google Search news
- **V2 (Market Fear)**: Replaced news sentiment with VXX/VIX volatility-based sentiment
- **V3 (Hybrid)**: Combined V1 and V2 sentiment in weighted voting system
- **V4 (LLM)**: Used GPT-4o-mini reasoning with date obfuscation to prevent leakage

### Key Findings

| Metric | V0 (Baseline) | V1-V4 (Sentiment) | Winner |
|--------|---------------|-------------------|--------|
| Best Single Stock | NVDA +106.73% | Mixed results | V0 |
| Consistency | Reliable | Unpredictable | V0 |
| Infrastructure | Simple | Complex | V0 |
| API Cost | Low | High | V0 |
| Latency | Fast | Slow (LLM calls) | V0 |

**Conclusion**: The simpler baseline (V0) consistently outperformed complex sentiment variations. Sentiment did not provide sufficient alpha to justify infrastructure burden.

### Archived Results

- Full analysis: `docs/archived/v0_v4_deprecated/` (not in the repository)
- Detailed report: `V0-V4_Framework_Results.md` (not in the repository)
- Raw backtest data: not in the repository

---

## Phase 3: Voting System Development (2024-2025)

### Pivot Decision

**Result of V0-V4 Experiment**: Focus on proven approaches instead of experimental complexity.

The team shifted from trying to add sentiment analysis to improving the core technical approach:

### Voting System Design

Instead of sentiment-weighted signals, implement pure consensus voting:

1. **Component Signals**: MACD and RSI independently generate BUY/SELL/HOLD signals
2. **Consensus Logic**:
   - Both agree same direction → **STRONG signal** (full position size)
   - One signals, one neutral → **WEAK signal** (half position size)
   - Disagreement or both neutral → **HOLD** (no position)
3. **Parameter Optimization**: Fibonacci parameters (13/34/8 for MACD, 14/30/70 for RSI) validated across tech stocks

### Validation Results (Experiment #293)

**Performance Metrics**:

- Sharpe Ratio: **0.856** (excellent risk-adjusted returns)
- Total Return: **36.6%** (2024-2025 validation period)
- Win Rate: **51.4%**
- Max Drawdown: **-10.10%**

**Comparison to V0**: Voting (0.856 Sharpe) **outperforms** single MACD (0.841 Sharpe) with more consistent signals.

### Market Insights

- **Volatile Markets**: Better performance in high-volatility periods (-14.6% gap vs bull markets)
- **Fibonacci Parameters**: 13/34/8, chosen from tests on 7 tech stocks (no robust MACD configuration out-of-sample, #518)
- **Framework Agnostic**: Pure math, no ML/LLM dependencies, reproducible

### Architecture

- **File**: `src/autogen_agents/agents/voter_agent.py` (removed in #331; now [`VotingSignal`](../../src/trading/instruments/voting_signal.py))
- **Status**: ✅ In use (paper account)
- **Validation**: [`docs/archived/experiments/experiment_293_validation/`](../archived/experiments/experiment_293_validation/)

---

## Phase 4: Fibonacci Regime Detection (Exploratory - CLOSED)

### Context

After validating the voting system, explored whether market regime detection could further improve performance:

- **Fibonacci Regime Detection**: Analyze market structure using Fibonacci levels
- **Regime-Weighted Signals**: Adjust voting strategy based on detected market regime
- **Multi-Timeframe Analysis**: Validate patterns across different timeframes

### Decision: Closed as Out of Scope

**Issues #297-301**: Fibonacci regime exploration closed as:

1. Voting system already provides good risk-adjusted returns
2. Over-engineering relative to proven benefits
3. Adds complexity without clear performance improvement
4. Voting system sufficient for production deployment

**Outcome**: Focused all efforts on completing the voting system as-is and preparing for multi-agent architecture.

---

## Phase 5: CLI & Automation (2024-2025)

### Key Features Implemented

- ✅ Interactive REPL-based CLI with natural language interface
- ✅ Human-in-loop trading approval system
- ✅ GTC daily scheduler with retry logic
- ✅ Multi-account management
- ✅ Position tracking and alerts
- ✅ Forward testing protocol (30-day validation)
- ✅ Timeframe support (1m through 1M)

### Architecture Milestone: Issue #287

Implemented "set it and forget it" daily trading system:

- Morning routine (9:20 AM ET): Position reconciliation + alerts
- Evening routine (3:50 PM ET): Performance review
- Exponential backoff retry logic with 10% jitter
- 90% API call reduction through GTC orders

### File Structure

```text
src/
├── human_interface/          # Interactive CLI components
│   ├── cli_session.py        # Main REPL loop (3024 lines - under refactoring)
│   └── message_handlers.py   # Intent-based request processing
├── cli/
│   ├── account_commands.py   # Account management
│   ├── timeframe_commands.py # Timeframe control
│   ├── scheduler_commands.py # Scheduler management
│   └── tools/                # Modular FunctionTool infrastructure
│       ├── __init__.py       # Tool registry (#455 Phase 1A-1B)
│       ├── mode_tools.py     # Mode/timeframe tools (Phase 1B COMPLETE)
│       ├── portfolio_tools.py # Portfolio display (Phase 1C IN DEVELOPMENT)
│       └── ...
└── trading/
    ├── daily_scheduler.py    # GTC scheduler daemon
    ├── account_manager.py    # Multi-account tracking
    └── trailing_stop_manager.py  # Progressive stop management
```

---

## Phase 6: Code Quality Refactoring (Current - Issue #433, #436)

### Epic: Modularize cli_session.py

**Context**: `cli_session.py` grew to 3,024 lines, making it hard to maintain and test.

**Approach**: Extract functionality into focused, type-hinted FunctionTool modules:

### Refactoring Plan (5 Sub-Issues)

| Phase | Issue | Status | Component | Lines |
|-------|-------|--------|-----------|-------|
| 1A | #455 | ✅ COMPLETE | FunctionTool infrastructure + registry | 250 |
| 1B | #456 | ✅ COMPLETE | Mode/timeframe tools | 200 |
| 1C | #457 | 🚧 IN PROGRESS | Portfolio/account display tools | 340 |
| 1D | #458 | 🚧 PLANNED | Order/scheduler/alert tools | 300 |
| 1E | #459 | 🚧 PLANNED | Final integration & cleanup | TBD |

**Expected Outcome**: cli_session.py reduced from 3,024 → ~500 lines, with most functionality extracted into reusable tool modules.

**Pattern Benefits**:

- Type hints enable AutoGen schema auto-generation
- Pure functions easier to test and mock
- Tools reusable by AutoGen agents
- Clear separation of concerns

### Reference

- Breakdown: [Issue #433 Initial Analysis](https://github.com/iAmGiG/AutoGen-TradingSystem/issues/433)
- Epic: [Issue #436 Code Quality Refactoring](https://github.com/iAmGiG/AutoGen-TradingSystem/issues/436)
- Progress: [`docs/04_development/02_project_status.md`](../04_development/02_project_status.md)

---

## Phase 7: Multi-Agent System (Planned)

### Vision

Extend the voting system with specialized agents for:

1. **ScannerAgent**: Market opportunity identification
2. **RiskAgent**: Position sizing and portfolio risk
3. **ExecutorAgent**: Order management and execution
4. **TradingOrchestrator**: Multi-agent coordination

### Current Status

- VoterAgent: ✅ **In use on paper** (no backtested edge at live timing, #574; the 0.856 Sharpe was in-sample)
- Others: 🚧 **Placeholder implementations** (scaffolding in place)

### Architecture

Built using **Microsoft AutoGen framework** for:

- Structured agent communication
- Tool integration and sharing
- Message-based coordination
- Framework-agnostic agent logic

---

## Key Decisions & Rationale

### Decision 1: Pure Math Over Sentiment (2024)

**Choice**: MACD+RSI voting instead of LLM sentiment analysis
**Evidence**: V0-V4 experiment showed sentiment added complexity without ROI
**Result**: Faster, cheaper, more reproducible, better validation

### Decision 2: Voting Over Single Indicator (2024-2025)

**Choice**: Consensus voting instead of single MACD strategy
**Evidence**: Experiment #293 showed 0.856 vs 0.841 Sharpe ratio improvement
**Result**: More robust signals with similar infrastructure cost

### Decision 3: FunctionTool Modularization (2025)

**Choice**: Extract CLI functionality into typed tool functions
**Evidence**: 3,024-line monolith hard to maintain and test
**Result**: Reusable, testable, agent-compatible modules

### Decision 4: Human-in-Loop Model (2024-2025)

**Choice**: System assists humans, not autonomous trading
**Evidence**: Regulatory, safety, and validation best practices
**Result**: Approval workflows, clear decision audit trail

### Decision 5: YAML Configuration (2025)

**Choice**: YAML for configs instead of JSON
**Evidence**: Better readability, industry standard
**Result**: Easier configuration management without code changes

---

## Architecture Milestones

### Current Production Architecture (AgentEdge)

```text
┌─────────────────────────────────────────────────────────────┐
│                    Interactive CLI (REPL)                    │
│          Natural Language Intent-Based Interface            │
└─────────────────────────────────────────────────────────────┘
                              ↓
┌─────────────────────────────────────────────────────────────┐
│                  Message Handler Layer                       │
│          (Intent → Tool Selection → Execution)              │
└─────────────────────────────────────────────────────────────┘
                              ↓
┌─────────────────────────────────────────────────────────────┐
│              CLI FunctionTool Infrastructure                 │
│  ✅ Mode/Timeframe Tools (#455, #456 - COMPLETE)           │
│  🚧 Portfolio/Account/Order/Scheduler Tools (#457-459)      │
└─────────────────────────────────────────────────────────────┘
                              ↓
┌─────────────────────────────────────────────────────────────┐
│                   AutoGen Agents (Planned)                   │
│  ✅ VoterAgent (MACD+RSI) - In use (paper)                 │
│  🚧 ScannerAgent, RiskAgent, ExecutorAgent                  │
│  🚧 TradingOrchestrator (Multi-Agent Coordination)          │
└─────────────────────────────────────────────────────────────┘
                              ↓
┌──────────────┬──────────────┬──────────────┬────────────────┐
│   Alpaca     │  Polygon.io  │  Alpha Vantage  │  Cache DB    │
│   Trading    │  Market Data │  Fallback Data  │  (SQLite)    │
└──────────────┴──────────────┴──────────────┴────────────────┘
```

---

## What Changed, What Stayed

### What We Kept

- ✅ **MACD+RSI Analysis**: Core indicators from V0, validated through voting system
- ✅ **Backtesting Framework**: Systematic testing approach evolved from V0-V4
- ✅ **Cache Infrastructure**: Market data caching system reused across systems
- ✅ **Modular Design**: Separation of concerns maintained

### What We Deprecated

- ❌ **Sentiment Analysis**: V1-V4 sentiment approaches (archived)
- ❌ **Fibonacci Regime Detection**: Exploratory phase (closed as out of scope)
- ❌ **Script-Based Commands**: Moved to intent-based REPL interface
- ❌ **JSON Configuration**: Migrated to YAML

### What We Added

- ✅ **Voting Consensus**: Multi-indicator agreement logic
- ✅ **Interactive CLI**: Human-in-loop trading interface
- ✅ **GTC Scheduler**: Automated daily execution system
- ✅ **Multi-Agent Framework**: AutoGen-based extensible architecture
- ✅ **FunctionTool Registry**: Reusable, testable tool infrastructure
- ✅ **Forward Testing**: 30-day validation protocol before deployment

---

## Lessons Learned

1. **Validate Before Over-Engineering**: The V0-V4 experiment prevented months of wasted effort on sentiment analysis
2. **Simplicity Scales**: Adding consensus voting improved results without proportional complexity increase
3. **Modularity Enables Growth**: FunctionTool architecture makes it easy to add new agents
4. **Human Oversight Critical**: Trading requires human judgment; automation should support not replace it
5. **Clear Validation Metrics**: Defined KPIs (Sharpe ratio, max drawdown, win rate) critical for evaluation

---

## Timeline

| Date | Milestone | Status |
|------|-----------|--------|
| 2024-Q1 | Research framework initial version | ✅ Completed |
| 2024-Q2-Q3 | V0-V4 sentiment experiments | ✅ Completed, Archived |
| 2024-Q4 | VoterAgent validation (#293) | ✅ Completed |
| 2025-01-02 | GTC daily scheduler (#287) | ✅ Completed |
| 2025-02-11 | Human-in-loop CLI (#308) | ✅ Completed |
| 2025-10-23 | main.py CLI completed (#327) | ✅ Completed |
| 2025-11-11 | Scheduler CLI + position tracking | ✅ Completed |
| 2025-12-02 | FunctionTool infrastructure (#455-456) | ✅ Completed |
| 2025-12-? | Portfolio display tools (#457) | 🚧 In Progress |
| 2025-12-? | Order/Scheduler/Alert tools (#458-459) | 🚧 Planned |
| 2026-Q1 | ScannerAgent multi-agent system | 🚧 Planned |

---

## Accessing Historical Information

### V0-V4 Sentiment Framework

- Location: `docs/archived/v0_v4_deprecated/` (not in the repository)
- Detailed Results: `V0-V4_Framework_Results.md` (not in the repository)

### VoterAgent Validation (Experiment #293)

- Location: [`docs/archived/experiments/experiment_293_validation/`](../archived/experiments/experiment_293_validation/)
- Validation: Results at 0.856 Sharpe ratio, 36.6% return

### Fibonacci Regime Exploration (Closed Issues #297-301)

- Status: Closed as out of scope, no public documentation
- Decision: Focus on voting system instead

### Current Development

- Epic: [Issue #433 - Refactor cli_session.py](https://github.com/iAmGiG/AutoGen-TradingSystem/issues/433)
- Status: [Project Status](../04_development/02_project_status.md)

---

**Last Updated**: December 2, 2025
**Maintained By**: AutoGen-Trader Development Team
