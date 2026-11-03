# News Data Limitations & Usage Guidance

## ⚠️ DEPRECATED SYSTEM WARNING

**Status**: The V0-V4 sentiment framework (including news data analysis) has been **DEPRECATED** as of the AutoTrader-AgentEdge architecture transition.

**Current System**: **`VotingSignal`** using **MACD+RSI voting** without sentiment dependencies.

**Reason for Deprecation**: Complex sentiment analysis proved to have unproven ROI compared to simple, validated technical indicators.

## Historical Context (Archived Information)

This document is preserved for historical reference only. The described news architecture was part of the deprecated V0-V4 sentiment framework.

### Former Unified News Tool Implementation (Deprecated)

The deprecated system used a **UnifiedNewsTool** that provided:

- **Multiple Source Integration**: Finnhub (priority 1), NewsAPI (priority 2), AlphaVantage (priority 3)
- **Consistent Sentiment Scoring**: Single-pass sentiment analysis across all sources
- **Smart Relevance Filtering**: Articles scored by keyword/ticker relevance
- **Automatic Fallback**: Seamless switching between sources when APIs fail

## Known Limitations

### 1. Data Freshness & Relevance

**Issue**: News results may sometimes be outdated or loosely related to the requested symbol.

**Impact**:

- Sentiment scores may not reflect current market conditions
- Analysis might be based on stale information
- Inverse ETFs (VXX, SQQQ) may show conflicting sentiment vs price action

**Mitigation**:

- Cross-verify sentiment with recent market data trends
- Check multiple timeframes (daily vs hourly sentiment)
- Use market heat score as additional validation

### 2. API Rate Limits

**Issue**: Free tier APIs have strict limits (Alpha Vantage: 25 calls/day, NewsAPI: limited requests)

**Impact**:

- Reduced data availability during heavy testing
- Potential gaps in sentiment coverage
- Fallback to cached data (may be stale)

**Mitigation**:

- Intelligent caching system (7-day expiry for news)
- Multi-source fallback (Finnhub → NewsAPI → AlphaVantage)
- Batch processing for backtesting

### 3. Sentiment Analysis Accuracy

**Issue**: Automated sentiment analysis may misinterpret context, sarcasm, or domain-specific language.

**Impact**:

- False positive/negative sentiment scores
- Misaligned sentiment for complex financial situations
- Inconsistent scoring across different news sources

**Mitigation**:

- Relevance score filtering (only use articles with score ≥ 0.5)
- Multiple article aggregation for better accuracy
- Manual verification of high-impact decisions

## Usage Guidance

### Interpreting Sentiment Output

#### Sentiment Score Ranges

- **+0.5 to +1.0**: Strong positive sentiment (bullish)
- **+0.1 to +0.5**: Mild positive sentiment  
- **-0.1 to +0.1**: Neutral sentiment
- **-0.1 to -0.5**: Mild negative sentiment
- **-0.5 to -1.0**: Strong negative sentiment (bearish)

#### Confidence Indicators

- **High Relevance (≥0.8)**: Strong keyword/ticker matches, likely accurate
- **Medium Relevance (0.5-0.8)**: Moderate matches, use with caution
- **Low Relevance (<0.5)**: Weak matches, filtered out automatically

#### Special Cases

- **Inverse ETFs**: Negative sentiment may actually be bullish for the instrument
- **Sector ETFs**: Sentiment may reflect individual components, not the ETF itself
- **Recent IPOs**: Limited news history may skew sentiment analysis

### Troubleshooting Common Issues

#### No Sentiment Data Available

**Symptoms**: All sentiment scores showing 0.0 or null

**Causes**:

- API key issues or expired credentials
- Rate limit exceeded
- Symbol not found in news databases
- Network connectivity issues

**Solutions**:

1. Verify API keys in `config/config.json`
2. Check API usage limits in provider dashboards
3. Try alternative ticker symbols (e.g., full company name)
4. Use cached data as fallback

#### Contradictory Sentiment vs Price Action

**Symptoms**: Positive sentiment during price decline or vice versa

**Causes**:

- Delayed news reporting
- Market sentiment vs fundamental sentiment disconnect
- Inverse relationship instruments (VXX, inverse ETFs)

**Solutions**:

1. Cross-reference with technical indicators (MACD, RSI)
2. Check market heat score for broader context
3. Examine individual article titles for context
4. Consider fundamental vs technical sentiment split

#### Low-Quality News Results

**Symptoms**: Irrelevant articles, low relevance scores, generic financial news

**Causes**:

- Broad keyword matching
- Limited news coverage for specific symbols
- Generic financial news overwhelming specific company news

**Solutions**:

1. Refine search terms (use company name + ticker)
2. Adjust date range to focus on recent events
3. Filter by news category (earnings, analyst reports)
4. Use multiple sources for validation

## Fallback Methods

### When News Data is Insufficient

1. **Market Heat Analysis**: Use broader market sentiment indicators
2. **Technical Analysis Only**: Rely on MACD and price action signals  
3. **Peer Comparison**: Analyze sentiment for sector or peer companies
4. **Manual News Search**: Use external financial news sites for verification

### Emergency Procedures

1. **API Outage**: Switch to cached sentiment data with timestamp warnings
2. **Data Quality Issues**: Flag low-confidence decisions for manual review
3. **Rate Limit Hit**: Queue requests for next available window
4. **Stale Data**: Use market volatility (VXX) as sentiment proxy

## Future Improvements

### Planned Enhancements (Referenced from Issue #36)

- **Real-time News Feeds**: Integration with premium news APIs
- **Domain-Specific Models**: Fine-tuned sentiment models for financial language
- **Multi-Language Support**: Analysis of international financial news
- **Event Detection**: Automatic identification of earnings, FDA approvals, etc.

### Monitoring & Alerts

- **Data Quality Metrics**: Track relevance scores and coverage gaps
- **Performance Correlation**: Monitor sentiment accuracy vs actual returns
- **API Health**: Automated monitoring of news source availability

## Migration to Current System

**Current System**: `VotingSignal` with MACD+RSI voting

- **File**: `src/trading/instruments/voting_signal.py`
- **Validation**: experiment 293's 0.856 Sharpe (in-sample) is superseded; the [live-rules baseline](../08_research/03_strategy_research/live_rules_baseline.md) finds no edge over buy-and-hold
- **Dependencies**: No news APIs, only market data (Alpaca)

## Related Issues & Documentation

- **Issue #36**: Original external scoring framework (DEPRECATED)
- **Issue #42**: Documentation of limitations (historical reference)
- **Issue #139**: Fix for sentiment pipeline showing 0.0 values (OBSOLETE)
- **Issue #310**: Complete remaining AutoGen agents (CURRENT FOCUS)
- [Architecture Guide](../system/architecture.md): Current vs deprecated systems
- [Commands Reference](commands.md): Updated for AutoGen system
- [Terminology](terminology.md): Updated agent architecture
