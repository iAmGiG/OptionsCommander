"""
The CLI's signal path, RealVoterStrategy, on fixed prices (#331). The golden values were
recorded from it while it still ran on VoterAgent (development at ebea7d4), so moving it to
VotingSignal must not change a single decision. Market data is synthetic; nothing is fetched.
"""

import asyncio
from unittest.mock import MagicMock

import numpy as np
import pandas as pd
import pytest

import src.strategies.real_voter_strategy as strategy_module
from src.core.models import Signal, TradeRequest


def _frame(seed, sessions=260):
    rng = np.random.default_rng(seed)
    drift = rng.normal(0, 0.002)
    close = 100 * np.exp(np.cumsum(rng.normal(drift, 0.015, sessions)))
    spread = close * rng.uniform(0.002, 0.02, sessions)
    return pd.DataFrame(
        {
            "Open": close - spread * rng.uniform(-1, 1, sessions),
            "High": close + spread,
            "Low": close - spread,
            "Close": close,
            "Volume": rng.integers(1_000_000, 9_000_000, sessions).astype(float),
        },
        index=pd.bdate_range("2024-01-02", periods=sessions),
    )


# mode, seed, signal, confidence, entry, stop, target, first reason
GOLDEN = [
    ("ranked", 0, "buy", 0.7, 107.83, 102.02, 119.45, "Weak signal: 1/2 voters signal BUY"),
    ("ranked", 1, "buy", 0.7, 79.72, 78.4, 87.52, "Weak signal: 1/2 voters signal BUY"),
    ("ranked", 4, "hold", 0.0, None, None, None, "Conflicting signals: 1 BUY, 1 SELL"),
    ("ranked", 7, "buy", 0.7, 52.81, 50.82, 59.08, "Weak signal: 1/2 voters signal BUY"),
    ("ranked", 11, "hold", 0.0, None, None, None, "Conflicting signals: 1 BUY, 1 SELL"),
    (
        "standard",
        0,
        "buy",
        0.7676712092302214,
        107.83,
        102.02,
        119.45,
        "Weak signal: Only MACD signals BUY",
    ),
    (
        "standard",
        1,
        "buy",
        0.7167379849213101,
        79.72,
        78.4,
        87.52,
        "Weak signal: Only MACD signals BUY",
    ),
    ("standard", 4, "hold", 0.2, None, None, None, "Conflicting signals: MACD=BUY, RSI=SELL"),
    (
        "standard",
        7,
        "buy",
        0.7999999999999999,
        52.81,
        50.82,
        59.08,
        "Weak signal: Only RSI signals BUY",
    ),
    ("standard", 11, "hold", 0.2, None, None, None, "Conflicting signals: MACD=BUY, RSI=SELL"),
    ("multi", 0, "hold", 0.65, None, None, None, "Multi-TF split: 1d=BUY, 4h=HOLD, 1h=HOLD"),
    ("multi", 1, "hold", 0.65, None, None, None, "Multi-TF split: 1d=BUY, 4h=HOLD, 1h=SELL"),
    ("multi", 4, "hold", 1.0, None, None, None, "Multi-TF split: 1d=HOLD, 4h=HOLD, 1h=HOLD"),
    ("multi", 7, "hold", 0.65, None, None, None, "Multi-TF split: 1d=BUY, 4h=HOLD, 1h=HOLD"),
    ("multi", 11, "hold", 0.86, None, None, None, "Multi-TF split: 1d=HOLD, 4h=HOLD, 1h=BUY"),
]


@pytest.fixture
def analyze(monkeypatch):
    """Analyze AAPL on the seeded prices, in one voting mode; returns the AnalysisResult."""
    monkeypatch.setattr(strategy_module, "AnalysisHistoryManager", MagicMock())
    monkeypatch.setattr(
        strategy_module, "get_current_timeframe", lambda: {"current_timeframe": "1d"}
    )

    def run(mode, seed):
        monkeypatch.setattr(
            strategy_module, "fetch_unified_market_data", lambda *a, **k: _frame(seed)
        )
        params = {"mode": "multi", "preset": "trend_following"} if mode == "multi" else {}
        monkeypatch.setattr(strategy_module, "get_multi_timeframe_params", lambda: params)
        if mode == "standard":  # one active voter: the fixed MACD+RSI vote
            manager = MagicMock()
            manager.return_value.get_active_voters.return_value = [MagicMock()]
            monkeypatch.setattr(strategy_module, "RankedVoterManager", manager)
        offsets = {"1d": 0, "4h": 100, "1h": 200}
        monkeypatch.setattr(
            strategy_module.MultiTimeframeVoter,
            "_fetch_data_for_timeframe",
            lambda self, symbol, timeframe, end_date=None: _frame(seed + offsets[timeframe]),
        )
        request = TradeRequest(ticker="AAPL", action="review", raw_input="review AAPL")
        return asyncio.run(strategy_module.RealVoterStrategy().analyze(request))

    return run


@pytest.mark.parametrize("mode,seed,signal,confidence,entry,stop,target,reason", GOLDEN)
def test_decisions_match_the_voter_agent_before_the_move(
    analyze, mode, seed, signal, confidence, entry, stop, target, reason
):
    result = analyze(mode, seed)

    assert result.signal == Signal(signal)
    assert result.confidence == pytest.approx(confidence, abs=1e-12)
    assert (result.entry_price, result.stop_loss, result.take_profit) == (entry, stop, target)
    assert result.reasoning[0] == reason
