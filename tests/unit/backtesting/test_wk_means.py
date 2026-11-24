"""WK-means, its baselines, the paper's scores and its synthetic paths (#673)."""

import math

import numpy as np
import pytest
from scipy import stats

from src.backtesting.regimes.synthetic import (
    MERTON_BEAR,
    MERTON_BULL,
    MIN_GAP,
    STEPS_PER_YEAR,
    gbm_log_returns,
    merton_log_returns,
    regime_intervals,
    regime_switching_returns,
)
from src.backtesting.regimes.wk_means import (
    MomentKMeans,
    VolatilityKMeans,
    Windows,
    WKMeans,
    accuracy_scores,
    barycenter,
    lift,
    moment_features,
    wasserstein,
    wasserstein_to_centroids,
    window_votes,
)

# --- the lift --------------------------------------------------------------------------------


def test_lift_starts_a_window_every_h1_minus_h2_returns_and_sorts_it():
    x = np.arange(100.0)[::-1]  # descending, so sorting is visible
    w = lift(x, h1=10, h2=7)
    assert len(w) == (100 - 10) // 3 + 1 == 31
    assert list(w.start[:3]) == [0, 3, 6]
    assert w.end[-1] <= 100
    np.testing.assert_array_equal(w.values[1], np.sort(x[3:13]))
    assert np.all(np.diff(w.values, axis=1) >= 0)


def test_the_paper_window_overlaps_by_h2_and_moves_one_day():
    w = lift(np.zeros(7 * 30), h1=35, h2=28)  # hourly: 7 returns a day
    assert w.start[1] - w.start[0] == 7
    assert w.end[0] - w.start[1] == 28


@pytest.mark.parametrize("h1,h2", [(1, 0), (10, 10), (10, -1), (10, 12)])
def test_lift_rejects_bad_windows(h1, h2):
    with pytest.raises(ValueError):
        lift(np.zeros(50), h1, h2)


def test_lift_rejects_short_or_missing_data():
    with pytest.raises(ValueError):
        lift(np.zeros(5), 10, 5)
    with pytest.raises(ValueError):
        lift(np.array([0.0, np.nan, 1.0, 2.0]), 2, 1)


# --- distances and barycenters --------------------------------------------------------------


def test_wasserstein_known_answers():
    assert wasserstein([0, 1, 2], [1, 2, 3], p=1) == pytest.approx(1.0)
    assert wasserstein([0, 1, 2], [1, 2, 3], p=2) == pytest.approx(1.0)
    # all the mass difference sits in one of four atoms
    assert wasserstein([0, 0, 0, 4], [0, 0, 0, 0], p=1) == pytest.approx(1.0)
    assert wasserstein([0, 0, 0, 4], [0, 0, 0, 0], p=2) == pytest.approx(2.0)
    # order inside a sample does not matter
    assert wasserstein([2, 0, 1], [3, 1, 2], p=1) == pytest.approx(1.0)


def test_w1_matches_scipy_on_random_samples():
    rng = np.random.default_rng(3)
    for _ in range(20):
        a, b = rng.standard_t(3, size=40), rng.normal(0.5, 2.0, size=40)
        assert wasserstein(a, b, p=1) == pytest.approx(stats.wasserstein_distance(a, b))


def test_w2_between_normals_matches_the_closed_form():
    # W2(N(m1, s1^2), N(m2, s2^2)) = sqrt((m1 - m2)^2 + (s1 - s2)^2); quantile samples, n large
    q = (np.arange(1, 4001) - 0.5) / 4000
    a, b = stats.norm.ppf(q, 0.1, 1.0), stats.norm.ppf(q, -0.2, 2.5)
    assert wasserstein(a, b, p=2) == pytest.approx(math.hypot(0.3, 1.5), rel=5e-3)


def test_distance_to_centroids_matches_the_pairwise_function():
    rng = np.random.default_rng(1)
    w = np.sort(rng.normal(size=(6, 9)), axis=1)
    c = np.sort(rng.normal(size=(2, 9)), axis=1)
    for p in (1, 2):
        d = wasserstein_to_centroids(w, c, p)
        assert d.shape == (6, 2)
        assert d[4, 1] == pytest.approx(wasserstein(w[4], c[1], p))


def test_only_p_1_and_2_are_supported():
    with pytest.raises(ValueError):
        wasserstein([0, 1], [1, 2], p=3)
    with pytest.raises(ValueError):
        WKMeans(p=3)


@pytest.mark.parametrize("p", [1, 2])
def test_the_barycenter_of_identical_windows_is_the_window(p):
    window = np.sort(np.random.default_rng(0).normal(size=12))
    np.testing.assert_allclose(barycenter(np.tile(window, (5, 1)), p), window)


@pytest.mark.parametrize("p", [1, 2])
def test_the_barycenter_minimises_the_cluster_cost(p):
    rng = np.random.default_rng(p)
    members = np.sort(rng.standard_t(4, size=(15, 10)), axis=1)
    center = barycenter(members, p)
    assert np.all(np.diff(center) >= 0)  # still a valid (sorted) window

    def cost(c):
        return float(np.sum(wasserstein_to_centroids(members, c[None, :], p) ** p))

    best = cost(center)
    for _ in range(50):
        trial = np.sort(center + rng.normal(scale=0.05, size=center.shape))
        assert cost(trial) >= best - 1e-12


# --- clustering ------------------------------------------------------------------------------


def _two_volatility_regimes(seed=0):
    rng = np.random.default_rng(seed)
    x = np.concatenate([rng.normal(0, 1, 2000), rng.normal(0, 3, 1000), rng.normal(0, 1, 2000)])
    truth = np.zeros(len(x), dtype=bool)
    truth[2000:3000] = True
    return x, truth


@pytest.mark.parametrize(
    "make,min_ta,min_rons",
    [
        (lambda: WKMeans(p=1, seed=0), 0.95, 0.9),
        (lambda: WKMeans(p=2, seed=0), 0.95, 0.9),
        (lambda: VolatilityKMeans(seed=0), 0.95, 0.9),
        # raw moments are dominated by a few extreme windows: high-volatility windows are
        # missed (the paper's MK-means regime-on score is 74.8% on GBM)
        (lambda: MomentKMeans(seed=0), 0.85, 0.5),
    ],
    ids=["wk_p1", "wk_p2", "vol", "mk"],
)
def test_two_well_separated_regimes_are_recovered(make, min_ta, min_rons):
    x, truth = _two_volatility_regimes()
    w = lift(x, 20, 10)
    model = make().fit(w)
    scores = accuracy_scores(window_votes(w, model.labels_, len(x), 2), truth)
    assert scores["ta"] > min_ta
    assert scores["rons"] > min_rons


def test_label_1_is_the_more_volatile_cluster():
    x, truth = _two_volatility_regimes(seed=4)
    w = lift(x, 20, 10)
    model = WKMeans(p=1, seed=7).fit(w)
    var = w.values.var(axis=1)
    assert var[model.labels_ == 1].mean() > var[model.labels_ == 0].mean()
    assert np.std(model.centers_[1]) > np.std(model.centers_[0])


def test_fit_is_reproducible_for_a_seed_and_predict_matches_it():
    x, _ = _two_volatility_regimes(seed=2)
    w = lift(x, 20, 10)
    a = WKMeans(p=2, seed=11).fit(w)
    b = WKMeans(p=2, seed=11).fit(w)
    np.testing.assert_array_equal(a.labels_, b.labels_)
    np.testing.assert_allclose(a.centers_, b.centers_)
    np.testing.assert_array_equal(a.predict(w), a.labels_)
    assert a.converged_


def test_the_cost_is_the_sum_of_w_p_to_the_assigned_centroids():
    x, _ = _two_volatility_regimes(seed=5)
    w = lift(x, 20, 10)
    for p in (1, 2):
        m = WKMeans(p=p, seed=0).fit(w)
        d = wasserstein_to_centroids(w.values, m.centers_, p)
        assert m.cost_ == pytest.approx(np.sum(d[np.arange(len(w)), m.labels_] ** p))


def test_more_restarts_never_give_a_higher_cost():
    x, _ = _two_volatility_regimes(seed=6)
    w = lift(x, 15, 5)
    one = WKMeans(k=3, p=1, n_init=1, init="random", seed=0).fit(w)
    ten = WKMeans(k=3, p=1, n_init=10, init="random", seed=0).fit(w)
    assert ten.cost_ <= one.cost_ + 1e-12  # the first of the ten runs is the single run


def test_empty_clusters_are_refilled_without_emptying_a_donor_or_sharing_a_point():
    from src.backtesting.regimes.wk_means import _refill_empty

    # k = 4: clusters 2 and 3 are empty; cluster 1 has one member and must keep it
    labels = np.array([0, 0, 0, 1])
    distances = np.array(
        [[0.1, 9, 9, 9], [0.5, 9, 9, 9], [0.9, 9, 9, 9], [9, 5.0, 9, 9]], dtype=float
    )  # point 3 fits worst, but it is cluster 1's only member
    _refill_empty(labels, distances, 4)
    assert sorted(np.bincount(labels, minlength=4)) == [1, 1, 1, 1]
    assert labels[3] == 1  # the single member stayed
    assert set(labels[[1, 2]]) == {2, 3}  # the two worst fits of cluster 0, one each


def test_duplicate_starting_windows_still_give_k_clusters():
    # many identical windows: a random start can pick the same window twice
    rng = np.random.default_rng(0)
    values = np.sort(np.vstack([np.zeros((30, 5)), rng.normal(size=(6, 5))]), axis=1)
    for seed in range(10):
        model = WKMeans(k=3, p=1, init="random", n_init=1, seed=seed).fit(values)
        assert len(set(model.labels_)) == 3


def test_bad_settings_are_rejected():
    with pytest.raises(ValueError):
        WKMeans(init="farthest")
    with pytest.raises(ValueError):
        WKMeans(k=0)
    with pytest.raises(ValueError):
        WKMeans(k=5).fit(np.zeros((3, 4)))


def test_moment_features_are_raw_moments_over_factorials():
    f = moment_features(np.array([[1.0, 2.0, 3.0]]), 3)
    np.testing.assert_allclose(f[0], [2.0, (14 / 3) / 2, (36 / 3) / 6])


def test_moment_kmeans_standardises_with_the_fit_sample():
    x, _ = _two_volatility_regimes(seed=8)
    w = lift(x, 20, 10)
    m = MomentKMeans(n_moments=4, seed=0).fit(w)
    z = (moment_features(w.values, 4) - m.feature_mean_) / m.feature_std_
    np.testing.assert_allclose(z.mean(axis=0), 0, atol=1e-9)
    np.testing.assert_array_equal(m.predict(w), m.labels_)


def test_windows_can_be_passed_as_a_plain_array():
    x, _ = _two_volatility_regimes(seed=9)
    w = lift(x, 20, 10)
    a = WKMeans(p=1, seed=3).fit(w)
    b = WKMeans(p=1, seed=3).fit(w.values[:, ::-1])  # unsorted rows are sorted on the way in
    np.testing.assert_array_equal(a.labels_, b.labels_)


# --- votes and the paper's scores ------------------------------------------------------------


def test_votes_and_scores_on_a_hand_worked_example():
    # six returns, windows [0, 3), [2, 5), [3, 6) labelled 0, 1, 1; returns 3-5 are the regime
    w = Windows(values=np.zeros((3, 3)), start=np.array([0, 2, 3]), h1=3, h2=2)
    votes = window_votes(w, np.array([0, 1, 1]), 6, 2)
    np.testing.assert_array_equal(votes, [[1, 0], [1, 0], [1, 1], [0, 2], [0, 2], [0, 1]])
    s = accuracy_scores(votes, np.array([0, 0, 0, 1, 1, 1], dtype=bool))
    assert s["rofs"] == pytest.approx(3 / 4)  # returns 0-2: 3 of 4 votes say standard
    assert s["rons"] == pytest.approx(1.0)  # returns 3-5: 5 of 5 votes say regime change
    assert s["ta"] == pytest.approx(8 / 9)


def test_returns_no_window_covers_are_left_out_of_the_scores():
    w = Windows(values=np.zeros((1, 2)), start=np.array([0]), h1=2, h2=0)
    votes = window_votes(w, np.array([1]), 4, 2)
    assert votes[2:].sum() == 0
    s = accuracy_scores(votes, np.array([1, 1, 0, 0], dtype=bool))
    assert s["ta"] == 1.0 and math.isnan(s["rofs"])


def test_votes_need_one_label_per_window_that_fits():
    w = Windows(values=np.zeros((2, 3)), start=np.array([0, 2]), h1=3, h2=1)
    with pytest.raises(ValueError):
        window_votes(w, np.array([0]), 5, 2)
    with pytest.raises(ValueError):
        window_votes(w, np.array([0, 1]), 4, 2)


# --- synthetic paths -------------------------------------------------------------------------


def test_regime_intervals_fit_and_keep_the_papers_gap():
    for seed in range(200):
        rng = np.random.default_rng(seed)
        iv = regime_intervals(35_280, 10, 882, rng)
        assert iv.shape == (10, 2)
        assert np.all(iv[:, 1] - iv[:, 0] == 882)
        assert iv[0, 0] >= 0 and iv[-1, 1] <= 35_280
        assert np.all(iv[1:, 0] - iv[:-1, 1] >= MIN_GAP)  # s_i + l_i + 2 < s_(i+1)


def test_regime_intervals_that_cannot_fit_are_refused():
    with pytest.raises(ValueError):
        regime_intervals(100, 3, 40, np.random.default_rng(0))


def test_merton_returns_have_the_papers_mean_and_variance():
    mu, sigma, lam, gamma, delta = MERTON_BEAR
    dt, n = 1 / STEPS_PER_YEAR, 2_000_000
    r = merton_log_returns(n, dt, mu, sigma, lam, gamma, delta, np.random.default_rng(0))
    mean = (mu - sigma**2 / 2 + lam * gamma) * dt  # equation 39
    var = (sigma**2 + lam * (delta**2 + gamma**2)) * dt  # equation 40
    assert r.var() == pytest.approx(var, rel=0.03)
    assert abs(r.mean() - mean) < 4 * math.sqrt(var / n)


def test_gbm_returns_have_the_lognormal_mean_and_variance():
    dt, n = 1 / STEPS_PER_YEAR, 1_000_000
    r = gbm_log_returns(n, dt, 0.02, 0.3, np.random.default_rng(1))
    assert r.var() == pytest.approx(0.09 * dt, rel=0.01)
    assert abs(r.mean() - (0.02 - 0.045) * dt) < 4 * math.sqrt(0.09 * dt / n)


def test_regime_switching_path_marks_a_quarter_of_the_steps():
    r, in_regime = regime_switching_returns("merton", seed=0)
    assert len(r) == 20 * STEPS_PER_YEAR
    assert in_regime.mean() == pytest.approx(0.25)  # 10 half-years out of 20 years
    assert r[in_regime].std() > 2 * r[~in_regime].std()  # bear: 0.4 vol and more jumps
    again, _ = regime_switching_returns("merton", seed=0)
    np.testing.assert_array_equal(r, again)
    assert MERTON_BULL[2] == 5.0  # lambda is jumps per year


def test_wk_means_finds_the_merton_regime_change():
    # the paper's headline case, one path: WK-means tells the regimes apart
    r, in_regime = regime_switching_returns("merton", seed=3)
    w = lift(r, 35, 28)
    model = WKMeans(p=1, seed=0).fit(w)
    scores = accuracy_scores(window_votes(w, model.labels_, len(r), 2), in_regime)
    assert scores["ta"] > 0.85


def test_unknown_model_is_refused():
    with pytest.raises(ValueError):
        regime_switching_returns("heston", seed=0)
