"""Causality of MACDRSISignal: the signal for day i uses prices through day i-1 only (#531).

The guard is the ``.shift(1)`` in ``MACDRSISignal._compute_signals``. The first two sections
pin it: the BUY/EXIT choice for row ``idx`` follows the consensus of row ``idx-1`` and does
not move when the price of row ``idx`` or any later row moves. With the shift removed, every
test in those two sections fails (mutation-checked on a scratch copy).

The last section guards two neighbouring properties that hold with or without the shift, so
the shift mutation leaves them green: the signal does not depend on rows after ``idx``, and
it does depend on row ``idx-1`` (it is not lagged by two days or more).
"""

import numpy as np
import pandas as pd
import pytest

from src.backtesting.signals import macd_rsi_signal as signal_module
from src.backtesting.signals.macd_rsi_signal import MACDRSISignal

WARMUP = MACDRSISignal().slow + 5  # generate_signal answers HOLD below this row


def _prices(n=300, seed=42):
    """A noisy uptrend: the real indicators give both BUY and EXIT days on it."""
    rng = np.random.default_rng(seed)
    dates = pd.bdate_range("2022-01-03", periods=n)
    return pd.DataFrame({"SPY": np.linspace(100, 150, n) + rng.normal(0, 1, n)}, index=dates)


def _action(prices, idx):
    return MACDRSISignal().generate_signal(prices, idx)["action"]


def _real_consensus(signal, series):
    """The unshifted consensus of the real indicators, with the signal's own parameters."""
    macd = signal_module.calculate_macd(series, signal.fast, signal.slow, signal.signal_period)
    rsi = signal_module.calculate_rsi(
        series, signal.rsi_period, signal.rsi_oversold, signal.rsi_overbought
    )
    return signal_module.calculate_voting_consensus(macd, rsi)["consensus"]


def _install_consensus(monkeypatch, prices, true_rows):
    """Replace the voting step with a fixed consensus pattern; return it."""
    consensus = pd.Series([i in true_rows for i in range(len(prices))], index=prices.index)
    monkeypatch.setattr(
        signal_module, "calculate_voting_consensus", lambda macd, rsi: {"consensus": consensus}
    )
    return consensus


# =============================================================================
# Known consensus pattern
# =============================================================================


def test_buy_at_idx_if_and_only_if_consensus_the_day_before(monkeypatch):
    prices = _prices(n=60)
    consensus = _install_consensus(monkeypatch, prices, {39, 41, 42, 50, 58, 59})
    signal = MACDRSISignal()

    actions = {idx: signal.generate_signal(prices, idx)["action"] for idx in range(WARMUP, 60)}

    expected = {idx: "BUY" if consensus.iloc[idx - 1] else "EXIT" for idx in range(WARMUP, 60)}
    assert actions == expected
    # the pattern has rows where consensus[idx] != consensus[idx-1], so the shift is observed
    assert actions[40] == "BUY" and actions[41] == "EXIT" and actions[43] == "BUY"


def test_the_day_consensus_turns_on_is_not_yet_a_buy(monkeypatch):
    prices = _prices(n=60)
    _install_consensus(monkeypatch, prices, {45, 46, 47})
    signal = MACDRSISignal()

    assert signal.generate_signal(prices, 45)["action"] == "EXIT"  # consensus[44] False
    assert signal.generate_signal(prices, 46)["action"] == "BUY"
    assert signal.generate_signal(prices, 48)["action"] == "BUY"  # consensus[47] True
    assert signal.generate_signal(prices, 49)["action"] == "EXIT"


def test_daily_pnl_is_the_days_return_when_long_and_zero_when_flat(monkeypatch):
    prices = _prices(n=60)
    _install_consensus(monkeypatch, prices, {44})
    signal = MACDRSISignal()
    p = prices["SPY"]

    long_day = signal.generate_signal(prices, 45)
    flat_day = signal.generate_signal(prices, 47)

    assert long_day["action"] == "BUY"
    assert long_day["position_size"] == 1.0
    assert long_day["daily_pnl_pct"] == pytest.approx(p.iloc[45] / p.iloc[44] - 1, abs=1e-12)
    assert flat_day["action"] == "EXIT"
    assert flat_day["position_size"] == 0.0
    assert flat_day["daily_pnl_pct"] == 0.0


# =============================================================================
# Real indicators: the signal for row idx is a function of rows < idx
# =============================================================================


def _rows():
    return list(range(WARMUP, 300, 7))


def test_signal_at_idx_ignores_the_price_of_idx_and_after():
    prices = _prices()
    rows = _rows()
    base = {idx: _action(prices, idx) for idx in rows}
    assert {"BUY", "EXIT"} <= set(base.values())  # both answers occur, so a flip would show

    for idx in rows:
        # keep rows < idx, replace row idx with a spike and drop everything after it
        spiked = prices.iloc[: idx + 1].copy()
        spiked.iloc[idx, 0] = 1e4
        assert _action(spiked, idx) == base[idx], idx
        # crash row idx and all later rows, full length
        crashed = prices.copy()
        crashed.iloc[idx:, 0] = crashed.iloc[idx:, 0].to_numpy() * 0.1
        assert _action(crashed, idx) == base[idx], idx


def test_every_action_follows_the_real_consensus_of_the_day_before():
    prices = _prices()
    signal = MACDRSISignal()
    consensus = _real_consensus(signal, prices["SPY"])
    rows = range(WARMUP, len(prices))

    actions = [signal.generate_signal(prices, idx)["action"] for idx in rows]

    assert actions == ["BUY" if consensus.iloc[idx - 1] else "EXIT" for idx in rows]
    # the same-day consensus gives a different answer on some rows, so the shift is observed
    assert actions != ["BUY" if consensus.iloc[idx] else "EXIT" for idx in rows]


# =============================================================================
# Neighbouring properties (they hold with or without the shift)
# =============================================================================


def test_signal_at_idx_is_the_same_on_the_series_truncated_at_idx():
    """No dependence on rows after idx: this catches a centred window, a backfill or a
    full-sample normalisation, not a missing shift (consensus[idx] is causal too)."""
    prices = _prices()
    for idx in _rows():
        assert _action(prices.iloc[: idx + 1], idx) == _action(prices, idx), idx


def test_the_price_the_day_before_moves_the_signal():
    """A crash on day idx-1 changes that day's consensus, and the action at idx follows it.

    A signal lagged by two days or more would ignore the crash, so no BUY would turn into an
    EXIT.
    """
    prices = _prices()
    signal = MACDRSISignal()
    buy_rows = [idx for idx in _rows() if _action(prices, idx) == "BUY"]
    assert buy_rows

    exits = 0
    for idx in buy_rows:
        crashed = prices.copy()
        crashed.iloc[idx - 1, 0] = crashed.iloc[idx - 1, 0] * 0.5
        day_before = bool(_real_consensus(signal, crashed["SPY"]).iloc[idx - 1])
        action = _action(crashed, idx)
        assert action == ("BUY" if day_before else "EXIT"), idx
        exits += action == "EXIT"
    assert exits > 0
