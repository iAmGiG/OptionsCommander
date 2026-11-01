"""
Core trading system architecture.

This package contains the foundational interfaces, models, and orchestrator
for the new plugin-based architecture.

For trading modes, use src/core/trading_modes.py (Issue #400).
"""

from .interfaces import ExecutionManager, InputParser, RiskManager, StrategyAnalyzer

# New plugin architecture
from .models import (
    AnalysisResult,
    AssetType,
    OrderResult,
    OrderType,
    RiskAssessment,
    SessionState,
    Signal,
    TimeInForce,
    TradeDecision,
    TradeRequest,
    TradeSuggestion,
)
from .trading_modes import (
    ModeParameters,
    TradingMode,
    TradingModeManager,
    get_current_mode,
    get_mode_manager,
    get_mode_parameters,
    set_trading_mode,
)
from .trading_orchestrator import TradingOrchestrator

__all__ = [
    # New architecture - Enums
    "Signal",
    "AssetType",
    "OrderType",
    "TimeInForce",
    "TradingMode",
    # New architecture - Models
    "TradeRequest",
    "AnalysisResult",
    "RiskAssessment",
    "TradeSuggestion",
    "TradeDecision",
    "OrderResult",
    "SessionState",
    "ModeParameters",
    # New architecture - Orchestrator
    "TradingOrchestrator",
    # New architecture - Interfaces
    "InputParser",
    "StrategyAnalyzer",
    "RiskManager",
    "ExecutionManager",
    # Trading Modes (Issue #400)
    "TradingModeManager",
    "get_mode_manager",
    "set_trading_mode",
    "get_current_mode",
    "get_mode_parameters",
]
