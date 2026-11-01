"""
CLI alert tools (#599): the same check the routines run, against an in-memory reconcile;
entries saved under the old timestamp keys never show up.
"""

from unittest.mock import MagicMock

import pytest

from src.cli.tools import alert_tools
from src.trading.positions.position_tracker import PositionTracker
from src.trading.scheduling.trading_cycle import CostEfficientTradeCycle
from src.trading.state.state_reconciler import StateReconciler


def _old_entry(position_id, ticker, entry):
    """Saved before #599: timestamp key, default levels below today's prices."""
    return {
        "position_id": position_id,
        "ticker": ticker,
        "entry_date": "2025-12-11T10:40:55",
        "entry_price": entry,
        "quantity": 1,
        "take_profit_price": entry * 1.08,
        "stop_loss_price": entry * 0.95,
        "status": "ACTIVE",
        "alert_history": [],
    }


def _broker(spy, orders=True):
    def stop(symbol, price, qty):
        return {"id": f"sl-{symbol}", "symbol": symbol, "type": "stop", "side": "sell",
                "quantity": qty, "stop_price": price, "time_in_force": "gtc"}  # fmt: skip

    return {
        "positions": {
            "SPY": {
                "symbol": "SPY",
                "quantity": 14,
                "entry_price": 657.6,
                "current_price": spy,
                "side": "long",
            },  # fmt: skip
            "QQQ": {
                "symbol": "QQQ",
                "quantity": 3,
                "entry_price": 616.8,
                "current_price": 715.19,
                "side": "long",
            },  # fmt: skip
        },
        "orders": {"SPY": [stop("SPY", 744.47, 14)], "QQQ": [stop("QQQ", 698.77, 3)]}
        if orders
        else {},
        "account": {},
    }


@pytest.fixture
def cycle(monkeypatch):
    tracker = PositionTracker(take_profit_pct=0.08, stop_loss_pct=0.05)
    tracker.restore_from_dict(
        {
            "positions": {
                "SPY_20251211_104055": _old_entry("SPY_20251211_104055", "SPY", 657.6),
                "QQQ_20261015_024405": _old_entry("QQQ_20261015_024405", "QQQ", 616.8),
                "AAPL_20251211_104055": _old_entry("AAPL_20251211_104055", "AAPL", 230.0),
            }
        }
    )
    c = object.__new__(CostEfficientTradeCycle)  # no broker clients
    c.position_tracker = tracker
    c.reconciler = StateReconciler(config=MagicMock(), position_tracker=tracker)
    # Local levels are stale; the in-memory reconcile takes the broker's live stops
    c.local_state_manager = MagicMock(
        state={
            "positions": {
                "SPY": {"entry_price": 657.6, "quantity": 14, "stop_price": 740.0},
                "QQQ": {"entry_price": 616.8, "quantity": 3, "stop_price": 690.0},
            }
        }
    )
    c.fetch_broker_state = MagicMock(return_value=_broker(spy=750.0))
    monkeypatch.setattr(alert_tools, "_get_trading_cycle", lambda: c)
    return c


def test_check_alerts_reports_open_positions_only(cycle):
    result = alert_tools.check_alerts()

    assert result["status"] == "success"
    assert [(a["ticker"], a["alert_type"]) for a in result["alerts"]] == [("SPY", "approaching_sl")]
    assert result["alerts"][0]["details"]["stop_price"] == 744.47  # the live stop
    assert result["positions_monitored"] == 2
    cycle.local_state_manager.save.assert_not_called()


def test_no_alerts_when_prices_are_clear(cycle):
    cycle.fetch_broker_state.return_value = _broker(spy=767.39)

    result = alert_tools.check_alerts()

    assert result["status"] == "no_alerts" and result["positions_monitored"] == 2


def test_an_unreachable_broker_is_an_error(cycle):
    cycle.fetch_broker_state.side_effect = ConnectionError("timeout")

    result = alert_tools.check_alerts()

    assert result["status"] == "error" and "timeout" in result["error"]


def test_show_alerts_uses_readable_labels(cycle):
    # No stop orders at the broker: the last known local level ($740.00) is the one checked
    cycle.fetch_broker_state.return_value = _broker(spy=735.0, orders=False)

    text = alert_tools.show_alerts()

    assert "SPY: stop breached, position still open @ $735.00" in text
    assert "level $740.00, 0.68% through it" in text
    assert "no live stop order" in text


def test_alert_history_honours_the_limit(cycle):
    cycle.position_tracker.alert_cooldown_seconds = 0
    alert_tools.check_alerts()
    alert_tools.check_alerts()

    result = alert_tools.get_alert_history(limit=1)

    assert result["returned"] == 1 and result["total_alerts"] == 2
