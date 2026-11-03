# Development Documentation

This directory contains guides for developers working on OptionsCommander (formerly RH2MAS).

## Quick Navigation

### Core References

1. **[Codebase Structure](01_codebase_structure.md)**
   - Project layout and module organization
   - Import conventions and patterns
   - Where to find specific functionality
   - **Start here** if you're new to the codebase

2. **[Project Status & Roadmap](02_project_status.md)**
   - Current implementation status
   - Completed features and known issues
   - Open issues and priorities
   - Sprint planning and milestones
   - **Check this** to understand what's being worked on

3. **[CLI Trade Assistant](03_cli_trade_assistant.md)**
   - Architecture of the interactive CLI
   - How human-in-the-loop trading works
   - Integration with trading cycle
   - **Reference this** for CLI-related changes

4. **[Cache Developer Guide](04_cache_developer_guide.md)**
   - SQLite caching system internals
   - API usage and examples
   - Performance tuning
   - **Read this** if working with market data or cache

5. **[Automation Setup](05_automation_setup.md)**
   - Pre-commit hooks configuration
   - GitHub Actions setup
   - Auto-formatting and linting
   - **Use this** to set up your development environment

6. **[Code Quality & Maintenance](06_code_quality.md)**
   - Linting standards and configuration
   - Active code quality issues
   - Technical debt tracking
   - Import consolidation history
   - **Reference this** for code quality improvements

### Additional Guides

- **[Backtesting Framework](07_backtesting_framework.md)** - Validation and backtesting tools
- **[Refactor Impact Assessment](08_refactor_impact_assessment.md)** - Change impact analysis
- **[Core Features Gameplan](09_core_features_gameplan.md)** - Feature implementation plans
- **[Database-First Caching](10_database_first_caching.md)** - SQLite caching architecture
- **[Config Migration Notes](11_config_migration_notes.md)** - Configuration system changes
- **[GEX Quick Reference](12_gex_quick_reference.md)** - Gamma exposure analysis guide
- **[GEX Voter Integration](13_gex_voter_integration_guide.md)** - GEX + voting signal integration plan (not pursued: no GEX overlay survived re-validation, #576)
- **[LLM Consolidation](14_llm_consolidation_analysis.md)** - LLM usage analysis

### CLI Integration

- **[CLI Integration Phase 1](15_cli_integration_status_phase1.md)** - ApprovedTickers, PositionSizer, PortfolioManager
- **[CLI Integration Phase 2](16_cli_integration_status_phase2.md)** - Ranked Voting, Multi-TF, GTT, Watchlist commands

### Feature Implementation

- **[Config Schema Review](17_config_schema_review.md)** - Configuration architecture
- **[GTT Implementation](18_gtt_implementation.md)** - Good-Till-Triggered orders
- **[Partial Exit Strategies](19_partial_exit_strategies.md)** - Research for #417, scaling out
- **[Profile Preset Architecture](20_profile_preset_architecture.md)** - User profile system

---

## Related Documentation

**For architecture decisions**, see [docs/05_decisions/](../05_decisions/README.md)

**For feature documentation**, see [docs/06_features/](../06_features/README.md)

**For testing**, see [docs/07_testing/](../07_testing/README.md)

---

## Common Tasks

### Adding a New Feature

1. Read [01_codebase_structure.md](01_codebase_structure.md) for where to place code
2. Check [docs/05_decisions/](../05_decisions/) for relevant architectural patterns
3. Update [02_project_status.md](02_project_status.md) to track your work
4. Create tests in `tests/` following existing patterns
5. Document in `docs/06_features/` when ready for users

### Fixing a Bug

1. Create a GitHub issue (if not already created)
2. Check [02_project_status.md](02_project_status.md) for related issues
3. Write tests that reproduce the bug
4. Fix the bug
5. Update [02_project_status.md](02_project_status.md) when complete
6. Reference the issue in your commit message

### Improving Performance

1. Profile the code to identify bottlenecks
2. Check [04_cache_developer_guide.md](04_cache_developer_guide.md) for caching strategies
3. Review [docs/05_decisions/03_api_caching_patterns.md](../05_decisions/03_api_caching_patterns.md)
4. Implement improvements with tests
5. Document in commit message and relevant docs

### Understanding How Something Works

1. Start with [01_codebase_structure.md](01_codebase_structure.md) to find the files
2. Read code comments and docstrings
3. Check related ADRs in [docs/05_decisions/](../05_decisions/)
4. Look at tests in `tests/` for usage examples
5. Ask in GitHub issues if stuck

---

## Code Quality Standards

All code must follow standards documented in:

**Decision Records** ([docs/05_decisions/](../05_decisions/)):

- **01_code_organization.md** - Directory structure and imports
- **02_error_handling_logging.md** - Error handling patterns
- **03_api_caching_patterns.md** - Cache implementation
- **04_agent_singleton_patterns.md** - Agent lifecycle

**Code Quality Guide** ([06_code_quality.md](06_code_quality.md)):

- Linting configuration (Ruff, Black, isort, MyPy)
- Import conventions and patterns
- Type hint guidelines
- Active code quality issues (#409-#413)

Pre-commit hooks enforce:

- Ruff formatting (`ruff format`, Black-compatible)
- Ruff linting, including import order (rule I)
- Trailing whitespace removal
- Security scanning (Bandit)

---

## Architecture Decisions

Significant design decisions are documented as ADRs. Before making architectural changes, check [docs/05_decisions/](../05_decisions/).

---

## Testing Strategy

See [docs/07_testing/](../07_testing/README.md) for:

- Unit testing guidelines
- Integration testing approach
- Forward testing protocol
- How to run tests safely (never run tests that place orders)

---

## Questions?

1. **Where do I put this code?** → [01_codebase_structure.md](01_codebase_structure.md)
2. **How does the CLI work?** → [03_cli_trade_assistant.md](03_cli_trade_assistant.md)
3. **How's the cache implemented?** → [04_cache_developer_guide.md](04_cache_developer_guide.md)
4. **What's the project status?** → [02_project_status.md](02_project_status.md)
5. **How should I design this?** → [docs/05_decisions/](../05_decisions/)
6. **What are the code quality standards?** → [06_code_quality.md](06_code_quality.md)
