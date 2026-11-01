#!/usr/bin/env python3
"""
Database Backup and Migration Utilities

Issue #483: Database backup and migration utilities

Provides utilities for:
- Backing up SQLite databases
- Exporting tables to JSON
- Importing from JSON
- Schema migrations
- Cleanup/maintenance
"""

import json
import logging
import os
import shutil
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional

from src.utils.state_paths import get_cache_db_path, get_state_dir

logger = logging.getLogger(__name__)

SQLITE_HEADER = b"SQLite format 3\x00"

# Database paths relative to project root
DB_PATHS = {
    "user": "state/user.db",
    "cache": ".cache/trading_data.db",
}


@dataclass
class BackupResult:
    """Result of a backup operation."""

    success: bool
    source_path: str
    backup_path: Optional[str]
    message: str
    timestamp: str


@dataclass
class ExportResult:
    """Result of an export operation."""

    success: bool
    table_name: str
    output_path: Optional[str]
    row_count: int
    message: str


@dataclass
class ImportResult:
    """Result of an import operation."""

    success: bool
    table_name: str
    source_path: str
    rows_imported: int
    message: str


class DBBackupManager:
    """
    Manages database backup, export, import, and maintenance operations.

    Usage:
        manager = DBBackupManager()
        result = manager.backup_database("user")
        result = manager.export_table("user", "voter_ranking_history", "exports/voter.json")
    """

    def __init__(self, project_root: Optional[str] = None):
        """
        Initialize backup manager.

        Args:
            project_root: Path to project root. Defaults to auto-detect.
        """
        explicit_root = project_root is not None
        if project_root is None:
            # Auto-detect project root (look for state/ directory)
            current = Path(__file__).resolve().parent
            while current != current.parent:
                if (current / "state").exists() or (current / "src").exists():
                    project_root = str(current)
                    break
                current = current.parent
            else:
                project_root = str(Path.cwd())

        self.project_root = Path(project_root)
        # user.db, backups and exports live in the state dir: <project_root>/state for an
        # explicit root, otherwise get_state_dir() (honours $AUTOTRADER_STATE_DIR).
        self.state_dir = self.project_root / "state" if explicit_root else get_state_dir()
        # The cache likewise: <project_root>/.cache, or get_cache_db_path() ($AUTOTRADER_CACHE_DIR)
        self.cache_db_path = (
            self.project_root / DB_PATHS["cache"] if explicit_root else Path(get_cache_db_path())
        )
        self.backup_dir = self.state_dir / "backups"
        self.export_dir = self.state_dir / "exports"

        # Ensure directories exist
        self.backup_dir.mkdir(parents=True, exist_ok=True)
        self.export_dir.mkdir(parents=True, exist_ok=True)

        logger.info(f"DBBackupManager initialized at {self.project_root}")

    def get_db_path(self, db_name: str) -> Path:
        """Full path of a database by name ("user" or "cache")."""
        return self._get_db_path(db_name)

    def _get_db_path(self, db_name: str) -> Path:
        """Get full path for a database by name."""
        if db_name not in DB_PATHS:
            raise ValueError(f"Unknown database: {db_name}. Available: {list(DB_PATHS.keys())}")
        if db_name == "user":
            return self.state_dir / "user.db"
        return self.cache_db_path

    def _generate_backup_filename(self, db_name: str) -> str:
        """Generate a timestamped backup filename that does not overwrite an existing backup.

        Timestamps have one-second resolution; restore_backup takes a pre-restore backup, so
        without the suffix a restore within the same second overwrote the file being restored.
        """
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        name = f"{db_name}_{timestamp}.db"
        n = 1
        while (self.backup_dir / name).exists():
            name = f"{db_name}_{timestamp}_{n}.db"
            n += 1
        return name

    def backup_database(self, db_name: str = "user") -> BackupResult:
        """
        Create a backup of the specified database.

        Args:
            db_name: Database name ("user" or "cache")

        Returns:
            BackupResult with backup details
        """
        timestamp = datetime.now().isoformat()

        try:
            source_path = self._get_db_path(db_name)
            if not source_path.exists():
                return BackupResult(
                    success=False,
                    source_path=str(source_path),
                    backup_path=None,
                    message=f"Database not found: {source_path}",
                    timestamp=timestamp,
                )

            backup_filename = self._generate_backup_filename(db_name)
            backup_path = self.backup_dir / backup_filename

            # Use SQLite backup API for consistency
            source_conn = backup_conn = None
            try:
                source_conn = sqlite3.connect(str(source_path))
                backup_conn = sqlite3.connect(str(backup_path))
                with backup_conn:
                    source_conn.backup(backup_conn)
            except Exception:
                if backup_conn is not None:
                    backup_conn.close()
                # Never leave a partial or empty file that list/restore would treat as a backup
                backup_path.unlink(missing_ok=True)
                raise
            finally:
                for conn in (source_conn, backup_conn):
                    if conn is not None:
                        conn.close()

            # Verify backup
            backup_size = backup_path.stat().st_size
            logger.info(f"Backed up {db_name} to {backup_path} ({backup_size} bytes)")

            return BackupResult(
                success=True,
                source_path=str(source_path),
                backup_path=str(backup_path),
                message=f"Successfully backed up to {backup_filename}",
                timestamp=timestamp,
            )

        except Exception as e:
            logger.error(f"Backup failed for {db_name}: {e}")
            return BackupResult(
                success=False,
                source_path=DB_PATHS.get(db_name, db_name),  # _get_db_path may be what failed
                backup_path=None,
                message=f"Backup failed: {e}",
                timestamp=timestamp,
            )

    def list_backups(self, db_name: Optional[str] = None) -> List[Dict[str, Any]]:
        """
        List all available backups.

        Args:
            db_name: Filter by database name (optional)

        Returns:
            List of backup info dicts
        """
        backups = []
        pattern = f"{db_name}_*.db" if db_name else "*.db"

        for backup_file in self.backup_dir.glob(pattern):
            stat = backup_file.stat()
            backups.append(
                {
                    "filename": backup_file.name,
                    "path": str(backup_file),
                    "size_bytes": stat.st_size,
                    "created": datetime.fromtimestamp(stat.st_ctime).isoformat(),
                    "modified": datetime.fromtimestamp(stat.st_mtime).isoformat(),
                }
            )

        return sorted(backups, key=lambda x: x["created"], reverse=True)

    def _save_current_before_restore(self, db_name: str, target_path: Path, timestamp: str):
        """Back up the live DB before a restore.

        Returns (target_unreadable, error). If the SQLite backup fails (a damaged DB, the
        main reason to restore), the raw file is set aside as ``.corrupt`` (never listed or
        restored) and the restore continues.
        """
        if not target_path.exists():
            return False, None
        pre_restore_backup = self.backup_database(db_name)
        if pre_restore_backup.success:
            return False, None
        aside = self.backup_dir / f"{db_name}_{timestamp[:19].replace(':', '')}.corrupt"
        try:
            shutil.copy2(target_path, aside)
        except OSError as e:
            return False, (
                f"Could not save the current DB before restore "
                f"({pre_restore_backup.message}; copy failed: {e})"
            )
        logger.warning(
            f"Current {db_name} DB could not be backed up "
            f"({pre_restore_backup.message}); raw copy saved to {aside}"
        )
        return True, None

    @staticmethod
    def _write_restore(backup_file: Path, target_path: Path, replace_file: bool) -> None:
        """Copy a backup into the target DB."""
        if replace_file:
            # The SQLite backup API cannot write into a damaged file: replace it whole
            tmp = target_path.with_suffix(".restore_tmp")
            shutil.copy2(backup_file, tmp)
            os.replace(tmp, target_path)
            return
        # SQLite backup API: safe while other connections have the DB open
        source_conn = target_conn = None
        try:
            source_conn = sqlite3.connect(str(backup_file))
            target_conn = sqlite3.connect(str(target_path))
            with target_conn:
                source_conn.backup(target_conn)
        finally:
            for conn in (source_conn, target_conn):
                if conn is not None:
                    conn.close()

    def restore_backup(self, backup_path: str, db_name: str = "user") -> BackupResult:
        """
        Restore a database from backup.

        Args:
            backup_path: Path to backup file
            db_name: Target database name

        Returns:
            BackupResult with restore details
        """
        timestamp = datetime.now().isoformat()

        def failed(message: str) -> BackupResult:
            return BackupResult(
                success=False,
                source_path=backup_path,
                backup_path=None,
                message=message,
                timestamp=timestamp,
            )

        try:
            backup_file = Path(backup_path)
            if not backup_file.is_file():
                return failed(f"Backup file not found: {backup_path}")
            with open(backup_file, "rb") as fh:
                if fh.read(len(SQLITE_HEADER)) != SQLITE_HEADER:
                    return failed(f"Not a SQLite database backup: {backup_file.name}")

            target_path = self._get_db_path(db_name)
            if target_path.exists() and backup_file.resolve() == target_path.resolve():
                return failed("Backup file is the live database; choose a file from backups")

            # Keep a copy of the current DB before replacing it
            target_unreadable, error = self._save_current_before_restore(
                db_name, target_path, timestamp
            )
            if error:
                return failed(error)
            self._write_restore(backup_file, target_path, replace_file=target_unreadable)

            logger.info(f"Restored {db_name} from {backup_path}")

            return BackupResult(
                success=True,
                source_path=backup_path,
                backup_path=str(target_path),
                message=f"Successfully restored {db_name} from backup",
                timestamp=timestamp,
            )

        except Exception as e:
            logger.error(f"Restore failed: {e}")
            return BackupResult(
                success=False,
                source_path=backup_path,
                backup_path=None,
                message=f"Restore failed: {e}",
                timestamp=timestamp,
            )

    def export_table(
        self,
        db_name: str,
        table_name: str,
        output_path: Optional[str] = None,
    ) -> ExportResult:
        """
        Export a table to JSON.

        Args:
            db_name: Database name
            table_name: Table to export
            output_path: Output file path (auto-generated if None). An existing file is
                never overwritten; the export fails instead.

        Returns:
            ExportResult with export details
        """
        try:
            if output_path is not None and Path(output_path).exists():
                return ExportResult(
                    success=False,
                    table_name=table_name,
                    output_path=None,
                    row_count=0,
                    message=f"{output_path} already exists; exports never overwrite files",
                )

            db_path = self._get_db_path(db_name)
            if not db_path.exists():
                return ExportResult(
                    success=False,
                    table_name=table_name,
                    output_path=None,
                    row_count=0,
                    message=f"Database not found: {db_path}",
                )

            conn = sqlite3.connect(str(db_path))
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()

            # Check if table exists
            cursor.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name=?",
                (table_name,),
            )
            if not cursor.fetchone():
                conn.close()
                return ExportResult(
                    success=False,
                    table_name=table_name,
                    output_path=None,
                    row_count=0,
                    message=f"Table not found: {table_name}",
                )

            # Export data
            cursor.execute(f"SELECT * FROM {table_name}")  # noqa: S608  # nosec B608
            rows = [dict(row) for row in cursor.fetchall()]
            conn.close()

            # Generate output path if not provided (never overwrite an earlier export)
            if output_path is None:
                timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
                name = f"{table_name}_{timestamp}.json"
                n = 1
                while (self.export_dir / name).exists():
                    name = f"{table_name}_{timestamp}_{n}.json"
                    n += 1
                output_path = str(self.export_dir / name)

            # Write JSON
            output_file = Path(output_path)
            output_file.parent.mkdir(parents=True, exist_ok=True)

            # "x" fails if the file appeared since the check above: never overwrite
            with open(output_file, "x", encoding="utf-8") as f:
                json.dump(
                    {
                        "table": table_name,
                        "database": db_name,
                        "exported_at": datetime.now().isoformat(),
                        "row_count": len(rows),
                        "data": rows,
                    },
                    f,
                    indent=2,
                    default=str,
                )

            logger.info(f"Exported {len(rows)} rows from {table_name} to {output_path}")

            return ExportResult(
                success=True,
                table_name=table_name,
                output_path=str(output_path),
                row_count=len(rows),
                message=f"Exported {len(rows)} rows to {output_file.name}",
            )

        except Exception as e:
            logger.error(f"Export failed for {table_name}: {e}")
            return ExportResult(
                success=False,
                table_name=table_name,
                output_path=None,
                row_count=0,
                message=f"Export failed: {e}",
            )

    def import_table(
        self,
        source_path: str,
        db_name: str,
        table_name: Optional[str] = None,
        mode: str = "append",
    ) -> ImportResult:
        """
        Import data from JSON into a table.

        Args:
            source_path: Path to JSON file
            db_name: Target database
            table_name: Target table (uses source if None)
            mode: "append" or "replace"

        Returns:
            ImportResult with import details
        """
        try:
            source_file = Path(source_path)
            if not source_file.exists():
                return ImportResult(
                    success=False,
                    table_name=table_name or "unknown",
                    source_path=source_path,
                    rows_imported=0,
                    message=f"Source file not found: {source_path}",
                )

            with open(source_file, "r", encoding="utf-8") as f:
                data = json.load(f)

            # Get table name from file if not provided
            if table_name is None:
                table_name = data.get("table")
                if not table_name:
                    return ImportResult(
                        success=False,
                        table_name="unknown",
                        source_path=source_path,
                        rows_imported=0,
                        message="No table name in JSON and none provided",
                    )

            rows = data.get("data", [])
            if not rows:
                return ImportResult(
                    success=True,
                    table_name=table_name,
                    source_path=source_path,
                    rows_imported=0,
                    message="No data to import",
                )

            db_path = self._get_db_path(db_name)
            conn = sqlite3.connect(str(db_path))
            cursor = conn.cursor()

            # Clear table if replace mode
            if mode == "replace":
                cursor.execute(f"DELETE FROM {table_name}")  # noqa: S608  # nosec B608

            # Get column names from first row
            columns = list(rows[0].keys())
            placeholders = ",".join(["?" for _ in columns])
            col_names = ",".join(columns)

            # Insert rows
            inserted = 0
            for row in rows:
                values = [row.get(col) for col in columns]
                try:
                    cursor.execute(
                        f"INSERT INTO {table_name} ({col_names}) VALUES ({placeholders})",  # noqa: S608  # nosec B608
                        values,
                    )
                    inserted += 1
                except sqlite3.IntegrityError as e:
                    logger.warning(f"Skipping duplicate row: {e}")

            conn.commit()
            conn.close()

            logger.info(f"Imported {inserted} rows into {table_name}")

            return ImportResult(
                success=True,
                table_name=table_name,
                source_path=source_path,
                rows_imported=inserted,
                message=f"Imported {inserted} rows into {table_name}",
            )

        except Exception as e:
            logger.error(f"Import failed: {e}")
            return ImportResult(
                success=False,
                table_name=table_name or "unknown",
                source_path=source_path,
                rows_imported=0,
                message=f"Import failed: {e}",
            )

    def list_tables(self, db_name: str) -> List[Dict[str, Any]]:
        """
        List all tables in a database.

        Args:
            db_name: Database name

        Returns:
            List of table info dicts
        """
        try:
            db_path = self._get_db_path(db_name)
            if not db_path.exists():
                return []

            conn = sqlite3.connect(str(db_path))
            cursor = conn.cursor()

            cursor.execute("""
                SELECT name FROM sqlite_master
                WHERE type='table' AND name NOT LIKE 'sqlite_%'
                ORDER BY name
                """)
            tables = []

            for (table_name,) in cursor.fetchall():
                cursor.execute(f"SELECT COUNT(*) FROM {table_name}")  # noqa: S608  # nosec B608
                count = cursor.fetchone()[0]
                tables.append({"name": table_name, "row_count": count})

            conn.close()
            return tables

        except Exception as e:
            logger.error(f"Failed to list tables: {e}")
            return []

    def cleanup_old_data(
        self,
        db_name: str = "user",
        days: int = 30,
        tables: Optional[List[str]] = None,
    ) -> Dict[str, int]:
        """
        Remove old history records from specified tables.

        Args:
            db_name: Database name
            days: Delete records older than this many days
            tables: Tables to clean (auto-detect history tables if None)

        Returns:
            Dict mapping table names to deleted row counts
        """
        results = {}
        cutoff_date = (datetime.now() - timedelta(days=days)).isoformat()

        try:
            db_path = self._get_db_path(db_name)
            if not db_path.exists():
                return results

            conn = sqlite3.connect(str(db_path))
            cursor = conn.cursor()

            # Auto-detect history tables if not specified
            if tables is None:
                cursor.execute("""
                    SELECT name FROM sqlite_master
                    WHERE type='table' AND name LIKE '%_history'
                    """)
                tables = [row[0] for row in cursor.fetchall()]

            for table_name in tables:
                # Look for timestamp column
                cursor.execute(f"PRAGMA table_info({table_name})")  # noqa: S608  # nosec B608
                columns = [row[1] for row in cursor.fetchall()]

                timestamp_col = None
                for col in ["timestamp", "created_at", "date", "time"]:
                    if col in columns:
                        timestamp_col = col
                        break

                if timestamp_col:
                    cursor.execute(
                        f"DELETE FROM {table_name} WHERE {timestamp_col} < ?",  # noqa: S608  # nosec B608
                        (cutoff_date,),
                    )
                    results[table_name] = cursor.rowcount

            conn.commit()
            conn.close()

            logger.info(f"Cleanup complete: {results}")
            return results

        except Exception as e:
            logger.error(f"Cleanup failed: {e}")
            return results

    def old_backups(self, keep_days: int = 7, keep_count: int = 5) -> List[Path]:
        """
        The backup files past their retention.

        Retention applies per database (the ``user_`` and ``cache_`` backups are counted
        separately), so cleanup can never remove every backup of one database.

        Args:
            keep_days: Keep backups modified within this many days
            keep_count: Always keep at least this many backups of each database

        Returns:
            The files to remove, oldest of each database last
        """
        cutoff = (datetime.now() - timedelta(days=keep_days)).timestamp()

        groups: Dict[str, List[Path]] = {}
        for backup in self.backup_dir.glob("*.db"):
            groups.setdefault(backup.name.split("_", 1)[0], []).append(backup)

        old: List[Path] = []
        for files in groups.values():
            files.sort(key=lambda p: p.stat().st_mtime, reverse=True)
            for backup in files[keep_count:]:
                try:
                    if backup.stat().st_mtime < cutoff:
                        old.append(backup)
                except OSError as e:
                    logger.warning(f"Could not check old backup {backup.name}: {e}")
        return old

    def cleanup_old_backups(
        self, keep_days: int = 7, keep_count: int = 5, only: Optional[List[str]] = None
    ) -> int:
        """
        Remove old backup files (see old_backups for what goes).

        Args:
            keep_days: Keep backups modified within this many days
            keep_count: Always keep at least this many backups of each database
            only: Remove only these file names (what the user was shown and agreed to)

        Returns:
            Number of backups deleted
        """
        deleted = 0
        for backup in self.old_backups(keep_days, keep_count):
            if only is not None and backup.name not in only:
                continue
            try:
                backup.unlink()
                deleted += 1
                logger.info(f"Deleted old backup: {backup.name}")
            except OSError as e:
                logger.warning(f"Could not delete old backup {backup.name}: {e}")

        return deleted


class DBMigrator:
    """
    Schema migration manager for SQLite databases.

    Usage:
        migrator = DBMigrator("state/user.db")
        migrator.register_migration(2, "ALTER TABLE users ADD COLUMN email TEXT")
        migrator.migrate_to_latest()
    """

    def __init__(self, db_path: str):
        """Initialize migrator for a specific database."""
        self.db_path = Path(db_path)
        self._migrations: Dict[int, str] = {}
        self._ensure_version_table()

    def _ensure_version_table(self) -> None:
        """Create schema_version table if not exists."""
        if not self.db_path.exists():
            return

        conn = sqlite3.connect(str(self.db_path))
        cursor = conn.cursor()
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS schema_version (
                version INTEGER PRIMARY KEY,
                applied_at TEXT NOT NULL
            )
            """)
        conn.commit()
        conn.close()

    def get_version(self) -> int:
        """Get current schema version."""
        if not self.db_path.exists():
            return 0

        conn = sqlite3.connect(str(self.db_path))
        cursor = conn.cursor()

        try:
            cursor.execute("SELECT MAX(version) FROM schema_version")
            result = cursor.fetchone()
            return result[0] if result[0] else 0
        except sqlite3.OperationalError:
            return 0
        finally:
            conn.close()

    def register_migration(self, version: int, sql: str) -> None:
        """
        Register a migration script.

        Args:
            version: Target version number
            sql: SQL to execute for migration
        """
        self._migrations[version] = sql

    def migrate_to(self, target_version: int) -> List[int]:
        """
        Run migrations up to target version.

        Args:
            target_version: Version to migrate to

        Returns:
            List of applied version numbers
        """
        current = self.get_version()
        applied = []

        if current >= target_version:
            logger.info(f"Already at version {current}, nothing to migrate")
            return applied

        conn = sqlite3.connect(str(self.db_path))
        cursor = conn.cursor()

        try:
            for version in sorted(self._migrations.keys()):
                if version <= current or version > target_version:
                    continue

                logger.info(f"Applying migration {version}...")
                cursor.executescript(self._migrations[version])

                cursor.execute(
                    "INSERT INTO schema_version (version, applied_at) VALUES (?, ?)",
                    (version, datetime.now().isoformat()),
                )
                applied.append(version)

            conn.commit()
            logger.info(f"Migration complete. Applied: {applied}")

        except Exception as e:
            conn.rollback()
            logger.error(f"Migration failed at version {version}: {e}")
            raise

        finally:
            conn.close()

        return applied

    def migrate_to_latest(self) -> List[int]:
        """Run all pending migrations."""
        if not self._migrations:
            return []
        return self.migrate_to(max(self._migrations.keys()))


# Singleton instance
_backup_manager: Optional[DBBackupManager] = None


def get_backup_manager() -> DBBackupManager:
    """Get global DBBackupManager instance."""
    global _backup_manager
    if _backup_manager is None:
        _backup_manager = DBBackupManager()
    return _backup_manager


# Convenience functions
def backup_database(db_name: str = "user") -> BackupResult:
    """Create a backup of the specified database."""
    return get_backup_manager().backup_database(db_name)


def export_table(db_name: str, table_name: str, output_path: Optional[str] = None) -> ExportResult:
    """Export a table to JSON."""
    return get_backup_manager().export_table(db_name, table_name, output_path)


def import_table(source_path: str, db_name: str, table_name: Optional[str] = None) -> ImportResult:
    """Import data from JSON into a table."""
    return get_backup_manager().import_table(source_path, db_name, table_name)


def list_tables(db_name: str = "user") -> List[Dict[str, Any]]:
    """List all tables in a database."""
    return get_backup_manager().list_tables(db_name)


if __name__ == "__main__":
    # Quick test when run directly
    import sys

    logging.basicConfig(level=logging.INFO)

    manager = DBBackupManager()

    print("=== Available Databases ===")
    for name, path in DB_PATHS.items():
        full_path = manager.project_root / path
        exists = "EXISTS" if full_path.exists() else "NOT FOUND"
        print(f"  {name}: {path} [{exists}]")

    print("\n=== Tables in user.db ===")
    tables = manager.list_tables("user")
    for table in tables:
        print(f"  {table['name']}: {table['row_count']} rows")

    if "--backup" in sys.argv:
        print("\n=== Creating Backup ===")
        result = manager.backup_database("user")
        print(f"  {result.message}")

    if "--list-backups" in sys.argv:
        print("\n=== Available Backups ===")
        backups = manager.list_backups()
        for b in backups[:5]:
            print(f"  {b['filename']} ({b['size_bytes']} bytes)")
