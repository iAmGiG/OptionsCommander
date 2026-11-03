# API Data Sources Reference

Comprehensive guide to external API integrations for market data, including rate limits, capabilities, and usage patterns.

## Overview

AutoTrader integrates multiple data providers with automatic fallback and caching:

| Provider | Primary Use | Rate Limit | Status |
|----------|-------------|------------|--------|
| **Alpaca Markets** | Primary market data + orders | 200 req/min | ✅ Production |
| **Finnhub** | Stock prices, news | 60 req/min | ✅ Available |
| **Alpha Vantage** | Fallback prices, options | 75 req/min (freemium) | ✅ Available |
| **Polygon.io** | Removed Oct 2026 (#607) | — | ❌ Removed |
| **FRED** | Macro economic data | Unlimited | ✅ Available |
| **FMP** (Financial Modeling Prep) | Fundamentals, earnings | 250 req/day | ✅ Available |

---

## Alpaca Markets

**Status**: ✅ Primary data source (Production)
**Documentation**: [05_integration_apis.md](../02_architecture/05_integration_apis.md)

### Capabilities

- **Market Data**: OHLCV bars, quotes, trades, snapshots
- **Order Execution**: All order types with bracket/trailing stops
- **Account Management**: Real-time positions, buying power
- **Paper Trading**: Full feature parity with live accounts

### Rate Limits

- **Free Tier**: 200 requests/minute
- **Paid Tier**: Higher limits (subscription-based)
- **Paper Accounts**: Same limits as live

### Configuration

```json
{
  "ALPACA_PAPER_API_KEY": "your_paper_key",
  "ALPACA_PAPER_SECRET": "your_paper_secret"
}
```

### Key Features

✅ Official `alpaca-py` SDK integration
✅ SQLite cache with 90%+ hit rate
✅ Plain-function tools for the CLI (`src/cli/tools/`)
✅ Multi-timeframe support (1Min to 1Day)
✅ IEX feed for paper accounts (free)

---

## Finnhub

**Status**: ✅ Available for backfill/research
**Official Docs**: [https://finnhub.io/docs/api](https://finnhub.io/docs/api)

### Capabilities

- **Stock Prices**: Daily candles, real-time quotes
- **Company Data**: Financials, profiles, news
- **Options**: Basic options chains (limited)
- **Forex & Crypto**: Multi-asset support

### Rate Limits

Source: [Finnhub API Documentation](https://finnhub.io/docs/api/rate-limit)

- **Free Tier**: 60 API calls/minute (with API key)
- **Without Key**: 2 calls/minute
- **Internal Cap**: 30 calls/second
- **No Daily Limit** (only per-minute)

### Configuration

```json
{
  "FINNHUB_KEY": "your_api_key"
}
```

### Usage Example

```python
import requests

url = "https://finnhub.io/api/v1/stock/candle"
params = {
    "symbol": "SPY",
    "resolution": "D",  # Daily
    "from": start_timestamp,
    "to": end_timestamp,
    "token": config["FINNHUB_KEY"]
}

response = requests.get(url, params=params)
data = response.json()
```

### Key Features

✅ Generous free tier (60 req/min)
✅ Good for development and backfilling
✅ Multi-asset support (stocks, forex, crypto)
⚠️ Limited options data (basic chains only)

---

## Alpha Vantage

**Status**: ✅ Freemium tier available
**Official Docs**: [https://www.alphavantage.co/documentation](https://www.alphavantage.co/documentation/)

### Capabilities

- **Stock Prices**: Daily/intraday time series
- **Options**: Historical options chains with Greeks
- **Technical Indicators**: Built-in indicator calculations
- **Fundamentals**: Company financials, earnings

### Rate Limits

Sources:

- [Alpha Vantage Premium](https://www.alphavantage.co/premium/)
- [Understanding Free Tier Limits](https://simplynostalgic.com/blog/alpha-vantage-api-understanding-free-1763412826837)

**Free Tier** (standard API key):

- 25 API requests per day
- 5 API calls per minute

**Freemium Tier** (AutoTrader uses this):

- 75 API requests per minute
- No daily cap
- $49.99/month tier

**Premium Tier** (gex-llm-patterns research):

- 1000 API calls per minute
- No daily cap
- Enterprise tier

### Configuration

```json
{
  "ALPHA_VANTAGE_KEY": "your_api_key"
}
```

### Usage Example

```python
import requests

url = "https://www.alphavantage.co/query"
params = {
    "function": "TIME_SERIES_DAILY",
    "symbol": "SPY",
    "outputsize": "full",  # or "compact" for last 100 days
    "apikey": config["ALPHA_VANTAGE_KEY"]
}

response = requests.get(url, params=params)
data = response.json()
prices = data["Time Series (Daily)"]
```

### Key Features

✅ Historical options data with Greeks
✅ Good for research and backfilling
✅ Technical indicators built-in
⚠️ Free tier very limited (25/day)
⚠️ `outputsize=full` can be slow (5+ years of data)

---

## Polygon.io

**Status**: ❌ Removed in Oct 2026 (#607). Its Python client was frozen (Polygon became Massive)
and access ended; data cached from it earlier is still served from the cache. The notes below
are kept for reference.
**Official Docs**: [https://polygon.io/docs](https://polygon.io/docs)

### Capabilities

- **Market Data**: Stocks, options, forex, crypto
- **Historical Data**: Minute-level granularity
- **Real-time**: WebSocket streams

### Rate Limits

- **Free Tier**: 5 calls/minute (very limited)
- **Starter**: $99/month - 1000 req/min
- **Developer**: $249/month - Unlimited

### Configuration

```json
{
  "POLYGON_IO": "your_api_key"
}
```

### Current Usage

None: the fallback after Alpaca is Alpha Vantage.

---

## FRED (Federal Reserve Economic Data)

**Status**: ✅ Unlimited free tier
**Official Docs**: [https://fred.stlouisfed.org/docs/api/](https://fred.stlouisfed.org/docs/api/)

### Capabilities

- **Economic Data**: GDP, unemployment, inflation
- **Interest Rates**: Fed funds rate, treasury yields
- **Market Indicators**: VIX, consumer sentiment

### Rate Limits

- **Unlimited** API calls (free)
- Requires free API key from St. Louis Fed

### Configuration

```json
{
  "FREDAPI": "your_api_key"
}
```

### Potential Use Cases

- Macro regime classification
- Interest rate correlation analysis
- Economic cycle detection for TSMOM

---

## FMP (Financial Modeling Prep)

**Status**: ✅ Free tier available
**Official Docs**: [https://site.financialmodelingprep.com/developer/docs](https://site.financialmodelingprep.com/developer/docs)

### Capabilities

- **Fundamentals**: Financial statements, ratios
- **Earnings**: Calendar, surprises, transcripts
- **Institutional**: 13F filings, insider trading

### Rate Limits

- **Free Tier**: 250 requests/day
- **Paid Tiers**: Higher limits available

### Configuration

```json
{
  "FMP": "your_api_key"
}
```

### Potential Use Cases

- Earnings calendar for event-driven trading
- Fundamental screening
- Institutional holdings analysis

---

## Rate Limiting Best Practices

### 1. Cache Everything

SQLite cache provides 90%+ hit rate:

```python
from src.data_sources.cache import TradingCacheManager

cache = TradingCacheManager()
data = cache.get_market_data("SPY", "1Day", "2024-01-01", "2024-01-31")
```

### 2. Respect Rate Limits

```python
import time

requests_per_minute = 60  # Finnhub free tier
request_interval = 60 / requests_per_minute  # 1 second

for symbol in symbols:
    fetch_data(symbol)
    time.sleep(request_interval)
```

### 3. Use Bulk Requests

Fetch multiple symbols or date ranges in single calls where supported:

```python
# Good: Single call for multiple symbols
alpaca.get_bars(["SPY", "QQQ", "IWM"], start, end, "1Day")

# Bad: Multiple calls
for symbol in ["SPY", "QQQ", "IWM"]:
    alpaca.get_bars([symbol], start, end, "1Day")
```

### 4. Implement Exponential Backoff

```python
import time
from requests.exceptions import HTTPError

max_retries = 3
for attempt in range(max_retries):
    try:
        response = requests.get(url, params=params)
        response.raise_for_status()
        break
    except HTTPError as e:
        if attempt < max_retries - 1:
            wait_time = 2 ** attempt  # 1s, 2s, 4s
            time.sleep(wait_time)
        else:
            raise
```

---

## Multi-Provider Fallback Pattern

AutoTrader uses cascading fallback:

```python
def get_market_data(symbol, start, end):
    """Try providers in priority order."""
    # 1. Check cache first
    cached = cache.get(symbol, start, end)
    if cached:
        return cached

    # 2. Try Alpaca (primary)
    try:
        data = alpaca.get_bars(symbol, start, end)
        cache.set(symbol, data)
        return data
    except Exception as e:
        logger.warning(f"Alpaca failed: {e}")

    # 3. Try Finnhub (fallback)
    try:
        data = finnhub.get_candles(symbol, start, end)
        cache.set(symbol, data)
        return data
    except Exception as e:
        logger.warning(f"Finnhub failed: {e}")

    # 4. Try Alpha Vantage (last resort)
    try:
        data = alpha_vantage.get_daily(symbol)
        cache.set(symbol, data)
        return data
    except Exception as e:
        raise DataFetchError("All providers failed")
```

---

## Cost Analysis

| Provider | Monthly Cost | Effective Rate | Use Case |
|----------|--------------|----------------|----------|
| **Alpaca** | Free (paper) | 200 req/min | Primary trading |
| **Finnhub** | Free | 60 req/min | Research, backfill |
| **Alpha Vantage (freemium)** | $49.99 | 75 req/min | Options data |
| **Alpha Vantage (premium)** | Custom | 1000 req/min | GEX research (gex-llm-patterns) |
| **FRED** | Free | Unlimited | Macro data |
| **FMP** | Free | 250 req/day | Fundamentals |

**Current AutoTrader Cost**: $49.99/month (Alpha Vantage freemium)

---

## Research Applications

### GEX Research (Current)

- **Primary**: Alpha Vantage premium (gex-llm-patterns project)
- **Collection**: 47.8M options contracts (SPY, expanding to 15 tickers)
- **Rate**: 1000 calls/min = ~23 minutes for 5 years of data per ticker

### TSMOM Validation

- **Primary**: Alpaca (IEX feed, free)
- **Cache**: 90%+ hit rate from SQLite
- **Backfill**: Finnhub for historical gaps

### Cross-Asset Analysis

- **Equities**: Alpaca + Finnhub
- **Bonds (TLT)**: Alpaca
- **Volatility (VXX)**: Alpaca + Finnhub
- **Macro (FRED)**: GDP, rates, inflation

---

## Troubleshooting

### Alpha Vantage Rate Limit

**Error**: `{"Note": "Thank you for using Alpha Vantage! Our standard API call frequency is 5 calls per minute..."}`

**Solution**:

- Check tier (free = 25/day, freemium = 75/min)
- Add rate limiting with `time.sleep()`
- Use cache to reduce API calls

### Finnhub 429 Error

**Error**: `429 Too Many Requests`

**Solution**:

- Verify API key is configured
- Reduce request rate to <60/min
- Check for concurrent requests (max 30/sec)

### yfinance Blocking

**Error**: `HTTPError: 429 Client Error`

**Solution**:

- **Avoid yfinance** - HPCC got IP blocked
- Use Alpaca or Finnhub instead
- Emergency only (with VPN/proxy)

---

## Related Documentation

- [Integration APIs](../02_architecture/05_integration_apis.md) - Alpaca details
- [Cache System](../02_architecture/04_cache_system.md) - SQLite caching
- [Troubleshooting](04_troubleshooting.md) - Common API issues

---

Last updated: 2025-12-17
