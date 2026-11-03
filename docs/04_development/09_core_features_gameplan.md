# Core Features Gameplan

> **Historical plan (2025-12-14).** Paths under `src/autogen_agents/` were removed in #331; `VoterAgent` is now `VotingSignal` and `MultiTimeframeVoter` is in `src/trading/instruments/`. For current status see [the project status](02_project_status.md).

**Created**: 2025-12-14
**Updated**: 2025-12-14
**Status**: Active - C Stream: COMPLETE (Core 2/2 + Infra 3/5)

## Overview

The 10 core features (`track:core` label) are split between two parallel development streams to enable concurrent work without conflicts.

## Branch Structure

```text
development
├── feature/core-execution      (A Chat - Position Management)
└── feature/core-signals-infra  (C Chat - Signals + Infrastructure)
```

## Stream Assignments

### A Chat: Execution Stream (`feature/core-execution`)

**Focus**: HOW positions are managed (entry, exit, protection)

| Priority | Issue | Title | Dependency |
|----------|-------|-------|------------|
| 1 | #414 | **KILLER** Advanced Trailing Stop Automation | #321 ✅, #400, #364* |
| 2 | #372 | Multi-Level Price Targets | #366 |
| 3 | #366 | OHLCV-Based Intraday Entry Plan | None |

**Execution Order**: #366 → #372 → #414

**Notes**:

- #366 provides ATR calculation needed by #372 and #414
- #414 can start with current VoterAgent; #364 enhancement is optional
- All three issues form a coherent execution pipeline

### C Chat: Signals + Infrastructure Stream (`feature/core-signals-infra`)

**Focus**: WHAT signals the system generates + support infrastructure

| Priority | Issue | Title | Dependency |
|----------|-------|-------|------------|
| 1 | #364 | Ranked Voter System | None |
| 2 | #395 | Multi-Timeframe Ranked Voting | #364, #365 ✅ |
| 3 | #402 | Credential Management | None |
| 4 | #405 | Tiered Watchlist System | None |
| 5 | #407 | CustomTimeframeBuilder | None |
| 6 | #483 | DB Backup/Migration | None |
| 7 | #370 | LLM Trade Journal | None |

**Execution Order**: #364 → #395, then infrastructure (#402, #405, #407, #483, #370) in any order

**Notes**:

- #364 is foundational for #395
- Infrastructure issues are independent and can be parallelized
- #370 (Journal) is lower priority, can be deferred

## Dependency Diagram

```text
Stream A (Execution):
  #366 (OHLCV Entry)
    └──→ #372 (Multi-Targets)
           └──→ #414 (Trailing Stops) ← KILLER

Stream C (Signals):
  #364 (Ranked Voter)
    └──→ #395 (Multi-TF Voting)

Stream C (Infra): Independent
  #402 (Credentials)
  #405 (Watchlist)
  #407 (Timeframes)
  #483 (DB Backup)
  #370 (Journal)
```

## Cross-Stream Dependencies

| A Issue | C Issue | Nature | Resolution |
|---------|---------|--------|------------|
| #414 | #364 | Optional voter integration | A can use current VoterAgent; enhance later |

**No blocking cross-dependencies** - streams can proceed independently.

## Worktree Setup

Each chat should work in a separate worktree:

```bash
# A Chat worktree
git worktree add ../AutoGen-Trader-execution feature/core-execution

# C Chat worktree
git worktree add ../AutoGen-Trader-signals feature/core-signals-infra
```

## Merge Strategy

1. Each stream PRs into `development` when complete
2. A stream merges first (execution is higher priority)
3. C stream rebases on updated `development` before merge
4. Resolve any conflicts in C stream PR

## Success Criteria

### A Stream Complete When:

- [ ] #366: OHLCV entry planner functional with ATR stops
- [ ] #372: Multi-level targets with order splitting
- [ ] #414: Autonomous trailing stops with voter integration

### C Stream Complete When:

- [x] #364: Ranked voter configuration system (commit b4d4bec, abe3905)
- [x] #395: Multi-timeframe voting operational (commit a875452)
- [ ] Infrastructure: 2 of 5 complete (#483, #405), need 1 more

## Timeline Estimate

| Stream | Issues | Estimate |
|--------|--------|----------|
| A (Execution) | 3 | 2-3 weeks |
| C (Signals) | 2 core + 5 infra | 2-3 weeks |

Parallel execution = both complete in ~3 weeks total.

## Quick Reference

**Filter commands:**

```bash
# All core features
gh issue list --label "track:core"

# Research (separate B chat)
gh issue list --label "track:research"

# Backlog (parked)
gh issue list --label "track:backlog"
```

**Branch switch:**

```bash
# A Chat
git checkout feature/core-execution

# C Chat
git checkout feature/core-signals-infra
```

## Progress Log

### December 14, 2025 - C Stream Progress

**#364 Ranked Voter System** - COMPLETE

- Created `src/trading/instruments/indicator_registry.py`
  - `BaseIndicator` ABC for pluggable indicators
  - `MACDIndicator` and `RSIIndicator` implementations
  - `IndicatorRegistry` with singleton pattern
- Created `src/core/ranked_voter_config.py`
  - `RankedVoterManager` with YAML + SQLite persistence
  - `VoterConfig` and `VotingConfig` dataclasses
  - Presets: default, macd_primary, rsi_primary
- Created `config_defaults/voters_config.yaml`
- Updated `src/autogen_agents/agents/voter_agent.py` with `evaluate_ranked_voting()`
- Commits: `b4d4bec`, `abe3905`

**#395 Multi-Timeframe Ranked Voting** - COMPLETE

- Created `src/autogen_agents/agents/multi_timeframe_voter.py`
  - `MultiTimeframeVoter` class with weighted consensus voting
  - `TimeframeResult` and `MultiTimeframeResult` dataclasses
  - 4 presets: trend_following, intraday, position, scalping
  - Async support for parallel data fetching
- Updated `config_defaults/voters_config.yaml` with multi_timeframe section
- Commit: `a875452`

**#483 DB Backup/Migration Utilities** - COMPLETE

- Created `src/utils/db_backup.py` (~750 lines)
  - `DBBackupManager` class for backup/restore operations
  - Export tables to JSON with metadata
  - Import from JSON with append/replace modes
  - `DBMigrator` for schema version management
  - Cleanup utilities for old data and backups
- Commit: `cbc84de`

**Next**: Continue with infrastructure (#402, #405, #407, #370)

**#405 Tiered Watchlist System** - COMPLETE

- Implementation already existed, fixed CONFIG_DIR path bug
- Tiered watchlist: positions (Tier 0), pending orders (Tier 1), strategy (Tier 2), discovery (Tier 3)
- Trading modes: conservative->balanced, moderate->momentum, aggressive->wheel_strategy
- All tests passing
- Commit: `54b2266`

**Next**: Need 1 more infrastructure issue (#402 or #407 or #370)

**#407 CustomTimeframeBuilder** - COMPLETE

- Created `src/trading/instruments/custom_timeframe.py`
  - `TimeframeParser` for notation parsing (65m, 1.5h, 2d, 2w, etc.)
  - `CustomTimeframeBuilder` with aggregation logic
  - Support for: custom minutes, fractional hours, multi-day, multi-week
  - Trading hours handling (9:30-16:00, session boundaries)
  - Singleton pattern and convenience functions
- Exported from `src/trading/instruments/__init__.py`
- Commit: `b74d57f`

**C Stream Complete: Core 2/2 + Infra 3/5**
Success criteria achieved (need 3/5 infra)!

### December 15, 2025 - CLI Integration Analysis

#### New Features CLI Integration Status

Comprehensive analysis of how features #364, #395, #407, #483, #405 integrate with CLI session:

| Feature | Implemented | Tested | CLI Exposed | Config Loaded | Status |
|---------|:---:|:---:|:---:|:---:|---------|
| #364 Ranked Voting | ✅ | ✅ | ❌ | ✅ | Available via Python API only |
| #395 Multi-Timeframe | ✅ | ✅ | ❌ | ✅ | Singleton available, no CLI cmd |
| #407 Custom Timeframe | ✅ | ✅ | ⚠️ | ⚠️ | Programmatic only, needs async CLI |
| #405 Tiered Watchlist | ✅ | ✅ | ✅ | ✅ | Working (path bug fixed) |
| #483 DB Backup | ✅ | ✅ | ❌ | N/A | Manual Python/CLI needed |

**Key Finding**: All features work as Python modules but lack CLI command exposure. RealVoterStrategy still uses `evaluate_voting()` instead of new `evaluate_ranked_voting()`.

**Phase 1 Issues Created**:

- #488 [CLI] Add /voter command group for ranked voting management
- #489 [CLI] Enhance /timeframe commands for multi-timeframe voting presets
- #490 [CLI] Add /backup command group for database management

**See**: [CLI_INTEGRATION_STATUS.md](./CLI_INTEGRATION_STATUS.md) for detailed analysis and Phase 1-3 recommendations.
