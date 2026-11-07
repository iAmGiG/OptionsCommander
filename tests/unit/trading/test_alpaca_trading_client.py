#!/usr/bin/env python3
"""
Unit tests for AlpacaTradingClient, AlpacaAccountMonitor, and AlpacaOrderManager.

Tests the unified Alpaca trading interface with safety rails:
- Singleton pattern for client instances
- Paper vs live mode switching
- Read-only account monitoring
- Order placement with validation
- Risk limit checking

Issue #408: Unit Testing and CLI Testing (Pre-Live Trading Validation)
Priority 1 Component - AlpacaTradingClient (Critical for live trading)
"""

import os
import sys
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../../.."))


@pytest.fixture(autouse=True)
def reset_client_singletons(monkeypatch):
    """Give each test an empty AlpacaTradingClient singleton map, restored afterwards."""
    from src.trading.broker.alpaca_trading_client import AlpacaTradingClient

    monkeypatch.setattr(AlpacaTradingClient, "_instances", {})


# alpaca-py is a project dependency, so the real SDK is imported. Tests patch the names
# used inside src.trading.broker.alpaca_trading_client instead of stubbing sys.modules,
# which leaked MagicMock packages into other tests.


# =============================================================================
# Mock Fixtures
# =============================================================================


@pytest.fixture
def mock_config():
    """Mock configuration loader."""
    config_mock = MagicMock()
    config_mock.get.side_effect = lambda key: {
        "ALPACA_PAPER_API_KEY": "test_paper_key",
        "ALPACA_PAPER_SECRET": "test_paper_secret",
        "ALPACA_LIVE_API_KEY": "test_live_key",
        "ALPACA_LIVE_SECRET": "test_live_secret",
    }.get(key)
    return config_mock


@pytest.fixture
def mock_trading_client():
    """Mock Alpaca TradingClient."""
    client = MagicMock()

    # Mock account
    account = MagicMock()
    account.buying_power = "100000.00"
    account.cash = "100000.00"
    account.portfolio_value = "100000.00"
    account.pattern_day_trader = False
    account.trading_blocked = False
    account.account_blocked = False
    account.account_number = "TEST123456"
    account.status = "ACTIVE"
    account.equity = "100000.00"
    account.last_equity = "99000.00"
    client.get_account.return_value = account

    # Mock positions
    position = MagicMock()
    position.symbol = "SPY"
    position.qty = "10"
    position.side = "long"
    position.avg_entry_price = "450.00"
    position.market_value = "4600.00"
    position.unrealized_pl = "100.00"
    position.unrealized_plpc = "0.0222"
    position.cost_basis = "4500.00"
    position.change_today = "0.01"
    client.get_all_positions.return_value = [position]

    # Mock orders
    order = MagicMock()
    order.id = "order-123"
    order.symbol = "SPY"
    order.side = MagicMock(value="buy")
    order.qty = "10"
    order.filled_qty = "0"
    order.status = MagicMock(value="new")
    order.order_type = MagicMock(value="market")
    order.time_in_force = MagicMock(value="day")
    order.limit_price = None
    order.stop_price = None
    order.submitted_at = datetime.now(timezone.utc)
    order.filled_at = None
    order.canceled_at = None
    order.filled_avg_price = None
    order.order_class = None
    order.legs = None
    client.get_orders.return_value = [order]
    client.get_order_by_id.return_value = order

    # Mock order submission
    submitted_order = MagicMock()
    submitted_order.id = "order-456"
    submitted_order.symbol = "SPY"
    submitted_order.qty = "10"
    submitted_order.side = MagicMock(value="buy")
    submitted_order.order_type = MagicMock(value="market")
    submitted_order.status = MagicMock(value="accepted")
    submitted_order.submitted_at = datetime.now(timezone.utc)
    submitted_order.limit_price = None
    client.submit_order.return_value = submitted_order

    return client


@pytest.fixture
def mock_account_data():
    """Mock account data dictionary."""
    return {
        "mode": "paper",
        "buying_power": 100000.0,
        "cash": 100000.0,
        "portfolio_value": 100000.0,
        "pattern_day_trader": False,
        "trading_blocked": False,
        "account_blocked": False,
        "account_number": "TEST123456",
        "status": "ACTIVE",
        "equity": 100000.0,
        "last_equity": 99000.0,
    }


@pytest.fixture
def mock_positions_data():
    """Mock positions data list."""
    return [
        {
            "symbol": "SPY",
            "qty": 10.0,
            "side": "long",
            "avg_entry_price": 450.0,
            "market_value": 4600.0,
            "unrealized_pl": 100.0,
            "unrealized_plpc": 0.0222,
            "cost_basis": 4500.0,
            "change_today": 0.01,
        }
    ]


# =============================================================================
# AlpacaTradingClient Tests
# =============================================================================


def _uuid(name: str) -> str:
    import uuid

    return str(uuid.uuid5(uuid.NAMESPACE_URL, name))


def _api_order(name, order_type, status, order_class, side="buy", legs=None, **prices):
    """A real alpaca.trading.models.Order, shaped like a nested=True API response."""
    from alpaca.trading.models import Order

    now = "2026-10-13T14:30:00Z"
    return Order.model_validate(
        {
            "id": _uuid(name),
            "client_order_id": name,
            "created_at": now,
            "updated_at": now,
            "submitted_at": now,
            "filled_at": now if status == "filled" else None,
            "symbol": "SPY",
            "asset_class": "us_equity",
            "qty": "10",
            "filled_qty": "10" if status == "filled" else "0",
            "order_class": order_class,
            "order_type": order_type,
            "type": order_type,
            "side": side,
            "time_in_force": "gtc",
            "status": status,
            "extended_hours": False,
            "legs": legs,
            **prices,
        }
    )


def _monitor_get_orders(mock_config, mock_trading_client, status):
    with patch("src.trading.broker.alpaca_trading_client.ConfigLoader", return_value=mock_config):
        with patch(
            "src.trading.broker.alpaca_trading_client.TradingClient",
            return_value=mock_trading_client,
        ):
            with patch("src.trading.broker.alpaca_trading_client.ALPACA_TRADING_AVAILABLE", True):
                from src.trading.broker.alpaca_trading_client import (
                    AlpacaAccountMonitor,
                    AlpacaTradingClient,
                )

                AlpacaTradingClient._instances = {}
                return AlpacaAccountMonitor(mode="paper").get_orders(status=status)


class TestAlpacaTradingClientInit:
    """Test AlpacaTradingClient initialization."""

    def test_invalid_mode_raises_error(self, mock_config):
        """Test that invalid mode raises ValueError."""
        with patch(
            "src.trading.broker.alpaca_trading_client.ConfigLoader", return_value=mock_config
        ):
            with patch("src.trading.broker.alpaca_trading_client.ALPACA_TRADING_AVAILABLE", True):
                # Clear singleton instances
                from src.trading.broker.alpaca_trading_client import AlpacaTradingClient

                AlpacaTradingClient._instances = {}

                with pytest.raises(ValueError, match="Mode must be explicitly"):
                    AlpacaTradingClient(mode="invalid")

    def test_missing_credentials_raises_error(self):
        """Test that missing credentials raises ValueError."""
        empty_config = MagicMock()
        empty_config.get.return_value = None

        with patch(
            "src.trading.broker.alpaca_trading_client.ConfigLoader", return_value=empty_config
        ):
            with patch("src.trading.broker.alpaca_trading_client.ALPACA_TRADING_AVAILABLE", True):
                from src.trading.broker.alpaca_trading_client import AlpacaTradingClient

                AlpacaTradingClient._instances = {}

                with pytest.raises(ValueError, match="credentials required"):
                    AlpacaTradingClient(mode="paper")


class TestAlpacaTradingClientSingleton:
    """Test AlpacaTradingClient singleton pattern."""

    def test_singleton_returns_same_instance(self, mock_config, mock_trading_client):
        """Test that singleton returns same instance for same mode."""
        with patch(
            "src.trading.broker.alpaca_trading_client.ConfigLoader", return_value=mock_config
        ):
            with patch(
                "src.trading.broker.alpaca_trading_client.TradingClient",
                return_value=mock_trading_client,
            ):
                with patch(
                    "src.trading.broker.alpaca_trading_client.ALPACA_TRADING_AVAILABLE", True
                ):
                    from src.trading.broker.alpaca_trading_client import AlpacaTradingClient

                    AlpacaTradingClient._instances = {}

                    client1 = AlpacaTradingClient(mode="paper")
                    client2 = AlpacaTradingClient(mode="paper")

                    assert client1 is client2


class TestAlpacaTradingClientSafetyCheck:
    """Test AlpacaTradingClient safety check mechanism."""

    def test_safety_check_skipped_when_not_required(self, mock_config, mock_trading_client):
        """Test safety check is skipped when require_confirmation is False."""
        with patch(
            "src.trading.broker.alpaca_trading_client.ConfigLoader", return_value=mock_config
        ):
            with patch(
                "src.trading.broker.alpaca_trading_client.TradingClient",
                return_value=mock_trading_client,
            ):
                with patch(
                    "src.trading.broker.alpaca_trading_client.ALPACA_TRADING_AVAILABLE", True
                ):
                    from src.trading.broker.alpaca_trading_client import AlpacaTradingClient

                    AlpacaTradingClient._instances = {}

                    client = AlpacaTradingClient(mode="paper", require_confirmation=False)
                    result = client._safety_check("test action", {"test": "data"})
                    assert result is True


# =============================================================================
# AlpacaAccountMonitor Tests
# =============================================================================


class TestAlpacaAccountMonitorInit:
    """Test AlpacaAccountMonitor initialization."""

    def test_init_paper_mode(self, mock_config, mock_trading_client):
        """Test initialization in paper mode."""
        with patch(
            "src.trading.broker.alpaca_trading_client.ConfigLoader", return_value=mock_config
        ):
            with patch(
                "src.trading.broker.alpaca_trading_client.TradingClient",
                return_value=mock_trading_client,
            ):
                with patch(
                    "src.trading.broker.alpaca_trading_client.ALPACA_TRADING_AVAILABLE", True
                ):
                    from src.trading.broker.alpaca_trading_client import (
                        AlpacaAccountMonitor,
                        AlpacaTradingClient,
                    )

                    AlpacaTradingClient._instances = {}

                    monitor = AlpacaAccountMonitor(mode="paper")
                    assert monitor.client.mode == "paper"


class TestAlpacaAccountMonitorGetAccount:
    """Test AlpacaAccountMonitor.get_account_status()."""

    def test_get_account_status_returns_dict(self, mock_config, mock_trading_client):
        """Test get_account_status returns proper dict structure."""
        with patch(
            "src.trading.broker.alpaca_trading_client.ConfigLoader", return_value=mock_config
        ):
            with patch(
                "src.trading.broker.alpaca_trading_client.TradingClient",
                return_value=mock_trading_client,
            ):
                with patch(
                    "src.trading.broker.alpaca_trading_client.ALPACA_TRADING_AVAILABLE", True
                ):
                    from src.trading.broker.alpaca_trading_client import (
                        AlpacaAccountMonitor,
                        AlpacaTradingClient,
                    )

                    AlpacaTradingClient._instances = {}

                    monitor = AlpacaAccountMonitor(mode="paper")
                    status = monitor.get_account_status()

                    assert "mode" in status
                    assert "buying_power" in status
                    assert "cash" in status
                    assert "portfolio_value" in status
                    assert status["mode"] == "paper"

    def test_get_account_status_api_error(self, mock_config, mock_trading_client):
        """Test get_account_status handles API errors."""
        mock_trading_client.get_account.side_effect = Exception("API Error")

        with patch(
            "src.trading.broker.alpaca_trading_client.ConfigLoader", return_value=mock_config
        ):
            with patch(
                "src.trading.broker.alpaca_trading_client.TradingClient",
                return_value=mock_trading_client,
            ):
                with patch(
                    "src.trading.broker.alpaca_trading_client.ALPACA_TRADING_AVAILABLE", True
                ):
                    from src.trading.broker.alpaca_trading_client import (
                        AlpacaAccountMonitor,
                        AlpacaTradingClient,
                    )

                    AlpacaTradingClient._instances = {}

                    monitor = AlpacaAccountMonitor(mode="paper")

                    with pytest.raises(Exception, match="API Error"):
                        monitor.get_account_status()


class TestAlpacaAccountMonitorGetPositions:
    """Test AlpacaAccountMonitor.get_positions()."""

    def test_get_positions_returns_list(self, mock_config, mock_trading_client):
        """Test get_positions returns list of positions."""
        with patch(
            "src.trading.broker.alpaca_trading_client.ConfigLoader", return_value=mock_config
        ):
            with patch(
                "src.trading.broker.alpaca_trading_client.TradingClient",
                return_value=mock_trading_client,
            ):
                with patch(
                    "src.trading.broker.alpaca_trading_client.ALPACA_TRADING_AVAILABLE", True
                ):
                    from src.trading.broker.alpaca_trading_client import (
                        AlpacaAccountMonitor,
                        AlpacaTradingClient,
                    )

                    AlpacaTradingClient._instances = {}

                    monitor = AlpacaAccountMonitor(mode="paper")
                    positions = monitor.get_positions()

                    assert isinstance(positions, list)
                    assert len(positions) == 1
                    assert positions[0]["symbol"] == "SPY"

    def test_get_positions_empty(self, mock_config, mock_trading_client):
        """Test get_positions with no positions."""
        mock_trading_client.get_all_positions.return_value = []

        with patch(
            "src.trading.broker.alpaca_trading_client.ConfigLoader", return_value=mock_config
        ):
            with patch(
                "src.trading.broker.alpaca_trading_client.TradingClient",
                return_value=mock_trading_client,
            ):
                with patch(
                    "src.trading.broker.alpaca_trading_client.ALPACA_TRADING_AVAILABLE", True
                ):
                    from src.trading.broker.alpaca_trading_client import (
                        AlpacaAccountMonitor,
                        AlpacaTradingClient,
                    )

                    AlpacaTradingClient._instances = {}

                    monitor = AlpacaAccountMonitor(mode="paper")
                    positions = monitor.get_positions()

                    assert positions == []


class TestAlpacaAccountMonitorGetOrders:
    """Test AlpacaAccountMonitor.get_orders()."""

    def test_get_orders_returns_list(self, mock_config, mock_trading_client):
        """Test get_orders returns list of orders."""
        with patch(
            "src.trading.broker.alpaca_trading_client.ConfigLoader", return_value=mock_config
        ):
            with patch(
                "src.trading.broker.alpaca_trading_client.TradingClient",
                return_value=mock_trading_client,
            ):
                with patch(
                    "src.trading.broker.alpaca_trading_client.ALPACA_TRADING_AVAILABLE", True
                ):
                    with patch(
                        "src.trading.broker.alpaca_trading_client.QueryOrderStatus"
                    ) as mock_query:
                        mock_query.OPEN = "open"
                        mock_query.CLOSED = "closed"

                        from src.trading.broker.alpaca_trading_client import (
                            AlpacaAccountMonitor,
                            AlpacaTradingClient,
                        )

                        AlpacaTradingClient._instances = {}

                        monitor = AlpacaAccountMonitor(mode="paper")
                        orders = monitor.get_orders(status="open")

                        assert isinstance(orders, list)
                        assert len(orders) == 1
                        assert orders[0]["symbol"] == "SPY"

    def test_get_orders_two_bracket_orders(self, mock_config, mock_trading_client):
        """Nested leg Orders of every bracket are attached to their parent.

        Real alpaca-py responses with nested=True carry legs as full Order objects under
        the parent (never top-level). get_orders used to treat them as IDs: first it raised
        AttributeError on a second bracket, then it attached no legs at all.
        """
        orders = [
            _api_order(
                "p1",
                "market",
                "filled",
                "bracket",
                legs=[
                    _api_order("p1-tp", "limit", "new", "simple", side="sell", limit_price="110"),
                    _api_order("p1-sl", "stop", "held", "simple", side="sell", stop_price="95"),
                ],
            ),
            _api_order(
                "p2",
                "market",
                "filled",
                "bracket",
                legs=[
                    _api_order("p2-tp", "limit", "new", "simple", side="sell", limit_price="210"),
                    _api_order("p2-sl", "stop", "held", "simple", side="sell", stop_price="190"),
                ],
            ),
        ]
        mock_trading_client.get_orders.return_value = orders
        result = _monitor_get_orders(mock_config, mock_trading_client, status="all")

        by_id = {o["id"]: o for o in result}
        assert set(by_id) == {_uuid("p1"), _uuid("p2")}
        legs = {leg["id"]: leg for leg in by_id[_uuid("p1")]["legs"]}
        assert set(legs) == {_uuid("p1-tp"), _uuid("p1-sl")}
        assert legs[_uuid("p1-sl")]["order_type"] == "stop"
        assert legs[_uuid("p1-sl")]["stop_price"] == 95.0
        assert legs[_uuid("p1-sl")]["status"] == "held"
        assert legs[_uuid("p1-tp")]["limit_price"] == 110.0
        assert len(by_id[_uuid("p2")]["legs"]) == 2
        assert by_id[_uuid("p1")]["order_class"] == "bracket"
        mock_trading_client.get_order_by_id.assert_not_called()

    def test_get_orders_all_requests_all_statuses(self, mock_config, mock_trading_client):
        """status='all' must send QueryOrderStatus.ALL (no status means Alpaca's 'open')."""
        from alpaca.trading.enums import QueryOrderStatus

        mock_trading_client.get_orders.return_value = []
        _monitor_get_orders(mock_config, mock_trading_client, status="all")
        request = mock_trading_client.get_orders.call_args.kwargs["filter"]
        assert request.status == QueryOrderStatus.ALL
        assert request.nested is True

    def test_get_orders_all_status(self, mock_config, mock_trading_client):
        """Test get_orders with 'all' status."""
        with patch(
            "src.trading.broker.alpaca_trading_client.ConfigLoader", return_value=mock_config
        ):
            with patch(
                "src.trading.broker.alpaca_trading_client.TradingClient",
                return_value=mock_trading_client,
            ):
                with patch(
                    "src.trading.broker.alpaca_trading_client.ALPACA_TRADING_AVAILABLE", True
                ):
                    from src.trading.broker.alpaca_trading_client import (
                        AlpacaAccountMonitor,
                        AlpacaTradingClient,
                    )

                    AlpacaTradingClient._instances = {}

                    monitor = AlpacaAccountMonitor(mode="paper")
                    orders = monitor.get_orders(status="all")

                    assert isinstance(orders, list)


class TestAlpacaAccountMonitorGetPortfolioSummary:
    """Test AlpacaAccountMonitor.get_portfolio_summary()."""

    def test_get_portfolio_summary(self, mock_config, mock_trading_client):
        """Test get_portfolio_summary returns comprehensive overview."""
        with patch(
            "src.trading.broker.alpaca_trading_client.ConfigLoader", return_value=mock_config
        ):
            with patch(
                "src.trading.broker.alpaca_trading_client.TradingClient",
                return_value=mock_trading_client,
            ):
                with patch(
                    "src.trading.broker.alpaca_trading_client.ALPACA_TRADING_AVAILABLE", True
                ):
                    with patch(
                        "src.trading.broker.alpaca_trading_client.QueryOrderStatus"
                    ) as mock_query:
                        mock_query.OPEN = "open"

                        from src.trading.broker.alpaca_trading_client import (
                            AlpacaAccountMonitor,
                            AlpacaTradingClient,
                        )

                        AlpacaTradingClient._instances = {}

                        monitor = AlpacaAccountMonitor(mode="paper")
                        summary = monitor.get_portfolio_summary()

                        assert "account" in summary
                        assert "portfolio_metrics" in summary
                        assert "positions" in summary
                        assert "open_orders" in summary


# =============================================================================
# AlpacaOrderManager Tests
# =============================================================================


class TestAlpacaOrderManagerInit:
    """Test AlpacaOrderManager initialization."""

    def test_init_with_default_risk_limits(self, mock_config, mock_trading_client):
        """Test initialization with default risk limits."""
        with patch(
            "src.trading.broker.alpaca_trading_client.ConfigLoader", return_value=mock_config
        ):
            with patch(
                "src.trading.broker.alpaca_trading_client.TradingClient",
                return_value=mock_trading_client,
            ):
                with patch(
                    "src.trading.broker.alpaca_trading_client.ALPACA_TRADING_AVAILABLE", True
                ):
                    from src.trading.broker.alpaca_trading_client import (
                        AlpacaOrderManager,
                        AlpacaTradingClient,
                    )

                    AlpacaTradingClient._instances = {}

                    manager = AlpacaOrderManager(mode="paper")

                    assert manager.risk_limits["max_position_percent"] == 0.10
                    assert manager.risk_limits["max_daily_trades"] == 50
                    assert manager.risk_limits["max_order_size"] == 1000

    def test_init_with_custom_risk_limits(self, mock_config, mock_trading_client):
        """Test initialization with custom risk limits."""
        custom_limits = {
            "max_position_percent": 0.05,
            "max_daily_trades": 25,
            "max_order_size": 500,
        }

        with patch(
            "src.trading.broker.alpaca_trading_client.ConfigLoader", return_value=mock_config
        ):
            with patch(
                "src.trading.broker.alpaca_trading_client.TradingClient",
                return_value=mock_trading_client,
            ):
                with patch(
                    "src.trading.broker.alpaca_trading_client.ALPACA_TRADING_AVAILABLE", True
                ):
                    from src.trading.broker.alpaca_trading_client import (
                        AlpacaOrderManager,
                        AlpacaTradingClient,
                    )

                    AlpacaTradingClient._instances = {}

                    manager = AlpacaOrderManager(mode="paper", risk_limits=custom_limits)

                    assert manager.risk_limits["max_position_percent"] == 0.05
                    assert manager.risk_limits["max_daily_trades"] == 25
                    assert manager.risk_limits["max_order_size"] == 500


class TestAlpacaOrderManagerValidation:
    """Test AlpacaOrderManager order validation."""

    def test_validate_order_empty_symbol(self, mock_config, mock_trading_client):
        """Test validation fails for empty symbol."""
        with patch(
            "src.trading.broker.alpaca_trading_client.ConfigLoader", return_value=mock_config
        ):
            with patch(
                "src.trading.broker.alpaca_trading_client.TradingClient",
                return_value=mock_trading_client,
            ):
                with patch(
                    "src.trading.broker.alpaca_trading_client.ALPACA_TRADING_AVAILABLE", True
                ):
                    from src.trading.broker.alpaca_trading_client import (
                        AlpacaOrderManager,
                        AlpacaTradingClient,
                    )

                    AlpacaTradingClient._instances = {}

                    manager = AlpacaOrderManager(mode="paper")

                    with pytest.raises(ValueError, match="Symbol is required"):
                        manager._validate_order("", 10, "buy")

    def test_validate_order_negative_qty(self, mock_config, mock_trading_client):
        """Test validation fails for negative quantity."""
        with patch(
            "src.trading.broker.alpaca_trading_client.ConfigLoader", return_value=mock_config
        ):
            with patch(
                "src.trading.broker.alpaca_trading_client.TradingClient",
                return_value=mock_trading_client,
            ):
                with patch(
                    "src.trading.broker.alpaca_trading_client.ALPACA_TRADING_AVAILABLE", True
                ):
                    from src.trading.broker.alpaca_trading_client import (
                        AlpacaOrderManager,
                        AlpacaTradingClient,
                    )

                    AlpacaTradingClient._instances = {}

                    manager = AlpacaOrderManager(mode="paper")

                    with pytest.raises(ValueError, match="Quantity must be positive"):
                        manager._validate_order("SPY", -10, "buy")

    def test_validate_order_invalid_side(self, mock_config, mock_trading_client):
        """Test validation fails for invalid side."""
        with patch(
            "src.trading.broker.alpaca_trading_client.ConfigLoader", return_value=mock_config
        ):
            with patch(
                "src.trading.broker.alpaca_trading_client.TradingClient",
                return_value=mock_trading_client,
            ):
                with patch(
                    "src.trading.broker.alpaca_trading_client.ALPACA_TRADING_AVAILABLE", True
                ):
                    from src.trading.broker.alpaca_trading_client import (
                        AlpacaOrderManager,
                        AlpacaTradingClient,
                    )

                    AlpacaTradingClient._instances = {}

                    manager = AlpacaOrderManager(mode="paper")

                    with pytest.raises(ValueError, match="Side must be"):
                        manager._validate_order("SPY", 10, "hold")

    def test_validate_order_exceeds_max_size(self, mock_config, mock_trading_client):
        """Test validation fails when exceeding max order size."""
        with patch(
            "src.trading.broker.alpaca_trading_client.ConfigLoader", return_value=mock_config
        ):
            with patch(
                "src.trading.broker.alpaca_trading_client.TradingClient",
                return_value=mock_trading_client,
            ):
                with patch(
                    "src.trading.broker.alpaca_trading_client.ALPACA_TRADING_AVAILABLE", True
                ):
                    from src.trading.broker.alpaca_trading_client import (
                        AlpacaOrderManager,
                        AlpacaTradingClient,
                    )

                    AlpacaTradingClient._instances = {}

                    manager = AlpacaOrderManager(mode="paper")

                    with pytest.raises(ValueError, match="exceeds limit"):
                        manager._validate_order("SPY", 10000, "buy")


class TestAlpacaOrderManagerMarketOrder:
    """Test AlpacaOrderManager.place_market_order()."""

    def test_place_market_order_success(self, mock_config, mock_trading_client):
        """Test successful market order placement."""
        with patch(
            "src.trading.broker.alpaca_trading_client.ConfigLoader", return_value=mock_config
        ):
            with patch(
                "src.trading.broker.alpaca_trading_client.TradingClient",
                return_value=mock_trading_client,
            ):
                with patch(
                    "src.trading.broker.alpaca_trading_client.ALPACA_TRADING_AVAILABLE", True
                ):
                    with patch("src.trading.broker.alpaca_trading_client.map_side") as mock_side:
                        mock_side.side_effect = lambda side: side

                        with patch(
                            "src.trading.broker.alpaca_trading_client.TimeInForce"
                        ) as mock_tif:
                            mock_tif.DAY = "day"
                            mock_tif.GTC = "gtc"

                            with patch(
                                "src.trading.broker.alpaca_trading_client.MarketOrderRequest"
                            ):
                                from src.trading.broker.alpaca_trading_client import (
                                    AlpacaOrderManager,
                                    AlpacaTradingClient,
                                )

                                AlpacaTradingClient._instances = {}

                                manager = AlpacaOrderManager(mode="paper")
                                result = manager.place_market_order("SPY", 10, "buy")

                                assert result["status"] == "submitted"
                                assert result["symbol"] == "SPY"
                                assert result["mode"] == "paper"


class TestAlpacaOrderManagerLimitOrder:
    """Test AlpacaOrderManager.place_limit_order_gtc()."""

    def test_place_limit_order_negative_price(self, mock_config, mock_trading_client):
        """Test limit order fails with negative price."""
        with patch(
            "src.trading.broker.alpaca_trading_client.ConfigLoader", return_value=mock_config
        ):
            with patch(
                "src.trading.broker.alpaca_trading_client.TradingClient",
                return_value=mock_trading_client,
            ):
                with patch(
                    "src.trading.broker.alpaca_trading_client.ALPACA_TRADING_AVAILABLE", True
                ):
                    from src.trading.broker.alpaca_trading_client import (
                        AlpacaOrderManager,
                        AlpacaTradingClient,
                    )

                    AlpacaTradingClient._instances = {}

                    manager = AlpacaOrderManager(mode="paper")
                    result = manager.place_limit_order_gtc("SPY", 10, "buy", -100.0)

                    assert result["status"] == "error"
                    assert "positive" in result["message"]


class TestAlpacaOrderManagerCancelOrder:
    """Test AlpacaOrderManager.cancel_order()."""

    def test_cancel_order_success(self, mock_config, mock_trading_client):
        """Test successful order cancellation."""
        with patch(
            "src.trading.broker.alpaca_trading_client.ConfigLoader", return_value=mock_config
        ):
            with patch(
                "src.trading.broker.alpaca_trading_client.TradingClient",
                return_value=mock_trading_client,
            ):
                with patch(
                    "src.trading.broker.alpaca_trading_client.ALPACA_TRADING_AVAILABLE", True
                ):
                    from src.trading.broker.alpaca_trading_client import (
                        AlpacaOrderManager,
                        AlpacaTradingClient,
                    )

                    AlpacaTradingClient._instances = {}

                    manager = AlpacaOrderManager(mode="paper")
                    result = manager.cancel_order("order-123")

                    assert result["status"] == "cancelled"
                    assert result["order_id"] == "order-123"

    def test_cancel_order_api_error(self, mock_config, mock_trading_client):
        """Test cancel order handles API errors."""
        mock_trading_client.cancel_order_by_id.side_effect = Exception("API Error")

        with patch(
            "src.trading.broker.alpaca_trading_client.ConfigLoader", return_value=mock_config
        ):
            with patch(
                "src.trading.broker.alpaca_trading_client.TradingClient",
                return_value=mock_trading_client,
            ):
                with patch(
                    "src.trading.broker.alpaca_trading_client.ALPACA_TRADING_AVAILABLE", True
                ):
                    from src.trading.broker.alpaca_trading_client import (
                        AlpacaOrderManager,
                        AlpacaTradingClient,
                    )

                    AlpacaTradingClient._instances = {}

                    manager = AlpacaOrderManager(mode="paper")
                    result = manager.cancel_order("order-123")

                    assert result["status"] == "error"
                    assert "API Error" in result["message"]


class TestAlpacaOrderManagerClosePosition:
    """Test AlpacaOrderManager.close_position()."""

    def test_close_position_no_position(self, mock_config, mock_trading_client):
        """Test close_position when no position exists."""
        mock_trading_client.get_all_positions.return_value = []

        with patch(
            "src.trading.broker.alpaca_trading_client.ConfigLoader", return_value=mock_config
        ):
            with patch(
                "src.trading.broker.alpaca_trading_client.TradingClient",
                return_value=mock_trading_client,
            ):
                with patch(
                    "src.trading.broker.alpaca_trading_client.ALPACA_TRADING_AVAILABLE", True
                ):
                    from src.trading.broker.alpaca_trading_client import (
                        AlpacaOrderManager,
                        AlpacaTradingClient,
                    )

                    AlpacaTradingClient._instances = {}

                    manager = AlpacaOrderManager(mode="paper")
                    result = manager.close_position("AAPL")

                    assert result["status"] == "error"
                    assert "No position found" in result["message"]


class TestAlpacaOrderManagerStopOrder:
    """Test AlpacaOrderManager.place_stop_order()."""

    def test_place_stop_order_negative_price(self, mock_config, mock_trading_client):
        """Test stop order fails with negative price."""
        with patch(
            "src.trading.broker.alpaca_trading_client.ConfigLoader", return_value=mock_config
        ):
            with patch(
                "src.trading.broker.alpaca_trading_client.TradingClient",
                return_value=mock_trading_client,
            ):
                with patch(
                    "src.trading.broker.alpaca_trading_client.ALPACA_TRADING_AVAILABLE", True
                ):
                    from src.trading.broker.alpaca_trading_client import (
                        AlpacaOrderManager,
                        AlpacaTradingClient,
                    )

                    AlpacaTradingClient._instances = {}

                    manager = AlpacaOrderManager(mode="paper")
                    result = manager.place_stop_order("SPY", 10, "sell", -100.0)

                    assert result["status"] == "error"
                    assert "positive" in result["message"]


class TestAlpacaOrderManagerTrailingStop:
    """Test AlpacaOrderManager.place_trailing_stop_order()."""

    def test_place_trailing_stop_no_trail_params(self, mock_config, mock_trading_client):
        """Test trailing stop order fails without trail parameters."""
        with patch(
            "src.trading.broker.alpaca_trading_client.ConfigLoader", return_value=mock_config
        ):
            with patch(
                "src.trading.broker.alpaca_trading_client.TradingClient",
                return_value=mock_trading_client,
            ):
                with patch(
                    "src.trading.broker.alpaca_trading_client.ALPACA_TRADING_AVAILABLE", True
                ):
                    from src.trading.broker.alpaca_trading_client import (
                        AlpacaOrderManager,
                        AlpacaTradingClient,
                    )

                    AlpacaTradingClient._instances = {}

                    manager = AlpacaOrderManager(mode="paper")
                    result = manager.place_trailing_stop_order("SPY", 10, "sell")

                    assert result["status"] == "error"
                    assert "trail_percent or trail_price" in result["message"]


class TestAlpacaOrderManagerBracketOrder:
    """Test AlpacaOrderManager.place_bracket_order()."""

    def test_place_bracket_order_no_exit_prices(self, mock_config, mock_trading_client):
        """Test bracket order fails without exit prices."""
        with patch(
            "src.trading.broker.alpaca_trading_client.ConfigLoader", return_value=mock_config
        ):
            with patch(
                "src.trading.broker.alpaca_trading_client.TradingClient",
                return_value=mock_trading_client,
            ):
                with patch(
                    "src.trading.broker.alpaca_trading_client.ALPACA_TRADING_AVAILABLE", True
                ):
                    from src.trading.broker.alpaca_trading_client import (
                        AlpacaOrderManager,
                        AlpacaTradingClient,
                    )

                    AlpacaTradingClient._instances = {}

                    manager = AlpacaOrderManager(mode="paper")
                    result = manager.place_bracket_order("SPY", 10, "buy")

                    assert result["status"] == "error"
                    assert "take_profit_price or stop_loss_price" in result["message"]


class TestAlpacaMarketHours:
    """Market status from the broker's market_hours.is_market_hours."""

    def test_is_market_hours_returns_dict(self):
        from src.trading.broker.market_hours import is_market_hours

        for extended in (False, True):
            result = is_market_hours(extended)
            assert "is_open" in result
            assert result["session"] == ("extended" if extended else "regular")
            assert "current_session" in result


# =============================================================================
# Convenience Functions Tests
# =============================================================================


class TestConvenienceFunctions:
    """Test module-level convenience functions."""

    def test_get_account_status_function(self, mock_config, mock_trading_client):
        """Test get_account_status convenience function."""
        with patch(
            "src.trading.broker.alpaca_trading_client.ConfigLoader", return_value=mock_config
        ):
            with patch(
                "src.trading.broker.alpaca_trading_client.TradingClient",
                return_value=mock_trading_client,
            ):
                with patch(
                    "src.trading.broker.alpaca_trading_client.ALPACA_TRADING_AVAILABLE", True
                ):
                    from src.trading.broker.alpaca_trading_client import (
                        AlpacaTradingClient,
                        get_account_status,
                    )

                    AlpacaTradingClient._instances = {}

                    status = get_account_status(mode="paper")
                    assert "buying_power" in status

    def test_get_positions_function(self, mock_config, mock_trading_client):
        """Test get_positions convenience function."""
        with patch(
            "src.trading.broker.alpaca_trading_client.ConfigLoader", return_value=mock_config
        ):
            with patch(
                "src.trading.broker.alpaca_trading_client.TradingClient",
                return_value=mock_trading_client,
            ):
                with patch(
                    "src.trading.broker.alpaca_trading_client.ALPACA_TRADING_AVAILABLE", True
                ):
                    from src.trading.broker.alpaca_trading_client import (
                        AlpacaTradingClient,
                        get_positions,
                    )

                    AlpacaTradingClient._instances = {}

                    positions = get_positions(mode="paper")
                    assert isinstance(positions, list)

    def test_get_orders_function(self, mock_config, mock_trading_client):
        """Test get_orders convenience function."""
        with patch(
            "src.trading.broker.alpaca_trading_client.ConfigLoader", return_value=mock_config
        ):
            with patch(
                "src.trading.broker.alpaca_trading_client.TradingClient",
                return_value=mock_trading_client,
            ):
                with patch(
                    "src.trading.broker.alpaca_trading_client.ALPACA_TRADING_AVAILABLE", True
                ):
                    with patch(
                        "src.trading.broker.alpaca_trading_client.QueryOrderStatus"
                    ) as mock_query:
                        mock_query.OPEN = "open"

                        from src.trading.broker.alpaca_trading_client import (
                            AlpacaTradingClient,
                            get_orders,
                        )

                        AlpacaTradingClient._instances = {}

                        orders = get_orders(mode="paper", status="open")
                        assert isinstance(orders, list)


# =============================================================================
# Run tests if executed directly
# =============================================================================


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
