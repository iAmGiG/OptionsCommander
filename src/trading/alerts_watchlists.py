#!/usr/bin/env python3
"""
Alerts and Watchlists Persistence - SQLite-backed alert and watchlist management.

Issue #480: Persist user alerts and watchlists to SQLite for survival across restarts.

This module provides:
- Price alerts (above/below thresholds, percentage changes)
- User-defined watchlists with notes
- Alert trigger tracking
- Default watchlist support

Uses state/user.db for persistence (same as other state managers).
"""

import logging
import sqlite3
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional

from src.utils.date_utils import now_iso
from src.utils.state_paths import get_user_db_path

logger = logging.getLogger(__name__)


class AlertType(Enum):
    """Types of price alerts."""

    PRICE_ABOVE = "price_above"
    PRICE_BELOW = "price_below"
    PCT_GAIN = "pct_gain"
    PCT_LOSS = "pct_loss"
    STOP_LOSS = "stop_loss"
    TAKE_PROFIT = "take_profit"


@dataclass
class Alert:
    """A user-defined price alert."""

    id: Optional[int] = None
    symbol: str = ""
    alert_type: str = AlertType.PRICE_ABOVE.value
    trigger_value: float = 0.0
    message: Optional[str] = None
    enabled: bool = True
    triggered_at: Optional[str] = None
    created_at: Optional[str] = None

    @property
    def is_triggered(self) -> bool:
        return self.triggered_at is not None


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
    Manages alerts and watchlists in SQLite.

    Provides persistence for:
    - Price alerts with trigger tracking
    - Multiple user-defined watchlists
    - Default watchlist for quick access
    """

    def __init__(self, db_path: Optional[str] = None):
        """
        Initialize alerts and watchlists manager.

        Args:
            db_path: Path to SQLite database (default: state/user.db)
        """
        if db_path is None:
            db_path = get_user_db_path()

        self._db_path = db_path
        self._init_database()
        logger.debug(f"AlertsWatchlistsManager initialized with db: {db_path}")

    def _init_database(self) -> None:
        """Initialize SQLite tables for alerts and watchlists."""
        try:
            conn = sqlite3.connect(self._db_path)
            cursor = conn.cursor()

            # User alerts table
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS user_alerts (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    symbol TEXT NOT NULL,
                    alert_type TEXT NOT NULL,
                    trigger_value REAL NOT NULL,
                    message TEXT,
                    enabled BOOLEAN DEFAULT TRUE,
                    triggered_at TEXT,
                    created_at TEXT NOT NULL
                )
            """)

            # Create index for symbol lookup
            cursor.execute("""
                CREATE INDEX IF NOT EXISTS idx_user_alerts_symbol
                ON user_alerts(symbol)
            """)

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
    # Alert Management
    # =========================================================================

    def create_alert(
        self,
        symbol: str,
        alert_type: AlertType,
        trigger_value: float,
        message: Optional[str] = None,
        allow_duplicate: bool = False,
    ) -> Optional[Alert]:
        """
        Create a new price alert.

        Args:
            symbol: Stock symbol
            alert_type: Type of alert (price_above, price_below, etc.)
            trigger_value: Value that triggers the alert
            message: Optional custom message
            allow_duplicate: If False, prevents duplicate alerts (default: False)

        Returns:
            Created Alert or None on failure/duplicate
        """
        try:
            conn = self._get_connection()
            cursor = conn.cursor()

            # Check for duplicate (Issue #482)
            if not allow_duplicate:
                cursor.execute(
                    """
                    SELECT id FROM user_alerts
                    WHERE symbol = ? AND alert_type = ? AND trigger_value = ?
                    AND enabled = TRUE AND triggered_at IS NULL
                """,
                    (symbol.upper(), alert_type.value, trigger_value),
                )
                existing = cursor.fetchone()
                if existing:
                    logger.warning(
                        f"Duplicate alert exists: {symbol} {alert_type.value} {trigger_value}"
                    )
                    conn.close()
                    return None

            cursor.execute(
                """
                INSERT INTO user_alerts (symbol, alert_type, trigger_value, message, enabled, created_at)
                VALUES (?, ?, ?, ?, TRUE, ?)
            """,
                (symbol.upper(), alert_type.value, trigger_value, message, now_iso()),
            )

            alert_id = cursor.lastrowid
            conn.commit()
            conn.close()

            logger.info(f"Created alert: {symbol} {alert_type.value} {trigger_value}")
            return Alert(
                id=alert_id,
                symbol=symbol.upper(),
                alert_type=alert_type.value,
                trigger_value=trigger_value,
                message=message,
                enabled=True,
                created_at=now_iso(),
            )
        except Exception as e:
            logger.error(f"Failed to create alert: {e}")
            return None

    def get_alerts(
        self,
        symbol: Optional[str] = None,
        enabled_only: bool = True,
        untriggered_only: bool = False,
    ) -> List[Alert]:
        """
        Get alerts, optionally filtered.

        Args:
            symbol: Filter by symbol
            enabled_only: Only return enabled alerts
            untriggered_only: Only return alerts not yet triggered

        Returns:
            List of matching alerts
        """
        try:
            conn = self._get_connection()
            cursor = conn.cursor()

            query = "SELECT * FROM user_alerts WHERE 1=1"
            params: List[Any] = []

            if symbol:
                query += " AND symbol = ?"
                params.append(symbol.upper())

            if enabled_only:
                query += " AND enabled = TRUE"

            if untriggered_only:
                query += " AND triggered_at IS NULL"

            query += " ORDER BY created_at DESC"

            cursor.execute(query, params)
            rows = cursor.fetchall()
            conn.close()

            return [
                Alert(
                    id=row["id"],
                    symbol=row["symbol"],
                    alert_type=row["alert_type"],
                    trigger_value=row["trigger_value"],
                    message=row["message"],
                    enabled=bool(row["enabled"]),
                    triggered_at=row["triggered_at"],
                    created_at=row["created_at"],
                )
                for row in rows
            ]
        except Exception as e:
            logger.error(f"Failed to get alerts: {e}")
            return []

    def check_alert(self, alert_id: int, current_price: float) -> bool:
        """
        Check if an alert should trigger.

        Args:
            alert_id: Alert ID to check
            current_price: Current price to compare

        Returns:
            True if alert triggered
        """
        alerts = self.get_alerts(enabled_only=True, untriggered_only=True)
        alert = next((a for a in alerts if a.id == alert_id), None)

        if not alert:
            return False

        triggered = False
        if alert.alert_type == AlertType.PRICE_ABOVE.value:
            triggered = current_price >= alert.trigger_value
        elif alert.alert_type == AlertType.PRICE_BELOW.value:
            triggered = current_price <= alert.trigger_value

        if triggered:
            self.trigger_alert(alert_id)

        return triggered

    def trigger_alert(self, alert_id: int) -> bool:
        """
        Mark an alert as triggered.

        Args:
            alert_id: Alert ID to trigger

        Returns:
            True if successful
        """
        try:
            conn = self._get_connection()
            cursor = conn.cursor()

            cursor.execute(
                "UPDATE user_alerts SET triggered_at = ? WHERE id = ?",
                (now_iso(), alert_id),
            )

            conn.commit()
            conn.close()

            logger.info(f"Alert {alert_id} triggered")
            return True
        except Exception as e:
            logger.error(f"Failed to trigger alert: {e}")
            return False

    def delete_alert(self, alert_id: int) -> bool:
        """Delete an alert."""
        try:
            conn = self._get_connection()
            cursor = conn.cursor()
            cursor.execute("DELETE FROM user_alerts WHERE id = ?", (alert_id,))
            conn.commit()
            conn.close()
            logger.info(f"Deleted alert {alert_id}")
            return True
        except Exception as e:
            logger.error(f"Failed to delete alert: {e}")
            return False

    def toggle_alert(self, alert_id: int, enabled: bool) -> bool:
        """Enable or disable an alert."""
        try:
            conn = self._get_connection()
            cursor = conn.cursor()
            cursor.execute(
                "UPDATE user_alerts SET enabled = ? WHERE id = ?",
                (enabled, alert_id),
            )
            conn.commit()
            conn.close()
            logger.info(f"Alert {alert_id} enabled={enabled}")
            return True
        except Exception as e:
            logger.error(f"Failed to toggle alert: {e}")
            return False

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
    # Batch Alert Checking (Issue #482)
    # =========================================================================

    def check_alerts_batch(self, prices: Dict[str, float]) -> List[Alert]:
        """
        Check multiple alerts against current prices.

        Args:
            prices: Dict mapping symbol -> current price

        Returns:
            List of alerts that were triggered
        """
        triggered_alerts: List[Alert] = []

        try:
            # Optimization: Only fetch alerts for symbols we have prices for
            symbols = list(prices.keys())
            if not symbols:
                return []
            alerts = self.get_alerts_for_symbols(symbols)

            for alert in alerts:
                current_price = prices[alert.symbol]
                should_trigger = False

                if alert.alert_type == AlertType.PRICE_ABOVE.value:
                    should_trigger = current_price >= alert.trigger_value
                elif alert.alert_type == AlertType.PRICE_BELOW.value:
                    should_trigger = current_price <= alert.trigger_value

                if should_trigger:
                    self.trigger_alert(alert.id)
                    alert.triggered_at = now_iso()
                    triggered_alerts.append(alert)
                    logger.info(
                        f"Alert triggered: {alert.symbol} {alert.alert_type} "
                        f"@ {alert.trigger_value} (current: {current_price})"
                    )

            return triggered_alerts
        except Exception as e:
            logger.error(f"Failed to check alerts batch: {e}")
            return []

    def get_alerts_for_symbols(self, symbols: List[str]) -> List[Alert]:
        """
        Get all active alerts for a list of symbols.

        Args:
            symbols: List of stock symbols

        Returns:
            List of alerts for those symbols
        """
        if not symbols:
            return []

        try:
            conn = self._get_connection()
            cursor = conn.cursor()

            placeholders = ",".join("?" * len(symbols))
            query = f"""
                SELECT * FROM user_alerts
                WHERE symbol IN ({placeholders})
                AND enabled = TRUE AND triggered_at IS NULL
                ORDER BY symbol, alert_type
            """  # nosec B608 - only "?" placeholders are interpolated; symbols are bound
            cursor.execute(
                query,
                [s.upper() for s in symbols],
            )

            rows = cursor.fetchall()
            conn.close()

            return [
                Alert(
                    id=row["id"],
                    symbol=row["symbol"],
                    alert_type=row["alert_type"],
                    trigger_value=row["trigger_value"],
                    message=row["message"],
                    enabled=bool(row["enabled"]),
                    triggered_at=row["triggered_at"],
                    created_at=row["created_at"],
                )
                for row in rows
            ]
        except Exception as e:
            logger.error(f"Failed to get alerts for symbols: {e}")
            return []

    # =========================================================================
    # Summary and Stats
    # =========================================================================

    def get_summary(self) -> Dict[str, Any]:
        """Get summary of alerts and watchlists."""
        alerts = self.get_alerts(enabled_only=True, untriggered_only=True)
        watchlists = self.get_watchlists()

        alerts_by_symbol = {}
        for alert in alerts:
            alerts_by_symbol[alert.symbol] = alerts_by_symbol.get(alert.symbol, 0) + 1

        default_watchlist = next((w for w in watchlists if w.is_default), None)
        unique_watched = {item.symbol for w in watchlists for item in w.items}

        return {
            "active_alerts": len(alerts),
            "alerts_by_symbol": alerts_by_symbol,
            "watchlist_count": len(watchlists),
            "default_watchlist": default_watchlist.name if default_watchlist else None,
            "total_watched_symbols": len(unique_watched),
        }

    def get_alert_summary(self) -> Dict[str, int]:
        """Get count of alerts by symbol."""
        alerts = self.get_alerts(enabled_only=True)
        summary: Dict[str, int] = {}
        for alert in alerts:
            summary[alert.symbol] = summary.get(alert.symbol, 0) + 1
        return summary


# Global instance for easy access
_alerts_watchlists_manager: Optional[AlertsWatchlistsManager] = None


def get_alerts_watchlists_manager() -> AlertsWatchlistsManager:
    """Get global alerts and watchlists manager instance."""
    global _alerts_watchlists_manager
    if _alerts_watchlists_manager is None:
        _alerts_watchlists_manager = AlertsWatchlistsManager()
    return _alerts_watchlists_manager
