"""
Market regime detection for research (#672).

- `wk_means`: Wasserstein k-means on windows of returns, with the moment k-means and
  volatility k-means baselines, and the paper's accuracy scores (#673).
- `synthetic`: the paper's regime-switching GBM and Merton paths, for known-answer checks.
"""

from .synthetic import regime_switching_returns
from .wk_means import (
    MomentKMeans,
    VolatilityKMeans,
    Windows,
    WKMeans,
    accuracy_scores,
    barycenter,
    lift,
    wasserstein,
    window_votes,
)

__all__ = [
    "MomentKMeans",
    "VolatilityKMeans",
    "WKMeans",
    "Windows",
    "accuracy_scores",
    "barycenter",
    "lift",
    "regime_switching_returns",
    "wasserstein",
    "window_votes",
]
