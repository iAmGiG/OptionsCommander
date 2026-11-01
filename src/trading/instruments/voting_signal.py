"""
MACD+RSI voting signal, free of any agent framework.

The signal maths (evaluate_voting, evaluate_ranked_voting), used by the CLI's
RealVoterStrategy and by research and backtests. It was VoterAgent's until that agent
was retired (#606, #331).
"""

import copy
import logging
from typing import Any, Dict, Optional, Tuple

import pandas as pd

from config_defaults.trading_config import TradingConfig

from src.core.ranked_voter_config import get_ranked_voter_manager
from src.trading.instruments.indicator_registry import get_indicator_registry
from src.trading.instruments.indicators import calculate_macd, calculate_rsi

logger = logging.getLogger(__name__)

VALID_TIMEFRAMES = ["1m", "5m", "15m", "30m", "1h", "2h", "4h", "1d", "1w", "1M"]
DEFAULT_TIMEFRAME = "1d"
DEFAULT_MACD = {"fast": 13, "slow": 34, "signal": 8}
DEFAULT_RSI = {"period": 14, "oversold": 30, "overbought": 70}
DEFAULT_THRESHOLDS = {
    "macd_threshold": 0.1,  # Histogram threshold for signal
    "consensus_boost": 0.15,  # Confidence boost for consensus
    "weak_signal_boost": 0.1,  # Confidence boost for weak signals
    "min_data_points": 42,  # Minimum data for reliable signals
}


class VotingSignal:
    """
    Parameterizable MACD+RSI voting (MACD 13/34/8, RSI 14 at 30/70 by default).

    evaluate_ranked_voting is the live CLI's signal; evaluate_voting is the fixed
    MACD+RSI vote. Both return a decision dict (action, confidence, position_size,
    reasoning, ...) and never raise: errors come back as HOLD with an "error" key.
    """

    def __init__(
        self,
        timeframe: Optional[str] = None,
        macd_params: Optional[Dict[str, int]] = None,
        rsi_params: Optional[Dict[str, int]] = None,
        voting_thresholds: Optional[Dict[str, float]] = None,
        use_config_file: bool = True,
    ):
        """
        Args:
            timeframe: Timeframe for analysis (5m, 15m, 30m, 1h, 4h, 1d, 1w, 1M). Default: 1d
            macd_params: Override MACD parameters {"fast": 13, "slow": 34, "signal": 8}
            rsi_params: Override RSI parameters {"period": 14, "oversold": 30, "overbought": 70}
            voting_thresholds: Override decision thresholds {"macd_threshold": 0.1, ...}
            use_config_file: Whether to load defaults from the trading config
        """
        self.config = TradingConfig() if use_config_file else None
        default_timeframe, default_macd, default_rsi = self._defaults()

        self.timeframe = timeframe or default_timeframe
        if self.timeframe not in VALID_TIMEFRAMES:
            logger.warning(
                f"Invalid timeframe '{self.timeframe}', defaulting to '{DEFAULT_TIMEFRAME}'. "
                f"Valid options: {VALID_TIMEFRAMES}"
            )
            self.timeframe = DEFAULT_TIMEFRAME

        # Copies: reconfigure() updates these in place, which must not reach a caller's dict
        self.macd_params = dict(macd_params or default_macd)
        self.rsi_params = dict(rsi_params or default_rsi)
        self.voting_thresholds = dict(voting_thresholds or DEFAULT_THRESHOLDS)
        self._sync_config()

    def _defaults(self) -> Tuple[str, Dict[str, int], Dict[str, int]]:
        """(timeframe, MACD, RSI) defaults: the trading config's when loaded, else built in."""
        if self.config is None:
            return DEFAULT_TIMEFRAME, dict(DEFAULT_MACD), dict(DEFAULT_RSI)
        macd, rsi = self.config.get_macd_config(), self.config.get_rsi_config()
        return (
            self.config.get_timeframe_config().default,
            {"fast": macd.fast, "slow": macd.slow, "signal": macd.signal},
            {"period": rsi.period, "oversold": rsi.oversold, "overbought": rsi.overbought},
        )

    def _sync_config(self) -> None:
        """Record the parameters in force (reported with each decision as parameters_used)."""
        self.current_config = {
            "timeframe": self.timeframe,
            "macd": dict(self.macd_params),
            "rsi": dict(self.rsi_params),
            "thresholds": dict(self.voting_thresholds),
        }

    def reconfigure(
        self,
        timeframe: Optional[str] = None,
        macd_params: Optional[Dict[str, int]] = None,
        rsi_params: Optional[Dict[str, int]] = None,
        voting_thresholds: Optional[Dict[str, float]] = None,
    ) -> None:
        """
        Reconfigure parameters on the fly for testing.

        This allows dynamic parameter testing without recreating the signal.

        Args:
            timeframe: New timeframe (5m, 15m, 30m, 1h, 4h, 1d, 1w, 1M)
            macd_params: New MACD parameters
            rsi_params: New RSI parameters
            voting_thresholds: New voting thresholds
        """
        if timeframe:
            if timeframe in VALID_TIMEFRAMES:
                self.timeframe = timeframe
            else:
                logger.warning(
                    f"Invalid timeframe '{timeframe}' ignored. Valid options: {VALID_TIMEFRAMES}"
                )
        if macd_params:
            self.macd_params.update(macd_params)
        if rsi_params:
            self.rsi_params.update(rsi_params)
        if voting_thresholds:
            self.voting_thresholds.update(voting_thresholds)
        self._sync_config()

        logger.info(f"VotingSignal reconfigured: {self.current_config}")

    def get_current_configuration(self) -> Dict[str, Any]:
        """Return current parameter configuration."""
        return copy.deepcopy(self.current_config)

    def reset_to_defaults(self, use_config_file: bool = True) -> None:
        """Reset the timeframe, MACD, RSI and thresholds to their defaults."""
        if use_config_file and self.config is not None:
            self.timeframe, self.macd_params, self.rsi_params = self._defaults()
        else:
            self.timeframe = DEFAULT_TIMEFRAME
            self.macd_params, self.rsi_params = dict(DEFAULT_MACD), dict(DEFAULT_RSI)
        self.voting_thresholds = dict(DEFAULT_THRESHOLDS)
        self._sync_config()

        logger.info(f"VotingSignal reset to defaults: {self.current_config}")

    def evaluate_voting(  # noqa: C901
        self, symbol: str, price_data: pd.DataFrame, return_components: bool = False
    ) -> Dict[str, Any]:
        """
        Core MACD+RSI voting logic with current parameters.

        Args:
            symbol: Stock symbol being evaluated
            price_data: DataFrame with price data (must have 'close' or 'Close' column)
            return_components: If True, return individual indicator signals

        Returns:
            Trading decision with confidence and reasoning
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
                    "parameters_used": copy.deepcopy(self.current_config),
                }

            # Extract price series
            prices = price_data["Close"] if "Close" in price_data.columns else price_data["close"]

            # Calculate MACD with current parameters
            macd_data = calculate_macd(
                prices,
                fast=self.macd_params["fast"],
                slow=self.macd_params["slow"],
                signal=self.macd_params["signal"],
            )

            # Calculate RSI with current parameters
            rsi_data = calculate_rsi(
                prices,
                period=self.rsi_params["period"],
                oversold=self.rsi_params["oversold"],
                overbought=self.rsi_params["overbought"],
            )

            # Determine MACD signal
            latest_histogram = macd_data["histogram"].iloc[-1]
            macd_threshold = self.voting_thresholds["macd_threshold"]

            if latest_histogram > macd_threshold:
                macd_action = "BUY"
                macd_conf = 0.6
                macd_strength = min(50.0, abs(latest_histogram) * 10)
            elif latest_histogram < -macd_threshold:
                macd_action = "SELL"
                macd_conf = 0.6
                macd_strength = -min(50.0, abs(latest_histogram) * 10)
            else:
                macd_action = "HOLD"
                macd_conf = 0.3
                macd_strength = 0.0

            # Determine RSI signal
            current_rsi = rsi_data["rsi"].iloc[-1]

            if current_rsi < self.rsi_params["oversold"]:
                rsi_action = "BUY"
                rsi_conf = 0.6
                rsi_strength = (self.rsi_params["oversold"] - current_rsi) * 3.33
            elif current_rsi > self.rsi_params["overbought"]:
                rsi_action = "SELL"
                rsi_conf = 0.6
                rsi_strength = (current_rsi - self.rsi_params["overbought"]) * 3.33
            else:
                rsi_action = "HOLD"
                rsi_conf = 0.3
                rsi_strength = 0.0

            # VOTING LOGIC (preserved from validated system)
            consensus_boost = self.voting_thresholds["consensus_boost"]
            weak_boost = self.voting_thresholds["weak_signal_boost"]

            if macd_action == rsi_action and macd_action != "HOLD":
                # Strong consensus
                action = macd_action
                confidence = min(0.85, (macd_conf + rsi_conf) / 2 + consensus_boost)
                position_size = 1.0
                reasoning = f"Strong consensus: Both MACD and RSI signal {action}"
                signal_type = "STRONG"

            elif (macd_action != "HOLD" and rsi_action == "HOLD") or (
                rsi_action != "HOLD" and macd_action == "HOLD"
            ):
                # Weak signal - use indicator strength to modulate confidence
                active_action = macd_action if macd_action != "HOLD" else rsi_action
                active_conf = macd_conf if macd_action != "HOLD" else rsi_conf
                active_strength = macd_strength if macd_action != "HOLD" else rsi_strength
                active_indicator = "MACD" if macd_action != "HOLD" else "RSI"

                action = active_action
                # Confidence scales with indicator strength (0-100% range)
                # Base: active_conf (0.6) + weak_boost (0.1) = 0.7 baseline
                # Strength adds 0-0.10 based on how strongly indicator triggered
                strength_bonus = min(0.10, active_strength / 100)
                confidence = min(0.80, active_conf + weak_boost + strength_bonus)
                position_size = 0.5
                reasoning = f"Weak signal: Only {active_indicator} signals {active_action}"
                signal_type = "WEAK"

            else:
                # Conflicting or neutral
                action = "HOLD"
                confidence = 0.2
                position_size = 0.0
                if macd_action != rsi_action and macd_action != "HOLD" and rsi_action != "HOLD":
                    reasoning = f"Conflicting signals: MACD={macd_action}, RSI={rsi_action}"
                    signal_type = "CONFLICT"
                else:
                    reasoning = "Both indicators neutral"
                    signal_type = "NEUTRAL"

            result = {
                "symbol": symbol,
                "action": action,
                "confidence": confidence,
                "position_size": position_size,
                "reasoning": reasoning,
                "signal_type": signal_type,
                "timeframe": self.timeframe,  # Issue #365: Explicit timeframe in results
                "current_price": float(prices.iloc[-1]),
                "parameters_used": copy.deepcopy(self.current_config),
            }

            # Add component details if requested
            if return_components:
                result["components"] = {
                    "macd": {
                        "action": macd_action,
                        "confidence": macd_conf,
                        "strength": macd_strength,
                        "histogram": float(latest_histogram),
                        "macd_line": float(macd_data["macd"].iloc[-1]),
                        "signal_line": float(macd_data["signal"].iloc[-1]),
                    },
                    "rsi": {
                        "action": rsi_action,
                        "confidence": rsi_conf,
                        "strength": rsi_strength,
                        "value": float(current_rsi),
                        "oversold": self.rsi_params["oversold"],
                        "overbought": self.rsi_params["overbought"],
                    },
                }

            return result

        except Exception as e:
            logger.error(f"Error in evaluate_voting for {symbol}: {e}")
            return {
                "symbol": symbol,
                "action": "HOLD",
                "confidence": 0.0,
                "position_size": 0.0,
                "reasoning": f"Analysis error: {str(e)}",
                "error": str(e),
                "parameters_used": copy.deepcopy(self.current_config),
            }

    def evaluate_ranked_voting(  # noqa: C901
        self, symbol: str, price_data: pd.DataFrame, return_components: bool = True
    ) -> Dict[str, Any]:
        """
        Evaluate voting using the indicator registry and ranked voter config.

        It reads its voters and thresholds from the ranked-voter config
        (get_ranked_voter_manager), not from this object's MACD/RSI/threshold parameters.

        Issue #364: Ranked Voter System for Technical Indicators

        This method uses the pluggable indicator registry instead of hardcoded
        MACD/RSI calls. Active voters from RankedVoterManager participate in
        decisions, while review-only voters are shown but don't affect the outcome.

        Args:
            symbol: Stock symbol being evaluated
            price_data: DataFrame with price data (must have 'close' or 'Close' column)
            return_components: If True, return individual indicator signals

        Returns:
            Trading decision with confidence, reasoning, and all voter signals
        """
        try:
            # Get ranked voter manager
            voter_manager = get_ranked_voter_manager()
            voting_config = voter_manager.get_voting_config()

            # Validate data sufficiency
            if len(price_data) < voting_config.min_data_points:
                return {
                    "symbol": symbol,
                    "action": "HOLD",
                    "confidence": 0.0,
                    "position_size": 0.0,
                    "reasoning": f"Insufficient data ({len(price_data)} < {voting_config.min_data_points})",
                    "voters": [],
                }

            # Extract price series
            prices = price_data["Close"] if "Close" in price_data.columns else price_data["close"]

            # Get indicator registry and create indicators from ranking
            registry = get_indicator_registry()
            registry.clear()  # Clear any previous indicators

            # Create indicators from ranked config
            ranking = voter_manager.get_ranking()
            for voter_cfg in ranking:
                indicator_config = voter_manager.get_indicator_config(voter_cfg.name)
                if indicator_config:
                    # Merge default params with custom params
                    params = indicator_config.get("default_params", {}).copy()
                    params.update(voter_cfg.params)
                    registry.create_indicator(
                        voter_cfg.name, f"{voter_cfg.name}_rank{voter_cfg.rank}", params
                    )

            # Get all signals
            all_signals = registry.get_all_signals(prices)

            # Separate active and review signals
            active_voters = voter_manager.get_active_voters()
            active_names = {f"{v.name}_rank{v.rank}" for v in active_voters}

            active_signals = {k: v for k, v in all_signals.items() if k in active_names}
            review_signals = {k: v for k, v in all_signals.items() if k not in active_names}

            # Voting logic for active voters
            buy_votes = sum(1 for s in active_signals.values() if s.action == "BUY")
            sell_votes = sum(1 for s in active_signals.values() if s.action == "SELL")
            hold_votes = sum(1 for s in active_signals.values() if s.action == "HOLD")
            total_active = len(active_signals)

            # Determine consensus
            if total_active == 0:
                action = "HOLD"
                confidence = 0.0
                position_size = 0.0
                reasoning = "No active voters configured"
                signal_type = "ERROR"
            elif buy_votes == total_active:
                action = "BUY"
                avg_conf = sum(s.confidence for s in active_signals.values()) / total_active
                confidence = min(0.85, avg_conf + voting_config.consensus_boost)
                position_size = voting_config.strong_signal_size
                reasoning = f"Strong consensus: All {total_active} active voters signal BUY"
                signal_type = "STRONG"
            elif sell_votes == total_active:
                action = "SELL"
                avg_conf = sum(s.confidence for s in active_signals.values()) / total_active
                confidence = min(0.85, avg_conf + voting_config.consensus_boost)
                position_size = voting_config.strong_signal_size
                reasoning = f"Strong consensus: All {total_active} active voters signal SELL"
                signal_type = "STRONG"
            elif buy_votes > 0 and sell_votes == 0:
                action = "BUY"
                buy_sigs = [s for s in active_signals.values() if s.action == "BUY"]
                avg_conf = sum(s.confidence for s in buy_sigs) / len(buy_sigs)
                confidence = min(0.80, avg_conf + voting_config.weak_signal_boost)
                position_size = voting_config.weak_signal_size
                reasoning = f"Weak signal: {buy_votes}/{total_active} voters signal BUY"
                signal_type = "WEAK"
            elif sell_votes > 0 and buy_votes == 0:
                action = "SELL"
                sell_sigs = [s for s in active_signals.values() if s.action == "SELL"]
                avg_conf = sum(s.confidence for s in sell_sigs) / len(sell_sigs)
                confidence = min(0.80, avg_conf + voting_config.weak_signal_boost)
                position_size = voting_config.weak_signal_size
                reasoning = f"Weak signal: {sell_votes}/{total_active} voters signal SELL"
                signal_type = "WEAK"
            elif buy_votes > 0 and sell_votes > 0:
                action = "HOLD"
                confidence = 0.2 - voting_config.conflict_penalty
                position_size = voting_config.conflict_size
                reasoning = f"Conflicting signals: {buy_votes} BUY, {sell_votes} SELL"
                signal_type = "CONFLICT"
            else:
                action = "HOLD"
                confidence = 0.2
                position_size = 0.0
                reasoning = "All voters neutral"
                signal_type = "NEUTRAL"

            result = {
                "symbol": symbol,
                "action": action,
                "confidence": max(0.0, confidence),
                "position_size": position_size,
                "reasoning": reasoning,
                "signal_type": signal_type,
                "timeframe": self.timeframe,
                "current_price": float(prices.iloc[-1]),
                "ranked_voting": True,
                "active_voter_count": total_active,
                "vote_breakdown": {"buy": buy_votes, "sell": sell_votes, "hold": hold_votes},
            }

            # Add voter details if requested
            if return_components:
                result["active_voters"] = [
                    {
                        "name": name,
                        "action": sig.action,
                        "confidence": sig.confidence,
                        "strength": sig.strength,
                        "value": sig.value,
                    }
                    for name, sig in active_signals.items()
                ]
                result["review_voters"] = [
                    {
                        "name": name,
                        "action": sig.action,
                        "confidence": sig.confidence,
                        "strength": sig.strength,
                        "value": sig.value,
                    }
                    for name, sig in review_signals.items()
                ]

            return result

        except Exception as e:
            logger.error(f"Error in evaluate_ranked_voting for {symbol}: {e}")
            return {
                "symbol": symbol,
                "action": "HOLD",
                "confidence": 0.0,
                "position_size": 0.0,
                "reasoning": f"Analysis error: {str(e)}",
                "error": str(e),
                "ranked_voting": True,
            }
