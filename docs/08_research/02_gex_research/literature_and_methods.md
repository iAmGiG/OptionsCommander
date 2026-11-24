# GEX Research: Literature and Methods Backlog

Added 2026-10-13 after the same-day regime look-ahead finding in #531 (see issue for details).

**Verification status:** *searched* means found by web search with abstracts or summaries read. *Citation verified* (2026-10-13) means authors, venue, volume and pages were confirmed by search, but the full text was not read. Confirm wording against the published text before quoting.

## Papers

| Paper | Status | Relevance |
|---|---|---|
| Barbon & Buraschi, "Gamma Fragility" (working paper) — <https://abarbon.com/papers/gamma-fragility> | searched, full PDF not read | Dealer gamma imbalance linked to intraday momentum (negative gamma) and reversal (positive gamma), strongest in illiquid stocks. Basis for intraday tests (#530) and a regime x liquidity split. |
| Maurer, "Dealer Gamma Exposure and Overnight Gap Risk" (OptionMetrics working paper) — <https://optionmetrics.com/research/dealer-gamma-exposure-and-overnight-gap-risk-incremental-information-in-low-volatility-regimes/> | searched, abstract only; peer-review status unknown | Gamma adds no out-of-sample volatility signal over a HAR baseline, except overnight gaps on calm low-VIX days. Motivates HAR benchmark and VIX-conditioned tests. |
| Ni, Pearson & Poteshman (2005), "Stock price clustering on option expiration dates", JFE 78(1) — <https://ideas.repec.org/a/eee/jfinec/v78y2005i1p49-87.html> | searched | Closing prices cluster at strikes on expiration dates (at least 16.5 bps average return impact). Testable: expiry days, distance to high-OI strike. |
| Bailey & Lopez de Prado, "The Deflated Sharpe Ratio" (2014) — <https://papers.ssrn.com/abstract=2460551> | searched | Corrects Sharpe for multiple testing and non-normality. Needs an honest trial count N. |
| Bailey, Borwein, Lopez de Prado & Zhu, "The Probability of Backtest Overfitting", Journal of Computational Finance 20(4) (2017), doi:10.21314/JCF.2016.322 — <https://papers.ssrn.com/abstract=2326253> | citation verified | Combinatorially symmetric cross-validation (CSCV); needs the full trial-by-trial return matrix including failed runs. |
| Baltussen, Da, Lammers & Martens, "Hedging demand and market intraday momentum", JFE 142 (2021) 377-403, doi:10.1016/j.jfineco.2021.04.029 — <https://www3.nd.edu/~zda/intramom.pdf> | citation verified | The last 30 minutes are predicted by the rest of the day across 60+ futures (1974-2020), linked to gamma hedging by option market makers and leveraged ETFs. The design used in `intraday_gamma_momentum.py`. |
| Ni, Pearson, Poteshman & White, "Does Option Trading Have a Pervasive Impact on Underlying Stock Prices?", RFS 34(4) (2021) 1952-1986, doi:10.1093/rfs/hhaa082 — <https://ideas.repec.org/a/oup/rfinst/v34y2021i4p1952-1986..html> | citation verified | Market makers' hedge rebalancing affects stock return volatility and the chance of large moves (a noninformational channel). Consistent with the 40-symbol panel: negative GEX goes with higher next-day variance. |
| White, "A Reality Check for Data Snooping", Econometrica 68(5) (2000) 1097-1126; Hansen, "A Test for Superior Predictive Ability", JBES 23 (2005) 365-380 | citation verified | Test whether the best of many models beats a benchmark; SPA reduces sensitivity to poor alternatives. |
| Politis & Romano, "The Stationary Bootstrap", JASA 89(428) (1994) 1303-1313 | citation verified | Resampling blocks of geometric random length for weakly dependent stationary data. |
| Harvey, Liu & Zhu, "...and the Cross-Section of Expected Returns", RFS 29(1) (2016) 5-68, doi:10.1093/rfs/hhv059 | citation verified | Argues new factors should clear t > 3 after multiple testing. Contested by later work (arXiv 2206.15365). |

## Regime detection (#672, #677)

Added 2026-11-24 for the Wasserstein k-means study ([wk_means_regimes.md](../03_strategy_research/wk_means_regimes.md)). *Read* means the arXiv HTML text was read, in full or in part as noted, and the abstract page checked. *Crossref-verified* means title, authors, journal, volume and DOI were confirmed through the Crossref API on 2026-11-24. No journal reference appears on any of the arXiv pages below.

**The method and the authors' related papers (#677).** Each has a verdict: use, later, or do not use.

| Paper | Status | Verdict |
|---|---|---|
| Horvath, Issa & Muguruza, "Clustering market regimes using the Wasserstein distance", *Journal of Computational Finance* (2024), doi:10.21314/JCF.2024.005 (Crossref gives no volume or pages); arXiv:2110.11848 | read (arXiv v1 HTML); journal record Crossref-verified | **Use: the method of #672.** Replicated in #673. MK-means on GBM matches Table 3 exactly, and WK-means with the paper's start matches within a point. The paper does not state p, its synthetic window sizes, or the number of moments behind MK-means. Its baselines leave out plain volatility clustering, which on SPY agrees with WK-means p = 1 on 91-93% of windows. It evaluates no online use and no trading use. |
| Issa & Horvath, "Non-parametric online market regime detection and regime clustering for multidimensional and path-dependent data structures" (2023), arXiv:2306.15835; code github.com/issaz/signature-regime-detection (not checked) | read in part (about 40% verbatim, the rest via summary) | **Later.** It is an online detector: a path-wise two-sample test (signature MMD with a PDE signature kernel) of incoming sub-paths against simulated "beliefs". It keeps the order of returns, which WK-means drops. The evidence is mostly qualitative: synthetic GBM and rough Bergomi paths, daily equities 1980-2021 checked against VIX, and hourly crypto. It reports no detection delay, no comparison with an HMM, and no trading use. The authors ran it on a GPU, and the rank-2 test is about 85x slower than rank-1. Revisit only if #674's delay table shows detection delay is WK-means' weakness. This is the closest path-dependent competitor, so cite it. |
| Alden, Horvath & Issa, "Signature Maximum Mean Discrepancy Two-Sample Statistical Tests" (2025), arXiv:2506.01718 | read | **Later**, with the paper above: how to configure the sig-MMD test (path scaling, level weights). On scaled Brownian motion with batches of 128, Type II error falls from 72.6% to 2.0%. |
| Issa, Horvath, Lemercier & Salvi, "Non-adversarial training of Neural SDEs with signature kernel scores" (2023), arXiv:2305.16274; code github.com/issaz/sigker-nsdes | read; repository checked (no licence file; pins torch 1.9.0+cu111, signatory, sigkernel) | **Do not use.** The synthetic tests #676 needs are the paper's own GBM and Merton generators (`src/backtesting/regimes/synthetic.py`) plus a no-regime null (GARCH or block bootstrap). The code has no licence and needs a CUDA PyTorch stack this project does not have. |
| Bühler, Horvath, Lyons, Perez Arribas & Wood, "A Data-driven Market Simulator for Small Data Environments" (2020), arXiv:2006.14498 | read | **Do not use**, for the same reason: a VAE market simulator on log-signatures, more than a null model for a regime test needs. |
| Futter, Horvath & Wiese, "Signature Trading: A Path-Dependent Extension of the Mean-Variance Framework with Exogenous Signals" (2023), arXiv:2308.15135 | read except the last part | **Do not use.** It covers trading strategies, not regimes, and gives illustrative examples only, with no out-of-sample performance in the text read. |
| Futter, Muca Cirone & Horvath, "Kernel Learning for Mean-Variance Trading Strategies" (2025), arXiv:2507.10701 | read except the last part | **Do not use**, for the same reason as Futter, Horvath & Wiese. MSFT 5-minute bars, qualitative results. |

**Closest prior work for the trading test (#675).** A regime filter that moves between the market and cash competes with volatility timing and with regime-switching allocation. The write-up must engage with:

| Paper | Status | Relevance |
|---|---|---|
| Moreira & Muir, "Volatility-Managed Portfolios", *Journal of Finance* 72(4) (2017) 1611-1644, doi:10.1111/jofi.12513 | Crossref-verified | Scaling exposure by inverse recent variance. The continuous baseline an in/out regime filter must beat. |
| Cederburg, O'Doherty, Wang & Yan, "On the performance of volatility-managed portfolios", *Journal of Financial Economics* (2020) | not verified (Crossref rate-limited) | Out-of-sample critique of Moreira-Muir. |
| Ang & Bekaert, "International Asset Allocation With Regime Shifts", *Review of Financial Studies* 15(4) (2002) 1137-1187, doi:10.1093/rfs/15.4.1137 | Crossref-verified | Regime-switching asset allocation. |
| Kritzman, Page & Turkington, "Regime Shifts: Implications for Dynamic Strategies", *Financial Analysts Journal* 68(3) (2012) 22-39, doi:10.2469/faj.v68.n3.3 | Crossref-verified | Markov-switching regimes used to time risk premia. |
| Nystrup, Hansen, Madsen & Lindström, "Regime-Based Versus Static Asset Allocation: Letting the Data Speak", *Journal of Portfolio Management* 42(1) (2015) 103-109, doi:10.3905/jpm.2015.42.1.103 | Crossref-verified | HMM-based regime allocation against static allocation. |
| Nystrup, Madsen & Lindström, "Dynamic portfolio optimization across hidden market regimes", *Quantitative Finance* (2018) | not verified (Crossref rate-limited) | Regime-based allocation with costs, out of sample. |
| Hamilton, "A New Approach to the Economic Analysis of Nonstationary Time Series and the Business Cycle", *Econometrica* 57(2) (1989), doi:10.2307/1912559 | Crossref-verified | Markov switching, the basis of the HMM baseline (#676). Its smoothed probabilities use the whole sample; an online filter must use the filtered ones. |
| Jobson & Korkie, "Performance Hypothesis Testing with the Sharpe and Treynor Measures", *Journal of Finance* 36(4) (1981) 889-908, doi:10.1111/j.1540-6261.1981.tb04891.x; Memmel's correction, *Finance Letters* (2003) | Jobson & Korkie Crossref-verified; Memmel not found in Crossref | Test of a Sharpe difference between two correlated strategies. It gives #675's minimum detectable effect. |
| Ledoit & Wolf, "Robust performance hypothesis testing with the Sharpe ratio", *Journal of Empirical Finance* 15(5) (2008) 850-859, doi:10.1016/j.jempfin.2008.03.002 | Crossref-verified | HAC and bootstrap inference for Sharpe differences with fat tails and serial correlation. |

## Data-quality caveats (vendor material, not validated error rates)

- OI-based GEX assumes dealers are long calls and short puts; public open interest cannot identify dealer vs customer. SpotGamma: <https://spotgamma.com/gamma-exposure-gex/>
- Open interest is a prior-night snapshot, so intraday estimates go stale. A flow-signed alternative exists: <https://flashalpha.com/articles/flow-signed-gex-polarity-dealers-long-or-short-gamma>
- gex-database-infrastructure Issue #74: stored equity GEX omits 0DTE contracts; `gex.gex_strike_profile` includes them.
- Vendor break: Alpha Vantage through 2026-10-09, Cboe delayed quotes from 2026-10-12 (GEX level break). Research clamps at 2026-10-09.

## Methods to add to the runners

Status as of 2026-10-13. Results are in [regime_lookahead_revalidation.md](regime_lookahead_revalidation.md).

| # | Method | Status |
|---|---|---|
| 1 | Report regime effects at lags 0, 1, 2, 5 (lag 0 is look-ahead, comparison only) | Done for MACD+RSI (`--regime-lags`) |
| 2 | Newey-West errors instead of Welch's t-test | Done (`research_cli.py`); block bootstrap not done |
| 3 | Out-of-sample split (pre-2020 vs 2020 onward) and VIX conditioning | Done (`var_vol_baseline.py`, `gex_vol_panel.py`) |
| 4 | HAR realized-volatility baseline, plus leverage controls | Done (log-HAR on Parkinson variance) |
| 5 | Multiple-testing correction | Holm, BH and \|t\| > 3 done (`multiple_testing_audit.py`); Deflated Sharpe and Reality Check / SPA not done (no trading overlay was significant) |
| 6 | Intraday tests on `intraday_gex_summary` (label change 2026-02-06, vendor break 2026-10-12) | Done (`intraday_gamma_momentum.py`): null |
| 7 | Expiry-day and distance-to-strike pinning tests | Done (`expiry_pinning.py`): tiny effect when dealers long gamma |
| 8 | Regime × liquidity split | Done (`gex_vol_panel.py`): effect scales with \|GEX\|/dollar volume, not illiquidity |
| 9 | Verify the from-memory citations | Done 2026-10-13 (citation level) |
