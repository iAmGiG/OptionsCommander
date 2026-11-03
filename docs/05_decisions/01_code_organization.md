# Code Organization Standards

> **Historical decision record (Nov 2025).** The tree below lists `src/autogen_agents/`, which was removed in #331. See [the codebase structure](../04_development/01_codebase_structure.md) for the layout now.

**Status:** Accepted | **Date:** November 2025

## Context

As the codebase grew with multiple agent types, trading components, and data sources, we needed consistent organization to maintain clarity and enable developers to quickly locate functionality.

## Decision

Adopt a layered architecture with clear separation of concerns:

```text
src/
├── autogen_agents/     # Multi-agent system (AutoGen framework)
│   ├── base_agent.py   # Common agent functionality
│   ├── agent_factory.py # Singleton factory for agent creation
│   ├── agent_bus.py    # Pub-sub messaging between agents
│   └── {role}_agent.py # Role-specific agents (voter, scanner, risk, executor)
├── core/               # Business logic & domain models
│   ├── interfaces.py   # Abstract base classes (ABCs)
│   ├── models.py       # Dataclasses (TradeRequest, TradeSuggestion, etc.)
│   ├── factory.py      # Component assembly for CLI
│   └── trading_orchestrator.py # Main workflow coordinator
├── trading/            # Order execution & position management
│   ├── alpaca_trading_client.py # Broker integration
│   ├── order_manager.py # Order lifecycle
│   └── position_manager.py # Position tracking
├── data_sources/       # Market data providers
│   ├── cache/          # SQLite caching layer
│   └── sources/market/ # Alpaca, Alpha Vantage (Polygon removed, #607)
├── risk/               # Risk management
├── execution/          # Execution layer (bracket orders)
├── cli/                # CLI presentation layer
└── utils/              # Cross-cutting utilities
```

## Consequences

**Benefits:**

- Clear ownership: each directory has one responsibility
- Import paths reflect architecture: `from src.core.models import TradeRequest`
- New developers can navigate intuitively

**Trade-offs:**

- Some cross-cutting concerns (like logging) span multiple layers
- Circular imports require careful dependency management

## Implementation

### Import Guidelines

- **All imports at top of file**: Avoid inline imports within functions/methods (PEP 8)
  - Exception: Circular dependency resolution (document with comment)
- **Relative imports within a package**: `from .models import TradeRequest`
- **Absolute imports across packages**: `from src.trading.order_manager import OrderManager`
- **Import order** (enforced by isort):
  1. Standard library imports
  2. Third-party imports
  3. Local application imports

### Code Organization

- Deprecated code goes to `src/deprecated/` (not deleted, for reference)
- Configuration files in `config_defaults/` (YAML preferred over hardcoded)
