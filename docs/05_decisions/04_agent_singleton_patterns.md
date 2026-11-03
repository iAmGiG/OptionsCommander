# Agent Architecture & Singleton Patterns

> **Historical decision record (Nov 2025).** The agents, `AgentFactory`, `AgentBus` and `BaseAgent` it describes were retired in #331 (#650, #655-#657); `src/autogen_agents/` no longer exists. The routines in `src/trading/scheduling/trading_cycle.py` and the plain functions in `src/cli/tools/` do this work now.

**Status:** Accepted | **Date:** November 2025

## Context

The multi-agent trading system requires:

- Shared state across agents (positions, prices, account info)
- Single broker connection (rate limits, consistency)
- Coordinated messaging between agents
- Predictable initialization order

Early implementations created multiple instances of shared resources, causing:

- Race conditions in state updates
- Duplicate API connections hitting rate limits
- Inconsistent views of market state

## Decision

### Singleton Pattern for Shared Infrastructure

**Components Using Singleton:**

| Component | Location | Purpose |
|-----------|----------|---------|
| AgentFactory | `src/autogen_agents/agent_factory.py` | Creates and manages agent instances |
| AgentBus | `src/autogen_agents/agent_bus.py` | Inter-agent message routing |
| UnifiedStateManager | `src/trading/unified_state_manager.py` | Centralized position/state tracking |
| UnifiedPriceFetcher | `src/trading/unified_price_fetcher.py` | Single price source with caching |
| AccountManager | `src/trading/account_manager.py` | Multi-account management |
| TradingCacheManager | `src/data_sources/cache/sqlite_cache.py` | Market data cache |

### Implementation Pattern

```python
class UnifiedStateManager:
    """Thread-safe singleton for state management."""

    _instance = None
    _lock = Lock()

    def __new__(cls):
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:  # Double-check locking
                    cls._instance = super().__new__(cls)
        return cls._instance
```

### Agent Architecture (AutoGen-Based)

```text
┌─────────────────────────────────────────────────────┐
│                  TradingOrchestrator                │
│            (coordinates agent workflows)            │
└────────────┬───────────────────────┬────────────────┘
             │                       │
     ┌───────▼───────┐       ┌───────▼───────┐
     │  AgentFactory │       │   AgentBus    │
     │  (singleton)  │       │  (singleton)  │
     └───────┬───────┘       └───────┬───────┘
             │                       │
     ┌───────▼───────────────────────▼───────┐
     │              Agent Pool               │
     │  VoterAgent | RiskAgent | Executor   │
     └───────────────────┬───────────────────┘
                         │
     ┌───────────────────▼───────────────────┐
     │         Shared Infrastructure         │
     │  UnifiedStateManager | PriceFetcher  │
     │    AccountManager | CacheManager     │
     └───────────────────────────────────────┘
```

### Agent Lifecycle

1. **Creation**: AgentFactory.create() - ensures single instance per type
2. **Communication**: AgentBus.publish()/subscribe() - async message passing
3. **State Access**: UnifiedStateManager - broker-as-truth for positions
4. **Cleanup**: Factory handles graceful shutdown

### Thread Safety

- All singletons use double-checked locking
- State mutations protected by locks
- Message queue for async agent communication

## Consequences

**Benefits:**

- Consistent state view across all agents
- Single broker connection prevents rate limit issues
- Predictable resource lifecycle
- Easy to test (mock singleton instances)

**Trade-offs:**

- Global state can complicate testing if not properly reset
- Must be careful with initialization order
- Cannot easily run parallel trading strategies (by design)

## Implementation

- Agent base class: `src/autogen_agents/base_agent.py`
- Factory pattern: `src/autogen_agents/agent_factory.py`
- Message bus: `src/autogen_agents/agent_bus.py`
- State manager: `src/trading/unified_state_manager.py`

## Related Issues

- #342: AgentFactory singleton implementation
- #341: AgentBus message routing
- #401: Multi-account management
