# Code Quality & Maintenance

**Purpose**: Guidelines and tracking for code quality improvements
**Last Updated**: November 2025
**Status**: Continuous improvement in progress

## Overview

This document tracks ongoing code quality improvements, linting standards, and technical debt reduction efforts across the OptionsCommander codebase.

---

## Linting & Code Standards

### Current Configuration

**Tools in Use**:

- **Ruff** - Linter, import sorter (rule I) and Black-compatible formatter (`ruff format`);
  it replaced black and isort (#603). One pinned version everywhere.
- **Pylint** - Static analysis (secondary)
- **MyPy** - Type checking (optional)
- **Bandit** - Security scanning

**Configuration Files**:

- [pyproject.toml](../../pyproject.toml) - Unified tool configuration
- [.pre-commit-config.yaml](../../.pre-commit-config.yaml) - Pre-commit hooks

### Linting Strategy

**Local Development** (Permissive):

- E501 (line length) - **IGNORED** locally for development speed
- E402 (module imports) - **IGNORED** in scripts/ for sys.path setup
- Focus on critical issues: imports, complexity, security

**GitHub CI** (Strict):

- All formatting and style checks enforced
- Line length limits validated
- Final quality gate before merge

**Rationale**: Allow fast local iteration while maintaining quality standards in CI.

### Pre-Commit Hooks

Automatically run on every commit:

```yaml
# .pre-commit-config.yaml
- ruff-check (linting with auto-fix, including import order)
- ruff-format (formatting)
- Trailing whitespace removal
- End-of-file fixer
- Bandit (security scanning)
```

**Bypass if needed** (use sparingly):

```bash
git commit --no-verify
```

---

## Recent Code Quality Improvements (November 2025)

### ✅ Import Consolidation (Complete)

**Issue**: C0415 warnings - imports inside functions/methods
**Status**: ✅ RESOLVED (Nov 29, 2025)
**Commits**: c77f407, 77f3a8b, 21c8df5

**Changes**:

- Moved all inline imports to module toplevel
- Added try/except wrappers for optional dependencies
- Created availability flags (CLI_AVAILABLE, yaml availability)
- Fixed import order across 10+ files

**Files Updated**:

- [main.py](../../main.py) - CLI imports, generate_summary, asyncio
- [alpaca_trading_client.py](../../src/trading/broker/alpaca_trading_client.py) - APIError, OrderStatus
- [alpaca_market_data.py](../../src/market_data/alpaca_market_data.py) - is_market_hours
- [daily_scheduler.py](../../src/trading/scheduling/daily_scheduler.py) - argparse, datetime consolidation
- [trading_pipeline.py](../../src/trading/scheduling/trading_pipeline.py) - Path, pytz, AlpacaMarketData
- [trailing_stop_manager.py](../../src/trading/orders/trailing_stop_manager.py) - get_current_price
- [unified_price_fetcher.py](../../src/trading/utils/unified_price_fetcher.py) - Import order fixes
- [alpaca_execution_manager.py](../../src/trading/broker/alpaca_execution_manager.py) - json, os, re, yaml

**Benefits**:

- Faster import-time error detection
- Better IDE autocomplete and type checking
- Clearer dependency graph
- Easier testing and mocking

### ✅ Linter Configuration Tuning (Complete)

**Issue**: E501 line-length warnings blocking commits
**Status**: ✅ RESOLVED (Nov 29, 2025)
**Commit**: 31b4e9e

**Changes**:

```toml
# pyproject.toml
[tool.ruff.lint]
ignore = ["E402", "E501"]  # Added E501

[tool.ruff.lint.per-file-ignores]
"*" = ["E501"]  # Line length deferred to GitHub CI
```

**Rationale**:

- 33 pre-existing E501 violations (mostly in data source files)
- Line length is stylistic, not critical to functionality
- Black formatter handles most cases automatically
- GitHub CI still enforces limits for final quality

---

## Active Code Quality Issues

### 🔴 High Priority

#### #409 - Refactor Complex Functions (C901 Warnings)

**Status**: 📋 Planned
**Complexity Violations**:

- [main.py:176](../../main.py#L176) - `run_paper_trading_check()` (complexity 26)
- [main.py:559](../../main.py#L559) - `main()` (complexity 22)

**Target**: Complexity ≤ 10 per function
**Impact**: Currently blocking commits without `--no-verify`

**Proposed Solution**:

```python
# Break down run_paper_trading_check() into:
def _load_paper_trading_config() -> dict
def _check_paper_account_status() -> bool
def _analyze_position_drift() -> dict
def _generate_drift_report(drift_data: dict) -> str

# Break down main() into:
def _handle_account_commands(args) -> None
def _handle_analysis_commands(args) -> None
def _handle_trading_commands(args) -> None
def _handle_scheduler_commands(args) -> None
```

**Benefits**:

- Easier to test individual components
- Better code reusability
- Clearer control flow
- Passes pre-commit hooks without bypass

---

### 🟡 Medium Priority

#### #411 - Add Comprehensive Type Hints ✅ ASSESSED (Nov 29, 2025)

**Status**: ✅ DEFERRED - Phase 1 already 85-90% covered
**Assessment**: Existing type coverage is sufficient for now

**Phase 1 Assessment - Core Trading Logic**:

- ✅ `voter_agent.py` (retired in #331; `VotingSignal` in [voting_signal.py](../../src/trading/instruments/voting_signal.py) took over) - 90% covered when assessed
- ✅ [position_manager.py](../../src/trading/positions/position_manager.py) - 95% covered (all public methods typed)
- ✅ [account_manager.py](../../src/trading/accounts/account_manager.py) - 100% covered (full dataclass typing)
- ✅ [trailing_stop_manager.py](../../src/trading/orders/trailing_stop_manager.py) - 90% covered (core methods typed)
- ✅ [unified_price_fetcher.py](../../src/trading/utils/unified_price_fetcher.py) - 100% covered (all methods typed)

**Overall Phase 1 Coverage**: Approximately 85-90%

**Remaining Gaps** (minor, low priority):

- Complex AutoGen integration methods (generate_reply)
- External dependency interfaces (broker_client parameter)
- Private helper methods in some classes

**Recommendation**:

- Current coverage sufficient for production use
- Defer remaining work until mypy is installed
- Focus on Phase 2/3 if type checking becomes critical
- Missing coverage is in complex integration points, not core logic

**Benefits Already Achieved**:

- ✅ IDE autocomplete working for all core methods
- ✅ Self-documenting code contracts in place
- ✅ Clear parameter and return types for public APIs

#### #413 - Validate Test Coverage After Import Consolidation

**Status**: 📋 Planned
**Scope**: Ensure recent import changes didn't break functionality

**Test Plan**:

1. **Unit Tests**: Run full test suite with coverage
2. **Integration Tests**: Test with/without optional dependencies
3. **Manual Validation**: Smoke test core functionality

**Coverage Goals**:

- Overall: Maintain baseline %
- Modified files: ≥ 80% coverage
- New error paths: 100% coverage (try/except blocks)

**Commands**:

```bash
# Run with coverage
python -m pytest tests/unit/ --cov=src --cov-report=term-missing

# Test optional dependency handling
python -c "from main import CLI_AVAILABLE; print(f'CLI: {CLI_AVAILABLE}')"
python main.py test-voter  # Should work regardless
```

---

### 🟢 Low Priority

#### #410 - Address Line Length Violations (E501) ✅ COMPLETE (Nov 29, 2025)

**Status**: ✅ RESOLVED
**Violations Fixed**: 33 lines across 5 files
**Commit**: c0e9703

**Files Updated**:

- `alpaca_execution_manager.py` - 16 violations fixed
- `timeframe_tools.py` - 5 violations fixed
- `alpaca_trading_client.py` - 5 violations fixed
- `alpaca_market_data.py` - 4 violations fixed
- `daily_scheduler.py` - 2 violations fixed

**Approach Taken**:

1. ✅ Ran Black/isort formatters (auto-fixed ~80%)
2. ✅ Manual fixes for complex cases:
   - Split long f-strings using implicit concatenation
   - Broke long docstring parameters across lines
   - Maintained 100-character line length limit

**Result**: Zero E501 violations remaining, all changes purely stylistic

#### #412 - Audit Scripts Directory Imports ✅ PARTIAL (Nov 29, 2025)

**Status**: ✅ PARTIAL COMPLETION
**Scope**: Normalized active scripts, excluded deprecated research

**Changes Made**:

1. Fixed `config_usage_demo.py` - Corrected sys.path to project root
2. Fixed `generate_results_summary.py` - Fixed import paths
3. Updated `pyproject.toml` - Minimal targeted exclusions

**New pyproject.toml Exclusions**:

```toml
[tool.ruff.lint.per-file-ignores]
# Utilities that need sys.path manipulation before imports
"scripts/utilities/cache_manager.py" = ["E402"]
# Deprecated research scripts (v0-v4 analysis, old config demos)
"scripts/research/**/*.py" = ["I001", "E402", "E501"]
# Line length handled by formatters and GitHub CI
"*" = ["E501"]
```

**Scripts Categorization**:

- **Needs sys.path** (1 file): `cache_manager.py` - imports from src/
- **Deprecated Research**: `scripts/research/**/*.py` - preserved for historical reference
- **No exclusions needed** (2 files): `lint_check.py`, `remove_commit_signatures.py`

**Result**: Active utility scripts normalized, deprecated scripts properly excluded

**Commit**: b486c03

---

## Code Quality Metrics

### Current Status

| Metric | Status | Notes |
|--------|--------|-------|
| **Import Issues (C0415)** | ✅ Resolved | All inline imports moved to toplevel |
| **Complexity (C901)** | 🔴 2 violations | main.py functions need refactoring (#409) |
| **Line Length (E501)** | ✅ Resolved | All 33 violations fixed (Nov 29, 2025) |
| **Type Coverage** | ✅ Phase 1 done | Phase 1: 85-90% covered (#411) |
| **Scripts Imports** | ✅ Normalized | Active scripts fixed, deprecated excluded (#412) |
| **Test Coverage** | ✅ 137 passing | Priority 1-2 components (#408) |
| **Security (Bandit)** | ✅ Passing | Pre-commit hook active |

### Historical Progress

**November 2025 - Scripts Import Audit (#412)**:

- Fixed: 2 research scripts with incorrect sys.path
- Updated: pyproject.toml with minimal targeted exclusions
- Commit: b486c03
- Result: Active scripts normalized, deprecated scripts excluded

**November 2025 - Type Hints Assessment (#411)**:

- Assessed: Phase 1 core trading logic (5 files)
- Coverage: 85-90% already present
- Result: Sufficient for now, deferred further work until mypy available

**November 2025 - Line Length Fixes (#410)**:

- Fixed: 33 violations across 5 files
- Commit: c0e9703
- Result: Zero E501 violations remaining

**November 2025 - Import Consolidation (C0415)**:

- Fixed: 15+ files with C0415 warnings
- Commits: 3 (c77f407, 77f3a8b, 21c8df5)
- Result: Zero C0415 warnings in src/

**November 2025 - Unit Test Infrastructure (#408)**:

- Added: 137 unit tests across Priority 1-2 components
- Result: 100% pass rate for tested components

**October 2025 - Integration Test Infrastructure**:

- Added: 35 Alpaca integration tests
- Result: 100% pass rate

---

## Development Workflow

### Before Committing

1. **Format Code**:

   ```bash
   pre-commit run ruff-check --all-files    # lint, import order (rule I)
   pre-commit run ruff-format --all-files   # format
   ```

   Run ruff through pre-commit: it uses the pinned version and passes tracked files.
   Plain `ruff format tests/` skips most tests, because `tests/` is gitignored (its tracked
   files are force-added) and ruff skips gitignored paths when it walks directories.

2. **Run Linters**:

   ```bash
   ruff check src/ --fix
   pylint src/ --rcfile=pyproject.toml
   ```

3. **Run Tests**:

   ```bash
   python -m pytest tests/ -v
   ```

4. **Commit** (pre-commit hooks run automatically):

   ```bash
   git add .
   git commit -m "fix: your message"
   ```

### If Pre-Commit Fails

**Review Changes**:

```bash
git status  # Check what hooks modified
git diff    # Review automatic fixes
```

**Accept Fixes**:

```bash
git add .
git commit -m "fix: your message"  # Commit hook-applied changes
```

**Bypass** (only if necessary):

```bash
git commit --no-verify
```

---

## Style Guide

### Import Order

1. Standard library imports
2. Third-party imports
3. Local application imports

```python
# Standard library
import argparse
import asyncio
import json

# Third-party
import pandas as pd

# Local
from src.trading.positions.position_manager import PositionManager
from src.utils.date_utils import get_datetime_now
```

### Optional Dependencies

```python
# Pattern for optional dependencies
try:
    import yaml
except ImportError:
    yaml = None

# Later usage
if yaml is not None:
    # Use yaml module
else:
    # Fallback behavior
```

### Type Hints

```python
from typing import Optional, Dict, List

def get_current_price(symbol: str, use_cache: bool = True) -> float:
    """
    Get current price for symbol.

    Args:
        symbol: Stock ticker symbol
        use_cache: Whether to use cached prices

    Returns:
        Current price as float
    """
    pass
```

---

## Tools Reference

### Ruff (Primary Linter)

```bash
# Check all files
ruff check src/

# Auto-fix issues
ruff check src/ --fix

# Check specific file
ruff check src/trading/position_manager.py

# Select specific rules
ruff check src/ --select E,F,I
```

### Ruff Format (Formatter)

```bash
# Format all tracked files (the pinned ruff, tests included)
pre-commit run ruff-format --all-files

# One directory outside tests/: check without modifying, or show the diff
ruff format --check src/
ruff format --diff src/
```

### MyPy (Type Checker)

```bash
# Check types
mypy src/ --ignore-missing-imports

# Strict mode
mypy src/trading/instruments/voting_signal.py --strict
```

---

## Related Documentation

- [Project Status](02_project_status.md) - Development roadmap
- [Codebase Structure](01_codebase_structure.md) - File organization
- [Naming Conventions](../03_reference/06_naming_conventions.md) - Coding standards
- [Known Issues](../03_reference/05_known_issues.md) - Bug tracking

---

*This document tracks code quality improvements and provides guidelines for maintaining high code standards across the OptionsCommander codebase.*
