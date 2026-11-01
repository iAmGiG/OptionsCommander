"""Live-rules simulator: partial exits, three designs, on hand-built bars (#584)."""

import numpy as np
import pandas as pd
import pytest

from src.backtesting.live_rules_sim import LiveRules, PartialPlan, simulate

DAYS = pd.bdate_range("2024-01-01", periods=12)
WARM = (100.0, 100.0, 100.0, 100.0)  # (open, high, low, close) of the warm-up day
# Day 1 buys 20 shares at 100 (2% of 100,000): the stop is 95, the 4% target 104, and a quiet
# close (100.5) leaves the stop alone. 10 shares have the target, 10 are the trailing tranche.
DAY1 = (100.0, 101.0, 99.5, 100.5)


def _run(rows, design=3, plan_kw=None, **rules):
    rows = [WARM, DAY1, *rows]
    frame = pd.DataFrame(rows, columns=["open", "high", "low", "close"], index=DAYS[: len(rows)])
    signals = pd.DataFrame(
        [(DAYS[i], "AAA", "BUY" if i == 1 else "HOLD", 1.0) for i in range(len(rows))],
        columns=["date", "symbol", "action", "position_size"],
    ).set_index(["date", "symbol"])
    plan = None if design is None else PartialPlan(design=design, **(plan_kw or {}))
    return simulate(
        {"AAA": frame}, signals, LiveRules(exit_mode="trailing", partial=plan, **rules)
    )[1]


def _legs(trades):
    return [(r.reason, round(r.qty), round(r.exit_price, 4)) for r in trades.itertuples()]


# --- design 3: per-tranche orders -------------------------------------------------------------


def test_design3_target_fills_at_the_limit_on_an_intraday_touch():
    trades = _run([(100.0, 105.0, 99.0, 101.0)], design=3)
    assert _legs(trades) == [("target", 10, 104.0), ("open", 10, 101.0)]


def test_design3_gap_over_the_target_fills_at_the_open():
    trades = _run([(106.0, 107.0, 105.0, 106.0)], design=3)
    assert _legs(trades)[0] == ("target_gap", 10, 106.0)


def test_design3_gap_through_the_stop_sells_every_share_at_the_open():
    trades = _run([(94.0, 96.0, 93.0, 95.0)], design=3)
    assert _legs(trades) == [("stop_gap", 20, 94.0)]


def test_design3_a_bar_touching_both_counts_as_the_stop():
    trades = _run([(100.0, 105.0, 94.0, 101.0)], design=3)
    assert _legs(trades) == [("stop", 20, 95.0)]
    assert trades.attrs["partial"]["same_bar_stop_and_target"] == 1


def test_design3_optimistic_same_bar_reading_takes_the_target_first():
    trades = _run([(100.0, 105.0, 94.0, 101.0)], design=3, plan_kw={"same_bar": "target"})
    assert _legs(trades) == [("target", 10, 104.0), ("stop", 10, 95.0)]
    assert trades.attrs["partial"]["same_bar_stop_and_target"] == 1


def test_design3_stop_hit_without_the_target_sells_all_shares():
    trades = _run([(99.0, 100.0, 94.0, 96.0)], design=3)
    assert _legs(trades) == [("stop", 20, 95.0)]
    assert trades.attrs["partial"]["same_bar_stop_and_target"] == 0


def test_design3_the_remaining_shares_keep_the_trailing_stop_after_the_target():
    # day 2 fills the target; its close (103) is +3%: breakeven -> stop 100 for the other 10
    rows = [(100.0, 105.0, 99.0, 103.0), (102.0, 103.0, 99.0, 100.0)]
    trades = _run(rows, design=3)
    assert _legs(trades) == [("target", 10, 104.0), ("trail", 10, 100.0)]


def test_target_filled_on_the_entry_day():
    # the entry-day range is checked against the limit, as the bracket does
    rows = [WARM, (100.0, 105.0, 99.0, 102.0)]
    frame = pd.DataFrame(rows, columns=["open", "high", "low", "close"], index=DAYS[:2])
    signals = pd.DataFrame(
        [(DAYS[0], "AAA", "HOLD", 0.0), (DAYS[1], "AAA", "BUY", 1.0)],
        columns=["date", "symbol", "action", "position_size"],
    ).set_index(["date", "symbol"])
    _, trades = simulate(
        {"AAA": frame},
        signals,
        LiveRules(exit_mode="trailing", partial=PartialPlan(design=3)),
    )
    assert _legs(trades)[0] == ("target", 10, 104.0)


def test_limit_needs_the_high_to_trade_through_when_asked():
    bar = (100.0, 104.05, 99.0, 101.0)  # 5 bps over the 104 target
    assert _legs(_run([bar], plan_kw={"limit_through_bps": 0.0}))[0][0] == "target"
    assert _legs(_run([bar], plan_kw={"limit_through_bps": 10.0}))[0][0] == "open"
    through = _run([(100.0, 104.2, 99.0, 101.0)], plan_kw={"limit_through_bps": 10.0})
    assert _legs(through)[0] == ("target", 10, 104.0)  # filled at the limit price


# --- design 2: software targets ---------------------------------------------------------------


def test_design2_has_no_intraday_fill():
    # the high reaches the target but neither the open nor the close does
    trades = _run([(100.0, 106.0, 99.0, 101.0)], design=2)
    assert _legs(trades) == [("open", 20, 101.0)]
    assert trades.attrs["partial"]["target_touched_not_seen"] == 1


def test_design2_sells_at_the_close_when_the_close_is_past_the_target():
    trades = _run([(100.0, 106.0, 99.0, 105.0)], design=2)
    assert _legs(trades) == [("target_check_close", 10, 105.0), ("open", 10, 105.0)]


def test_design2_open_check_sells_at_the_open_price():
    # the 09:20 routine sees the gap: the 10 shares go at the open, not at the target
    trades = _run([(106.0, 106.5, 94.0, 96.0)], design=2)
    assert _legs(trades) == [("target_check_open", 10, 106.0), ("stop", 10, 95.0)]


def test_design2_the_open_check_cannot_see_the_days_close():
    # opens below the target, closes far above it: the sale is the close check, at the close
    trades = _run([(100.0, 112.0, 99.0, 110.0)], design=2)
    assert _legs(trades)[0] == ("target_check_close", 10, 110.0)


def test_design2_the_close_check_does_not_use_the_high_or_the_low():
    # high above the target, close below: no sale; low under the stop is the stop, whole position
    trades = _run([(100.0, 110.0, 94.0, 101.0)], design=2)
    assert _legs(trades) == [("stop", 20, 95.0)]


def test_design2_every_share_has_the_stop():
    trades = _run([(99.0, 100.0, 94.0, 96.0)], design=2)
    assert _legs(trades) == [("stop", 20, 95.0)]


def test_design2_close_check_applies_on_the_entry_day():
    # bought at 105 (target 109.2); the 15:50 routine sees a close of 109.5
    rows = [WARM, (105.0, 110.0, 104.5, 109.5)]
    frame = pd.DataFrame(rows, columns=["open", "high", "low", "close"], index=DAYS[:2])
    signals = pd.DataFrame(
        [(DAYS[0], "AAA", "HOLD", 0.0), (DAYS[1], "AAA", "BUY", 1.0)],
        columns=["date", "symbol", "action", "position_size"],
    ).set_index(["date", "symbol"])
    _, trades = simulate(
        {"AAA": frame},
        signals,
        LiveRules(exit_mode="trailing", partial=PartialPlan(design=2)),
    )
    assert _legs(trades) == [("target_check_close", 10, 109.5), ("open", 10, 109.5)]


# --- design 1: shrink the stop ----------------------------------------------------------------


def test_design1_target_shares_have_no_stop():
    # the range falls through the stop: only the 10 stop shares go; the target shares are held
    trades = _run([(99.0, 100.0, 90.0, 92.0)], design=1)
    assert _legs(trades) == [("stop", 10, 95.0), ("open", 10, 92.0)]
    assert trades.attrs["partial"]["orphan_target_shares"] == 1


def test_design1_unprotected_shares_rest_until_the_target():
    rows = [(99.0, 100.0, 90.0, 92.0), (92.0, 93.0, 80.0, 85.0), (85.0, 105.0, 84.0, 104.0)]
    trades = _run(rows, design=1)
    assert _legs(trades) == [("stop", 10, 95.0), ("target", 10, 104.0)]


def test_design1_exit_next_open_sells_the_unprotected_shares_at_the_next_open():
    rows = [(99.0, 100.0, 90.0, 92.0), (91.0, 93.0, 80.0, 85.0)]
    trades = _run(rows, design=1, plan_kw={"unprotected": "exit_next_open"})
    assert _legs(trades) == [("stop", 10, 95.0), ("orphan_exit", 10, 91.0)]


def test_design1_stop_and_limit_are_independent_in_one_bar():
    trades = _run([(100.0, 105.0, 94.0, 101.0)], design=1)
    assert _legs(trades) == [("stop", 10, 95.0), ("target", 10, 104.0)]


def test_design1_gap_through_the_stop_leaves_the_target_shares():
    trades = _run([(94.0, 96.0, 93.0, 95.0)], design=1)
    assert _legs(trades) == [("stop_gap", 10, 94.0), ("open", 10, 95.0)]


def test_design1_after_the_target_the_rest_keeps_its_stop():
    rows = [(100.0, 105.0, 99.0, 103.0), (102.0, 103.0, 99.0, 100.0)]
    trades = _run(rows, design=1)
    assert _legs(trades) == [("target", 10, 104.0), ("trail", 10, 100.0)]


# --- shared rules -----------------------------------------------------------------------------


def test_a_one_share_position_has_no_plan_and_trades_as_plain_trailing():
    rows = [(100.0, 106.0, 94.0, 101.0)]
    plain = _run(rows, design=None, position_pct=0.001)
    for design in (1, 2, 3):
        planned = _run(rows, design=design, position_pct=0.001)
        assert planned.attrs["partial"]["no_plan_one_share"] == 1
        pd.testing.assert_frame_equal(plain, planned.drop(columns="pid"))


def test_costs_apply_to_every_leg():
    trades = _run([(100.0, 105.0, 99.0, 101.0)], design=3, cost_bps=10.0)
    assert trades.iloc[0].exit_price == pytest.approx(104.0 * 0.999)
    assert trades.iloc[0].entry_price == pytest.approx(100.0 * 1.001)


def test_stop_slippage_is_off_by_default_and_hits_stops_only():
    base = _run([(99.0, 100.0, 94.0, 96.0)], design=None)
    assert base.iloc[0].exit_price == pytest.approx(95.0)
    slipped = _run([(99.0, 100.0, 94.0, 96.0)], design=None, stop_slip_bps=20.0)
    assert slipped.iloc[0].exit_price == pytest.approx(95.0 * 0.998)
    gap_only = _run(
        [(99.0, 100.0, 94.0, 96.0)], design=None, stop_slip_bps=20.0, stop_slip_gap_only=True
    )
    assert gap_only.iloc[0].exit_price == pytest.approx(95.0)
    gap = _run([(94.0, 96.0, 93.0, 95.0)], design=3, stop_slip_bps=20.0, stop_slip_gap_only=True)
    assert gap.iloc[0].exit_price == pytest.approx(94.0 * 0.998)
    target = _run([(100.0, 105.0, 99.0, 101.0)], design=3, stop_slip_bps=20.0)
    assert target.iloc[0].exit_price == pytest.approx(104.0)  # limit fills are not slipped


def test_partial_needs_the_trailing_mode():
    frame = pd.DataFrame([WARM, DAY1], columns=["open", "high", "low", "close"], index=DAYS[:2])
    signals = pd.DataFrame(
        [(DAYS[0], "AAA", "HOLD", 0.0)], columns=["date", "symbol", "action", "position_size"]
    ).set_index(["date", "symbol"])
    with pytest.raises(ValueError, match="trailing"):
        simulate({"AAA": frame}, signals, LiveRules(exit_mode="bracket", partial=PartialPlan()))


def _random_world(seed=3, days=300):
    rng = np.random.default_rng(seed)
    idx = pd.bdate_range("2020-01-01", periods=days)
    prices, rows = {}, []
    for sym in ("AAA", "BBB", "CCC"):
        close = 50 * np.exp(np.cumsum(rng.normal(0.0004, 0.015, days)))
        gap = rng.normal(0, 0.006, days)
        open_ = np.r_[close[0], close[:-1]] * (1 + gap)
        high = np.maximum(open_, close) * (1 + np.abs(rng.normal(0, 0.006, days)))
        low = np.minimum(open_, close) * (1 - np.abs(rng.normal(0, 0.006, days)))
        prices[sym] = pd.DataFrame(
            {"open": open_, "high": high, "low": low, "close": close}, index=idx
        )
        for d in idx:
            rows.append((d, sym, rng.choice(["BUY", "HOLD", "SELL"], p=[0.05, 0.9, 0.05]), 1.0))
    signals = pd.DataFrame(rows, columns=["date", "symbol", "action", "position_size"])
    return prices, signals.set_index(["date", "symbol"])


@pytest.mark.parametrize("design", [2, 3])
def test_an_unreachable_target_changes_nothing_in_designs_2_and_3(design):
    # every share keeps the trailing stop in both: with a target that is never met, the
    # equity and the exits equal plain trailing
    prices, signals = _random_world()
    common = {"exit_mode": "trailing", "position_pct": 0.1, "cost_bps": 5.0}
    eq_plain, tr_plain = simulate(prices, signals, LiveRules(**common))
    eq_plan, tr_plan = simulate(
        prices, signals, LiveRules(**common, partial=PartialPlan(design=design, target_pct=50.0))
    )
    pd.testing.assert_series_equal(eq_plain, eq_plan)
    assert len(tr_plain) > 20
    assert sorted(tr_plain["return"].round(10)) == sorted(
        tr_plan.groupby("pid")
        .apply(lambda g: (g["return"] * g["qty"]).sum() / g["qty"].sum())
        .round(10)
    )


def test_default_rules_ignore_the_partial_machinery():
    prices, signals = _random_world(seed=5)
    a = simulate(prices, signals, LiveRules(exit_mode="trailing", position_pct=0.1))
    b = simulate(
        prices,
        signals,
        LiveRules(
            exit_mode="trailing",
            position_pct=0.1,
            partial=None,
            stop_slip_bps=0.0,
            stop_slip_gap_only=True,
        ),
    )
    pd.testing.assert_series_equal(a[0], b[0])
    pd.testing.assert_frame_equal(a[1], b[1])
    assert "pid" not in a[1].columns
    assert "partial" not in a[1].attrs


@pytest.mark.parametrize("design", [1, 2, 3])
def test_planned_runs_keep_the_books_balanced(design):
    prices, signals = _random_world(seed=7)
    eq, trades = simulate(
        prices,
        signals,
        LiveRules(exit_mode="trailing", position_pct=0.1, partial=PartialPlan(design=design)),
    )
    assert np.isfinite(eq).all() and (eq > 0).all()
    # cash and shares balance: final equity is the capital plus every leg's gain (open legs
    # marked at the last close), so no leg sells shares that were not bought
    gain = (trades["qty"] * (trades["exit_price"] - trades["entry_price"])).sum()
    assert eq.iloc[-1] == pytest.approx(100_000.0 + gain)
    assert (trades["qty"] > 0).all() and trades["pid"].nunique() > 10
