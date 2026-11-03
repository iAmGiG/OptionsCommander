# Codebase Structure

**Purpose**: Developer guide to file organization and component layout
**Last Updated**: November 2026 (the agents were retired in #331)
**Status**: Paper account; scheduled routines manage stops, the CLI places orders on request

## Project Structure

```bash
OptionsCommander/                     # repository root
├── main.py                           # CLI entry point (interactive, --run-due, --daemon)
├── src/
│   ├── trading/                      # Trading: broker, orders, positions, state, scheduling
│   │   ├── scheduling/               # daily_scheduler.py, trading_cycle.py, scheduler_state.py, notices.py, watchdog.py
│   │   ├── state/                    # state_reconciler.py, broker_state_cache.py, daily_records.py, local_state_manager.py
│   │   ├── orders/                   # trailing_stop_manager.py, partial_exit_manager.py, multi_level_targets.py, dry_run.py
│   │   ├── positions/                # position_manager.py, position_tracker.py, position_sizer.py, portfolio_manager.py
│   │   ├── broker/                   # alpaca_trading_client.py, alpaca_execution_manager.py, advanced_orders.py, market_hours.py
│   │   ├── gtt/                      # gtt_manager.py, trigger_evaluator.py, action_executor.py, trailing_stop_bridge.py
│   │   ├── instruments/              # voting_signal.py, multi_timeframe_voter.py, scanner_config.py, entry_planning.py, indicators.py
│   │   ├── risk/                     # simple_risk_manager.py, risk_calculator.py, broker_account_service.py
│   │   ├── accounts/                 # account_manager.py, account_tools.py
│   │   ├── utils/                    # simple_signals.py, unified_price_fetcher.py, report_generator.py
│   │   └── alerts_watchlists.py      # Watchlists
│   │
│   ├── cli/                          # Interactive CLI
│   │   ├── cli_session.py            # The session loop
│   │   ├── commands/                 # Slash commands (slash_commands.py, registry.py, records_commands.py, voter_commands.py, ...)
│   │   ├── tools/                    # Plain functions behind the slash commands and typed requests
│   │   ├── handlers/                 # trade_handler.py
│   │   ├── scheduler/                # Scheduler CLI pieces (monitor, setup wizard, config editor, daemon manager)
│   │   └── utils/                    # help_system.py, faq.py, input_parser.py, intent_classifier.py, ...
│   │
│   ├── core/                         # Request pipeline: factory.py, trading_orchestrator.py, models.py, trading_modes.py, ranked_voter_config.py, interfaces/
│   ├── strategies/                   # real_voter_strategy.py (runs VotingSignal for CLI requests), voter_strategy.py (stub)
│   ├── parsers/                      # llm_parser.py (LLMParser): the only user of Microsoft Agent Framework
│   ├── market_data/                  # alpaca_market_data.py, alpha_vantage_market.py, unified_market_tool.py, options_chain.py
│   ├── cache/                        # SQLite market-data and broker cache (sqlite_cache.py, cache_adapter.py, unified_broker_cache.py, ...)
│   ├── database/                     # analysis_history_manager.py
│   ├── backtesting/                  # Research backtester, GEX overlay, live_rules_sim.py, signals/
│   └── utils/                        # state_paths.py, market_hours.py (NYSE calendar), date_utils.py, config_loader.py, db_backup.py
│
├── config/config.json                # API keys and secrets (gitignored)
├── config_defaults/                  # Tracked defaults: trading_config.yaml, scheduler_config.yaml, trading_modes.yaml,
│                                     #   scanner_config.yaml, voters_config.yaml, help_commands.yaml, faq.yaml, ...
├── scripts/                          # research/ (GEX and strategy runners), utilities/, analysis/, deployment/
├── tests/                            # unit/, integration/ (gitignored; tracked files are force-added)
├── docs/                             # Documentation (01_overview to 09_future, archived)
├── state/                            # user.db, notices.json, scheduler.log, backups/ (runtime, not tracked)
├── reports/daily/                    # Routine reports (<ET date>_<routine>.md)
├── pyproject.toml
└── README.md
```

## Directory Responsibilities

### src/trading/ - Unified Trading Domain

**Purpose**: All trading operations organized by concern

#### broker/ - Broker Integration

- `alpaca_trading_client.py`: Alpaca API integration (`AlpacaAccountMonitor`, `AlpacaOrderManager`); the account is paper
- `alpaca_execution_manager.py`: AlpacaExecutionManager implementing the ExecutionManager interface; `plan_close` and `close_position` sell a position, cancelling the stop that holds the shares first
- `validators/`: Order validation, response parsing, error handling

#### orders/ - Order Lifecycle

- `trailing_stop_manager.py`: Progressive trailing stop logic
- `partial_exit_manager.py`: Partial position exit handling
- `multi_level_targets.py`: Multi-level exit targets

#### positions/ - Position Management

- `position_manager.py`: Position tracking (broker as source of truth)
- `position_sizer.py`: Profile-based position sizing
- `position_tracker.py`: Exit alerts and TP/SL monitoring
- `portfolio_manager.py`: Portfolio risk & allocation management

#### risk/ - Risk Management

- `simple_risk_manager.py`: Pre-trade risk checks (buying power, position %)
- `risk_calculator.py`: Portfolio risk metrics and position sizing

#### state/ - State Management

- `state_reconciler.py`: Broker-to-local state synchronization, stop checks, repairs, raises
- `broker_state_cache.py`: Broker state caching layer
- `local_state_manager.py`: Local JSON state persistence
- `daily_records.py`: Daily account, position and fill records (`state/user.db`)

#### scheduling/ - Trading Cycles

- `trading_cycle.py`: Cost-efficient daily routines (morning/evening)
- `daily_scheduler.py`: Runs the routines on NYSE trading days
- `scheduler_state.py`, `notices.py`, `watchdog.py`: Saved flags, notices and stuck-routine checks

#### accounts/ - Multi-Account Management

- `account_manager.py`: Multi-account support with API-first discovery
- `account_tools.py`: Account helper functions

#### instruments/ - Signals, Tickers and Timeframes

- `voting_signal.py`: `VotingSignal`, the MACD+RSI vote
- `multi_timeframe_voter.py`, `scanner_config.py`: Multi-timeframe voting and the scanner config
- `entry_planning.py`: Entry, stop and target planning from daily bars
- `ticker_database.py`, `approved_tickers.py`: Ticker metadata and per-ticker limits
- `indicators.py`, `data_fetch.py`: Indicator calculations and market data retrieval

#### utils/ - Trading Utilities

- `unified_price_fetcher.py`: Current price fetching with fallback
- `simple_signals.py`: Signal generation helpers (`get_simple_signal`)
- `report_generator.py`: Morning/evening report formatting

### src/cache/ and src/market_data/ - Data Integration

**Purpose**: Market data acquisition and caching

- `src/cache/sqlite_cache.py`: TradingCacheManager (SQLite); `cache_adapter.py` and `unified_broker_cache.py` are built on first use
- `src/market_data/`: Alpaca (`alpaca_market_data.py`) and Alpha Vantage (`alpha_vantage_market.py`) behind `unified_market_tool.py`

### src/utils/ - Utility Functions

**Purpose**: General-purpose helper functions

- `state_paths.py`: Where `state/user.db` lives (`$AUTOTRADER_STATE_DIR` overrides it)
- `market_hours.py`: NYSE calendar
- `date_utils.py`: Date/time processing and timezone handling

## Key File Descriptions

### main.py - Unified Entry Point

**Commands**:

```bash
python main.py                         # Interactive CLI (/help lists the commands)
python main.py --run-due               # Run a routine if one is due now (what cron runs)
python main.py --daemon                # Daily scheduler in the background
python main.py --list-accounts         # List configured trading accounts
python main.py --account ID            # Select a trading account
```

### src/trading/instruments/voting_signal.py

The MACD+RSI vote the CLI's `RealVoterStrategy` runs. Plain Python, no LLM.

### src/trading/broker/alpaca_trading_client.py

**Unified Paper/Live Trading**:

**Classes**:

- `AlpacaTradingClient`: Base client with mode selection
- `AlpacaAccountMonitor`: Account status and positions
- `AlpacaOrderManager`: Complete order lifecycle

**Safety Features**:

- Explicit paper/live mode selection
- Live trading confirmations required
- Error handling and logging

### src/trading/scheduling/trading_cycle.py

**Cost-Efficient Operations**:

**Daily Routines**:

- Morning (9:20 AM ET): Fetch broker state, record, reconcile, re-place stops, report
- Evening (3:50 PM ET): the same, plus raising stops to the trailing level (#649)

**Efficiency**:

- ~10-15 API calls/day (vs 100+ reactive systems)
- 90%+ cost reduction
- GTC orders minimize monitoring

## Data Flow Architecture

```bash
┌────────────────────────────────────┐
│  Market Data Acquisition            │
│  (Alpaca → Alpha Vantage)           │
└────────────┬───────────────────────┘
             ↓
┌────────────────────────────────────┐
│  SQLite Cache (8-10x perf, 90%+ hit)│
└────────────┬───────────────────────┘
             ↓
┌────────────────────────────────────┐
│  VotingSignal Decision              │
│  ├─ MACD calculation (13/34/8)      │
│  ├─ RSI calculation (14/30/70)      │
│  └─ Voting consensus                │
└────────────┬───────────────────────┘
             ↓
┌────────────────────────────────────┐
│  Risk Management                    │
│  ├─ Position sizing                 │
│  ├─ Portfolio limits                │
│  └─ Confidence threshold            │
└────────────┬───────────────────────┘
             ↓
┌────────────────────────────────────┐
│  Order Execution (Alpaca)           │
│  ├─ Market/bracket orders           │
│  ├─ Stop/trailing stops             │
│  └─ Fill monitoring                 │
└────────────┬───────────────────────┘
             ↓
┌────────────────────────────────────┐
│  Position Tracking                  │
│  ├─ Broker state reconciliation     │
│  ├─ Progressive stop adjustment     │
│  └─ State persistence               │
└────────────────────────────────────┘
```

## Configuration Management

### API Keys (config/config.json)

**Gitignored** - Never committed to version control

```json
{
  "ALPHA_VANTAGE_KEY": "...",
  "OPENAI_API_KEY": "sk-...",
  "ALPACA_PAPER_API_KEY": "...",
  "ALPACA_PAPER_SECRET": "...",
  "ALPACA_LIVE_API_KEY": "...",
  "ALPACA_LIVE_SECRET": "..."
}
```

### Trading Parameters (config_defaults/)

**Version-controlled** - Default trading parameters in YAML format

#### trading_config.yaml

```yaml
strategy_parameters:
  macd:
    fast: 13
    slow: 34
    signal: 8
  rsi:
    period: 14
    oversold: 30
    overbought: 70
  exits:
    balanced:
      take_profit: 0.08
      stop_loss: 0.05
```

#### market_hours.yaml

```yaml
timezone: "America/New_York"
regular_hours:
  open_hour: 9
  open_minute: 30
  close_hour: 16
  close_minute: 0
weekend:
  saturday: 5
  sunday: 6
holidays:
  enabled: false
  # NYSE 2025 holiday calendar
```

#### cli_messages.yaml

```yaml
execution:
  init_with_manager: "AlpacaExecutionManager initialized with OrderManager"
  market_closed_warning: "Market is CLOSED (weekend/off-hours)..."
  sell_no_position: "SELL signal rejected: No position in {ticker}..."
  # 50+ message templates for user-facing output
```

## Testing Structure

### Test Organization

```bash
tests/
├── conftest.py                       # Sets the cache folder to a temp dir for every test
├── module_sandbox.py                 # Restores sys.modules and get_config() after stubs
├── unit/                             # Unit tests (conftest.py sets AUTOTRADER_STATE_DIR to a temp dir)
└── integration/                      # Integration tests (-m db needs a local PostgreSQL)
```

`tests/` is gitignored; add new test files with `git add -f`. Read the test-safety notes in [the testing README](../07_testing/README.md) before a full run.

### Running the Tests

```bash
python -m pytest tests/ -m "not db"    # what CI runs (no database, no credentials)
python -m pytest tests/ -m "db"        # database tests (read-only, needs a local PostgreSQL)
```

## Dependencies

### Core Framework

```text
agent-framework-core>=1.23,<2     # Microsoft Agent Framework: the natural-language parser only (#606)
agent-framework-openai>=1.17,<2   # OpenAI chat clients; brings openai
```

### Trading & Data

```text
alpaca-py>=0.43.2             # Alpaca trading
pandas>=2.2.0,<4              # Data manipulation (pandas 3 supported)
numpy>=2.0.0                  # Numerical computing
```

### AI & LLM

```text
openai                        # LLM integration (installed by agent-framework-openai)
```

## Architecture Principles

### 1. Separation of Concerns

- **Signal**: `VotingSignal`, the MACD+RSI vote (pure functions, no LLM)
- **Tools**: Plain functions in `src/cli/tools/` behind the slash commands
- **Trading**: Broker integration and execution
- **Data Sources**: Data acquisition and caching

### 2. Broker as Source of Truth

Always reconcile local state with broker:

1. Fetch broker state (single API call)
2. Compare with local JSON state
3. Update local state
4. Execute any needed changes

### 3. Cost Efficiency

- GTC orders reduce monitoring
- Batch data fetches
- SQLite caching (8-10x faster, 90%+ hit rate)
- ~10-15 API calls/day vs 100+ reactive

### 4. Extensibility

**Adding a slash command**: register a function with `@command("/name", help_text=...)` in `src/cli/commands/slash_commands.py`, put the logic in a plain function in `src/cli/tools/`, and add its entry to `config_defaults/help_commands.yaml`.

**Adding a signal**: write a plain class or function under `src/trading/instruments/` and call it from a strategy such as `src/strategies/real_voter_strategy.py`.

---

*Comprehensive guide to codebase organization for contributors and developers.*
