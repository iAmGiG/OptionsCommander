# Command Reference

## Interactive CLI Commands (Issue #396)

The OptionsCommander CLI is an interactive trading assistant for the Alpaca paper account. Launch it with:

```bash
python main.py                     # Interactive mode
python main.py --run-due           # Run a routine if one is due now, then exit (what cron runs)
python main.py --daemon            # Background scheduler mode
python main.py --list-accounts     # List configured trading accounts
python main.py --account ID        # Select a trading account
```

Nothing opens trades automatically. Orders are placed only when you ask for them.

### Slash Commands

Every registered slash command (`@command(` in `src/cli/commands/slash_commands.py`). Usage and examples are in `config_defaults/help_commands.yaml`; `/help COMMAND` shows them in the CLI.

| Command | Alias | What it does |
| --- | --- | --- |
| `/help [topic]` | | Show help |
| `/exit` | `/quit` | Exit the CLI |
| `/about` | `/status` | Status dashboard |
| `/clear` | `/home`, `/cls` | Clear the screen and show the home page |
| `/faq [section]` | `/guide` | Features and how-to guide |
| `/tips` | | Moved to `/faq` |
| `/toggle` | | Switch between CONFIRM and AUTO execution of typed orders |
| `/mode [conservative\|moderate\|aggressive]` | | Show or set the trading mode |
| `/schedule` | | Enter scheduler management (status, notices, ack) |
| `/records [days\|all]` | | Daily equity, fills and realized P&L from the last routine's records. Last 10 recorded days by default. Read-only, no broker calls (#651) |
| `/plan SYM [levels\|volume\|atr]` | | Entry, stop, target and risk/reward from completed daily bars; `levels` gives support and resistance, `volume` the volume against its average, `atr` the average true range. Places nothing |
| `/trailing [explain [rate] \| compare \| example ENTRY PRICE STOP [rate]]` | | Trailing stop settings; the tiers of a climb rate (slow, medium, fast); the rates side by side; where the routines would put a given stop. Changes no stop |
| `/gtt [list \| create SYM COND VAL [notes] \| oco SYM above P below P [days] \| summary \| status ID \| delete ID \| cancel ID \| enable ID \| disable ID]` | | GTT price and gain/loss triggers. Conditions: above, below (a price); gain, loss, trailing (a percent). `oco` makes two price alerts; the first to fire disables the other. Held symbols are checked at the morning and evening routines; other symbols only at the evening routine (when a fresh price exists), and only alerts run there |
| `/watchlist [list \| add SYM [name] \| remove SYM [name] \| show NAME \| create NAME [desc] \| delete NAME \| limits \| scanner]` | `/wl` | Watchlists. `delete` asks first |
| `/partial [list \| summary \| active \| SYM \| levels SYM \| modify SYM N PRICE \| set SYM \| remove SYM]` | `/pe` | Partial exit plans. `set` plans target 1 (half the shares with the 50/50 split) of a held long position (2+ shares) to be sold at market by the evening routine once the price is at the average entry plus the mode's `target_1_pct` (4%, 5%, 6%); it asks first if the price is already there. `remove` asks first |
| `/timeframe [mode \| list \| presets \| preset NAME \| single TF \| validate VALUE]` | `/tf` | Timeframe mode. Only 1d has been backtested |
| `/voter [list \| info \| presets \| preset NAME \| promote NAME \| demote NAME \| role NAME active\|review]` | | Voter rankings and presets for the MACD+RSI signal (`VotingSignal`) |
| `/backup [list \| create \| restore NAME \| export TABLE \| status \| cleanup [DAYS]]` | | Back up and restore `state/user.db`. `cleanup` removes backups older than DAYS (default 30) after listing them and asking; the 5 newest of each database stay |

The commands under the headings below are typed requests (no slash) that the CLI parses, with the language model for free text.

### Trading Commands

```bash
# Buy and sell orders
> buy AAPL 10                      # Buy 10 shares of AAPL
> sell MSFT 5                      # Sell 5 shares of MSFT
> cancel ORDER_ID                  # Cancel pending order

# Position and portfolio management
> show positions                   # View all open positions
> show portfolio                   # Portfolio summary with P&L
> show orders                      # View pending orders
> show watchlist                   # View scanner watchlist
```

### Workflow Commands

```bash
# Daily routines
> morning-routine                  # Run morning market scan and analysis
> monitor                          # Monitor active positions for exit signals
> evening-summary                  # Generate end-of-day performance report

# Trade approval workflow
> approve                          # Approve pending trades from scanner
> reject                           # Reject pending trades
```

### Configuration Commands (Issue #358)

```bash
# View YAML configuration files
> show config-file                 # List all config files
> show config-file --file trading  # View trading_config.yaml
> show config-file --file scanner  # View scanner_config.yaml

# Configuration details
> show watchlist                   # View market scanner symbols
> show indicators                  # List available technical indicators
```

### Timeframe Commands (Issue #365)

```bash
# Timeframe management
> show timeframe                   # Display current trading timeframe
> set timeframe 1d                 # Change to daily timeframe (the backtested one)
> set timeframe 1h                 # Change to hourly timeframe (experimental)

# Supported timeframes:
# - 1m, 5m, 15m, 30m (scalping/day trading)
# - 1h, 2h, 4h (intraday swing trading)
# - 1d (daily swing/position trading - the backtested timeframe)
# - 1w, 1M (position/long-term trading)
```

⚠️ **Note**: Only the 1d timeframe has been backtested (with no edge over buy-and-hold, #574). Other timeframes are experimental.

### Forward Testing (Issue #324)

The forward-test code was deleted in #435, so there are no `forward-test` commands that work. The 30-day paper signal run (#574 stage C) is deferred. See [the forward test protocol](../07_testing/01_forward_test_protocol.md) (historical).

### Scheduler Commands

```bash
# Automated trading scheduler
> show scheduler                   # View scheduler status and schedule
> enable scheduler                 # Enable automated trading
> disable scheduler                # Disable automation
```

### Help System

```bash
# Interactive help
> /help                            # Show all commands grouped by category
> /help COMMAND                    # Detailed help for specific command
> /help search KEYWORD             # Search commands by keyword
> /help --examples                 # View all command examples

# Typo suggestions - intelligent error handling
> /help mnitor
💡 Did you mean one of these?
  - monitor          Monitor active positions
  - morning-routine  Run morning market scan and analysis

# Exit CLI
> /exit                            # Exit the interactive session
```

### Command Aliases

Many commands support short aliases for faster typing:

- `pos` → `show positions`
- `port` → `show portfolio`
- `tf` → `timeframe`
- `morning` → `morning-routine`
- `monitor` → `monitor`

## Environment Setup

- **Python version**: 3.12
- **Environment**: `~/miniconda3/envs/OptionsCommander/bin/python` (see CLAUDE.md)
- **Install dependencies**: `pip install -e .`

## Current Development Commands

### Import Check ✅

```bash
python -c "from src.trading.instruments.voting_signal import VotingSignal; print('VotingSignal: OK')"

# All unit tests (read the test-safety notes in docs/07_testing/README.md before a full run)
python -m unittest discover tests
```

### Archived Validation Results ✅

```bash
# Validation complete - results archived in docs/archived/experiments/experiment_293_validation/
# Key findings (in-sample, superseded by #574): MACD+RSI voting (0.856 Sharpe) > single MACD (0.841 Sharpe)
# Live-rules baseline (#574 stage A): no edge over buy-and-hold at live timing
```

### Code Quality & Validation

```bash
# Lint and format code
pre-commit run ruff-check --all-files    # lint (tracked files, pinned ruff)
pre-commit run ruff-format --all-files   # format

# Type checking (if available)
mypy src/ --ignore-missing-imports

# Run unit tests
python -m unittest discover tests
```

## Configuration Commands

### Market Data Cache (SQLite)

```bash
# Check cache status and statistics
python scripts/utilities/cache_manager.py stats

# List cached symbols
python scripts/utilities/cache_manager.py symbols

# Query specific symbol data
python scripts/utilities/cache_manager.py query SPY --start 2024-01-01 --end 2024-12-31

# Check database size
ls -lh .cache/trading_data.db

# Legacy: Check old JSON cache (deprecated)
ls -la .cache/market_data/ 2>/dev/null || echo "No legacy cache found"
```

### API Configuration

```bash
# Create config file
mkdir -p config
cat > config/config.json << EOF
{
  "ALPHA_VANTAGE_KEY": "your_key_here"
}
EOF
```

## Results Analysis Commands

### Active Results (Current Development)

```bash
# View voting strategy validation
cat docs/archived/experiments/experiment_293_validation/README.md

# Check validation results
cat docs/01_overview/01_system_overview.md

# Review voting system design
cat docs/02_architecture/03_voting_system.md
```

### Historical Analysis (Archived)

```bash
# View archived V0-V4 results
cat reports/archived/v0_v4_deprecated/analysis/V0-V4_Framework_Results.md

# Check legacy backtest data
ls reports/archived/v0_v4_deprecated/continuous_backtests/
```

## Development Workflow Commands

### GitHub Issue Management

```bash
# View active development issues
gh issue list --repo iAmGiG/AutoTrader --label "refactoring"

# Check project board status
gh project list --repo iAmGiG/AutoTrader

# View specific issue details
gh issue view 433 --repo iAmGiG/AutoTrader  # Code quality refactoring epic
gh issue view 436 --repo iAmGiG/AutoTrader  # Related refactoring issues
```

### Documentation Updates

```bash
# View current documentation structure
tree docs/ -I "__pycache__"

# Check documentation status
cat docs/README.md
cat docs/01_overview/01_system_overview.md
cat docs/02_architecture/03_voting_system.md
```

## Legacy Commands (Deprecated)

### V0-V4 Sentiment Framework ❌

```bash
# These commands are deprecated - system moved to archived
python scripts/runs/backtest.py --version V0 --symbol AAPL --year 2024
python scripts/runs/backtest.py --all-versions --symbol AAPL --year 2024
python scripts/analysis/generate_results_summary.py --advanced
```

**Note**: V0-V4 framework deprecated in favor of the MACD+RSI voting approach. Its code is not in the repository.

## Troubleshooting Commands

### Common Issues

```bash
# Check Python environment
python --version
conda info --envs

# Verify the signal imports
python -c "from src.trading.instruments.voting_signal import VotingSignal; print('VotingSignal import successful')"

# Test cache data loading (SQLite)
python scripts/utilities/cache_manager.py query AAPL --start 2024-01-01 --end 2024-12-31

# Check SQLite cache database
sqlite3 .cache/trading_data.db "SELECT COUNT(*) FROM market_cache;"

# Check disk space for cache
du -sh .cache/
```

### Performance Monitoring

```bash
# Monitor test execution time
time python tests/experiment_293_macd_vs_voting.py

# Check memory usage during backtests
/usr/bin/time -v python tests/experiment_extended_period_voting.py
```

---

**Note**: Commands updated for Issue #396 - Interactive CLI with help system, configuration commands, timeframe support, and forward testing.

**Current Focus**: CLI Enhancement (Issue #396) - Complete command documentation and help system.
