# CLI Integration Status - New Features (#415, #416, #333)

**Date**: December 1, 2025
**Components**: ApprovedTickersManager, PositionSizer, PortfolioManager

---

## 📊 Current Status

### ✅ Implemented (Phase 1 Complete)

- **ApprovedTickersManager** (#415) - SQLite database for ticker management
- **PositionSizer** (#416) - Profile-based position sizing
- **PortfolioManager** (#333) - Pre-trade risk assessment

### ❌ NOT Yet Integrated into CLI

The new components exist but are **not connected** to the interactive CLI (`python main.py`).

**Current CLI Flow:**

```text
User Input → LLMParser → RealVoterStrategy → SimpleRiskManager → AlpacaExecutionManager
```

**What's Missing:**

- `SimpleRiskManager` does NOT use `PortfolioManager` or `PositionSizer`
- No ticker approval checking via `ApprovedTickersManager`
- No pre-trade risk assessment display
- No portfolio allocation warnings

---

## 🔌 Integration Needed

### 1. Update `SimpleRiskManager` to use new components

**File**: `src/risk/simple_risk_manager.py`

**Changes Needed:**

```python
from src.trading.portfolio_manager import PortfolioManager
from src.trading.position_sizer import PositionSizer
from src.trading.approved_tickers import ApprovedTickersManager

class SimpleRiskManager(RiskManager):
    def __init__(self, ...):
        # Add new managers
        self.portfolio_manager = PortfolioManager()
        self.position_sizer = PositionSizer()
        self.ticker_manager = ApprovedTickersManager()

    async def assess(self, request, analysis, user_id):
        # 1. Check ticker approval
        if not self.ticker_manager.is_approved(request.ticker):
            raise ValueError(f"{request.ticker} is not on approved list")

        # 2. Use PortfolioManager for pre-trade assessment
        assessment = self.portfolio_manager.assess_trade(
            symbol=request.ticker,
            current_price=request.entry_price or analysis.current_price,
            mode=self.current_mode
        )

        # 3. Use PositionSizer for quantity calculation
        if assessment.approved:
            size_result = self.position_sizer.calculate_position_size(
                symbol=request.ticker,
                current_price=assessment.entry_price,
                portfolio_value=portfolio_value,
                buying_power=buying_power,
                mode=self.current_mode
            )
            recommended_quantity = size_result.shares

        # 4. Return RiskAssessment with warnings
        return RiskAssessment(
            approved=assessment.approved,
            recommended_quantity=recommended_quantity,
            warnings=assessment.warnings,
            ...
        )
```

### 2. Add CLI Commands for Ticker Management

**File**: `src/cli/cli_session.py` (add new commands)

```python
# Inside CLISession class
def _handle_ticker_commands(self, user_input: str) -> bool:
    """Handle ticker management commands."""
    lower = user_input.lower()

    if "add ticker" in lower or "approve ticker" in lower:
        # Extract ticker symbol
        ticker = self._extract_ticker(user_input)
        self.ticker_manager.add_ticker(ticker)
        print(f"✅ {ticker} added to approved list")
        return True

    elif "list tickers" in lower or "show approved" in lower:
        tickers = self.ticker_manager.list_tickers()
        print("\n📋 Approved Tickers:")
        for t in tickers:
            print(f"  • {t.symbol} - {t.status}")
        return True

    return False
```

### 3. Display Portfolio Assessment in Decision Formatter

**File**: `src/cli/decision_formatter.py`

**Add to format output:**

```python
def format_suggestion(suggestion, assessment):
    """Format trade suggestion with portfolio context."""

    # ... existing formatting ...

    # Add portfolio context
    if hasattr(assessment, 'portfolio_allocation'):
        print("\n📊 Portfolio Impact:")
        print(f"  Position Size: {assessment.portfolio_pct:.1f}% of portfolio")
        print(f"  Total Exposure: {assessment.total_exposure_pct:.1f}%")

        if assessment.warnings:
            print("\n⚠️  Warnings:")
            for warning in assessment.warnings:
                print(f"  • {warning}")
```

---

## 🧪 Test Prompts (Once Integrated)

### Basic Ticker Management

```bash
# Launch CLI
python main.py

# Test ticker approval
> add ticker AAPL
> add ticker NVDA
> list tickers
> remove ticker AAPL

# Try unapproved ticker (should fail)
> buy RANDOM_TICKER
```

### Position Sizing Tests

```bash
# Conservative mode (5% per position)
> set mode conservative
> review AAPL at 185.50

# Moderate mode (10% per position)
> set mode moderate
> review NVDA at 880.00

# Aggressive mode (20% per position)
> set mode aggressive
> review SPY at 600.00
```

### Portfolio Assessment Tests

```bash
# Check portfolio warnings
> show portfolio status
> review AAPL at 185.50
# Should show: position size %, total exposure %, warnings

# Test exposure limits
> buy AAPL 100 shares
> buy MSFT 50 shares
> buy NVDA 30 shares
# Should warn when approaching 80% max exposure
```

### Multi-Account Tests (#401)

```bash
# List accounts
> list accounts

# Switch account
> switch to paper_main

# Show current account
> show current account

# Trade with specific account context
> buy AAPL 10 shares
```

---

## 🎯 Integration Checklist

**Risk Manager Integration:**

- [ ] Import PortfolioManager, PositionSizer, ApprovedTickersManager
- [ ] Update `SimpleRiskManager.__init__()` to create instances
- [ ] Update `SimpleRiskManager.assess()` to use new components
- [ ] Pass trading mode to PortfolioManager and PositionSizer
- [ ] Return warnings in RiskAssessment

**CLI Commands:**

- [ ] Add ticker management commands (add/remove/list)
- [ ] Add portfolio status command
- [ ] Add mode switching command (already exists via #400)
- [ ] Update help system with new commands

**Display Formatting:**

- [ ] Show portfolio allocation % in suggestions
- [ ] Display warnings prominently
- [ ] Show ticker approval status
- [ ] Format portfolio summary

**Testing:**

- [ ] Unit tests for integrated flow
- [ ] Integration test with real CLI
- [ ] Test all trading modes
- [ ] Test ticker approval flow

---

## 📝 Tips and Manual Commands

### Current Working Commands (Before Integration)

```bash
# Trading modes (Issue #400 - WORKING)
python main.py
> set mode conservative
> set mode moderate
> set mode aggressive
> show mode

# Account management (Issue #401 - WORKING)
> accounts              # List all trading accounts
> list accounts         # List all trading accounts
> switch to paper_main  # Switch active account
> show current account  # Show current account
> refresh accounts      # Re-discover accounts

# Important distinction:
> accounts   → Show trading accounts (Alpaca paper/live)
> account    → Show portfolio positions (your holdings)

# Timeframe commands (WORKING)
> set timeframe 1d
> set timeframe 4h
> show timeframe

# Trailing stops (Issue #321 - WORKING)
> show stops
> update stops AAPL
```

### New Commands (NOT WORKING - Need Integration)

```bash
# These will NOT work until integration is complete:
> add ticker AAPL           # Not connected
> list tickers              # Not connected
> show portfolio status     # Not connected
```

---

## 🚀 Next Steps

1. **Immediate**: Create integration branch
2. **Update**: Modify `SimpleRiskManager` to use new components
3. **Add**: CLI commands for ticker management
4. **Test**: End-to-end flow with approved tickers
5. **Document**: Update user guide with new commands

**Estimated Effort**: 2-3 hours for basic integration
