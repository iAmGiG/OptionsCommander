# GEX VoterAgent Integration Guide - Issue #419

> **Historical plan.** This predates the removal of the agents (#331). `VoterAgent` is now `VotingSignal` (`src/trading/instruments/voting_signal.py`), `src/autogen_agents/` no longer exists, and GEX overlays were tested and rejected ([re-validation](../08_research/02_gex_research/regime_lookahead_revalidation.md)).

## Overview

This guide provides implementation details for **Issue #419**: Adding Gamma Exposure (GEX) as a third voter signal to the VoterAgent, creating a triple-voting system: **MACD+RSI+GEX**.

**Status**: Implementation Planning
**Related Issues**: #352 (GEX foundation), #367 (regime detection), #394 (forward testing)
**Priority**: P1 (High)

---

## 1. Architecture Overview

### Current Dual-Voting System

```text
Price Data (OHLCV)
    ↓
┌─────────────────────────────────────┐
│        MACD (13/34/8)               │
├─────────────────────────────────────┤
│ Signal: BUY, SELL, HOLD             │
│ Confidence: 0.0-1.0                 │
└──────────────┬──────────────────────┘
               │
               │
         ┌─────▼──────────────┐
         │  Voting Logic      │
         │  Consensus Check   │◄─────────────────┐
         │  Position Sizing   │                  │
         └─────┬──────────────┘                  │
               │                                 │
               ▼                                 │
         ┌──────────────────┐                   │
         │ Final Decision   │                   │
         ├──────────────────┤                   │
         │ BUY/SELL/HOLD    │                   │
         │ Confidence       │                   │
         │ Position Size    │                   │
         └──────────────────┘                   │
                                                │
         ┌──────────────────────────────────────┘
         │
         │
┌────────▼──────────────────────────────┐
│    RSI (14, 30/70)                    │
├───────────────────────────────────────┤
│ Signal: BUY, SELL, HOLD               │
│ Confidence: 0.0-1.0                   │
└───────────────────────────────────────┘
```

### Enhanced Triple-Voting System

```text
Price Data (OHLCV)          Options Chain Data
    ↓                                ↓
    │                                │
    ├──────┬──────────────────────────┤
    │      │                          │
    ▼      ▼                          ▼
┌───────┐┌──────┐      ┌────────────────┐
│ MACD  ││ RSI  │      │  GEX Generator │
│       ││      │      │                │
│BUY    ││HOLD  │      │ BUY/SELL/HOLD  │
│Conf:0.6││Conf: │      │ Conf: 0.0-1.0  │
└───┬───┘└───┬──┘      └────────┬───────┘
    │        │                  │
    │        └──────┬───────────┘
    │               │
    │        ┌──────▼──────────────┐
    │        │ Triple Voting       │
    │        │ Logic               │
    │        │ Count Agreement     │
    │        │ (0-3 consensus)     │
    │        │ Resolve Conflicts   │
    │        └──────┬──────────────┘
    │               │
    └───────┬───────┘
            │
            ▼
    ┌──────────────────┐
    │ Final Decision   │
    ├──────────────────┤
    │ BUY/SELL/HOLD    │
    │ Confidence:0-1   │
    │ Position:0-100%  │
    │ Signal Type      │
    │ (STRONG/MODERATE)│
    │ (WEAK/CONFLICT)  │
    └──────────────────┘
```

---

## 2. GEX Signal Generator Implementation

### File Location

`src/trading_tools/gex_signal_generator.py`

### Core Class: GEXSignalGenerator

```python
import logging
from typing import Dict, List, Optional, Tuple
import pandas as pd
import numpy as np

logger = logging.getLogger(__name__)


class GEXSignalGenerator:
    """
    Generate trading signals from Gamma Exposure (GEX) options market analysis.

    GEX measures dealer hedging obligations in options market, providing
    insights into support/resistance levels and volatility expectations.

    Attributes:
        gex_threshold_positive: GEX level indicating dealers long gamma
        gex_threshold_negative: GEX level indicating dealers short gamma
        zero_gamma_tolerance: Tolerance band around zero gamma level
    """

    def __init__(
        self,
        gex_threshold_positive: float = 500_000_000,  # 500M (positive GEX)
        gex_threshold_negative: float = -500_000_000,  # -500M (negative GEX)
        zero_gamma_tolerance: float = 0.01,  # 1% price tolerance
        confidence_boost_strong: float = 0.7,  # Strong signal confidence
        confidence_boost_moderate: float = 0.5,  # Moderate signal confidence
        confidence_hold: float = 0.3,  # Hold signal confidence
    ):
        """Initialize GEX signal generator with configurable thresholds."""
        self.gex_threshold_positive = gex_threshold_positive
        self.gex_threshold_negative = gex_threshold_negative
        self.zero_gamma_tolerance = zero_gamma_tolerance
        self.confidence_strong = confidence_boost_strong
        self.confidence_moderate = confidence_boost_moderate
        self.confidence_hold = confidence_hold

        logger.info("GEXSignalGenerator initialized")
        logger.info(f"  Positive GEX threshold: {gex_threshold_positive:,.0f}")
        logger.info(f"  Negative GEX threshold: {gex_threshold_negative:,.0f}")

    def generate_signal(
        self,
        symbol: str,
        options_chain: pd.DataFrame,
        current_price: float,
        return_details: bool = False,
    ) -> Dict:
        """
        Generate BUY/SELL/HOLD signal from options chain GEX analysis.

        Args:
            symbol: Stock symbol (e.g., 'SPY')
            options_chain: DataFrame with options data
                Required columns: gamma, open_interest, option_type (call/put)
            current_price: Current underlying price
            return_details: Include detailed analysis in response

        Returns:
            {
                'action': 'BUY' | 'SELL' | 'HOLD',
                'confidence': float (0.0-1.0),
                'reasoning': str,
                'gex_total': float,
                'zero_gamma_level': float,
                'dealer_positioning': str,  # LONG_GAMMA, NEUTRAL, SHORT_GAMMA
                'volatility_expectation': str,  # STABLE, ELEVATED, EXTREME
                'details': {...} if return_details
            }
        """
        try:
            # Validate input data
            if options_chain is None or options_chain.empty:
                return self._create_response(
                    symbol=symbol,
                    action='HOLD',
                    confidence=0.0,
                    reasoning='No options data available',
                    gex_total=0.0,
                    zero_gamma_level=current_price,
                    dealer_pos='UNKNOWN',
                    vol_exp='UNKNOWN',
                )

            # Calculate GEX metrics
            gex_total = self._calculate_gex_total(options_chain)
            zero_gamma_level = self._find_zero_gamma_level(options_chain)
            gex_by_volume = self._calculate_gex_by_volume(options_chain)

            # Determine dealer positioning
            dealer_positioning = self._classify_dealer_positioning(gex_total)

            # Estimate volatility expectation
            vol_expectation = self._estimate_volatility_expectation(
                gex_total, zero_gamma_level, current_price
            )

            # Generate signal based on positioning
            action, confidence = self._generate_action(
                gex_total=gex_total,
                zero_gamma_level=zero_gamma_level,
                current_price=current_price,
                dealer_positioning=dealer_positioning,
                vol_expectation=vol_expectation,
            )

            # Create reasoning
            reasoning = self._create_reasoning(
                gex_total, dealer_positioning, vol_expectation, action
            )

            response = self._create_response(
                symbol=symbol,
                action=action,
                confidence=confidence,
                reasoning=reasoning,
                gex_total=gex_total,
                zero_gamma_level=zero_gamma_level,
                dealer_pos=dealer_positioning,
                vol_exp=vol_expectation,
            )

            # Add detailed analysis if requested
            if return_details:
                response['details'] = {
                    'gex_by_volume': gex_by_volume,
                    'option_count': len(options_chain),
                    'call_gamma': self._total_gamma_by_type(options_chain, 'call'),
                    'put_gamma': self._total_gamma_by_type(options_chain, 'put'),
                    'open_interest': options_chain['open_interest'].sum(),
                }

            return response

        except Exception as e:
            logger.error(f"Error generating GEX signal for {symbol}: {e}")
            return self._create_error_response(symbol, str(e))

    def _calculate_gex_total(self, options_chain: pd.DataFrame) -> float:
        """
        Calculate total Gamma Exposure (GEX).

        Formula: GEX = Σ(gamma × OI × 100 × Spot² × [+1 calls, -1 puts])

        Where:
        - gamma: Greek letter gamma (second derivative of option price w.r.t. spot)
        - OI: Open Interest (number of contracts)
        - 100: Standard contract multiplier
        - Spot²: Spot price squared (normalizes across price levels)
        - Direction: +1 for calls (upside protection), -1 for puts (downside protection)
        """
        gex_total = 0.0

        try:
            for _, row in options_chain.iterrows():
                gamma = float(row.get('gamma', 0))
                oi = float(row.get('open_interest', 0))
                spot = float(row.get('spot_price', 100))  # Fallback default
                opt_type = str(row.get('option_type', 'call')).lower()

                # Direction multiplier: calls are +1, puts are -1
                direction = 1.0 if opt_type == 'call' else -1.0

                # GEX contribution: gamma × OI × 100 × Spot² × direction
                contribution = gamma * oi * 100 * (spot ** 2) * direction
                gex_total += contribution

        except Exception as e:
            logger.warning(f"Error calculating GEX total: {e}")

        return gex_total

    def _calculate_gex_by_volume(self, options_chain: pd.DataFrame) -> float:
        """Calculate GEX weighted by volume instead of open interest."""
        gex_volume = 0.0

        try:
            for _, row in options_chain.iterrows():
                gamma = float(row.get('gamma', 0))
                volume = float(row.get('volume', 0))
                spot = float(row.get('spot_price', 100))
                opt_type = str(row.get('option_type', 'call')).lower()

                direction = 1.0 if opt_type == 'call' else -1.0
                contribution = gamma * volume * 100 * (spot ** 2) * direction
                gex_volume += contribution

        except Exception as e:
            logger.warning(f"Error calculating GEX by volume: {e}")

        return gex_volume

    def _find_zero_gamma_level(self, options_chain: pd.DataFrame) -> float:
        """
        Find the price level where total GEX crosses zero.

        This is the "gamma flip" level where dealer positioning changes
        from long gamma (supportive) to short gamma (destabilizing).

        Algorithm:
        1. Get unique strike prices
        2. For each strike, calculate GEX if spot = strike
        3. Find strike where GEX crosses from + to - (or vice versa)
        4. Interpolate between strikes for more precision
        """
        try:
            # Extract unique strikes and current options
            strikes = sorted(options_chain['strike'].unique())

            if len(strikes) < 2:
                logger.warning("Insufficient strike data for zero gamma level")
                return options_chain.iloc[0]['spot_price']

            # Find the strike closest to zero gamma
            # (For now, use simple approach: middle strike)
            # TODO: Implement more sophisticated interpolation
            mid_strike = strikes[len(strikes) // 2]

            return float(mid_strike)

        except Exception as e:
            logger.warning(f"Error finding zero gamma level: {e}")
            return 0.0

    def _classify_dealer_positioning(self, gex_total: float) -> str:
        """Classify dealer positioning based on GEX magnitude."""
        if gex_total > self.gex_threshold_positive:
            return 'LONG_GAMMA'  # Dealers long, profitable on volatility increase
        elif gex_total < self.gex_threshold_negative:
            return 'SHORT_GAMMA'  # Dealers short, profitable on volatility decrease
        else:
            return 'NEUTRAL'  # Dealers near neutral positioning

    def _estimate_volatility_expectation(
        self, gex_total: float, zero_gamma_level: float, current_price: float
    ) -> str:
        """Estimate market volatility expectation from GEX positioning."""
        if gex_total > self.gex_threshold_positive:
            return 'STABLE'  # Long gamma dampens volatility
        elif gex_total < self.gex_threshold_negative:
            return 'EXTREME'  # Short gamma amplifies volatility
        else:
            # Neutral positioning - check price relationship
            if abs(current_price - zero_gamma_level) < zero_gamma_level * 0.01:
                return 'ELEVATED'  # Near flip point = increased risk
            return 'NORMAL'

    def _generate_action(
        self,
        gex_total: float,
        zero_gamma_level: float,
        current_price: float,
        dealer_positioning: str,
        vol_expectation: str,
    ) -> Tuple[str, float]:
        """Generate BUY/SELL/HOLD action and confidence from GEX analysis."""

        # Rule 1: Long gamma (dealers protecting) = BUY signal
        if dealer_positioning == 'LONG_GAMMA':
            confidence = self.confidence_strong
            # If price is below zero gamma level, dealers are protecting downside
            if current_price < zero_gamma_level:
                action = 'BUY'
            else:
                action = 'HOLD'

        # Rule 2: Short gamma (dealers hedging) = SELL signal
        elif dealer_positioning == 'SHORT_GAMMA':
            confidence = self.confidence_strong
            # If price is above zero gamma level, dealers are shorting upside
            if current_price > zero_gamma_level:
                action = 'SELL'
            else:
                action = 'HOLD'

        # Rule 3: Neutral positioning
        else:
            action = 'HOLD'
            confidence = self.confidence_hold

        # Rule 4: Adjust for extreme volatility expectations
        if vol_expectation == 'EXTREME':
            # High fragility - reduce confidence slightly
            confidence = max(0.3, confidence - 0.15)

        return action, confidence

    def _total_gamma_by_type(self, options_chain: pd.DataFrame, opt_type: str) -> float:
        """Calculate total gamma for specific option type (call or put)."""
        try:
            filtered = options_chain[
                options_chain['option_type'].str.lower() == opt_type.lower()
            ]
            return float(filtered['gamma'].sum())
        except Exception:
            return 0.0

    def _create_reasoning(
        self, gex_total: float, positioning: str, vol_exp: str, action: str
    ) -> str:
        """Create human-readable reasoning for the GEX signal."""
        return (
            f"GEX Analysis: Total GEX = {gex_total:,.0f} ({positioning}). "
            f"Volatility Expectation: {vol_exp}. "
            f"Signal: {action}"
        )

    def _create_response(
        self,
        symbol: str,
        action: str,
        confidence: float,
        reasoning: str,
        gex_total: float,
        zero_gamma_level: float,
        dealer_pos: str,
        vol_exp: str,
    ) -> Dict:
        """Create standardized response dictionary."""
        return {
            'symbol': symbol,
            'action': action,
            'confidence': float(confidence),
            'reasoning': reasoning,
            'gex_total': float(gex_total),
            'zero_gamma_level': float(zero_gamma_level),
            'dealer_positioning': dealer_pos,
            'volatility_expectation': vol_exp,
        }

    def _create_error_response(self, symbol: str, error: str) -> Dict:
        """Create error response."""
        return {
            'symbol': symbol,
            'action': 'HOLD',
            'confidence': 0.0,
            'reasoning': f"GEX analysis error: {error}",
            'gex_total': 0.0,
            'zero_gamma_level': 0.0,
            'dealer_positioning': 'UNKNOWN',
            'volatility_expectation': 'UNKNOWN',
            'error': error,
        }
```

---

## 3. Enhanced VoterAgent Integration

### File Location

Update: `src/autogen_agents/voter_agent.py`

### Key Changes

#### 1. Enhanced **init**() Method

```python
def __init__(
    self,
    name: str = "voter_agent",
    timeframe: Optional[str] = None,
    macd_params: Optional[Dict[str, int]] = None,
    rsi_params: Optional[Dict[str, int]] = None,
    gex_params: Optional[Dict[str, float]] = None,  # NEW
    voting_thresholds: Optional[Dict[str, float]] = None,
    use_gex: bool = False,  # NEW: Enable/disable GEX
    use_config_file: bool = True,
    **kwargs,
):
    """Initialize VoterAgent with optional GEX support."""
    super().__init__(name=name, **kwargs)

    # ... existing MACD/RSI initialization ...

    # NEW: GEX initialization
    self.use_gex = use_gex
    if use_gex:
        from src.trading_tools.gex_signal_generator import GEXSignalGenerator
        self.gex_generator = GEXSignalGenerator(
            **(gex_params or {})
        )
        logger.info("GEX voting enabled")
    else:
        self.gex_generator = None
        logger.info("GEX voting disabled (legacy MACD+RSI mode)")

    # Track if triple voting is active
    self.voting_mode = "triple" if use_gex else "dual"
```

#### 2. Enhanced evaluate_voting() Method

```python
def evaluate_voting(
    self,
    symbol: str,
    price_data: pd.DataFrame,
    options_chain: Optional[pd.DataFrame] = None,  # NEW
    return_components: bool = False,
) -> Dict[str, Any]:
    """
    Enhanced voting with optional GEX signal.

    Dual voting (MACD+RSI):
    - Called without options_chain
    - Use existing logic

    Triple voting (MACD+RSI+GEX):
    - Called with options_chain
    - Requires use_gex=True
    - Uses new consensus logic
    """
    try:
        # Validate data sufficiency
        if len(price_data) < self.voting_thresholds["min_data_points"]:
            return {
                "symbol": symbol,
                "action": "HOLD",
                "confidence": 0.0,
                "position_size": 0.0,
                "reasoning": f"Insufficient data ({len(price_data)} < {self.voting_thresholds['min_data_points']})",
                "voting_mode": self.voting_mode,
            }

        # Extract price series
        prices = price_data["Close"] if "Close" in price_data.columns else price_data["close"]

        # Calculate MACD signal
        macd_signal = self._get_macd_signal(prices)

        # Calculate RSI signal
        rsi_signal = self._get_rsi_signal(prices)

        # NEW: Calculate GEX signal if enabled
        if self.use_gex and options_chain is not None:
            gex_signal = self._get_gex_signal(symbol, options_chain)
        else:
            gex_signal = None

        # Perform voting
        if gex_signal is not None:
            # Triple voting
            result = self._perform_triple_voting(
                macd_signal, rsi_signal, gex_signal, symbol, prices
            )
        else:
            # Dual voting (legacy)
            result = self._perform_dual_voting(
                macd_signal, rsi_signal, symbol, prices
            )

        # Add component details if requested
        if return_components:
            result["components"] = {
                "macd": macd_signal,
                "rsi": rsi_signal,
            }
            if gex_signal is not None:
                result["components"]["gex"] = gex_signal

        # Track voting mode
        result["voting_mode"] = self.voting_mode

        return result

    except Exception as e:
        logger.error(f"Error in enhanced evaluate_voting: {e}")
        return {
            "symbol": symbol,
            "action": "HOLD",
            "confidence": 0.0,
            "position_size": 0.0,
            "reasoning": f"Analysis error: {str(e)}",
            "error": str(e),
            "voting_mode": self.voting_mode,
        }
```

#### 3. New Helper Methods

```python
def _get_macd_signal(self, prices: pd.Series) -> Dict[str, Any]:
    """Calculate MACD signal."""
    macd_data = calculate_macd(
        prices,
        fast=self.macd_params["fast"],
        slow=self.macd_params["slow"],
        signal=self.macd_params["signal"],
    )

    latest_histogram = macd_data["histogram"].iloc[-1]
    macd_threshold = self.voting_thresholds["macd_threshold"]

    if latest_histogram > macd_threshold:
        action = "BUY"
        confidence = 0.6
    elif latest_histogram < -macd_threshold:
        action = "SELL"
        confidence = 0.6
    else:
        action = "HOLD"
        confidence = 0.3

    return {
        "action": action,
        "confidence": confidence,
        "histogram": float(latest_histogram),
        "data": macd_data,
    }

def _get_rsi_signal(self, prices: pd.Series) -> Dict[str, Any]:
    """Calculate RSI signal."""
    rsi_data = calculate_rsi(
        prices,
        period=self.rsi_params["period"],
        oversold=self.rsi_params["oversold"],
        overbought=self.rsi_params["overbought"],
    )

    current_rsi = rsi_data["rsi"].iloc[-1]

    if current_rsi < self.rsi_params["oversold"]:
        action = "BUY"
        confidence = 0.6
    elif current_rsi > self.rsi_params["overbought"]:
        action = "SELL"
        confidence = 0.6
    else:
        action = "HOLD"
        confidence = 0.3

    return {
        "action": action,
        "confidence": confidence,
        "value": float(current_rsi),
        "data": rsi_data,
    }

def _get_gex_signal(
    self, symbol: str, options_chain: pd.DataFrame
) -> Dict[str, Any]:
    """Calculate GEX signal (requires GEX enabled and options data)."""
    if self.gex_generator is None:
        return {
            "action": "HOLD",
            "confidence": 0.0,
            "error": "GEX generator not initialized",
        }

    try:
        current_price = options_chain['spot_price'].iloc[0]
        signal = self.gex_generator.generate_signal(
            symbol, options_chain, current_price, return_details=True
        )
        return signal
    except Exception as e:
        logger.warning(f"Error getting GEX signal: {e}")
        return {
            "action": "HOLD",
            "confidence": 0.0,
            "error": str(e),
        }

def _perform_dual_voting(
    self,
    macd: Dict,
    rsi: Dict,
    symbol: str,
    prices: pd.Series,
) -> Dict[str, Any]:
    """Existing dual-voting logic (MACD+RSI)."""
    # Existing implementation preserved
    # ... (keep original _perform_dual_voting logic) ...

def _perform_triple_voting(
    self,
    macd: Dict,
    rsi: Dict,
    gex: Dict,
    symbol: str,
    prices: pd.Series,
) -> Dict[str, Any]:
    """
    Enhanced triple-voting logic (MACD+RSI+GEX).

    Consensus Rules:
    - 3/3 agree: STRONG signal, 85% confidence, 100% position
    - 2/3 agree: MODERATE signal, 65% confidence, 70% position
    - 1/3 agrees: WEAK signal, 45% confidence, 40% position
    - 0/3 or conflict: HOLD, 20% confidence, 0% position
    """
    # Extract actions from three voters
    actions = {
        'BUY': sum([
            1 for signal in [macd, rsi, gex]
            if signal.get('action') == 'BUY'
        ]),
        'SELL': sum([
            1 for signal in [macd, rsi, gex]
            if signal.get('action') == 'SELL'
        ]),
        'HOLD': sum([
            1 for signal in [macd, rsi, gex]
            if signal.get('action') == 'HOLD'
        ]),
    }

    # Determine final action
    max_votes = max(actions.values())

    if actions['BUY'] > actions['SELL'] and actions['BUY'] == max_votes:
        final_action = 'BUY'
    elif actions['SELL'] > actions['BUY'] and actions['SELL'] == max_votes:
        final_action = 'SELL'
    else:
        final_action = 'HOLD'

    # Assign confidence and position size
    if actions[final_action] == 3:
        signal_type = 'STRONG_TRIPLE'
        confidence = 0.85
        position_size = 1.0
    elif actions[final_action] == 2:
        signal_type = 'MODERATE_DOUBLE'
        confidence = 0.65
        position_size = 0.7
    elif actions[final_action] == 1:
        signal_type = 'WEAK_SINGLE'
        confidence = 0.45
        position_size = 0.4
    else:
        signal_type = 'NO_CONSENSUS'
        final_action = 'HOLD'
        confidence = 0.2
        position_size = 0.0

    # Create result
    return {
        "symbol": symbol,
        "action": final_action,
        "confidence": confidence,
        "position_size": position_size,
        "signal_type": signal_type,
        "voting_mode": "triple",
        "votes": actions,
        "voters": {
            "macd": macd,
            "rsi": rsi,
            "gex": gex,
        },
        "reasoning": (
            f"Triple voting consensus: {actions['BUY']} BUY, "
            f"{actions['SELL']} SELL, {actions['HOLD']} HOLD"
        ),
        "current_price": float(prices.iloc[-1]),
        "parameters_used": self.current_config,
    }
```

---

## 4. Testing Strategy

### Unit Tests: GEXSignalGenerator

File: `tests/unit/trading_tools/test_gex_signal_generator.py`

```python
import pytest
import pandas as pd
from src.trading_tools.gex_signal_generator import GEXSignalGenerator


class TestGEXCalculation:
    """Test GEX calculation formulas."""

    @pytest.fixture
    def generator(self):
        return GEXSignalGenerator()

    @pytest.fixture
    def sample_options_chain(self):
        """Create mock options chain with known GEX."""
        return pd.DataFrame({
            'strike': [100, 105, 110],
            'gamma': [0.05, 0.08, 0.06],
            'open_interest': [1000, 2000, 1500],
            'volume': [100, 200, 150],
            'option_type': ['call', 'call', 'call'],
            'spot_price': [105, 105, 105],
        })

    def test_gex_calculation_all_calls(self, generator, sample_options_chain):
        """GEX calculation with all call contracts."""
        gex = generator._calculate_gex_total(sample_options_chain)

        # All calls → positive GEX
        assert gex > 0

        # Formula verification
        # GEX = Σ(gamma × OI × 100 × Spot² × +1)
        expected = (
            0.05 * 1000 * 100 * (105**2) * 1.0 +
            0.08 * 2000 * 100 * (105**2) * 1.0 +
            0.06 * 1500 * 100 * (105**2) * 1.0
        )
        assert abs(gex - expected) < 1000  # Allow small floating point error

    def test_zero_gamma_level_detection(self, generator, sample_options_chain):
        """Zero gamma level detection."""
        zero_level = generator._find_zero_gamma_level(sample_options_chain)

        # Should return a valid strike price
        assert zero_level in sample_options_chain['strike'].values

    def test_gex_signal_long_gamma(self, generator):
        """GEX signal generation with long gamma positioning."""
        # Create long gamma options chain (all calls)
        chain = pd.DataFrame({
            'strike': [100, 105, 110],
            'gamma': [0.10, 0.15, 0.12],
            'open_interest': [5000, 10000, 5000],
            'option_type': ['call', 'call', 'call'],
            'spot_price': [105, 105, 105],
        })

        signal = generator.generate_signal('SPY', chain, current_price=105)

        assert signal['action'] in ['BUY', 'HOLD']
        assert signal['dealer_positioning'] == 'LONG_GAMMA'
        assert signal['volatility_expectation'] == 'STABLE'

    def test_gex_signal_short_gamma(self, generator):
        """GEX signal generation with short gamma positioning."""
        # Create short gamma options chain (all puts)
        chain = pd.DataFrame({
            'strike': [100, 105, 110],
            'gamma': [0.10, 0.15, 0.12],
            'open_interest': [5000, 10000, 5000],
            'option_type': ['put', 'put', 'put'],
            'spot_price': [105, 105, 105],
        })

        signal = generator.generate_signal('SPY', chain, current_price=105)

        assert signal['action'] in ['SELL', 'HOLD']
        assert signal['dealer_positioning'] == 'SHORT_GAMMA'
        assert signal['volatility_expectation'] == 'EXTREME'


class TestGEXIntegration:
    """Integration tests with realistic data."""

    def test_gex_signal_with_empty_chain(self):
        """GEX signal handles empty options chain gracefully."""
        generator = GEXSignalGenerator()
        signal = generator.generate_signal('SPY', pd.DataFrame(), 100)

        assert signal['action'] == 'HOLD'
        assert signal['confidence'] == 0.0
```

### Unit Tests: Enhanced VoterAgent

File: `tests/unit/agents/test_voter_agent_triple_voting.py`

```python
import pytest
import pandas as pd
from src.autogen_agents.voter_agent import VoterAgent


class TestTripleVoting:
    """Test enhanced VoterAgent with GEX."""

    @pytest.fixture
    def price_data_sample(self):
        """Generate sample price data."""
        dates = pd.date_range('2024-01-01', periods=100)
        prices = pd.Series([100 + i*0.5 for i in range(100)], index=dates)
        return pd.DataFrame({
            'Close': prices,
            'Volume': [1000000] * 100,
        }, index=dates)

    @pytest.fixture
    def options_chain_sample(self):
        """Generate sample options chain."""
        return pd.DataFrame({
            'strike': [95, 100, 105, 110, 115],
            'gamma': [0.05, 0.08, 0.10, 0.08, 0.05],
            'open_interest': [2000, 5000, 10000, 5000, 2000],
            'volume': [200, 500, 1000, 500, 200],
            'option_type': ['call', 'call', 'call', 'call', 'call'],
            'spot_price': [104.5, 104.5, 104.5, 104.5, 104.5],
        })

    def test_voter_agent_dual_voting_legacy(self, price_data_sample):
        """VoterAgent works in legacy dual-voting mode (MACD+RSI only)."""
        voter = VoterAgent(use_gex=False)
        result = voter.evaluate_voting('AAPL', price_data_sample)

        # Dual voting should work without options_chain
        assert result['action'] in ['BUY', 'SELL', 'HOLD']
        assert result['voting_mode'] == 'dual'
        assert 'gex' not in result.get('voters', {})

    def test_voter_agent_triple_voting_enabled(
        self, price_data_sample, options_chain_sample
    ):
        """VoterAgent works with triple voting enabled."""
        voter = VoterAgent(use_gex=True)
        result = voter.evaluate_voting(
            'AAPL',
            price_data_sample,
            options_chain=options_chain_sample,
        )

        # Triple voting requires GEX data
        assert result['action'] in ['BUY', 'SELL', 'HOLD']
        assert result['voting_mode'] == 'triple'
        assert 'voters' in result
        assert 'gex' in result['voters']

    def test_triple_voting_consensus_all_agree(self, price_data_sample):
        """Three-way agreement produces strong signal."""
        voter = VoterAgent(use_gex=True)

        # Mock all three voters agreeing on BUY
        macd_signal = {'action': 'BUY', 'confidence': 0.6}
        rsi_signal = {'action': 'BUY', 'confidence': 0.6}
        gex_signal = {'action': 'BUY', 'confidence': 0.7}

        result = voter._perform_triple_voting(
            macd_signal, rsi_signal, gex_signal, 'AAPL', price_data_sample['Close']
        )

        assert result['action'] == 'BUY'
        assert result['confidence'] == 0.85
        assert result['position_size'] == 1.0
        assert result['signal_type'] == 'STRONG_TRIPLE'
        assert result['votes']['BUY'] == 3

    def test_triple_voting_partial_consensus(self, price_data_sample):
        """Two-way agreement produces moderate signal."""
        voter = VoterAgent(use_gex=True)

        macd_signal = {'action': 'BUY', 'confidence': 0.6}
        rsi_signal = {'action': 'BUY', 'confidence': 0.6}
        gex_signal = {'action': 'HOLD', 'confidence': 0.3}

        result = voter._perform_triple_voting(
            macd_signal, rsi_signal, gex_signal, 'AAPL', price_data_sample['Close']
        )

        assert result['action'] == 'BUY'
        assert result['confidence'] == 0.65
        assert result['position_size'] == 0.7
        assert result['signal_type'] == 'MODERATE_DOUBLE'
        assert result['votes']['BUY'] == 2

    def test_triple_voting_conflict(self, price_data_sample):
        """Conflicting signals produce HOLD."""
        voter = VoterAgent(use_gex=True)

        macd_signal = {'action': 'BUY', 'confidence': 0.6}
        rsi_signal = {'action': 'SELL', 'confidence': 0.6}
        gex_signal = {'action': 'HOLD', 'confidence': 0.3}

        result = voter._perform_triple_voting(
            macd_signal, rsi_signal, gex_signal, 'AAPL', price_data_sample['Close']
        )

        assert result['action'] == 'HOLD'
        assert result['confidence'] <= 0.45  # Weak signal or lower
        assert result['signal_type'] in ['WEAK_SINGLE', 'NO_CONSENSUS']

    def test_backward_compatibility_without_gex(self, price_data_sample):
        """Backward compatibility: can use VoterAgent without GEX."""
        # Create with use_gex=False (default)
        voter = VoterAgent()

        # Should work with just price data, no options_chain
        result = voter.evaluate_voting('AAPL', price_data_sample)

        assert 'action' in result
        assert result['voting_mode'] == 'dual'
```

### Integration Tests

File: `tests/integration/test_voter_gex_integration.py`

```python
def test_voter_agent_real_options_data():
    """Integration test with mock real options data."""
    from src.data_sources.sources.market.unified_options_tool import UnifiedOptionsDataTool

    voter = VoterAgent(use_gex=True)
    options_fetcher = UnifiedOptionsDataTool()

    # Fetch real options data
    options_chain = options_fetcher.fetch_options('SPY', '2024-11-29')

    if options_chain is not None:
        # Get price data
        price_data = voter._fetch_price_data('SPY', days=60)

        # Evaluate with triple voting
        result = voter.evaluate_voting(
            'SPY', price_data, options_chain=options_chain
        )

        assert result['action'] in ['BUY', 'SELL', 'HOLD']
        assert result['voting_mode'] == 'triple'
```

---

## 5. Configuration

### VoterAgent Configuration

File: `config_defaults/voting_config.yaml` (new or updated)

```yaml
voter_agent:
  default_mode: dual  # Can switch to triple

  dual_voting:
    enabled: true
    macd:
      fast: 13
      slow: 34
      signal: 8
      threshold: 0.1
    rsi:
      period: 14
      oversold: 30
      overbought: 70
    consensus_boost: 0.15
    weak_signal_boost: 0.1
    min_data_points: 42

  triple_voting:
    enabled: false  # Set to true to use GEX
    gex_generator:
      gex_threshold_positive: 500000000
      gex_threshold_negative: -500000000
      zero_gamma_tolerance: 0.01
      confidence_strong: 0.7
      confidence_moderate: 0.5
      confidence_hold: 0.3

    # How to weight voters in triple system
    voter_weights:
      macd: 0.333
      rsi: 0.333
      gex: 0.333
```

---

## 6. Implementation Checklist

### Phase 1: GEX Signal Generator (Weeks 1-3)

- [ ] Create `GEXSignalGenerator` class
- [ ] Implement `_calculate_gex_total()` method
- [ ] Implement `_find_zero_gamma_level()` method
- [ ] Implement `generate_signal()` method
- [ ] Write unit tests (target: >90% coverage)
- [ ] Validate GEX calculations against manual verification

### Phase 2: VoterAgent Integration (Weeks 4-6)

- [ ] Add `use_gex` parameter to `VoterAgent.__init__()`
- [ ] Add `gex_generator` initialization
- [ ] Implement `_get_gex_signal()` method
- [ ] Implement `_perform_triple_voting()` method
- [ ] Keep `_perform_dual_voting()` for backward compatibility
- [ ] Update `evaluate_voting()` to support both modes
- [ ] Write unit tests for voting logic
- [ ] Test backward compatibility

### Phase 3: Integration Testing (Weeks 7-9)

- [ ] Test with mock options data
- [ ] Test with real options data (Alpaca API)
- [ ] Validate backward compatibility
- [ ] Test error handling

### Phase 4: Backtesting (Weeks 10-12)

- [ ] Gather historical options data (2024-2025)
- [ ] Create backtest runner
- [ ] Compare MACD+RSI vs MACD+RSI+GEX
- [ ] Measure Sharpe ratio, win rate, drawdown
- [ ] Parameter tuning
- [ ] Document results

### Phase 5: Production Integration (Weeks 13-14)

- [ ] Add CLI flag: `--use-gex`
- [ ] Update documentation
- [ ] Paper trading validation
- [ ] Performance monitoring

---

## 7. References & Context

### Related GitHub Issues

- **#352**: GEX Integration Foundation
- **#367**: Advanced GEX Regime Detection
- **#394**: Forward Testing Metrics - GEX vs Traditional Technicals
- **#395**: Multi-Timeframe Ranked Voting System

### Research Sources

- @TailThatWagsDog (Twitter): Gamma Exposure analysis and dealer positioning
- SpotGamma: Gamma exposure dashboards
- SqueezeMetrics: GEX methodology

### Key Documentation

- [VoterAgent Implementation](../architecture/voting_system.md)
- [Options Analysis Infrastructure](../../issues/352)

---

## 8. Success Metrics

| Metric | Target | Threshold |
|--------|--------|-----------|
| **Sharpe Ratio** | > 0.69 | Beat the live-rules baseline (5 bps a side, #574) |
| **Win Rate** | ≥55% | Profitable majority |
| **GEX Precision** | ≥70% | Accurate signals |
| **False Positive Rate** | ≤25% | Avoid noise |
| **Unit Test Coverage** | ≥90% | Maintain high quality |
| **Backtest Period** | 2024-2025 | 12+ months |
| **Paper Trading** | 100% uptime | 7-10 days validation |

---

## 9. Troubleshooting Guide

### Issue: Options data not available

**Symptom**: GEX signals return HOLD with "No options data available"

**Cause**: UnifiedOptionsDataTool unable to fetch options chain

**Solution**:

1. Check Alpaca API credentials
2. Verify API key has options data access
3. Check network connectivity
4. Review TradingCacheManager logs

### Issue: GEX calculations seem incorrect

**Symptom**: GEX values far outside expected range

**Cause**: Incorrect spot price or gamma values in options chain

**Solution**:

1. Validate options chain DataFrame columns
2. Verify spot_price is populated correctly
3. Check gamma values are within [0, 1] range
4. Review data quality score in UnifiedOptionsDataTool

### Issue: Triple voting produces different signals than dual

**Symptom**: MACD+RSI+GEX result differs from MACD+RSI alone

**Cause**: GEX signal conflicts with MACD/RSI consensus

**Solution**:

1. Review GEX signal details (`return_components=True`)
2. Check zero gamma level vs current price
3. Verify dealer positioning classification
4. Tune GEX thresholds if needed

---

**Last Updated**: 2025-11-30
**Status**: Planning & Specification
**Author**: Claude Code
**Issue**: #419
