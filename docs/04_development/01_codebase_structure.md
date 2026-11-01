# Codebase Structure

**Purpose**: Developer guide to file organization and component layout
**Last Updated**: November 2025
**Status**: Production VoterAgent with multi-agent framework in development

## Project Structure

```bash
OptionsCommander/                     # repository root
├── src/                              # Core implementation
│   │
│   ├── execution/                   # Order execution layer
│   │   └── alpaca_execution_manager.py # AlpacaExecutionManager (plugin architecture)
│   │
│   ├── trading/                     # Trading execution & lifecycle
│   │   ├── alpaca_trading_client.py # Alpaca API integration
│   │   ├── order_manager.py         # Order placement & tracking
│   │   ├── position_manager.py      # Position management
│   │   ├── trading_cycle.py         # Cost-efficient trade cycle
│   │   ├── market_scanner.py        # Market opportunity detection
│   │   ├── simple_signals.py        # Signal generation
│   │   └── unified_state_manager.py # State persistence
│   │
│   ├── trading_tools/               # Technical analysis (pure functions)
│   │   ├── indicators.py            # MACD & RSI calculations
│   │   ├── data_fetch.py            # Market data retrieval
│   │   └── risk_calculator.py       # Risk metrics
│   │
│   ├── data_sources/                # Data integration layer
│   │   ├── tools.py                 # Unified tool configuration
│   │   ├── cache/                   # SQLite caching system (8-10x improvement)
│   │   │   ├── sqlite_cache.py      # TradingCacheManager (production)
│   │   │   ├── cache_adapter.py     # Cache router (backward compatibility)
│   │   │   ├── unified_cache.py     # Deprecated file-based cache
│   │   │   └── market_data_cache.py # Deprecated legacy cache
│   │   │
│   │   ├── sources/market/          # Market data providers
│   │   │   ├── alpaca_market_data.py      # Primary real-time data
│   │   │   ├── unified_market_tool.py     # Unified interface
│   │   │   └── alpha_vantage_market.py    # Fallback source
│   │   │
│   │   └── processors/              # Data processing
│   │       ├── data_normalizer.py   # Format standardization
│   │       └── sentiment_analyzer.py # News sentiment (deprecated)
│   │
│   ├── voting/                      # Voting strategy system
│   │   ├── base_voting_strategy.py  # Base voting framework
│   │   └── basic_voting_strategy.py # MACD+RSI voting logic
│   │
│   ├── core/indicators/             # Indicator library
│   │   ├── base_indicator.py        # Base indicator class
│   │   ├── simple_rsi.py            # RSI implementation
│   │   └── indicator_library.py     # Indicator collection
│   │
│   ├── human_interface/             # CLI & decision formatting
│   │   ├── cli_interface.py         # Command-line interface
│   │   └── decision_formatter.py    # Output formatting
│   │
│   └── utils/                       # Utility functions
│       ├── date_utils.py            # Date/time utilities
│       ├── agent_utils.py           # Agent helpers
│       └── output_manager.py        # Result organization
│
├── config/                          # API configuration (gitignored)
│   └── config.json                  # API keys and secrets
│
├── config_defaults/                 # Default configurations (Issue #358)
│   ├── README.md                    # Configuration system documentation
│   ├── trading_config.yaml          # Trading strategy & risk parameters
│   ├── scanner_config.yaml          # Market scanner watchlist & settings
│   ├── paths_config.yaml            # File paths & directory structure
│   ├── market_hours.yaml            # Market hours & holidays
│   ├── cli_messages.yaml            # User-facing message templates
│   ├── message_loader.py            # Message template loader
│   └── safe_print.py                # Platform-aware output utilities
│
│
├── scripts/                         # Utility scripts
│   ├── experiments/                 # Backtesting experiments
│   │   ├── experiment_293_validation/ # VoterAgent validation
│   │   └── configuration_system/      # Parameter testing
│   ├── analysis/                    # Results analysis
│   └── data/                        # Data collection
│
├── tests/                           # Test suite
│   ├── unit/                        # Unit tests
│   ├── tools/                       # Tool integration tests
│   ├── test_alpaca_basic.py         # Alpaca connection tests
│   ├── test_alpaca_market_data.py   # Market data tests
│   ├── test_alpaca_order_manager.py # Order management tests
│   └── test_alpaca_advanced_orders.py # Advanced order tests
│
├── docs/                            # Documentation
│   ├── 01_overview/                 # System overview
│   ├── 02_architecture/             # Technical design
│   ├── 03_reference/                # Reference docs
│   └── 04_development/              # Developer guides
│
├── state/                           # JSON state management
│   ├── positions.json               # Position tracking
│   └── orders.json                  # Order history
│
├── reports/                         # Generated reports
│   ├── active/voting_strategy/      # Current experiments
│   └── README.md                    # Reports index
│
├── main.py                          # Unified CLI entry point
├── pyproject.toml                   # Project configuration
├── requirements.txt                 # Python dependencies
└── README.md                        # Project overview
```

## Directory Responsibilities

### src/trading/ - Unified Trading Domain

**Purpose**: All trading operations organized by concern (consolidated from src/execution/, src/risk/, src/trading_tools/)

**Structure** (9 subfolders):

#### broker/ - Broker Integration

- `alpaca_trading_client.py`: Alpaca API integration (paper + live trading)
- `alpaca_execution_manager.py`: AlpacaExecutionManager implementing ExecutionManager interface
- `validators/`: Order validation, response parsing, error handling

#### orders/ - Order Lifecycle

- `order_manager.py`: Order placement, modification, cancellation
- `trailing_stop_manager.py`: Progressive trailing stop logic
- `partial_exit_manager.py`: Partial position exit handling

#### positions/ - Position Management

- `position_manager.py`: Position tracking (broker as source of truth)
- `position_sizer.py`: Profile-based position sizing automation
- `position_tracker.py`: Exit alerts and TP/SL monitoring
- `portfolio_manager.py`: Portfolio risk & allocation management

#### risk/ - Risk Management

- `simple_risk_manager.py`: Pre-trade risk checks (buying power, position %)
- `risk_calculator.py`: Portfolio risk metrics and position sizing

#### state/ - State Management

- `state_reconciler.py`: Broker-to-local state synchronization
- `broker_state_cache.py`: Broker state caching layer
- `local_state_manager.py`: Local JSON state persistence

#### scheduling/ - Trading Cycles

- `trading_cycle.py`: Cost-efficient daily routines (morning/evening)
- `trading_pipeline.py`: Complete daily workflow orchestrator
- `daily_scheduler.py`: Automated scheduling with GTC orders

#### accounts/ - Multi-Account Management

- `account_manager.py`: Multi-account support with API-first discovery
- `account_tools.py`: Agent-compatible account tools

#### instruments/ - Ticker & Timeframe Management

- `ticker_database.py`: Ticker metadata and approved list management
- `approved_tickers.py`: Per-ticker entry modes and limits
- `timeframe_tools.py`: Timeframe parsing and conversion
- `indicators.py`: MACD, RSI, and technical indicator calculations
- `data_fetch.py`: Market data retrieval utilities

#### utils/ - Trading Utilities

- `unified_price_fetcher.py`: Current price fetching with fallback
- `simple_signals.py`: Signal generation helpers
- `report_generator.py`: Morning/evening report formatting

### src/data_sources/ - Data Integration

**Purpose**: Market data acquisition, caching, and normalization

**Structure**:

- `cache/`: SQLite-based caching (8-10x performance improvement, 90%+ hit rate)
  - `sqlite_cache.py`: Production TradingCacheManager (ACID, thread-safe, futures-ready)
  - `cache_adapter.py`: Transparent upgrade layer
  - `unified_cache.py`, `market_data_cache.py`: Deprecated file-based caches
- `sources/market/`: Multiple provider support (Alpaca, Alpha Vantage)
- `processors/`: Data normalization and format standardization

### src/voting/ - Voting Strategy Framework

**Purpose**: Multi-indicator ensemble voting system

**Files**:

- `base_voting_strategy.py`: Abstract voting framework
- `basic_voting_strategy.py`: MACD+RSI voting implementation

### src/utils/ - Utility Functions

**Purpose**: General-purpose helper functions

**Files**:

- `date_utils.py`: Date/time processing and timezone handling
- `agent_utils.py`: Agent configuration and parsing
- `output_manager.py`: Organized result management

## Key File Descriptions

### main.py - Unified Entry Point

**Commands**:

```bash
python main.py test-voter              # Test VoterAgent with AAPL
python main.py check-positions          # Check paper trading positions
python main.py paper-trade [SYMBOL]    # Full trading cycle check
python main.py analysis                 # Generate analysis reports
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

### src/trading/trading_cycle.py

**Cost-Efficient Operations**:

**Daily Routines**:

- Morning (9:20 AM ET): Reconcile, adjust stops, report
- Evening (3:50 PM ET): EOD review, next-day prep

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
│  VoterAgent Decision                │
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
├── test_alpaca_basic.py              # Basic Alpaca integration (3/3)
├── test_alpaca_market_data.py        # Market data retrieval
├── test_alpaca_order_manager.py      # Order management (9/9)
├── test_alpaca_advanced_orders.py    # Advanced orders (6/6)
├── unit/agents/                      # Unit tests for agents
└── tools/                            # Tool integration tests
```

### Test Coverage

**Current**: 35/35 tests passing

- Market Data: 3/3
- Basic Orders: 9/9
- Advanced Orders: 6/6
- Advanced Features: 17/17

## Dependencies

### Core Framework

```text
agent-framework-core>=1.23,<2     # Microsoft Agent Framework: agents, FunctionTool (#606)
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

- **Agents**: Decision making (Agent Framework agents)
- **Tools**: Data access and calculations (pure functions)
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

**Adding Agents**:

```python
class NewAgent(BaseAgent):
    def __init__(self, name, **kwargs):
        super().__init__(name=name, tools=tools, **kwargs)

    def generate_reply(self, messages, context=None):
        # Custom logic
        pass
```

**Adding Tools**:

```python
new_tool = FunctionTool(
    func=my_function,
    name="tool_name",
    description="Tool description"
)
ALL_TOOLS.append(new_tool)
```

---

*Comprehensive guide to codebase organization for contributors and developers.*
