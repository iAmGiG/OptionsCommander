<!-- markdownlint-disable MD060 -->
# OptionsCommander: Multi-Agent Trading System

Formerly RH2MAS (Reflective Hybrid Head Multi-Agent System). The GitHub repository `iAmGiG/OptionsCommander` was renamed from `AutoTrader-AgentEdge` on 2026-10-28 (#549); old links redirect.

**Copyright (C) 2024-2026 Chris R. (iAmGiG)** | Licensed under [AGPL-3.0](LICENSE) | See [NOTICE](NOTICE)

## Powered by AgentEdge - AI-assisted trading with human oversight

[![Python](https://img.shields.io/badge/python-3.12+-blue.svg)](https://www.python.org/downloads/)
[![Agent Framework](https://img.shields.io/badge/Agent%20Framework-1.x-green.svg)](https://github.com/microsoft/agent-framework)
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

**OptionsCommander** is a research-driven trading platform with a multi-agent AI architecture built on Microsoft Agent Framework (it was AutoGen until #606). It trades a MACD+RSI voting strategy through Alpaca Markets with human oversight, and its research tests strategies out-of-sample, with transaction costs and look-ahead checks.

**Core Philosophy**: simple rules + human decision making, tested out-of-sample with costs > curve-fit technical indicators

### Key Research Findings (Oct 2026)

- **Live rules have no edge (#574 stage A)**: the CLI's MACD+RSI rules at live timing, on the 19-symbol watchlist from 2009 to 2026-10-09 at 5 bps a side, have a Sharpe of 0.69 (0.71 from 2020; the 95% interval includes zero), a 44.2% win rate and a -6.9% max drawdown. Alpha against buy-and-hold of the same symbols is +0.11% a year (t = 0.19). See the [live-rules baseline](docs/08_research/03_strategy_research/live_rules_baseline.md). The 30-day paper signal run (stage C) and automated entries are deferred.
- **GEX research closed (#576)**: with the end-of-day regime lagged one day and a corrected backtester, no GEX trading overlay survives multiple-testing correction. Negative GEX goes with about 7% higher next-day variance in high-VIX markets, but adds no out-of-sample forecast value. See the [re-validation](docs/08_research/02_gex_research/regime_lookahead_revalidation.md).

Earlier findings (Dec 2025 - Jan 2026):

- **Transaction Cost Robustness**: MACD+RSI shows 44% pass rate vs TSMOM's 19% despite 10x higher turnover (#519)
- **MACD Parameter Stability**: No robustly profitable MACD configs found out-of-sample. Best OOS Sharpe: -0.223 (#518)
- **Simulation Fidelity Gap**: Path-dependent backtesting engine created to address "wick risk" (#528)

### Strategy Research Results

| Issue | Strategy | Finding | Status |
|-------|----------|---------|--------|
| #574 | Live MACD+RSI rules | Sharpe 0.69 at 5 bps; alpha +0.11%/yr (t = 0.19) vs buy-and-hold | No edge (stage A) |
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
| SQLite Cache | Complete (90%+ hit rate) |
| LLM Intent Routing | Complete |

---

## Core Components

### VotingSignal (in use on paper)

Location: `src/trading/instruments/voting_signal.py`

- **MACD Signal Generation**: 13/34/8 Fibonacci periods
- **RSI Momentum Analysis**: 14-period with 30/70 thresholds
- **Consensus Voting**: Strong signals when both indicators agree
- **Backtested Performance**: with the live rules and timing, Sharpe 0.69 at 5 bps a side (2009 to 2026-10-09), and no alpha over buy-and-hold of the same symbols; see the [live-rules baseline](docs/08_research/03_strategy_research/live_rules_baseline.md). The earlier 0.856 was in-sample on AAPL 2024.

### LLM-Based Intent Classification

- **Natural Language Commands**: GPT-4o-mini parses trade requests
- **Context-Aware Routing**: Distinguishes "any open orders?" from ticker "ANY"
- **Scalable Design**: No hardcoded keyword patterns for ticker disambiguation

---

## Installation

```bash
# Python 3.12+ required
conda create -n AutoTrader python=3.12
conda activate AutoTrader
pip install -e .
```

## Configuration

Create `config/config.yaml` with API credentials:

```yaml
ALPHA_VANTAGE_KEY: "your_key"    # Required: Fallback data
ALPACA_PAPER_API_KEY: "your_key" # Required: Paper trading
ALPACA_PAPER_SECRET: "your_key"  # Required: Paper trading
ALPACA_ENDPOINT: "https://paper-api.alpaca.markets/v2"

# Optional - VotingSignal uses pure math, no LLM required
OPEN_AI_KEY: "sk-..."
```

---

## Quick Start

### Interactive CLI

```bash
python main.py

# Example session:
> buy 10 AAPL              # Execute trade
> show portfolio           # View positions
> check my alerts          # Position alerts
> /schedule                # Scheduler management
> /help                    # All commands
```

### Scheduled Routines

```bash
# Recommended: a cron entry every 5 minutes runs whatever routine is due
./scripts/deployment/setup_cron.sh
# adds: */5 * * * * cd <repo> && <python> main.py --run-due

# Alternative: a long-running daemon (or the systemd unit in scripts/deployment/)
python main.py --daemon
```

The morning (9:20 AM ET) and evening (3:50 PM ET) routines run on NYSE trading days only. Each fetches broker state, records the account, positions and fills, reconciles, re-places missing or expiring stops, checks alerts and GTT triggers, raises stops to their trailing level (the evening routine only while the market is open), and writes `reports/daily/<ET date>_<routine>.md`. Logs go to `state/scheduler.log`, cron errors to `state/scheduler-cron.err`. The scheduler is off by default (`enabled: false` in `config_defaults/scheduler_config.yaml`); `enable` in the CLI's `/schedule` mode turns it on.

**Exits**: the routines renew and re-place stops but not take-profit orders (an aging one is only flagged in the report). Alpaca cancels GTC orders after 90 days, so a bracket's take-profit leg lapses and a position held that long exits by its trailing stop.

---

## CLI Commands

### Trading

| Command | Description |
|---------|-------------|
| `buy SYMBOL QTY` | Place buy order |
| `sell SYMBOL QTY` | Place sell order |
| `cancel ORDER_ID` | Cancel order |

### Information

| Command | Description |
|---------|-------------|
| `show portfolio` | Portfolio summary |
| `show positions` | Open positions with P&L |
| `show orders` | Order history |
| `show account` | Account details |

### Configuration

| Command | Description |
|---------|-------------|
| `show timeframe` | Current timeframe |
| `set timeframe 1d` | Change timeframe |
| `show config-file` | View YAML config |
| `show watchlist` | Scanner symbols |

### Workflow

| Command | Description |
|---------|-------------|
| `morning-routine` | Morning scan and analysis |
| `evening-summary` | End-of-day report |
| `monitor` | Watch positions for exits |
| `forward-test start NAME` | Start validation test |

---

## Project Structure

```text
OptionsCommander/           # repository root
├── src/
│   ├── trading/            # Trading infrastructure
│   │   ├── broker/         # Alpaca integration
│   │   ├── orders/         # Order management
│   │   ├── positions/      # Position tracking
│   │   └── scheduling/     # Daily routines
│   ├── data_sources/       # Market data
│   └── cli/                # CLI tools
├── config/                 # API credentials (local only)
├── config_defaults/        # Default YAML configs
├── docs/                   # Documentation
├── reports/                # Trading reports
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
# Unit tests
python -m pytest tests/ -v

# Code quality
ruff check src/
ruff format --check src/

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
