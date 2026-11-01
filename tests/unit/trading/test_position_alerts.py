"""
Position alerts (#599): open positions near or through their stop, and their target where
one exists. The live positions exit by trailing stop only, with no target.

Before #599 the routines never produced an alert: positions without a target were
skipped, and the tracker stored each entry under a timestamp key the reconciler never
looked up (310 saved entries for 2 open positions).
"""

from unittest.mock import MagicMock

import pytest

import src.trading.scheduling.trading_cycle as cycle_module
from src.trading.positions.position_tracker import (
    MAX_ALERT_HISTORY,
    AlertType,
    PositionTracker,
)
from src.trading.scheduling.trading_cycle import CostEfficientTradeCycle
from src.trading.state.state_reconciler import StateReconciler
from src.trading.utils.report_generator import ReportGenerator

# The live paper positions on 2026-10-27
SPY_ENTRY, SPY_STOP = 657.60, 744.47
QQQ_ENTRY, QQQ_STOP = 616.80, 698.77


def _tracker():
    return PositionTracker(take_profit_pct=0.08, stop_loss_pct=0.05)


def _old_entry(position_id, ticker, entry, alerts=()):
    """A tracker entry as saved before #599: timestamp key, default levels."""
    return {
        "position_id": position_id,
        "ticker": ticker,
        "entry_date": "2025-12-11T10:40:55",
        "entry_price": entry,
        "quantity": 1,
        "take_profit_price": entry * 1.08,
        "stop_loss_price": entry * 0.95,
        "status": "ACTIVE",
        "exit_date": None,
        "exit_price": None,
        "exit_reason": None,
        "alert_history": list(alerts),
        "last_alert_time": None,
    }


def _old_tracker_state():
    return {
        "alert_counter": 0,
        "positions": {
            "AAPL_20251211_104055": _old_entry("AAPL_20251211_104055", "AAPL", 230.0),
            "SPY_20251211_104055": _old_entry("SPY_20251211_104055", "SPY", 657.6),
            "QQQ_20261015_024405": _old_entry("QQQ_20261015_024405", "QQQ", 616.8),
        },
    }


def _sell_stop(symbol, stop_price, quantity):
    return {"id": f"sl-{symbol}", "symbol": symbol, "type": "stop", "side": "sell",
            "quantity": quantity, "stop_price": stop_price, "time_in_force": "gtc"}  # fmt: skip


def _broker(spy=767.39, qqq=715.19, orders=None):
    if orders is None:
        orders = {"SPY": [_sell_stop("SPY", SPY_STOP, 14)], "QQQ": [_sell_stop("QQQ", QQQ_STOP, 3)]}
    return {
        "positions": {
            "SPY": {
                "symbol": "SPY",
                "quantity": 14,
                "entry_price": SPY_ENTRY,
                "current_price": spy,
                "side": "long",
            },  # fmt: skip
            "QQQ": {
                "symbol": "QQQ",
                "quantity": 3,
                "entry_price": QQQ_ENTRY,
                "current_price": qqq,
                "side": "long",
            },  # fmt: skip
        },
        "orders": orders,
        "account": {},
    }


def _local(spy_stop=SPY_STOP, qqq_stop=QQQ_STOP, spy_target=None):
    return {
        "positions": {
            "SPY": {
                "entry_price": SPY_ENTRY,
                "quantity": 14,
                "stop_price": spy_stop,
                "target_price": spy_target,
            },  # fmt: skip
            "QQQ": {
                "entry_price": QQQ_ENTRY,
                "quantity": 3,
                "stop_price": qqq_stop,
                "target_price": None,
            },  # fmt: skip
        }
    }


# --- PositionTracker -------------------------------------------------------------------


def test_a_stop_only_position_alerts_near_its_stop():
    tracker = _tracker()
    tracker.sync_position("SPY", SPY_ENTRY, 14, SPY_STOP)

    condition = tracker.check_exit_conditions("SPY", 758.0)

    assert condition["recommendation"] == "ALERT"
    assert condition["alert_type"] == AlertType.APPROACHING_STOP_LOSS
    assert condition["severity"] == "WARNING"
    # 1.78% of the price; measured from the entry (the old rule) it would be 2.06%, no alert
    assert condition["details"]["distance_pct"] == pytest.approx((758.0 - SPY_STOP) / 758.0 * 100)
    assert condition["message"] == "⚠️ SPY is 1.78% above its stop $744.47 (price $758.00)"


def test_no_alert_with_the_price_well_clear_and_no_target():
    tracker = _tracker()
    tracker.sync_position("SPY", SPY_ENTRY, 14, SPY_STOP)

    assert tracker.check_exit_conditions("SPY", 767.39) is None  # 2.99% above the stop
    assert tracker.check_exit_conditions("SPY", 5000.0) is None  # no target to reach


def test_through_the_stop_with_the_position_open_is_critical():
    tracker = _tracker()
    tracker.sync_position("SPY", SPY_ENTRY, 14, SPY_STOP)

    condition = tracker.check_exit_conditions("SPY", 740.0)

    assert condition["recommendation"] == "EXIT"
    assert condition["alert_type"] == AlertType.LOSS_THRESHOLD_REACHED
    assert condition["severity"] == "CRITICAL"
    assert "at or below its stop $744.47 (price $740.00), still open" in condition["message"]


def test_target_alerts_only_when_there_is_a_target():
    tracker = _tracker()
    tracker.sync_position("SPY", SPY_ENTRY, 14, SPY_STOP, take_profit_price=780.0)

    near = tracker.check_exit_conditions("SPY", 770.0)
    assert (near["alert_type"], near["severity"]) == (AlertType.APPROACHING_TAKE_PROFIT, "WARNING")
    reached = tracker.check_exit_conditions("SPY", 781.0)
    assert reached["recommendation"] == "EXIT"
    assert (reached["alert_type"], reached["severity"]) == (
        AlertType.PROFIT_TARGET_REACHED,
        "CRITICAL",
    )


def test_every_check_returns_the_condition_and_the_cooldown_only_limits_history():
    tracker = _tracker()  # 300 s cooldown
    tracker.sync_position("SPY", SPY_ENTRY, 14, SPY_STOP)

    first = tracker.check_exit_conditions("SPY", 750.0)
    second = tracker.check_exit_conditions("SPY", 749.0)

    assert first["alert"] is not None and second["alert"] is None
    assert second["details"]["distance_pct"] == pytest.approx((749.0 - SPY_STOP) / 749.0 * 100)
    assert len(tracker.positions["SPY"].alert_history) == 1


def test_a_new_condition_is_recorded_within_the_cooldown():
    tracker = _tracker()  # 300 s cooldown
    tracker.sync_position("SPY", SPY_ENTRY, 14, SPY_STOP)

    tracker.check_exit_conditions("SPY", 750.0)  # near the stop
    breach = tracker.check_exit_conditions("SPY", 740.0)  # through it, seconds later
    again = tracker.check_exit_conditions("SPY", 739.0)

    assert breach["alert"] is not None and again["alert"] is None
    assert [a.alert_type for a in tracker.positions["SPY"].alert_history] == [
        AlertType.APPROACHING_STOP_LOSS,
        AlertType.LOSS_THRESHOLD_REACHED,
    ]


def test_the_earlier_return_keys_are_kept():
    tracker = _tracker()
    tracker.sync_position("SPY", SPY_ENTRY, 14, SPY_STOP)

    near = tracker.check_exit_conditions("SPY", 750.0)
    through = tracker.check_exit_conditions("SPY", 740.0)

    assert near["distance_pct"] == near["details"]["distance_pct"]
    assert near["distance_dollars"] == pytest.approx(750.0 - SPY_STOP)
    assert through["exit_price"] == 740.0
    assert through["pnl_pct"] == pytest.approx((740.0 - SPY_ENTRY) / SPY_ENTRY * 100)


def test_alert_history_is_capped():
    tracker = PositionTracker(take_profit_pct=0.08, stop_loss_pct=0.05, alert_cooldown_seconds=0)
    tracker.sync_position("SPY", SPY_ENTRY, 14, SPY_STOP)

    for _ in range(MAX_ALERT_HISTORY + 5):
        tracker.check_exit_conditions("SPY", 750.0)

    assert len(tracker.positions["SPY"].alert_history) == MAX_ALERT_HISTORY


def test_one_entry_per_position_with_its_levels_refreshed():
    tracker = _tracker()
    tracker.sync_position("SPY", SPY_ENTRY, 14, 740.50)
    tracker.sync_position("SPY", SPY_ENTRY, 14, SPY_STOP, take_profit_price=800.0)

    assert list(tracker.positions) == ["SPY"]
    position = tracker.positions["SPY"]
    assert (position.stop_loss_price, position.take_profit_price) == (SPY_STOP, 800.0)

    tracker.sync_position("spy", SPY_ENTRY, 14, SPY_STOP)  # the target lapsed
    assert list(tracker.positions) == ["SPY"] and position.take_profit_price is None


def test_prune_keeps_only_open_positions():
    tracker = _tracker()
    tracker.restore_from_dict(_old_tracker_state())
    tracker.sync_position("SPY", SPY_ENTRY, 14, SPY_STOP)
    tracker.sync_position("QQQ", QQQ_ENTRY, 3, QQQ_STOP)

    dropped = tracker.prune(["SPY", "QQQ"])

    assert set(tracker.positions) == {"SPY", "QQQ"}
    assert len(dropped) == 3


def test_restore_replaces_the_entries_and_tolerates_old_data():
    tracker = _tracker()
    tracker.sync_position("MSFT", 400.0, 2, 390.0)
    unknown = {"alert_id": "a1", "position_id": "SPY", "ticker": "SPY", "alert_type": "new_kind",
               "timestamp": "2026-10-27T13:20:06", "current_price": 750.0, "details": {},
               "severity": "WARNING"}  # fmt: skip
    entry = _old_entry("SPY", "SPY", SPY_ENTRY, alerts=[unknown])
    entry["take_profit_price"] = None

    tracker.restore_from_dict({"alert_counter": 4, "positions": {"SPY": entry}})

    assert list(tracker.positions) == ["SPY"]  # MSFT is gone: restore replaces
    assert tracker.positions["SPY"].take_profit_price is None
    assert tracker.positions["SPY"].alert_history == []  # the unknown type is skipped
    assert tracker.to_dict()["positions"]["SPY"]["take_profit_price"] is None


# --- StateReconciler.check_position_alerts ----------------------------------------------


def _reconciler(tracker):
    return StateReconciler(config=MagicMock(), position_tracker=tracker)


def test_trailing_stop_only_positions_get_stop_alerts():
    """The #599 acceptance case: a stop and no target."""
    reconciler = _reconciler(_tracker())

    alerts = reconciler.check_position_alerts(_broker(spy=750.0), _local())

    assert [(a.symbol, a.alert_type, a.severity) for a in alerts] == [
        ("SPY", "approaching_sl", "WARNING")
    ]
    assert alerts[0].details["live_stop"] is True
    assert alerts[0].message == "⚠️ SPY is 0.74% above its stop $744.47 (price $750.00)"


def test_a_breach_with_no_live_stop_says_so():
    reconciler = _reconciler(_tracker())

    alerts = reconciler.check_position_alerts(_broker(spy=740.0, orders={}), _local())

    assert len(alerts) == 1 and alerts[0].severity == "CRITICAL"
    assert alerts[0].details["live_stop"] is False
    assert alerts[0].message.endswith("still open - no live stop order")


def test_repeated_runs_keep_one_entry_per_position_at_the_latest_levels():
    tracker = _tracker()
    reconciler = _reconciler(tracker)

    reconciler.check_position_alerts(_broker(), _local(spy_stop=740.50))
    reconciler.check_position_alerts(_broker(), _local(spy_stop=SPY_STOP))  # after a raise

    assert set(tracker.positions) == {"SPY", "QQQ"}
    assert tracker.positions["SPY"].stop_loss_price == SPY_STOP


def test_old_entries_and_closed_positions_are_dropped():
    tracker = _tracker()
    tracker.restore_from_dict(_old_tracker_state())
    reconciler = _reconciler(tracker)

    reconciler.check_position_alerts(_broker(), _local())
    assert set(tracker.positions) == {"SPY", "QQQ"}

    closed = _broker()
    del closed["positions"]["QQQ"]  # QQQ sold since
    reconciler.check_position_alerts(closed, _local())
    assert set(tracker.positions) == {"SPY"}


def test_an_entry_is_dropped_when_its_position_is_not_checked():
    """Open, but without a stop this run: its entry would keep stale levels."""
    tracker = _tracker()
    reconciler = _reconciler(tracker)
    reconciler.check_position_alerts(_broker(), _local())

    reconciler.check_position_alerts(_broker(), _local(qqq_stop=None))

    assert set(tracker.positions) == {"SPY"}


def test_short_unknown_and_incomplete_positions_are_skipped():
    tracker = _tracker()
    reconciler = _reconciler(tracker)
    broker = _broker(spy=750.0, qqq=699.0)
    broker["positions"]["SPY"]["side"] = "short"
    broker["positions"]["TSLA"] = {"symbol": "TSLA", "quantity": 1, "entry_price": 200.0,
                                   "current_price": 201.0, "side": "long"}  # fmt: skip
    local = _local()
    local["positions"]["QQQ"]["stop_price"] = None

    assert reconciler.check_position_alerts(broker, local) == []
    assert tracker.positions == {}


def test_one_positions_error_does_not_cost_the_others_alert(monkeypatch):
    tracker = _tracker()
    reconciler = _reconciler(tracker)
    real = tracker.check_exit_conditions

    def failing_for_qqq(position_id, price):
        if position_id == "QQQ":
            raise RuntimeError("bad data")
        return real(position_id, price)

    monkeypatch.setattr(tracker, "check_exit_conditions", failing_for_qqq)

    alerts = reconciler.check_position_alerts(_broker(spy=750.0, qqq=699.0), _local())

    assert [a.symbol for a in alerts] == ["SPY"]


# --- The routines -----------------------------------------------------------------------


@pytest.fixture
def cycle(tmp_path, monkeypatch):
    """A cycle with the real reconciler, tracker and report code, and a mock broker."""
    monkeypatch.setattr(cycle_module, "GTT_AVAILABLE", False)
    monkeypatch.setattr(cycle_module, "is_market_hours", lambda: True)
    tracker = _tracker()
    tracker.restore_from_dict(_old_tracker_state())

    c = object.__new__(CostEfficientTradeCycle)  # no broker clients
    c.order_manager = MagicMock()
    c.account_monitor = MagicMock()
    c.broker_cache = MagicMock()
    c.position_tracker = tracker
    c.reconciler = StateReconciler(config=MagicMock(), position_tracker=tracker)
    c.local_state_manager = MagicMock(state=_local())
    c.trailing_stop_manager = None
    c.report_generator = ReportGenerator()
    c.last_report_path = None
    c.fetch_broker_state = MagicMock(return_value=_broker(spy=750.0))
    c.record_daily = MagicMock(return_value=[])
    c.reconcile_state = MagicMock(return_value=[])
    c.check_and_repair_stops = MagicMock(return_value=[])
    c.calculate_stop_adjustments = MagicMock(return_value=[])
    c._get_report_file_path = lambda routine: str(tmp_path / f"{routine}.md")
    return c


@pytest.mark.parametrize("routine", ["morning_routine", "evening_routine"])
def test_the_report_lists_the_alert(cycle, routine):
    report = getattr(cycle, routine)(raise_errors=True)

    assert "## Position Alerts" in report
    assert "⚠️ SPY is 0.74% above its stop $744.47 (price $750.00)" in report
    assert [a.symbol for a in cycle.last_alerts] == ["SPY"]
    assert set(cycle.position_tracker.positions) == {"SPY", "QQQ"}  # saved with the state
    cycle.local_state_manager.save.assert_called_once()


@pytest.mark.parametrize("routine", ["morning_routine", "evening_routine"])
def test_a_failing_alert_check_is_reported_and_does_not_fail_the_routine(
    cycle, routine, monkeypatch
):
    monkeypatch.setattr(
        cycle.reconciler, "check_position_alerts", MagicMock(side_effect=RuntimeError("boom"))
    )

    report = getattr(cycle, routine)(raise_errors=True)  # no RoutineError

    assert "⚠️ Position alerts not checked: boom" in report
    assert cycle.last_alerts == ()
    cycle.local_state_manager.save.assert_called_once()


def test_alerts_measure_the_stop_after_the_raise(cycle, monkeypatch):
    """The raise happens before the alert check, so the alert sees the new stop."""

    def raise_spy_stop(broker_state):
        cycle.local_state_manager.state["positions"]["SPY"]["stop_price"] = 747.0
        return []

    cycle.calculate_stop_adjustments = MagicMock(side_effect=raise_spy_stop)
    monkeypatch.setattr(
        "src.trading.scheduling.trading_cycle.is_market_hours", lambda: True
    )  # the evening raise needs an open market

    cycle.evening_routine(raise_errors=True)

    assert cycle.position_tracker.positions["SPY"].stop_loss_price == 747.0
    assert cycle.last_alerts[0].details["stop_price"] == 747.0
