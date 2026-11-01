"""
The typed trade path (#632): the order placed is the one shown and asked for. What reaches
execution is asserted, and everything is mocked: no test here sends an order.
"""

import asyncio
import json
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.cli.cli_session import CLISession
from src.cli.handlers import trade_handler as handler_module
from src.cli.utils.input_parser import detect_user_intent
from src.core.models import OrderResult, Signal, TradeDecision, TradeRequest, TradeSuggestion
from src.trading.state.local_state_manager import LocalStateManager

POSITION = {"qty": 14, "avg_entry_price": 657.6}


def _suggestion(signal, qty=10):
    return TradeSuggestion(
        signal=signal,
        confidence=0.7,
        entry_price=100.0,
        stop_loss=95.0,
        take_profit=110.0,
        reasoning=["why"],
        recommended_quantity=qty,
        portfolio_pct=5.0,
        max_loss_usd=50.0,
        risk_reward_ratio=2.0,
        warnings=[],
        ticker="AAPL",
    )


def run(
    monkeypatch, typed, signal, action, mode="confirm", position=None, answers=("yes",), plan=None
):
    """Run a typed request; returns the orchestrator and the prompts asked."""
    orchestrator = MagicMock()
    orchestrator.plan_close.return_value = plan
    orchestrator.close_position = AsyncMock(
        return_value=OrderResult(success=True, message="sold", ticker="AAPL", quantity=14)
    )
    decision = TradeDecision(suggestion=_suggestion(signal))
    decision.suggestion.current_price = 100.0
    orchestrator.process_request = AsyncMock(return_value=decision)
    orchestrator.parser.parse = AsyncMock(
        return_value=TradeRequest(ticker="AAPL", action=action, raw_input=typed)
    )
    orchestrator.execute_decision = AsyncMock(
        return_value=OrderResult(success=False, message="mocked")
    )
    monkeypatch.setattr(handler_module, "display_position_context", MagicMock())
    monkeypatch.setattr(handler_module, "display_result", MagicMock())

    prompts = []
    remaining = iter(answers)

    def fake_input(prompt=""):
        prompts.append(prompt)
        return next(remaining)

    monkeypatch.setattr("builtins.input", fake_input)
    handler = handler_module.TradeHandler(orchestrator, MagicMock(), None, mode)
    monkeypatch.setattr(handler, "_check_position_for_ticker", lambda ticker: position)
    asyncio.run(handler.handle_trade_request(typed))
    return orchestrator, prompts


def order_placed(orchestrator):
    """The side and quantity that reached execution, or None."""
    if not orchestrator.execute_decision.await_count:
        return None
    suggestion = orchestrator.execute_decision.await_args.args[0].suggestion
    return suggestion.signal.value.upper(), suggestion.recommended_quantity


# Intent words


@pytest.mark.parametrize(
    ("typed", "intent"),
    [
        ("buy some AAPL", "buy"),
        ("BUY 10 AAPL", "buy"),
        ("go long AAPL", "buy"),
        ("long AAPL", "buy"),
        ("purchase AAPL", "buy"),
        ("sell TSLA", "sell"),
        ("short NVDA", "sell"),
        ("I want to short NVDA", "sell"),
        ("I want to long NVDA", "buy"),
        ("closing my AAPL position", "sell"),
        ("get out of AAPL", "sell"),
        ("get me out of AAPL", "sell"),
        ("close AAPL", "sell"),
        ("exit AAPL", "sell"),
        # Questions and analysis state no intent
        ("should I get out of AAPL?", None),
        ("should I sell TSLA", None),
        ("how long should I hold AAPL", None),
        ("what about a buy here", None),
        ("analyze AAPL", None),
        ("buy AAPL?", None),
        # Substrings are not words
        ("AAPL belongs in my list", None),
        ("AAPL had a buyback", None),
        ("AAPL closed higher", None),
        ("AAPL shortly", None),
        # Not a request to trade
        ("get a quote for AAPL", None),
        ("AAPL has been up a long time", None),
        ("AAPL near the close", None),
        # Both sides named
        ("sell AAPL and buy TSLA", None),
    ],
)
def test_detect_user_intent(typed, intent):
    assert detect_user_intent(typed) == intent


# The order can't be the opposite of the request


@pytest.mark.parametrize("mode", ["confirm", "auto"])
def test_buy_more_with_a_sell_signal_doesnt_sell_the_position(monkeypatch, mode):
    orchestrator, prompts = run(
        monkeypatch, "buy more AAPL", Signal.SELL, "buy", mode, position=POSITION
    )

    assert order_placed(orchestrator) is None
    assert prompts == []


def test_matching_request_and_signal_places_that_order(monkeypatch, capsys):
    orchestrator, prompts = run(monkeypatch, "buy AAPL", Signal.BUY, "buy")

    assert order_placed(orchestrator) == ("BUY", 10)
    assert len(prompts) == 1
    out = capsys.readouterr().out
    assert "BUY (as requested)" in out
    assert "Order: BUY 10 AAPL" in out


def test_declining_the_prompt_places_nothing(monkeypatch):
    orchestrator, _ = run(monkeypatch, "buy AAPL", Signal.BUY, "buy", answers=("no",))

    assert order_placed(orchestrator) is None


def test_auto_says_which_order_it_is_placing(monkeypatch, capsys):
    orchestrator, prompts = run(monkeypatch, "buy AAPL", Signal.BUY, "buy", "auto")

    assert order_placed(orchestrator) == ("BUY", 10)
    assert prompts == []
    assert "BUY 10 AAPL" in capsys.readouterr().out


def test_buy_with_a_sell_signal_and_no_position_still_asks_to_override(monkeypatch):
    orchestrator, prompts = run(monkeypatch, "buy AAPL", Signal.SELL, "buy", answers=("yes", "yes"))

    assert "Do you still want to BUY AAPL" in prompts[0]
    assert order_placed(orchestrator) == ("BUY", 10)


def override(monkeypatch, capsys, answer):
    """Answer the prompt on a sale's suggestion: the result, the screen, the suggestion."""
    suggestion = _suggestion(Signal.SELL)
    suggestion.stop_loss, suggestion.take_profit = 105.0, 90.0
    shown = []

    def fake_input(prompt=""):
        shown.append(capsys.readouterr().out)
        return answer

    monkeypatch.setattr("builtins.input", fake_input)
    handler = handler_module.TradeHandler(MagicMock(), MagicMock(), None, "confirm")
    decision = TradeDecision(suggestion=suggestion)
    return asyncio.run(handler._handle_buy_override(decision)), shown, suggestion


def test_the_override_prompt_shows_the_buy_it_would_place(monkeypatch, capsys):
    decision, shown, _ = override(monkeypatch, capsys, "yes")

    assert "BUY (LONG)" in shown[0]
    assert "SELL (SHORT)" not in shown[0]
    assert "$95.00" in shown[0]  # the stop is below the price
    assert "$105.00" not in shown[0]
    assert decision.suggestion.signal == Signal.BUY
    assert (decision.suggestion.stop_loss, decision.suggestion.take_profit) == (95.0, 110.0)


def test_declining_the_override_changes_nothing_and_offers_no_short(monkeypatch, capsys):
    decision, _, suggestion = override(monkeypatch, capsys, "no")

    assert decision is None
    assert suggestion.signal == Signal.SELL
    assert suggestion.stop_loss == 105.0
    assert "short AAPL" not in capsys.readouterr().out


# Selling a position: the user's own exit


PLAN = {
    "ticker": "AAPL",
    "quantity": 14,
    "held": 14,
    "market_open": True,
    "orders_to_cancel": [{"id": "s1", "order_type": "stop", "stop_price": 95.0}],
}


@pytest.mark.parametrize("signal", [Signal.BUY, Signal.HOLD, Signal.SELL])
@pytest.mark.parametrize("mode", ["confirm", "auto"])
def test_sell_with_a_position_sells_whatever_the_signal(monkeypatch, signal, mode):
    orchestrator, prompts = run(monkeypatch, "sell AAPL", signal, "sell", mode, plan=PLAN)

    assert len(prompts) == 1  # in AUTO too
    orchestrator.close_position.assert_awaited_once_with(PLAN)


def test_a_sale_needs_no_analysis(monkeypatch):
    """An exit can't depend on market data or an indicator being available."""
    orchestrator, _ = run(monkeypatch, "sell AAPL", Signal.BUY, "sell", plan=PLAN)

    orchestrator.process_request.assert_not_awaited()


@pytest.mark.parametrize("answer", ["no", "", "y", "yes please"])
def test_only_yes_sells(monkeypatch, capsys, answer):
    orchestrator, _ = run(
        monkeypatch, "sell AAPL", Signal.BUY, "sell", plan=PLAN, answers=(answer,)
    )

    orchestrator.close_position.assert_not_awaited()
    assert "Nothing sold" in capsys.readouterr().out


def test_the_prompt_says_what_the_sale_does(monkeypatch, capsys):
    plan = {**PLAN, "quantity": 5, "market_open": False}
    run(monkeypatch, "sell 5 AAPL", Signal.BUY, "sell", plan=plan, answers=("no",))

    out = capsys.readouterr().out
    assert "SELL 5 of your 14 AAPL at market" in out
    assert "cancels its open stop order @ $95.00" in out
    assert "market is closed" in out


def test_no_cancel_warning_when_nothing_holds_the_shares(monkeypatch, capsys):
    plan = {**PLAN, "orders_to_cancel": []}
    run(monkeypatch, "sell AAPL", Signal.BUY, "sell", plan=plan, answers=("no",))

    out = capsys.readouterr().out
    assert "cancels" not in out
    assert "market is closed" not in out


def test_the_requested_quantity_reaches_the_plan(monkeypatch):
    orchestrator = MagicMock()
    orchestrator.plan_close.return_value = None
    handler = handler_module.TradeHandler(orchestrator, MagicMock(), None, "confirm")

    request = TradeRequest(ticker="AAPL", action="sell", quantity=5)
    asyncio.run(handler.handle_trade_request("sell 5 AAPL", request))

    orchestrator.plan_close.assert_called_once_with("AAPL", 5)


@pytest.mark.parametrize("signal", [Signal.BUY, Signal.SELL, Signal.HOLD])
def test_sell_with_no_position_cannot_sell(monkeypatch, capsys, signal):
    orchestrator, prompts = run(monkeypatch, "sell AAPL", signal, "sell")

    assert prompts == []
    orchestrator.close_position.assert_not_awaited()
    assert "Cannot SELL" in capsys.readouterr().out


def test_a_parsed_sell_counts_without_the_word(monkeypatch):
    orchestrator, _ = run(monkeypatch, "I am done with AAPL", Signal.BUY, "sell", plan=PLAN)

    orchestrator.close_position.assert_awaited_once()


@pytest.mark.parametrize("action", ["review", "sell"])
def test_a_sell_question_is_not_a_sale(monkeypatch, action):
    """Whatever the model makes of it."""
    orchestrator, prompts = run(monkeypatch, "should I sell AAPL?", Signal.BUY, action, plan=PLAN)

    orchestrator.close_position.assert_not_awaited()
    assert prompts == []


def test_a_buy_question_is_not_a_buy(monkeypatch):
    orchestrator, prompts = run(monkeypatch, "should I buy AAPL?", Signal.BUY, "buy", "auto")

    assert order_placed(orchestrator) is None
    assert prompts == []


@pytest.mark.parametrize("typed", ["short AAPL", "I want to short AAPL", "go short AAPL"])
def test_a_short_request_doesnt_sell_the_long_position(monkeypatch, capsys, typed):
    orchestrator, prompts = run(monkeypatch, typed, Signal.SELL, "sell", plan=PLAN)

    orchestrator.close_position.assert_not_awaited()
    assert prompts == []
    assert "short selling is not supported" in capsys.readouterr().out


def test_a_sale_with_no_symbol_asks_which(monkeypatch, capsys):
    orchestrator = MagicMock()
    handler = handler_module.TradeHandler(orchestrator, MagicMock(), None, "confirm")

    request = TradeRequest(ticker="", action="sell")
    asyncio.run(handler.handle_trade_request("sell it", request))

    orchestrator.plan_close.assert_not_called()
    assert "Which symbol" in capsys.readouterr().out


def test_a_failed_sale_leaves_the_local_state_alone(monkeypatch, capsys):
    orchestrator = MagicMock()
    orchestrator.plan_close.return_value = PLAN
    orchestrator.close_position = AsyncMock(
        return_value=OrderResult(success=False, message="refused", ticker="AAPL")
    )
    monkeypatch.setattr("builtins.input", lambda prompt="": "yes")
    monkeypatch.setattr(handler_module, "display_result", MagicMock())
    cycle = MagicMock()
    handler = handler_module.TradeHandler(orchestrator, MagicMock(), cycle, "confirm")

    request = TradeRequest(ticker="AAPL", action="sell")
    asyncio.run(handler.handle_trade_request("sell AAPL", request))

    cycle.save_local_state.assert_not_called()


# A HOLD signal


@pytest.mark.parametrize("mode", ["confirm", "auto"])
def test_a_question_doesnt_turn_hold_into_a_trade(monkeypatch, mode):
    orchestrator, prompts = run(
        monkeypatch, "should I get out of AAPL?", Signal.HOLD, "review", mode
    )

    assert order_placed(orchestrator) is None
    assert prompts == []


@pytest.mark.parametrize("mode", ["confirm", "auto"])
def test_hold_asks_before_becoming_a_trade(monkeypatch, mode):
    orchestrator, prompts = run(monkeypatch, "buy AAPL", Signal.HOLD, "buy", mode, answers=("no",))

    assert "Prepare a BUY order anyway" in prompts[0]
    assert order_placed(orchestrator) is None


def test_hold_becomes_the_requested_trade_once_agreed(monkeypatch):
    orchestrator, prompts = run(monkeypatch, "buy AAPL", Signal.HOLD, "buy", answers=("yes", "yes"))

    assert len(prompts) == 2
    side, _quantity = order_placed(orchestrator)
    assert side == "BUY"


def test_a_review_with_a_buy_word_doesnt_flip_hold(monkeypatch):
    orchestrator, prompts = run(monkeypatch, "buy AAPL", Signal.HOLD, "review")

    assert order_placed(orchestrator) is None
    assert prompts == []


def test_a_review_only_shows_the_analysis_whatever_the_signal(monkeypatch, capsys):
    orchestrator, prompts = run(monkeypatch, "review the buy case for AAPL", Signal.SELL, "review")

    assert order_placed(orchestrator) is None
    assert prompts == []
    assert "No trade execution requested" in capsys.readouterr().out


# One parse decides


@pytest.mark.parametrize("action", ["review", "analyze", None])
@pytest.mark.parametrize("mode", ["confirm", "auto"])
def test_anything_but_buy_or_sell_never_executes(monkeypatch, capsys, action, mode):
    orchestrator, prompts = run(monkeypatch, "AAPL", Signal.BUY, action, mode)

    assert order_placed(orchestrator) is None
    assert prompts == []
    assert "No trade execution requested" in capsys.readouterr().out


def test_the_handler_parses_once_and_shares_the_result(monkeypatch):
    orchestrator, _ = run(monkeypatch, "buy AAPL", Signal.BUY, "buy")

    orchestrator.parser.parse.assert_awaited_once()
    request = orchestrator.process_request.await_args.kwargs["request"]
    assert request.action == "buy"


def test_a_request_already_parsed_isnt_parsed_again(monkeypatch):
    orchestrator = MagicMock()
    decision = TradeDecision(suggestion=_suggestion(Signal.BUY))
    orchestrator.process_request = AsyncMock(return_value=decision)
    orchestrator.parser.parse = AsyncMock()
    orchestrator.execute_decision = AsyncMock(return_value=OrderResult(success=False, message="x"))
    monkeypatch.setattr(handler_module, "display_position_context", MagicMock())
    monkeypatch.setattr(handler_module, "display_result", MagicMock())
    monkeypatch.setattr("builtins.input", lambda prompt="": "yes")
    handler = handler_module.TradeHandler(orchestrator, MagicMock(), None, "confirm")
    monkeypatch.setattr(handler, "_check_position_for_ticker", lambda ticker: None)

    request = TradeRequest(ticker="AAPL", action="buy")
    asyncio.run(handler.handle_trade_request("buy AAPL", request))

    orchestrator.parser.parse.assert_not_awaited()
    assert order_placed(orchestrator) == ("BUY", 10)


def test_the_session_passes_its_parse_to_the_handler():
    session = CLISession.__new__(CLISession)
    request = TradeRequest(ticker="AAPL", action="buy", request_type="trade")
    session.orchestrator = MagicMock()
    session.orchestrator.parser.parse = AsyncMock(return_value=request)
    session.user_id = "cli_user"
    session.autonomy_mode = "confirm"
    session.trade_handler = MagicMock()
    session.trade_handler.handle_trade_request = AsyncMock()

    asyncio.run(session._process_request("I would buy some AAPL here"))

    session.orchestrator.parser.parse.assert_awaited_once()
    session.trade_handler.handle_trade_request.assert_awaited_once_with(
        "I would buy some AAPL here", request
    )


def test_a_parse_failure_places_nothing(monkeypatch, capsys):
    orchestrator = MagicMock()
    orchestrator.parser.parse = AsyncMock(side_effect=ValueError("Could not parse input"))
    orchestrator.process_request = AsyncMock()
    orchestrator.execute_decision = AsyncMock()
    handler = handler_module.TradeHandler(orchestrator, MagicMock(), None, "auto")

    asyncio.run(handler.handle_trade_request("buy AAPL"))

    orchestrator.process_request.assert_not_awaited()
    orchestrator.execute_decision.assert_not_awaited()


# The saved state after a trade


@pytest.fixture
def cycle(tmp_path):
    """A trade cycle with a real state file, loaded when the session started."""
    state_file = tmp_path / "state" / "positions.json"
    state_file.parent.mkdir()
    state_file.write_text(json.dumps({"positions": {}}))
    manager = LocalStateManager(str(state_file))
    fake = MagicMock()
    fake.state_file = state_file
    fake.manager = manager
    fake.load_local_state = manager.reload
    fake.save_local_state = manager.save
    type(fake).local_state = property(lambda self: manager.state)
    return fake


def routine_saves(cycle, positions):
    """A routine in another process writes the file after this session loaded it."""
    cycle.state_file.write_text(json.dumps({"positions": positions}))


def saved_positions(cycle):
    return json.loads(cycle.state_file.read_text())["positions"]


def traded(cycle, signal, qty=10):
    handler = handler_module.TradeHandler(MagicMock(), MagicMock(), cycle, "confirm")
    decision = TradeDecision(suggestion=_suggestion(signal, qty))
    result = OrderResult(success=True, message="ok", quantity=qty, entry_order_id="o1")
    handler._update_local_state_after_trade(decision, result)


def test_a_cli_trade_keeps_what_a_routine_saved_since_startup(cycle):
    routine_saves(cycle, {"SPY": {"quantity": 14, "stop_price": 744.47}})

    traded(cycle, Signal.BUY)

    saved = saved_positions(cycle)
    assert saved["SPY"]["stop_price"] == 744.47
    assert saved["AAPL"]["quantity"] == 10


def sold(cycle, qty):
    handler = handler_module.TradeHandler(MagicMock(), MagicMock(), cycle, "confirm")
    handler._record_sale("AAPL", qty)


def test_a_sale_of_something_not_recorded_adds_nothing(cycle):
    sold(cycle, 10)

    assert saved_positions(cycle) == {}


def test_a_sale_keeps_what_a_routine_saved_since_startup(cycle):
    routine_saves(cycle, {"SPY": {"quantity": 14, "stop_price": 744.47}, "AAPL": {"quantity": 10}})

    sold(cycle, 10)

    assert saved_positions(cycle) == {"SPY": {"quantity": 14, "stop_price": 744.47}}


def test_selling_part_keeps_the_rest(cycle):
    routine_saves(cycle, {"AAPL": {"quantity": 14, "stop_price": 90.0}})

    sold(cycle, 4)

    assert saved_positions(cycle)["AAPL"] == {"quantity": 10, "stop_price": 90.0}
