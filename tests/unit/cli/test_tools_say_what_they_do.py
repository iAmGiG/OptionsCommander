"""
A tool's words match what it does (#637): for someone at the CLI, and for a model choosing
a tool. The broker and data sources are mocked.
"""

from unittest.mock import MagicMock, patch

import pytest

from config_defaults.trading_config import ClimbRate, TrailingStopConfig

from src.cli.tools import order_tools, trailing_stop_tools
from src.cli.tools.timeframe_tools import MULTI_TF_PRESETS
from src.market_data import unified_market_tool
from src.trading.instruments.multi_timeframe_voter import MULTI_TIMEFRAME_PRESETS
from src.trading.orders.trailing_stop_manager import TrailingStopManager

# Multi-timeframe presets


@pytest.mark.parametrize("name", sorted(MULTI_TIMEFRAME_PRESETS))
def test_a_preset_shows_the_weights_the_voter_runs(name):
    assert MULTI_TF_PRESETS[name]["timeframes"] == MULTI_TIMEFRAME_PRESETS[name]["timeframes"]
    assert MULTI_TF_PRESETS[name]["style"]


# Market data can't quietly be 2024


def test_market_data_has_no_default_symbol_or_dates():
    with pytest.raises(TypeError):
        unified_market_tool.fetch_unified_market_data()


# Climb rate


@pytest.mark.parametrize("rate", ["slow", "medium", "fast"])
def test_the_explanation_matches_the_live_tiers(rate):
    text = trailing_stop_tools.explain_climb_rate(rate)
    locks = ClimbRate.get_gain_locks(rate)

    assert "+2-4%:  Move the stop to breakeven" in text
    assert f"+4-6%:  Lock {locks[1] * 100:.0f}%" in text
    assert f"+6-10%: Lock {locks[2] * 100:.0f}%" in text
    assert f"+10%+:  Lock {locks[3] * 100:.0f}%" in text


@pytest.mark.parametrize(
    ("price", "text"),
    [
        (103.0, "+2-4%"),  # breakeven
        (105.0, "+4-6%"),
        (108.0, "+6-10%"),
        (112.0, "+10%+"),
    ],
)
def test_the_tier_named_is_the_one_the_manager_applies(price, text):
    """Against the real manager at medium, from a $100 entry."""
    config = MagicMock()
    trailing = trailing_stop_tools.TradingConfig().get_trailing_stop_config()
    config.get_trailing_stop_config.return_value = trailing
    manager = TrailingStopManager(order_manager=None, config=config)
    manager.register_position("X", 100.0, 95.0, 1)
    locks = ClimbRate.get_gain_locks(trailing.climb_rate)
    expected = {"+2-4%": 100.0, "+4-6%": 100 + 5 * locks[1], "+6-10%": 100 + 8 * locks[2]}
    expected["+10%+"] = 100 + 12 * locks[3]

    stop = manager.calculate_new_stop("X", price)

    assert stop == pytest.approx(expected[text], abs=0.5)
    comparison = trailing_stop_tools.compare_climb_rates()
    assert text in comparison
    assert "breakeven" in comparison
    assert "Zone" not in comparison


def test_the_config_shown_is_the_one_the_routines_read():
    live = TrailingStopConfig(climb_rate="fast")
    with patch.object(trailing_stop_tools, "TradingConfig") as config:
        config.return_value.get_trailing_stop_config.return_value = live
        text = trailing_stop_tools.show_trailing_stop_config()

    assert "Climb Rate: fast" in text


# Orders


def test_a_protective_stop_isnt_labelled_short():
    orders = {
        "status": "success",
        "count": 1,
        "by_symbol": {
            "SPY": [{"side": "sell", "type": "stop", "qty": 14.0, "price": 744.47, "id": "x"}]
        },
    }
    with patch.object(order_tools, "list_orders", return_value=orders):
        text = order_tools.show_orders()

    assert "SHORT" not in text
    assert "$SPY (1 orders)" in text
