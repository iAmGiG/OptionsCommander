#!/usr/bin/env python3
"""
Watchlist Persistence - SQLite-backed watchlist management.

Issue #480: Persist watchlists to SQLite for survival across restarts. The user price
alerts that lived here never fired and were removed (#331); /gtt has the working alerts.

This module provides:
- User-defined watchlists with notes
- Default watchlist support

Uses state/user.db for persistence (same as other state managers).
"""

import logging
import sqlite3
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from src.utils.date_utils import now_iso
from src.utils.state_paths import get_user_db_path

logger = logging.getLogger(__name__)


@dataclass
class Watchlist:
    """A user-defined watchlist."""

    id: Optional[int] = None
    name: str = ""
    description: Optional[str] = None
    is_default: bool = False
    created_at: Optional[str] = None
    items: List["WatchlistItem"] = field(default_factory=list)


@dataclass
class WatchlistItem:
    """An item in a watchlist."""

    id: Optional[int] = None
    watchlist_id: Optional[int] = None
    symbol: str = ""
    notes: Optional[str] = None
    added_at: Optional[str] = None


class AlertsWatchlistsManager:
    """
    Manages watchlists in SQLite.

    Provides persistence for:
    - Multiple user-defined watchlists
    - Default watchlist for quick access
    """

    def __init__(self, db_path: Optional[str] = None):
        """
        Initialize the watchlists manager.

        Args:
            db_path: Path to SQLite database (default: state/user.db)
        """
        if db_path is None:
            db_path = get_user_db_path()

        self._db_path = db_path
        self._init_database()
        logger.debug(f"AlertsWatchlistsManager initialized with db: {db_path}")

    def _init_database(self) -> None:
        """Initialize SQLite tables for watchlists."""
        try:
            conn = sqlite3.connect(self._db_path)
            cursor = conn.cursor()

            # Watchlists table
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS watchlists (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT UNIQUE NOT NULL,
                    description TEXT,
                    is_default BOOLEAN DEFAULT FALSE,
                    created_at TEXT NOT NULL
                )
            """)

            # Watchlist items table
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS watchlist_items (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    watchlist_id INTEGER NOT NULL,
                    symbol TEXT NOT NULL,
                    notes TEXT,
                    added_at TEXT NOT NULL,
                    FOREIGN KEY (watchlist_id) REFERENCES watchlists(id) ON DELETE CASCADE,
                    UNIQUE(watchlist_id, symbol)
                )
            """)

            # Enable foreign key support
            cursor.execute("PRAGMA foreign_keys = ON")

            conn.commit()
            conn.close()
            logger.debug("Alerts and watchlists tables initialized")
        except Exception as e:
            logger.error(f"Failed to initialize alerts/watchlists database: {e}")

    def _get_connection(self) -> sqlite3.Connection:
        """Get database connection with row factory."""
        conn = sqlite3.connect(self._db_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        return conn

    # =========================================================================
    # Watchlist Management
    # =========================================================================

    def create_watchlist(
        self,
        name: str,
        description: Optional[str] = None,
        is_default: bool = False,
    ) -> Optional[Watchlist]:
        """
        Create a new watchlist.

        Args:
            name: Watchlist name (unique)
            description: Optional description
            is_default: Set as default watchlist

        Returns:
            Created Watchlist or None on failure
        """
        try:
            conn = self._get_connection()
            cursor = conn.cursor()

            # If setting as default, clear other defaults
            if is_default:
                cursor.execute("UPDATE watchlists SET is_default = FALSE")

            cursor.execute(
                """
                INSERT INTO watchlists (name, description, is_default, created_at)
                VALUES (?, ?, ?, ?)
            """,
                (name, description, is_default, now_iso()),
            )

            watchlist_id = cursor.lastrowid
            conn.commit()
            conn.close()

            logger.info(f"Created watchlist: {name}")
            return Watchlist(
                id=watchlist_id,
                name=name,
                description=description,
                is_default=is_default,
                created_at=now_iso(),
            )
        except sqlite3.IntegrityError:
            logger.warning(f"Watchlist '{name}' already exists")
            return None
        except Exception as e:
            logger.error(f"Failed to create watchlist: {e}")
            return None

    def get_watchlists(self) -> List[Watchlist]:
        """Get all watchlists."""
        try:
            conn = self._get_connection()
            cursor = conn.cursor()

            cursor.execute("SELECT * FROM watchlists ORDER BY name")
            rows = cursor.fetchall()

            watchlists = []
            for row in rows:
                watchlist = Watchlist(
                    id=row["id"],
                    name=row["name"],
                    description=row["description"],
                    is_default=bool(row["is_default"]),
                    created_at=row["created_at"],
                )
                # Get items for this watchlist
                cursor.execute(
                    "SELECT * FROM watchlist_items WHERE watchlist_id = ? ORDER BY symbol",
                    (row["id"],),
                )
                item_rows = cursor.fetchall()
                watchlist.items = [
                    WatchlistItem(
                        id=item["id"],
                        watchlist_id=item["watchlist_id"],
                        symbol=item["symbol"],
                        notes=item["notes"],
                        added_at=item["added_at"],
                    )
                    for item in item_rows
                ]
                watchlists.append(watchlist)

            conn.close()
            return watchlists
        except Exception as e:
            logger.error(f"Failed to get watchlists: {e}")
            return []

    def get_watchlist(self, name: str) -> Optional[Watchlist]:
        """Get a specific watchlist by name."""
        watchlists = self.get_watchlists()
        return next((w for w in watchlists if w.name == name), None)

    def get_default_watchlist(self) -> Optional[Watchlist]:
        """Get the default watchlist."""
        try:
            conn = self._get_connection()
            cursor = conn.cursor()

            cursor.execute("SELECT * FROM watchlists WHERE is_default = TRUE LIMIT 1")
            row = cursor.fetchone()
            conn.close()

            if row:
                watchlist = self.get_watchlist(row["name"])
                return watchlist
            return None
        except Exception as e:
            logger.error(f"Failed to get default watchlist: {e}")
            return None

    def delete_watchlist(self, name: str) -> bool:
        """Delete a watchlist and its items."""
        try:
            conn = self._get_connection()
            cursor = conn.cursor()
            cursor.execute("DELETE FROM watchlists WHERE name = ?", (name,))
            deleted = cursor.rowcount > 0
            conn.commit()
            conn.close()
            if deleted:
                logger.info(f"Deleted watchlist: {name}")
            return deleted
        except Exception as e:
            logger.error(f"Failed to delete watchlist: {e}")
            return False

    def add_to_watchlist(
        self,
        watchlist_name: str,
        symbol: str,
        notes: Optional[str] = None,
    ) -> bool:
        """
        Add a symbol to a watchlist.

        Args:
            watchlist_name: Name of watchlist
            symbol: Stock symbol to add
            notes: Optional notes about the symbol

        Returns:
            True if successful
        """
        try:
            conn = self._get_connection()
            cursor = conn.cursor()

            # Get watchlist ID
            cursor.execute("SELECT id FROM watchlists WHERE name = ?", (watchlist_name,))
            row = cursor.fetchone()

            if not row:
                logger.warning(f"Watchlist '{watchlist_name}' not found")
                conn.close()
                return False

            watchlist_id = row["id"]

            cursor.execute(
                """
                INSERT OR REPLACE INTO watchlist_items
                (watchlist_id, symbol, notes, added_at)
                VALUES (?, ?, ?, ?)
            """,
                (watchlist_id, symbol.upper(), notes, now_iso()),
            )

            conn.commit()
            conn.close()

            logger.info(f"Added {symbol} to watchlist '{watchlist_name}'")
            return True
        except Exception as e:
            logger.error(f"Failed to add to watchlist: {e}")
            return False

    def remove_from_watchlist(self, watchlist_name: str, symbol: str) -> bool:
        """Remove a symbol from a watchlist."""
        try:
            conn = self._get_connection()
            cursor = conn.cursor()

            cursor.execute(
                """
                DELETE FROM watchlist_items
                WHERE watchlist_id = (SELECT id FROM watchlists WHERE name = ?)
                AND symbol = ?
            """,
                (watchlist_name, symbol.upper()),
            )

            conn.commit()
            conn.close()

            logger.info(f"Removed {symbol} from watchlist '{watchlist_name}'")
            return True
        except Exception as e:
            logger.error(f"Failed to remove from watchlist: {e}")
            return False

    def get_watchlist_symbols(self, watchlist_name: str) -> List[str]:
        """Get all symbols in a watchlist."""
        watchlist = self.get_watchlist(watchlist_name)
        if watchlist:
            return [item.symbol for item in watchlist.items]
        return []

    # =========================================================================
    # Summary and Stats
    # =========================================================================

    def get_summary(self) -> Dict[str, Any]:
        """Get summary of the watchlists."""
        watchlists = self.get_watchlists()

        default_watchlist = next((w for w in watchlists if w.is_default), None)
        unique_watched = {item.symbol for w in watchlists for item in w.items}

        return {
            "watchlist_count": len(watchlists),
            "default_watchlist": default_watchlist.name if default_watchlist else None,
            "total_watched_symbols": len(unique_watched),
        }


# Global instance for easy access
_alerts_watchlists_manager: Optional[AlertsWatchlistsManager] = None


def get_alerts_watchlists_manager() -> AlertsWatchlistsManager:
    """Get global alerts and watchlists manager instance."""
    global _alerts_watchlists_manager
    if _alerts_watchlists_manager is None:
        _alerts_watchlists_manager = AlertsWatchlistsManager()
    return _alerts_watchlists_manager
