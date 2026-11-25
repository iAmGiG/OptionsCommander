"""
A protective stop must cover every share (E1-07).

check_protective_stops used to ask only whether a stop existed. An add-on bought from the
CLI, or a stop re-placed for a smaller count, left the extra shares with no stop and no
notice, and Alpaca holds the covered shares against the existing stop, so nothing could be
added by hand without noticing. Now the stop quantities are summed against the position:
the uncovered shares get a top-up stop at the existing stop's level, or go to review when
other sell orders hold them. Take-profit legs never count as coverage. No broker is called.
"""

from datetime import timedelta
from unittest.mock import MagicMock

import pytest

from tests.unit.trading.test_stop_renewal import (
    NOW,
    _broker,
    _cycle,
    _local,
    _reconciler,
    _stop_order,
    _trailing_reconciler,
)

from src.trading.state.state_reconciler import StopRepair


def stop(quantity, age_days=10, order_id="sl-1", stop_price=627.0):
    return {
        **_stop_order(age_days, order_id=order_id),
        "quantity": quantity,
        "stop_price": stop_price,
    }


def take_profit(quantity, order_id="tp-1"):
    return {
        "id": order_id,
        "type": "limit",
        "side": "sell",
        "quantity": quantity,
        "limit_price": 712.8,
        "time_in_force": "gtc",
        "created_at": (NOW - timedelta(days=5)).isoformat(),
    }


def market_sell(quantity, order_id="mk-1"):
    """A market sell on its way: a partial sale's, or the CLI's."""
    return {
        "id": order_id,
        "type": "market",
        "side": "sell",
        "quantity": quantity,
        "limit_price": None,
        "stop_price": None,
        "status": "new",
        "time_in_force": "day",
        "created_at": NOW.isoformat(),
    }


def check(orders, quantity=14, price=769.2, reconciler=None, raise_to_trailing=True, **position):
    broker = _broker(price=price, quantity=quantity, orders=orders)
    broker["positions"]["SPY"].update(position)
    return (reconciler or _reconciler()).check_protective_stops(
        broker, _local(), NOW, raise_to_trailing=raise_to_trailing
    )


class TestStopCoverage:
    def test_a_stop_on_fewer_shares_than_held_is_topped_up(self):
        review, repairs = check([stop(7)])

        assert review == []
        assert repairs == [
            StopRepair(
                kind="MISSING_STOP",
                symbol="SPY",
                quantity=7,
                stop_price=627.0,
                reason="the live stop covers 7 of 14 shares; a stop is added on the other 7",
            )
        ]

    def test_a_stop_on_every_share_needs_nothing(self):
        assert check([stop(14)]) == ([], [])

    def test_two_stops_that_add_up_need_nothing(self):
        assert check([stop(9), stop(5, order_id="sl-2")]) == ([], [])

    def test_two_short_stops_are_topped_up_at_the_higher_level(self):
        _, repairs = check([stop(5, stop_price=620.0), stop(5, order_id="sl-2", stop_price=627.0)])

        assert [(r.quantity, r.stop_price) for r in repairs] == [(4, 627.0)]

    def test_a_stop_on_more_shares_than_held_needs_nothing(self):
        # a stop placed before part of the position was sold another way
        assert check([stop(20)]) == ([], [])

    def test_a_take_profit_leg_is_not_coverage(self):
        review, repairs = check([stop(7), take_profit(7)])

        assert repairs == []
        assert [(d.type, d.action, d.severity) for d in review] == [
            ("MISSING_STOP", "NEEDS_HUMAN_REVIEW", "HIGH")
        ]
        assert review[0].details["quantity"] == 7
        assert review[0].details["covered"] == 7
        assert "held by open sell orders" in review[0].details["reason"]

    def test_a_take_profit_on_some_of_the_rest_leaves_the_free_shares_topped_up(self):
        review, repairs = check([stop(7), take_profit(3)])

        assert [(r.kind, r.quantity, r.stop_price) for r in repairs] == [("MISSING_STOP", 4, 627.0)]
        assert [(d.type, d.details["quantity"]) for d in review] == [("MISSING_STOP", 3)]

    def test_the_brokers_free_share_count_is_used_when_given(self):
        # A bracket's legs share their shares: the stop and the take-profit both say 7,
        # and Alpaca says 7 are free, so the sum of the sells would be wrong
        review, repairs = check([stop(7), take_profit(7)], qty_available=7)

        assert review == []
        assert [(r.quantity, r.stop_price) for r in repairs] == [(7, 627.0)]

    def test_no_free_shares_according_to_the_broker_goes_to_review(self):
        review, repairs = check([stop(7)], qty_available=0)

        assert repairs == []
        assert review[0].type == "MISSING_STOP"

    def test_a_stop_whose_quantity_is_unknown_is_not_judged(self):
        order = stop(7)
        del order["quantity"]

        assert check([order]) == ([], [])

    def test_a_stop_of_unknown_size_next_to_a_known_one_is_not_judged(self):
        # counted as 0, the unknown stop's shares would get a second stop the broker rejects
        unknown = stop(7, order_id="sl-2")
        del unknown["quantity"]

        assert check([stop(7), unknown]) == ([], [])

    def test_the_top_up_never_exceeds_the_shortfall(self):
        _, repairs = check([stop(7)], qty_available=14)

        assert [r.quantity for r in repairs] == [7]

    def test_more_free_shares_than_the_shortfall_are_flagged(self):
        # 20 held under a 14-share stop; the stop sold 4 (part-filled: Alpaca still lists
        # it at 14), so 16 are held, 10 under the stop and 6 free. The order's size says 2.
        review, repairs = check([stop(14)], quantity=16, qty_available=6)

        assert [r.quantity for r in repairs] == [2]
        assert [(d.type, d.details["quantity"]) for d in review] == [("MISSING_STOP", 4)]
        assert "partly filled" in review[0].details["reason"]

    def test_shares_an_open_market_sell_is_taking_need_no_stop(self):
        # right after a partial sale: the sale's market order is still open, the stop is
        # back on the shares that stay
        assert check([stop(5), market_sell(5)], quantity=10, qty_available=0) == ([], [])

    def test_a_market_sell_of_part_leaves_the_rest_to_cover(self):
        review, repairs = check([stop(7), market_sell(3)])

        assert review == []
        assert [r.quantity for r in repairs] == [4]

    @pytest.mark.parametrize("price", [600.0, 627.0])  # below the stop's level, at it
    def test_a_stop_at_or_above_the_price_is_left_for_review(self, price):
        review, repairs = check([stop(7)], price=price)

        assert repairs == []
        assert review[0].type == "MISSING_STOP"
        assert "below the current price" in review[0].details["reason"]

    def test_the_top_up_is_raised_to_the_trailing_level_in_the_evening(self):
        _, repairs = check([stop(7)], reconciler=_trailing_reconciler(739.21))

        assert repairs[0].stop_price == 739.21
        assert "trailing level" in repairs[0].reason

    def test_the_morning_tops_up_at_the_stops_own_level(self):
        _, repairs = check(
            [stop(7)], reconciler=_trailing_reconciler(739.21), raise_to_trailing=False
        )

        assert repairs[0].stop_price == 627.0
        assert "trailing level" not in repairs[0].reason

    def test_an_aging_short_stop_is_renewed_and_topped_up(self):
        _, repairs = check([stop(7, age_days=85)])

        assert [(r.kind, r.quantity, r.replaces_order_id) for r in repairs] == [
            ("STOP_EXPIRING", 7, "sl-1"),
            ("MISSING_STOP", 7, None),
        ]

    def test_short_positions_are_skipped(self):
        broker = _broker(orders=[stop(7)], side="short")

        assert _reconciler().check_protective_stops(broker, _local(), NOW) == ([], [])


class TestThroughTheCycle:
    def test_the_top_up_is_placed_as_a_gtc_sell_stop(self):
        om = MagicMock()
        om.place_stop_order.return_value = {"status": "submitted", "order_id": "sl-2"}
        cycle = _cycle(om)

        discrepancies = cycle.check_and_repair_stops(_broker(orders=[stop(7)]))

        om.place_stop_order.assert_called_once_with(
            symbol="SPY", qty=7, side="sell", stop_price=627.0, time_in_force="gtc"
        )
        assert [(d.type, d.action, d.details["quantity"]) for d in discrepancies] == [
            ("MISSING_STOP", "AUTO_FIXED", 7)
        ]

    def test_a_refused_top_up_fails_the_routines_stop_check(self):
        from src.trading.scheduling.trading_cycle import stop_failures

        om = MagicMock()
        om.place_stop_order.return_value = {"status": "error", "message": "insufficient qty"}
        cycle = _cycle(om)

        discrepancies = cycle.check_and_repair_stops(_broker(orders=[stop(7)]))

        assert [d.symbol for d in stop_failures(discrepancies)] == ["SPY"]

    @pytest.mark.parametrize("held", [7, 14])
    def test_a_covered_position_places_nothing(self, held):
        om = MagicMock()
        cycle = _cycle(om)

        discrepancies = cycle.check_and_repair_stops(_broker(quantity=held, orders=[stop(14)]))

        om.place_stop_order.assert_not_called()
        assert discrepancies == []
