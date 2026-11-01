# Troubleshooting Guide

## Agent Framework Issues

The agents run on Microsoft Agent Framework (`agent-framework-core`, `agent-framework-openai`). AutoGen was removed in #606 and is no longer a dependency.

### `ImportError` for `agent_framework`

- **Solution**: Use this project's environment (`~/miniconda3/envs/OptionsCommander/bin/python`) or reinstall: `pip install -e .`
- **Verify**: `python -c "import agent_framework; print('Agent Framework available')"`

### `ImportError` for `autogen_agentchat` or `autogen_ext`

- **Issue**: Old code or a local script still imports AutoGen
- **Solution**: AutoGen was removed in #606 and the agents in #331. Signals are in `src/trading/instruments/voting_signal.py`; the parser in `src/parsers/llm_parser.py` is the one LLM call.

### "OpenAI API key not found"

- **Issue**: An agent made an LLM call (`process_with_tools`) without a key
- **Solution**: Set `OPEN_AI_KEY` in the environment or in `config/config.json`
- **Note**: Agents build their OpenAI client on first use, so voting, scanning and risk checks need no key

### `FunctionTool.__init__() takes 1 positional argument`

- **Issue**: Agent Framework's `FunctionTool` takes keyword arguments only
- **Solution**: `FunctionTool(func=..., name="...", description="...")`

### VotingSignal MACD/RSI Calculation Errors

- **Issue**: Indicator imports or data format issues
- **Solution**: Verify `src/trading/instruments/indicators.py` imports work
- **Test**: `python -c "from src.trading.instruments.indicators import calculate_macd, calculate_rsi; print('Indicators available')"`

## Data Type Errors

### `Object of type 'DataFrame' is not JSON serializable`

- **Solution**: Convert DataFrames to dict before serializing
- **Method**: `df.to_dict(orient='records')` then `json.dumps(result_dict)`

### `Object of type 'float64' is not JSON serializable`

- **Solution**: Convert numpy types to Python native types before serialization

## Cache System Issues (Still Relevant)

### Cache Issues (SQLite-Based)

- **Issue**: Cache misses or incomplete data
- **Symptoms**: VotingSignal backtests showing date jumps, missing trading days
- **Solution**: Use TradingCacheManager (SQLite) for all agent market data access
- **Prevention**: Ensure agents fetch through `fetch_unified_market_data` (`src/market_data/unified_market_tool.py`)
- **Check Cache**: Run `python scripts/utilities/cache_manager.py stats` to verify cache health

### VotingSignal Data Access Issues

- **Issue**: VotingSignal not finding market data for MACD/RSI calculations
- **Symptoms**: "No market data available" despite cache containing data
- **Solution**: Verify TradingCacheManager integration and cache hits
- **Debug**: Check `python scripts/utilities/cache_manager.py query SYMBOL --start DATE --end DATE`
- **Code**: Check `src/strategies/real_voter_strategy.py` data fetching logic

### Market Data Format Issues

- **Issue**: MACD/RSI calculations failing due to data format mismatches
- **Symptoms**: Pandas DataFrame issues, column name mismatches
- **Solution**: Ensure data normalization through `src/trading/instruments/indicators.py`
- **Verification**: Test individual indicator functions before agent integration

## API and Data Issues

### Empty DataFrames

- **Check**: Dependencies are properly installed and API keys are configured
- **Location**: API keys should be in `config/config.json` (not in repo)

### Alpha Vantage API Limits

- **Limits**: 5 calls/minute, 500 calls/day
- **Solution**: Use caching extensively, consider FMP as alternative

### Module Import Issues

- **Solution**: Run commands and tests from the project root directory
- **Verification**: `python -c "from src.trading.instruments.voting_signal import VotingSignal; print('OK')"`
- **Environment**: Call the env's Python directly (`~/miniconda3/envs/OptionsCommander/bin/python`); `conda activate` is broken on this host

## Package and Environment Issues

### Missing Dependencies

- **Install**: `pip install -e .` installs all dependencies, including `agent-framework-core` and `agent-framework-openai` (which brings `openai`)
- **Verify**: `python -c "from agent_framework import Agent, FunctionTool; print('Agent Framework working')"`

## Tool-Specific Issues

### Agent Market Data Sources

- **Primary**: Alpaca Markets (official SDK, real-time and historical)
- **Fallback**: Alpha Vantage (25 calls/day limit). Polygon.io was removed in Oct 2026 (#607)
- **Cache**: TradingCacheManager (SQLite) reduces API calls by 90%+ with 8-10x query performance

### Agent Date/Time Issues

- **VotingSignal**: Ensure proper date range formatting for market data requests
- **General**: Use consistent YYYY-MM-DD format across all agents
- **Timezone**: Market data cache handles timezone conversion automatically

## Performance Issues

### Agent Performance

- **VotingSignal**: Optimized MACD+RSI calculations with SQLite caching
- **Cache Hit Rate**: Should achieve 90%+ with TradingCacheManager (8-10x faster than file-based)
- **Cache Maintenance**: Run `python scripts/utilities/cache_manager.py cleanup` weekly to remove expired data

### API Rate Limits (Current System)

- **Alpaca API**: 200 requests/minute (for live trading integration)
- **Cache Strategy**: Intelligent caching reduces API usage by 90%+
- **Solution**: Use cache-first approach in all agents

### Agent Memory Usage

- **VotingSignal**: Lightweight, only loads necessary market data
- **BaseAgent**: Efficient tool registration and memory management
- **Monitoring**: Use `python -m memory_profiler` for agent performance testing
