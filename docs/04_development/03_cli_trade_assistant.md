# CLI Trade Assistant - Issue #308 Implementation

**Status**: ✅ Complete (November 8, 2025). Since #331 the signal is `VotingSignal`, not an agent, and the CLI's tools are plain functions in `src/cli/tools/`; nothing here opens trades automatically.
**Type**: Human-in-Loop Trading Interface
**Priority**: P0 - Critical

---

## Overview

The CLI Trade Assistant is a conversational trading interface that allows users to request trade analysis using natural language. The system uses OpenAI's GPT models for parsing user input and the production VotingSignal for MACD+RSI technical analysis.

### Key Features

- **Natural Language Understanding**: Parse queries like "is SPY at 600 a good entry?" or "buy 10 AAPL"
- **Real Technical Analysis**: Production VotingSignal with MACD+RSI voting system
- **Portfolio Risk Management**: Automatic position sizing based on portfolio percentage
- **Interactive Confirmation**: Review suggestions before execution
- **Two Autonomy Modes**: Confirm (default) or Auto-execute

---

## Architecture

### Plugin-Based Design

The implementation follows a clean plugin architecture with dependency injection:

```text
┌─────────────────────────────────────────────────────────┐
│                   CLI Presentation Layer                 │
│              (Interactive REPL with commands)             │
└──────────────────┬──────────────────────────────────────┘
                   │
                   ▼
┌─────────────────────────────────────────────────────────┐
│                  TradingOrchestrator                     │
│          (Central coordinator for all UIs)               │
└──┬────────┬──────────┬──────────┬────────────────────┬──┘
   │        │          │          │                    │
   ▼        ▼          ▼          ▼                    ▼
┌──────┐ ┌─────┐  ┌──────┐  ┌──────┐         ┌─────────────┐
│Input │ │Strat│  │Risk  │  │Exec  │         │Session Store│
│Parser│ │Anlyz│  │Mgr   │  │Mgr   │         │  (Optional) │
└──────┘ └─────┘  └──────┘  └──────┘         └─────────────┘
    │        │         │         │
    │        │         │         │
    ▼        ▼         ▼         ▼
 LLMParser  Real   Simple   Alpaca
 (OpenAI)   Voter  Risk    Execution
            Agent  Manager  Manager
```

### Component Responsibilities

**TradingOrchestrator** (`src/core/trading_orchestrator.py`, the CLI's request pipeline; not an agent):

- Central coordinator for complete trading workflow
- Manages: parse → analyze → risk → suggest → execute
- Used by all presentation layers (CLI, GUI, API)
- Session state management (optional)

**InputParser Interface** (`src/core/interfaces/input_parser.py`):

- Abstract interface for parsing user input
- Implementations: LLMParser (OpenAI), RegexParser (future), GUIParser (future)

**StrategyAnalyzer Interface** (`src/core/interfaces/strategy_analyzer.py`):

- Abstract interface for trade analysis strategies
- Implementations: RealVoterStrategy (MACD+RSI), OptionsStrategy (future #330)

**RiskManager Interface** (`src/core/interfaces/risk_manager.py`):

- Abstract interface for risk assessment
- Implementations: SimpleRiskManager (MVP), PortfolioManager (future #333)

**ExecutionManager Interface** (`src/core/interfaces/execution_manager.py`):

- Abstract interface for order execution
- Implementations: AlpacaExecutionManager, SimulatedExecutor (future)

---

## Implementation Details

### 1. LLM Integration (`src/services/llm/`)

**OpenAIService** - Dual model configuration:

- **gpt-4o-mini**: Tool calling and function extraction (fast, cheap)
- **o4-mini**: Reasoning and analysis (configured in config.json)

```python
llm_service = OpenAIService(
    tool_calling_model="gpt-4o-mini",
    reasoning_model="o4-mini",
    api_key=config.get("OPEN_AI_KEY")
)
```

**LLMParser** - Natural language parsing:

- Extracts ticker, action, quantity, price from user input
- Auto-correction for common typos ("spy at 60" → "SPY at 600")
- Validation (ticker format, quantity, price ranges)

### 2. Real VotingSignal Integration (`src/strategies/real_voter_strategy.py`)

**RealVoterStrategy** - Production MACD+RSI analysis:

- Runs the production `VotingSignal` (`src/trading/instruments/voting_signal.py`)
- Fetches real market data (Alpaca, Alpha Vantage)
- MACD(13/34/8) + RSI(14) with Fibonacci parameters
- Voting logic: Strong consensus, weak signal, conflict detection

**Market Data Fetching**:

- 60-day lookback period (configurable)
- Fallback (Alpaca → Alpha Vantage)
- Graceful error handling with HOLD signal on failure

**Signal Generation**:

- **Strong Consensus**: MACD and RSI agree → 85% confidence
- **Weak Signal**: Only one indicator active → 65% confidence
- **Conflict**: MACD and RSI disagree → HOLD
- **Neutral**: Both indicators neutral → HOLD

### 3. Risk Management (`src/risk/simple_risk_manager.py`)

**SimpleRiskManager** - Portfolio percentage based:

- Default position size: 5% of portfolio
- Max position size: 15% of portfolio (warning)
- Buying power check with fallback ($100k portfolio, 50% buying power)
- Risk/reward ratio calculation
- Warning generation (no blocking)

**Position Sizing Logic**:

```python
if user_specified_quantity:
    quantity = user_quantity
else:
    target_value = portfolio_value * (5.0 / 100.0)  # 5% default
    quantity = int(target_value / entry_price)
```

### 4. Execution Manager (`src/execution/alpaca_execution_manager.py`)

**AlpacaExecutionManager** - Bracket order execution:

- Integrates with existing OrderManager
- Places entry + stop-loss + take-profit orders
- Enforces GTC (Good-Til-Canceled) time in force
- Stub mode for testing without broker connection

**Order Tracking**:

- Returns entry_order_id, stop_order_id, target_order_id
- Order status queries
- Cancel order support

### 5. CLI Session (`src/presentation/cli/cli_session.py`)

**CLISession** - Interactive REPL:

- Async-based input loop
- Command handling: /help, /exit, /auto, /confirm
- Two autonomy modes:
  - **confirm**: Review each trade before execution (default)
  - **auto**: Auto-execute approved trades
- Formatted suggestion display
- User confirmation workflow

**Display Format**:

```text
======================================================================
📊 SPY @ $670.97
======================================================================
⬇️ SELL SUGGESTED
   Confidence: 65.0%

📈 Analysis:
   • Weak signal: Only MACD signals SELL
   • MACD: SELL (histogram: -1.195184)
   • RSI: HOLD (value: 49.7)
   • ⚠️ Weak signal from single indicator

💰 Entry Plan:
   Entry:  $670.97
   Stop:   $684.39 (+2.0%)
   Target: $647.49 (-3.5%)
   Qty:    7 shares
   Order:  GTC

📊 Portfolio Impact:
   Trade Value: $4,696.79
   Portfolio %: 4.7% (after transaction)
   Max Loss:    $93.94
   Risk/Reward: 1.75

Continue? [yes/no]: _
```

### 6. Factory Pattern (`src/core/factory.py`)

**OrchestratorFactory** - Component wiring:

- Loads configuration from config.json
- Creates all components with correct dependencies
- Supports stub vs real VotingSignal selection
- Hardcoded configuration (YAML deferred to iteration 2)

```python
factory = OrchestratorFactory()
orchestrator = factory.create(
    order_manager=None,      # None = stub mode
    use_real_voter=True      # True = production VotingSignal
)
```

---

## Usage

### Interactive Mode

```bash
python main.py trade-assist
```

**Example Session**:

```text
> is SPY at 600 a good entry?

[System fetches market data, analyzes with MACD+RSI]
[Displays full analysis with technical indicators]

Continue? [yes/no]: yes

[Executes bracket order with entry/stop/target]
✅ ORDER PLACED SUCCESSFULLY
   7 shares SPY
   Entry Order:  stub_entry_123
   Stop Order:   stub_stop_123
   Target Order: stub_target_123
```

### Commands

- `/help` - Show help and examples
- `/exit` - Exit the CLI
- `/auto` - Enable auto-execute mode (no confirmation)
- `/confirm` - Enable confirm mode (default, requires yes/no)

### Natural Language Examples

**Review queries**:

- "is SPY at 600 a good entry?"
- "should I buy AAPL?"
- "analyze TSLA"
- "what do you think about MSFT?"

**Buy orders**:

- "buy 10 AAPL"
- "buy 10 AAPL at 200"
- "enter AAPL with 10 shares"

**Sell orders**:

- "sell 5 TSLA"
- "sell 5 TSLA at 250"
- "exit my AAPL position"

---

## Test Results

### Real OpenAI API Tests (3/3 passing)

#### Test 1: Review Query

```text
Input: "is SPY at 600 a good entry?"
✅ Parsed: SPY, review action, $600 price
✅ Generated: Entry/stop/target, 8 shares, 75% confidence
```

#### Test 2: Buy Order

```text
Input: "buy 10 AAPL at 200"
✅ Parsed: AAPL, buy action, 10 shares, $200 price
✅ Executed: Bracket order with stub (entry/stop/target IDs)
```

#### Test 3: Sell Order

```text
Input: "sell 5 TSLA"
✅ Parsed: TSLA, sell action, 5 shares
✅ Generated: Full suggestion with analysis
```

### Real VotingSignal Tests (3/3 passing)

#### Test 1: SPY Analysis

```text
Market Data: Fetched 60 days from Alpaca
Signal: SELL (65% confidence)
MACD Histogram: -1.195184 (bearish)
RSI: 49.7 (neutral)
Verdict: Weak sell signal from MACD
Current Price: $670.97
```

#### Test 2: AAPL Analysis

```text
Market Data: Fetched 60 days from Alpaca
Signal: BUY (65% confidence)
MACD Histogram: 0.111064 (bullish)
RSI: 51.6 (neutral)
Verdict: Weak buy signal from MACD
Current Price: $258.45
```

#### Test 3: Stub vs Real Comparison

```text
Stub VoterStrategy:
  Signal: BUY (75% confidence)
  Reasoning: "MACD: BUY (bullish crossover) [MVP STUB]"

Real VotingSignal:
  Signal: SELL (65% confidence)
  Reasoning: "Weak signal: Only MACD signals SELL"

✅ Signals differ based on actual market data
```

---

## Configuration

### config.json Settings

```json
{
  "OPEN_AI_KEY": "sk-proj-...",
  "OPENAI_TOOL_MODEL": "gpt-4o-mini",
  "OPENAI_PROMPT_MODEL": "o4-mini",
  "ALPACA_ENDPOINT": "https://paper-api.alpaca.markets/v2",
  "ALPACA_PAPER_API_KEY": "...",
  "ALPACA_PAPER_SECRET": "..."
}
```

### VotingSignal Parameters

```python
macd_params = {
    'fast': 13,    # Fibonacci parameter (validated)
    'slow': 34,    # Fibonacci parameter (validated)
    'signal': 8    # Signal line period
}

rsi_params = {
    'period': 14,       # RSI calculation period
    'oversold': 30,     # Oversold threshold
    'overbought': 70    # Overbought threshold
}
```

---

## Files Created

### Core Components

- `src/core/models.py` - Data structures (TradeRequest, AnalysisResult, etc.)
- `src/core/trading_orchestrator.py` - Central coordinator
- `src/core/interfaces/` - Abstract interfaces for all plugins
- `src/core/factory.py` - Component wiring with config.json

### Services & Plugins

- `src/services/llm/llm_service.py` - LLM abstraction
- `src/services/llm/openai_service.py` - OpenAI implementation
- `src/parsers/llm_parser.py` - Natural language parser
- `src/strategies/voter_strategy.py` - Stub for testing
- `src/strategies/real_voter_strategy.py` - Runs VotingSignal for CLI requests
- `src/risk/simple_risk_manager.py` - Portfolio % risk management
- `src/execution/alpaca_execution_manager.py` - Alpaca execution

### Presentation Layer

- `src/presentation/cli/__init__.py` - Package initialization
- `src/presentation/cli/cli_session.py` - Interactive REPL

### Tests

- `tests/test_basic.py` - Foundation tests (4/4 passing)
- `tests/test_end_to_end.py` - Integration tests (4/4 passing)
- `test_real_api.py` - Real OpenAI API tests (3/3 passing)
- `test_real_voter.py` - Real VotingSignal tests (3/3 passing)

### Documentation

- `docs/sessions/2025-01-08_plugin_architecture_implementation.md` - Implementation tracking
- `docs/04_development/03_cli_trade_assistant.md` - This document

---

## Future Enhancements (Iteration 2)

### High Priority

1. **Real Alpaca OrderManager Integration** (1 hour)
   - Connect to actual paper trading account
   - Test with real bracket order placement
   - Validate order lifecycle management

2. **Portfolio Manager (#333)** (5-7 hours)
   - Replace SimpleRiskManager with PortfolioManager
   - Risk-based position sizing
   - Sector limits and correlation analysis
   - Existing position conflict detection

### Medium Priority

1. **YAML Configuration** (2 hours)
   - Replace hardcoded factory with YAML-based DI
   - Allow component swapping via config files
   - orchestrator_config.yaml for plugin selection
   - llm_config.yaml for provider settings

2. **Rich Formatting** (2-3 hours)
   - Add colors and tables via `rich` library
   - Better visual presentation
   - Progress indicators
   - Syntax highlighting

3. **Options Analysis (#330)** (5-7 hours)
   - OptionsStrategy plugin for calls/puts
   - Greeks calculation (Delta, Gamma, Theta, Vega)
   - Implied volatility analysis
   - Open interest and volume analysis

### Low Priority

1. **Multi-Agent Debate (#331)** (7-10 hours)
   - Implement agent coordination patterns
   - Sequential, group chat, voting modes
   - Consensus building with dissenting opinions
   - Integration with event bus (#316)

2. **Autonomy Expansion (#332)** (3-5 hours)
   - Per-ticker whitelists
   - Conditional auto-execute rules
   - Risk-based autonomy levels
   - Rule-based delegation

---

## Lessons Learned

### What Worked Well

1. **Plugin Architecture**: Clean separation of concerns, easy to test with mocks
2. **Stub First, Real Later**: VoterStrategy stub allowed fast iteration, then swapped for real
3. **Interface-Driven Design**: All components implement interfaces, swappable via factory
4. **Config.json Integration**: Simple configuration without YAML complexity
5. **Dual Model Strategy**: gpt-4o-mini for tool calling, o4-mini for reasoning (cost-effective)

### Challenges Overcome

1. **Import Path Issues**: Resolved with sys.path manipulation and absolute imports
2. **Circular Dependencies**: VoterStrategy stub avoided complex data pipeline integration
3. **Testing Without APIs**: Mock-based testing allowed validation without external calls
4. **Market Data Fetching**: Graceful fallback when data unavailable or insufficient

### Best Practices Established

1. **Always read files before editing**: Prevents data loss
2. **Test incrementally**: Foundation tests first, then integration tests
3. **Document as you build**: `docs/sessions/2025-01-08_plugin_architecture_implementation.md` tracked progress
4. **Commit frequently**: Small, focused commits with descriptive messages
5. **Use stubs for MVP**: Defer complex integrations until architecture validated

---

## Performance Metrics

### Response Times (Tested on development machine)

- **Natural language parsing**: 500-800ms (OpenAI API call)
- **Market data fetch**: 1-2 seconds (Alpaca API)
- **MACD+RSI calculation**: <50ms (local computation)
- **Total workflow**: 2-3 seconds from input to suggestion

### Resource Usage

- **Memory**: ~100MB (baseline Python + dependencies)
- **API Calls**: 1 OpenAI call per request (tool calling)
- **Market Data**: 60 days cached, reused for analysis

### Cost Estimates

- **OpenAI API**: $0.0001-0.0002 per request (gpt-4o-mini)
- **Alpaca Market Data**: Free (included with account)
- **Total per trade**: <$0.001 (OpenAI + minimal compute)

---

## Conclusion

The CLI Trade Assistant (#308) delivers a human-in-loop trading interface with:

✅ Natural language understanding (OpenAI gpt-4o-mini + o4-mini)
✅ Real technical analysis (VotingSignal MACD+RSI voting)
✅ Portfolio risk management (5% default position sizing)
✅ Interactive confirmation workflow (confirm/auto modes)
✅ Plugin architecture (easy to extend and test)
✅ Complete integration (8/8 tests passing)

**Used with**: Real API keys and a paper trading account

**Next steps**: Connect real Alpaca OrderManager for actual order placement

---

*Last Updated: November 8, 2025*
*Status: ✅ Complete*

---

## Help System (Issue #369)

**Status**: ✅ Complete (November 27, 2025)

### Overview

The CLI now includes an interactive help system with categorized commands, keyword search, and detailed examples.

### Usage

```bash
# Show all commands (categorized)
/help

# Get help for specific command
/help buy
/help morning-routine

# Search by keyword
/help search order
/help search stop

# Show trading tips for beginners
/tips
```

### Features

- **Categorized Commands**: workflow, trading, status, config, system
- **Keyword Search**: Find commands by searching descriptions and tags
- **Command Aliases**: Multiple ways to invoke commands
- **Related Commands**: Suggestions for related functionality
- **Detailed Examples**: Usage patterns for each command

### Implementation

- **File**: `src/cli/help_system.py`
- **Class**: `HelpSystem` - Centralized command documentation
- **Integration**: `CLISession._handle_command()` routes `/help` requests

### Command Categories

**WORKFLOW** - Daily trading routines

- `morning-routine` - Market scan and analysis
- `approve/reject` - Human-in-loop trade approval
- `monitor` - Position monitoring
- `evening-summary` - End-of-day report

**TRADING** - Execute trades

- `buy` - Place buy orders
- `sell` - Close positions
- `cancel` - Cancel pending orders

**STATUS** - Portfolio and positions

- `show portfolio` - Account overview
- `show positions` - Active holdings
- `show orders` - Open and recent orders
- `check my alerts` - Stop/target alerts

**CONFIG** - Settings

- `set execution-mode` - CONFIRM/AUTO/PAPER/DISABLED
- `show execution-mode` - Current mode
- `show config` - Trading configuration

**SYSTEM** - CLI controls

- `/help` - Help system
- `/exit` - Exit CLI
- `/toggle` - Toggle execution mode
- `/tips` - Trading education

### Future Enhancements

- Context-aware help (show help for current workflow step)
- Command history with arrow keys (#362)
- Interactive tutorials
- Command completion/suggestions
