"""
VotingSignal: the MACD+RSI signal, free of the agent framework (#606).

The golden values below come from VoterAgent before the signal was moved out of it
(development at f9fc63a); the move must not change a single number.
"""

import os
import subprocess
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from src.trading.instruments.voting_signal import VotingSignal

REPO = Path(__file__).resolve().parents[3]


def _series(seed):
    """The synthetic series the golden values were computed on."""
    rng = np.random.default_rng(seed)
    n = int(rng.integers(30, 260))
    drift = rng.normal(0, 0.002)
    close = 100 * np.exp(np.cumsum(rng.normal(drift, 0.015, n)))
    col = "close" if seed % 2 else "Close"
    return pd.DataFrame({col: close}, index=pd.bdate_range("2024-01-02", periods=n))


GOLDEN = [
    ("evaluate_ranked_voting", 0, "BUY", 0.7, 0.5, "WEAK"),
    ("evaluate_ranked_voting", 1, "HOLD", 0.0, 0.0, "CONFLICT"),
    ("evaluate_ranked_voting", 4, "SELL", 0.7, 0.5, "WEAK"),
    ("evaluate_voting", 0, "BUY", 0.736982745480242, 0.5, "WEAK"),
    ("evaluate_voting", 1, "HOLD", 0.2, 0.0, "CONFLICT"),
    ("evaluate_voting", 4, "SELL", 0.672028271408902, 0.5, "WEAK"),
]


@pytest.mark.parametrize("method,seed,action,confidence,size,signal_type", GOLDEN)
def test_decisions_match_the_voter_agent_before_the_move(
    method, seed, action, confidence, size, signal_type
):
    result = getattr(VotingSignal(), method)(f"S{seed}", _series(seed), False)

    assert (result["action"], result["position_size"], result["signal_type"]) == (
        action,
        size,
        signal_type,
    )
    assert result["confidence"] == pytest.approx(confidence, abs=1e-12)


def test_too_little_data_is_a_hold():
    result = VotingSignal().evaluate_voting("S23", _series(23))  # 38 points < 42

    assert result["action"] == "HOLD"
    assert "Insufficient data (38 < 42)" in result["reasoning"]


def test_the_signal_loads_no_agent_framework_or_llm_client(tmp_path):
    """Run from a temp dir: importing src.trading creates .cache/ in the working dir (#600)."""
    code = (
        "import sys; import src.trading.instruments.voting_signal; "
        "print('LOADED=' + ','.join(sorted({m.split('.')[0] for m in sys.modules "
        "if m.startswith(('autogen', 'openai', 'agent_framework'))})))"
    )
    env = {**os.environ, "PYTHONPATH": str(REPO), "AUTOTRADER_STATE_DIR": str(tmp_path)}
    out = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        check=True,
        cwd=tmp_path,
        env=env,
    )

    assert [line for line in out.stdout.splitlines() if line.startswith("LOADED=")] == ["LOADED="]


def test_a_decision_keeps_the_parameters_it_was_computed_with():
    signal = VotingSignal()
    result = signal.evaluate_voting("S0", _series(0))

    signal.reconfigure(timeframe="4h", macd_params={"fast": 8})

    assert result["parameters_used"]["timeframe"] == "1d"
    assert result["parameters_used"]["macd"]["fast"] == 13


def test_a_callers_parameter_dict_is_not_changed():
    shared = {"fast": 13, "slow": 34, "signal": 8}
    first, second = VotingSignal(macd_params=shared), VotingSignal(macd_params=shared)

    first.reconfigure(macd_params={"fast": 8})

    assert shared["fast"] == 13
    assert second.macd_params["fast"] == 13


def test_reset_restores_every_parameter_including_the_timeframe():
    signal = VotingSignal(use_config_file=False)
    signal.reconfigure(timeframe="4h", rsi_params={"period": 7})

    signal.reset_to_defaults(use_config_file=False)

    assert signal.timeframe == "1d"
    assert signal.rsi_params["period"] == 14
    assert signal.current_config["timeframe"] == "1d"
