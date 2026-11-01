"""
A tool's words match what it does (#637): for someone at the CLI, and for a model choosing
a tool. The broker and data sources are mocked.
"""

import json
from unittest.mock import MagicMock, patch

import pandas as pd
import pytest

from config_defaults.trading_config import ClimbRate, TrailingStopConfig

from src.autogen_agents.agents.multi_timeframe_voter import MULTI_TIMEFRAME_PRESETS
from src.autogen_agents.tools.market_tools import market_data_result
from src.cli.tools import get_cli_tool, order_tools, portfolio_tools, trailing_stop_tools
from src.cli.tools.timeframe_tools import MULTI_TF_PRESETS
from src.cli.tools.user_alert_tools import create_price_alert
from src.market_data import market_context_tool, unified_market_tool
from src.trading.orders.trailing_stop_manager import TrailingStopManager

# Multi-timeframe presets


@pytest.mark.parametrize("name", sorted(MULTI_TIMEFRAME_PRESETS))
def test_a_preset_shows_the_weights_the_voter_runs(name):
    assert MULTI_TF_PRESETS[name]["timeframes"] == MULTI_TIMEFRAME_PRESETS[name]["timeframes"]
    assert MULTI_TF_PRESETS[name]["style"]


# Market data can't quietly be 2024, or quietly be nothing


def test_market_data_has_no_default_symbol_or_dates():
    with pytest.raises(TypeError):
        unified_market_tool.fetch_unified_market_data()
    with pytest.raises(TypeError):
        market_context_tool.fetch_market_context_data()


def test_the_model_must_give_a_symbol_and_dates():
    from src.autogen_agents.tools.market_tools import unified_market_tool as tool

    required = set(tool.parameters()["required"])

    assert {"symbol", "start_date", "end_date"} <= required


def context_with(monkeypatch, data_for):
    def fetch(symbol, *args, **kwargs):
        return data_for.get(symbol, pd.DataFrame())

    monkeypatch.setattr(market_context_tool, "fetch_unified_market_data", fetch)
    return market_context_tool.fetch_market_context_data("2026-10-09")


def test_no_data_at_all_is_unknown_not_neutral(monkeypatch):
    summary = context_with(monkeypatch, {})["market_summary"]

    assert summary["overall_sentiment"] == "UNKNOWN"
    assert summary["market_daily_avg"] is None
    assert "SPY" in summary["interpretation"]
    assert "QQQ" in summary["interpretation"]


def test_data_for_only_one_symbol_is_unknown(monkeypatch):
    bars = pd.DataFrame(
        {"close": [100.0 + i for i in range(7)], "volume": [1000] * 7},
        index=pd.date_range("2026-10-01", periods=7),
    )

    summary = context_with(monkeypatch, {"SPY": bars})["market_summary"]

    assert summary["overall_sentiment"] == "UNKNOWN"
    assert "QQQ" in summary["interpretation"]


def test_asking_for_one_symbol_only_needs_that_symbol(monkeypatch):
    bars = pd.DataFrame(
        {"close": [100.0 + i for i in range(7)], "volume": [1000] * 7},
        index=pd.date_range("2026-10-01", periods=7),
    )
    monkeypatch.setattr(market_context_tool, "fetch_unified_market_data", lambda *a, **k: bars)

    summary = market_context_tool.fetch_market_context_data("2026-10-07", symbols=["SPY"])

    assert summary["market_summary"]["overall_sentiment"] != "UNKNOWN"
    # The average is SPY's own, not halved by the QQQ that wasn't asked for
    assert summary["market_summary"]["market_daily_avg"] == pytest.approx(
        summary["market_context"]["SPY"]["daily_change_pct"], abs=0.01
    )


def test_a_tool_failure_is_unknown(monkeypatch):
    monkeypatch.setattr(
        market_context_tool, "_create_market_summary", MagicMock(side_effect=OSError)
    )

    summary = market_context_tool.fetch_market_context_data("2026-10-09")["market_summary"]

    assert summary["overall_sentiment"] == "UNKNOWN"


def test_an_empty_result_doesnt_read_as_a_flat_market():
    reply = json.loads(market_data_result(pd.DataFrame()))

    assert reply["data"] == []
    assert "data sources may be failing" in reply["message"]


# Portfolio tools


def test_the_portfolio_tools_read_the_broker():
    cycle = MagicMock()
    with (
        patch("src.trading.scheduling.trading_cycle.CostEfficientTradeCycle", return_value=cycle),
        patch.object(portfolio_tools, "show_portfolio", return_value="real view") as show,
    ):
        assert get_cli_tool("show_portfolio").func() == "real view"
        show.assert_called_with(cycle.account_monitor, cycle, specific_ticker=None)

        assert get_cli_tool("get_position").func("spy") == "real view"
        show.assert_called_with(cycle.account_monitor, cycle, specific_ticker="SPY")


def test_a_broker_failure_says_so_without_details():
    with patch(
        "src.trading.scheduling.trading_cycle.CostEfficientTradeCycle",
        side_effect=RuntimeError("secret detail"),
    ):
        reply = get_cli_tool("show_portfolio").func()

    assert "Could not read the portfolio" in reply
    assert "secret detail" not in reply


# User alerts


@pytest.mark.parametrize(
    "alert_type", ["gain", "pct_gain", "loss", "pct_loss", "stop", "stop_loss", "target"]
)
def test_an_alert_that_could_never_fire_is_refused(alert_type):
    manager = MagicMock()
    with patch("src.cli.tools.user_alert_tools._get_alerts_manager", return_value=manager):
        result = create_price_alert("AAPL", alert_type, 10.0)

    assert result["status"] == "error"
    assert "above or below" in result["message"]
    manager.create_alert.assert_not_called()


def test_alert_descriptions_say_no_routine_checks_them():
    assert "no routine checks" in get_cli_tool("create_price_alert").description


# Overrides and accounts


@pytest.mark.parametrize("name", ["set_portfolio_override", "get_portfolio_value"])
def test_override_descriptions_say_nothing_reads_them(name):
    assert "don't read" in get_cli_tool(name).description


def test_switch_account_says_orders_keep_their_account():
    tool = get_cli_tool("switch_account")

    assert "keep the account the CLI started with" in tool.description
    assert "all trading operations" not in tool.func.__doc__


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
