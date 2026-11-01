"""
Trading Package - Unified trading domain.

Organized Structure:
- broker/: Alpaca API integration and execution
- orders/: Order lifecycle management
- positions/: Position tracking, sizing, portfolios
- risk/: Risk management and calculations
- state/: State management and reconciliation
- scheduling/: Trading cycles and pipelines
- accounts/: Multi-account management
- instruments/: Ticker and timeframe tools
- utils/: Supporting utilities

This package provides backward-compatible exports from the reorganized structure.
"""

# Accounts
from .accounts.account_manager import AccountManager, get_account_manager
from .accounts.account_tools import (
    get_account_buying_power,
    get_active_account_info,
    get_available_accounts,
    is_account_paper_trading,
    refresh_account_data,
    switch_active_account,
)

# Alerts and Watchlists (#480)
from .alerts_watchlists import (
    Alert,
    AlertsWatchlistsManager,
    AlertType,
    Watchlist,
    WatchlistItem,
    get_alerts_watchlists_manager,
)

# Broker layer
from .broker.alpaca_execution_manager import AlpacaExecutionManager
from .broker.alpaca_trading_client import (
    AlpacaAccountMonitor,
    AlpacaOrderManager,
    AlpacaTradingClient,
    get_trading_client,
)
from .broker.api_error_translator import APIErrorTranslator

# Instruments
from .instruments.approved_tickers import ApprovedTickersManager
from .instruments.ticker_database import TickerDatabase, TickerMode
from .instruments.timeframe_tools import (
    TimeframeManager,
    convert_to_alpaca_timeframe,
    get_current_timeframe,
    get_timeframe_display_name,
    validate_timeframe,
)

# Orders
from .orders.partial_exit_manager import PartialExitManager
from .orders.trailing_stop_manager import TrailingStopManager, get_current_price

# Portfolio Override (#479)
from .portfolio_override import (
    PortfolioOverride,
    PortfolioOverrideManager,
    get_portfolio_override_manager,
)

# Positions
from .positions.portfolio_manager import PortfolioManager
from .positions.position_manager import PositionManager
from .positions.position_sizer import PositionSizer, PositionSizeResult, SizingMode

# Risk
from .risk.risk_calculator import RiskMetrics, calculate_portfolio_risk
from .risk.simple_risk_manager import SimpleRiskManager

# Scheduling
from .scheduling.daily_scheduler import DailyScheduler
from .scheduling.trading_cycle import CostEfficientTradeCycle, RoutineType

# State
from .state.broker_state_cache import BrokerStateCache
from .state.local_state_manager import LocalStateManager
from .state.state_reconciler import StateReconciler

# Utils
from .utils.report_generator import ReportGenerator
from .utils.simple_signals import SimpleSignalGenerator
from .utils.unified_price_fetcher import UnifiedPriceFetcher

__all__ = [
    # Broker
    "AlpacaExecutionManager",
    "AlpacaTradingClient",
    "AlpacaAccountMonitor",
    "AlpacaOrderManager",
    "get_trading_client",
    "APIErrorTranslator",
    # Orders
    "PartialExitManager",
    "TrailingStopManager",
    # Positions
    "PositionManager",
    "PositionSizer",
    "PositionSizeResult",
    "SizingMode",
    "PortfolioManager",
    # Risk
    "SimpleRiskManager",
    "RiskMetrics",
    "calculate_portfolio_risk",
    # State
    "BrokerStateCache",
    "LocalStateManager",
    "StateReconciler",
    # Scheduling
    "DailyScheduler",
    "CostEfficientTradeCycle",
    "RoutineType",
    # Accounts
    "AccountManager",
    "get_account_manager",
    "get_active_account_info",
    "get_available_accounts",
    "get_account_buying_power",
    "is_account_paper_trading",
    "refresh_account_data",
    "switch_active_account",
    # Instruments
    "ApprovedTickersManager",
    "TickerDatabase",
    "TickerMode",
    "TimeframeManager",
    "validate_timeframe",
    "convert_to_alpaca_timeframe",
    "get_current_timeframe",
    "get_timeframe_display_name",
    # Utils
    "UnifiedPriceFetcher",
    "SimpleSignalGenerator",
    "ReportGenerator",
    "get_current_price",
    # Portfolio Override
    "PortfolioOverride",
    "PortfolioOverrideManager",
    "get_portfolio_override_manager",
    # Alerts and Watchlists
    "Alert",
    "AlertType",
    "AlertsWatchlistsManager",
    "Watchlist",
    "WatchlistItem",
    "get_alerts_watchlists_manager",
]
