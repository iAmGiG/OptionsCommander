"""
Wasserstein k-means (WK-means) market regimes, with two baselines (#673).

Horvath, Issa and Muguruza, "Clustering market regimes using the Wasserstein distance",
Journal of Computational Finance (2024), doi:10.21314/JCF.2024.005; preprint arXiv:2110.11848.

A return series is cut into overlapping windows (the paper's lift, Definition 1.2). A window
holds h1 consecutive log returns and starts h1 - h2 returns after the one before, so h2 is the
overlap: the paper's (35, 28) on hourly returns is a one-week window moved forward one day.
(The paper's own formula for the window starts in Definition 1.2 does not match that; its prose
and its "adjacent partitions within one day" do.) Each window is treated as the empirical
distribution of its returns and kept as its sorted values. For two windows of the same size the
p-Wasserstein distance is then the mean over the sorted values,

    W_p = (mean_i |a_(i) - b_(i)|^p)^(1/p),

and k-means runs in that space. A cluster's centroid is its Wasserstein barycenter: the
coordinate-wise median of the sorted windows for p = 1 (Proposition 2.6; it need not be unique)
and the coordinate-wise mean for p = 2 (Remark C.2; for one-dimensional measures this is the
exact W_2 barycenter). Only p = 1 and p = 2 are supported. The paper does not say which p its
experiments use.

Sorting discards the order of the returns inside a window: the method sees each window's
distribution of returns, never its path.

Baselines:

- MomentKMeans (MK-means, the paper's Section 3.1.1): each window becomes the vector of its
  first n raw moments scaled by 1/j! (equation 24), each coordinate is standardised over the
  windows, and Euclidean k-means runs on that. The paper does not say how many moments it uses.
- VolatilityKMeans: k-means on the log standard deviation of each window alone. Not in the paper:
  it shows how much of a WK-means clustering is plain volatility.

Clusters are numbered by the mean variance of their member windows, lowest first, so for k = 2
label 0 is the calm regime and label 1 the volatile one. The paper fixes the same convention by
assumption ("cluster 1 is the standard regime").

Initialisation: "random" draws k windows, as in the paper's Algorithm 1; "kmeans++" draws each
new centroid with probability proportional to its distance to the nearest centroid already
chosen, raised to the power of the cost (p for WK-means, 2 for Euclidean). With n_init > 1 the
run with the lowest cost is kept: the sum of W_p^p to the centroids for WK-means (which the
barycenter step minimises), the sum of squared distances otherwise. A run stops when the
centroids move less than `tol` in total (the paper's equation 23) or no label changes.
"""

import math
from dataclasses import dataclass
from typing import Callable, Optional, Union

import numpy as np

SUPPORTED_P = (1, 2)
INITS = ("random", "kmeans++")


@dataclass(frozen=True)
class Windows:
    """Return windows, each sorted, and where each starts in the return series."""

    values: np.ndarray  # (m, h1), each row sorted ascending
    start: np.ndarray  # (m,), index of each window's first return
    h1: int
    h2: int

    @property
    def end(self) -> np.ndarray:
        """Index one past each window's last return."""
        return self.start + self.h1

    def __len__(self) -> int:
        return len(self.values)


def lift(returns, h1: int, h2: int) -> Windows:
    """Cut a return series into windows of h1 returns that overlap by h2 (Definition 1.2).

    Windows start every h1 - h2 returns from the first; returns after the last full window are
    left out.
    """
    x = np.asarray(returns, dtype=float)
    if x.ndim != 1:
        raise ValueError("returns must be one-dimensional")
    if not np.all(np.isfinite(x)):
        raise ValueError("returns contain NaN or infinite values")
    if h1 < 2 or not 0 <= h2 < h1:
        raise ValueError(f"need h1 >= 2 and 0 <= h2 < h1, got h1={h1}, h2={h2}")
    if len(x) < h1:
        raise ValueError(f"{len(x)} returns are fewer than one window of {h1}")
    start = np.arange(0, len(x) - h1 + 1, h1 - h2)
    values = np.sort(x[start[:, None] + np.arange(h1)], axis=1)
    return Windows(values=values, start=start, h1=h1, h2=h2)


def _check_p(p: int) -> None:
    if p not in SUPPORTED_P:
        raise ValueError(f"p must be 1 or 2, got {p}")


def wasserstein(a, b, p: int = 1) -> float:
    """W_p between two samples of the same size, in any order."""
    _check_p(p)
    a = np.sort(np.asarray(a, dtype=float))
    b = np.sort(np.asarray(b, dtype=float))
    if a.shape != b.shape or a.ndim != 1:
        raise ValueError("samples must be one-dimensional and of the same size")
    return float(np.mean(np.abs(a - b) ** p) ** (1.0 / p))


def wasserstein_to_centroids(values: np.ndarray, centroids: np.ndarray, p: int) -> np.ndarray:
    """W_p from each sorted window (a row of values) to each sorted centroid: shape (m, k)."""
    diff = np.abs(values[:, None, :] - centroids[None, :, :])
    return np.mean(diff**p, axis=2) ** (1.0 / p)


def barycenter(values, p: int) -> np.ndarray:
    """W_p barycenter of sorted windows (rows): the coordinate-wise median (p=1) or mean (p=2).

    Both keep the result sorted, so it is a valid window itself.
    """
    _check_p(p)
    values = np.asarray(values, dtype=float)
    return np.median(values, axis=0) if p == 1 else values.mean(axis=0)


def _euclidean(points: np.ndarray, centers: np.ndarray) -> np.ndarray:
    return np.sqrt(((points[:, None, :] - centers[None, :, :]) ** 2).sum(axis=2))


@dataclass
class _Run:
    centers: np.ndarray
    labels: np.ndarray
    cost: float
    n_iter: int
    converged: bool


Distance = Callable[[np.ndarray, np.ndarray], np.ndarray]


def _initial_centers(points, k, rng, init, distance: Distance, power) -> np.ndarray:
    m = len(points)
    if init == "random":  # the paper's Algorithm 1: k draws from the windows
        return points[rng.choice(m, size=k, replace=False)].copy()
    chosen = [int(rng.integers(m))]
    for _ in range(1, k):
        weight = distance(points, points[chosen]).min(axis=1) ** power
        total = weight.sum()
        chosen.append(int(rng.choice(m, p=weight / total)) if total > 0 else int(rng.integers(m)))
    return points[chosen].copy()


def _update_centers(points, labels, distances, centers, center: Callable) -> np.ndarray:
    """New centers from the members; an empty cluster restarts at the worst-fitting point."""
    new = centers.copy()
    for j in range(len(centers)):
        members = labels == j
        if members.any():
            new[j] = center(points[members])
        else:
            worst = int(distances[np.arange(len(points)), labels].argmax())
            labels[worst] = j
            new[j] = points[worst]
    return new


def _lloyd(points, k, *, distance: Distance, center, power, rng, init, max_iter, tol) -> _Run:
    centers = _initial_centers(points, k, rng, init, distance, power)
    labels = np.full(len(points), -1)
    converged, n_iter = False, 0
    for n_iter in range(1, max_iter + 1):
        d = distance(points, centers)
        new_labels = d.argmin(axis=1)
        new_centers = _update_centers(points, new_labels, d, centers, center)
        shift = float(np.trace(distance(centers, new_centers)))  # the paper's equation 23
        unchanged = np.array_equal(new_labels, labels)
        centers, labels = new_centers, new_labels
        if shift < tol or unchanged:
            converged = True
            break
    d = distance(points, centers)
    labels = d.argmin(axis=1)
    cost = float(np.sum(d[np.arange(len(points)), labels] ** power))
    return _Run(centers, labels, cost, n_iter, converged)


def _window_values(windows: Union[Windows, np.ndarray]) -> np.ndarray:
    values = windows.values if isinstance(windows, Windows) else np.asarray(windows, dtype=float)
    if values.ndim != 2:
        raise ValueError("windows must be a Windows object or a 2-D array, one window per row")
    return np.sort(values, axis=1)


def _variance_order(values: np.ndarray, labels: np.ndarray, k: int) -> np.ndarray:
    """Cluster numbers sorted by the mean variance of their member windows, lowest first."""
    variances = values.var(axis=1)
    means = [variances[labels == j].mean() if (labels == j).any() else np.inf for j in range(k)]
    return np.argsort(means, kind="stable")


class _KMeans:
    """Lloyd's k-means with restarts, over points the subclass derives from the windows."""

    power = 2

    def __init__(
        self,
        k: int = 2,
        n_init: int = 10,
        max_iter: int = 300,
        tol: float = 1e-10,
        init: str = "kmeans++",
        seed: Optional[int] = None,
    ):
        if k < 1 or n_init < 1 or max_iter < 1:
            raise ValueError("k, n_init and max_iter must be at least 1")
        if init not in INITS:
            raise ValueError(f"init must be one of {INITS}, got {init!r}")
        self.k, self.n_init, self.max_iter, self.tol = k, n_init, max_iter, tol
        self.init, self.seed = init, seed

    # Subclasses define the space.
    def _points(self, values: np.ndarray, fitting: bool) -> np.ndarray:
        raise NotImplementedError

    def _distance(self, points: np.ndarray, centers: np.ndarray) -> np.ndarray:
        return _euclidean(points, centers)

    def _center(self, members: np.ndarray) -> np.ndarray:
        return members.mean(axis=0)

    def fit(self, windows):
        """Cluster the windows; sets labels_, centers_, cost_, n_iter_ and converged_."""
        values = _window_values(windows)
        if len(values) < self.k:
            raise ValueError(f"{len(values)} windows are fewer than k = {self.k}")
        points = self._points(values, fitting=True)
        rng = np.random.default_rng(self.seed)
        best = None
        for _ in range(self.n_init):
            run = _lloyd(
                points,
                self.k,
                distance=self._distance,
                center=self._center,
                power=self.power,
                rng=rng,
                init=self.init,
                max_iter=self.max_iter,
                tol=self.tol,
            )
            if best is None or run.cost < best.cost:
                best = run
        order = _variance_order(values, best.labels, self.k)
        rank = np.empty(self.k, dtype=int)
        rank[order] = np.arange(self.k)
        self.centers_ = best.centers[order]
        self.labels_ = rank[best.labels]
        self.cost_, self.n_iter_, self.converged_ = best.cost, best.n_iter, best.converged
        return self

    def predict(self, windows) -> np.ndarray:
        """Label windows by their nearest fitted centroid."""
        points = self._points(_window_values(windows), fitting=False)
        return self._distance(points, self.centers_).argmin(axis=1)


class WKMeans(_KMeans):
    """Wasserstein k-means on sorted return windows (the paper's Algorithm 1)."""

    def __init__(self, k: int = 2, p: int = 1, **kwargs):
        _check_p(p)
        super().__init__(k=k, **kwargs)
        self.p = p
        self.power = p

    def _points(self, values, fitting):
        return values

    def _distance(self, points, centers):
        return wasserstein_to_centroids(points, centers, self.p)

    def _center(self, members):
        return barycenter(members, self.p)


def moment_features(values: np.ndarray, n_moments: int) -> np.ndarray:
    """Raw moments 1..n of each window, the j-th scaled by 1/j! (the paper's equation 24)."""
    values = np.asarray(values, dtype=float)
    return np.column_stack(
        [np.mean(values**j, axis=1) / math.factorial(j) for j in range(1, n_moments + 1)]
    )


class _StandardisedKMeans(_KMeans):
    """Euclidean k-means on features standardised with the fit sample's mean and spread."""

    def _features(self, values: np.ndarray) -> np.ndarray:
        raise NotImplementedError

    def _points(self, values, fitting):
        features = self._features(values)
        if fitting:
            self.feature_mean_ = features.mean(axis=0)
            spread = features.std(axis=0)
            self.feature_std_ = np.where(spread > 0, spread, 1.0)
        return (features - self.feature_mean_) / self.feature_std_


class MomentKMeans(_StandardisedKMeans):
    """Moment k-means (MK-means): Euclidean k-means on standardised raw moments."""

    def __init__(self, k: int = 2, n_moments: int = 4, **kwargs):
        if n_moments < 1:
            raise ValueError("n_moments must be at least 1")
        super().__init__(k=k, **kwargs)
        self.n_moments = n_moments

    def _features(self, values):
        return moment_features(values, self.n_moments)


class VolatilityKMeans(_StandardisedKMeans):
    """k-means on the log standard deviation of each window: volatility and nothing else."""

    def _features(self, values):
        spread = values.std(axis=1)
        floor = np.finfo(float).tiny
        return np.log(np.maximum(spread, floor))[:, None]


def window_votes(windows: Windows, labels, n_returns: int, k: int) -> np.ndarray:
    """For each return, how many of the windows that contain it carry each label: (n_returns, k).

    A return can sit in up to ceil(h1 / (h1 - h2)) windows; returns after the last window get no
    votes.
    """
    labels = np.asarray(labels)
    if len(labels) != len(windows):
        raise ValueError("one label per window is needed")
    if len(windows) and windows.end[-1] > n_returns:
        raise ValueError("the windows reach past n_returns")
    marks = np.zeros((n_returns + 1, k))
    for j in range(k):
        chosen = labels == j
        np.add.at(marks[:, j], windows.start[chosen], 1.0)
        np.add.at(marks[:, j], windows.end[chosen], -1.0)
    return np.cumsum(marks, axis=0)[:-1]


def accuracy_scores(votes: np.ndarray, in_regime) -> dict:
    """The paper's ROFS, RONS and TA (Definition 3.7, equations 31-33), for k = 2.

    Truth is per return: in_regime marks the returns drawn from the regime-change parameters.
    Label 0 counts as the standard regime and label 1 as the regime change. Each return
    contributes the votes of every window that contains it; returns no window covers are
    left out. ROFS is the share of correct votes on standard returns, RONS on regime-change
    returns, TA on all of them.
    """
    votes = np.asarray(votes, dtype=float)
    if votes.ndim != 2 or votes.shape[1] != 2:
        raise ValueError("accuracy_scores needs votes for k = 2")
    in_regime = np.asarray(in_regime, dtype=bool)
    covered = votes.sum(axis=1) > 0
    off, on = covered & ~in_regime, covered & in_regime
    right_off, right_on = votes[off, 0].sum(), votes[on, 1].sum()

    def share(right, rows):
        total = votes[rows].sum()
        return float(right / total) if total > 0 else float("nan")

    return {
        "rofs": share(right_off, off),
        "rons": share(right_on, on),
        "ta": share(right_off + right_on, covered),
    }
