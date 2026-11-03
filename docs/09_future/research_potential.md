# Research Potential

Ideas, papers, and techniques worth exploring in future phases.

> **Note**: Categories are elastic - add new ones as needed. Don't force-fit entries.

---

## Meta-Categories

| Category | Description |
| -------- | ----------- |
| **Multi-Agent Architecture** | Agent coordination, hierarchy, optimization |
| **Strategy Research** | Trading strategies, signals, timing |
| **Market Microstructure** | Order books, execution, spreads, market making concepts |
| **Risk & Portfolio** | Position sizing, hedging, drawdown control, risk metrics |
| **ML/AI Techniques** | Machine learning applications to trading |
| **Data & Signals** | Alternative data, signal processing |
| **Validation & Testing** | Backtesting improvements, statistical methods |

---

## Entry Template

```markdown
### Title

- **Source**: [Link] or "LinkedIn post" with date
- **Summary**: 2-3 sentences
- **Relevance**: How it applies to AutoTrader
- **Priority**: Low / Medium / High / Future
- **GH Potential**: Do research | Create issue | Skip till after [X] | Archive
- **Revisit When**: Trigger condition
```

---

## Multi-Agent Architecture

### BOAD: Hierarchical Agent Discovery via Bandit Optimization

- **Source**: [arXiv:2512.23631](https://arxiv.org/abs/2512.23631v1) (Dec 2025, preprint)
- **Authors**: Iris Xu, Guangtao Zeng, et al.
- **Summary**: Uses multi-armed bandit optimization to automatically discover effective agent hierarchies instead of manual design. Shows hierarchical multi-agent > monolith for complex tasks. Achieved 2nd place on SWE-bench-Live with 36B parameter system.
- **Relevance**: Validates specialized-component designs (the agents were retired in #331). Bandit optimization could select between `VotingSignal` variants per-regime.
- **Priority**: Future (Phase 3+)
- **GH Potential**: Skip till after multiple strategy variants exist
- **Revisit When**: Building multiple strategy variants that need automatic selection
- **Transferable Ideas**:
  - Bandit for agent/strategy selection
  - Hierarchy validation (Scanner/Voter/Risk/Executor pattern)
- **Non-Transferable**: SWE-specific implementation, code localization focus

---

## Strategy Research

### Derivatives Fundamentals: Pricing and Mechanics

- **Source**: LinkedIn carousel (Jan 2026)
- **Summary**: Comprehensive reference covering forwards, futures, vanilla options, Black-Scholes, Greeks, exotic options, and volatility products. Essential fundamentals for derivatives trading.
- **Relevance**: **Future Phase 4** - Options trading in roadmap (#400). Vol products (VIX) relevant for regime detection.
- **Priority**: Reference material (Phase 4 prerequisite)
- **GH Potential**: Skip till after options phase begins
- **Revisit When**: Starting options integration or VIX-based regime detection

#### Forward Contracts - OTC, Customizable, Counterparty Risk

| Feature | Specification |
| ------- | ------------- |
| Payout | S_T - K at maturity T |
| Pricing | `F = S₀ × e^((r-q)T)` |
| Risk | Bilateral credit risk |
| Liquidity | Zero (must unwind) |
| Use Case | Corporate hedging |

> "A Forward is just a Future without the clearinghouse."

#### Futures - Exchange Traded, Cleared, Standardized

| Feature | Specification |
| ------- | ------------- |
| Margin | Daily Mark-to-Market |
| Convexity | Futures rate > Fwd rate |
| Liquidity | High (Front month) |
| Roll Cost | Contango / Backwardation |
| Use Case | Macro / Basis Trading |

> "Convexity Adjustment: ≈ ½σ²T²"

#### European Options - Exercise at Maturity Only

**Black-Scholes Formulas**:

| Type | Formula |
| ---- | ------- |
| Call | `C = S₀N(d₁) - Ke^(-rT)N(d₂)` |
| Put | `P = Ke^(-rT)N(-d₂) - S₀N(-d₁)` |
| d₁ | `[ln(S₀/K) + (r + σ²/2)T] / (σ√T)` |
| d₂ | `d₁ - σ√T` |
| Parity | `C - P = S₀ - Ke^(-rT)` |

> "Assumption: Log-normal prices, constant vol, no jumps."

#### American Options - Early Exercise Premium

| Property | Implication |
| -------- | ----------- |
| Price | American ≥ European |
| Boundary | Critical price for early exercise |
| Dividends | Exercise before ex-date |
| Methods | Binomial Tree, LSMC |
| Use Case | Single Stocks, ETFs |

> "Rule: Never exercise an American Call early unless there is a dividend."

#### The Greeks (Not shown but implied)

| Greek | Measures | Hedging Use |
| ----- | -------- | ----------- |
| Delta (Δ) | Price sensitivity | Directional hedge |
| Gamma (Γ) | Delta sensitivity | Convexity risk |
| Theta (Θ) | Time decay | Carry cost |
| Vega (ν) | Vol sensitivity | Vol exposure |
| Rho (ρ) | Rate sensitivity | Interest rate risk |

#### Swaps - Fixed vs Floating Exchange

| Type | Valuation Logic |
| ---- | --------------- |
| IRS | Sum of discounted fixed-floating differences |
| CDS | Premium Leg = Protection Leg |
| Equity | Stock Return vs LIBOR |
| FX Swap | Spot + Fwd (Points) |

> "IRS Definition: Exchanging fixed cashflows for floating cashflows."

#### Barrier Options - Knock-In / Knock-Out

| Type | Condition |
| ---- | --------- |
| Up&Out | Dies if S_t > Barrier |
| Down&In | Alive only if S_t < Barrier |
| Rebate | Cash paid on hit |
| Sensitivity | High Vanna / Volga |
| Risk | Discontinuous Delta |

> "Barriers = Vanilla Option + Binary Option (at the barrier)."

#### Path Dependent - Average Price Options

| Type | Payoff Structure |
| ---- | ---------------- |
| Asian Call | max(Avg(S) - K, 0) |
| Lookback | S_T - min(S) over [0,T] |
| Volatility | σ_Asian ≈ σ/√3 |
| Fixing | Daily/Monthly Observations |
| Use Case | Smooth FX hedging |

> "Asian options are cheaper because averaging dampens volatility."

#### Volatility Products - Pure Vega/Variance

| Instrument | Key Formula |
| ---------- | ----------- |
| Var Swap | (252/N) × Σ(ln returns)² - K |
| VIX Fut | E^Q[Future Vol] |
| Vol Swap | √(Var Swap) - K_vol |
| Convexity | Var > Vol² |
| Hedge | Static strip of options |

> "Variance swaps have a convex payoff profile (Long Vol = Long Gamma)."

#### Why This Matters for AutoTrader

| Product | Relevance | Phase |
| ------- | --------- | ----- |
| Vanilla Options | Core for Phase 4 | #400 |
| Greeks | Position sizing/hedging | Phase 4 |
| VIX Futures | Regime detection (replaces V2 VXX) | Phase 2-3 |
| Exotics | Out of scope | N/A |
| Var Swaps | Academic interest only | N/A |

**Current Status**: `VotingSignal` trades equities only. Options mentioned in Trading Modes Phase 4.

---

## Market Microstructure

### Market Making Fundamentals: Why Instant Fills = Lost Money

- **Source**: LinkedIn carousel (Jan 2026)
- **Summary**: Mechanics of market making - liquidity provision, inventory risk, adverse selection, and Avellaneda-Stoikov pricing. Explains why market makers get paid for patience and how toxic flow creates losses.
- **Relevance**: **Background knowledge** - We're directional traders (takers), not market makers. Understanding this explains why we pay the spread and how execution quality matters.
- **Priority**: Reference material
- **GH Potential**: Archive as reference - not implementing MM strategy
- **Revisit When**: Optimizing execution or considering limit orders vs market orders

> **Key Insight**: "If your limit order gets filled immediately, you likely lost money."

#### The Core Model - Providing Liquidity

| Concept | Definition |
| ------- | ---------- |
| Bid Price | Price you buy at |
| Ask Price | Price you sell at |
| Spread | Ask - Bid |
| Mid-Price | (Ask + Bid) / 2 |
| Revenue | Capturing the spread |

> "Market Makers get paid for waiting. You sell patience (liquidity) to impatient traders."

#### The Limit Order Book - L1 vs L2

| Term | Mechanism |
| ---- | --------- |
| L1 Data | Best Bid and Best Ask |
| L2 Data | Full depth of book |
| Maker | Posts Limit Orders (Passive) |
| Taker | Sends Market Orders (Aggressive) |
| Queue | Price-Time Priority |

> "If you are not at the top of the queue, you do not get filled. Latency matters."

**Our Status**: Alpaca provides L1 data. We're primarily takers (market orders).

#### Inventory Risk - The Central Problem

| State | Risk Consequence |
| ----- | ---------------- |
| Long Inv. | Price drops → Loss |
| Short Inv. | Price rises → Loss |
| Goal | Flat (Zero Inventory) |
| Reality | Rarely flat |
| Strategy | Adjust quotes to revert |

> "You are not a directional trader. Inventory accumulation is a risk, not a bet."

**Our Status**: We ARE directional traders - we intentionally accumulate inventory (positions) based on signals.

#### Skewing Quotes - Managing Inventory

When holding too much inventory (Long):

| Action | Outcome |
| ------ | ------- |
| Lower Bid | Discourage sellers |
| Lower Ask | Attract buyers |
| Result | Inventory decreases |
| Formula | P_quote = P_mid - gamma * q |
| Cost | Giving up edge to dump risk |

> "Logic: Make it cheap for others to take your toxic inventory."

#### Adverse Selection - Toxic Flow

| Trader Type | Impact on MM |
| ----------- | ------------ |
| Uninformed | Noise (Profitable) |
| Informed | Toxic (Loss making) |
| The Trap | Being filled before a move |
| Signal | Large/Fast order flow |
| Defense | Widen spread / Cancel |

> "If you get filled instantly on a quote, you probably priced it wrong."

**Our Implication**: When we send market orders, we're the "taker" - we pay the spread. In volatile moments, spreads widen (MM defense).

#### Avellaneda-Stoikov Model - Gold Standard

| Variable | Role in Pricing |
| -------- | --------------- |
| s | Current Mid Price |
| q | Current Inventory |
| sigma | Volatility |
| gamma | Risk Aversion Parameter |
| T - t | Time to close |

**Optimal Price**: `r(s,t) = s - q × γ × σ² × (T-t)`

> "Higher vol or risk aversion = Aggressive skew."

#### P&L Components - Where's the Money?

| Source | Description |
| ------ | ----------- |
| Spread P&L | Value captured from bid/ask |
| Position P&L | Value change of inventory |
| Rebates | Exchange payments (Maker) |
| Fees | Exchange costs (Taker) |
| Net | Spread + Rebates - Risk |

> "A good MM strategy maximizes spread capture while minimizing position variance."

#### Why This Matters for AutoTrader

| Concept | Our Implication |
| ------- | --------------- |
| Spread | Part of our transaction costs |
| Taker fees | We pay when using market orders |
| Adverse selection | Our signals might be "informed flow" to MMs |
| Inventory risk | We embrace it (directional bets) |
| L1 vs L2 | We use L1; L2 could improve execution |

**Bottom Line**: We're on the opposite side of this trade - we consume liquidity rather than provide it. Understanding MM mechanics helps us recognize why spreads widen in volatility and why execution timing matters.

---

## Risk & Portfolio

### VaR vs CVaR (Conditional Value at Risk)

- **Source**: LinkedIn post (Jan 2026)
- **Summary**: Comprehensive comparison of Value at Risk (VaR) and Conditional Value at Risk (CVaR/Expected Shortfall). VaR = max expected loss at confidence level; CVaR = average loss in tail beyond VaR. CVaR is coherent, convex, and better for optimization. Basel IV shifting from VaR to ES.
- **Relevance**: **High** - directly applicable to position sizing and portfolio risk limits
- **Priority**: High
- **GH Potential**: **Issue created** - [#543](https://github.com/iAmGiG/OptionsCommander/issues/543)
- **Revisit When**: Implementing portfolio-level risk management

#### Key Takeaways

| Aspect | VaR | CVaR (ES) |
| ------ | --- | --------- |
| Definition | Max loss at confidence level | Average loss beyond VaR |
| Coherence | Not coherent (fails subadditivity) | Coherent (satisfies all 4 axioms) |
| Optimization | Non-convex, multiple local minima | Convex, unique global minimum |
| Tail sensitivity | Ignores severity beyond threshold | Captures tail severity |
| Regulation | Basel II/III (99%, 10-day) | Basel IV proposed (97.5%) |
| Computation | Faster, simpler | More complex, needs tail data |

#### Formulas (Normal Distribution)

| Metric | 95% Confidence, 2% Volatility |
| ------ | ----------------------------- |
| VaR | 3.29% |
| CVaR | 4.12% (25% higher) |

#### Fat-Tail Impact

| Distribution | ES/VaR Ratio |
| ------------ | ------------ |
| Normal | 1.26 |
| Student-t (df=3) | 1.58 |

#### Implementation Notes

- **Rockafellar-Uryasev reformulation** enables efficient CVaR portfolio optimization
- CVaR admits **Euler allocation** for risk budgeting (clean risk attribution)
- VaR lacks clean decomposition

#### Recommendation

Use **both** for comprehensive risk assessment:

- VaR for regulatory compliance, simple communication, backtesting
- CVaR for portfolio optimization, tail risk, risk decomposition

---

## ML/AI Techniques

### Strawberry: Information-Theoretic Hallucination Detection

- **Source**: [GitHub: leochlon/pythea/strawberry](https://github.com/leochlon/pythea/tree/main/strawberry)
- **Paper**: [arXiv:2509.11208](https://arxiv.org/abs/2509.11208) (Sep 2025, preprint)
- **Summary**: Uses logprobs + KL divergence to detect when LLM confidence doesn't match available evidence. Catches "evidence in context but ignored" patterns. MCP tool for Claude Code.
- **Relevance**: Low for current `VotingSignal` (deterministic math). Could matter if building LLM-based analysis agents.
- **Priority**: Low
- **GH Potential**: Archive - not relevant to current architecture
- **Revisit When**: Building LLM-driven strategy reasoning (not just NL parsing)
- **Limitations**:
  - Only catches subset of hallucinations
  - Not peer-reviewed
  - Adds latency per call
- **Verdict**: Marketing oversells; technique is legitimate but narrow

---

## Data & Signals

No entries yet.

---

## Validation & Testing

### Backtesting Pitfalls: 10 Reasons Quant Strategies Fail

- **Source**: LinkedIn carousel (Jan 2026)
- **Summary**: Comprehensive checklist of why strategies show Sharpe 3.0 in backtest but lose money in production. Covers overfitting, look-ahead bias, survivorship bias, data leakage, p-hacking, transaction costs, regime change, capacity, and robustness testing.
- **Relevance**: **High** - directly validates our existing research gaps and adds new ones
- **Priority**: Reference material (use as checklist)
- **GH Potential**: Cross-reference existing issues; create checklist for enhanced backtesting (#270)
- **Revisit When**: Building any new backtesting infrastructure

#### 1. Overfitting - "The Sin of Complexity"

| Aspect | Detail |
| ------ | ------ |
| Problem | Fitting noise, not signal |
| Symptom | High Sharpe in-sample, negative out-of-sample |
| Cause | Too many parameters |
| Fix | Occam's Razor / Regularization |
| Metric | AIC / BIC scores |

> "If you torture the data long enough, it will confess to anything."

**Our Status**: `VotingSignal` uses only 2 indicators (MACD + RSI) with fixed params - low overfitting risk.

#### 2. Look-Ahead Bias - "Peeking at the Future"

| Scenario | The Error |
| -------- | --------- |
| Close Price | Trading on Close using Close info |
| Earnings | Trading on data released post-market |
| High/Low | Assuming execution at extremes |
| Aggregates | Normalizing with full sample mean |

**Fix**: Lag data by 1 period (t-1)

> "Always ask: Did I actually have this information at the timestamp?"

**Our Status**: Documented gap in exit strategy research. Issue #540 (time-based exits) partially addresses.

#### 3. Survivorship Bias - "Ignoring the Dead"

| Factor | Impact |
| ------ | ------ |
| Delistings | Bankrupt firms vanish from data |
| Result | Artificially inflated returns |
| Indices | "Current Constituents" list is biased |
| Example | Testing S&P500 using 2025 list |

**Solution**: Point-in-Time (PIT) Databases

> "If you exclude the losers (Lehman, Enron), your alpha is fake."

**Our Status**: Not addressed. Testing on AAPL/SPY (survivors). Low priority for current scope.

#### 4. Data Leakage - "Contaminating the Test Set"

| Source | Mechanism |
| ------ | --------- |
| Normalization | Z-Scoring using global mean/std |
| Train/Test | Random split (breaks time series) |
| Future Ref | Labelling based on future returns |
| Outcome | Model learns future distribution |

**Fix**: Walk-forward Optimization

**Our Status**: Existing label `data-leakage` on GitHub. `VotingSignal` doesn't use ML, so low risk.

#### 5. P-Hacking - "Optimization Bias"

| Concept | Reality |
| ------- | ------- |
| Grid Search | Trying 1000 combos to find 1 |
| Reality | With enough trials, something will always look good |
| Metric | "Best" result is likely an outlier |
| Stability | Is the parameter plateau stable? |

**Fix**: Deflated Sharpe Ratio (DSR)

> "If changing the lookback from 20 to 21 kills the PnL, it's noise."

**Our Status**: Fibonacci MACD (13/34/8) was validated across 7 stocks, not just cherry-picked. Parameter sensitivity testing recommended.

#### 6. Transaction Costs - "The Silent Killer"

| Cost Type | Modeling |
| --------- | -------- |
| Commission | Fixed $ per trade/share |
| Spread | Bid-Ask width (varies by vol) |
| Slippage | Market impact of your size |
| SWR | Square Root Law of Impact |

**Reality**: Gross Alpha ≠ Net Alpha

> "A high-turnover strategy with zero cost assumptions is worthless."

**Our Status**: Issue #541 (Transaction Cost Sensitivity) created. `performance_clarification.py` now includes 0.1% cost.

#### 7. Regime Change - "Past ≠ Future"

| Environment | Strategy Risk |
| ----------- | ------------- |
| Low Vol | Mean reversion thrives |
| High Vol | Trend following thrives |
| Correlation | Assets correlate to 1 in crashes |
| Stationarity | Price stats drift over time |

**Fix**: Train on diverse regimes

> "Strategies trained only on 2010-2019 (Bull Market) fail in 2020/2022."

**Our Status**: Issue #542 (Fat Tails Validation) addresses this. `VotingSignal` tested on 2024-2025 (mixed regimes). Documented insight: better in volatile (-14.6%) vs bull (-25.8%) markets.

#### 8. Capacity - "Scale Limits"

| Constraint | Effect |
| ---------- | ------ |
| ADV Limit | Cannot trade >1-2% daily vol |
| Alpha Decay | More AUM = Lower Returns |
| Small Caps | High alpha, zero scalability |
| Crowding | Others trading same signal |

**Question**: "Is this worth my time?"

> "Backtesting $10M with the same fills as $10k is a fallacy."

**Our Status**: Not addressed. Paper trading with small positions. Future consideration for scaling.

#### 9. Robustness - "The Checklist"

| Method | Purpose |
| ------ | ------- |
| Hold-out | True Out-of-Sample test |
| Sensitivity | Vary params ±10% |
| Noise Test | Train on shuffled data (should fail) |
| Drawdown | Can you survive the max DD? |

**Reality**: Expect 50% of backtest performance

> "Golden Rule: Be skeptical of your own success."

**Our Status**: Extended period testing (2024-2025) done. Parameter sensitivity and noise tests not yet implemented.

#### Cross-Reference to Existing Issues

| Pitfall | Related Issue | Status |
| ------- | ------------- | ------ |
| Transaction Costs | #541 | Created |
| Regime Change | #542 | Created |
| Look-Ahead | #540 | Created |
| Data Leakage | Label exists | Monitored |
| Overfitting | N/A | Low risk (simple model) |
| P-Hacking | #270 | Enhanced backtesting |
| Survivorship | N/A | Future consideration |
| Capacity | N/A | Future consideration |
| Robustness | #538, #539 | ATR/Trailing tests |

---

## Archive

Items reviewed and determined not relevant:

| Item | Reason | Date |
| ---- | ------ | ---- |
| (none yet) | | |
