"""Candidate signals for the live-rules simulator (#532 RAF, #529 weekly KAMA): no look-ahead."""

import numpy as np
import pandas as pd
import pytest

from src.backtesting import live_rules_signals as sig
from src.backtesting.live_rules_sim import compute_signals


def make_bars(n=700, seed=3, start="2015-01-05"):
    rng = np.random.default_rng(seed)
    dates = pd.bdate_range(start, periods=n)
    close = pd.Series(100 * np.exp(np.cumsum(rng.normal(0.0004, 0.012, n))), index=dates)
    spread = pd.Series(rng.uniform(0.003, 0.02, n), index=dates)
    return pd.DataFrame(
        {"open": close, "high": close * (1 + spread), "low": close * (1 - spread), "close": close}
    )


def run(fn, bars, start, end):
    closes = {"X": bars["close"]}
    return compute_signals(closes, closes, fn, start, end, lookback_days=4000, bars={"X": bars})


class StubLive:
    """A live voter with no signal of its own, to isolate the combination rules."""

    def __init__(self, macd, rsi):
        self.macd, self.rsi = macd, rsi

    def __call__(self, symbol, frame):
        return {
            "action": "HOLD",
            "active_voters": [
                {"name": "MACD_rank1", "action": self.macd},
                {"name": "RSI_rank2", "action": self.rsi},
            ],
        }


def test_indicators_are_causal():
    """Each indicator's value at bar i is the same on the full series and on the series cut at i."""
    bars = make_bars(300)
    h, lo, c = bars["high"], bars["low"], bars["close"]
    for i in (150, 220, 299):
        cut = slice(0, i + 1)
        pairs = [
            (sig.stochastic(h, lo, c, 8, 5), sig.stochastic(h[cut], lo[cut], c[cut], 8, 5)),
            (sig.tema(c, 28), sig.tema(c[cut], 28)),
            (sig.fisher_transform(h, lo), sig.fisher_transform(h[cut], lo[cut])),
        ]
        for full, part in pairs:
            assert full.iloc[i] == pytest.approx(part.iloc[i])


def test_stochastic_bounds_and_flat_range():
    bars = make_bars(100)
    k = sig.stochastic(bars["high"], bars["low"], bars["close"], 5, 3).dropna()
    assert (k >= 0).all() and (k <= 100).all()
    flat = pd.Series(10.0, index=bars.index)
    assert sig.stochastic(flat, flat, flat, 5, 3).dropna().eq(50.0).all()


def test_raf_ignores_the_decision_days_own_bar():
    """The signal for day d does not change when day d's own high, low and close change."""
    bars = make_bars()
    day = bars.index[500]
    fn = sig.raf_signal_fn()
    base = run(fn, bars, day, day)
    shocked = bars.copy()
    shocked.loc[day, ["open", "high", "low", "close"]] = [1e4, 2e4, 1.0, 5e3]
    assert run(fn, shocked, day, day)["action"].iloc[0] == base["action"].iloc[0]
    # nor does anything after it
    later = bars.copy()
    later.loc[later.index > day, ["high", "low", "close"]] *= 0.1
    assert run(fn, later, day, day)["action"].iloc[0] == base["action"].iloc[0]


def test_raf_buys_a_clean_uptrend_and_sells_a_downtrend():
    dates = pd.bdate_range("2015-01-05", periods=300)
    rng = np.random.default_rng(0)
    up = pd.Series(100 + np.arange(300) * 0.5 + rng.normal(0, 0.4, 300), index=dates)
    h, lo = up + 0.3, up - 0.3
    states = [sig.raf_state(h[: i + 1], lo[: i + 1], up[: i + 1]) for i in range(150, 300)]
    assert 1 in states and -1 not in states
    down = up.iloc[::-1].set_axis(dates)
    h, lo = down + 0.3, down - 0.3
    states = [sig.raf_state(h[: i + 1], lo[: i + 1], down[: i + 1]) for i in range(150, 300)]
    assert -1 in states and 1 not in states


def test_third_voter_rule():
    bars = make_bars(300)
    frame = pd.DataFrame({"Close": bars["close"], "High": bars["high"], "Low": bars["low"]}).iloc[
        :250
    ]
    frame.attrs["day"] = bars.index[250]
    raf = sig._raf_vote(frame)
    cases = {
        ("BUY", "BUY"): "BUY",  # two voters agree whatever RAF says
        ("SELL", "SELL"): "SELL",
        ("HOLD", "HOLD"): "HOLD",  # RAF alone is one vote
        ("BUY", "SELL"): "BUY" if raf == 1 else "SELL" if raf == -1 else "HOLD",
    }
    for (macd, rsi), expected in cases.items():
        fn = sig.third_voter_fn(StubLive(macd, rsi))
        assert fn("X", frame)["action"] == expected, (macd, rsi, raf)
    # one voter alone is never enough (the live rule would act on it)
    one = sig.third_voter_fn(StubLive("BUY", "HOLD"))("X", frame)["action"]
    assert one == ("BUY" if raf == 1 else "HOLD")


def test_completed_weekly_closes_drop_the_open_week():
    dates = pd.bdate_range("2024-01-01", "2024-02-02")  # Mon 1 Jan .. Fri 2 Feb
    close = pd.Series(np.arange(len(dates), dtype=float), index=dates)
    # Wednesday 24 Jan: bars through Tuesday; the week of 22 Jan is open
    day = pd.Timestamp("2024-01-24")
    weekly = sig.completed_weekly_closes(close[close.index < day], day)
    assert weekly.index[-1] == pd.Timestamp("2024-01-15").to_period(
        "W"
    )  # week of 15 Jan, a Friday close
    assert weekly.iloc[-1] == close[pd.Timestamp("2024-01-19")]
    # Monday 22 Jan: the 15-19 Jan week has closed, and is used
    day = pd.Timestamp("2024-01-22")
    weekly = sig.completed_weekly_closes(close[close.index < day], day)
    assert weekly.iloc[-1] == close[pd.Timestamp("2024-01-19")]
    # Friday 19 Jan: its own week (bars through Thursday) is still open
    day = pd.Timestamp("2024-01-19")
    weekly = sig.completed_weekly_closes(close[close.index < day], day)
    assert weekly.iloc[-1] == close[pd.Timestamp("2024-01-12")]


@pytest.mark.parametrize("spec", ["research", "issue"])
def test_kama_state_ignores_the_unfinished_week(spec):
    """Changing any bar of the decision day's own week (before the day) leaves the state alone."""
    bars = make_bars(600)
    wed = [d for d in bars.index[300:500] if d.weekday() == 2][3]
    week_start = wed - pd.Timedelta(days=2)
    fn = sig.kama_signal_fn(spec)
    base = run(fn, bars, wed, wed)["action"].iloc[0]
    for factor in (0.2, 5.0):
        moved = bars.copy()
        in_week = (moved.index >= week_start) & (moved.index <= wed)
        moved.loc[in_week, ["open", "high", "low", "close"]] *= factor
        fresh = sig.kama_signal_fn(spec)  # no cached states
        assert run(fresh, moved, wed, wed)["action"].iloc[0] == base


@pytest.mark.parametrize("spec", ["research", "issue"])
def test_kama_state_uses_the_last_closed_week(spec):
    """A change in the last closed week can move the state; the cache does not hide it."""
    bars = make_bars(600)
    monday = [d for d in bars.index[300:500] if d.weekday() == 0][3]
    last_friday = monday - pd.Timedelta(days=3)
    states = set()
    for factor in (1.0, 0.3, 3.0):
        changed = bars.copy()
        changed.loc[last_friday, ["high", "low", "close"]] *= factor
        states.add(run(sig.kama_signal_fn(spec), changed, monday, monday)["action"].iloc[0])
    assert len(states) >= 2


def test_kama_state_is_causal_across_weeks():
    """The state at a week equals the state computed from the data cut at that week."""
    bars = make_bars(700)
    spec = sig.KAMA_SPECS["research"]
    weekly = bars["close"].groupby(bars.index.to_period("W")).last()
    full = [sig.kama_state(weekly.iloc[: i + 1], spec) for i in range(60, len(weekly))]
    for i in (70, 90, len(weekly) - 1):
        cut = sig.kama_state(weekly.iloc[: i + 1], spec)
        assert cut == full[i - 60]


def test_kama_filter_only_removes_buys():
    bars = make_bars(600)
    day = [d for d in bars.index[400:500] if d.weekday() == 1][0]
    frame = pd.DataFrame({"Close": bars["close"], "High": bars["high"], "Low": bars["low"]})
    frame = frame[frame.index < day]
    frame.attrs["day"] = day
    state = sig.kama_state_fn("issue")("X", frame)

    class Live:
        def __init__(self, action):
            self.action = action

        def __call__(self, symbol, window):
            assert window.index.max() < day
            assert window.index.min() >= day - pd.Timedelta(days=sig.LIVE_WINDOW_DAYS)
            return {"action": self.action, "position_size": 1.0}

    buy = sig.kama_filter_fn(Live("BUY"), "issue")("X", frame)["action"]
    assert buy == ("BUY" if state == sig.BULLISH else "HOLD")
    for action in ("SELL", "HOLD"):
        assert sig.kama_filter_fn(Live(action), "issue")("X", frame)["action"] == action


def test_compute_signals_passes_bars_and_day_without_changing_close_only_signals():
    bars = make_bars(400)
    seen = {}

    def fn(symbol, frame):
        seen["cols"] = list(frame.columns)
        seen["day"] = frame.attrs["day"]
        seen["last"] = frame.index[-1]
        return {"action": "HOLD"}

    day = bars.index[300]
    run(fn, bars, day, day)
    assert seen["cols"] == ["Close", "High", "Low"]
    assert seen["day"] == day and seen["last"] < day

    closes = {"X": bars["close"]}
    plain = compute_signals(closes, closes, lambda s, f: {"action": "HOLD"}, day, day)
    assert len(plain) == 1
