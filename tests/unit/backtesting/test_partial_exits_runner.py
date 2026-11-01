"""The #584 partial-exit runner's helpers and its multiple-testing family."""

import importlib
import json
import os
import sys
from pathlib import Path

import pandas as pd
import pytest

REPO = Path(__file__).resolve().parents[3]


@pytest.fixture
def runner(monkeypatch):
    monkeypatch.setenv("AUTOTRADER_STATE_DIR", os.environ["AUTOTRADER_STATE_DIR"])
    monkeypatch.syspath_prepend(str(REPO / "scripts" / "research"))
    monkeypatch.syspath_prepend(str(REPO))
    yield importlib.import_module("partial_exits")
    sys.modules.pop("partial_exits", None)


def test_the_plan_is_the_partial_exit_managers(runner):
    from src.trading.orders.partial_exit_manager import PartialExitManager

    manager = PartialExitManager(order_manager=None)
    config = manager.config
    assert config["split"] == [0.5, 0.5] and config["target_1_pct"] == runner.TARGETS["T4"]
    targets = manager._calculate_targets("AAA", 100.0, 5, 95.0)
    assert [t.quantity for t in targets] == [2, 3]  # int(5 * 0.5) then the remainder
    assert targets[0].exit_price == pytest.approx(104.0) and targets[1].exit_type == "trailing"
    sim = runner.make_rules("D3_T4", runner.VARIANTS["moderate"], 5.0)
    assert (sim.partial.target_pct, sim.partial.first_ratio) == (0.04, 0.5)
    assert sim.exit_mode == "trailing" and sim.cost_bps == 5.0


def test_make_rules_for_the_reference_modes_has_no_plan(runner):
    for key in ("bracket", "trailing", "trailing_target"):
        rules = runner.make_rules(key, runner.VARIANTS["moderate"], 0.0)
        assert rules.partial is None and rules.exit_mode == key


def test_positions_combine_their_legs_by_quantity(runner):
    d = pd.Timestamp
    legs = pd.DataFrame(
        [
            ("AAA", d("2024-01-02"), 100.0, d("2024-01-03"), 104.0, "target", 10, 0.04, 0),
            ("AAA", d("2024-01-02"), 100.0, d("2024-01-05"), 96.0, "stop", 30, -0.04, 0),
            ("BBB", d("2024-01-02"), 50.0, None, 55.0, "open", 4, 0.10, 1),
        ],
        columns=[
            "symbol",
            "entry_date",
            "entry_price",
            "exit_date",
            "exit_price",
            "reason",
            "qty",
            "return",
            "pid",
        ],
    )
    pos = runner.positions_of(legs)
    assert len(pos) == 2
    assert pos.iloc[0]["return"] == pytest.approx((10 * 0.04 + 30 * -0.04) / 40)
    assert pos.iloc[0]["exit_date"] == d("2024-01-05") and pos.iloc[0]["reason"] == "stop"
    assert pos.iloc[1]["reason"] == "open"


def test_audit_collects_the_partial_family_only(monkeypatch, tmp_path):
    monkeypatch.syspath_prepend(str(REPO / "scripts" / "research"))
    audit = importlib.import_module("multiple_testing_audit")
    test = {
        "comparison": "D3_T4 - trailing",
        "variant": "moderate",
        "period": "full",
        "cost_bps": 5.0,
        "return_test": {"mean_daily_diff": 1e-5, "t_statistic": 1.0, "p_value": 0.3},
    }
    (tmp_path / "partial_exits.json").write_text(
        json.dumps({"paired_tests": [test], "paired_extra": [{**test, "comparison": "D3 - D2"}]})
    )
    monkeypatch.setattr(audit, "STRATEGY_RESULTS_DIR", tmp_path)
    rows = []
    audit.collect_partial_exits(rows)
    assert [r["study"] for r in rows] == ["#584 D3_T4 - trailing (moderate)"]
