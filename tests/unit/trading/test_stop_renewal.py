"""
Protective stops don't lapse silently (#586).

Alpaca cancels GTC orders 90 days after creation, bracket legs included. A long
position with no live stop gets one re-placed at its last known stop, and a GTC stop
nearing 90 days is renewed. All broker calls are mocked; no orders are placed.
"""

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from src.trading.scheduling.trading_cycle import CostEfficientTradeCycle
from src.trading.state.state_reconciler import StateReconciler, StopAdjustment, StopRepair

NOW = datetime(2026, 10, 14, 13, 0, tzinfo=timezone.utc)


def _reconciler():
    config = MagicMock()
    config.get_risk_config.return_value = 0.05  # stop loss
    return StateReconciler(config=config)


def _broker(price=769.2, quantity=14, orders=(), side="long"):
    return {
        "positions": {
            "SPY": {
                "symbol": "SPY",
                "quantity": quantity,
                "entry_price": 657.6,
                "current_price": price,
                "side": side,
            }
        },
        "orders": {"SPY": list(orders)} if orders else {},
    }


def _local(stop_price=627.0):
    return {"positions": {"SPY": {"entry_price": 657.6, "quantity": 14, "stop_price": stop_price}}}


def _stop_order(age_days, tif="gtc", order_id="sl-1"):
    return {
        "id": order_id,
        "type": "stop",
        "side": "sell",
        "quantity": 14,
        "stop_price": 627.0,
        "time_in_force": tif,
        "created_at": (NOW - timedelta(days=age_days)).isoformat(),
    }


class TestCheckProtectiveStops:
    def test_missing_stop_is_replaced_at_last_known_stop(self):
        review, repairs = _reconciler().check_protective_stops(_broker(), _local(), NOW)

        assert review == []
        assert repairs == [
            StopRepair(
                kind="MISSING_STOP",
                symbol="SPY",
                quantity=14,
                stop_price=627.0,
                reason="no live stop order (GTC stops lapse after 90 days)",
            )
        ]

    def test_missing_stop_without_saved_level_uses_entry_minus_stop_loss(self):
        _, repairs = _reconciler().check_protective_stops(_broker(), _local(None), NOW)

        assert repairs[0].stop_price == round(657.6 * 0.95, 2)

    def test_position_below_its_stop_is_left_for_review(self):
        review, repairs = _reconciler().check_protective_stops(_broker(price=600.0), _local(), NOW)

        assert repairs == []
        assert review[0].type == "MISSING_STOP"
        assert review[0].action == "NEEDS_HUMAN_REVIEW"

    def test_aging_gtc_stop_is_renewed(self):
        broker = _broker(orders=[_stop_order(age_days=85)])

        review, repairs = _reconciler().check_protective_stops(broker, _local(), NOW)

        assert review == []
        assert len(repairs) == 1
        assert repairs[0].kind == "STOP_EXPIRING"
        assert repairs[0].replaces_order_id == "sl-1"
        assert repairs[0].stop_price == 627.0

    @pytest.mark.parametrize("order", [_stop_order(age_days=10), _stop_order(85, tif="day")])
    def test_fresh_or_day_stop_needs_nothing(self, order):
        review, repairs = _reconciler().check_protective_stops(
            _broker(orders=[order]), _local(), NOW
        )

        assert (review, repairs) == ([], [])

    def test_shares_held_by_another_sell_order_go_to_review(self):
        take_profit = {
            "id": "tp-1",
            "type": "limit",
            "side": "sell",
            "quantity": 14,
            "limit_price": 712.8,
            "time_in_force": "gtc",
            "created_at": (NOW - timedelta(days=5)).isoformat(),
        }
        review, repairs = _reconciler().check_protective_stops(
            _broker(orders=[take_profit]), _local(), NOW
        )

        assert repairs == []
        assert review[0].type == "MISSING_STOP"
        assert "held by open sell orders" in review[0].details["reason"]

    def test_aging_trailing_stop_goes_to_review(self):
        trailing = _stop_order(85)
        trailing["type"] = "trailing_stop"

        review, repairs = _reconciler().check_protective_stops(
            _broker(orders=[trailing]), _local(), NOW
        )

        assert repairs == []
        assert (review[0].type, review[0].action) == ("STOP_EXPIRING", "NEEDS_HUMAN_REVIEW")

    def test_aging_take_profit_is_noticed_next_to_a_renewed_stop(self):
        take_profit = {
            "id": "tp-1",
            "type": "limit",
            "side": "sell",
            "quantity": 14,
            "limit_price": 712.8,
            "time_in_force": "gtc",
            "created_at": (NOW - timedelta(days=85)).isoformat(),
        }

        review, repairs = _reconciler().check_protective_stops(
            _broker(orders=[_stop_order(85), take_profit]), _local(), NOW
        )

        assert [r.kind for r in repairs] == ["STOP_EXPIRING"]
        assert [(d.type, d.details["order_id"]) for d in review] == [("TARGET_EXPIRING", "tp-1")]

    def test_age_uses_utc_by_default(self):
        # A stop created 85 days ago in UTC; `now` is left to the default
        order = _stop_order(0)
        order["created_at"] = (datetime.now(timezone.utc) - timedelta(days=85)).isoformat()

        _, repairs = _reconciler().check_protective_stops(_broker(orders=[order]), _local())

        assert repairs and repairs[0].kind == "STOP_EXPIRING"

    def test_short_positions_are_skipped(self):
        review, repairs = _reconciler().check_protective_stops(_broker(side="short"), _local(), NOW)

        assert (review, repairs) == ([], [])


def _cycle(order_manager):
    cycle = object.__new__(CostEfficientTradeCycle)  # no broker clients
    cycle.order_manager = order_manager
    cycle.broker_cache = MagicMock()
    cycle.reconciler = _reconciler()
    cycle.local_state_manager = MagicMock(state=_local())
    cycle.trailing_stop_manager = None
    return cycle


class TestRepairStops:
    def test_missing_stop_is_placed_as_gtc_sell_stop(self):
        om = MagicMock()
        om.place_stop_order.return_value = {"status": "submitted", "order_id": "new-sl"}
        cycle = _cycle(om)

        discrepancies = cycle.check_and_repair_stops(_broker())

        om.place_stop_order.assert_called_once_with(
            symbol="SPY", qty=14, side="sell", stop_price=627.0, time_in_force="gtc"
        )
        assert discrepancies[0].type == "MISSING_STOP"
        assert discrepancies[0].action == "AUTO_FIXED"
        assert discrepancies[0].details["order_id"] == "new-sl"
        cycle.broker_cache.invalidate.assert_called_once()

    def test_failed_placement_needs_review(self):
        om = MagicMock()
        om.place_stop_order.return_value = {"status": "error", "message": "insufficient qty"}
        cycle = _cycle(om)

        discrepancies = cycle.check_and_repair_stops(_broker())

        assert discrepancies[0].action == "NEEDS_HUMAN_REVIEW"
        assert discrepancies[0].details["error"] == "insufficient qty"
        cycle.broker_cache.invalidate.assert_not_called()

    def test_aging_stop_is_renewed_by_replacing_it(self):
        om = MagicMock()
        om.modify_order.return_value = {"status": "submitted", "order_id": "sl-2"}
        cycle = _cycle(om)

        discrepancies = cycle.check_and_repair_stops(_broker(orders=[_stop_order(85)]))

        om.modify_order.assert_called_once_with(order_id="sl-1", stop_price=627.0)
        om.place_stop_order.assert_not_called()
        assert (discrepancies[0].type, discrepancies[0].action) == ("STOP_EXPIRING", "AUTO_FIXED")

    def test_unplaceable_stop_is_reported_without_an_order(self):
        om = MagicMock()
        cycle = _cycle(om)

        discrepancies = cycle.check_and_repair_stops(_broker(price=600.0))

        om.place_stop_order.assert_not_called()
        assert discrepancies[0].action == "NEEDS_HUMAN_REVIEW"

    def test_check_errors_do_not_escape(self):
        om = MagicMock()
        cycle = _cycle(om)
        cycle.local_state_manager.state = {"positions": {}}  # forces the config lookup
        cycle.reconciler.config.get_risk_config.side_effect = KeyError("stop_loss")

        discrepancies = cycle.check_and_repair_stops(_broker())

        assert (discrepancies[0].type, discrepancies[0].action) == (
            "STOP_CHECK_FAILED",
            "NEEDS_HUMAN_REVIEW",
        )
        om.place_stop_order.assert_not_called()


def test_failed_stop_modification_is_not_counted_as_success():
    om = MagicMock()
    om.modify_order.return_value = {"status": "error", "message": "order not found"}
    cycle = _cycle(om)
    adjustment = StopAdjustment("SPY", "sl-1", 627.0, 700.0, "trail", 0.17)

    results = cycle.batch_modify_orders([adjustment])

    assert results["modifications"] == 0
    assert not results["success"]
    assert "order not found" in results["errors"][0]


def test_report_shows_stop_details():
    from src.trading.state.state_reconciler import Discrepancy
    from src.trading.utils.report_generator import ReportGenerator, RoutineType

    disc = Discrepancy(
        type="MISSING_STOP",
        symbol="SPY",
        details={"quantity": 14, "stop_price": 624.72, "reason": "no live stop order"},
        action="AUTO_FIXED",
        severity="HIGH",
    )
    broker_state = {"positions": {}, "orders": {}, "account": {}}
    report = ReportGenerator().generate_routine_report(
        RoutineType.MORNING, broker_state, {"positions": {}}, [disc], [], []
    )

    assert "MISSING_STOP: SPY" in report
    assert "stop $624.72, 14 shares, no live stop order - AUTO_FIXED" in report


def _cycle_after_a_repair(monkeypatch, tmp_path):
    """A cycle whose stop check places a stop, so later steps need fresh broker state."""
    import src.trading.scheduling.trading_cycle as cycle_module
    from src.trading.state.state_reconciler import Discrepancy

    monkeypatch.setattr(cycle_module, "GTT_AVAILABLE", False)
    monkeypatch.setattr(cycle_module, "is_market_hours", lambda: True)
    cycle = _cycle(MagicMock())
    stale, fresh = {"positions": {}, "orders": {}}, {"positions": {}, "orders": {"SPY": []}}
    placed = Discrepancy("MISSING_STOP", "SPY", {}, "AUTO_FIXED", "HIGH")
    cycle.fetch_broker_state = MagicMock(side_effect=[stale, fresh])
    cycle.reconcile_state = MagicMock(return_value=[])
    cycle.check_and_repair_stops = MagicMock(return_value=[placed])
    cycle.check_position_alerts = MagicMock(return_value=[])
    cycle.check_gtt_triggers = MagicMock(return_value=[])
    cycle.calculate_stop_adjustments = MagicMock(return_value=[])
    cycle.generate_routine_report = MagicMock(return_value="report")
    cycle._get_report_file_path = lambda routine: str(tmp_path / f"{routine}.md")
    return cycle, stale, fresh


def test_evening_routine_adjusts_stops_against_fresh_broker_state(monkeypatch, tmp_path):
    cycle, stale, fresh = _cycle_after_a_repair(monkeypatch, tmp_path)

    cycle.evening_routine()

    cycle.fetch_broker_state.assert_called_with(force_refresh=True)
    cycle.check_and_repair_stops.assert_called_once_with(stale)
    cycle.calculate_stop_adjustments.assert_called_once_with(fresh)


def test_morning_routine_repairs_at_the_stops_level_and_raises_nothing(monkeypatch, tmp_path):
    cycle, stale, fresh = _cycle_after_a_repair(monkeypatch, tmp_path)

    cycle.morning_routine()

    cycle.check_and_repair_stops.assert_called_once_with(stale, raise_to_trailing=False)
    cycle.calculate_stop_adjustments.assert_not_called()
    cycle.check_gtt_triggers.assert_called_once_with(fresh)  # still sees the new stop


class _Trail:
    """
    Trailing-stop manager stub: its rules put the stop at ``level``, and like the real
    one it never moves a stop down (None unless above the state's current stop).
    """

    def __init__(self, level):
        self.level = level
        self.stop_states = {}

    def register_position(self, symbol, entry_price, initial_stop, quantity, stop_order_id=None):
        self.stop_states[symbol] = SimpleNamespace(
            current_stop=initial_stop, stop_order_id=stop_order_id
        )

    def calculate_new_stop(self, symbol, current_price):
        current = self.stop_states[symbol].current_stop
        return self.level if self.level is not None and self.level > current else None


def _trailing_reconciler(level):
    reconciler = _reconciler()
    reconciler.trailing_stop_manager = _Trail(level)
    return reconciler


class TestStopsAtTheTrailingLevel:
    """A re-placed or renewed stop goes straight to the trailing level (#574 follow-up).

    Orders placed outside market hours stay 'accepted' and can't be replaced in the same
    run, so placing at the old level and then raising it failed (2026-10-14).
    """

    def test_a_missing_stop_is_placed_at_the_trailing_level(self):
        _, repairs = _trailing_reconciler(739.21).check_protective_stops(_broker(), _local(), NOW)

        assert repairs[0].stop_price == 739.21
        assert "trailing level (last stop $627.00)" in repairs[0].reason

    @pytest.mark.parametrize("level", [None, 600.0])  # none, or below the saved stop
    def test_otherwise_the_last_known_stop_is_used(self, level):
        _, repairs = _trailing_reconciler(level).check_protective_stops(_broker(), _local(), NOW)

        assert repairs[0].stop_price == 627.0

    @pytest.mark.parametrize("level", [768.0, 780.0])  # within 1% of the price 769.2, above it
    def test_the_level_stays_one_percent_below_the_price(self, level):
        _, repairs = _trailing_reconciler(level).check_protective_stops(_broker(), _local(), NOW)

        assert repairs[0].stop_price == 761.51  # 769.2 x 0.99

    def test_a_stale_higher_stop_in_the_manager_cant_hide_the_level(self):
        # e.g. restored from GTT after a raise the broker refused (2026-10-14)
        reconciler = _trailing_reconciler(739.21)
        reconciler.trailing_stop_manager.register_position("SPY", 657.6, 739.21, 14)

        _, repairs = reconciler.check_protective_stops(_broker(), _local(), NOW)

        assert repairs[0].stop_price == 739.21

    def test_the_morning_places_a_missing_stop_at_its_last_level(self):
        """#623: a pre-market price doesn't set the stop, even through a repair."""
        _, repairs = _trailing_reconciler(739.21).check_protective_stops(
            _broker(), _local(), NOW, raise_to_trailing=False
        )

        assert repairs[0].stop_price == 627.0
        assert "trailing level" not in repairs[0].reason

    def test_the_morning_renews_an_aging_stop_at_its_level(self):
        orders = [_stop_order(85)]
        reconciler = _trailing_reconciler(739.21)

        _, repairs = reconciler.check_protective_stops(
            _broker(orders=orders), _local(), NOW, raise_to_trailing=False
        )

        assert repairs[0].kind == "STOP_EXPIRING"
        assert repairs[0].stop_price == orders[0]["stop_price"]
        assert repairs[0].replaces_order_id == "sl-1"
        assert "raised to the trailing level" not in repairs[0].reason
        assert reconciler.trailing_stop_manager.stop_states == {}  # nothing worked out

    def test_an_aging_stop_is_renewed_at_the_trailing_level(self):
        orders = [_stop_order(85)]
        _, repairs = _trailing_reconciler(739.21).check_protective_stops(
            _broker(orders=orders), _local(), NOW
        )

        assert (repairs[0].kind, repairs[0].stop_price) == ("STOP_EXPIRING", 739.21)
        assert repairs[0].replaces_order_id == "sl-1"

    def test_the_placed_stop_is_what_the_adjustment_step_starts_from(self):
        om = MagicMock()
        om.place_stop_order.return_value = {"status": "submitted", "order_id": "new-sl"}
        cycle = _cycle(om)
        cycle.reconciler = _trailing_reconciler(739.21)
        cycle.trailing_stop_manager = cycle.reconciler.trailing_stop_manager

        cycle.check_and_repair_stops(_broker())
        refreshed = _broker(orders=[{**_stop_order(0, order_id="new-sl"), "stop_price": 739.21}])
        adjustments, _ = cycle.reconciler.calculate_stop_adjustments(
            refreshed, cycle.local_state_manager.state
        )

        om.place_stop_order.assert_called_once_with(
            symbol="SPY", qty=14, side="sell", stop_price=739.21, time_in_force="gtc"
        )
        assert cycle.local_state_manager.state["positions"]["SPY"]["stop_price"] == 739.21
        assert adjustments == []  # nothing left to modify (it would fail while 'accepted')
        # the manager takes the stop from the broker, with its order
        state = cycle.trailing_stop_manager.stop_states["SPY"]
        assert (state.current_stop, state.stop_order_id) == (739.21, "new-sl")

    def test_working_out_a_repair_leaves_the_manager_unchanged(self):
        reconciler = _trailing_reconciler(739.21)
        reconciler.trailing_stop_manager.register_position("SPY", 657.6, 700.0, 14, "sl-old")

        reconciler.check_protective_stops(_broker(), _local(), NOW)

        state = reconciler.trailing_stop_manager.stop_states["SPY"]
        assert (state.current_stop, state.stop_order_id) == (700.0, "sl-old")
        assert "QQQ" not in reconciler.trailing_stop_manager.stop_states

    def test_a_new_position_is_not_registered_until_its_stop_is_live(self):
        reconciler = _trailing_reconciler(739.21)

        reconciler.check_protective_stops(_broker(), _local(), NOW)

        assert reconciler.trailing_stop_manager.stop_states == {}


class TestRaisingLiveStops:
    """The broker's stop is the truth; a raise is recorded only once the broker takes it."""

    def _cycle(self, om, local_stop):
        cycle = _cycle(om)
        cycle.reconciler = _trailing_reconciler(739.21)
        cycle.trailing_stop_manager = cycle.reconciler.trailing_stop_manager
        cycle.local_state_manager.state = _local(local_stop)
        return cycle

    def test_the_raise_is_computed_from_the_broker_stop_not_local_state(self):
        # local state says 739.21 (a refused raise was recorded); the broker has 624.72
        cycle = self._cycle(MagicMock(), local_stop=739.21)
        live = _broker(orders=[{**_stop_order(0), "stop_price": 624.72}])

        adjustments = cycle.calculate_stop_adjustments(live)

        assert [(a.current_stop, a.new_stop) for a in adjustments] == [(624.72, 739.21)]

    def test_a_refused_raise_is_not_recorded(self):
        om = MagicMock()
        om.modify_order.return_value = {
            "status": "error",
            "message": "cannot replace order in accepted status",
        }
        cycle = self._cycle(om, local_stop=624.72)
        live = _broker(orders=[{**_stop_order(0), "stop_price": 624.72}])

        cycle.batch_modify_orders(cycle.calculate_stop_adjustments(live))

        assert cycle.local_state_manager.state["positions"]["SPY"]["stop_price"] == 624.72
        assert cycle.trailing_stop_manager.stop_states["SPY"].current_stop == 624.72
        assert cycle.calculate_stop_adjustments(live)  # so the next run tries again

    def test_stop_limit_orders_and_every_stop_on_the_position_are_raised(self):
        om = MagicMock()
        cycle = self._cycle(om, local_stop=620.0)
        legs = [
            {**_stop_order(0, order_id="sl-a"), "stop_price": 620.0, "quantity": 7},
            {**_stop_order(0, order_id="sl-b"), "type": "stop_limit", "stop_price": 624.72},
        ]

        adjustments = cycle.calculate_stop_adjustments(_broker(orders=legs))

        assert [(a.order_id, a.current_stop, a.new_stop) for a in adjustments] == [
            ("sl-a", 620.0, 739.21),
            ("sl-b", 624.72, 739.21),
        ]

    def test_an_order_not_yet_replaceable_waits_for_a_later_run(self):
        cycle = self._cycle(MagicMock(), local_stop=624.72)
        accepted = {**_stop_order(0), "stop_price": 624.72, "status": "accepted"}

        adjustments = cycle.calculate_stop_adjustments(_broker(orders=[accepted]))

        assert adjustments == []  # Alpaca refuses to replace it yet

    def test_the_reason_describes_the_level_chosen(self):
        cycle = self._cycle(MagicMock(), local_stop=624.72)
        live = _broker(orders=[{**_stop_order(0), "stop_price": 624.72}])

        (adjustment,) = cycle.calculate_stop_adjustments(live)

        assert adjustment.reason == "Lock 73% of gains (17.0% profit)"  # entry 657.6, price 769.2

    def test_an_accepted_raise_is_recorded_with_the_new_order_id(self):
        om = MagicMock()
        om.modify_order.return_value = {"status": "submitted", "order_id": "sl-2"}
        cycle = self._cycle(om, local_stop=624.72)
        live = _broker(orders=[{**_stop_order(0), "stop_price": 624.72}])

        cycle.batch_modify_orders(cycle.calculate_stop_adjustments(live))

        om.modify_order.assert_called_once_with(order_id="sl-1", stop_price=739.21)
        assert cycle.local_state_manager.state["positions"]["SPY"]["stop_price"] == 739.21
        state = cycle.trailing_stop_manager.stop_states["SPY"]
        assert (state.current_stop, state.stop_order_id) == (739.21, "sl-2")


def _evening_cycle(monkeypatch, tmp_path, om, market_open=True):
    import src.trading.scheduling.trading_cycle as cycle_module
    from src.trading.utils.report_generator import ReportGenerator

    monkeypatch.setattr(cycle_module, "GTT_AVAILABLE", False)
    monkeypatch.setattr(cycle_module, "is_market_hours", lambda: market_open)
    cycle = _cycle(om)
    cycle.reconciler = _trailing_reconciler(739.21)
    cycle.trailing_stop_manager = cycle.reconciler.trailing_stop_manager
    cycle.local_state_manager.state = _local(624.72)
    cycle.report_generator = ReportGenerator()
    live = _broker(orders=[{**_stop_order(0), "stop_price": 624.72, "status": "new"}])
    cycle.fetch_broker_state = MagicMock(return_value=live)
    cycle.record_daily = MagicMock(return_value=[])
    cycle.check_position_alerts = MagicMock(return_value=[])
    cycle._get_report_file_path = lambda routine: str(tmp_path / f"{routine}.md")
    return cycle


class TestEveningStopRaise:
    """The day's gains are locked at 15:50 ET, not left to the next morning run."""

    def test_stops_are_raised_before_the_close(self, monkeypatch, tmp_path):
        om = MagicMock()
        om.modify_order.return_value = {"status": "submitted", "order_id": "sl-2"}
        cycle = _evening_cycle(monkeypatch, tmp_path, om)

        report = cycle.evening_routine(raise_errors=True)

        om.modify_order.assert_called_once_with(order_id="sl-1", stop_price=739.21)
        assert cycle.local_state_manager.state["positions"]["SPY"]["stop_price"] == 739.21
        assert "| SPY |" in report and "$739.21" in report
        assert "✅ 1 stops adjusted successfully" in report
        assert report.splitlines()[0].endswith(" ET")  # times in ET, as the file name

    def test_after_hours_the_raise_is_left_to_the_morning(self, monkeypatch, tmp_path):
        om = MagicMock()
        cycle = _evening_cycle(monkeypatch, tmp_path, om, market_open=False)

        cycle.evening_routine(raise_errors=True)

        om.modify_order.assert_not_called()  # thin after-hours prints don't set stops

    def test_a_failed_raise_is_reported_and_the_routine_completes(self, monkeypatch, tmp_path):
        cycle = _evening_cycle(monkeypatch, tmp_path, MagicMock())
        trail = cycle.reconciler.trailing_stop_manager
        trail.calculate_new_stop = MagicMock(side_effect=KeyError("current_price"))

        report = cycle.evening_routine(raise_errors=True)

        assert "Stop raise failed: 'current_price'" in report
        assert (tmp_path / "evening.md").exists()

    def test_stops_are_raised_from_state_fetched_after_gtt_orders(self):
        cycle = object.__new__(CostEfficientTradeCycle)
        before, after = {"positions": {}}, {"positions": {"SPY": {}}}
        cycle.fetch_broker_state = MagicMock(return_value=after)
        sold = SimpleNamespace(success=True, action_type="place_order")
        alert = SimpleNamespace(success=True, action_type="alert")
        skipped = SimpleNamespace(success=False, action_type="place_order")

        assert cycle._after_gtt_actions(before, [alert, skipped]) is before
        cycle.fetch_broker_state.assert_not_called()
        assert cycle._after_gtt_actions(before, [sold]) is after
        cycle.fetch_broker_state.assert_called_once_with(force_refresh=True)


def test_report_footer_describes_broker_synced_stops_and_no_target():
    from src.trading.utils.report_generator import ReportGenerator, RoutineType

    broker_state = _broker(orders=[{**_stop_order(0), "stop_price": 744.34}])
    broker_state["account"] = {}
    report = ReportGenerator().generate_routine_report(
        RoutineType.EVENING, broker_state, _local(744.34), [], [], []
    )

    assert "Alpaca hides" not in report and "calculated from entry" not in report
    assert "synced from the broker's open stop orders" in report
    spy_row = next(line for line in report.splitlines() if line.startswith("| SPY |"))
    assert "| none |" in spy_row  # trailing-stop exit, no take-profit order
