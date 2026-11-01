"""
Candidate entry signals for the live-rules simulator (#532 RAF, #529 weekly KAMA).

Each factory returns a ``SignalFn`` for ``live_rules_sim.compute_signals``: it gets the window
of bars strictly before the decision day (``High`` / ``Low`` / ``Close``, rescaled to the day's
split level) and ``frame.attrs["day"]``, and returns a decision dict. Nothing here reads past the
frame it is given, so a signal for day d can only use data through d - 1.

Rules are fixed from the issues' text; the plainest reading is taken where they leave a choice
(see scripts/research/raf_kama_live_rules.py for the list). No parameter here is tuned.

- RAF (Ready-Aim-Fire, #532): the pseudo-code of the issue. BUY when the fast stochastic (5, 3)
  has %K > %D, the medium (8, 5) and slow (17, 5) %K are above 50, the close is above TEMA(28)
  and the Fisher transform is positive; SELL on the mirror image; HOLD otherwise.
- Weekly KAMA (#529): a trend state (BULLISH / BEARISH / NEUTRAL) from weekly closes, used only
  once the week has closed: a decision on day d sees the weeks that ended before the week of d.
  Two parameter sets are in the issue and the research note, so both are kept (``KAMA_SPECS``).
"""

from typing import Callable, Dict

import numpy as np
import pandas as pd

from src.trading.instruments.indicators import calculate_fold_ma, calculate_kama

SignalFn = Callable[[str, pd.DataFrame], dict]

LIVE_WINDOW_DAYS = 90  # RealVoterStrategy's lookback, which the MACD and RSI voters keep
RAF_WINDOW_DAYS = 400  # calendar days of bars for RAF (about 275 bars: TEMA(28) warm-up)
RAF_MIN_BARS = 100

BULLISH, BEARISH, NEUTRAL = "BULLISH", "BEARISH", "NEUTRAL"

# Weekly KAMA parameter sets. "research" is #467's profile (weekly_kama.yaml) with its trend
# rule; "issue" is the parameter list of #529 with the rule of its Option A snippet.
KAMA_SPECS = {
    "research": {
        "lookback": 10,
        "fast": 2,
        "slow": 30,
        "rule": "slopes",  # KAMA slope and Fold-MA slope agree, price beyond both
        "fold_periods": 5,
        "fold_index": 2,
        "fold_slope_weeks": 3,
    },
    "issue": {
        "lookback": 6,
        "fast": 6,
        "slow": 7,
        "rule": "price_vs_kama",  # price vs KAMA, and KAMA rising over one week
    },
}


def _decision(action: str, size: float = 1.0, **extra) -> dict:
    return {"action": action, "position_size": size if action != "HOLD" else 0.0, **extra}


# --- RAF -------------------------------------------------------------------------------------


def stochastic(high: pd.Series, low: pd.Series, close: pd.Series, period: int, smooth: int):
    """%K = SMA(smooth) of the raw stochastic over ``period`` bars (50 when the range is zero)."""
    low_min = low.rolling(period).min()
    span = high.rolling(period).max() - low_min
    raw = (100 * (close - low_min) / span).where(span > 0, 50.0).where(low_min.notna())
    return raw.rolling(smooth).mean()


def tema(close: pd.Series, period: int = 28) -> pd.Series:
    """Triple exponential moving average, 3 EMA1 - 3 EMA2 + EMA3 (EMAs seeded at the first bar)."""
    e1 = close.ewm(span=period, adjust=False).mean()
    e2 = e1.ewm(span=period, adjust=False).mean()
    e3 = e2.ewm(span=period, adjust=False).mean()
    return 3 * e1 - 3 * e2 + e3


def fisher_transform(high: pd.Series, low: pd.Series, period: int = 10) -> pd.Series:
    """Ehlers' Fisher transform of the median price over ``period`` bars (0.33 / 0.67 smoothing)."""
    mid = ((high + low) / 2).to_numpy(dtype=float)
    hi = pd.Series(mid).rolling(period).max().to_numpy()
    lo = pd.Series(mid).rolling(period).min().to_numpy()
    out = np.full(len(mid), np.nan)
    value = fish = 0.0
    for i in range(period - 1, len(mid)):
        span = hi[i] - lo[i]
        x = (mid[i] - lo[i]) / span - 0.5 if span > 0 else 0.0
        value = min(max(0.66 * x + 0.67 * value, -0.999), 0.999)
        fish = 0.5 * np.log((1 + value) / (1 - value)) + 0.5 * fish
        out[i] = fish
    return pd.Series(out, index=high.index)


def raf_state(high: pd.Series, low: pd.Series, close: pd.Series) -> int:
    """+1 / -1 / 0: the RAF reading on the last bar of the series."""
    fast = stochastic(high, low, close, 5, 3)
    fast_d = fast.rolling(3).mean()
    med = stochastic(high, low, close, 8, 5)
    slow = stochastic(high, low, close, 17, 5)
    trend = tema(close, 28)
    fisher = fisher_transform(high, low, 10)
    k, d, m, s = fast.iloc[-1], fast_d.iloc[-1], med.iloc[-1], slow.iloc[-1]
    t, f, price = trend.iloc[-1], fisher.iloc[-1], close.iloc[-1]
    if any(pd.isna(v) for v in (k, d, m, s, t, f)):
        return 0
    if k > d and m > 50 and s > 50 and price > t and f > 0:
        return 1
    if k < d and m < 50 and s < 50 and price < t and f < 0:
        return -1
    return 0


def _raf_vote(frame: pd.DataFrame) -> int:
    day = frame.attrs.get("day")
    cut = frame if day is None else frame[frame.index >= day - pd.Timedelta(days=RAF_WINDOW_DAYS)]
    if len(cut) < RAF_MIN_BARS:
        return 0
    return raf_state(cut["High"], cut["Low"], cut["Close"])


def raf_signal_fn() -> SignalFn:
    """RAF alone: BUY when bullish, SELL when bearish."""

    def fn(symbol: str, frame: pd.DataFrame) -> dict:
        vote = _raf_vote(frame)
        return _decision({1: "BUY", -1: "SELL", 0: "HOLD"}[vote], reasoning="RAF")

    return fn


# --- voting ----------------------------------------------------------------------------------


def _voter_votes(live_fn: SignalFn, symbol: str, frame: pd.DataFrame) -> Dict[str, int]:
    """The live MACD and RSI voters' votes (+1 / -1 / 0), on the live 90-calendar-day window."""
    day = frame.attrs.get("day")
    window = (
        frame if day is None else frame[frame.index >= day - pd.Timedelta(days=LIVE_WINDOW_DAYS)]
    )
    result = live_fn(symbol, window[["Close"]])
    sign = {"BUY": 1, "SELL": -1, "HOLD": 0}
    return {v["name"].split("_")[0]: sign[v["action"]] for v in result.get("active_voters", [])}


def third_voter_fn(live_fn: SignalFn) -> SignalFn:
    """
    MACD, RSI and RAF under the rule proposed in #532: three agree is STRONG, two agree is
    MODERATE, fewer than two is HOLD. Two BUY votes make a BUY, two SELL votes a SELL.
    """

    def fn(symbol: str, frame: pd.DataFrame) -> dict:
        votes = list(_voter_votes(live_fn, symbol, frame).values()) + [_raf_vote(frame)]
        buys, sells = votes.count(1), votes.count(-1)
        if buys >= 2:
            return _decision("BUY", 1.0 if buys == 3 else 0.5)
        if sells >= 2:
            return _decision("SELL", 1.0 if sells == 3 else 0.5)
        return _decision("HOLD")

    return fn


# --- weekly KAMA -----------------------------------------------------------------------------


def completed_weekly_closes(close: pd.Series, day: pd.Timestamp) -> pd.Series:
    """
    Weekly closes (last close of each Monday-Sunday week) of the weeks that ended before ``day``'s
    week. The week containing ``day`` is dropped: on a Wednesday its Monday and Tuesday bars are
    there, but the week has not closed. A week is complete only when a later week has begun.
    """
    week = close.index.to_period("W")
    weekly = close.groupby(week).last()
    return weekly[weekly.index < pd.Timestamp(day).to_period("W")]


def kama_state(weekly: pd.Series, spec: dict) -> str:
    """BULLISH / BEARISH / NEUTRAL from the last weekly close and a KAMA parameter set."""
    lookback = spec["lookback"]
    need = lookback + 2
    if spec["rule"] == "slopes":
        need = max(lookback, spec["fold_periods"]) + 6 + 1
    if len(weekly) < need:
        return NEUTRAL
    price = float(weekly.iloc[-1])
    kama = calculate_kama(weekly, lookback, spec["fast"], spec["slow"], "1w")
    level = float(kama["kama"].iloc[-1])
    if spec["rule"] == "price_vs_kama":
        previous = float(kama["kama"].iloc[-2])
        if np.isnan(level) or np.isnan(previous):
            return NEUTRAL
        if price > level and level > previous:
            return BULLISH
        if price < level and level < previous:
            return BEARISH
        return NEUTRAL
    fold = calculate_fold_ma(weekly, spec["fold_periods"], spec["fold_index"])
    kama_slope = float(kama["slope"].iloc[-1])  # KAMA change over 6 weeks (indicators.py)
    fold_slope = float(fold.diff(spec["fold_slope_weeks"]).iloc[-1])
    fold_level = float(fold.iloc[-1])
    if any(np.isnan(v) for v in (level, kama_slope, fold_slope, fold_level)):
        return NEUTRAL
    if kama_slope > 0 and fold_slope > 0 and price > level and price > fold_level:
        return BULLISH
    if kama_slope < 0 and fold_slope < 0 and price < level and price < fold_level:
        return BEARISH
    return NEUTRAL


def kama_state_fn(spec_name: str) -> Callable[[str, pd.DataFrame], str]:
    """Weekly KAMA state of a symbol at the frame's decision day (cached per completed week)."""
    spec = KAMA_SPECS[spec_name]
    cache: Dict[tuple, str] = {}

    def state(symbol: str, frame: pd.DataFrame) -> str:
        day = frame.attrs["day"]
        weekly = completed_weekly_closes(frame["Close"], day)
        if weekly.empty:
            return NEUTRAL
        key = (symbol, weekly.index[-1])
        if key not in cache:
            cache[key] = kama_state(weekly, spec)
        return cache[key]

    return state


def kama_signal_fn(spec_name: str) -> SignalFn:
    """Weekly KAMA alone: BUY when BULLISH, SELL when BEARISH, HOLD when NEUTRAL."""
    state = kama_state_fn(spec_name)

    def fn(symbol: str, frame: pd.DataFrame) -> dict:
        s = state(symbol, frame)
        return _decision({BULLISH: "BUY", BEARISH: "SELL", NEUTRAL: "HOLD"}[s], reasoning=s)

    return fn


def kama_filter_fn(live_fn: SignalFn, spec_name: str) -> SignalFn:
    """
    The live voter signal with a weekly KAMA trend filter: a BUY goes through only while the
    weekly trend is BULLISH (a BEARISH or NEUTRAL week holds off new entries); SELLs and HOLDs
    are the voter's own. The filter never sells by itself.
    """
    state = kama_state_fn(spec_name)

    def fn(symbol: str, frame: pd.DataFrame) -> dict:
        day = frame.attrs.get("day")
        window = (
            frame
            if day is None
            else frame[frame.index >= day - pd.Timedelta(days=LIVE_WINDOW_DAYS)]
        )
        result = live_fn(symbol, window[["Close"]])
        if result.get("action") == "BUY" and state(symbol, frame) != BULLISH:
            return _decision("HOLD", reasoning="weekly KAMA filter")
        return result

    return fn
