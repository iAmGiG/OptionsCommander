"""
Risk blocks are enforced: a trade the risk check refuses can't be confirmed or executed.

Covers SimpleRiskManager (real positions, exits never blocked, fail closed), the
orchestrator (refusal at execution, re-check of the final trade) and the CLI handler
(no confirmation prompt for a refused trade).
"""

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

import src.trading.risk.simple_risk_manager as risk_module
from src.cli.handlers import trade_handler as handler_module
from src.core.models import (
    AnalysisResult,
    RiskAssessment,
    Signal,
    TradeDecision,
    TradeRequest,
    TradeSuggestion,
)
from src.core.trading_orchestrator import TradingOrchestrator


class _DefaultsConfig:
    """Stands in for ConfigLoader so no config/config.json is needed."""

    def get(self, key, default=None):
        return default


class _Account:
    def __init__(self, portfolio=100_000.0, buying_power=100_000.0, positions=(), error=None):
        self.portfolio = portfolio
        self.buying_power = buying_power
        self.positions = list(positions)
        self.error = error

    async def get_portfolio_value(self, user_id="default"):
        if self.error:
            raise self.error
        return self.portfolio

    async def get_buying_power(self, user_id="default"):
        return self.buying_power

    async def get_positions(self, user_id="default"):
        return self.positions


def _position(symbol, qty, market_value):
    return {"symbol": symbol, "qty": qty, "market_value": market_value}


@pytest.fixture
def risk_manager(monkeypatch):
    monkeypatch.setattr(risk_module, "ConfigLoader", _DefaultsConfig)

    def build(**account):
        return risk_module.SimpleRiskManager(account_service=_Account(**account))

    return build


def _assess(manager, signal, qty, ticker="SPY", price=660.0):
    request = TradeRequest(ticker=ticker, action=signal.value, quantity=qty)
    analysis = AnalysisResult(
        signal=signal,
        confidence=0.7,
        entry_price=price,
        stop_loss=price * 0.95,
        take_profit=price * 1.08,
    )
    return asyncio.run(manager.assess(request, analysis))


class TestSimpleRiskManager:
    def test_buy_beyond_buying_power_is_blocked(self, risk_manager):
        assessment = _assess(risk_manager(buying_power=10_000.0), Signal.BUY, 50)

        assert not assessment.approved
        assert any("Insufficient buying power" in r for r in assessment.block_reasons)

    def test_selling_held_shares_is_never_blocked(self, risk_manager):
        manager = risk_manager(buying_power=0.0, positions=[_position("SPY", 50, 33_000.0)])

        assessment = _assess(manager, Signal.SELL, 50)

        assert assessment.approved
        assert assessment.block_reasons == []
        assert assessment.existing_position_qty == 50

    def test_exposure_counts_open_positions(self, risk_manager):
        held = [_position("QQQ", 100, 40_000.0), _position("IWM", 150, 35_000.0)]

        assessment = _assess(risk_manager(positions=held), Signal.BUY, 10)  # +6.6%

        assert not assessment.approved
        assert any("max exposure" in r for r in assessment.block_reasons)

    def test_position_count_counts_open_positions(self, risk_manager):
        held = [_position(f"S{i}", 1, 100.0) for i in range(10)]
        manager = risk_manager(positions=held)

        new_symbol = _assess(manager, Signal.BUY, 1)
        added_to_held = _assess(manager, Signal.BUY, 1, ticker="S0")

        assert any("max positions" in r for r in new_symbol.block_reasons)
        assert added_to_held.approved

    def test_exit_needs_only_the_positions(self, risk_manager):
        manager = risk_manager(
            error=ConnectionError("account endpoint down"),
            positions=[_position("SPY", 50, 33_000.0)],
        )

        assessment = _assess(manager, Signal.SELL, 50)

        assert assessment.approved

    def test_selling_more_than_held_is_blocked(self, risk_manager):
        manager = risk_manager(positions=[_position("SPY", 10, 6_600.0)])

        assessment = _assess(manager, Signal.SELL, 100)

        assert not assessment.approved
        assert any("only 10 held" in r for r in assessment.block_reasons)

    def test_sell_without_quantity_closes_the_position(self, risk_manager):
        manager = risk_manager(positions=[_position("SPY", 37, 24_420.0)])

        assessment = _assess(manager, Signal.SELL, None)

        assert assessment.approved
        assert assessment.recommended_quantity == 37

    def test_unfunded_account_is_blocked_with_a_clear_reason(self, risk_manager):
        assessment = _assess(risk_manager(portfolio=0.0, buying_power=0.0), Signal.BUY, None)

        assert not assessment.approved
        assert any("no portfolio value" in r for r in assessment.block_reasons)

    def test_unreadable_account_blocks_instead_of_using_demo_values(self, risk_manager):
        manager = risk_manager(error=ConnectionError("broker down"))

        assessment = _assess(manager, Signal.BUY, 1)

        assert not assessment.approved
        assert any("broker down" in r for r in assessment.block_reasons)


def _suggestion(block_reasons=(), signal=Signal.BUY, qty=50):
    return TradeSuggestion(
        signal=signal,
        confidence=0.7,
        entry_price=660.0,
        stop_loss=627.0,
        take_profit=712.8,
        reasoning=[],
        recommended_quantity=qty,
        portfolio_pct=33.0,
        max_loss_usd=1650.0,
        risk_reward_ratio=1.6,
        warnings=[],
        ticker="SPY",
        risk_block_reasons=list(block_reasons),
    )


def _assessment(approved, block_reasons=()):
    return RiskAssessment(
        approved=approved,
        recommended_quantity=50,
        portfolio_pct=33.0,
        max_loss_usd=1650.0,
        risk_reward_ratio=1.6,
        block_reasons=list(block_reasons),
    )


def _orchestrator(assessment):
    analysis = AnalysisResult(
        signal=Signal.BUY, confidence=0.7, entry_price=660.0, stop_loss=627.0, take_profit=712.8
    )
    parser = MagicMock()
    parser.parse = AsyncMock(return_value=TradeRequest(ticker="SPY", action="buy", quantity=50))
    parser.validate = AsyncMock(return_value=True)
    analyzer = MagicMock()
    analyzer.analyze = AsyncMock(return_value=analysis)
    risk = MagicMock()
    risk.assess = AsyncMock(return_value=assessment)
    executor = MagicMock()
    executor.execute_trade = AsyncMock()
    return TradingOrchestrator(parser, analyzer, risk, executor), risk, executor


class TestOrchestratorEnforcement:
    def test_refused_suggestion_cannot_be_executed(self):
        orchestrator, _, executor = _orchestrator(
            _assessment(False, ["Insufficient buying power: need $33,000.00, have $10.00"])
        )
        decision = asyncio.run(orchestrator.process_request("buy 50 SPY"))
        assert decision.suggestion.risk_block_reasons

        decision.approved = True  # a confirmation must not override the block
        result = asyncio.run(orchestrator.execute_decision(decision))

        assert not result.success
        assert result.message == "Trade refused by risk check"
        assert "Insufficient buying power" in result.error
        executor.execute_trade.assert_not_awaited()

    def test_final_trade_is_rechecked_at_execution(self):
        # e.g. a HOLD the user turned into a BUY, then resized: the first check didn't see it
        orchestrator, risk, executor = _orchestrator(
            _assessment(False, ["Portfolio limit: Would exceed max exposure (91.0% > 80.0%)"])
        )
        decision = TradeDecision(suggestion=_suggestion(), approved=True, modified_quantity=120)

        result = asyncio.run(orchestrator.execute_decision(decision))

        assert not result.success
        assert "max exposure" in result.error
        request = risk.assess.await_args.args[0]
        assert request.quantity == 120
        executor.execute_trade.assert_not_awaited()

    def test_approved_trade_executes(self):
        orchestrator, _, executor = _orchestrator(_assessment(True))
        executor.execute_trade.return_value = MagicMock(success=True, entry_order_id="o-1")
        decision = TradeDecision(suggestion=_suggestion(), approved=True)

        result = asyncio.run(orchestrator.execute_decision(decision))

        assert result.success
        executor.execute_trade.assert_awaited_once()


def test_cli_does_not_ask_to_confirm_a_refused_trade(monkeypatch):
    orchestrator = MagicMock()
    decision = TradeDecision(suggestion=_suggestion(["Insufficient buying power"]))
    orchestrator.process_request = AsyncMock(return_value=decision)
    orchestrator.parser.parse = AsyncMock(
        return_value=TradeRequest(ticker="SPY", action="buy", quantity=50)
    )
    orchestrator.execute_decision = AsyncMock()
    monkeypatch.setattr(handler_module, "display_position_context", MagicMock())
    monkeypatch.setattr(handler_module, "display_suggestion", MagicMock())

    handler = handler_module.TradeHandler(orchestrator, MagicMock(), None, "confirm")
    monkeypatch.setattr(handler, "_check_position_for_ticker", lambda ticker: None)
    monkeypatch.setattr(handler, "_get_confirmation", MagicMock(return_value=True))

    asyncio.run(handler.handle_trade_request("buy 50 SPY"))

    handler._get_confirmation.assert_not_called()
    orchestrator.execute_decision.assert_not_awaited()


def _refused_trade_handler(monkeypatch, action="buy"):
    orchestrator = MagicMock()
    decision = TradeDecision(suggestion=_suggestion(["Insufficient buying power"]))
    orchestrator.process_request = AsyncMock(return_value=decision)
    orchestrator.parser.parse = AsyncMock(
        return_value=TradeRequest(ticker="SPY", action=action, quantity=50)
    )
    orchestrator.execute_decision = AsyncMock()
    monkeypatch.setattr(handler_module, "display_position_context", MagicMock())
    monkeypatch.setattr(handler_module, "display_suggestion", MagicMock())

    handler = handler_module.TradeHandler(orchestrator, MagicMock(), None, "confirm")
    monkeypatch.setattr(handler, "_check_position_for_ticker", lambda ticker: None)
    monkeypatch.setattr(handler, "_get_confirmation", MagicMock(return_value=True))
    monkeypatch.setattr(handler, "_show_risk_refusal", MagicMock())
    return handler, orchestrator


def test_cli_review_request_reports_the_block_without_trading(monkeypatch):
    handler, orchestrator = _refused_trade_handler(monkeypatch, action="review")

    asyncio.run(handler.handle_trade_request("review SPY"))

    handler._show_risk_refusal.assert_called_once()
    handler._get_confirmation.assert_not_called()
    orchestrator.execute_decision.assert_not_awaited()


def test_cli_resolves_signal_conflicts_before_refusing(monkeypatch):
    # e.g. a SELL signal with no position first asks whether to buy anyway
    handler, orchestrator = _refused_trade_handler(monkeypatch)
    conflicts = AsyncMock(return_value=None)  # the user says no
    monkeypatch.setattr(handler, "_handle_signal_conflicts", conflicts)

    asyncio.run(handler.handle_trade_request("buy 50 SPY"))

    conflicts.assert_awaited_once()
    orchestrator.execute_decision.assert_not_awaited()
