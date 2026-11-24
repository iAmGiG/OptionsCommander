"""
Regime-switching paths from the WK-means paper's synthetic experiments (Section 3.3, #673).

Horvath, Issa and Muguruza, "Clustering market regimes using the Wasserstein distance",
arXiv:2110.11848.

Twenty years of hourly steps, dt = 1 / (252 * 7). The path follows the "bull" parameters except
on ten non-overlapping half-year intervals, where it follows the "bear" ones. The paper draws the
interval starts at random under s_i + l_i + 2 < s_{i+1}; here they are uniform over every
placement that keeps that gap. Log returns are simulated directly:

- GBM (equation 35): (mu - sigma^2/2) dt + sigma sqrt(dt) Z.
- Merton jump diffusion (equations 36-40): the same plus a Poisson(lambda dt) number of jumps
  whose log sizes are Normal(gamma, delta^2). There is no jump compensator, so the mean is
  (mu - sigma^2/2 + lambda gamma) dt and the variance (sigma^2 + lambda (delta^2 + gamma^2)) dt.

The parameters are the paper's. GBM (mu, sigma): bull (0.02, 0.2), bear (-0.02, 0.3). Merton
(mu, sigma, lambda, gamma, delta): bull (0.05, 0.2, 5, 0.02, 0.0125), bear
(-0.05, 0.4, 10, -0.04, 0.1). Rates are per year (lambda in jumps per year).
"""

from typing import Tuple

import numpy as np

STEPS_PER_YEAR = 252 * 7  # hourly: seven returns a trading day
MIN_GAP = 3  # s_i + l_i + 2 < s_{i+1}

GBM_BULL = (0.02, 0.2)
GBM_BEAR = (-0.02, 0.3)
MERTON_BULL = (0.05, 0.2, 5.0, 0.02, 0.0125)
MERTON_BEAR = (-0.05, 0.4, 10.0, -0.04, 0.1)

MODELS = {"gbm": (GBM_BULL, GBM_BEAR), "merton": (MERTON_BULL, MERTON_BEAR)}


def regime_intervals(
    n_steps: int, n_changes: int, length: int, rng: np.random.Generator
) -> np.ndarray:
    """Start and end (exclusive) of n_changes intervals of `length` steps, MIN_GAP apart.

    Uniform over all placements: the free steps are split at sorted uniform draws.
    """
    free = n_steps - n_changes * length - (n_changes - 1) * MIN_GAP
    if free < 0:
        raise ValueError("the intervals do not fit in the path")
    offsets = np.sort(rng.integers(0, free + 1, size=n_changes))
    starts = offsets + np.arange(n_changes) * (length + MIN_GAP)
    return np.column_stack([starts, starts + length])


def gbm_log_returns(n: int, dt: float, mu: float, sigma: float, rng) -> np.ndarray:
    """Log returns of geometric Brownian motion over n steps of dt."""
    return (mu - 0.5 * sigma**2) * dt + sigma * np.sqrt(dt) * rng.standard_normal(n)


def merton_log_returns(
    n: int, dt: float, mu: float, sigma: float, lam: float, gamma: float, delta: float, rng
) -> np.ndarray:
    """Log returns of Merton's jump diffusion (normal log jumps, no compensator)."""
    jumps = rng.poisson(lam * dt, size=n)
    jump_part = gamma * jumps + delta * np.sqrt(jumps) * rng.standard_normal(n)
    return gbm_log_returns(n, dt, mu, sigma, rng) + jump_part


def _simulate(model: str, n: int, dt: float, params, rng) -> np.ndarray:
    if model == "gbm":
        return gbm_log_returns(n, dt, *params, rng)
    return merton_log_returns(n, dt, *params, rng)


def regime_switching_returns(
    model: str,
    seed: int,
    years: float = 20,
    n_changes: int = 10,
    change_years: float = 0.5,
) -> Tuple[np.ndarray, np.ndarray]:
    """(log returns, in_regime) for one path; in_regime marks the bear-parameter steps."""
    if model not in MODELS:
        raise ValueError(f"model must be one of {sorted(MODELS)}, got {model!r}")
    rng = np.random.default_rng(seed)
    n = int(round(years * STEPS_PER_YEAR))
    dt = 1.0 / STEPS_PER_YEAR
    length = int(round(change_years * STEPS_PER_YEAR))
    in_regime = np.zeros(n, dtype=bool)
    for start, end in regime_intervals(n, n_changes, length, rng):
        in_regime[start:end] = True
    bull, bear = MODELS[model]
    returns = np.where(
        in_regime, _simulate(model, n, dt, bear, rng), _simulate(model, n, dt, bull, rng)
    )
    return returns, in_regime
