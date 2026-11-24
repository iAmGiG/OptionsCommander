"""Known-answer tests for the Newey-West helpers in research_cli (#576).

``paired_return_test`` and ``newey_west_diff`` supply the t-statistics and p-values that
``multiple_testing_audit.py`` corrects across the overlay, exit-rule, morning-raise and
partial-exit studies. The answers here come from arithmetic done by hand, from a numpy
Bartlett-kernel sandwich written below, and from the plain Welch form at zero lags; none of
them call statsmodels.
"""

import numpy as np
import pandas as pd
import pytest
from scipy import stats

from src.backtesting.research_cli import newey_west_diff, paired_return_test


def _returns(seed, n=300, mean=0.0005, sd=0.01, rho=0.0):
    """Daily returns on business days; ``rho`` adds AR(1) autocorrelation."""
    rng = np.random.default_rng(seed)
    shocks = rng.normal(0.0, sd, n)
    r = np.empty(n)
    r[0] = shocks[0]
    for i in range(1, n):
        r[i] = rho * r[i - 1] + shocks[i]
    return pd.Series(r + mean, index=pd.bdate_range("2015-01-01", periods=n))


def _lag_rule(n):
    return int(np.floor(4 * (n / 100) ** (2 / 9)))


def _bartlett_hac(design, y, max_lags):
    """OLS coefficients and Newey-West t-statistics with Bartlett weights 1 - l/(L+1).

    V = (X'X)^-1 S (X'X)^-1 with S = G_0 + sum_l w_l (G_l + G_l'), G_l = sum_i x_i u_i u_{i-l} x_{i-l}'.
    No small-sample correction, so it must match statsmodels' ``cov_type="HAC"`` default.
    """
    x = np.asarray(design, dtype=float)
    y = np.asarray(y, dtype=float)
    beta = np.linalg.solve(x.T @ x, x.T @ y)
    scores = x * (y - x @ beta)[:, None]
    long_run = scores.T @ scores
    for lag in range(1, max_lags + 1):
        gamma = scores[lag:].T @ scores[:-lag]
        long_run += (1 - lag / (max_lags + 1)) * (gamma + gamma.T)
    bread = np.linalg.inv(x.T @ x)
    se = np.sqrt(np.diag(bread @ long_run @ bread))
    return beta, beta / se


# =============================================================================
# paired_return_test
# =============================================================================


def test_paired_test_matches_a_hand_computed_bartlett_variance():
    """d_i = 0.1 + (-1)^i for 30 days: L = 3; autocovariances g_l = (-1)^l (30-l)/30;
    S = g_0 + 2[(3/4) g_1 + (1/2) g_2 + (1/4) g_3] = 1 - 58/60 = 1/30; se = sqrt(S/30) = 1/30;
    t = 0.1 / (1/30) = 3; two-sided normal p = 0.0027."""
    idx = pd.bdate_range("2020-01-01", periods=30)
    baseline = pd.Series(0.0, index=idx)
    overlay = pd.Series(0.1 + (-1.0) ** np.arange(30), index=idx)

    out = paired_return_test(baseline, overlay)

    assert out == {
        "mean_daily_diff": 0.1,
        "t_statistic": 3.0,
        "p_value": 0.0027,
        "n_days": 30,
        "maxlags": 3,
    }
    assert out["p_value"] == pytest.approx(2 * stats.norm.sf(3.0), abs=5e-5)


def test_paired_test_matches_an_independent_sandwich_on_autocorrelated_differences():
    baseline = _returns(1, rho=0.3)
    overlay = baseline + _returns(2, mean=0.0002, sd=0.004, rho=0.5)
    diff = (overlay - baseline).to_numpy()
    lags = _lag_rule(len(diff))

    out = paired_return_test(baseline, overlay)

    beta, t = _bartlett_hac(np.ones((len(diff), 1)), diff, lags)
    assert out["maxlags"] == lags == 5
    assert out["mean_daily_diff"] == pytest.approx(beta[0], abs=1e-7)
    assert out["mean_daily_diff"] == pytest.approx(diff.mean(), abs=1e-7)
    assert out["t_statistic"] == pytest.approx(t[0], abs=1e-3)
    assert out["p_value"] == pytest.approx(2 * stats.norm.sf(abs(t[0])), abs=1e-4)
    # the Bartlett weights matter: with positive autocorrelation the plain t is larger
    plain_t = diff.mean() / (diff.std(ddof=0) / np.sqrt(len(diff)))
    assert abs(plain_t) > abs(out["t_statistic"]) * 1.2


def test_paired_test_p_values_are_two_sided_normal():
    out = paired_return_test(_returns(3), _returns(3) + _returns(4, mean=0.001, sd=0.005))

    assert out["p_value"] == pytest.approx(2 * stats.norm.sf(abs(out["t_statistic"])), abs=2e-4)


def test_paired_test_flips_sign_with_the_order_of_the_arguments():
    a, b = _returns(5), _returns(5) + _returns(6, mean=0.0, sd=0.003)

    ab, ba = paired_return_test(a, b), paired_return_test(b, a)

    assert ba["mean_daily_diff"] == pytest.approx(-ab["mean_daily_diff"], abs=1e-7)
    assert ba["t_statistic"] == pytest.approx(-ab["t_statistic"], abs=1e-3)
    assert ba["p_value"] == ab["p_value"]
    assert ba["n_days"] == ab["n_days"] == 300


@pytest.mark.parametrize(
    "n, lags", [(30, 3), (50, 3), (100, 4), (200, 4), (400, 5), (1000, 6), (1500, 7)]
)
def test_paired_test_uses_the_floor_4_n_over_100_to_the_2_9_lag_rule(n, lags):
    baseline = _returns(7, n=n)
    overlay = baseline + _returns(8, n=n, mean=0.0, sd=0.002)

    out = paired_return_test(baseline, overlay)

    assert out["maxlags"] == lags == _lag_rule(n)
    assert out["n_days"] == n


def test_paired_test_needs_thirty_days():
    baseline, overlay = _returns(9, n=29), _returns(9, n=29) + _returns(10, n=29, sd=0.002)

    assert paired_return_test(baseline, overlay) is None
    assert (
        paired_return_test(_returns(9, n=30), _returns(9, n=30) + _returns(10, n=30))["n_days"]
        == 30
    )


def test_paired_test_gives_none_when_the_series_are_identical():
    r = _returns(11)

    assert paired_return_test(r, r) is None
    assert paired_return_test(r.to_numpy(), r.to_numpy()) is None


def test_paired_test_drops_nan_and_inf_days_and_counts_the_rest():
    baseline = _returns(12, n=120)
    overlay = baseline + _returns(13, n=120, mean=0.0, sd=0.003)
    dirty_baseline, dirty_overlay = baseline.copy(), overlay.copy()
    dirty_baseline.iloc[[3, 40]] = np.nan
    dirty_overlay.iloc[77] = np.nan
    dirty_overlay.iloc[90] = np.inf
    dirty_baseline.iloc[91] = -np.inf

    out = paired_return_test(dirty_baseline, dirty_overlay)

    assert out["n_days"] == 115
    keep = baseline.index.delete([3, 40, 77, 90, 91])
    assert out == paired_return_test(baseline[keep], overlay[keep])


def test_paired_test_aligns_by_date_label_not_by_position():
    baseline = _returns(14)
    overlay = baseline + _returns(15, mean=0.0, sd=0.003)

    shorter = paired_return_test(baseline, overlay.iloc[10:])
    reordered = paired_return_test(baseline, overlay.iloc[::-1])

    assert shorter["n_days"] == 290
    assert shorter == paired_return_test(baseline.iloc[10:], overlay.iloc[10:])
    assert reordered == paired_return_test(baseline, overlay)


def test_paired_test_with_nothing_in_common_gives_none_rather_than_a_number():
    """An array against a date-indexed Series aligns a RangeIndex with dates: no overlap."""
    baseline = _returns(16)

    assert paired_return_test(baseline.to_numpy(), baseline + 0.001 * _returns(17)) is None
    assert paired_return_test(baseline, baseline.iloc[:0]) is None


# =============================================================================
# newey_west_diff
# =============================================================================


def test_regime_difference_at_zero_lags_is_the_welch_form_with_population_variances():
    """L = 0 is White's HC0; for a dummy regression that is the two-sample t with ddof=0."""
    y = _returns(18, n=80).to_numpy()
    is_a = np.array([True, False] * 40)
    a, b = y[is_a], y[~is_a]

    out = newey_west_diff(y, is_a, max_lags=0)

    t_hand = (a.mean() - b.mean()) / np.sqrt(a.var(ddof=0) / 40 + b.var(ddof=0) / 40)
    assert out["maxlags"] == 0
    assert out["diff_mean"] == pytest.approx(a.mean() - b.mean(), abs=1e-6)
    assert out["t_statistic"] == pytest.approx(t_hand, abs=1e-3)
    assert out["p_value"] == pytest.approx(2 * stats.norm.sf(abs(t_hand)), abs=1e-4)


def test_regime_difference_matches_an_independent_sandwich_at_the_default_lags():
    y = _returns(19, n=400, rho=0.4).to_numpy()
    rng = np.random.default_rng(20)
    is_a = rng.random(400) < 0.4
    y = y + 0.002 * is_a  # regime A earns 20 bps a day more
    lags = _lag_rule(400)

    out = newey_west_diff(y, is_a)

    design = np.column_stack([np.ones(400), is_a.astype(float)])
    beta, t = _bartlett_hac(design, y, lags)
    assert out["maxlags"] == lags == 5
    assert out["diff_mean"] == pytest.approx(beta[1], abs=1e-6)
    assert out["diff_mean"] == pytest.approx(y[is_a].mean() - y[~is_a].mean(), abs=1e-6)
    assert out["t_statistic"] == pytest.approx(t[1], abs=1e-3)
    assert out["p_value"] == pytest.approx(2 * stats.norm.sf(abs(t[1])), abs=1e-4)


def test_regime_difference_honours_an_explicit_max_lags():
    y = _returns(21, n=200, rho=0.5).to_numpy()
    is_a = np.arange(200) % 3 == 0
    design = np.column_stack([np.ones(200), is_a.astype(float)])

    for lags in (0, 2, 9):
        out = newey_west_diff(y, is_a, max_lags=lags)
        assert out["maxlags"] == lags
        assert out["t_statistic"] == pytest.approx(_bartlett_hac(design, y, lags)[1][1], abs=1e-3)
    assert newey_west_diff(y, is_a)["maxlags"] == _lag_rule(200) == 4


def test_regime_difference_flips_sign_when_the_regimes_swap():
    y = _returns(22, n=150).to_numpy()
    is_a = np.arange(150) % 2 == 0

    ab, ba = newey_west_diff(y, is_a), newey_west_diff(y, ~is_a)

    assert ba["diff_mean"] == pytest.approx(-ab["diff_mean"], abs=1e-6)
    assert ba["t_statistic"] == pytest.approx(-ab["t_statistic"], abs=1e-3)
    assert ba["p_value"] == ab["p_value"]


def test_regime_difference_needs_ten_days_in_each_regime():
    y = _returns(23, n=100).to_numpy()

    nine_in_a = np.zeros(100, dtype=bool)
    nine_in_a[:9] = True
    assert newey_west_diff(y, nine_in_a) is None
    assert newey_west_diff(y, ~nine_in_a) is None  # nine in B

    ten_in_a = nine_in_a.copy()
    ten_in_a[9] = True
    assert newey_west_diff(y, ten_in_a) is not None
    assert newey_west_diff(y, ~ten_in_a) is not None


def test_regime_difference_accepts_lists_and_pandas_input():
    r = _returns(24, n=60)
    flags = [i % 2 == 0 for i in range(60)]

    from_lists = newey_west_diff(r.tolist(), flags)
    from_pandas = newey_west_diff(r, pd.Series(flags, index=r.index))

    assert from_lists == from_pandas
    assert from_lists["diff_mean"] == pytest.approx(
        r[flags].mean() - r[[not f for f in flags]].mean(), abs=1e-6
    )
