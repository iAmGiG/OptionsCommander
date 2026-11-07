#!/usr/bin/env python3
"""
Pytest configuration and shared fixtures.

Provides mock objects for Alpaca API, market data, positions, and orders.
Used across all test modules for consistent test data.

Issue #408: Unit Testing and CLI Testing (Pre-Live Trading Validation)
"""

import os
import sys
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import List, Optional
from unittest.mock import MagicMock

import pandas as pd
import pytest

# Add project root to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


# =============================================================================
# Mock Alpaca Data Classes
# =============================================================================


@dataclass
class MockPosition:
    """Mock Alpaca Position object."""

    symbol: str
    qty: str
    avg_cost: str
    market_value: str
    unrealized_pl: str
    unrealized_plpc: str
    cost_basis: str
    side: str = "long"


@dataclass
class MockOrder:
    """Mock Alpaca Order object."""

    id: str
    symbol: str
    qty: str
    side: str
    order_type: str
    status: str
    submitted_at: Optional[datetime] = None
    filled_at: Optional[datetime] = None
    filled_qty: Optional[str] = None
    filled_avg_price: Optional[str] = None
    limit_price: Optional[str] = None
    stop_price: Optional[str] = None
    time_in_force: str = "gtc"
    order_class: str = "simple"
    legs: Optional[List] = None


@dataclass
class MockAccount:
    """Mock Alpaca Account object."""

    buying_power: str = "100000.00"
    cash: str = "50000.00"
    portfolio_value: str = "150000.00"
    equity: str = "150000.00"
    daytrade_buying_power: str = "400000"  # Integer string for int() conversion
    status: str = "ACTIVE"


# =============================================================================
# Test Safety
# =============================================================================


@pytest.fixture(autouse=True)
def no_broker_snapshot_cache(monkeypatch):
    """Keep BrokerStateCache from writing snapshots to the real .cache/trading_data.db."""
    from src.trading.state import broker_state_cache

    monkeypatch.setattr(broker_state_cache, "_unified_cache", False)


@pytest.fixture(autouse=True)
def isolated_cache_dir(tmp_path, monkeypatch):
    """Point the market-data and broker cache at a temp dir (#600).

    The shared cache instances are built on first use; dropping them per test makes
    each test build its own in its temp dir. A module not loaded yet has none to drop.
    """
    from src.cache.lazy import LazyInstance
    from src.utils.state_paths import CACHE_DIR_ENV

    monkeypatch.setenv(CACHE_DIR_ENV, str(tmp_path / "cache"))
    for module_name, name in (
        ("src.cache.cache_adapter", "cache_adapter"),
        ("src.cache.unified_broker_cache", "unified_broker_cache"),
    ):
        shared = getattr(sys.modules.get(module_name), name, None)
        if isinstance(shared, LazyInstance):  # not a test's stub
            monkeypatch.setattr(shared, "_instance", None)


# =============================================================================
# Broker Client Fixtures
# =============================================================================


@pytest.fixture
def mock_broker_client():
    """
    Create a mock broker client with configurable responses.

    Returns a MagicMock that simulates Alpaca TradingClient behavior.
    """
    client = MagicMock()

    # Default account
    client.get_account.return_value = MockAccount()

    # Default empty positions
    client.get_all_positions.return_value = []

    # Default empty orders
    client.get_orders.return_value = []

    return client


@pytest.fixture
def mock_broker_with_positions(mock_broker_client):
    """
    Broker client with sample positions.

    Includes SPY (profitable), AAPL (at loss), and MSFT (breakeven).
    """
    positions = [
        MockPosition(
            symbol="SPY",
            qty="10",
            avg_cost="450.00",
            market_value="4800.00",
            unrealized_pl="300.00",
            unrealized_plpc="0.0667",
            cost_basis="4500.00",
        ),
        MockPosition(
            symbol="AAPL",
            qty="20",
            avg_cost="180.00",
            market_value="3400.00",
            unrealized_pl="-200.00",
            unrealized_plpc="-0.0556",
            cost_basis="3600.00",
        ),
        MockPosition(
            symbol="MSFT",
            qty="5",
            avg_cost="400.00",
            market_value="2000.00",
            unrealized_pl="0.00",
            unrealized_plpc="0.0000",
            cost_basis="2000.00",
        ),
    ]
    mock_broker_client.get_all_positions.return_value = positions
    return mock_broker_client


@pytest.fixture
def mock_broker_with_orders(mock_broker_client):
    """
    Broker client with sample open orders.

    Includes various order types: market, limit, bracket.
    """
    now = datetime.now()
    orders = [
        MockOrder(
            id="order-001",
            symbol="SPY",
            qty="5",
            side="buy",
            order_type="limit",
            status="new",
            submitted_at=now - timedelta(minutes=30),
            limit_price="595.00",
            time_in_force="gtc",
        ),
        MockOrder(
            id="order-002",
            symbol="AAPL",
            qty="10",
            side="sell",
            order_type="stop",
            status="held",
            submitted_at=now - timedelta(hours=2),
            stop_price="165.00",
            time_in_force="gtc",
        ),
        MockOrder(
            id="order-003",
            symbol="MSFT",
            qty="8",
            side="buy",
            order_type="market",
            status="filled",
            submitted_at=now - timedelta(days=1),
            filled_at=now - timedelta(days=1) + timedelta(seconds=5),
            filled_qty="8",
            filled_avg_price="398.50",
        ),
    ]
    mock_broker_client.get_orders.return_value = orders
    return mock_broker_client


@pytest.fixture
def mock_broker_api_error(mock_broker_client):
    """Broker client that raises API errors."""
    mock_broker_client.get_all_positions.side_effect = Exception("API connection failed")
    mock_broker_client.get_orders.side_effect = Exception("API connection failed")
    mock_broker_client.get_account.side_effect = Exception("API connection failed")
    return mock_broker_client


# =============================================================================
# Market Data Fixtures
# =============================================================================


@pytest.fixture
def sample_ohlcv_data():
    """
    Generate sample OHLCV data for testing.

    Returns a DataFrame with 100 days of realistic price data.
    """
    dates = pd.date_range(end=datetime.now(), periods=100, freq="D")

    # Generate somewhat realistic price movement
    import numpy as np

    np.random.seed(42)  # Reproducible

    base_price = 450.0
    returns = np.random.normal(0.0005, 0.015, 100)  # Small daily returns
    prices = base_price * np.cumprod(1 + returns)

    # Add some volatility
    highs = prices * (1 + np.abs(np.random.normal(0, 0.01, 100)))
    lows = prices * (1 - np.abs(np.random.normal(0, 0.01, 100)))
    opens = prices * (1 + np.random.normal(0, 0.005, 100))

    df = pd.DataFrame(
        {
            "Open": opens,
            "High": highs,
            "Low": lows,
            "Close": prices,
            "Volume": np.random.randint(1000000, 10000000, 100),
        },
        index=dates,
    )

    return df


@pytest.fixture
def sample_ohlcv_short():
    """Short OHLCV data (20 days) - insufficient for some indicators."""
    dates = pd.date_range(end=datetime.now(), periods=20, freq="D")

    import numpy as np

    np.random.seed(42)

    base_price = 200.0
    prices = base_price + np.cumsum(np.random.randn(20) * 2)

    df = pd.DataFrame(
        {
            "Open": prices * 0.998,
            "High": prices * 1.01,
            "Low": prices * 0.99,
            "Close": prices,
            "Volume": np.random.randint(500000, 5000000, 20),
        },
        index=dates,
    )

    return df


@pytest.fixture
def sample_ohlcv_bullish():
    """OHLCV data with clear bullish trend for testing BUY signals."""
    dates = pd.date_range(end=datetime.now(), periods=60, freq="D")

    import numpy as np

    np.random.seed(123)

    # Strongly upward trend
    base_price = 100.0
    trend = np.linspace(0, 50, 60)  # +50% over period
    noise = np.random.randn(60) * 2
    prices = base_price + trend + noise

    df = pd.DataFrame(
        {
            "Open": prices * 0.998,
            "High": prices * 1.015,
            "Low": prices * 0.985,
            "Close": prices,
            "Volume": np.random.randint(1000000, 8000000, 60),
        },
        index=dates,
    )

    return df


@pytest.fixture
def sample_ohlcv_bearish():
    """OHLCV data with clear bearish trend for testing SELL signals."""
    dates = pd.date_range(end=datetime.now(), periods=60, freq="D")

    import numpy as np

    np.random.seed(456)

    # Strongly downward trend
    base_price = 150.0
    trend = np.linspace(0, -40, 60)  # -27% over period
    noise = np.random.randn(60) * 2
    prices = base_price + trend + noise

    df = pd.DataFrame(
        {
            "Open": prices * 1.002,
            "High": prices * 1.015,
            "Low": prices * 0.985,
            "Close": prices,
            "Volume": np.random.randint(1000000, 8000000, 60),
        },
        index=dates,
    )

    return df


@pytest.fixture
def sample_ohlcv_sideways():
    """OHLCV data with sideways/ranging market for testing HOLD signals."""
    dates = pd.date_range(end=datetime.now(), periods=60, freq="D")

    import numpy as np

    np.random.seed(789)

    # Sideways with oscillation
    base_price = 100.0
    oscillation = 5 * np.sin(np.linspace(0, 4 * np.pi, 60))
    noise = np.random.randn(60) * 1.5
    prices = base_price + oscillation + noise

    df = pd.DataFrame(
        {
            "Open": prices * 0.999,
            "High": prices * 1.01,
            "Low": prices * 0.99,
            "Close": prices,
            "Volume": np.random.randint(500000, 3000000, 60),
        },
        index=dates,
    )

    return df


# =============================================================================
# Trading Signal Fixtures
# =============================================================================


@pytest.fixture
def buy_signal_result():
    """Expected result structure for a BUY signal."""
    return {
        "signal": "BUY",
        "confidence": 0.75,
        "entry_price": 450.00,
        "stop_loss": 441.00,  # 2% below entry
        "take_profit": 472.50,  # 5% above entry
        "reasoning": ["MACD: BUY (bullish crossover)", "RSI: NEUTRAL (52)"],
    }


@pytest.fixture
def sell_signal_result():
    """Expected result structure for a SELL signal."""
    return {
        "signal": "SELL",
        "confidence": 0.70,
        "entry_price": 450.00,
        "stop_loss": 459.00,  # 2% above entry
        "take_profit": 427.50,  # 5% below entry
        "reasoning": ["MACD: SELL (bearish crossover)", "RSI: OVERBOUGHT (78)"],
    }


@pytest.fixture
def hold_signal_result():
    """Expected result structure for a HOLD signal."""
    return {
        "signal": "HOLD",
        "confidence": 0.50,
        "entry_price": None,
        "stop_loss": None,
        "take_profit": None,
        "reasoning": ["MACD: NEUTRAL", "RSI: NEUTRAL (55)"],
    }


# =============================================================================
# Configuration Fixtures
# =============================================================================


@pytest.fixture
def voter_config():
    """Standard VoterAgent configuration."""
    return {
        "macd_params": {"fast": 13, "slow": 34, "signal": 8},
        "rsi_params": {"period": 14, "oversold": 30, "overbought": 70},
    }


@pytest.fixture
def conservative_mode_config():
    """Conservative trading mode configuration."""
    return {
        "mode": "conservative",
        "position_size_pct": 0.02,
        "max_loss_pct": 0.01,
        "stop_loss_pct": 0.02,
        "take_profit_pct": 0.04,
    }


@pytest.fixture
def aggressive_mode_config():
    """Aggressive trading mode configuration."""
    return {
        "mode": "aggressive",
        "position_size_pct": 0.08,
        "max_loss_pct": 0.04,
        "stop_loss_pct": 0.05,
        "take_profit_pct": 0.10,
    }


# =============================================================================
# Utility Fixtures
# =============================================================================


@pytest.fixture
def temp_state_dir(tmp_path):
    """Create temporary state directory for tests."""
    state_dir = tmp_path / "state"
    state_dir.mkdir()
    return state_dir


@pytest.fixture
def mock_datetime_now(monkeypatch):
    """
    Fixture to mock datetime.now() for deterministic tests.

    Usage:
        def test_something(mock_datetime_now):
            mock_datetime_now(datetime(2025, 1, 15, 10, 30))
    """

    def _mock_datetime(target_datetime):
        class MockDatetime:
            @classmethod
            def now(cls, tz=None):
                return target_datetime

        monkeypatch.setattr("datetime.datetime", MockDatetime)
        return target_datetime

    return _mock_datetime


# =============================================================================
# GEX Database Mock Fixtures (no PostgreSQL required)
# =============================================================================


@pytest.fixture
def mock_options_chain():
    """Realistic 10-contract SPY options chain DataFrame."""
    import numpy as np

    np.random.seed(42)
    strikes = np.arange(590, 610, 2.0)
    n = len(strikes)
    return pd.DataFrame(
        {
            "symbol": "SPY",
            "strike": strikes,
            "expiration": "2026-03-20",
            "type": ["call"] * 5 + ["put"] * 5,
            "last_price": np.random.uniform(1.0, 20.0, n),
            "bid": np.random.uniform(0.5, 19.5, n),
            "ask": np.random.uniform(1.5, 20.5, n),
            "volume": np.random.randint(100, 10000, n),
            "open_interest": np.random.randint(1000, 50000, n),
            "implied_volatility": np.random.uniform(0.12, 0.35, n),
            "delta": np.concatenate(
                [np.random.uniform(0.3, 0.8, 5), np.random.uniform(-0.8, -0.3, 5)]
            ),
            "gamma": np.random.uniform(0.005, 0.03, n),
            "theta": np.random.uniform(-0.5, -0.01, n),
            "vega": np.random.uniform(0.05, 0.30, n),
            "days_to_expiration": [33] * n,
        }
    )


@pytest.fixture
def mock_options_manager(mock_options_chain):
    """MagicMock PostgreSQLOptionsManager with realistic responses."""
    mgr = MagicMock()
    mgr.retrieve_options_chain.return_value = mock_options_chain
    mgr.has_options_data.return_value = True
    mgr.get_database_stats.return_value = {
        "total_options_records": 123_800_000,
        "total_symbols": 62,
        "date_range": ("2020-01-02", "2025-12-31"),
    }
    return mgr


@pytest.fixture
def mock_gex_snapshots():
    """5-snapshot GEX time series for a single symbol."""
    timestamps = pd.date_range("2026-02-14 09:30", periods=5, freq="30min")
    return pd.DataFrame(
        {
            "symbol": "SPY",
            "snapshot_timestamp": timestamps,
            "regime": ["persistent_positive"] * 3 + ["transitional"] * 2,
            "total_gex": [5e9, 4.8e9, 5.2e9, 2.1e9, -0.5e9],
            "zero_gamma_level": [598.5, 598.2, 598.8, 599.1, 599.5],
            "call_gex": [8e9, 7.5e9, 8.5e9, 5e9, 3e9],
            "put_gex": [-3e9, -2.7e9, -3.3e9, -2.9e9, -3.5e9],
        }
    )


@pytest.fixture
def mock_intraday_gex_manager(mock_gex_snapshots):
    """MagicMock IntradayGexManager with realistic responses."""
    mgr = MagicMock()
    mgr.retrieve_gex_snapshots.return_value = mock_gex_snapshots
    mgr.get_latest_gex.return_value = {
        "symbol": "SPY",
        "regime": "persistent_positive",
        "total_gex": 5.2e9,
        "zero_gamma_level": 598.8,
    }
    mgr.get_regime_changes.return_value = [
        {"from": "transitional", "to": "persistent_positive", "date": "2026-02-10"},
        {"from": "persistent_positive", "to": "transitional", "date": "2026-02-14"},
    ]
    mgr.get_snapshot_count.return_value = 21
    return mgr


# =============================================================================
# Notifications (#342): no real mail or ping from any test
# =============================================================================


class RealNetworkUsed(BaseException):
    """A test reached for the real network; a BaseException, so no handler hides it."""


def _refuse(*args, **kwargs):
    raise RealNetworkUsed("a test tried to use the network (SMTP or the heartbeat)")


@pytest.fixture(autouse=True)
def no_notifications(monkeypatch):
    """No real SMTP or heartbeat request, and the notifier sees no settings by default."""
    import smtplib

    from src.trading.scheduling import notifier

    monkeypatch.setattr(smtplib, "SMTP", _refuse)
    monkeypatch.setattr(smtplib, "SMTP_SSL", _refuse)
    monkeypatch.setattr(notifier, "_get", _refuse)
    monkeypatch.setattr(notifier, "_setting", lambda key: None)
