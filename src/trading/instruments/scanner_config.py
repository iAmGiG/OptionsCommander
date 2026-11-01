"""
Scanner Configuration Module.

Issue #512: Extracted from the scanner agent, since removed (#331); /watchlist scanner reads it.
Contains configuration classes, dataclasses, and YAML loaders.
"""

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml

logger = logging.getLogger(__name__)

# Config paths
CONFIG_DIR = Path(__file__).parent.parent.parent.parent / "config_defaults"
SCANNER_CONFIG_PATH = CONFIG_DIR / "scanner_config.yaml"
TRADING_MODES_PATH = CONFIG_DIR / "trading_modes.yaml"
WATCHLISTS_DIR = CONFIG_DIR / "watchlists"

# Fallback watchlist when config unavailable
FALLBACK_WATCHLIST = ["SPY", "QQQ", "AAPL", "MSFT", "NVDA", "TSLA"]


def load_scanner_config() -> Dict[str, Any]:
    """Load scanner configuration from YAML."""
    try:
        with open(SCANNER_CONFIG_PATH, "r", encoding="utf-8") as f:
            return yaml.safe_load(f) or {}
    except Exception as e:
        logger.warning(f"Could not load scanner config: {e}")
        return {}


def load_trading_modes() -> Dict[str, Any]:
    """Load trading modes configuration from YAML."""
    try:
        with open(TRADING_MODES_PATH, "r", encoding="utf-8") as f:
            return yaml.safe_load(f) or {}
    except Exception as e:
        logger.warning(f"Could not load trading modes: {e}")
        return {}


def load_strategy_watchlist(strategy_name: str) -> List[str]:
    """Load tickers from a strategy watchlist file."""
    try:
        watchlist_path = WATCHLISTS_DIR / f"{strategy_name}.yaml"
        if not watchlist_path.exists():
            logger.warning(f"Strategy watchlist not found: {strategy_name}")
            return []

        with open(watchlist_path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}

        tickers = data.get("tickers", [])
        logger.debug(f"Loaded {len(tickers)} tickers from {strategy_name} watchlist")
        return tickers
    except Exception as e:
        logger.warning(f"Could not load strategy watchlist {strategy_name}: {e}")
        return []


def load_discovery_tickers() -> List[str]:
    """Load discovery tickers from default_watchlist in scanner_config."""
    config = load_scanner_config()
    default_watchlist = config.get("default_watchlist", {})

    tickers = []
    for category_tickers in default_watchlist.values():
        if isinstance(category_tickers, list):
            tickers.extend(category_tickers)

    return tickers if tickers else FALLBACK_WATCHLIST


@dataclass
class TierLimits:
    """Limits for each watchlist tier."""

    positions: int = 10
    pending_orders: int = 5
    strategy: int = 8
    discovery: int = 5


@dataclass
class TieredWatchlistConfig:
    """Configuration for tiered watchlist system (Issue #405)."""

    enabled: bool = True
    max_symbols_per_scan: int = 25
    tier_limits: TierLimits = field(default_factory=TierLimits)
    fallback_to_config: bool = True

    @classmethod
    def from_config(cls) -> "TieredWatchlistConfig":
        """Load tiered config from scanner_config.yaml."""
        config = load_scanner_config()
        tiered = config.get("tiered_watchlist", {})

        limits_data = tiered.get("tier_limits", {})
        tier_limits = TierLimits(
            positions=limits_data.get("positions", 10),
            pending_orders=limits_data.get("pending_orders", 5),
            strategy=limits_data.get("strategy", 8),
            discovery=limits_data.get("discovery", 5),
        )

        return cls(
            enabled=tiered.get("enabled", True),
            max_symbols_per_scan=tiered.get("max_symbols_per_scan", 25),
            tier_limits=tier_limits,
            fallback_to_config=tiered.get("fallback_to_config", True),
        )


@dataclass
class ScanConfig:
    """Configuration for market scanning."""

    watchlist: List[str] = field(default_factory=lambda: FALLBACK_WATCHLIST.copy())
    lookback_days: int = 60  # Days of price data to fetch
    max_concurrent_requests: int = 5  # Rate limiting
    request_delay_seconds: float = 0.2  # Delay between requests
    min_data_points: int = 42  # Minimum points for reliable signals
    macd_params: Dict[str, int] = field(
        default_factory=lambda: {"fast": 13, "slow": 34, "signal": 8}
    )
    rsi_params: Dict[str, int] = field(
        default_factory=lambda: {"period": 14, "oversold": 30, "overbought": 70}
    )
    volume_threshold: float = 1.0  # Minimum volume ratio vs 20-day average
    min_confidence: float = 0.5  # Minimum confidence for inclusion

    # Tiered watchlist config (Issue #405)
    tiered_config: TieredWatchlistConfig = field(default_factory=TieredWatchlistConfig.from_config)


@dataclass
class ScanResult:
    """Result of scanning a single ticker."""

    symbol: str
    action: str  # BUY, SELL, HOLD
    confidence: float
    signal_type: str  # STRONG, WEAK, NEUTRAL, CONFLICT
    current_price: float
    macd_signal: str
    macd_histogram: float
    rsi_value: float
    rsi_signal: str
    volume_ratio: float
    ranking_score: float
    timestamp: str
    error: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for serialization."""
        return {
            "symbol": self.symbol,
            "action": self.action,
            "confidence": self.confidence,
            "signal_type": self.signal_type,
            "current_price": self.current_price,
            "macd_signal": self.macd_signal,
            "macd_histogram": self.macd_histogram,
            "rsi_value": self.rsi_value,
            "rsi_signal": self.rsi_signal,
            "volume_ratio": self.volume_ratio,
            "ranking_score": self.ranking_score,
            "timestamp": self.timestamp,
            "error": self.error,
        }
