<!-- markdownlint-disable MD060 -->
# OptionsCommander: Trading System

Formerly RH2MAS (Reflective Hybrid Head Multi-Agent System). The GitHub repository `iAmGiG/OptionsCommander` was renamed from `AutoTrader-AgentEdge` on 2026-10-28 (#549); old links redirect.

**Copyright (C) 2024-2026 Chris R. (iAmGiG)** | Licensed under [AGPL-3.0](LICENSE) | See [NOTICE](NOTICE)

## Rule-based paper trading with human oversight

[![Python](https://img.shields.io/badge/python-3.12%20%7C%203.14%20%7C%203.15-blue.svg)](https://www.python.org/downloads/)
[![pandas](https://img.shields.io/badge/pandas-3.x-blue.svg)](https://pandas.pydata.org/)
[![Agent Framework](https://img.shields.io/badge/Agent%20Framework-1.x%20(parser)-green.svg)](https://github.com/microsoft/agent-framework)
[![Alpaca](https://img.shields.io/badge/Broker-Alpaca-yellow.svg)](https://github.com/alpacahq/alpaca-py)
[![Code Style](https://img.shields.io/endpoint?url=https://raw.githubusercontent.com/astral-sh/ruff/main/assets/badge/v2.json)](https://github.com/astral-sh/ruff)
[![License](https://img.shields.io/badge/license-AGPL--3.0-blue.svg)](LICENSE)

---

## Disclaimer

**THIS SOFTWARE IS FOR EDUCATIONAL AND RESEARCH PURPOSES ONLY.**

- **Not Financial Advice**: This system does not provide financial, investment, or trading advice
- **Use at Your Own Risk**: Trading involves substantial risk of loss
- **No Warranties**: Provided "as-is" without guarantees of accuracy or profitability
- **Past Performance**: Backtested results do not guarantee future performance

---

## Overview

**OptionsCommander** is a research-driven trading platform on Alpaca Markets. It runs on a paper account: scheduled routines manage the open positions' stops and keep daily records, and an interactive CLI places orders on request. The MACD+RSI voting signal has no backtested edge, so nothing opens trades automatically. The agents were retired in #331; Microsoft Agent Framework is used only by the natural-language parser. Its research tests strategies out-of-sample, with transaction costs and look-ahead checks.

**Core Philosophy**: simple rules + human decision making, tested out-of-sample with costs > curve-fit technical indicators

### Key Research Findings (Oct 2026)

- **Live rules have no edge (#574 stage A)**: the CLI's MACD+RSI rules at live timing, on the 19-symbol watchlist from 2009 to 2026-10-09 at 5 bps a side, have a Sharpe of 0.69 (0.71 from 2020; the 95% interval includes zero), a 44.2% win rate and a -6.9% max drawdown. Alpha against buy-and-hold of the same symbols is +0.11% a year (t = 0.19). See the [live-rules baseline](docs/08_research/03_strategy_research/live_rules_baseline.md). The 30-day paper signal run (stage C) and automated entries are deferred.
- **No exit or entry variant beats it either (Oct-Nov 2026)**: the live trailing stop against the fixed bracket (#539), the 09:20 morning raise on real prices (#623, removed in #649), RAF and weekly KAMA (#532, #529) and partial exits (#584) all measure within noise of the baseline, with nothing surviving multiple-testing correction. See [docs/08_research/03_strategy_research/](docs/08_research/03_strategy_research/).
- **GEX research closed (#576)**: with the end-of-day regime lagged one day and a corrected backtester, no GEX trading overlay survives multiple-testing correction. Negative GEX goes with about 7% higher next-day variance in high-VIX markets, but adds no out-of-sample forecast value. See the [re-validation](docs/08_research/02_gex_research/regime_lookahead_revalidation.md).

Earlier findings (Dec 2025 - Jan 2026):

- **Transaction Cost Robustness**: MACD+RSI shows 44% pass rate vs TSMOM's 19% despite 10x higher turnover (#519)
- **MACD Parameter Stability**: No robustly profitable MACD configs found out-of-sample. Best OOS Sharpe: -0.223 (#518)
- **Simulation Fidelity Gap**: Path-dependent backtesting engine created to address "wick risk" (#528)

### Strategy Research Results

| Issue | Strategy | Finding | Status |
|-------|----------|---------|--------|
| #574 | Live MACD+RSI rules | Sharpe 0.69 at 5 bps; alpha +0.11%/yr (t = 0.19) vs buy-and-hold | No edge (stage A) |
| #539 | Trailing stop vs fixed bracket | No detectable difference | CLOSED |
| #623 | Morning stop raise | Lower Sharpe on real 09:20 prices; removed (#649) | CLOSED |
| #532, #529 | RAF and weekly KAMA | No edge at live timing | STOP |
| #584 | Partial exits | No gain; every design lowers CAGR | Measured (#654) |
| #576 | GEX overlays | None survives multiple-testing correction | CLOSED |
| #516 | TSMOM+GEX Hybrid | Median improvement: **-2.9%** (worse than baseline) | CLOSED |
| #518 | MACD Stability | OOS Sharpe: -0.223 (least unprofitable) | CLOSED |
| #519 | Transaction Costs | MACD+RSI: 44% pass rate, TSMOM: 19% | CLOSED |

**Methodology**: Walk-forward validation, turnover-proportional transaction costs, look-ahead bias protection, median reporting for outlier robustness.

**Note**: Past performance does not guarantee future results. Research is ongoing with focus on simulation fidelity improvements.

### Component Status

| Component | Status |
|-----------|--------|
| VotingSignal | In use (paper account) |
| CLI Trade Assistant | Complete |
| Alpaca Integration | Operational |
| Position Management | Complete |
| Trading Cycle | Complete |
| Daily Scheduler | In use (cron, paper account) |
| SQLite Cache | In use (market data and broker reads) |
| LLM request parsing | In use (typed requests only) |

---

## Core Components

### VotingSignal (in use on paper)

Location: `src/trading/instruments/voting_signal.py`

- **MACD Signal Generation**: 13/34/8 Fibonacci periods
- **RSI Momentum Analysis**: 14-period with 30/70 thresholds
- **Consensus Voting**: Strong signals when both indicators agree
- **Backtested Performance**: with the live rules and timing, Sharpe 0.69 at 5 bps a side (2009 to 2026-10-09), and no alpha over buy-and-hold of the same symbols; see the [live-rules baseline](docs/08_research/03_strategy_research/live_rules_baseline.md). The earlier 0.856 was in-sample on AAPL 2024.

### Typed Requests

- **One model call per request**: `LLMParser` (`src/parsers/llm_parser.py`) turns free text into a parsed request with a forced tool call. The model is `gpt-4o-mini` unless `OPENAI_TOOL_MODEL` says otherwise. This is the only place the system calls a model.
- **Commands are routed first, by whole words**: cancel, scheduler and mode requests never reach the model.
- **The model's answer is checked**: action, quantity and request type are validated, and a question ("should I sell SPY?") stays a review whatever the model says.

---

## Installation

```bash
# Python 3.12 (CI also runs 3.14 and 3.15)
conda create -n OptionsCommander python=3.12
conda activate OptionsCommander
pip install -e .              # runtime
pip install -e ".[dev]"       # + ruff, pre-commit, pytest and the other dev tools
pip install -e ".[gex]"       # optional: gex-database-infrastructure (private repo; research only)
pre-commit install
```

If `~/.local` holds packages for other interpreters, run with `python -s` (the cron entry does) so it isn't loaded ahead of the env.

## Toolchain and Versions

Declared in `pyproject.toml`; "in use" is the OptionsCommander env that runs the paper account (November 2026).

| Tool | In use | Declared | Used for |
| ---- | ------ | -------- | -------- |
| Python | 3.12.15 | `>=3.12` | Runtime; CI tests 3.12, 3.14 and 3.15 |
| pandas | 3.1.0 | `>=2.2.0,<4` | Data handling (pandas 3 reproduces every committed research result) |
| numpy, scipy, statsmodels | 2.5.4, 1.18.1, 0.15.0 | `>=2.0`, `>=1.14`, `>=0.14` | Indicators and research statistics |
| alpaca-py | 0.44.0 | `>=0.43.2` | Broker and market data (paper account) |
| agent-framework-core, -openai | 1.23.0, 1.17.0 | `>=1.23,<2`, `>=1.17,<2` | The natural-language parser only; brings `openai` 3.39.0 |
| psycopg2-binary | 2.9.13 | `>=2.9.9` | Research backtester (PostgreSQL, read-only) |
| gex-database-infrastructure | 1.4.0 | optional `[gex]` | Options and GEX data for research and DB tests |
| ruff | 0.17.1 | `==0.17.1` (`[dev]`, pre-commit, workflows) | Lint, import order and format (replaced black and isort) |
| pre-commit | 4.7.0 | `>=3.6.0` | Runs ruff, markdownlint-cli2 0.12.1, bandit 1.9.4 and the file checks |
| pytest | 9.1.1 | `>=7.4.0` | Tests (`-m "not db"` in CI) |

Market data comes from the SQLite cache, then Alpaca, then Alpha Vantage (Polygon was removed in #608). State lives in SQLite (`state/user.db`).

## Configuration

Create `config/config.yaml` (or `config/config.json`; YAML is read first) with API credentials. It is gitignored: never commit it.

```yaml
ALPHA_VANTAGE_KEY: "your_key"    # Required: Fallback data
ALPACA_PAPER_API_KEY: "your_key" # Required: Paper trading
ALPACA_PAPER_SECRET: "your_key"  # Required: Paper trading
ALPACA_ENDPOINT: "https://paper-api.alpaca.markets/v2"

# Optional: only typed requests use the model; slash commands, the signal and the routines don't
OPEN_AI_KEY: "sk-..."
OPENAI_TOOL_MODEL: "gpt-4o-mini"   # optional, the default
```

---

## Quick Start

### Interactive CLI

```bash
python main.py

# Example session:
> how does SPY look?       # Analysis only, nothing placed
> buy 10 AAPL              # Shows the order and asks before placing it
> show portfolio           # Positions and account
> /records                 # Daily equity, fills and realized P&L
> /schedule                # Scheduler management
> /help                    # All commands
```

### Scheduled Routines

```bash
# Recommended: a cron entry every 5 minutes runs whatever routine is due
./scripts/deployment/setup_cron.sh
# adds: */5 * * * * cd <repo> && <python> -s main.py --run-due
# (PYTHON_BIN defaults to ~/miniconda3/envs/OptionsCommander/bin/python)

# Alternative: a long-running daemon (or the systemd unit in scripts/deployment/)
python main.py --daemon
```

The morning (9:20 AM ET) and evening (3:50 PM ET) routines run on NYSE trading days only. Each fetches broker state, records the account, positions and fills, reconciles, re-places missing or expiring stops, checks alerts and GTT triggers, raises stops to their trailing level (the evening routine only while the market is open), and writes `reports/daily/<ET date>_<routine>.md`. Logs go to `state/scheduler.log`, cron errors to `state/scheduler-cron.err`. The scheduler is off by default (`enabled: false` in `config_defaults/scheduler_config.yaml`); `enable` in the CLI's `/schedule` mode turns it on.

**Exits**: the routines renew and re-place stops but not take-profit orders (an aging one is only flagged in the report). Alpaca cancels GTC orders after 90 days, so a bracket's take-profit leg lapses and a position held that long exits by its trailing stop.

---

## CLI Commands

Slash commands are listed in full, from the code, in [the command reference](docs/03_reference/03_commands.md). The main ones:

| Command | Description |
|---------|-------------|
| `/records [days\|all]` | Daily equity, fills and realized P&L |
| `/plan SYM [levels\|volume\|atr]` | Entry plan for a buy, and its inputs (places nothing) |
| `/trailing [explain\|compare\|example]` | How trailing stops move, from the routines' own code |
| `/gtt` | Persistent price triggers (held symbols checked at both routines; other symbols at the evening routine, alerts only) |
| `/watchlist`, `/backup`, `/partial` | Watchlists, database backups, partial exit plans |
| `/mode`, `/timeframe`, `/voter` | Trading mode, timeframe and voter settings |
| `/schedule` | Scheduler status, notices and acknowledgements |
| `/toggle` | CONFIRM (default) or AUTO execution of typed orders |

Typed requests (no slash):

| Example | What happens |
|---------|--------------|
| `buy 10 AAPL` | Analysis, then the order shown and asked for (CONFIRM mode) |
| `sell SPY`, `sell 5 SPY` | Sells a held position; cancels the stop that holds its shares first, and asks |
| `cancel orders for TSLA`, `cancel all orders` | Lists the orders, marks protective stops, and needs a typed `yes` |
| `show portfolio`, `any open orders?` | Positions, account and open orders |
| `what's my stop on SPY?` | That symbol's orders and stop |
| `how does SPY look?` | Analysis only |
| `check my alerts` | Position alerts against their stops |
| `list accounts` | Configured accounts |

Short selling is refused.

## Project Structure

```text
OptionsCommander/           # repository root
├── main.py                 # CLI, --run-due (cron), --daemon
├── src/
│   ├── trading/            # broker/, orders/, positions/, state/, gtt/, scheduling/, instruments/
│   ├── strategies/         # RealVoterStrategy (the CLI's analysis)
│   ├── core/               # Models, the CLI's request pipeline, trading modes
│   ├── parsers/            # LLMParser (the one model call)
│   ├── market_data/        # Alpaca, Alpha Vantage, read-only options chains
│   ├── cache/              # SQLite cache
│   ├── cli/                # Interactive CLI: commands/, tools/, scheduler/
│   ├── backtesting/        # Research backtester, live-rules simulator
│   └── utils/              # Market hours, dates, state paths
├── scripts/                # research/, deployment/, utilities/
├── config/                 # API credentials (local only)
├── config_defaults/        # Default YAML configs and help text
├── docs/                   # Documentation
├── state/                  # Local state, logs, notices (gitignored)
├── reports/                # Daily routine reports
└── tests/                  # Test suite
```

---

## Documentation

| Topic | Location |
|-------|----------|
| Architecture | [docs/02_architecture/](docs/02_architecture/) |
| Development | [docs/04_development/](docs/04_development/) |
| Design Decisions | [docs/05_decisions/](docs/05_decisions/) |
| Features | [docs/06_features/](docs/06_features/) |
| Testing | [docs/07_testing/](docs/07_testing/) |
| Research | [docs/08_research/](docs/08_research/) |

---

## Development

### Testing

```bash
# Tests: what CI runs (no database, no credentials)
python -m pytest tests/ -m "not db"
# Database integration tests (read-only; needs the local PostgreSQL)
python -m pytest tests/ -m "db"

# Lint and format: the pinned hooks, over every tracked file. tests/ is gitignored
# (its files are force-added), so run ruff through pre-commit, not on directories
python -m pre_commit run --all-files

# Signal import check
python -c "from src.trading.instruments.voting_signal import VotingSignal; print('OK')"
```

### Contributing

1. Pick an issue from [GitHub Issues](https://github.com/iAmGiG/OptionsCommander/issues)
2. Create a feature branch
3. Make changes following [docs/05_decisions/](docs/05_decisions/)
4. Submit PR to `development` branch

---

## License

AGPL-3.0 - See LICENSE file for details.
