#!/usr/bin/env python3
"""
Multi-Timeframe Ranked Voting System

Issue #395: Implement multi-timeframe voting that runs VotingSignal across
multiple timeframes simultaneously with weighted consensus.

Concept: Timeframe confluence - signals from multiple timeframes must align
for strong trades. Classic TA principle: "Trade with the trend, time with
the pullback."

Dependencies:
- Issue #364 (Ranked Voter System) - Foundation for indicator voting
- Issue #365 (Timeframe Specification) - Timeframe infrastructure
"""

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Dict, Optional

import pandas as pd

from src.trading.instruments.data_fetch import fetch_market_data
from src.trading.instruments.timeframe_tools import convert_to_alpaca_timeframe
from src.trading.instruments.voting_signal import VotingSignal

logger = logging.getLogger(__name__)


@dataclass
class TimeframeResult:
    """Result from a single timeframe evaluation."""

    timeframe: str
    action: str  # BUY, SELL, HOLD
    confidence: float
    weight: float
    reasoning: str
    indicators: Dict[str, Any] = field(default_factory=dict)


@dataclass
class MultiTimeframeResult:
    """Aggregated result from multi-timeframe voting."""

    symbol: str
    action: str  # BUY, SELL, HOLD
    confidence: float
    signal_type: str  # STRONG, MODERATE, WEAK, CONFLICT
    reasoning: str
    weighted_score: float
    timeframes_aligned: int
    timeframes_total: int
    consensus_strength: str  # UNANIMOUS, MAJORITY, SPLIT
    timeframe_breakdown: Dict[str, TimeframeResult] = field(default_factory=dict)


# Default timeframe configurations (presets)
MULTI_TIMEFRAME_PRESETS = {
    "trend_following": {
        "description": "Trend direction with entry timing",
        "timeframes": {"1d": 0.5, "4h": 0.3, "1h": 0.2},
        "min_data_days": {"1d": 60, "4h": 10, "1h": 3},
    },
    "intraday": {
        "description": "Intraday swing trading with precise entries",
        "timeframes": {"4h": 0.4, "1h": 0.35, "15m": 0.25},
        "min_data_days": {"4h": 10, "1h": 3, "15m": 1},
    },
    "position": {
        "description": "Long-term position trading",
        "timeframes": {"1w": 0.5, "1d": 0.35, "4h": 0.15},
        "min_data_days": {"1w": 180, "1d": 60, "4h": 10},
    },
    "scalping": {
        "description": "Fast scalping with micro trends",
        "timeframes": {"1h": 0.4, "15m": 0.35, "5m": 0.25},
        "min_data_days": {"1h": 3, "15m": 1, "5m": 1},
    },
}


class MultiTimeframeVoter:
    """
    Multi-timeframe ranked voting system.

    Runs VotingSignal's ranked voting across multiple timeframes and aggregates
    results using weighted consensus voting.

    Usage:
        voter = MultiTimeframeVoter(preset="trend_following")
        result = voter.evaluate_multi_timeframe("AAPL")
    """

    def __init__(
        self,
        preset: Optional[str] = "trend_following",
        custom_timeframes: Optional[Dict[str, float]] = None,
    ):
        """
        Initialize multi-timeframe voter.

        Args:
            preset: Preset name from MULTI_TIMEFRAME_PRESETS
            custom_timeframes: Custom timeframe weights (overrides preset)
        """
        if custom_timeframes:
            self.timeframe_weights = custom_timeframes
            self.preset_name = "custom"
            self.min_data_days = self._infer_min_data_days(custom_timeframes)
        elif preset and preset in MULTI_TIMEFRAME_PRESETS:
            config = MULTI_TIMEFRAME_PRESETS[preset]
            self.timeframe_weights = config["timeframes"]
            self.preset_name = preset
            self.min_data_days = config["min_data_days"]
        else:
            # Default to trend_following
            config = MULTI_TIMEFRAME_PRESETS["trend_following"]
            self.timeframe_weights = config["timeframes"]
            self.preset_name = "trend_following"
            self.min_data_days = config["min_data_days"]

        self._voter: Optional[VotingSignal] = None

        logger.info(
            f"MultiTimeframeVoter initialized with preset '{self.preset_name}': "
            f"{list(self.timeframe_weights.keys())}"
        )

    def _infer_min_data_days(self, timeframes: Dict[str, float]) -> Dict[str, int]:
        """Infer minimum data days based on timeframe."""
        defaults = {
            "1M": 365,
            "1w": 180,
            "1d": 60,
            "4h": 10,
            "2h": 5,
            "1h": 3,
            "30m": 2,
            "15m": 1,
            "5m": 1,
            "1m": 1,
        }
        return {tf: defaults.get(tf, 30) for tf in timeframes}

    def _get_voter(self) -> VotingSignal:
        """Get or create the voting signal."""
        if self._voter is None:
            self._voter = VotingSignal()
        return self._voter

    def _fetch_data_for_timeframe(
        self, symbol: str, timeframe: str, end_date: Optional[datetime] = None
    ) -> Optional[pd.DataFrame]:
        """
        Fetch price data for a specific timeframe.

        Args:
            symbol: Stock symbol
            timeframe: Timeframe string (e.g., "1d", "4h")
            end_date: End date (defaults to today)

        Returns:
            DataFrame with OHLCV data or None
        """
        if end_date is None:
            end_date = datetime.now()

        min_days = self.min_data_days.get(timeframe, 60)
        start_date = end_date - timedelta(days=min_days)

        # Convert to Alpaca format
        alpaca_tf = convert_to_alpaca_timeframe(timeframe)

        try:
            data = fetch_market_data(
                symbol=symbol,
                start_date=start_date.strftime("%Y-%m-%d"),
                end_date=end_date.strftime("%Y-%m-%d"),
                timeframe=alpaca_tf,
            )
            if data is not None and not data.empty:
                logger.debug(f"Fetched {len(data)} bars for {symbol} @ {timeframe}")
                return data
        except Exception as e:
            logger.warning(f"Failed to fetch {timeframe} data for {symbol}: {e}")

        return None

    def _evaluate_single_timeframe(
        self, symbol: str, timeframe: str, weight: float, price_data: pd.DataFrame
    ) -> Optional[TimeframeResult]:
        """
        Run ranked voting on a single timeframe.

        Args:
            symbol: Stock symbol
            timeframe: Timeframe being evaluated
            weight: Weight for this timeframe
            price_data: OHLCV data for this timeframe

        Returns:
            TimeframeResult or None if evaluation failed
        """
        try:
            voter = self._get_voter()
            result = voter.evaluate_ranked_voting(symbol, price_data)

            return TimeframeResult(
                timeframe=timeframe,
                action=result.get("action", "HOLD"),
                confidence=result.get("confidence", 0.0),
                weight=weight,
                reasoning=result.get("reasoning", ""),
                indicators=result.get("vote_breakdown", {}),
            )
        except Exception as e:
            logger.warning(f"Failed to evaluate {timeframe} for {symbol}: {e}")
            return None

    def evaluate_multi_timeframe(
        self,
        symbol: str,
        price_data: Optional[Dict[str, pd.DataFrame]] = None,
        end_date: Optional[datetime] = None,
    ) -> MultiTimeframeResult:
        """
        Evaluate trading signal using multiple timeframes with weighted voting.

        Args:
            symbol: Stock symbol to evaluate
            price_data: Optional pre-fetched data {timeframe: DataFrame}
            end_date: End date for data fetching (defaults to today)

        Returns:
            MultiTimeframeResult with aggregated decision
        """
        timeframe_results: Dict[str, TimeframeResult] = {}

        # Fetch data and evaluate each timeframe
        for timeframe, weight in self.timeframe_weights.items():
            # Use provided data or fetch
            if price_data and timeframe in price_data:
                data = price_data[timeframe]
            else:
                data = self._fetch_data_for_timeframe(symbol, timeframe, end_date)

            if data is None or data.empty:
                logger.warning(f"No data for {symbol} @ {timeframe}, skipping")
                continue

            result = self._evaluate_single_timeframe(symbol, timeframe, weight, data)
            if result:
                timeframe_results[timeframe] = result

        # Aggregate results
        return self._aggregate_results(symbol, timeframe_results)

    async def evaluate_multi_timeframe_async(
        self,
        symbol: str,
        end_date: Optional[datetime] = None,
    ) -> MultiTimeframeResult:
        """
        Async version - fetches all timeframes in parallel.

        Args:
            symbol: Stock symbol to evaluate
            end_date: End date for data fetching

        Returns:
            MultiTimeframeResult with aggregated decision
        """

        async def fetch_and_evaluate(tf: str, weight: float):
            # Run in executor since fetch_market_data is sync
            loop = asyncio.get_event_loop()
            data = await loop.run_in_executor(
                None, lambda: self._fetch_data_for_timeframe(symbol, tf, end_date)
            )
            if data is None or data.empty:
                return None
            return await loop.run_in_executor(
                None, lambda: self._evaluate_single_timeframe(symbol, tf, weight, data)
            )

        # Run all timeframes in parallel
        tasks = [fetch_and_evaluate(tf, weight) for tf, weight in self.timeframe_weights.items()]
        results = await asyncio.gather(*tasks)

        # Build results dict
        timeframe_results = {}
        for result in results:
            if result:
                timeframe_results[result.timeframe] = result

        return self._aggregate_results(symbol, timeframe_results)

    def _aggregate_results(
        self, symbol: str, timeframe_results: Dict[str, TimeframeResult]
    ) -> MultiTimeframeResult:
        """
        Aggregate timeframe results using weighted voting.

        Voting logic:
        - UNANIMOUS: All timeframes agree -> STRONG signal
        - MAJORITY: Most timeframes agree -> MODERATE signal
        - SPLIT: No clear consensus -> WEAK/HOLD signal
        """
        if not timeframe_results:
            return MultiTimeframeResult(
                symbol=symbol,
                action="HOLD",
                confidence=0.0,
                signal_type="WEAK",
                reasoning="No timeframe data available",
                weighted_score=0.0,
                timeframes_aligned=0,
                timeframes_total=len(self.timeframe_weights),
                consensus_strength="NONE",
                timeframe_breakdown={},
            )

        # Count votes by action
        buy_weight = 0.0
        sell_weight = 0.0
        hold_weight = 0.0
        total_weight = 0.0

        buy_count = 0
        sell_count = 0
        hold_count = 0

        for result in timeframe_results.values():
            total_weight += result.weight
            if result.action == "BUY":
                buy_weight += result.weight * result.confidence
                buy_count += 1
            elif result.action == "SELL":
                sell_weight += result.weight * result.confidence
                sell_count += 1
            else:
                hold_weight += result.weight
                hold_count += 1

        # Normalize weights
        if total_weight > 0:
            buy_score = buy_weight / total_weight
            sell_score = sell_weight / total_weight
            hold_score = hold_weight / total_weight
        else:
            buy_score = sell_score = hold_score = 0.0

        # Determine action and consensus
        total_timeframes = len(timeframe_results)

        if buy_count == total_timeframes:
            action = "BUY"
            consensus = "UNANIMOUS"
            signal_type = "STRONG"
            confidence = buy_score
            aligned = buy_count
        elif sell_count == total_timeframes:
            action = "SELL"
            consensus = "UNANIMOUS"
            signal_type = "STRONG"
            confidence = sell_score
            aligned = sell_count
        elif buy_score > sell_score and buy_count > total_timeframes / 2:
            action = "BUY"
            consensus = "MAJORITY"
            signal_type = "MODERATE"
            confidence = buy_score * 0.8  # Reduce confidence for non-unanimous
            aligned = buy_count
        elif sell_score > buy_score and sell_count > total_timeframes / 2:
            action = "SELL"
            consensus = "MAJORITY"
            signal_type = "MODERATE"
            confidence = sell_score * 0.8
            aligned = sell_count
        else:
            # Conflicting or no consensus
            action = "HOLD"
            consensus = "SPLIT"
            signal_type = "WEAK" if max(buy_score, sell_score) > 0.3 else "CONFLICT"
            confidence = 1 - max(buy_score, sell_score)  # Lower conf for conflicts
            aligned = hold_count

        # Build reasoning
        tf_summary = ", ".join([f"{r.timeframe}={r.action}" for r in timeframe_results.values()])
        reasoning = f"Multi-TF {consensus.lower()}: {tf_summary}"

        weighted_score = max(buy_score, sell_score, hold_score)

        return MultiTimeframeResult(
            symbol=symbol,
            action=action,
            confidence=round(confidence, 3),
            signal_type=signal_type,
            reasoning=reasoning,
            weighted_score=round(weighted_score, 3),
            timeframes_aligned=aligned,
            timeframes_total=total_timeframes,
            consensus_strength=consensus,
            timeframe_breakdown=timeframe_results,
        )

    def get_available_presets(self) -> Dict[str, str]:
        """Get available preset configurations with descriptions."""
        return {name: cfg["description"] for name, cfg in MULTI_TIMEFRAME_PRESETS.items()}

    def set_preset(self, preset_name: str) -> bool:
        """
        Switch to a different preset configuration.

        Args:
            preset_name: Name of preset to apply

        Returns:
            True if preset was applied, False if not found
        """
        if preset_name not in MULTI_TIMEFRAME_PRESETS:
            logger.warning(f"Preset not found: {preset_name}")
            return False

        config = MULTI_TIMEFRAME_PRESETS[preset_name]
        self.timeframe_weights = config["timeframes"]
        self.preset_name = preset_name
        self.min_data_days = config["min_data_days"]
        logger.info(f"Switched to preset '{preset_name}': {list(self.timeframe_weights.keys())}")
        return True

    def to_dict(self) -> Dict[str, Any]:
        """Convert current configuration to dictionary."""
        return {
            "preset": self.preset_name,
            "timeframes": self.timeframe_weights,
            "min_data_days": self.min_data_days,
        }


# Singleton instance
_multi_tf_voter: Optional[MultiTimeframeVoter] = None


def get_multi_timeframe_voter(preset: str = "trend_following") -> MultiTimeframeVoter:
    """Get global MultiTimeframeVoter instance."""
    global _multi_tf_voter
    if _multi_tf_voter is None:
        _multi_tf_voter = MultiTimeframeVoter(preset=preset)
    return _multi_tf_voter


def evaluate_multi_timeframe(
    symbol: str,
    preset: str = "trend_following",
    custom_timeframes: Optional[Dict[str, float]] = None,
) -> Dict[str, Any]:
    """
    Convenience function to evaluate multi-timeframe voting.

    Args:
        symbol: Stock symbol
        preset: Preset configuration name
        custom_timeframes: Optional custom timeframe weights

    Returns:
        Dictionary with multi-timeframe result
    """
    voter = MultiTimeframeVoter(preset=preset, custom_timeframes=custom_timeframes)
    result = voter.evaluate_multi_timeframe(symbol)

    # Convert to dict for JSON serialization
    return {
        "symbol": result.symbol,
        "action": result.action,
        "confidence": result.confidence,
        "signal_type": result.signal_type,
        "reasoning": result.reasoning,
        "weighted_score": result.weighted_score,
        "timeframes_aligned": result.timeframes_aligned,
        "timeframes_total": result.timeframes_total,
        "consensus_strength": result.consensus_strength,
        "timeframe_breakdown": {
            tf: {
                "timeframe": r.timeframe,
                "action": r.action,
                "confidence": r.confidence,
                "weight": r.weight,
                "reasoning": r.reasoning,
            }
            for tf, r in result.timeframe_breakdown.items()
        },
    }
