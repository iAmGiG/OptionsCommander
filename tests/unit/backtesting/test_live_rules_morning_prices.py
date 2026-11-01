"""Live-rules simulator: the morning raise from supplied 09:20 prices (#623)."""

import numpy as np
import pandas as pd
import pytest

from src.backtesting.live_rules_sim import LiveRules, simulate

DAYS = pd.bdate_range("2024-01-01", periods=10)
FLAT = (100.0, 100.0, 100.0, 100.0)  # (open, high, low, close) of the warm-up day
# day 1: bought at 100, +2% close (evening raise: breakeven 100). Day 2 gaps up and dips.
ROWS = [
    FLAT,
    (100.0, 102.0, 99.0, 102.0),
    (107.0, 109.0, 103.0, 108.5),
    (108.5, 108.6, 108.0, 108.2),
]


def _run(rows, price_on_day2=None, mode="trailing", **rules):
    frame = pd.DataFrame(rows, columns=["open", "high", "low", "close"], index=DAYS[: len(rows)])
    signals = pd.DataFrame(
        [(DAYS[i], "AAA", "BUY" if i == 1 else "HOLD", 1.0) for i in range(len(rows))],
        columns=["date", "symbol", "action", "position_size"],
    ).set_index(["date", "symbol"])
    morning = None
    if price_on_day2 is not None:
        morning = {"AAA": pd.Series({DAYS[2]: price_on_day2})}
    _, trades = simulate(
        {"AAA": frame},
        signals,
        LiveRules(
            exit_mode=mode,
            **{"morning_raise": morning is not None, "morning_prices": morning, **rules},
        ),
    )
    return trades


def test_morning_prices_need_the_morning_raise_flag():
    frame = pd.DataFrame(ROWS, columns=["open", "high", "low", "close"], index=DAYS[:4])
    signals = pd.DataFrame(
        [(DAYS[i], "AAA", "HOLD", 0.0) for i in range(4)],
        columns=["date", "symbol", "action", "position_size"],
    ).set_index(["date", "symbol"])
    rules = LiveRules(exit_mode="trailing", morning_prices={"AAA": pd.Series(dtype=float)})
    with pytest.raises(ValueError, match="morning_prices"):
        simulate({"AAA": frame}, signals, rules)


def test_a_09_20_price_at_the_open_matches_the_open_proxy():
    real = _run(ROWS, price_on_day2=107.0).iloc[0]
    proxy = _run(ROWS, morning_raise=True, morning_prices=None).iloc[0]
    assert (real.reason, real.exit_price) == (proxy.reason, proxy.exit_price)
    assert (real.reason, real.exit_price) == ("trail", pytest.approx(103.5))


def test_a_lower_09_20_price_raises_less_and_the_session_survives():
    # 104 is +4%: lock 25% -> 101, which the dip to 103 does not meet (the open proxy, 107,
    # would have set 103.5 and been stopped)
    trade = _run(ROWS, price_on_day2=104.0).iloc[0]
    assert trade.reason == "open"


def test_a_stop_above_the_open_fills_at_the_open():
    # 109 at 09:20 (+9%: lock 50% -> 104.5, 4% below the price) and the session opens at 103
    rows = [ROWS[0], ROWS[1], (103.0, 104.0, 102.0, 103.5), ROWS[3]]
    trade = _run(rows, price_on_day2=109.0).iloc[0]
    assert (trade.exit_date, trade.reason) == (DAYS[2], "trail_gap")
    assert trade.exit_price == pytest.approx(103.0)


@pytest.mark.parametrize("missing", ["nan", "date"])
def test_no_09_20_price_means_no_morning_raise(missing):
    plain = _run(ROWS)
    frame = pd.DataFrame(ROWS, columns=["open", "high", "low", "close"], index=DAYS[:4])
    signals = pd.DataFrame(
        [(DAYS[i], "AAA", "BUY" if i == 1 else "HOLD", 1.0) for i in range(4)],
        columns=["date", "symbol", "action", "position_size"],
    ).set_index(["date", "symbol"])
    series = pd.Series({DAYS[2]: np.nan}) if missing == "nan" else pd.Series({DAYS[3]: 107.0})
    _, trades = simulate(
        {"AAA": frame},
        signals,
        LiveRules(exit_mode="trailing", morning_raise=True, morning_prices={"AAA": series}),
    )
    pd.testing.assert_frame_equal(plain, trades)


def test_a_symbol_missing_from_the_morning_prices_gets_no_raise():
    frame = pd.DataFrame(ROWS, columns=["open", "high", "low", "close"], index=DAYS[:4])
    signals = pd.DataFrame(
        [(DAYS[i], "AAA", "BUY" if i == 1 else "HOLD", 1.0) for i in range(4)],
        columns=["date", "symbol", "action", "position_size"],
    ).set_index(["date", "symbol"])
    rules = LiveRules(exit_mode="trailing", morning_raise=True, morning_prices={})

    simulate({"AAA": frame}, signals, rules)  # no KeyError


def test_the_previous_close_as_the_morning_price_changes_nothing():
    # the evening raise already used the close, so a morning price equal to it adds nothing
    plain = _run(ROWS)
    same = _run(ROWS, price_on_day2=102.0)
    pd.testing.assert_frame_equal(plain, same)


def test_the_raise_cannot_see_the_days_range():
    # same 09:20 price, different highs, lows and closes later that day: the stop in force at the
    # open is the same, so a session that stays above it is unaffected by how it then trades
    a = _run(ROWS, price_on_day2=104.0)
    rows_b = [ROWS[0], ROWS[1], (107.0, 130.0, 105.0, 125.0), ROWS[3]]
    b = _run(rows_b, price_on_day2=104.0)
    # (the evening raise from a day-2 close of 125 then differs, so only day 2 is compared)
    assert a.iloc[0].exit_date != DAYS[2] and b.iloc[0].exit_date != DAYS[2]
    assert a.iloc[0].reason == "open"
