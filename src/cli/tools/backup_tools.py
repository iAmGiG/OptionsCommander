"""
Database Backup CLI Tools - FunctionTool wrappers for DB backup/restore.

Issue #490: /backup command group for database management.
Issue #483: DB Backup/Migration (core implementation).

This module provides FunctionTool wrappers for database backup, restore,
and export operations. When DBBackupManager is available, these tools
will integrate with it. Until then, they provide helpful stubs.

Original Implementation: src/utils/db_backup.py (when available)
Pattern: Pure function wrappers → FunctionTool → Registry
"""

from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from agent_framework import FunctionTool

from src.utils.date_utils import format_for_filename
from src.utils.safe_print import get_symbol
from src.utils.state_paths import get_cache_db_path, get_state_dir, get_user_db_path

# Default database names (for DBBackupManager)
# DBBackupManager maps these to actual paths: user -> state/user.db, cache -> .cache/trading_data.db
STATE_DB = "user"  # Maps to state/user.db
CACHE_DB = "cache"  # Maps to .cache/trading_data.db
# Fallback paths, used only when DBBackupManager cannot be imported; with the manager,
# its own (project-root) paths are used so the result does not depend on the cwd.
BACKUP_DIR = "state/backups"
DB_FILES = {STATE_DB: "state/user.db", CACHE_DB: ".cache/trading_data.db"}


# ============================================================================
# Pure Function Wrappers
# ============================================================================


def _failure_text(result, verb: str) -> str:
    """Error text from a DBBackupManager result without repeating '<verb> failed:'."""
    message = getattr(result, "message", "") or "unknown error"
    prefix = f"{verb} failed:"
    if message.lower().startswith(prefix.lower()):
        message = message[len(prefix) :].strip()
    return message


def _get_backup_manager():
    """Try to get DBBackupManager if available."""
    try:
        from src.utils.db_backup import DBBackupManager

        return DBBackupManager()
    except ImportError:
        return None


def _backup_dir(manager) -> Path:
    return Path(manager.backup_dir) if manager else Path(BACKUP_DIR)


def _db_file(manager, db_name: str) -> Path:
    if manager:
        return Path(manager.get_db_path(db_name))
    if db_name == STATE_DB:
        return Path(get_user_db_path())
    if db_name == CACHE_DB:
        return Path(get_cache_db_path())
    return Path(db_name)


def _backup_db_name(backup_name: str):
    """Database a backup file belongs to, from its 'user_' / 'cache_' prefix."""
    prefix = backup_name.split("_", 1)[0]
    return prefix if prefix in DB_FILES else None


def backup_database(db_path: str = STATE_DB) -> str:
    """
    Create a backup of a database.

    Creates a timestamped backup copy of the specified database in state/backups/.

    Args:
        db_path: Database name, "user" (state/user.db, default) or "cache"
            (.cache/trading_data.db)

    Returns:
        Success message with backup path, or error message

    Example:
        >>> backup_database()
        'Backup created: state/backups/user_20241215_143022.db'
        >>> backup_database('cache')
        'Backup created: state/backups/cache_20241215_143022.db'
    """
    manager = _get_backup_manager()

    if manager:
        try:
            result = manager.backup_database(db_path)
        except Exception as e:  # e.g. unknown database name
            return f"{get_symbol('ERROR')} Backup failed: {e}"
        if result.success:
            return f"{get_symbol('SUCCESS')} Backup created: {result.backup_path}"
        return f"{get_symbol('ERROR')} Backup failed: {_failure_text(result, 'Backup')}"

    # Stub implementation - manual file copy
    source = _db_file(None, db_path)
    if not source.exists():
        return f"{get_symbol('ERROR')} Database not found: {db_path}"

    backup_dir = _backup_dir(None)
    backup_dir.mkdir(parents=True, exist_ok=True)
    name = db_path if db_path in DB_FILES else source.stem
    backup_path = backup_dir / f"{name}_{format_for_filename()}.db"

    try:
        import shutil

        shutil.copy2(source, backup_path)
        return f"{get_symbol('SUCCESS')} Backup created: {backup_path}"
    except Exception as e:
        return f"{get_symbol('ERROR')} Backup failed: {e}"


def list_backups() -> str:
    """
    List all available database backups.

    Shows the backup files in state/backups/ with the database each belongs to,
    newest first.

    Returns:
        Formatted list of available backups

    Example:
        >>> list_backups()
        'Available Backups
        user_20241215_143022.db  (user, 1.2 MB)  2024-12-15 14:30:22'
    """
    backup_dir = _backup_dir(_get_backup_manager())

    if not backup_dir.exists():
        return f"{get_symbol('MAILBOX')} No backups directory found.\n\nRun '/backup create' to create your first backup."

    backups = list(backup_dir.glob("*.db"))

    if not backups:
        return f"{get_symbol('MAILBOX')} No backups found.\n\nRun '/backup create' to create your first backup."

    # Sort by modification time (newest first)
    backups.sort(key=lambda p: p.stat().st_mtime, reverse=True)

    output = f"{get_symbol('MAILBOX')} Available Backups ({backup_dir})\n"
    output += "=" * 60 + "\n\n"

    for backup in backups:
        stat = backup.stat()
        size_mb = stat.st_size / (1024 * 1024)
        mod_time = datetime.fromtimestamp(stat.st_mtime)

        output += f"  {backup.name}\n"
        output += f"    Database: {_backup_db_name(backup.name) or 'unknown'}\n"
        output += f"    Size: {size_mb:.1f} MB\n"
        output += f"    Date: {mod_time.strftime('%Y-%m-%d %H:%M:%S')}\n\n"

    output += "-" * 60 + "\n"
    output += f"Total: {len(backups)} backup(s)\n"

    return output


def _invalid_backup_name(backup_name: str):
    """Error message if the name is not a plain backup file name (no directories)."""
    if Path(backup_name).name != backup_name or not backup_name.endswith(".db"):
        return (
            f"{get_symbol('ERROR')} Invalid backup name: {backup_name}\n\n"
            "Give a file name from '/backup list' (no directories)."
        )
    return None


def _restore_target(backup_name: str, target_db: str):
    """Database a restore writes to; returns (target database name, error message)."""
    inferred = _backup_db_name(backup_name)
    target = target_db or inferred
    if target not in DB_FILES:
        return None, (
            f"{get_symbol('ERROR')} Cannot tell which database {backup_name} belongs to; "
            f"pass target_db as one of {sorted(DB_FILES)}."
        )
    if inferred and target != inferred:
        return None, (
            f"{get_symbol('ERROR')} {backup_name} is a backup of the '{inferred}' database, "
            f"not '{target}'."
        )
    return target, None


def restore_backup(backup_name: str, target_db: str = "") -> str:
    """
    Restore a database from a backup.

    Replaces the database the backup belongs to (from its 'user_' / 'cache_' prefix)
    with the backup copy. The current database is backed up first.
    WARNING: This will overwrite the current database!

    Args:
        backup_name: File name of a backup listed by /backup list
            (e.g., 'user_20241215_143022.db'); paths are not accepted
        target_db: Optional database name ("user" or "cache"); must match the backup

    Returns:
        Success or error message

    Example:
        >>> restore_backup('user_20241215_143022.db')
        'Database restored from: state/backups/user_20241215_143022.db'
    """
    error = _invalid_backup_name(backup_name)
    if error:
        return error

    manager = _get_backup_manager()
    backup_path = _backup_dir(manager) / backup_name

    if not backup_path.is_file():
        return f"{get_symbol('ERROR')} Backup not found: {backup_name}\n\nRun '/backup list' to see available backups."
    if backup_path.stat().st_size == 0:
        return f"{get_symbol('ERROR')} Backup file is empty: {backup_name}"

    target, error = _restore_target(backup_name, target_db)
    if error:
        return error

    if manager:
        try:
            result = manager.restore_backup(str(backup_path), target)
        except Exception as e:
            return f"{get_symbol('ERROR')} Restore failed: {e}"
        if result.success:
            return f"{get_symbol('SUCCESS')} Database restored from: {backup_path}"
        return f"{get_symbol('ERROR')} Restore failed: {_failure_text(result, 'Restore')}"

    # Stub implementation - manual file copy
    target_file = _db_file(None, target)
    try:
        import shutil

        # Create backup of current before restore
        if target_file.exists():
            shutil.copy2(target_file, f"{target_file}.pre_restore")

        shutil.copy2(backup_path, target_file)
        return f"{get_symbol('SUCCESS')} Database restored from: {backup_path}\n   (Previous DB saved to: {target_file}.pre_restore)"
    except Exception as e:
        return f"{get_symbol('ERROR')} Restore failed: {e}"


def export_table(
    table_name: str,
    output_path: str = "",
    db_path: str = STATE_DB,
) -> str:
    """
    Export a database table to JSON.

    Exports all rows from a table to a JSON file for
    backup, analysis, or migration purposes.

    Args:
        table_name: Name of table to export
        output_path: Output JSON file path (default: a new timestamped file in state/exports/)
        db_path: Database name, "user" (default) or "cache"

    Returns:
        Success message with output path, or error message

    Example:
        >>> export_table('voter_ranking_history')
        'Exported 150 rows to: state/exports/voter_ranking_history_20241215_143022.json'
    """
    manager = _get_backup_manager()

    if manager:
        try:
            result = manager.export_table(db_path, table_name, output_path or None)
        except Exception as e:
            return f"{get_symbol('ERROR')} Export failed: {e}"
        if result.success:
            return (
                f"{get_symbol('SUCCESS')} Exported {result.row_count} rows to: {result.output_path}"
            )
        return f"{get_symbol('ERROR')} Export failed: {_failure_text(result, 'Export')}"

    # Stub implementation using sqlite3
    import json
    import sqlite3

    source = _db_file(None, db_path)
    if not source.exists():
        return f"{get_symbol('ERROR')} Database not found: {db_path}"
    if output_path and Path(output_path).exists():
        return (
            f"{get_symbol('ERROR')} Export failed: {output_path} already exists; "
            "exports never overwrite files"
        )

    try:
        conn = sqlite3.connect(str(source))
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()

        # Check table exists
        cursor.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name=?",
            (table_name,),
        )
        if not cursor.fetchone():
            cursor.execute("SELECT name FROM sqlite_master WHERE type='table'")
            tables = [row[0] for row in cursor.fetchall()]
            conn.close()
            return f"{get_symbol('ERROR')} Table not found: {table_name}\n\nAvailable tables: {', '.join(tables)}"

        # Export data
        # Table name is validated above via sqlite_master query
        cursor.execute(f"SELECT * FROM {table_name}")  # noqa: S608  # nosec B608
        rows = [dict(row) for row in cursor.fetchall()]
        conn.close()

        # Generate output path if not provided
        if not output_path:
            exports_dir = get_state_dir() / "exports"
            exports_dir.mkdir(parents=True, exist_ok=True)
            output_path = str(exports_dir / f"{table_name}_{format_for_filename()}.json")

        # Write JSON
        Path(output_path).parent.mkdir(parents=True, exist_ok=True)
        with open(output_path, "x") as f:  # never overwrite an earlier file
            json.dump(rows, f, indent=2, default=str)

        return f"{get_symbol('SUCCESS')} Exported {len(rows)} rows to: {output_path}"
    except Exception as e:
        return f"{get_symbol('ERROR')} Export failed: {e}"


def show_backup_info() -> str:
    """
    Show backup system information and status.

    Displays backup configuration, available databases,
    and recent backup activity.

    Returns:
        Formatted backup system information

    Example:
        >>> show_backup_info()
        'Backup System Info
        State DB: state/user.db (2.3 MB)
        Cache DB: .cache/trading_data.db (15.1 MB)
        Backups: 5 files (23.4 MB total)'
    """
    manager = _get_backup_manager()
    output = f"{get_symbol('INFO')} Backup System Info\n"
    output += "=" * 50 + "\n\n"

    # Check databases
    output += "Databases:\n"
    for db_label, db_name in [("State DB", STATE_DB), ("Cache DB", CACHE_DB)]:
        db_file = _db_file(manager, db_name)
        if db_file.exists():
            size_mb = db_file.stat().st_size / (1024 * 1024)
            output += f"  {db_label}: {db_file} ({size_mb:.1f} MB)\n"
        else:
            output += f"  {db_label}: {db_file} (not found)\n"

    output += "\n"

    # Check backups
    backup_dir = _backup_dir(manager)
    if backup_dir.exists():
        backups = list(backup_dir.glob("*.db"))
        total_size = sum(b.stat().st_size for b in backups) / (1024 * 1024)
        output += f"Backups Directory: {backup_dir}\n"
        output += f"  Total Backups: {len(backups)}\n"
        output += f"  Total Size: {total_size:.1f} MB\n"

        if backups:
            newest = max(backups, key=lambda p: p.stat().st_mtime)
            newest_time = datetime.fromtimestamp(newest.stat().st_mtime)
            output += f"  Latest: {newest.name}\n"
            output += f"  Created: {newest_time.strftime('%Y-%m-%d %H:%M:%S')}\n"
    else:
        output += "Backups Directory: Not created yet\n"
        output += "  Run '/backup create' to create your first backup\n"

    return output


def cleanup_old_backups(days: int = 30, only: Optional[List[str]] = None) -> str:
    """
    Clean up backups older than specified days.

    Removes backup files older than the specified number of days to free up disk space.
    The 5 newest backups of each database are always kept.

    Args:
        days: Keep backups newer than this many days (default: 30)
        only: Remove only these names, as listed by backups_to_clean (default: all that qualify)

    Returns:
        Cleanup result with files removed

    Example:
        >>> cleanup_old_backups(30)
        'Cleanup complete: Removed 3 backups older than 30 days'
    """
    manager = _get_backup_manager()
    if not manager:
        return f"{get_symbol('ERROR')} Backup manager not available: nothing removed"

    try:
        removed = manager.cleanup_old_backups(keep_days=days, only=only)  # returns a count
    except Exception as e:
        return f"{get_symbol('ERROR')} Cleanup failed: {e}"
    return (
        f"{get_symbol('SUCCESS')} Cleanup complete: Removed {removed} backup(s) older than "
        f"{days} days (the 5 newest backups of each database are always kept)"
    )


def backups_to_clean(days: int = 30) -> List[str]:
    """Names of the backups cleanup_old_backups(days) would remove."""
    manager = _get_backup_manager()
    return [backup.name for backup in manager.old_backups(keep_days=days)] if manager else []


def get_backup_params() -> Dict[str, Any]:
    """
    Get backup configuration as structured data for agents.

    Provides backup system information in a structured format
    suitable for programmatic consumption.

    Returns:
        Dictionary with backup configuration and status

    Example:
        >>> get_backup_params()
        {'state_db': 'state/user.db', 'backup_count': 5, ...}
    """
    manager = _get_backup_manager()
    backup_dir = _backup_dir(manager)
    backups: List[Dict[str, Any]] = []

    if backup_dir.exists():
        for backup in backup_dir.glob("*.db"):
            stat = backup.stat()
            backups.append(
                {
                    "name": backup.name,
                    "database": _backup_db_name(backup.name),
                    "size_bytes": stat.st_size,
                    "modified": datetime.fromtimestamp(stat.st_mtime).isoformat(),
                }
            )

    state_db_path = _db_file(manager, STATE_DB)
    cache_db_path = _db_file(manager, CACHE_DB)

    return {
        "state_db": str(state_db_path),
        "cache_db": str(cache_db_path),
        "backup_dir": str(backup_dir),
        "state_db_exists": state_db_path.exists(),
        "cache_db_exists": cache_db_path.exists(),
        "backup_count": len(backups),
        "backups": sorted(backups, key=lambda x: x["modified"], reverse=True),
    }


# ============================================================================
# FunctionTool Definitions
# ============================================================================

backup_database_tool = FunctionTool(
    func=backup_database,
    name="backup_database",
    description=(
        "Create a backup of a database ('user' = state/user.db, default; 'cache'). "
        "Backups are stored with timestamps in state/backups/."
    ),
)

list_backups_tool = FunctionTool(
    func=list_backups,
    name="list_backups",
    description=("List all available database backups with their dates and sizes."),
)

restore_backup_tool = FunctionTool(
    func=restore_backup,
    name="restore_backup",
    description=(
        "Restore a database from a backup file listed by list_backups. The database is "
        "chosen from the backup name; the current one is backed up first. "
        "WARNING: This will overwrite the current database."
    ),
)

export_table_tool = FunctionTool(
    func=export_table,
    name="export_table",
    description=(
        "Export a database table to JSON for backup or analysis. "
        "Specify table name and optional output path."
    ),
)

show_backup_info_tool = FunctionTool(
    func=show_backup_info,
    name="show_backup_info",
    description=(
        "Show backup system information including database sizes, "
        "backup counts, and latest backup details."
    ),
)

cleanup_old_backups_tool = FunctionTool(
    func=cleanup_old_backups,
    name="cleanup_old_backups",
    description=(
        "Clean up backups older than specified days to free disk space. Default is 30 days."
    ),
)

get_backup_params_tool = FunctionTool(
    func=get_backup_params,
    name="get_backup_params",
    description=("Get backup configuration and status as structured data for agents."),
)


# ============================================================================
# Tool Collection for Registry
# ============================================================================

CLI_BACKUP_TOOLS = [
    backup_database_tool,
    list_backups_tool,
    restore_backup_tool,
    export_table_tool,
    show_backup_info_tool,
    cleanup_old_backups_tool,
    get_backup_params_tool,
]

__all__ = [
    # Functions
    "backup_database",
    "list_backups",
    "restore_backup",
    "export_table",
    "show_backup_info",
    "cleanup_old_backups",
    "get_backup_params",
    # Tools
    "backup_database_tool",
    "list_backups_tool",
    "restore_backup_tool",
    "export_table_tool",
    "show_backup_info_tool",
    "cleanup_old_backups_tool",
    "get_backup_params_tool",
    # Collection
    "CLI_BACKUP_TOOLS",
]
