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


def run(monkeypatch, typed, signal, action, mode="confirm", position=None, answers=("yes",)):
    """Run a typed request; returns the orchestrator and the prompts asked."""
    orchestrator = MagicMock()
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
def test_sell_with_a_buy_signal_places_no_buy(monkeypatch, capsys, mode):
    orchestrator, prompts = run(monkeypatch, "sell AAPL", Signal.BUY, "sell", mode)

    assert order_placed(orchestrator) is None
    assert prompts == []
    out = capsys.readouterr().out
    assert "NO ORDER PLACED" in out
    assert "as requested" not in out


@pytest.mark.parametrize("mode", ["confirm", "auto"])
def test_buy_more_with_a_sell_signal_doesnt_sell_the_position(monkeypatch, mode):
    orchestrator, prompts = run(
        monkeypatch, "buy more AAPL", Signal.SELL, "buy", mode, position=POSITION
    )

    assert order_placed(orchestrator) is None
    assert prompts == []


def test_a_parsed_sell_counts_as_a_request_without_the_word(monkeypatch):
    orchestrator, _ = run(monkeypatch, "I am done with AAPL", Signal.BUY, "sell")

    assert order_placed(orchestrator) is None


def test_matching_request_and_signal_places_that_order(monkeypatch, capsys):
    orchestrator, prompts = run(monkeypatch, "buy AAPL", Signal.BUY, "buy")

    assert order_placed(orchestrator) == ("BUY", 10)
    assert len(prompts) == 1
    out = capsys.readouterr().out
    assert "BUY (as requested)" in out
    assert "Order: BUY 10 AAPL" in out


def test_sell_with_a_sell_signal_and_a_position_sells(monkeypatch):
    orchestrator, _ = run(monkeypatch, "sell AAPL", Signal.SELL, "sell", position=POSITION)

    assert order_placed(orchestrator) == ("SELL", 10)


def test_declining_the_prompt_places_nothing(monkeypatch):
    orchestrator, _ = run(monkeypatch, "buy AAPL", Signal.BUY, "buy", answers=("no",))

    assert order_placed(orchestrator) is None


def test_auto_says_which_order_it_is_placing(monkeypatch, capsys):
    orchestrator, prompts = run(monkeypatch, "buy AAPL", Signal.BUY, "buy", "auto")

    assert order_placed(orchestrator) == ("BUY", 10)
    assert prompts == []
    assert "BUY 10 AAPL" in capsys.readouterr().out


def test_sell_with_no_position_still_cannot_sell(monkeypatch):
    orchestrator, prompts = run(monkeypatch, "sell AAPL", Signal.SELL, "sell")

    assert order_placed(orchestrator) is None
    assert prompts == []


def test_buy_with_a_sell_signal_and_no_position_still_asks_to_override(monkeypatch):
    orchestrator, prompts = run(monkeypatch, "buy AAPL", Signal.SELL, "buy", answers=("yes", "yes"))

    assert "Do you still want to BUY AAPL" in prompts[0]
    assert order_placed(orchestrator) == ("BUY", 10)


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


def test_a_sale_is_not_recorded_as_a_position(cycle):
    traded(cycle, Signal.SELL)

    assert "AAPL" not in saved_positions(cycle)


def test_selling_everything_removes_the_position(cycle):
    routine_saves(cycle, {"AAPL": {"quantity": 10}})

    traded(cycle, Signal.SELL)

    assert saved_positions(cycle) == {}


def test_selling_part_keeps_the_rest(cycle):
    routine_saves(cycle, {"AAPL": {"quantity": 14, "stop_price": 90.0}})

    traded(cycle, Signal.SELL, qty=4)

    assert saved_positions(cycle)["AAPL"] == {"quantity": 10, "stop_price": 90.0}
