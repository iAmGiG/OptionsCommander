# Scripts Directory

Production utilities, deployment tools, data collection, and research scripts for the OptionsCommander system.

## Directory Structure

```bash
scripts/
├── README.md (this file)
│
├── utilities/                     # General-purpose production tools
│   ├── cache_manager.py          # SQLite cache CLI (stats, cleanup, export, etc.)
│   ├── lint_check.py             # Ruff linting helper for code quality
│   └── remove_commit_signatures.py  # Git filter-branch utility
│
├── deployment/                   # Linux deployment automation
│   ├── autogen-trading-scheduler.service  # systemd service definition
│   ├── install_scheduler.sh     # Installation script for systemd
│   └── setup_cron.sh            # Alternative cron-based setup
│
└── research/                    # Documented experiments and analysis
    ├── configuration_system/    # Config system demonstrations
    ├── exit_strategy_analysis/  # Exit strategy performance research
    ├── v0_v4_analysis/         # V0-V4 sentiment research results
    │   └── generate_results_summary.py  # V0-V4 results summary generator
    └── README.md               # Research documentation
```

## Production Utilities

### SQLite Cache Manager (`utilities/cache_manager.py`)

Administrative CLI for the SQLite trading cache:

```bash
# View cache statistics
python scripts/utilities/cache_manager.py stats

# Cleanup expired entries
python scripts/utilities/cache_manager.py cleanup

# Optimize database (reclaim space)
python scripts/utilities/cache_manager.py vacuum

# List all cached symbols
python scripts/utilities/cache_manager.py symbols

# Export data to JSON
python scripts/utilities/cache_manager.py export SPY --start 2025-01-01 --end 2025-12-31

# Clear specific symbol
python scripts/utilities/cache_manager.py clear --symbol SPY --confirm

# Query cache data
python scripts/utilities/cache_manager.py query SPY --start 2025-10-01 --end 2025-10-31
```

**Features**:

- Cache statistics and health monitoring
- Expired entry cleanup (TTL-based)
- Database vacuum for space reclamation
- Symbol listing and filtering
- Data export to JSON
- Selective or full cache clearing
- Interactive querying

### Code Quality Checker (`utilities/lint_check.py`)

Ruff-based linting for code quality validation:

```bash
# Check entire project
python scripts/utilities/lint_check.py

# Check specific path
python scripts/utilities/lint_check.py src/voting/

# Check specific file
python scripts/utilities/lint_check.py src/strategies/voter_strategy.py
```

**Features**:

- Import ordering validation (PEP 8)
- Line length checks (configurable)
- Unused import detection
- Integration with pyproject.toml configuration
- Auto-fix suggestions

### Git Signature Remover (`utilities/remove_commit_signatures.py`)

Remove Claude Code signatures from commit messages:

```bash
# Use with git filter-branch
git filter-branch -f --msg-filter 'python scripts/utilities/remove_commit_signatures.py' HEAD~10..HEAD
```

**Use Case**: Clean up commit history by removing automated signature lines.

## Linux Deployment

### systemd Service (`deployment/autogen-trading-scheduler.service`)

systemd unit file for running the trading scheduler as a service:

```bash
# Install service
sudo bash scripts/deployment/install_scheduler.sh

# Check status
sudo systemctl status autogen-trading-scheduler

# View logs
sudo journalctl -u autogen-trading-scheduler -f
```

### Cron Setup (`deployment/setup_cron.sh`)

Alternative cron-based scheduling:

```bash
# Setup cron jobs
bash scripts/deployment/setup_cron.sh
```

**Schedule**: Runs morning and evening routines automatically.

## Research Scripts

### V0-V4 Sentiment Analysis (`research/v0_v4_analysis/`)

Generate comprehensive results summary for V0-V4 sentiment framework backtests:

```bash
# Basic summary (default)
python scripts/research/v0_v4_analysis/generate_results_summary.py

# Advanced metrics analysis
python scripts/research/v0_v4_analysis/generate_results_summary.py --advanced
```

**Advanced Metrics Include**:

- Sentiment effectiveness analysis
- Risk-adjusted performance metrics
- Market regime analysis
- Trade quality metrics
- Execution cost modeling
- Cost efficiency rankings

**Note**: V0-V4 framework is deprecated in favor of the MACD+RSI voting signal (`VotingSignal`), but research results preserved for reference.

### Configuration System Demos (`research/configuration_system/`)

Demonstrations of flexible parameter management via YAML configs:

```bash
python scripts/research/configuration_system/config_usage_demo.py
```

**Purpose**: Show how to change trading parameters without code modifications (Issue #303).

### Exit Strategy Analysis (`research/exit_strategy_analysis/`)

Performance and expected value analysis for exit strategies:

```bash
python scripts/research/exit_strategy_analysis/performance_clarification.py
python scripts/research/exit_strategy_analysis/expected_value_analysis.py
```

**Key Finding**: Conservative exits (6%/8%) have negative expected value. Use Balanced exits (8%/5%) for optimal returns.

## Deprecated Scripts

One-time migration scripts have been removed from the repository (Issue #435). Historical deprecated scripts are gitignored but documented below for reference.

### Removed: Migration Scripts (formerly `src/deprecated/scripts_migrations/`)

These scripts completed their purpose and were removed in Issue #435:

- **cleanup_legacy_cache.py** - Post-migration JSON cache cleanup (completed)
- **migrate_cache_to_sqlite.py** - JSON→SQLite migration (completed)
- **migrate_help_to_yaml.py** - help_system.py→YAML migration (completed)

### Gitignored: Forward Testing Scripts (`src/deprecated/scripts_forward_test_issue_324/`)

- **example_forward_test.py** - Demo for unused forward test framework
- **forward_test_runner.py** - CLI for unused forward test infrastructure

**Status**: Issue #324 infrastructure built but never integrated. Gitignored.

### Gitignored: V0-V4 Research Scripts (`src/deprecated/scripts_v0_v4_research/`)

- **backtest.py** - Continuous backtesting for V0-V4 sentiment agents
- **obfuscation_test.py** - V4 date obfuscation validation testing
- **collect_benchmark_data.py** - VXX/SPY/QQQ data collector
- **polygon_data_collector.py** - MAG 7 + leveraged ETFs collector (Polygon was removed in Oct 2026, #607)

**Status**: V0-V4 sentiment framework deprecated in favor of simpler MACD+RSI voting strategy. Gitignored.

## Cross-Platform Notes

All scripts support both Windows and Linux environments:

- Windows: Use `python scripts/utilities/cache_manager.py`
- Linux: Use `python3 scripts/utilities/cache_manager.py` or make executable with shebang

## Development Workflow

**Before committing**:

```bash
# Run linting check
python scripts/utilities/lint_check.py src/

# Fix auto-fixable issues
ruff check --fix src/
```

**Cache maintenance**:

```bash
# Weekly: Check cache health
python scripts/utilities/cache_manager.py stats

# Monthly: Cleanup expired entries
python scripts/utilities/cache_manager.py cleanup --vacuum
```

**Research workflow**:

```bash
# After backtesting V0-V4: Generate summary
python scripts/research/v0_v4_analysis/generate_results_summary.py --advanced
```

## Related Documentation

- **Cache System**: `docs/02_architecture/04_cache_system.md`
- **Development Guide**: `docs/04_development/04_cache_developer_guide.md`
- **Deployment**: `docs/03_deployment/` (if available)
- **Research Results**: `docs/archived/experiments/`

## Contributing

When adding new scripts:

1. Place in appropriate subdirectory (utilities/, deployment/, research/)
2. Add shebang line: `#!/usr/bin/env python3`
3. Include docstring with purpose and usage examples
4. Update this README with script description
5. Follow existing naming conventions

For research scripts:

- Create dedicated subdirectory in `research/`
- Include README explaining experiment purpose
- Document key findings and results
- Link to related issues/PRs
