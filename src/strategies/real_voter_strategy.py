"""
Real VoterStrategy - The production VotingSignal with MACD+RSI analysis.

This adapter integrates the production VotingSignal into
the plugin architecture while handling market data fetching.
"""

import logging
from datetime import timedelta
from typing import Any, Dict, Optional

import pandas as pd

from config_defaults.trading_config import TradingConfig

from src.cli.tools.timeframe_tools import get_multi_timeframe_params
from src.core.interfaces.strategy_analyzer import StrategyAnalyzer
from src.core.models import AnalysisResult, AssetType, Signal, TradeRequest
from src.core.ranked_voter_config import RankedVoterManager
from src.core.trading_modes import ModeParameters, get_mode_manager
from src.database import AnalysisHistoryManager
from src.market_data.unified_market_tool import fetch_unified_market_data
from src.trading.instruments.entry_planning import calculate_entry_plan
from src.trading.instruments.multi_timeframe_voter import MultiTimeframeVoter
from src.trading.instruments.timeframe_tools import (
    convert_to_alpaca_timeframe,
    get_current_timeframe,
)
from src.trading.instruments.voting_signal import VotingSignal
from src.utils.date_utils import get_datetime_now

logger = logging.getLogger(__name__)


class RealVoterStrategy(StrategyAnalyzer):
    """
    Production VoterStrategy using real MACD+RSI analysis.

    Features:
    - Production VotingSignal (MACD+RSI voting)
    - Real market data fetching (Alpaca, Alpha Vantage)
    - MACD(13/34/8) + RSI(14) voting system
    - Confidence-based position sizing
    """

    # Minimum candles required for MACD calculation: 34 (slow) + 8 (signal)
    MIN_REQUIRED_CANDLES = 42

    def __init__(
        self,
        macd_params: Optional[Dict[str, int]] = None,
        rsi_params: Optional[Dict[str, int]] = None,
        lookback_days: int = 60,
        mode_params: Optional[ModeParameters] = None,
    ):
        """
        Initialize RealVoterStrategy.

        Args:
            macd_params: MACD parameters (default: {fast: 13, slow: 34, signal: 8})
            rsi_params: RSI parameters (default: {period: 14, oversold: 30, overbought: 70})
            lookback_days: Days of historical data to fetch (default: 60)
            mode_params: Trading mode parameters for stop/target (Issue #400)
        """
        # Use validated production parameters by default
        self.macd_params = macd_params or {"fast": 13, "slow": 34, "signal": 8}
        self.rsi_params = rsi_params or {"period": 14, "oversold": 30, "overbought": 70}
        self.lookback_days = lookback_days

        # Use provided mode params or get from global mode manager (Issue #400)
        self.mode_params = mode_params or get_mode_manager().get_parameters()

        # Load trading config (fallback for other settings)
        self.config = TradingConfig()

        self.voter = VotingSignal(
            macd_params=self.macd_params,
            rsi_params=self.rsi_params,
            use_config_file=True,
        )

        # Initialize analysis history manager for ML tracking
        self.analysis_history = AnalysisHistoryManager()

        logger.info(
            f"RealVoterStrategy initialized with MACD({self.macd_params['fast']}/{self.macd_params['slow']}/{self.macd_params['signal']}) + RSI({self.rsi_params['period']})"
        )

    @property
    def name(self) -> str:
        """Strategy name."""
        return "VoterStrategy (MACD+RSI)"

    @property
    def supported_asset_types(self) -> list:
        """Currently supports stocks only."""
        return [AssetType.STOCK]

    async def analyze(self, request: TradeRequest) -> AnalysisResult:  # noqa: C901
        """
        Analyze trade request using MACD+RSI voting.

        Args:
            request: Trade request with ticker and optional price

        Returns:
            AnalysisResult with signal, confidence, entry/stop/target, and reasoning
        """
        ticker = request.ticker

        try:
            user_timeframe, market_data = self._fetch_and_prepare_data(ticker)

            # Issue #505: Check if multi-timeframe mode is active
            tf_params = get_multi_timeframe_params()
            use_multi_tf = tf_params.get("mode") == "multi"

            # Issue #504: Check if ranked voting is enabled
            # A fresh manager reloads the ranking persisted in user.db, so changes made
            # with /voters in another process (e.g. the CLI vs the scheduler) apply
            ranked_voter = RankedVoterManager()
            active_voters = ranked_voter.get_active_voters()
            use_ranked = len(active_voters) > 1  # Use ranked if multiple active voters

            if use_multi_tf:
                # Issue #505: Use MultiTimeframeVoter for multi-TF analysis
                preset = tf_params.get("preset", "trend_following")
                logger.info(f"Using multi-timeframe voting for {ticker} (preset: {preset})")
                mtf_voter = MultiTimeframeVoter(preset=preset)
                mtf_result = mtf_voter.evaluate_multi_timeframe(ticker)

                # Convert MultiTimeframeResult to dict format expected by downstream
                result = {
                    "symbol": mtf_result.symbol,
                    "action": mtf_result.action,
                    "confidence": mtf_result.confidence,
                    "signal_type": mtf_result.signal_type,
                    "reasoning": mtf_result.reasoning,
                    "current_price": (
                        market_data["Close"].iloc[-1] if "Close" in market_data.columns else 0
                    ),
                    "position_size": 1.0 if mtf_result.signal_type == "STRONG" else 0.5,
                    "voting_mode": "multi_timeframe",
                    "multi_tf_preset": preset,
                    "timeframes_aligned": mtf_result.timeframes_aligned,
                    "timeframes_total": mtf_result.timeframes_total,
                    "consensus_strength": mtf_result.consensus_strength,
                }
            elif use_ranked:
                logger.info(
                    f"Using ranked voting for {ticker} with {len(active_voters)} active voters"
                )
                result = self.voter.evaluate_ranked_voting(
                    ticker, market_data, return_components=True
                )
                # Add voting mode indicator
                if isinstance(result, dict):
                    result["voting_mode"] = "ranked"
                    result["active_voters"] = [v.name for v in active_voters]
            else:
                logger.info(f"Using standard voting for {ticker}")
                result = self.voter.evaluate_voting(ticker, market_data, return_components=True)
                # Add voting mode indicator
                if isinstance(result, dict):
                    result["voting_mode"] = "standard"
                    result["active_voters"] = [v.name for v in active_voters]

            formatted = self._format_analysis_result(result, request, user_timeframe, market_data)
            return formatted
        except ValueError:
            # Re-raise ValueError (user-friendly messages already set)
            raise
        except Exception as e:
            # Log unexpected errors and return generic message
            logger.error(f"Unexpected error analyzing {ticker}: {e}", exc_info=True)
            raise ValueError("Error processing request")

    def _fetch_and_prepare_data(self, ticker: str) -> tuple[str, Any]:  # noqa: C901
        """
        Fetches and validates market data for the given ticker.

        Returns:
            Tuple of (user_timeframe, market_data)
        """
        # 1. Get timeframe and calculate lookback
        timeframe_info = get_current_timeframe()
        user_timeframe = timeframe_info.get("current_timeframe", "1d")
        alpaca_timeframe = convert_to_alpaca_timeframe(user_timeframe)

        # Calculate lookback based on timeframe to ensure 42+ candles for MACD
        # Need 34 (slow) + 8 (signal) = 42 periods minimum
        min_candles = 50  # Buffer for calculation
        lookback_days = self.lookback_days

        # Strategy: Always fetch DAILY data for cache efficiency, then resample
        # This minimizes API calls and maximizes cache hits
        fetch_timeframe = "1Day"  # Always fetch daily
        resample_needed = False

        if user_timeframe.endswith("M"):  # Monthly
            # Monthly candles: need ~4 years of daily data
            lookback_days = max(self.lookback_days, min_candles * 30)
            resample_needed = True
        elif user_timeframe.endswith("w"):  # Weekly
            # Weekly candles: need ~1 year of daily data
            lookback_days = max(self.lookback_days, min_candles * 7)
            resample_needed = True
        elif user_timeframe.endswith("d"):  # Daily
            # Daily candles: fetch daily directly
            lookback_days = max(self.lookback_days, min_candles)
        elif user_timeframe.endswith("h"):  # Hourly
            # Hourly needs actual hourly data from API (can't resample from daily)
            fetch_timeframe = alpaca_timeframe
            lookback_days = max(self.lookback_days, 30)
        elif user_timeframe.endswith("m"):  # Minutes
            # Minutes need actual minute data from API
            fetch_timeframe = alpaca_timeframe
            lookback_days = max(self.lookback_days, 14)

        # 2. Fetch market data
        logger.info(
            f"Fetching market data for {ticker} ({lookback_days} days, {user_timeframe}, fetch_tf={fetch_timeframe})..."
        )

        end_date = get_datetime_now().strftime("%Y-%m-%d")
        start_date = (get_datetime_now() - timedelta(days=lookback_days)).strftime("%Y-%m-%d")

        try:
            market_data = fetch_unified_market_data(
                ticker, start_date=start_date, end_date=end_date, timeframe=fetch_timeframe
            )
            bars_count = (
                len(market_data) if market_data is not None and not market_data.empty else 0
            )
            logger.info(f"fetch_unified_market_data returned: {bars_count} bars")
        except Exception as api_error:
            logger.debug(f"API error fetching data for {ticker}: {api_error}", exc_info=True)
            raise ValueError("Ticker not found")

        if market_data is None or market_data.empty:
            logger.info(f"No market data returned for {ticker}")
            raise ValueError("Ticker not found")

        if "Close" not in market_data.columns and "close" in market_data.columns:
            market_data["Close"] = market_data["close"]

        # 3. Resample if needed (weekly/monthly from daily data)
        if resample_needed:
            logger.info(f"Resampling daily data to {user_timeframe}...")
            market_data = self._resample_timeframe(market_data, user_timeframe)

        if len(market_data) < self.MIN_REQUIRED_CANDLES:
            logger.info(
                f"Insufficient data for {ticker}: {len(market_data)} points (need {self.MIN_REQUIRED_CANDLES}+)"
            )
            raise ValueError("Data unavailable")

        logger.info(f"✅ Loaded {len(market_data)} data points for {ticker}")
        return user_timeframe, market_data

    def _resample_timeframe(self, daily_data: pd.DataFrame, target_timeframe: str) -> pd.DataFrame:
        """
        Resample daily OHLCV data to weekly or monthly timeframe.

        Args:
            daily_data: DataFrame with daily OHLCV data
            target_timeframe: Target timeframe (e.g., '1w', '1M')

        Returns:
            Resampled DataFrame
        """
        # Map timeframe to pandas resample rule
        resample_rules = {
            "1w": "W-FRI",  # Weekly ending Friday
            "1M": "ME",  # Monthly (end of month) - updated for pandas 2.2+
        }

        rule = resample_rules.get(target_timeframe)
        if not rule:
            logger.warning(f"Unknown resample timeframe: {target_timeframe}, returning daily")
            return daily_data

        # Resample OHLCV data
        resampled = (
            daily_data.resample(rule)
            .agg(
                {
                    "Open": "first",
                    "High": "max",
                    "Low": "min",
                    "Close": "last",
                    "Volume": "sum",
                }
            )
            .dropna()
        )

        logger.debug(
            f"Resampled {len(daily_data)} daily bars → {len(resampled)} {target_timeframe} bars"
        )
        return resampled

    def _format_analysis_result(
        self,
        result: Dict[str, Any],
        request: TradeRequest,
        user_timeframe: str,
        market_data: pd.DataFrame,
    ) -> AnalysisResult:
        """Converts the agent's raw result into a structured AnalysisResult."""
        signal_map = {"BUY": Signal.BUY, "SELL": Signal.SELL, "HOLD": Signal.HOLD}
        signal = signal_map.get(result["action"], Signal.HOLD)
        current_price = result.get("current_price", request.price or 0.0)

        # Calculate entry/stop/target prices
        entry_price, stop_loss, take_profit = self._calculate_price_levels(
            signal, current_price, market_data, result["action"]
        )

        # Build reasoning list
        reasoning = self._build_reasoning_list(result, user_timeframe)

        # Extract indicators
        indicators = self._extract_indicators(result, user_timeframe)

        # Record analysis for ML training and strategy improvement
        self._record_analysis(request.ticker, user_timeframe, result, signal)

        return AnalysisResult(
            signal=signal,
            confidence=result["confidence"],
            entry_price=entry_price,
            stop_loss=stop_loss,
            take_profit=take_profit,
            reasoning=reasoning,
            indicators=indicators,
            analyzer_name=self.name,
        )

    def _calculate_price_levels(
        self,
        signal: Signal,
        current_price: float,
        market_data: Optional[pd.DataFrame] = None,
        signal_direction: Optional[str] = None,
    ) -> tuple[Optional[float], Optional[float], Optional[float]]:
        """
        Calculates entry, stop loss, and take profit based on the signal.

        Uses OHLCV-based entry planning (Issue #366) when market data is available,
        with ATR-based stops and support/resistance awareness. Falls back to
        trading mode percentages (Issue #400) if OHLCV data unavailable.

        Args:
            signal: Signal enum (BUY/SELL/HOLD)
            current_price: Current market price
            market_data: Optional OHLCV DataFrame for ATR-based calculations
            signal_direction: Optional signal direction string ("BUY"/"SELL")

        Returns:
            Tuple of (entry_price, stop_loss, take_profit)
        """
        if signal == Signal.HOLD:
            return None, None, None

        # Try OHLCV-based entry planning (Issue #366)
        if market_data is not None and not market_data.empty and signal_direction:
            try:
                # Ensure required columns exist
                required_cols = {"High", "Low", "Close", "Volume"}
                if required_cols.issubset(set(market_data.columns)):
                    entry_plan = calculate_entry_plan(
                        ohlcv=market_data,
                        current_price=current_price,
                        signal_direction=signal_direction,
                        atr_multiplier=2.0,
                        risk_reward_ratio=2.0,
                    )

                    if entry_plan.get("plan_quality") != "INSUFFICIENT_DATA":
                        logger.debug(
                            f"OHLCV Entry Plan: {entry_plan['plan_quality']} - "
                            f"ATR: {entry_plan['atr_value']}, S/R: {entry_plan['support']}/{entry_plan['resistance']}"
                        )
                        return (
                            entry_plan["entry_price"],
                            entry_plan["stop_loss"],
                            entry_plan["take_profit"],
                        )
            except Exception as e:
                logger.debug(f"OHLCV entry planning failed, using fallback: {e}")

        # Fallback: Use trading mode parameters (Issue #400)
        stop_loss_pct = self.mode_params.stop_loss
        take_profit_pct = self.mode_params.take_profit
        entry_price = round(current_price, 2)

        if signal == Signal.BUY:
            stop_loss = round(current_price * (1 - stop_loss_pct), 2)
            take_profit = round(current_price * (1 + take_profit_pct), 2)
        else:  # SELL
            stop_loss = round(current_price * (1 + stop_loss_pct), 2)
            take_profit = round(current_price * (1 - take_profit_pct), 2)

        return entry_price, stop_loss, take_profit

    def _build_reasoning_list(self, result: Dict[str, Any], user_timeframe: str) -> list[str]:
        """Constructs the human-readable reasoning for the analysis."""
        reasoning = [result["reasoning"], f"Timeframe: {user_timeframe}"]

        if "components" in result:
            macd = result["components"]["macd"]
            rsi = result["components"]["rsi"]
            reasoning.append(f"MACD: {macd['action']} (histogram: {macd['histogram']:.6f})")
            reasoning.append(f"RSI: {rsi['action']} (value: {rsi['value']:.1f})")

        signal_type = result.get("signal_type", "UNKNOWN")
        if signal_type == "STRONG":
            reasoning.append("✅ Strong consensus between indicators")
        elif signal_type == "WEAK":
            reasoning.append("⚠️ Weak signal from single indicator")
        elif signal_type == "CONFLICT":
            reasoning.append("⚠️ Conflicting signals between indicators")

        return reasoning

    def _extract_indicators(self, result: Dict[str, Any], user_timeframe: str) -> Dict[str, Any]:
        """Extracts key indicator values for logging and further analysis."""
        components = result.get("components", {})
        macd = components.get("macd", {})
        rsi = components.get("rsi", {})

        return {
            "macd_histogram": macd.get("histogram", 0.0),
            "rsi_value": rsi.get("value", 50.0),
            "signal_type": result.get("signal_type", "UNKNOWN"),
            "position_size_multiplier": result.get("position_size", 1.0),
            "timeframe": user_timeframe,
            "current_price": result.get(
                "current_price", 0.0
            ),  # Issue #474: For HOLD signal overrides
        }

    def _record_analysis(
        self, ticker: str, timeframe: str, result: Dict[str, Any], signal: Signal
    ) -> None:
        """
        Records analysis details to database for ML training and strategy analysis.

        Args:
            ticker: Stock symbol
            timeframe: Current timeframe
            result: VotingSignal evaluation result
            signal: Final signal (BUY/SELL/HOLD)
        """
        try:
            components = result.get("components", {})
            macd = components.get("macd", {})
            rsi = components.get("rsi", {})

            # Extract individual component signals and values
            macd_histogram = macd.get("histogram")
            macd_signal = macd.get("action")  # BUY/SELL/HOLD
            rsi_value = rsi.get("value")
            rsi_signal = rsi.get("action")  # BUY/SELL/HOLD

            # Final signal and confidence
            final_signal = signal.value  # Convert enum to string
            confidence = result.get("confidence", 0.0)

            # Action taken (this will be updated later if trade is executed)
            # For now, mark as "pending"
            action_taken = "pending" if signal != Signal.HOLD else "hold_signal"

            # Record to database
            self.analysis_history.record_analysis(
                ticker=ticker,
                timeframe=timeframe,
                macd_histogram=macd_histogram,
                macd_signal=macd_signal,
                rsi_value=rsi_value,
                rsi_signal=rsi_signal,
                final_signal=final_signal,
                confidence=confidence,
                action_taken=action_taken,
            )

            logger.debug(
                f"Recorded analysis for {ticker}: {final_signal} "
                f"(MACD: {macd_signal}, RSI: {rsi_signal}, conf: {confidence:.1%})"
            )

        except Exception as e:
            # Don't let recording errors break the trading flow
            logger.error(f"Error recording analysis for {ticker}: {e}", exc_info=True)

    def _create_fallback_result(self, reason: str) -> AnalysisResult:
        """Create fallback result when analysis fails."""
        return AnalysisResult(
            signal=Signal.HOLD,
            confidence=0.0,
            entry_price=None,
            stop_loss=None,
            take_profit=None,
            reasoning=[f"⚠️ {reason}", "Defaulting to HOLD with no position"],
            indicators={"error": reason},
            analyzer_name=f"{self.name} (Fallback)",
        )
