# Configuration Migration Notes

> **Historical notes.** The layout below lists `src/autogen_agents/` and an `agent_bus.py` fix; both were removed in #331. See [the codebase structure](01_codebase_structure.md) for the layout now.

## Overview

Moving hardcoded configuration data to YAML files in `config_defaults/` for better maintainability.

## Current State

### ✅ Already in YAML Config

- `cli_messages.yaml` - All CLI display messages
- `trading_config.yaml` - Trading parameters (MACD, RSI, risk levels)
- `trading_modes.yaml` - Trading mode configurations (conservative/moderate/aggressive)
- `agent_prompts.yaml` - LLM agent system prompts
- `market_hours.yaml` - Market hours configuration
- `scheduler_config.yaml` - Task scheduling settings
- `scanner_config.yaml` - Market scanner settings (includes default ticker watchlists)
- `paths_config.yaml` - File paths configuration
- `scheduler_cli_messages.yaml` - **NEW** - Scheduler CLI messages and command registry

### ✅ Fully Migrated

- `help_commands.yaml` - **COMPLETE** - All 33 CLI help commands
  - Fully migrated from hardcoded Python dict (687 lines removed)
  - Help system now loads from YAML with proper error handling
  - File reduced from ~900 lines to 288 lines (68% reduction)

- `scheduler_cli_messages.yaml` - **COMPLETE** - Config-driven command system
  - All scheduler CLI messages migrated from hardcoded strings
  - Command registry system (14 commands with aliases, handlers, categories)
  - Auto-generated help menu from enabled commands
  - Commands can be added/removed/disabled without code changes
  - Includes: welcome, status, config_info, edit, setup, daemon, testing, history, logs, common messages
  - ~490 lines of structured configuration

- **Ticker Seeds** - **COMPLETE** - Migrated to scanner_config.yaml
  - Removed hardcoded `_SEED_TICKERS` list from `cli_session.py`
  - Now loads from `scanner_config.yaml` default_watchlist (20 tickers)
  - Reuses existing watchlist configuration (single source of truth)

## Pending Migrations

### Medium Priority

1. ~~**Ticker Watchlists**~~ - ✅ **COMPLETE**
   - ✅ Migrated hardcoded `_SEED_TICKERS` to `scanner_config.yaml`
   - ✅ CLI ticker completer now loads from config

2. **Error Messages** (if scattered)
   - Audit for hardcoded error messages outside `cli_messages.yaml`
   - Consolidate into YAML

3. **Validation Rules**
   - Trading validation rules (min/max quantities, etc.)
   - Consider `validation_rules.yaml`

### Low Priority

1. **Color Schemes** (if any)
   - Terminal color schemes
   - Move to `ui_config.yaml`

2. **Keyboard Shortcuts** (if any)
   - CLI keyboard mappings
   - Move to `keybindings.yaml`

## Migration Process

### For Help Commands

```python
# BEFORE (in help_system.py)
def _build_help_data(self):
    return {
        "morning-routine": {
            "category": "Workflow",
            "description": "...",
            ...
        }
    }

# AFTER
import yaml

def _build_help_data(self):
    config_path = "config_defaults/help_commands.yaml"
    with open(config_path) as f:
        return yaml.safe_load(f)
```

### For Config-Driven Commands (Scheduler CLI Pattern)

```yaml
# config_defaults/scheduler_cli_messages.yaml
commands:
  status:
    enabled: true
    handler: show_status
    aliases: ["stat"]
    requires_scheduler: true
    category: quick_start
    description: "Show detailed scheduler status"
```

```python
# src/cli/scheduler_cli.py
def _build_command_registry(self) -> Dict[str, Dict[str, Any]]:
    """Build command lookup table from config."""
    registry = {}
    commands = MSG.get("commands", {})

    for cmd_name, cmd_def in commands.items():
        if not cmd_def.get("enabled", True):
            continue  # Skip disabled commands

        # Register primary command + aliases
        registry[cmd_name] = {
            "handler": cmd_def.get("handler", cmd_name),
            "requires_scheduler": cmd_def.get("requires_scheduler", False),
            # ... other metadata
        }

    return registry

async def _handle_command(self, command: str):
    """Dynamic routing via config."""
    cmd_def = self._command_registry.get(command)
    handler = getattr(self, f"_{cmd_def['handler']}", None)
    await handler() if asyncio.iscoroutinefunction(handler) else handler()
```

### Benefits of YAML Config

1. **Easier Editing**: Non-programmers can update help text
2. **Version Control**: Cleaner diffs when help text changes
3. **Validation**: Can add schema validation (e.g., pydantic models)
4. **Multilingual**: Easier to support multiple languages later
5. **Hot Reload**: Can reload config without restarting (future feature)
6. **Elastic Features**: Commands can be added/removed/disabled by editing YAML (scheduler CLI pattern)
7. **Single Source of Truth**: Reuse existing configs (e.g., ticker lists from scanner_config.yaml)

## Code Quality Improvements

### Linter Fixes Applied

- ✅ **help_system.py:804** - Fixed `dict.keys()` iteration (use `.items()` instead)
  - Before: `for cmd in self.commands.keys():`
  - After: `for cmd, cmd_data in self.commands.items():`

- ✅ **cli_session.py** - PEP 8 import standards (19 inline imports moved to top)
  - All imports now at top of file (lines 11-22, 158-168)
  - Exception: readline in platform-specific try/except (valid use case)
  - Updated ADR 01 with import guidelines

- ✅ **Pylint Fixes (Nov 2025)** - Fixed E0203, E1102, E0606, W0613, W0108 across 6 files:
  - **agent_bus.py:152** - Added class-level `_initialized` declaration for singleton pattern
  - **scheduler_cli.py:262** - Added `callable()` check for handler validation
  - **timeframe_commands.py:27** - Added class-level `_initialized` declaration for singleton
  - **scanner_agent.py:63** - Replaced unnecessary lambda with `list` factory
  - **indicator_library.py:184** - Prefixed unused `close` param with underscore (`_close`)
  - **alpaca_execution_manager.py:506** - Initialize `error_data = None` before try block

### Pre-commit Hook Fixes

- ✅ **Bandit security scanner** - Fixed configuration in `.pre-commit-config.yaml`
  - Changed from `-r src/` to `--recursive` with proper file filters
  - Now passes security scans without errors

### Platform Compatibility

- ✅ **PowerShell Support** - Added detection and helpful messaging
  - `_is_powershell()` function detects PowerShell environment
  - Disables readline (incompatible with PSReadLine)
  - Shows startup notice directing users to cmd.exe or Git Bash for tab completion
  - Prevents confusing "tab doesn't work" experience

## Next Steps

1. **✅ Help System Full Migration - COMPLETED**
   - ✅ Extracted all 33 commands from `help_system.py` using automated script
   - ✅ Generated `help_commands.yaml` via AST parsing migration tool
   - ✅ Updated `HelpSystem._load_help_data()` to load from YAML
   - ✅ Removed 687-line fallback method (enforces YAML-first approach)
   - ✅ File size reduced by 68% (900 → 288 lines)

2. **Config Loader Utility**
   - Create `config_defaults/config_loader.py` if not exists
   - Centralize YAML loading with caching
   - Add schema validation

3. **Documentation**
   - Update developer docs with config file locations
   - Add examples for adding new commands/settings

## Files Modified (Recent Session)

### Config Files

- `config_defaults/help_commands.yaml` - ✅ Updated "show account" vs "list accounts" documentation
- `config_defaults/scheduler_cli_messages.yaml` - ✅ **NEW** - Complete scheduler CLI config (~490 lines)
- `config_defaults/scanner_config.yaml` - ✅ Now used for CLI ticker seeds (20 tickers)

### Source Files

- `src/cli/help_system.py` - ✅ **COMPLETE**
  - Linter fixes applied (dict iteration, line length)
  - Migrated to YAML loading with proper error handling
  - Removed 687-line `_build_help_data_fallback()` method
  - Reduced from ~900 lines to 288 lines (68% reduction)

- `src/cli/cli_session.py` - ✅ **COMPLETE**
  - Fixed account command routing (lines 759, 2762)
  - Moved 19 inline imports to top (PEP 8 compliance)
  - Added PowerShell detection and ticker completer config loading
  - Removed hardcoded `_SEED_TICKERS` list

- `src/cli/scheduler_cli.py` - ✅ **COMPLETE**
  - Refactored to config-driven command system
  - Added `_load_scheduler_messages()`, `_get_msg()`, `_get_emoji()` helpers
  - Implemented `_build_command_registry()` with dynamic routing
  - Auto-generated help menu from enabled commands
  - Replaced 60+ line if/elif chain with registry pattern

- `docs/05_decisions/01_code_organization.md` - ✅ Updated with import guidelines

### Scripts

- `scripts/migrate_help_to_yaml.py` - **REMOVED** (Issue #435) - One-time migration completed

### Configuration

- `.pre-commit-config.yaml` - Fixed bandit hook

## Config-Driven Command System Pattern

The scheduler CLI now implements a reusable pattern for elastic/inelastic features via YAML configuration. This pattern can be applied to other CLI tools.

### Key Features

1. **Commands defined in YAML** - Add/remove/disable without code changes
2. **Dynamic routing** - Registry pattern with `getattr()` dispatch
3. **Auto-generated help** - Help menu built from enabled commands
4. **Category organization** - Grouped display with custom ordering
5. **Alias support** - Multiple names for same command
6. **Conditional initialization** - Commands can require specific dependencies

### Adding a New Command

#### Step 1: Define in YAML

`config_defaults/scheduler_cli_messages.yaml`

```yaml
commands:
  mycmd:
    enabled: true
    handler: my_command_handler
    aliases: ["mc", "my"]
    requires_scheduler: false  # or true if needs DailyScheduler
    category: testing
    description: "My new command description"
    usage: "mycmd [args]"  # optional
```

#### Step 2: Implement handler

`src/cli/scheduler_cli.py`

```python
def _my_command_handler(self, *args):
    """Handler for mycmd command."""
    print(f"{_get_emoji('check_green', '✅')} My command executed!")
    # ... implementation
```

#### Step 3: Test

```bash
python main.py --scheduler
> mycmd
✅ My command executed!
```

The command automatically appears in help menu, responds to aliases, and can be disabled by setting `enabled: false` in YAML.

### Disabling a Command

Set `enabled: false` in YAML:

```yaml
commands:
  mycmd:
    enabled: false  # Command will not appear or be callable
    # ... rest of config
```

No code changes needed - command disappears from help and routing.

### Message Access Pattern

Use dot-notation to access hierarchical messages:

```python
# Get a simple message
title = _get_msg("welcome.title", default="Default Title")

# Get with string formatting
msg = _get_msg("daemon.start.failed", error="Connection timeout")
# Returns: "Failed to start daemon: Connection timeout"

# Get emoji
check = _get_emoji("check_green", default="✅")
```

This pattern keeps all user-facing text in YAML for easy maintenance.

## Folder Consolidation (Nov 2025)

### CLI Folder Cleanup

Consolidated `src/human_interface/` into `src/cli/`:

- ✅ **decision_formatter.py** - Moved to `src/cli/decision_formatter.py`
  - Formats trading decisions for human display
  - Used by `orchestrator.py`

- ✅ **cli_interface.py** - **REMOVED** (dead code)
  - Never imported anywhere in codebase
  - Only had `if __name__ == "__main__"` entry point
  - Superseded by `src/cli/cli_session.py`

- ✅ **human_interface/ folder** - **REMOVED**
  - Empty after moving decision_formatter.py

- ✅ **src/services/llm/** - **REMOVED** (Issue #406)
  - Replaced by AutoGen's native `OpenAIChatCompletionClient`
  - New parser: `src/parsers/autogen_llm_parser.py`
    (renamed `src/parsers/llm_parser.py`, `LLMParser`, when AutoGen was replaced by Microsoft Agent Framework in #606)
  - 500+ lines of code removed
  - See `docs/04_development/llm_consolidation_analysis.md` for details

### Final src/ Structure

```text
src/
├── cli/                    # ✅ Unified CLI layer
│   ├── cli_session.py      # Main interactive CLI
│   ├── decision_formatter.py # Trade decision formatting (moved from human_interface)
│   ├── account_commands.py
│   ├── help_system.py
│   ├── scheduler_cli.py
│   └── timeframe_commands.py
├── autogen_agents/         # AutoGen agent implementations
├── parsers/
│   └── llm_parser.py       # ✅ NL parsing with an LLM tool call (#406; Agent Framework since #606)
├── ...
```

## Recent Commits (Session Summary)

This session completed 5 major improvements:

### 1. Account Command Routing Fix (943bac0)

**Commit**: `fix: Distinguish between account status and account management commands`

**Problem**: "account" worked (showed portfolio) but "show account" incorrectly showed account manager list

**Solution**:

- Singular "show account" → portfolio status
- Plural "show accounts" → account management list
- Updated help documentation to clarify distinction

### 2. PEP 8 Import Standards (e0eca16)

**Commit**: `refactor: Move all imports to top of file per PEP 8 standards`

**Changes**:

- Moved 19 inline imports from cli_session.py to top of file
- Updated ADR 01 with import guidelines
- Exception documented: readline in platform-specific try/except

**Impact**: Pylint clean, better code organization

### 3. Config-Driven Ticker Seeds + PowerShell (2d8aae2)

**Commit**: `feat: Load ticker completer from scanner_config.yaml + PowerShell detection`

**Changes**:

- Removed hardcoded `_SEED_TICKERS` list (13 tickers)
- Now loads from scanner_config.yaml default_watchlist (20 tickers)
- Added `_is_powershell()` detection
- Disables readline in PowerShell (incompatible with PSReadLine)
- Shows helpful startup notice for PowerShell users

**Impact**: Single source of truth, better cross-platform support

### 4. Scheduler CLI Messages to YAML (690984b)

**Commit**: `feat: Add YAML configuration for scheduler CLI messages`

**Changes**:

- Created scheduler_cli_messages.yaml (~490 lines)
- 12 sections: welcome, status, config_info, edit, setup, daemon, testing, history, logs, common, emojis
- Added module-level helpers: `_load_scheduler_messages()`, `_get_msg()`, `_get_emoji()`
- Refactored `_print_welcome()` and `_show_config_info()` to use config

**Impact**: All user-facing text externalized for easy maintenance

### 5. Config-Driven Command System (3c88876)

**Commit**: `refactor: Implement config-driven command system for scheduler CLI`

**Changes**:

- Command registry in YAML (14 commands with enabled, handler, aliases, category)
- `_build_command_registry()` builds lookup table from config
- Dynamic routing with `getattr()` dispatch
- Auto-generated help menu from enabled commands
- Replaced 60+ line if/elif chain with registry pattern

**Impact**: Commands can be added/removed/disabled by editing YAML - no code changes needed

### Documentation Updates

- Updated `config_migration_notes.md` (this file) with:
  - New migrations completed
  - Config-driven command pattern documentation
  - Code quality improvements
  - Platform compatibility notes
  - Recent commits summary

- Updated `01_code_organization.md` ADR with:
  - Import guidelines (PEP 8 standards)
  - Exception handling for circular dependencies
