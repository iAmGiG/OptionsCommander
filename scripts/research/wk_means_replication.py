"""
WK-means market regimes: implement and replicate the published clustering (#673).

Horvath, Issa and Muguruza, "Clustering market regimes using the Wasserstein distance", Journal
of Computational Finance (2024), doi:10.21314/JCF.2024.005; preprint arXiv:2110.11848. The
method is in src/backtesting/regimes/wk_means.py. This runner has three parts.

1. Synthetic, the paper's Tables 3 and 4: regime-switching GBM and Merton paths, 50 of each,
   with the paper's parameters (src/backtesting/regimes/synthetic.py), windows (h1, h2) = (35, 28)
   and k = 2. The synthetic windows are not stated in the paper; (35, 28) is its SPY setting and
   fits its "v in [1, 5]". Methods: WK-means for p = 1 and p = 2 and MK-means with four moments,
   each with the paper's initialisation (k random windows, one run) and with k-means++ and ten
   restarts, plus k-means on window volatility alone. Scores are the paper's ROFS, RONS and TA.
   The paper reports TA 90.60% (GBM) and 91.28% (Merton) for WK-means, 93.23% and 66.64% for
   MK-means.
2. Daily SPY, 2008-01-02 to 2020-12-31 (gex equity_prices_daily, read-only; the table starts in
   2008, the paper's sample in 2005): log close-to-close returns, windows (20, 15), four weeks
   moved forward one week.
3. Hourly SPY, 2016 to 2020, the paper's frequency and windows (35, 28): Alpaca bars (SIP, raw)
   fetched by fetch_hourly_bars.py into a folder outside the repo (--hourly-cache). The regular
   session's hourly closes (bars starting 09:00 to 15:00 ET, to 12:00 on early-close days) give
   seven returns a day, the overnight one included, matching the paper's 252 x 7 a year.

For the real data the clustering is in sample, as published: all windows are clustered at once,
so a day's label uses returns after it. Nothing here is a trading result. A day is labelled by
the majority of the windows that contain it (for hourly data, by the votes on that day's returns).
The labels are compared with the episodes the paper names (date ranges fixed below), with VIX
(FRED VIXCLS, same day) and with k-means on window volatility alone, which shows how much of the
clustering is plain volatility.

Writes <results-dir>/<output>.json (with provenance: commit, versions, data hashes, seeds),
<output>.md (tables) and <output>_daily.svg / <output>_hourly.svg (price, stress days shaded).

Usage:
    ~/miniconda3/envs/OptionsCommander/bin/python -s scripts/research/wk_means_replication.py \\
        [--hourly-cache <folder from fetch_hourly_bars.py>] [--synthetic-runs 50]
"""

import argparse
import atexit
import datetime
import hashlib
import json
import os
import platform
import shutil
import subprocess
import sys
import tempfile
from itertools import combinations
from pathlib import Path
from html import escape

import numpy as np
import pandas as pd

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO_ROOT))

# Research never touches the user's state or cache; scratch directories are removed at exit.
_SCRATCH_DIRS = []
for _var, _prefix in (
    ("AUTOTRADER_STATE_DIR", "wkmeans_state_"),
    ("AUTOTRADER_CACHE_DIR", "wkmeans_cache_"),
):
    if _var not in os.environ:
        os.environ[_var] = tempfile.mkdtemp(prefix=_prefix)
        _SCRATCH_DIRS.append(os.environ[_var])


@atexit.register
def _remove_scratch_dirs() -> None:
    for path in _SCRATCH_DIRS:
        shutil.rmtree(path, ignore_errors=True)


from src.backtesting.regimes.synthetic import regime_switching_returns  # noqa: E402
from src.backtesting.regimes.wk_means import (  # noqa: E402
    MomentKMeans,
    VolatilityKMeans,
    WKMeans,
    accuracy_scores,
    lift,
    window_votes,
)
from src.utils.market_hours import nyse_early_closes  # noqa: E402

RESULTS_DIR = REPO_ROOT / "docs/08_research/03_strategy_research/results"
OUTPUT_STEM = "wk_means_replication"

K = 2
N_MOMENTS = 4
N_INIT = 10
SEED = 0
SYNTHETIC_WINDOW = (35, 28)
SYNTHETIC_YEARS = 20
SYNTHETIC_SEED_BASE = {"gbm": 0, "merton": 10_000}  # path seed = base + run; fit seed = run
DAILY_WINDOW = (20, 15)
HOURLY_WINDOW = (35, 28)
DAILY_START, DAILY_END = "2008-01-02", "2020-12-31"
HOURLY_START, HOURLY_END = "2016-01-01", "2020-12-31"
PERIODS_PER_YEAR = {"daily": 252, "hourly": 252 * 7}

# Tables 3 (GBM) and 4 (Merton): mean and the paper's "95% CI" (definition not stated), n = 50.
PAPER = {
    "gbm": {
        "wk": {"ta": [90.60, 5.81], "rons": [87.24, 4.11], "rofs": [91.72, 6.46]},
        "mk": {"ta": [93.23, 0.41], "rons": [74.83, 1.57], "rofs": [99.38, 0.10]},
        "hmm": {"ta": [58.16, 7.11], "rons": [41.51, 7.43], "rofs": [63.72, 11.94]},
    },
    "merton": {
        "wk": {"ta": [91.28, 4.08], "rons": [86.87, 3.10], "rofs": [92.76, 4.43]},
        "mk": {"ta": [66.64, 3.42], "rons": [27.25, 8.73], "rofs": [79.79, 7.40]},
        "hmm": {"ta": [75.05, 0.01], "rons": [0.66, 0.04], "rofs": [99.87, 0.01]},
    },
}

# The SPY episodes the paper names (Section 3.2.2) as date ranges set before the run, plus two
# 2018 episodes it does not name, as a check on what the clustering flags beyond its list.
EPISODES = [
    ("Financial crisis", "2008-09-01", "2009-03-31", True),
    ("Euro debt crisis, 2010", "2010-05-01", "2010-07-31", True),
    ("US downgrade, 2011", "2011-08-01", "2011-10-31", True),
    ("China devaluation, 2015", "2015-08-01", "2015-09-30", True),
    ("China sell-off, early 2016", "2016-01-01", "2016-02-29", True),
    ("Volatility spike, Feb 2018", "2018-02-01", "2018-02-28", False),
    ("Q4 2018 sell-off", "2018-10-01", "2018-12-31", False),
    ("COVID crash, 2020", "2020-03-01", "2020-04-30", True),
]

METHOD_LABELS = {
    "wk_p1": "WK-means p=1",
    "wk_p2": "WK-means p=2",
    "mk": "MK-means (4 moments)",
    "vol": "Volatility k-means",
}

SYNTHETIC_VARIANTS = {
    "wk_p1_paper_init": lambda s: WKMeans(k=K, p=1, n_init=1, init="random", seed=s),
    "wk_p1": lambda s: WKMeans(k=K, p=1, n_init=N_INIT, init="kmeans++", seed=s),
    "wk_p2_paper_init": lambda s: WKMeans(k=K, p=2, n_init=1, init="random", seed=s),
    "wk_p2": lambda s: WKMeans(k=K, p=2, n_init=N_INIT, init="kmeans++", seed=s),
    "mk_paper_init": lambda s: MomentKMeans(
        k=K, n_moments=N_MOMENTS, n_init=1, init="random", seed=s
    ),
    "mk": lambda s: MomentKMeans(k=K, n_moments=N_MOMENTS, n_init=N_INIT, init="kmeans++", seed=s),
    "vol": lambda s: VolatilityKMeans(k=K, n_init=N_INIT, init="kmeans++", seed=s),
}


def real_methods(seed: int) -> dict:
    return {
        "wk_p1": WKMeans(k=K, p=1, n_init=N_INIT, init="kmeans++", seed=seed),
        "wk_p2": WKMeans(k=K, p=2, n_init=N_INIT, init="kmeans++", seed=seed),
        "mk": MomentKMeans(k=K, n_moments=N_MOMENTS, n_init=N_INIT, init="kmeans++", seed=seed),
        "vol": VolatilityKMeans(k=K, n_init=N_INIT, init="kmeans++", seed=seed),
    }


# --- synthetic ---------------------------------------------------------------------------------


def synthetic_run(model: str, run: int, years: float) -> dict:
    returns, in_regime = regime_switching_returns(
        model, seed=SYNTHETIC_SEED_BASE[model] + run, years=years
    )
    windows = lift(returns, *SYNTHETIC_WINDOW)
    scores = {}
    for name, make in SYNTHETIC_VARIANTS.items():
        fit = make(run).fit(windows)
        votes = window_votes(windows, fit.labels_, len(returns), K)
        scores[name] = accuracy_scores(votes, in_regime)
    return scores


def summarise(values) -> dict:
    """Mean, sd, 95% CI half-width of the mean, and minimum, in percent."""
    a = 100.0 * np.asarray(values, dtype=float)
    sd = float(a.std(ddof=1)) if len(a) > 1 else 0.0
    return {
        "mean": round(float(a.mean()), 2),
        "sd": round(sd, 2),
        "ci95": round(1.96 * sd / np.sqrt(len(a)), 2),
        "min": round(float(a.min()), 2),
        "n": len(a),
    }


def synthetic_section(n_runs: int, years: float) -> dict:
    out = {
        "window": list(SYNTHETIC_WINDOW),
        "k": K,
        "runs": n_runs,
        "years": years,
        "path_seed": {m: f"{b} + run" for m, b in SYNTHETIC_SEED_BASE.items()},
        "fit_seed": "run",
        "models": {},
    }
    for model in ("gbm", "merton"):
        per_run = [synthetic_run(model, run, years) for run in range(n_runs)]
        table = {
            name: {s: summarise([r[name][s] for r in per_run]) for s in ("ta", "rons", "rofs")}
            for name in SYNTHETIC_VARIANTS
        }
        out["models"][model] = {"ours": table, "paper": PAPER[model]}
    return out


# --- real data ---------------------------------------------------------------------------------


def daily_inputs(start: str, end: str):
    """SPY daily closes and FRED VIXCLS from the gex database (read-only session)."""
    from src.backtesting.research_backtester import ResearchBacktester

    bt = ResearchBacktester()
    try:
        prices = bt.fetch_equity_prices(["SPY"], start, end)
        close = prices.set_index("trading_date")["close"].astype(float).sort_index()
        with bt._get_connection().cursor() as cur:  # the backtester's read-only session
            cur.execute(
                """
                SELECT observation_date, value FROM market_data.fred_economic_data
                WHERE series_id = 'VIXCLS' AND value IS NOT NULL
                  AND observation_date BETWEEN %s AND %s
                ORDER BY observation_date
                """,
                (start, end),
            )
            rows = cur.fetchall()
    finally:
        bt.close()
    vix = pd.Series([float(v) for _, v in rows], index=pd.to_datetime([d for d, _ in rows]))
    return close, vix[~vix.index.duplicated()]


def regular_session_closes(bars: pd.DataFrame) -> pd.Series:
    """Closes of the regular-session hourly bars (09:00 to 15:00 ET; to 12:00 on early closes).

    A bar starting at 09:00 closes at about 10:00, so its close is a regular-session price; bars
    from the close on hold extended-hours trades and are dropped.
    """
    et = pd.to_datetime(bars["et"])
    days = et.dt.date
    early = set()
    for year in sorted(set(et.dt.year)):
        early |= nyse_early_closes(int(year))
    last_hour = np.where(days.isin(early), 12, 15)
    keep = ((et.dt.minute == 0) & (et.dt.hour >= 9) & (et.dt.hour <= last_hour)).to_numpy()
    closes = pd.Series(bars["close"].astype(float).to_numpy()[keep], index=et[keep].to_numpy())
    closes = closes.sort_index()
    return closes[~closes.index.duplicated(keep="last")]


def hourly_inputs(cache_dir: Path, start: str, end: str):
    """Regular-session hourly closes from the fetch_hourly_bars.py folder, or None."""
    files = sorted(Path(cache_dir).glob("SPY_*.csv.gz"))
    if not files:
        return None
    frames = [pd.read_csv(f, parse_dates=["et"]) for f in files]
    bars = pd.concat(frames, ignore_index=True)
    et = pd.to_datetime(bars["et"])
    bars = bars[(et >= pd.Timestamp(start)) & (et < pd.Timestamp(end) + pd.Timedelta(days=1))]
    return regular_session_closes(bars)


def session_check(closes: pd.Series) -> dict:
    """Days whose regular-session bar count is not 7 (or 4 on an early close).

    A missing bar would turn a two-hour move into one hourly return, so the count is reported
    with the results rather than assumed.
    """
    stamps = pd.DatetimeIndex(closes.index)
    per_day = pd.Series(1, index=stamps.normalize()).groupby(level=0).sum()
    early = set()
    for year in sorted(set(per_day.index.year)):
        early |= nyse_early_closes(int(year))
    expected = pd.Series([4 if d.date() in early else 7 for d in per_day.index], per_day.index)
    short = per_day[per_day != expected]
    return {
        "days": len(per_day),
        "early_close_days": int((expected == 4).sum()),
        "days_with_unexpected_bar_count": len(short),
        "examples": [f"{d.date()}: {n} bars" for d, n in short.head(10).items()],
    }


def data_hash(index, values) -> str:
    digest = hashlib.sha256(np.ascontiguousarray(values, dtype=np.float64).tobytes())
    digest.update("|".join(str(i) for i in index).encode())
    return digest.hexdigest()[:16]


def _cluster_description(model, windows, periods: int) -> list:
    vol = windows.values.std(axis=1) * np.sqrt(periods)
    return [
        {
            "label": j,
            "windows": int((model.labels_ == j).sum()),
            "share": round(float((model.labels_ == j).mean()), 4),
            "median_annual_vol_pct": round(float(np.median(vol[model.labels_ == j]) * 100), 1),
        }
        for j in range(K)
    ]


def cluster_returns(returns, return_day, window, seed: int, periods: int):
    """Fit every method; per-day stress labels (majority vote), fit details, agreement."""
    windows = lift(returns, *window)
    labels, fits, stress = {}, {}, {}
    for name, model in real_methods(seed).items():
        model.fit(windows)
        votes = window_votes(windows, model.labels_, len(returns), K)
        per_day = pd.DataFrame(votes, index=return_day).groupby(level=0).sum()
        covered = per_day.sum(axis=1) > 0
        stress[name] = (per_day[K - 1] > per_day[0])[covered]
        labels[name] = model.labels_
        fits[name] = {
            "cost": round(model.cost_, 10),
            "n_iter": model.n_iter_,
            "converged": bool(model.converged_),
            "clusters": _cluster_description(model, windows, periods),
        }
    agreement = {
        f"{a}|{b}": round(float(np.mean(labels[a] == labels[b])), 4)
        for a, b in combinations(labels, 2)
    }
    return windows, fits, pd.DataFrame(stress), agreement


def episode_table(day_stress: pd.DataFrame) -> list:
    """Share of each episode's trading days labelled stress, per method."""
    days = day_stress.index
    inside = np.zeros(len(days), dtype=bool)
    rows = []
    for name, start, end, named in EPISODES:
        mask = (days >= pd.Timestamp(start)) & (days <= pd.Timestamp(end))
        if not mask.any():
            continue
        inside |= mask
        shares = {m: round(float(day_stress[m][mask].mean()), 3) for m in day_stress}
        rows.append(
            {
                "episode": name,
                "named_by_paper": named,
                "start": start,
                "end": end,
                "days": int(mask.sum()),
                **shares,
            }
        )
    outside = {m: round(float(day_stress[m][~inside].mean()), 3) for m in day_stress}
    rows.append(
        {
            "episode": "All other days",
            "named_by_paper": None,
            "start": None,
            "end": None,
            "days": int((~inside).sum()),
            **outside,
        }
    )
    return rows


def vix_comparison(day_stress: pd.DataFrame, vix: pd.Series) -> dict:
    """Same-day VIX on stress and calm days, and how often high and low VIX days are stress."""
    v = vix.reindex(day_stress.index)
    ok = v.notna().to_numpy()
    v = v[ok]
    out = {
        "days_with_vix": int(ok.sum()),
        "days_vix_30_plus": int((v >= 30).sum()),
        "days_vix_under_15": int((v < 15).sum()),
        "methods": {},
    }
    for method in day_stress:
        s = day_stress[method][ok].astype(bool)
        out["methods"][method] = {
            "mean_vix_stress_days": round(float(v[s.to_numpy()].mean()), 2),
            "mean_vix_calm_days": round(float(v[~s.to_numpy()].mean()), 2),
            "share_stress_when_vix_30_plus": round(float(s[(v >= 30).to_numpy()].mean()), 3),
            "share_stress_when_vix_under_15": round(float(s[(v < 15).to_numpy()].mean()), 3),
        }
    return out


def real_section(closes: pd.Series, freq: str, window, seed: int, vix=None) -> tuple:
    returns = np.diff(np.log(closes.to_numpy()))
    stamps = pd.DatetimeIndex(closes.index[1:])
    return_day = stamps.normalize()
    windows, fits, stress, agreement = cluster_returns(
        returns, return_day, window, seed, PERIODS_PER_YEAR[freq]
    )
    section = {
        "frequency": freq,
        "window": list(window),
        "k": K,
        "fit_seed": seed,
        "n_returns": len(returns),
        "n_windows": len(windows),
        "first": str(stamps[0]),
        "last": str(stamps[-1]),
        "data_hash": data_hash(closes.index, closes.to_numpy()),
        "fits": fits,
        "agreement_windows": agreement,
        "stress_days_share": {m: round(float(stress[m].mean()), 3) for m in stress},
        "episodes": episode_table(stress),
    }
    if vix is not None:
        section["vix"] = vix_comparison(stress, vix)
    return section, stress


# --- outputs -----------------------------------------------------------------------------------


def _runs(flags) -> list:
    """(first, last) index of each run of True."""
    f = np.concatenate([[0], np.asarray(flags, dtype=int), [0]])
    d = np.diff(f)
    return list(zip(np.flatnonzero(d == 1), np.flatnonzero(d == -1) - 1))


def stress_svg(days: pd.DatetimeIndex, close, strips: dict, title: str) -> str:
    """Log price with the first strip's stress days shaded, and one strip per method below."""
    width, left, right, top, panel, strip_h, gap = 1000, 160, 20, 40, 240, 12, 6
    n, span = len(days), width - left - right
    step = span / max(n - 1, 1)
    strips_top = top + panel + 22
    height = strips_top + len(strips) * (strip_h + gap) + 34
    logp = np.log(np.asarray(close, dtype=float))
    lo, hi = float(logp.min()), float(logp.max())

    def x(i):
        return left + step * i

    def y(v):
        return top + panel * (1 - (v - lo) / (hi - lo or 1.0))

    red = "#c0392b"
    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" '
        f'viewBox="0 0 {width} {height}" font-family="Helvetica, Arial, sans-serif">',
        f'<rect width="{width}" height="{height}" fill="#ffffff"/>',
        f'<text x="{left}" y="24" font-size="15" fill="#111827">{escape(title)}</text>',
        f'<text x="{width - right}" y="24" font-size="11" text-anchor="end" fill="#6b7280">'
        "x axis: trading days</text>",
    ]
    for a, b in _runs(next(iter(strips.values()))):
        parts.append(
            f'<rect x="{x(a) - step / 2:.1f}" y="{top}" width="{x(b) - x(a) + step:.1f}" '
            f'height="{panel}" fill="{red}" fill-opacity="0.16"/>'
        )
    points = " ".join(f"{x(i):.1f},{y(v):.1f}" for i, v in enumerate(logp))
    parts.append(f'<polyline points="{points}" fill="none" stroke="#1f2937" stroke-width="1.1"/>')
    for v in (lo, hi):
        parts.append(
            f'<text x="{left - 8}" y="{y(v) + 4:.1f}" font-size="11" text-anchor="end" '
            f'fill="#374151">${np.exp(v):,.0f}</text>'
        )
    for row, (label, flags) in enumerate(strips.items()):
        yy = strips_top + row * (strip_h + gap)
        parts.append(
            f'<text x="{left - 8}" y="{yy + strip_h - 2}" font-size="11" '
            f'text-anchor="end" fill="#111827">{escape(label)}</text>'
        )
        parts.append(
            f'<rect x="{left}" y="{yy}" width="{span}" height="{strip_h}" fill="#f3f4f6"/>'
        )
        for a, b in _runs(flags):
            parts.append(
                f'<rect x="{x(a) - step / 2:.1f}" y="{yy}" '
                f'width="{x(b) - x(a) + step:.1f}" height="{strip_h}" fill="{red}"/>'
            )
    bottom = strips_top + len(strips) * (strip_h + gap)
    years = days.year
    for i in np.flatnonzero(np.r_[True, years[1:] != years[:-1]]):
        parts.append(
            f'<line x1="{x(i):.1f}" y1="{top}" x2="{x(i):.1f}" y2="{bottom}" '
            f'stroke="#d1d5db" stroke-width="0.6"/>'
        )
        parts.append(
            f'<text x="{x(i) + 2:.1f}" y="{bottom + 16}" font-size="11" '
            f'fill="#374151">{years[i]}</text>'
        )
    parts.append("</svg>")
    return "\n".join(parts) + "\n"


def provenance(hashes: dict) -> dict:
    def git(*args):
        result = subprocess.run(["git", *args], cwd=REPO_ROOT, capture_output=True, text=True)
        return result.stdout.strip()

    changed = git(
        "status", "--porcelain", "--untracked-files=no", "--", ".", ":(exclude)docs/08_research"
    )
    return {
        "commit": git("rev-parse", "HEAD"),
        "uncommitted_code_changes": bool(changed),
        "run_timestamp_utc": datetime.datetime.now(datetime.timezone.utc).isoformat(
            timespec="seconds"
        ),
        "python": platform.python_version(),
        "numpy": np.__version__,
        "pandas": pd.__version__,
        "data_hashes": hashes,
    }


def _pct(cell) -> str:
    return f"{cell['mean']:.1f} ± {cell['ci95']:.1f}"


def synthetic_md(syn: dict) -> list:
    lines = [
        "## Synthetic paths (the paper's Tables 3 and 4)",
        "",
        f"{syn['runs']} paths per model, {syn['years']} years of hourly steps, windows "
        f"{tuple(syn['window'])}, k = {syn['k']}. Mean ± 95% CI of the mean, in percent. "
        "The paper's ± is labelled a 95% CI; its definition is not stated.",
        "",
        "| Model | Method | TA | RONS | ROFS | Paper TA |",
        "| --- | --- | --- | --- | --- | --- |",
    ]
    for model, block in syn["models"].items():
        for name, cell in block["ours"].items():
            family = "wk" if name.startswith("wk") else ("mk" if name.startswith("mk") else None)
            paper = block["paper"].get(family, {}).get("ta") if family else None
            paper_text = f"{paper[0]:.2f} ± {paper[1]:.2f}" if paper else "—"
            lines.append(
                f"| {model} | {name} | {_pct(cell['ta'])} | {_pct(cell['rons'])} | "
                f"{_pct(cell['rofs'])} | {paper_text} |"
            )
    return lines + [""]


def _bar_count_lines(section: dict) -> list:
    check = section.get("bars_per_day")
    if not check:
        return []
    return [
        f"Bars per day checked: {check['days']} days, {check['early_close_days']} early closes, "
        f"{check['days_with_unexpected_bar_count']} with an unexpected count.",
        "",
    ]


def real_md(section: dict, title: str) -> list:
    methods = list(section["stress_days_share"])
    head = "| " + " | ".join(METHOD_LABELS[m] for m in methods) + " |"
    rule = "| --- " * (len(methods) + 2) + "|"
    lines = [
        f"## {title}",
        "",
        f"{section['n_returns']} returns, {section['first'][:10]} to {section['last'][:10]}, "
        f"windows {tuple(section['window'])} ({section['n_windows']}), k = {section['k']}, "
        f"data hash {section['data_hash']}. In-sample clustering.",
        "",
        *_bar_count_lines(section),
        "Share of trading days labelled stress:",
        "",
        "| Episode | Days " + head,
        rule,
    ]
    for row in section["episodes"]:
        mark = "" if row["named_by_paper"] in (True, None) else " (not named by the paper)"
        cells = " | ".join(f"{row[m]:.2f}" for m in methods)
        lines.append(f"| {row['episode']}{mark} | {row['days']} | {cells} |")
    lines += [
        "",
        "Clusters (median annualised volatility of the windows in each):",
        "",
        "| Method | Calm windows | Calm vol % | Stress windows | Stress vol % |",
        "| --- | --- | --- | --- | --- |",
    ]
    for m in methods:
        calm, stress = section["fits"][m]["clusters"]
        lines.append(
            f"| {METHOD_LABELS[m]} | {calm['windows']} | {calm['median_annual_vol_pct']} "
            f"| {stress['windows']} | {stress['median_annual_vol_pct']} |"
        )
    lines += ["", "Agreement (share of windows with the same label):", ""]
    lines += [
        f"- {METHOD_LABELS[a]} and {METHOD_LABELS[b]}: {v:.3f}"
        for (a, b), v in ((k.split("|"), v) for k, v in section["agreement_windows"].items())
    ]
    if "vix" in section:
        vix = section["vix"]
        lines += [
            "",
            f"Same-day VIX ({vix['days_vix_30_plus']} days at 30 or more, "
            f"{vix['days_vix_under_15']} under 15):",
            "",
            "| Method | Mean VIX, stress days | Mean VIX, calm days | Stress share, VIX ≥ 30 "
            "| Stress share, VIX < 15 |",
            "| --- | --- | --- | --- | --- |",
        ]
        for m, c in vix["methods"].items():
            lines.append(
                f"| {METHOD_LABELS[m]} | {c['mean_vix_stress_days']} | "
                f"{c['mean_vix_calm_days']} | {c['share_stress_when_vix_30_plus']} | "
                f"{c['share_stress_when_vix_under_15']} |"
            )
    return lines + [""]


def report_md(out: dict) -> str:
    prov = out["provenance"]
    lines = [
        "# WK-means replication (#673)",
        "",
        f"Generated by `scripts/research/wk_means_replication.py` at commit "
        f"`{prov['commit'][:10]}` ({prov['run_timestamp_utc']}). The clustering is in "
        "sample, as published: these are not trading results. Write-up: "
        "[../wk_means_regimes.md](../wk_means_regimes.md).",
        "",
    ]
    if out.get("synthetic"):
        lines += synthetic_md(out["synthetic"])
    for key, title in (("daily", "Daily SPY"), ("hourly", "Hourly SPY")):
        if out.get(key):
            lines += real_md(out[key], title)
    if out.get("hourly_vs_daily"):
        lines += ["## Hourly against daily labels", ""]
        lines += [
            f"- {METHOD_LABELS[m]}: {v:.3f} of the common days get the same label"
            for m, v in out["hourly_vs_daily"].items()
        ]
        lines.append("")
    return "\n".join(lines)


def figure(stress: pd.DataFrame, close: pd.Series, title: str) -> str:
    """The SVG for one data set: the days the windows cover, that day's close, one strip each."""
    days = pd.DatetimeIndex(stress.index)
    strips = {METHOD_LABELS[m]: stress[m].to_numpy() for m in stress}
    return stress_svg(days, close.reindex(days).to_numpy(), strips, title)


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description="WK-means replication (#673)")
    parser.add_argument("--synthetic-runs", type=int, default=50, help="0 skips the synthetic part")
    parser.add_argument("--no-daily", action="store_true", help="skip the daily SPY part (DB)")
    parser.add_argument("--hourly-cache", type=Path, help="folder from fetch_hourly_bars.py")
    parser.add_argument("--seed", type=int, default=SEED, help="fit seed for the real data")
    parser.add_argument("--results-dir", type=Path, default=RESULTS_DIR)
    parser.add_argument("--output", default=OUTPUT_STEM, help="file stem in the results folder")
    return parser.parse_args(argv)


def main(argv=None):
    args = parse_args(argv)
    args.results_dir.mkdir(parents=True, exist_ok=True)
    out = {"issue": "#673", "paper": "arXiv:2110.11848; doi:10.21314/JCF.2024.005"}
    hashes, figures, day_stress = {}, {}, {}
    if args.synthetic_runs > 0:
        out["synthetic"] = synthetic_section(args.synthetic_runs, SYNTHETIC_YEARS)
    if not args.no_daily:
        close, vix = daily_inputs(DAILY_START, DAILY_END)
        out["daily"], day_stress["daily"] = real_section(
            close, "daily", DAILY_WINDOW, args.seed, vix
        )
        hashes["daily_spy_close"] = out["daily"]["data_hash"]
        figures["daily"] = figure(
            day_stress["daily"], close, "SPY daily, 2008-2020: in-sample stress days"
        )
    hourly = (
        hourly_inputs(args.hourly_cache, HOURLY_START, HOURLY_END) if args.hourly_cache else None
    )
    if hourly is not None:
        out["hourly"], day_stress["hourly"] = real_section(
            hourly, "hourly", HOURLY_WINDOW, args.seed
        )
        hashes["hourly_spy_close"] = out["hourly"]["data_hash"]
        out["hourly"]["bars_per_day"] = session_check(hourly)
        if out["hourly"]["bars_per_day"]["days_with_unexpected_bar_count"]:
            print(f"WARNING: hourly bars missing: {out['hourly']['bars_per_day']['examples']}")
        stress = day_stress["hourly"]
        day_close = hourly.groupby(pd.DatetimeIndex(hourly.index).normalize()).last()
        figures["hourly"] = figure(
            stress, day_close, "SPY hourly, 2016-2020: in-sample stress days"
        )
        if "daily" in day_stress:
            common = stress.index.intersection(day_stress["daily"].index)
            out["hourly_vs_daily"] = {
                m: round(float((stress[m][common] == day_stress["daily"][m][common]).mean()), 3)
                for m in stress
            }
    out["provenance"] = provenance(hashes)
    stem = args.results_dir / args.output
    stem.with_suffix(".json").write_text(json.dumps(out, indent=2) + "\n")
    stem.with_suffix(".md").write_text(report_md(out))
    for key, svg in figures.items():
        (args.results_dir / f"{args.output}_{key}.svg").write_text(svg)
    print(f"wrote {stem.with_suffix('.json')}, .md and {len(figures)} figure(s)")
    return out


if __name__ == "__main__":
    main()
