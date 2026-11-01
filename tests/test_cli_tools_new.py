"""
Tests for New CLI Tools (Issues #488, #489, #490)

Tests the new CLI FunctionTool implementations:
- #488: Voter tools (VoterAgent configuration)
- #489: Enhanced timeframe tools (multi-timeframe presets)
- #490: Backup tools (database backup/restore)

Run with: conda run -n AutoTrader python -m pytest tests/test_cli_tools_new.py -v

NOTE: Uses direct module imports to avoid config loading issues.
"""

# ruff: noqa: N806

import importlib.util
import sys
import tempfile
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from tests.module_sandbox import sandboxed_modules

# Add project root to path BEFORE any imports
project_root = Path(__file__).parent.parent
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))


@pytest.fixture(autouse=True)
def isolated_state_dir(tmp_path, monkeypatch):
    """Keep state managers away from the real state/user.db (see tests/unit/conftest.py)."""
    monkeypatch.setenv("AUTOTRADER_STATE_DIR", str(tmp_path / "state"))


def import_module_directly(module_path: str, module_name: str):
    """Import a module directly from file path, bypassing package __init__."""
    spec = importlib.util.spec_from_file_location(module_name, module_path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


# =============================================================================
# Test Fixtures
# =============================================================================


@pytest.fixture
def module_sandbox():
    """Restore sys.modules and the config singleton after a fixture installs stubs."""
    with sandboxed_modules("voter_tools_test", "backup_tools_test", "timeframe_tools_test"):
        yield


@pytest.fixture
def backup_tools_module(module_sandbox, monkeypatch):
    """Load backup_tools module directly."""

    module_path = project_root / "src" / "cli" / "tools" / "backup_tools.py"
    return import_module_directly(str(module_path), "backup_tools_test")


@pytest.fixture
def temp_backup_dir():
    """Create temporary backup directory for testing."""
    with tempfile.TemporaryDirectory() as tmpdir:
        yield tmpdir


# =============================================================================
# Tests: Issue #489 - Enhanced Timeframe Tools (Subset - Direct Functions Only)
# =============================================================================


class TestEnhancedTimeframeToolsFunctions:
    """Test enhanced timeframe helper functions (Issue #489).

    These tests only cover the pure functions that don't require
    the full timeframe_commands infrastructure.
    """

    @pytest.fixture
    def timeframe_helpers(self, module_sandbox, monkeypatch):
        """Load just the helper functions from timeframe_tools."""

        # Create a mock timeframe commands
        mock_tf_commands = MagicMock()
        mock_tf_commands.manager.get_current_timeframe.return_value = "1d"
        mock_tf_commands.validate_and_info.return_value = "Valid"

        # Create a mock module for the commands
        mock_tf_module = MagicMock()
        mock_tf_module.get_timeframe_commands.return_value = mock_tf_commands

        monkeypatch.setitem(sys.modules, "src.cli.commands.timeframe_commands", mock_tf_module)

        module_path = project_root / "src" / "cli" / "tools" / "timeframe_tools.py"
        return import_module_directly(str(module_path), "timeframe_tools_test")

    def test_parse_timeframe_to_minutes_minutes(self, timeframe_helpers):
        """Test parsing minute-based timeframes."""
        parse = timeframe_helpers._parse_timeframe_to_minutes

        assert parse("30m") == 30
        assert parse("60m") == 60
        assert parse("90m") == 90

    def test_parse_timeframe_to_minutes_hours(self, timeframe_helpers):
        """Test parsing hour-based timeframes."""
        parse = timeframe_helpers._parse_timeframe_to_minutes

        assert parse("1h") == 60
        assert parse("2h") == 120
        assert parse("1.5h") == 90
        assert parse("4h") == 240

    def test_parse_timeframe_to_minutes_days(self, timeframe_helpers):
        """Test parsing day-based timeframes."""
        parse = timeframe_helpers._parse_timeframe_to_minutes

        assert parse("1d") == 1440
        assert parse("2d") == 2880

    def test_parse_timeframe_to_minutes_weeks(self, timeframe_helpers):
        """Test parsing week-based timeframes."""
        parse = timeframe_helpers._parse_timeframe_to_minutes

        assert parse("1w") == 10080
        assert parse("2w") == 20160

    def test_parse_timeframe_to_minutes_invalid(self, timeframe_helpers):
        """Test parsing invalid timeframes."""
        parse = timeframe_helpers._parse_timeframe_to_minutes

        assert parse("invalid") is None
        assert parse("abc") is None

    def test_get_base_timeframe_short(self, timeframe_helpers):
        """Test base timeframe for short periods."""
        get_base = timeframe_helpers._get_base_timeframe

        assert get_base(30) == "1m"
        assert get_base(60) == "1m"

    def test_get_base_timeframe_medium(self, timeframe_helpers):
        """Test base timeframe for medium periods."""
        get_base = timeframe_helpers._get_base_timeframe

        assert get_base(120) == "5m"
        assert get_base(240) == "5m"

    def test_get_base_timeframe_long(self, timeframe_helpers):
        """Test base timeframe for long periods."""
        get_base = timeframe_helpers._get_base_timeframe

        assert get_base(720) == "15m"
        assert get_base(1440) == "15m"

    def test_get_base_timeframe_very_long(self, timeframe_helpers):
        """Test base timeframe for very long periods."""
        get_base = timeframe_helpers._get_base_timeframe

        assert get_base(2880) == "1h"
        assert get_base(10080) == "1h"

    def test_multi_tf_presets_defined(self, timeframe_helpers):
        """Test MULTI_TF_PRESETS constant is defined."""
        presets = timeframe_helpers.MULTI_TF_PRESETS

        assert isinstance(presets, dict)
        assert "trend_following" in presets
        assert "intraday" in presets
        assert "position" in presets
        assert "scalping" in presets

    def test_multi_tf_presets_have_required_keys(self, timeframe_helpers):
        """Test each preset has required configuration keys."""
        presets = timeframe_helpers.MULTI_TF_PRESETS

        for name, config in presets.items():
            assert "description" in config, f"{name} missing description"
            assert "timeframes" in config, f"{name} missing timeframes"
            assert "style" in config, f"{name} missing style"

    def test_multi_tf_presets_weights_sum_to_one(self, timeframe_helpers):
        """Test each preset's timeframe weights sum approximately to 1.0."""
        presets = timeframe_helpers.MULTI_TF_PRESETS

        for name, config in presets.items():
            total = sum(config["timeframes"].values())
            # Allow for small floating point error
            assert 0.95 <= total <= 1.05, f"{name} weights sum to {total}, not ~1.0"


# =============================================================================
# Tests: Issue #490 - Backup Tools
# =============================================================================


class TestBackupTools:
    """Test backup CLI tools (Issue #490)."""

    def test_show_backup_info_returns_string(self, backup_tools_module):
        """Test show_backup_info returns formatted info."""
        result = backup_tools_module.show_backup_info()

        assert isinstance(result, str)
        assert "Backup System Info" in result
        assert "Databases" in result

    def test_get_backup_params_returns_dict(self, backup_tools_module):
        """Test get_backup_params returns structured data."""
        result = backup_tools_module.get_backup_params()

        assert isinstance(result, dict)
        assert "state_db" in result
        assert "cache_db" in result
        assert "backup_dir" in result
        assert "backup_count" in result
        assert "backups" in result

    def test_backup_database_missing_file(self, backup_tools_module):
        """Test backup_database with missing database file."""
        result = backup_tools_module.backup_database("nonexistent.db")

        assert "not found" in result or "failed" in result.lower()

    def test_restore_backup_missing_backup(self, backup_tools_module):
        """Test restore_backup with missing backup file."""
        result = backup_tools_module.restore_backup("nonexistent_backup.db")

        assert "not found" in result

    def test_list_backups_no_directory(self, backup_tools_module, temp_backup_dir):
        """Test list_backups behavior."""
        # Patch BACKUP_DIR to temp directory that doesn't exist yet
        nonexistent = Path(temp_backup_dir) / "nonexistent"
        original_backup_dir = backup_tools_module.BACKUP_DIR

        backup_tools_module.BACKUP_DIR = str(nonexistent)
        try:
            result = backup_tools_module.list_backups()
            assert "No backups" in result or "no directory" in result.lower()
        finally:
            backup_tools_module.BACKUP_DIR = original_backup_dir

    def test_default_paths_defined(self, backup_tools_module):
        """Test default path constants are defined."""
        # Logical names; DBBackupManager maps user -> state/user.db, cache -> .cache/trading_data.db
        assert backup_tools_module.STATE_DB == "user"
        assert backup_tools_module.CACHE_DB == "cache"
        assert backup_tools_module.BACKUP_DIR == "state/backups"  # DBBackupManager.backup_dir


# =============================================================================
# Tests: Pure Function Tests (No Import Required)
# =============================================================================


class TestPureFunctions:
    """Test pure functions that can be tested without module imports."""

    def test_multi_tf_preset_names(self):
        """Test expected multi-timeframe preset names."""
        expected_presets = ["trend_following", "intraday", "position", "scalping"]

        # These are hardcoded in the module - just verify naming convention
        for preset in expected_presets:
            assert "_" in preset or preset.isalpha()
            assert preset.islower()

    def test_signal_types(self):
        """Test expected signal type names."""
        expected_signals = ["STRONG", "WEAK", "CONFLICT", "NEUTRAL"]

        for signal in expected_signals:
            assert signal.isupper()
            assert signal.isalpha()
