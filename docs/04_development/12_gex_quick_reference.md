# GEX VoterAgent Integration - Quick Reference

> **Historical plan.** This predates the removal of the agents (#331). `VoterAgent` is now `VotingSignal` (`src/trading/instruments/voting_signal.py`), and GEX overlays were tested and rejected ([re-validation](../08_research/02_gex_research/regime_lookahead_revalidation.md)).

## Issue #419 Summary

**Goal**: Add Gamma Exposure (GEX) as a third voting signal to VoterAgent, creating a triple-voting system (MACD+RSI+GEX).

**Status**: Planning Complete ✅
**Complexity**: High (Options data + voting redesign)
**Effort**: 8-12 weeks (4 phases)
**Priority**: P1 (High)

---

## Triple Voting System At A Glance

```text
VOTER 1: MACD (13/34/8)          → BUY/SELL/HOLD + Confidence
VOTER 2: RSI (14, 30/70)         → BUY/SELL/HOLD + Confidence
VOTER 3: GEX (Options Microstructure) → BUY/SELL/HOLD + Confidence
         ↓
    Triple Voting Logic (Consensus)
         ↓
    Final Decision: BUY/SELL/HOLD + Position Size
```

### Consensus Rules

| Votes | Signal Type | Confidence | Position Size | Action |
|-------|-------------|-----------|---------------|--------|
| 3/3 agree | STRONG_TRIPLE | 85% | 100% | Full position |
| 2/3 agree | MODERATE_DOUBLE | 65% | 70% | Reduced position |
| 1/3 agree | WEAK_SINGLE | 45% | 40% | Small position |
| Conflict | NO_CONSENSUS | 20% | 0% | HOLD (no trade) |

---

## GEX Signal Source

### What is GEX?

**Gamma Exposure (GEX)** measures dealer hedging obligations in the options market.

- **Positive GEX**: Dealers are long gamma (volatility-suppressing position)
- **Negative GEX**: Dealers are short gamma (volatility-amplifying position)
- **Zero GEX**: Gamma flip point (regime shift risk zone)

### GEX Calculation

```text
GEX = Σ(Gamma × OpenInterest × 100 × Spot² × Direction)

Direction: +1 for calls, -1 for puts
Result: Shows dealer net positioning
```

### GEX Signal Generation

1. Calculate total GEX from options chain
2. Find zero-gamma level (price where GEX = 0)
3. Classify dealer positioning:
   - **LONG_GAMMA**: GEX > +500M (stable environment)
   - **SHORT_GAMMA**: GEX < -500M (volatile environment)
   - **NEUTRAL**: GEX near zero (fragile/uncertain)
4. Generate signal:
   - **BUY**: When dealers are LONG gamma (protective)
   - **SELL**: When dealers are SHORT gamma (hedging upside)
   - **HOLD**: When positioning is neutral

---

## Implementation Phases

### Phase 1: GEX Signal Generator (Weeks 1-3)

**File**: `src/trading_tools/gex_signal_generator.py`

```python
class GEXSignalGenerator:
    def generate_signal(symbol, options_chain, current_price) -> Dict
    def _calculate_gex_total(options_chain) -> float
    def _find_zero_gamma_level(options_chain) -> float
    def _classify_dealer_positioning(gex_total) -> str
    def _estimate_volatility_expectation(...) -> str
```

### Phase 2: VoterAgent Integration (Weeks 4-6)

**File**: `src/autogen_agents/voter_agent.py` (enhanced)

```python
class VoterAgent:
    def __init__(self, use_gex=False, gex_params=None, ...)
    def evaluate_voting(symbol, price_data, options_chain=None) -> Dict
    def _get_gex_signal(symbol, options_chain) -> Dict
    def _perform_triple_voting(macd, rsi, gex, symbol, prices) -> Dict
```

**Key Design**:

- Default: `use_gex=False` (dual voting, backward compatible)
- Opt-in: `use_gex=True` (triple voting with GEX)

### Phase 3: Integration Testing (Weeks 7-9)

- Mock options data tests
- Real Alpaca options API tests
- Error handling & edge cases
- Backward compatibility validation

### Phase 4: Backtesting & Validation (Weeks 10-12)

- Historical options data (2024-2025)
- Performance comparison (dual vs triple)
- Parameter tuning
- Paper trading validation
- Production deployment

---

## Files & Documentation

### Created ✅

- **Issue #419**: Full specification on GitHub
- **docs/04_development/gex_voter_integration_guide.md**: Comprehensive implementation guide (41 KB)
- **This file**: Quick reference card

### Will Be Created (Implementation)

- `src/trading_tools/gex_signal_generator.py` (new)
- `src/autogen_agents/voter_agent.py` (enhanced)
- `tests/unit/trading_tools/test_gex_signal_generator.py` (new)
- `tests/unit/agents/test_voter_agent_triple_voting.py` (new)
- `tests/integration/test_voter_gex_integration.py` (new)
- `config_defaults/voting_config.yaml` (new/updated)

---

## Success Metrics

| Metric | Target | Why It Matters |
|--------|--------|---|
| **Sharpe Ratio** | > 0.69 | Beat the live-rules baseline (5 bps a side, #574) |
| **Win Rate** | ≥55% | Profitable majority of trades |
| **GEX Precision** | ≥70% | Most GEX signals are profitable |
| **Max Drawdown** | ≤20% | Risk stays acceptable |
| **Unit Test Coverage** | ≥90% | Code quality & reliability |
| **False Positive Rate** | ≤25% | Avoid excessive false signals |
| **Backtest Period** | 2024-2025 | Validate with 12+ months data |

---

## Key Design Decisions

### 1. Backward Compatibility ✅

- VoterAgent works WITHOUT GEX (default)
- Existing MACD+RSI logic unchanged
- Triple voting is opt-in feature

### 2. Consensus-Based ✅

- All three voters must agree for strong signal
- No single indicator dominates
- Reduces false positives & whipsaws

### 3. Options Data Integration ✅

- Uses existing UnifiedOptionsDataTool
- Provider fallback (Alpaca → Alpha Vantage)
- Built-in caching via TradingCacheManager

### 4. Graceful Degradation ✅

- If no options_chain → falls back to dual voting
- If GEX fails → still uses MACD+RSI
- No hard dependency on options data availability

---

## Related Issues & References

### Dependencies

- **#352**: GEX Infrastructure Foundation (GEX calculation methods)
- **#330**: Options Analysis Support (options data infrastructure)

### Related Research

- **#367**: Advanced GEX Regime Detection (volatility cycles & fragility)
- **#394**: Forward Testing Metrics (GEX vs traditional technicals comparison)
- **#395**: Multi-Timeframe Ranked Voting (related voting enhancements)

### External References

- @TailThatWagsDog (Twitter/X): Gamma exposure analysis
- SpotGamma: Dealer positioning dashboards
- SqueezeMetrics: GEX methodology research

---

## Quick Start Commands (After Implementation)

### Enable GEX Voting

```python
from src.autogen_agents.voter_agent import VoterAgent

# Create voter with GEX enabled
voter = VoterAgent(use_gex=True)

# Evaluate with options data
result = voter.evaluate_voting(
    symbol='SPY',
    price_data=df_prices,
    options_chain=df_options  # Required for GEX
)

print(f"Action: {result['action']}")
print(f"Confidence: {result['confidence']:.2%}")
print(f"Signal Type: {result['signal_type']}")
print(f"Votes: {result['votes']}")  # See each voter's vote
```

### CLI Usage (After CLI Integration)

```bash
# Trade with GEX enabled
python main.py trade-assist --use-gex

# Or use default (legacy MACD+RSI only)
python main.py trade-assist
```

---

## Common Questions

**Q: Will this break my existing trading system?**
A: No. GEX is opt-in. Default behavior unchanged. Set `use_gex=False` (default).

**Q: What if Alpaca options API is not available?**
A: System falls back to dual voting (MACD+RSI). GEX is optional, not required.

**Q: How often should GEX be calculated?**
A: Once per trading day minimum. Can be more frequent (hourly) if options data available.

**Q: Can I use GEX with my existing backtest data?**
A: Need historical options chain data (gamma, OI, volume). Not available from standard price feeds.

**Q: What if GEX signals conflict with MACD+RSI?**
A: That's the point! Consensus-based approach reduces false signals. Only trade on 2/3+ agreement.

---

## Troubleshooting Guide

### Issue: "No options data available"

**Cause**: UnifiedOptionsDataTool can't fetch options chain
**Solution**:

1. Check Alpaca API credentials
2. Verify account has options approval
3. Check network connectivity
4. Review logs: `src/data_sources/sources/market/unified_options_tool.py`

### Issue: "GEX calculations seem incorrect"

**Cause**: Bad data in options chain
**Solution**:

1. Validate DataFrame columns exist (gamma, open_interest, option_type)
2. Check spot_price is populated
3. Verify gamma values in [0, 1] range
4. Look at data quality score in cache metadata

### Issue: "Triple voting produces different results than dual"

**Cause**: GEX signal conflicts with MACD/RSI consensus
**Solution**: This is expected behavior

1. Review GEX signal details (`return_components=True`)
2. Check zero gamma level vs current price
3. Verify dealer positioning classification
4. Consider if GEX is providing useful contraindication

---

## Next Steps

1. **Review Planning** (Your job)
   - [ ] Read Issue #419
   - [ ] Read implementation guide (gex_voter_integration_guide.md)
   - [ ] Clarify any questions

2. **Verify Dependencies** (Your job)
   - [ ] Check #352 status (GEX foundation)
   - [ ] Confirm Alpaca options API available
   - [ ] Check historical options data sources

3. **Begin Implementation** (When ready)
   - [ ] Start Phase 1: GEXSignalGenerator
   - [ ] Create GitHub project milestone
   - [ ] Set up branch: `feature/gex-voter-integration`
   - [ ] Link PRs to Issue #419

---

## Resources

- **Full Implementation Guide**: [docs/04_development/gex_voter_integration_guide.md](gex_voter_integration_guide.md)
- **GitHub Issue**: [#419 Add GEX as VoterAgent Signal Source](https://github.com/iAmGiG/OptionsCommander/issues/419)
- **VoterAgent Code**: [src/autogen_agents/voter_agent.py](../../src/autogen_agents/voter_agent.py)
- **Options Tools**: [src/data_sources/sources/market/unified_options_tool.py](../../src/data_sources/sources/market/unified_options_tool.py)

---

**Last Updated**: 2025-11-30
**Status**: Ready for Implementation
**Complexity**: High | **Priority**: P1 | **Effort**: 8-12 weeks
