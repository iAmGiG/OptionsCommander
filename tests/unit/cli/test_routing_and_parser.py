"""
Typed requests reach the right handler (#636): a word in a sentence isn't a command. And the
parser reports what actually went wrong, with a fake model server standing in for OpenAI.
"""

import asyncio
from unittest.mock import AsyncMock, MagicMock

import httpx2
import openai
import pytest
from agent_framework.openai import RawOpenAIChatCompletionClient

from src.cli.cli_session import CLISession
from src.cli.utils.error_utils import sanitize_error_message
from src.cli.utils.input_parser import is_mode_request, is_scheduler_request
from src.core.trading_orchestrator import TradingOrchestrator
from src.parsers import LLMParser, llm_parser


@pytest.mark.parametrize(
    "typed",
    [
        "scheduler",
        "show schedule",
        "what's the schedule",
        "morning routine",
        "evening routines",
        "when is the morning report",
        "show evening summary",
        "morning",
        "evening",
    ],
)
def test_scheduler_commands(typed):
    assert is_scheduler_request(typed)


@pytest.mark.parametrize(
    "typed",
    [
        "buy 10 AAPL this morning",
        "sell AAPL this evening",
        "buy AAPL on schedule",
        "good morning",
        "execution",
        "how did AAPL do this morning",
        "unscheduled",
    ],
)
def test_a_word_in_a_sentence_is_not_a_scheduler_command(typed):
    assert not is_scheduler_request(typed)


@pytest.mark.parametrize(
    "typed",
    [
        "show mode",
        "current mode",
        "set mode aggressive",
        "change mode to moderate",
        "set risk mode to conservative",
        "trading mode",
        "use conservative mode",
        "use conservative",
        "what mode am I in",
        "which mode",
        "switch mode",
        "mode aggressive",
        "aggressive",
        " Moderate ",
    ],
)
def test_mode_commands(typed):
    assert is_mode_request(typed)


@pytest.mark.parametrize(
    "typed",
    [
        "buy SPY aggressively",
        "I want to be conservative with this AAPL trade because of earnings",
        "moderate gains in AAPL",
    ],
)
def test_a_sentence_using_a_mode_name_is_not_a_mode_command(typed):
    assert not is_mode_request(typed)


def routed(typed):
    """The handler a typed request goes to."""
    session = CLISession.__new__(CLISession)
    handlers = (
        "_handle_scheduler_request",
        "_handle_alerts_request",
        "_handle_cancel_request",
        "_handle_account_request",
        "_handle_timeframe_request",
        "_handle_trading_mode_request",
        "_handle_trade_or_status_request",
    )
    for name in handlers:
        setattr(session, name, AsyncMock())
    asyncio.run(session._process_request(typed))
    called = [name for name in handlers if getattr(session, name).await_count]
    assert len(called) == 1
    return called[0]


@pytest.mark.parametrize(
    ("typed", "handler"),
    [
        ("buy 10 AAPL this morning", "_handle_trade_or_status_request"),
        ("buy SPY aggressively", "_handle_trade_or_status_request"),
        ("execution mode", "_handle_trade_or_status_request"),
        ("how does SPY look?", "_handle_trade_or_status_request"),
        ("show schedule", "_handle_scheduler_request"),
        ("set mode moderate", "_handle_trading_mode_request"),
        ("cancel all orders", "_handle_cancel_request"),
    ],
)
def test_routing(typed, handler):
    assert routed(typed) == handler


def mode_change(monkeypatch, typed):
    """Run the mode handler; returns the mode it set, or None."""
    from src.cli import cli_session as module

    changed = MagicMock(return_value="ok")
    monkeypatch.setattr(module, "set_mode", changed)
    monkeypatch.setattr(module, "show_current_mode", lambda: "current")
    session = CLISession.__new__(CLISession)
    asyncio.run(session._handle_trading_mode_request(typed))
    return changed.call_args.args[0] if changed.called else None


def test_because_doesnt_change_the_saved_mode(monkeypatch):
    """Only reachable through a mode phrase now, but "use" must still be a word."""
    typed = "trading mode conservative because of earnings"
    assert mode_change(monkeypatch, typed) is None


@pytest.mark.parametrize(
    ("typed", "mode"),
    [
        ("set mode aggressive", "aggressive"),
        ("use moderate mode", "moderate"),
        ("moderate", "moderate"),
    ],
)
def test_a_mode_command_sets_the_mode(monkeypatch, typed, mode):
    assert mode_change(monkeypatch, typed) == mode


def test_the_execution_mode_tools_are_gone():
    from src.cli import tools

    names = {tool.name for tool in tools.get_all_cli_tools()}
    assert not names & {"show_execution_mode", "get_execution_mode", "set_execution_mode"}


# The parser


def parser_with(handler):
    requests = []

    def recording(request):
        requests.append(request)
        return handler(request)

    async_client = openai.AsyncOpenAI(
        api_key="sk-test",
        max_retries=0,
        http_client=httpx2.AsyncClient(transport=httpx2.MockTransport(recording)),
    )
    client = RawOpenAIChatCompletionClient(model="gpt-4o-mini", async_client=async_client)
    return LLMParser(model_client=client), requests


def prose(request):
    return httpx2.Response(
        200,
        json={
            "id": "x",
            "object": "chat.completion",
            "created": 0,
            "model": "gpt-4o-mini",
            "choices": [
                {
                    "index": 0,
                    "finish_reason": "stop",
                    "message": {"role": "assistant", "content": "Which ticker?"},
                }
            ],
        },
    )


def fails_with(status):
    return lambda request: httpx2.Response(status, json={"error": {"message": "nope"}})


def timing_out(request):
    raise httpx2.ReadTimeout("timed out", request=request)


def connection_lost(request):
    raise httpx2.ConnectError("refused", request=request)


def parse_error(handler, typed="buy AAPL"):
    parser, _ = parser_with(handler)
    with pytest.raises(ValueError) as caught:
        asyncio.run(parser.parse(typed))
    return str(caught.value)


@pytest.mark.parametrize(
    ("handler", "message"),
    [
        (fails_with(429), "Model service unavailable"),
        (fails_with(500), "Model service unavailable"),
        (timing_out, "Model service unavailable"),
        (connection_lost, "Model service unavailable"),
        (fails_with(401), "Configuration error"),
        (fails_with(403), "Configuration error"),
        (prose, "Could not parse input"),
    ],
)
def test_each_failure_says_what_went_wrong(handler, message):
    assert parse_error(handler) == message


def test_input_containing_401_is_not_a_configuration_error():
    assert parse_error(prose, "buy 401 AAPL") == "Could not parse input"


def test_the_tool_call_is_forced():
    import json

    parser, requests = parser_with(prose)
    with pytest.raises(ValueError):
        asyncio.run(parser.parse("buy AAPL"))

    body = json.loads(requests[0].content)
    assert body["tool_choice"] == {"type": "function", "function": {"name": "parse_trade_request"}}


def test_the_input_reaches_the_model_unchanged():
    import json

    parser, requests = parser_with(prose)
    with pytest.raises(ValueError):
        asyncio.run(parser.parse("is spy at 60 good"))

    assert "is spy at 60 good" in json.dumps(json.loads(requests[0].content))


def test_the_default_client_has_a_timeout(monkeypatch):
    built = {}

    class FakeClient:
        def __init__(self, **kwargs):
            built.update(kwargs)

    monkeypatch.setattr(llm_parser, "RawOpenAIChatCompletionClient", FakeClient)

    LLMParser(api_key="sk-test")

    assert built["async_client"].timeout == llm_parser.PARSE_TIMEOUT_SECONDS
    assert built["async_client"].max_retries == llm_parser.PARSE_MAX_RETRIES


def test_the_prompt_keeps_a_named_ticker_in_a_question():
    prompt = LLMParser(model_client=MagicMock())._build_prompt("how does SPY look?")

    assert "The symbol if the input names one" in prompt
    assert "how does SPY look?" in prompt.split("status_query")[0]


@pytest.mark.parametrize(
    ("error", "shown"),
    [
        ("Model service unavailable", "isn't responding"),
        ("Configuration error", "Configuration error. Nothing done."),
        ("Could not parse input", "Didn't understand that"),
        ("No symbol given", "Which symbol?"),
    ],
)
def test_what_the_user_sees(error, shown):
    assert shown in sanitize_error_message(ValueError(error))


# No symbol


def test_no_symbol_stops_before_any_data_is_fetched():
    parser = MagicMock()
    parser.validate = AsyncMock(return_value=True)
    analyzer = MagicMock()
    analyzer.analyze = AsyncMock()
    orchestrator = TradingOrchestrator.__new__(TradingOrchestrator)
    orchestrator.parser = parser
    orchestrator.analyzer = analyzer

    request = MagicMock(ticker="", request_type="status_query")
    with pytest.raises(ValueError, match="No symbol given"):
        asyncio.run(orchestrator.process_request("hello there", request=request))

    analyzer.analyze.assert_not_awaited()
