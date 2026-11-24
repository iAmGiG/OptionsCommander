"""The WK-means replication runner and the hourly-bar fetcher (#673), with no DB or network."""

import datetime as dt
import importlib
import json
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

REPO = Path(__file__).resolve().parents[3]


@pytest.fixture
def runner(monkeypatch, tmp_path):
    monkeypatch.syspath_prepend(str(REPO / "scripts" / "research"))
    monkeypatch.setenv("AUTOTRADER_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.setenv("AUTOTRADER_CACHE_DIR", str(tmp_path / "cache"))
    module = importlib.import_module("wk_means_replication")
    monkeypatch.setattr(module, "SYNTHETIC_YEARS", 6)  # the shortest path that fits ten half-years
    return module


def _fake_daily(start, end):
    """Two calm years around a volatile half-year, with VIX following the volatility."""
    days = pd.bdate_range("2015-01-02", "2017-12-29")
    rng = np.random.default_rng(0)
    vol = np.where((days >= "2016-01-01") & (days <= "2016-06-30"), 0.03, 0.007)
    close = pd.Series(100 * np.exp(np.cumsum(rng.normal(0, vol))), index=days)
    vix = pd.Series(np.where(vol > 0.01, 35.0, 12.0), index=days)
    return close, vix


def _fake_hourly_bars(days):
    rows = []
    rng = np.random.default_rng(1)
    price = 200.0
    for day in days:
        for hour in range(4, 20):  # extended hours included, as Alpaca returns them
            price *= float(np.exp(rng.normal(0, 0.002)))
            rows.append({"et": pd.Timestamp(day) + pd.Timedelta(hours=hour), "close": price})
    return pd.DataFrame(rows)


def test_regular_session_keeps_seven_closes_a_day_and_four_on_early_closes(runner):
    days = [dt.date(2019, 11, 27), dt.date(2019, 11, 29)]  # the 29th closes at 13:00
    closes = runner.regular_session_closes(_fake_hourly_bars(days))
    per_day = pd.Series(1, index=pd.DatetimeIndex(closes.index).date).groupby(level=0).sum()
    assert per_day[dt.date(2019, 11, 27)] == 7
    assert per_day[dt.date(2019, 11, 29)] == 4
    hours = pd.DatetimeIndex(closes.index).hour
    assert hours.min() == 9 and hours.max() == 15


def test_the_runner_writes_results_tables_and_figures(runner, monkeypatch, tmp_path):
    monkeypatch.setattr(runner, "daily_inputs", _fake_daily)
    cache = tmp_path / "bars"
    cache.mkdir()
    days = pd.bdate_range("2016-01-04", "2016-03-31").date
    _fake_hourly_bars(days).to_parquet(cache / "SPY_2016.parquet")
    out_dir = tmp_path / "results"
    out = runner.main(
        ["--synthetic-runs", "2", "--hourly-cache", str(cache), "--results-dir", str(out_dir)]
    )
    saved = json.loads((out_dir / "wk_means_replication.json").read_text())
    assert saved["synthetic"]["runs"] == 2
    assert set(saved["synthetic"]["models"]) == {"gbm", "merton"}
    assert saved["synthetic"]["models"]["merton"]["paper"]["wk"]["ta"] == [91.28, 4.08]
    daily = saved["daily"]
    assert daily["window"] == [20, 15]
    assert set(daily["stress_days_share"]) == {"wk_p1", "wk_p2", "mk", "vol"}
    vol_2016 = [r for r in daily["episodes"] if r["episode"] == "China sell-off, early 2016"][0]
    assert vol_2016["wk_p1"] > 0.8  # the volatile half-year is found
    assert daily["vix"]["methods"]["wk_p1"]["mean_vix_stress_days"] > 30
    assert saved["hourly"]["window"] == [35, 28]
    assert "hourly_vs_daily" in saved
    prov = saved["provenance"]
    assert len(prov["commit"]) == 40 and prov["data_hashes"]["daily_spy_close"]
    assert (out_dir / "wk_means_replication.md").read_text().startswith("# WK-means replication")
    svg = (out_dir / "wk_means_replication_daily.svg").read_text()
    assert svg.startswith("<svg") and "WK-means p=1" in svg
    assert (out_dir / "wk_means_replication_hourly.svg").exists()
    assert out["daily"]["n_windows"] == len(np.arange(0, daily["n_returns"] - 20 + 1, 5))


def test_the_runner_skips_the_hourly_part_without_bars(runner, monkeypatch, tmp_path):
    monkeypatch.setattr(runner, "daily_inputs", _fake_daily)
    empty = tmp_path / "none"
    empty.mkdir()
    out = runner.main(
        ["--synthetic-runs", "0", "--hourly-cache", str(empty), "--results-dir", str(tmp_path)]
    )
    assert "hourly" not in out and "synthetic" not in out and "daily" in out


def test_runs_of_true_are_found(runner):
    assert runner._runs([0, 1, 1, 0, 1]) == [(1, 2), (4, 4)]
    assert runner._runs([0, 0]) == []


def test_the_fetcher_stores_et_timestamps_and_skips_nothing_it_got(monkeypatch):
    monkeypatch.syspath_prepend(str(REPO / "scripts" / "research"))
    fetcher = importlib.import_module("fetch_hourly_bars")
    index = pd.MultiIndex.from_product(
        [["SPY"], pd.date_range("2016-01-04 14:00", periods=3, freq="h", tz="UTC")],
        names=["symbol", "timestamp"],
    )
    frame = pd.DataFrame(
        {
            "open": 1.0,
            "high": 1.0,
            "low": 1.0,
            "close": [1.0, 2.0, 3.0],
            "volume": 10.0,
            "trade_count": 5.0,
            "vwap": 1.0,
        },
        index=index,
    )

    class FakeClient:
        def get_stock_bars(self, request):
            self.request = request
            return type("Bars", (), {"df": frame})()

    client = FakeClient()
    got = fetcher.fetch_year(client, "SPY", dt.date(2016, 1, 1), dt.date(2016, 1, 5))
    assert list(got.columns) == fetcher.COLUMNS
    assert got["et"].iloc[0] == pd.Timestamp("2016-01-04 09:00")  # 14:00 UTC in winter
    assert str(client.request.feed).endswith("sip") or "SIP" in str(client.request.feed)
    assert list(fetcher.years(dt.date(2016, 1, 1), dt.date(2017, 6, 30)))[-1] == (
        2017,
        dt.date(2017, 1, 1),
        dt.date(2017, 7, 1),
    )
