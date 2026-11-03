# Partial Position Exit Strategies: Research & Analysis

**Issue**: #248 (Research Component)
**Date**: December 2025
**Status**: Complete

## 1. Executive Summary

This document explores the theoretical and practical foundations for partial position exits (scaling out). The research supports the implementation of a multi-target exit system in `PartialExitManager` to balance profit capture with trend following potential.

**Key Finding**: Partial exits typically reduce total theoretical return compared to a perfect full exit, but significantly improve risk-adjusted returns (Sharpe Ratio) and psychological sustainability by smoothing the equity curve and reducing drawdown duration.

## 2. Academic Foundations

### The Disposition Effect & Behavioral Finance

**Paper**: *The Disposition to Sell Winners Too Early and Ride Losers Too Long: Theory and Evidence* (Shefrin & Statman, 1985)

* **Concept**: Traders psychologically struggle to hold winning positions due to risk aversion in the domain of gains (Prospect Theory).
* **Relevance**: Partial exits act as a psychological bridge. By "banking" some profit, the trader reduces the anxiety of a reversal, making it easier to hold the remainder of the position for larger gains. This counters the disposition effect.

### Optimal Stopping & Variance Reduction

**Concept**: In stochastic processes, determining the optimal time to stop (exit) to maximize reward.

* **Application**: A single exit point assumes one can predict the local maximum. Partial exits approximate a continuous exit distribution, reducing the variance of the exit price relative to the true maximum.
* **Trade-off**: Scaling out reduces the volatility of returns but often lowers the expected value if the asset has strong positive drift (momentum).

### Portfolio Heat & Risk Management

**Paper**: *Optimal Liquidation of a Trading Position* (Almgren & Chriss, 2000)

* **Relevance**: While focused on execution, the framework highlights the trade-off between volatility risk (holding longer) and market impact/opportunity cost. Partial exits reduce exposure over time, linearly decreasing risk as the trade progresses.

## 3. Industry Practices

### Institutional Approaches

* **Trend Following CTAs**: Often use trailing stops on the entire position or scale out in units based on volatility (ATR) expansion.
* **Mean Reversion**: Typically use single target exits (all-out) as the edge is short-lived.

### Retail & Proprietary Trading Conventions

1. **The "Free Ride" (Breakeven Strategy)**
    * Exit 50% of position when Profit = Initial Risk (1R).
    * Move Stop Loss to Breakeven.
    * **Result**: The remaining position has zero capital risk.

2. **The Rule of Thirds**
    * 1/3 at conservative target (high probability).
    * 1/3 at measured move target.
    * 1/3 on trailing stop (moon bag).

3. **Volatility-Based Scaling**
    * Exit portions at 1 ATR, 2 ATR, 3 ATR extensions.

## 4. Statistical Evaluation

### Expected Value vs. Sharpe Ratio

* **Full Exits**: Higher variance, higher potential Max Drawdown, higher potential Total Return (if targets are accurate).
* **Partial Exits**: Lower variance, lower Max Drawdown, smoother Equity Curve.

### Impact on Metrics

| Metric | Full Exit (Target) | Partial Exit (50/50) |
|--------|-------------------|----------------------|
| **Win Rate** | Lower (must hit full target) | Higher (hits first target more often) |
| **Avg Win** | Higher | Lower |
| **Drawdown** | Higher | Lower |
| **Psychology** | Harder | Easier |

## 5. Recommended Configuration for #248

Based on this research, the `PartialExitManager` should implement the following default behavior for the MACD+RSI voting signal (`VotingSignal`; Trend/Momentum strategy):

### Default Strategy: "The Balanced Runner"

* **Split Ratio**: 50% / 50%
* **Target 1 (Bank)**: Fixed Limit Order
  * **Level**: 1.5x to 2.0x ATR or fixed % (e.g., 4-5% for stocks).
  * **Goal**: Secure profit and finance the trade.
* **Target 2 (Runner)**: Dynamic Trailing Stop
  * **Mechanism**: `TrailingStopManager`
  * **Goal**: Capture fat-tail trend moves.

### Configuration for `trading_modes.yaml`

```yaml
# Recommended defaults based on research
conservative:
  partial_exits:
    enabled: true
    split_ratio: 0.5
    target_1:
      type: limit
      value: 0.04  # 4% gain
    target_2:
      type: trailing_stop
      manager: TrailingStopManager

moderate:
  partial_exits:
    enabled: true
    split_ratio: 0.5
    target_1:
      type: limit
      value: 0.06  # 6% gain
    target_2:
      type: trailing_stop

aggressive:
  partial_exits:
    enabled: false # Aggressive often favors all-or-nothing for max EV
```

## 6. Citations

1. Shefrin, H., & Statman, M. (1985). *The Disposition to Sell Winners Too Early and Ride Losers Too Long: Theory and Evidence*. The Journal of Finance.
2. Kahneman, D., & Tversky, A. (1979). *Prospect Theory: An Analysis of Decision under Risk*. Econometrica.
3. Almgren, R., & Chriss, N. (2000). *Optimal Liquidation of a Trading Position*. Journal of Risk.
