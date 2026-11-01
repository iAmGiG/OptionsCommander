#!/usr/bin/env python3
"""
Cost-Efficient Trade Cycle - Minimal API calls using GTC orders

Core Principle: Let the broker do the work via GTC orders, minimize LLM/API calls.
- Morning routine: 9:20 AM ET - reconcile, adjust stops, generate report
- Evening routine: 3:50 PM ET - EOD review and preparation
- Batch all API calls to minimize costs
- JSON is for humans, broker is truth

Refactored in #439 to use extracted components:
- LocalStateManager: JSON persistence
- BrokerStateCache: Broker state caching
- StateReconciler: Reconciliation logic
- ReportGenerator: Report formatting
"""

import copy
import logging
import os
from contextlib import contextmanager
from dataclasses import asdict, replace
from typing import Any, Dict, Iterator, List, Optional, Tuple
from zoneinfo import ZoneInfo

import yaml

logger = logging.getLogger(__name__)

from config_defaults.trading_config import TradingConfig

from src.market_data.alpaca_market_data import AlpacaMarketData
from src.trading.broker.alpaca_trading_client import AlpacaAccountMonitor, AlpacaOrderManager
from src.trading.orders.dry_run import DRY_RUN_STATUS, DryRunOrderManager
from src.trading.orders.trailing_stop_manager import TrailingStopManager
from src.trading.positions.position_tracker import PositionTracker
from src.trading.scheduling.scheduler_state import market_today
from src.trading.state.broker_state_cache import BrokerStateCache
from src.trading.state.daily_records import DailyRecords
from src.trading.state.local_state_manager import LocalStateManager
from src.trading.state.state_reconciler import (
    Discrepancy,
    PositionAlertSummary,
    StateReconciler,
    StopAdjustment,
    StopRepair,
)
from src.trading.utils.report_generator import ReportGenerator, RoutineType
from src.utils.date_utils import DEFAULT_TIMEZONE, get_datetime_now
from src.utils.market_hours import is_market_hours

# GTT (Good-Till-Triggered) integration - Issue #340
try:
    from src.trading.gtt.action_executor import ActionResult, get_action_executor
    from src.trading.gtt.trailing_stop_bridge import (
        cleanup_orphaned_trailing_stops,
        restore_trailing_stops_from_gtt,
        sync_all_trailing_stops,
    )
    from src.trading.gtt.trigger_evaluator import get_trigger_evaluator

    GTT_AVAILABLE = True
except ImportError:
    GTT_AVAILABLE = False

# Re-export dataclasses for backward compatibility
__all__ = [
    "CostEfficientTradeCycle",
    "Discrepancy",
    "RoutineError",
    "StopAdjustment",
    "PositionAlertSummary",
    "RoutineType",
]

# Discrepancy types of protective-stop repairs (StopRepair.kind)
STOP_REPAIR_TYPES = ("MISSING_STOP", "STOP_EXPIRING")
# A position's average entry moved this far from its trailing state's: it was sold and bought
# again, or added to (#634). Wide enough for the state's rounded copy of the entry.
ENTRY_CHANGE_TOLERANCE = 0.001

# Discrepancy action of a stop repair a dry run did not send (#601)
DRY_RUN_ACTION = "DRY_RUN_NOT_SENT"

# Open notices listed in a routine report (the latest); the scheduler CLI's `notices` has all
REPORT_NOTICES = 10

DRY_RUN_BANNER = (
    "> 🧪 **DRY RUN**: no orders were sent, and local state, daily records and GTT "
    "triggers were not changed. Stop changes below are what the routine would have made.\n\n"
)


def _free_shares(symbol: str, broker_state: Dict[str, Any]) -> int:
    """
    Shares of a long position not held by open orders: Alpaca's qty_available, which
    counts a bracket's or OCO's legs once and only the unfilled part of a partial fill.
    Without it, the position less every open sell order (it may count OCO legs twice,
    so it errs toward selling less, never more).
    """
    position = broker_state.get("positions", {}).get(symbol) or {}
    if not position or "short" in str(position.get("side", "")).lower():
        return 0
    if position.get("qty_available") is not None:
        return int(position["qty_available"])
    held_by_orders = sum(
        int(order.get("quantity") or 0)
        for order in broker_state.get("orders", {}).get(symbol, [])
        if "sell" in str(order.get("side", "")).lower()
    )
    return int(position.get("quantity", 0)) - held_by_orders


def _position_prices(broker_state: Dict[str, Any]) -> Dict[str, float]:
    """Current price of each position (from market value when no price is given)."""
    prices = {}
    for symbol, pos in broker_state.get("positions", {}).items():
        if "current_price" in pos:
            prices[symbol] = pos["current_price"]
        elif "market_value" in pos and "quantity" in pos and pos["quantity"] > 0:
            prices[symbol] = pos["market_value"] / pos["quantity"]
    return prices


def _not_executed(triggered: List[Any]) -> List[Any]:
    """Results for GTT triggers a dry run did not execute (#601)."""
    results = []
    for trigger in triggered:
        logger.info(f"DRY RUN: GTT {trigger.id} ({trigger.symbol}) would fire")
        results.append(
            ActionResult(
                trigger_id=trigger.id,
                success=False,
                action_type=trigger.action_type,
                message=f"dry run: {trigger.symbol} {trigger.action_type} not executed",
                details={"symbol": trigger.symbol, "dry_run": True},
            )
        )
    return results


def _within_free_shares(
    triggered: List[Any], broker_state: Dict[str, Any]
) -> Tuple[List[Any], List[Tuple[Any, str]]]:
    """
    Keep GTT sell orders within the shares free to sell (see _free_shares), sharing
    them among a symbol's sell triggers in the same run.

    A sell trigger with no free shares is skipped this run (open orders, such as the
    broker stop, hold them, and Alpaca would refuse the sell); it stays active. A larger
    one is cut to the free shares, so it can't sell more than is held or open a short.
    A trigger with an unusable quantity is skipped on its own.

    Returns:
        (triggers to execute, [(skipped trigger, reason)])
    """
    kept: List[Any] = []
    skipped: List[Tuple[Any, str]] = []
    free_left: Dict[str, int] = {}
    for trigger in triggered:
        config = trigger.action_config or {}
        is_sell = str(config.get("side", "buy")).lower() == "sell"
        if trigger.action_type != "place_order" or not is_sell:
            kept.append(trigger)
            continue

        try:
            qty = int(float(config.get("qty")))
        except (TypeError, ValueError):
            qty = 0
        if qty <= 0:
            skipped.append((trigger, f"not executed: unusable quantity {config.get('qty')!r}"))
            continue

        if trigger.symbol not in free_left:
            free_left[trigger.symbol] = _free_shares(trigger.symbol, broker_state)
        free = free_left[trigger.symbol]
        if free <= 0:
            skipped.append((trigger, "not executed: no free shares (open sell orders hold them)"))
            continue
        if qty > free:
            logger.info(f"GTT {trigger.id} ({trigger.symbol} sell) cut to the {free} free shares")
            trigger.action_config = {**config, "qty": free, "quantity": free}
            qty = free
        free_left[trigger.symbol] -= qty
        kept.append(trigger)
    return kept, skipped


class RoutineError(RuntimeError):
    """A routine failed, so the scheduler should record the failure and retry."""


def stop_failures(discrepancies: List[Discrepancy]) -> List[Discrepancy]:
    """
    Protective-stop steps that failed: the check itself, or a stop that wasn't placed or
    renewed. Cases left for human review are not failures, since a retry can't fix them.
    """
    return [
        d
        for d in discrepancies
        if d.type == "STOP_CHECK_FAILED"
        or (d.type in STOP_REPAIR_TYPES and (d.details or {}).get("error"))
    ]


def _check_stops_secured(discrepancies: List[Discrepancy]) -> None:
    """Raise RoutineError if any protective stop couldn't be checked, placed or renewed."""
    failures = stop_failures(discrepancies)
    if failures:
        symbols = ", ".join(sorted({d.symbol for d in failures}))
        raise RoutineError(f"protective stops not secured ({symbols})")


class CostEfficientTradeCycle:
    """
    Redesigned for minimal API calls using GTC orders.

    Strategy:
    - Two scheduled routines per day (morning/evening)
    - Single API call to get all positions/orders per routine
    - Batch all modifications into single API calls
    - Generate human-readable reports for oversight
    - Crash recovery rebuilds state from broker truth

    Components (extracted in #439):
    - LocalStateManager: JSON state persistence
    - BrokerStateCache: Cached broker state with TTL
    - StateReconciler: Reconciliation and alert logic
    - ReportGenerator: Human-readable reports
    """

    # Class defaults, so a cycle built without __init__ (tests) has them too
    dry_run = False
    _dry_run_active = False
    position_tracker = None
    # The latest routine's alerts and discrepancies, read by the scheduler for its notices
    last_alerts: Tuple[PositionAlertSummary, ...] = ()
    last_discrepancies: Tuple[Discrepancy, ...] = ()
    notice_store = None  # set by the DailyScheduler; the reports list its open notices

    def __init__(self, state_file: str = None, dry_run: bool = False):
        # Load path configuration from config_defaults/paths_config.yaml
        config_path = os.path.join("config_defaults", "paths_config.yaml")
        try:
            with open(config_path) as f:
                paths_config = yaml.safe_load(f)
                self.paths = paths_config
                logger.info(f"Loaded paths config from {config_path}")
        except FileNotFoundError:
            logger.warning(f"Paths config not found at {config_path}, using hardcoded defaults")
            self.paths = {
                "state_files": {"cost_efficient": "state/cost_efficient_positions.json"},
                "report_templates": {"daily_routine": "reports/daily/{date}_{routine_type}.md"},
            }

        # Use config value if state_file not explicitly provided
        if state_file is None:
            state_file = self.paths.get("state_files", {}).get(
                "cost_efficient", "state/cost_efficient_positions.json"
            )

        # Initialize market data and account services
        self.market_data = AlpacaMarketData()
        self.account_monitor = AlpacaAccountMonitor(mode="paper")
        self.order_manager = AlpacaOrderManager(mode="paper")

        # Load trading config
        self.config = TradingConfig()

        # Initialize position tracker for alerts
        self.position_tracker = PositionTracker(
            take_profit_pct=self.config.get_risk_config("take_profit"),
            stop_loss_pct=self.config.get_risk_config("stop_loss"),
            alert_cooldown_seconds=300,  # 5 minutes between alerts
        )

        # Initialize trailing stop manager for dynamic stop adjustments
        self.trailing_stop_manager = TrailingStopManager(
            order_manager=None,  # We handle broker calls separately via batch_modify_orders()
            config=self.config,
        )

        # Restore multi-day trailing stops from GTT persistence (Phase 3, Issue #340)
        if GTT_AVAILABLE:
            try:
                restore_result = restore_trailing_stops_from_gtt(self.trailing_stop_manager)
                if restore_result["restored"] > 0:
                    logger.info(
                        f"Restored {restore_result['restored']} trailing stops from GTT persistence"
                    )
            except Exception as e:
                logger.warning(f"Failed to restore trailing stops from GTT: {e}")

        # Initialize extracted components
        self.local_state_manager = LocalStateManager(
            state_file=state_file,
            position_tracker=self.position_tracker,
        )

        # No alert callback: alerts are checked once per routine, after the stop raise,
        # against the stops the positions end the run with
        self.broker_cache = BrokerStateCache(
            account_monitor=self.account_monitor, cache_ttl_seconds=60
        )

        self.reconciler = StateReconciler(
            config=self.config,
            trailing_stop_manager=self.trailing_stop_manager,
            position_tracker=self.position_tracker,
        )

        self.report_generator = ReportGenerator()
        self.last_report_path: Optional[str] = None  # report of the latest routine
        self._daily_records: Optional[DailyRecords] = None
        # Dry run (#601): the routines' default; a routine call can override it
        self.dry_run = dry_run

        logger.info("CostEfficientTradeCycle initialized with extracted components")

    @contextmanager
    def _dry_run_mode(self, dry_run: bool) -> Iterator[None]:
        """
        For a dry run: order calls go to a DryRunOrderManager (reads pass, nothing is
        sent), and the local state and trailing-stop states in memory are restored
        afterwards. The routines also skip the daily records, GTT actions and syncs, and
        saving local state while it is active.
        """
        if not dry_run:
            yield
            return
        real_order_manager = self.order_manager
        saved_state = copy.deepcopy(self.local_state_manager.state)
        trailing = self.trailing_stop_manager
        saved_stops = copy.deepcopy(trailing.stop_states) if trailing is not None else None
        tracker = self.position_tracker
        saved_tracker = (
            (copy.deepcopy(tracker.positions), tracker.alert_counter)
            if tracker is not None
            else None
        )
        self.order_manager = DryRunOrderManager(real_order_manager)
        self._dry_run_active = True
        try:
            yield
        finally:
            self.order_manager = real_order_manager
            self.local_state_manager.state = saved_state
            if trailing is not None:
                trailing.stop_states = saved_stops
            if tracker is not None:
                tracker.positions, tracker.alert_counter = saved_tracker
            self._dry_run_active = False

    def _start_routine(self) -> None:
        """Forget the previous routine's results, so a routine that fails early leaves none."""
        self.last_alerts = ()
        self.last_discrepancies = ()
        self.last_report_path = None
        self.last_open_symbols = None
        self._new_fills = []

    # -------------------------------------------------------------------------
    # Backward-compatible property accessors
    # -------------------------------------------------------------------------

    @property
    def local_state(self) -> Dict[str, Any]:
        """Get local state dict (backward compatibility)."""
        return self.local_state_manager.state

    @local_state.setter
    def local_state(self, value: Dict[str, Any]):
        """Set local state dict (backward compatibility)."""
        self.local_state_manager.state = value

    @property
    def state_file(self) -> str:
        """Get state file path (backward compatibility)."""
        return self.local_state_manager.state_file

    # -------------------------------------------------------------------------
    # Backward-compatible method wrappers
    # -------------------------------------------------------------------------

    def load_local_state(self) -> Dict[str, Any]:
        """Load local JSON state (backward compatibility)."""
        return self.local_state_manager.reload()

    def save_local_state(self):
        """Save local state to JSON (backward compatibility)."""
        self.local_state_manager.save()

    def fetch_broker_state(self, force_refresh: bool = False) -> Dict[str, Any]:
        """Get broker state with caching (backward compatibility)."""
        return self.broker_cache.fetch(force_refresh)

    def invalidate_broker_cache(self):
        """Invalidate broker cache (backward compatibility)."""
        self.broker_cache.invalidate()

    def get_cache_stats(self) -> Dict[str, Any]:
        """Get cache statistics (backward compatibility)."""
        return self.broker_cache.get_stats()

    def get_current_alerts(self) -> List[Any]:
        """Position alerts at the current broker state (backward compatibility)."""
        return self.check_position_alerts(self.fetch_broker_state())

    def reconcile_state(self, broker_state: Dict[str, Any]) -> List[Discrepancy]:
        """Reconcile state (backward compatibility)."""
        discrepancies, updated_state = self.reconciler.reconcile(
            broker_state, self.local_state_manager.state
        )
        self.local_state_manager.state = updated_state
        return discrepancies

    def calculate_stop_adjustments(self, broker_state: Dict[str, Any]) -> List[StopAdjustment]:
        """Calculate stop adjustments (backward compatibility)."""
        adjustments, updated_state = self.reconciler.calculate_stop_adjustments(
            broker_state, self.local_state_manager.state
        )
        self.local_state_manager.state = updated_state
        return adjustments

    def check_position_alerts(self, broker_state: Dict[str, Any]) -> List[PositionAlertSummary]:
        """Check position alerts (backward compatibility)."""
        return self.reconciler.check_position_alerts(broker_state, self.local_state_manager.state)

    def _position_alerts(
        self, broker_state: Dict[str, Any]
    ) -> Tuple[List[PositionAlertSummary], List[str]]:
        """
        The routine's position alerts (#599), checked after the stop raise so they measure
        the stops the positions end the run with.

        Errors are logged and reported, never raised: alerts don't protect positions, so
        they shouldn't make the routine retry.

        Returns:
            (alerts, report lines: a note when the check failed)
        """
        try:
            alerts = self.check_position_alerts(broker_state)
        except Exception as e:
            logger.warning(f"Position alerts not checked: {e}", exc_info=True)
            self.last_alerts = ()
            return [], ["", f"⚠️ Position alerts not checked: {e}"]
        self.last_alerts = tuple(alerts)
        return alerts, []

    def generate_position_summaries(
        self, broker_state: Dict[str, Any], adjustments: List[StopAdjustment]
    ) -> List[Any]:
        """Generate position summaries (backward compatibility)."""
        return self.report_generator.generate_position_summaries(
            broker_state, self.local_state_manager.state, adjustments
        )

    def generate_routine_report(
        self,
        routine_type: RoutineType,
        broker_state: Dict[str, Any],
        discrepancies: List[Discrepancy],
        adjustments: List[StopAdjustment],
        alerts: List[PositionAlertSummary] = None,
        modification_results: Dict[str, Any] = None,
    ) -> str:
        """Generate trading report (backward compatibility)."""
        return self.report_generator.generate_routine_report(
            routine_type,
            broker_state,
            self.local_state_manager.state,
            discrepancies,
            adjustments,
            alerts,
            modification_results,
        )

    # -------------------------------------------------------------------------
    # Order execution (kept in main class as it handles broker communication)
    # -------------------------------------------------------------------------

    def batch_modify_orders(self, adjustments: List[StopAdjustment]) -> Dict[str, Any]:
        """
        Batch modify stop orders with enhanced logging.
        """
        if not adjustments:
            logger.info("No stop adjustments to execute")
            return {"success": True, "modifications": 0, "errors": []}

        logger.info(f"Executing {len(adjustments)} stop order modifications...")

        results = {"success": True, "modifications": 0, "errors": [], "details": [], "dry_run": []}

        for i, adjustment in enumerate(adjustments, 1):
            try:
                logger.info(f"[{i}/{len(adjustments)}] Modifying {adjustment.symbol} stop order...")
                logger.debug(f"  Order ID: {adjustment.order_id}")
                logger.debug(f"  Current stop: ${adjustment.current_stop:.2f}")
                logger.debug(f"  New stop: ${adjustment.new_stop:.2f}")
                logger.debug(f"  Reason: {adjustment.reason}")

                # Modify the stop order (returns a dict; status "submitted" on success)
                result = self.order_manager.modify_order(
                    order_id=adjustment.order_id, stop_price=adjustment.new_stop
                )

                if result.get("status") == DRY_RUN_STATUS:
                    line = (
                        f"{adjustment.symbol}: would raise stop "
                        f"${adjustment.current_stop:.2f} -> ${adjustment.new_stop:.2f}"
                    )
                    results["dry_run"].append(line)
                    logger.info(f"DRY RUN: {line}")
                elif result.get("status") == "submitted":
                    results["modifications"] += 1
                    # Recorded only now: a refused raise is computed again next run
                    self._remember_stop(
                        adjustment.symbol, adjustment.new_stop, result.get("order_id")
                    )
                    entry_price = (
                        self.local_state_manager.state.get("positions", {})
                        .get(adjustment.symbol, {})
                        .get("entry_price", 0)
                    )
                    profit_locked = adjustment.new_stop - float(entry_price)
                    detail_msg = (
                        f"✅ {adjustment.symbol}: Stop adjusted to ${adjustment.new_stop:.2f} "
                        f"(locking ${profit_locked:+.2f} profit)"
                    )
                    results["details"].append(detail_msg)
                    logger.info(detail_msg)
                else:
                    error_msg = (
                        f"Failed to modify {adjustment.symbol} stop order: "
                        f"{result.get('message', 'unknown error')}"
                    )
                    results["errors"].append(error_msg)
                    results["success"] = False
                    logger.error(error_msg)

            except Exception as e:
                error_msg = f"Error modifying {adjustment.symbol} stop: {e}"
                results["errors"].append(error_msg)
                logger.error(error_msg)
                logger.exception("Full exception details:")
                results["success"] = False

        summary = (
            f"Stop adjustment batch complete: "
            f"{results['modifications']}/{len(adjustments)} successful, "
            f"{len(results['errors'])} errors"
        )
        logger.info(summary)

        # Invalidate cache after modifying orders
        if results["modifications"] > 0:
            self.broker_cache.invalidate()

        return results

    def raise_stops(
        self, broker_state: Dict[str, Any]
    ) -> Tuple[List[StopAdjustment], Optional[Dict[str, Any]]]:
        """
        Raise live stops to their trailing level (a modify call only for stops that move).

        Errors are contained and reported: the live stops still protect the positions,
        and the next run works the raise out again.

        Returns:
            (adjustments, modification results or None when no stop needed raising)
        """
        try:
            adjustments = self.calculate_stop_adjustments(broker_state)
        except Exception as e:
            logger.error(f"Stop raise failed: {e}", exc_info=True)
            return [], {"success": False, "modifications": 0, "errors": [f"Stop raise failed: {e}"]}
        modification_results = self.batch_modify_orders(adjustments) if adjustments else None
        return adjustments, modification_results

    def _after_gtt_actions(
        self, broker_state: Dict[str, Any], gtt_results: List[Any]
    ) -> Dict[str, Any]:
        """Broker state fetched again if a GTT order or cancel went out (stops are raised next)."""
        if any(r.success and r.action_type != "alert" for r in gtt_results):
            return self.fetch_broker_state(force_refresh=True)
        return broker_state

    # -------------------------------------------------------------------------
    # Protective stops - Issue #586
    # -------------------------------------------------------------------------

    def check_and_repair_stops(self, broker_state: Dict[str, Any]) -> List[Discrepancy]:
        """
        Re-place missing protective stops and renew GTC stops near Alpaca's 90-day limit.

        Errors are contained, so a failure here never stops the rest of the routine.

        Returns:
            Discrepancies to record and report (repairs with their outcome, and cases
            left for review)
        """
        try:
            needs_review, repairs = self.reconciler.check_protective_stops(
                broker_state, self.local_state_manager.state
            )
            return needs_review + self.repair_stops(repairs)
        except Exception as e:
            logger.error(f"Protective-stop check failed: {e}", exc_info=True)
            return [
                Discrepancy(
                    type="STOP_CHECK_FAILED",
                    symbol="*",
                    details={"reason": str(e)},
                    action="NEEDS_HUMAN_REVIEW",
                    severity="HIGH",
                )
            ]

    def repair_stops(self, repairs: List[StopRepair]) -> List[Discrepancy]:
        """
        Place or renew protective stops.

        A renewal replaces the old stop order, which creates a new order and so restarts
        Alpaca's 90-day GTC clock.

        Returns:
            One discrepancy per repair, with its outcome
        """
        discrepancies = []
        for repair in repairs:
            if repair.replaces_order_id:
                result = self.order_manager.modify_order(
                    order_id=repair.replaces_order_id, stop_price=repair.stop_price
                )
            else:
                result = self.order_manager.place_stop_order(
                    symbol=repair.symbol,
                    qty=repair.quantity,
                    side="sell",
                    stop_price=repair.stop_price,
                    time_in_force="gtc",
                )

            ok = result.get("status") == "submitted"
            not_sent = result.get("status") == DRY_RUN_STATUS
            verb = "renewed" if repair.replaces_order_id else "placed"
            if not_sent:
                logger.info(
                    f"DRY RUN: {repair.symbol}: stop would be {verb} at ${repair.stop_price:.2f}"
                )
            elif ok:
                logger.info(f"{repair.symbol}: stop {verb} at ${repair.stop_price:.2f}")
                self._remember_stop(repair.symbol, repair.stop_price, result.get("order_id"))
            else:
                logger.error(
                    f"{repair.symbol}: stop not {verb}: {result.get('message', 'unknown error')}"
                )
            discrepancies.append(
                Discrepancy(
                    type=repair.kind,
                    symbol=repair.symbol,
                    details={
                        "quantity": repair.quantity,
                        "stop_price": repair.stop_price,
                        "reason": repair.reason,
                        "replaced_order_id": repair.replaces_order_id,
                        "order_id": result.get("order_id"),
                        "error": None if ok or not_sent else result.get("message", "unknown error"),
                    },
                    action=(
                        DRY_RUN_ACTION if not_sent else "AUTO_FIXED" if ok else "NEEDS_HUMAN_REVIEW"
                    ),
                    severity="MEDIUM"
                    if (ok or not_sent) and repair.kind == "STOP_EXPIRING"
                    else "HIGH",
                )
            )

        if any(d.action == "AUTO_FIXED" for d in discrepancies):
            self.broker_cache.invalidate()
        return discrepancies

    def _remember_stop(self, symbol: str, stop_price: float, order_id: Optional[str]) -> None:
        """
        Record a stop the broker accepted (placed, renewed or raised) in local state and
        the trailing-stop manager, with its order id (a replace creates a new order).
        """
        position = self.local_state_manager.state.get("positions", {}).get(symbol)
        if position is not None:
            position["stop_price"] = stop_price
        manager = self.trailing_stop_manager
        stop_state = manager.stop_states.get(symbol) if manager is not None else None
        if stop_state is not None:
            stop_state.current_stop = stop_price
            if order_id:
                stop_state.stop_order_id = order_id

    # -------------------------------------------------------------------------
    # GTT (Good-Till-Triggered) Integration - Issue #340
    # -------------------------------------------------------------------------

    def check_gtt_triggers(self, broker_state: Dict[str, Any]) -> List[Any]:
        """
        Check GTT triggers against current prices.

        Args:
            broker_state: Current broker state with positions/prices

        Returns:
            List of ActionResult for triggers that fired
        """
        if not GTT_AVAILABLE:
            return []

        try:
            prices = _position_prices(broker_state)
            if not prices:
                logger.debug("No prices available for GTT check")
                return []

            # Evaluate triggers; sells are kept within the shares free to sell
            evaluator = get_trigger_evaluator()
            # A dry run doesn't save the trailing stops' new highs
            fired = evaluator.evaluate_triggers_batch(prices, persist=not self._dry_run_active)
            triggered, skipped = _within_free_shares(fired, broker_state)
            results = []
            for trigger, reason in skipped:
                logger.info(f"GTT {trigger.id} ({trigger.symbol}) {reason}")
                results.append(
                    ActionResult(
                        trigger_id=trigger.id,
                        success=False,
                        action_type=trigger.action_type,
                        message=f"{trigger.symbol} sell {reason}",
                        details={"symbol": trigger.symbol, "skipped": True},
                    )
                )

            if not triggered:
                logger.debug("No GTT triggers to execute")
                return results

            if self._dry_run_active:
                return results + _not_executed(triggered)

            # Execute actions for triggered GTTs
            executor = get_action_executor()
            executed = executor.execute_triggers_batch(triggered, prices)

            # Log summary
            success_count = sum(1 for r in executed if r.success)
            logger.info(f"GTT: {len(triggered)} triggers fired, {success_count} actions executed")

            return results + executed

        except Exception as e:
            logger.error(f"GTT check failed: {e}")
            return []

    # -------------------------------------------------------------------------
    # Daily records - Issue #574
    # -------------------------------------------------------------------------

    @property
    def daily_records(self) -> DailyRecords:
        """Account, position and fill records in state/user.db (created on first use)."""
        if self._daily_records is None:
            self._daily_records = DailyRecords()
        return self._daily_records

    def record_daily(self, routine: str, broker_state: Dict[str, Any]) -> List[str]:
        """
        Record the account, the positions and any orders filled since the last run.

        Errors are logged and reported, never raised: the records don't protect positions,
        so they shouldn't make the routine retry.

        Returns:
            Report lines (equity and day P&L, fills since the previous run, the day's
            realized P&L)
        """
        if self._dry_run_active:
            return ["", "🧪 Dry run: daily records not saved"]
        try:
            records = self.daily_records
            market_date = market_today()
            fetched_at = broker_state.get("fetched_at")
            positions = broker_state["positions"]
            # Symbols held now or at the previous run: their closed orders are fetched
            # per symbol too, so an old bracket whose leg fills today (its parent long
            # past the latest 100 closed orders) is still found
            symbols = sorted(set(positions) | set(records.last_symbols()))

            records.record_account(market_date, routine, broker_state["account"], fetched_at)
            records.record_positions(market_date, routine, positions, fetched_at)
            closed = self.account_monitor.get_orders(status="closed", limit=100)
            if symbols:
                closed += self.account_monitor.get_orders(
                    status="closed", limit=500, symbols=symbols
                )
            self._new_fills = records.record_fills(closed)
            return records.summary_lines(market_date, routine)
        except Exception as e:
            logger.warning(f"Daily records not saved: {e}", exc_info=True)
            return ["", f"⚠️ Daily records not saved: {e}"]

    def _settle_closed_positions(
        self, broker_state: Dict[str, Any], discrepancies: List[Discrepancy]
    ) -> List[Discrepancy]:
        """
        After the reconcile: name normal exits, and drop the trailing state of positions
        that closed (#634). Without this it outlives them, and a later purchase of the
        symbol inherits the old entry and high, and the GTT trigger that could sell it.

        A moved average entry means the position was sold and bought again, or added to,
        so its state starts afresh too; that can't lower a stop, as a raise only goes up.

        Errors are logged, never raised: this doesn't protect a position.

        Returns:
            The discrepancies, a position sold by a fill in this run's records named as
            closed rather than as a ghost
        """
        positions = broker_state["positions"]
        self.last_open_symbols = frozenset(positions)
        settled = discrepancies
        try:
            settled = [self._closed_by_fill(d) for d in discrepancies]
            manager = self.trailing_stop_manager
            if self._dry_run_active or manager is None:
                return settled

            stale = set()
            for symbol, state in manager.stop_states.items():
                position = positions.get(symbol)
                if position is None or (
                    abs(position["entry_price"] - state.entry_price)
                    > ENTRY_CHANGE_TOLERANCE * state.entry_price
                ):
                    stale.add(symbol)
                else:
                    state.quantity = position["quantity"]
            for symbol in stale:
                manager.unregister_position(symbol)
            if GTT_AVAILABLE:
                cleanup_orphaned_trailing_stops(set(positions) - stale)
        except Exception as e:
            logger.warning(f"Closed positions not settled: {e}", exc_info=True)
        return settled

    def _closed_by_fill(self, discrepancy: Discrepancy) -> Discrepancy:
        """A position gone from the broker that a new sell fill explains is a normal exit."""
        if discrepancy.type != "GHOST_POSITION":
            return discrepancy
        sale = next(
            (
                fill
                for fill in reversed(self._new_fills)
                if fill.get("symbol") == discrepancy.symbol
                and "sell" in str(fill.get("side", "")).lower()
            ),
            None,
        )
        if sale is None:
            return discrepancy
        return replace(
            discrepancy,
            type="POSITION_CLOSED",
            severity="LOW",
            details={
                **discrepancy.details,
                "order_type": sale.get("order_type"),
                "fill_price": float(sale["filled_avg_price"]),
            },
        )

    def _sync_trailing_stops_to_gtt(self) -> None:
        """Save the trailing stops' state to GTT for the next run (not in a dry run)."""
        if not GTT_AVAILABLE or self._dry_run_active:
            return
        try:
            sync_result = sync_all_trailing_stops(self.trailing_stop_manager)
            if sync_result["synced"] > 0:
                logger.info(f"Synced {sync_result['synced']} trailing stops to GTT")
        except Exception as e:
            logger.warning(f"Failed to sync trailing stops to GTT: {e}")

    def _save_report(self, routine: str, report: str) -> Tuple[str, str]:
        """
        Write a routine's report to reports/daily/. A dry run's report starts with a
        banner and has its own name (<date>_<routine>_dry_run.md).

        Returns:
            (the report as saved, its path)
        """
        if self._dry_run_active:
            report = DRY_RUN_BANNER + report
            routine = f"{routine}_dry_run"
        report_file = self._get_report_file_path(routine)
        os.makedirs(os.path.dirname(report_file), exist_ok=True)
        with open(report_file, "w", encoding="utf-8") as f:
            f.write(report)
        self.last_report_path = report_file
        return report, report_file

    def _append_report_sections(
        self, report: str, gtt_results: List[Any], record_lines: List[str]
    ) -> str:
        """The routine report with its GTT results, daily records and open notices, if any."""
        if gtt_results:
            report += "\n" + "\n".join(self._format_gtt_results_for_report(gtt_results))
        if record_lines:
            report += "\n" + "\n".join(record_lines)
        notice_lines = self._open_notice_lines()
        if notice_lines:
            report += "\n" + "\n".join(notice_lines)
        return report

    def _open_notice_lines(self) -> List[str]:
        """
        Report lines for the notices nobody has acknowledged (resolved ones are marked), the
        latest REPORT_NOTICES of them, or none. Contained: notices never make a routine fail.
        """
        if self.notice_store is None:
            return []
        try:
            notices = list(self.notice_store.open_notices())
            lines = [f"- {notice.line()}" for notice in notices[-REPORT_NOTICES:]]
        except Exception as e:
            logger.warning(f"Open notices not listed in the report: {e}")
            return []
        if not lines:
            return []
        older = len(notices) - len(lines)
        if older:
            lines.append(
                f"- ... and {older} older ones (`notices` in the scheduler CLI lists them all)"
            )
        return ["", "## Open Notices", ""] + lines

    def _format_gtt_results_for_report(self, gtt_results: List[Any]) -> List[str]:
        """Format GTT results for inclusion in routine report."""
        if not gtt_results:
            return []

        lines = [
            "",
            "## GTT Triggers Fired",
            "",
        ]

        for result in gtt_results:
            details = result.details or {}
            status = "~" if details.get("dry_run") else "+" if result.success else "x"
            lines.append(f"[{status}] Trigger {result.trigger_id}: {result.message}")

            if details.get("symbol"):
                lines.append(f"    Symbol: {details['symbol']}")
            if details.get("current_price"):
                lines.append(f"    Price: ${details['current_price']:.2f}")
            if details.get("oco_disabled"):
                lines.append(f"    OCO: Disabled {details['oco_disabled']} partner(s)")

        return lines

    # -------------------------------------------------------------------------
    # Trading routines
    # -------------------------------------------------------------------------

    def morning_routine(self, raise_errors: bool = False, dry_run: Optional[bool] = None) -> str:
        """
        Run once at 9:20 AM ET - before market open.
        Minimal API calls, maximum insight.

        Args:
            raise_errors: Raise RoutineError on failure, including protective stops that
                couldn't be checked, placed or renewed (the report is still saved), instead
                of returning an error message. The scheduler uses this to retry.
            dry_run: Send no orders and change no saved state; the report shows what the
                routine would have done (#601). Default: the cycle's dry_run setting.
        """
        dry_run = self.dry_run if dry_run is None else dry_run
        with self._dry_run_mode(dry_run):
            return self._morning_routine(raise_errors)

    def evening_routine(self, raise_errors: bool = False, dry_run: Optional[bool] = None) -> str:
        """
        Run once at 3:50 PM ET - before market close.
        Focus on EOD position review and preparation for next day. Stops are raised on
        the day's gains here, before the close, rather than at the next morning run.

        Args:
            raise_errors, dry_run: As for morning_routine()
        """
        dry_run = self.dry_run if dry_run is None else dry_run
        with self._dry_run_mode(dry_run):
            return self._evening_routine(raise_errors)

    def _morning_routine(self, raise_errors: bool) -> str:
        """The morning routine's steps (see morning_routine)."""
        self._start_routine()
        logger.info("Starting morning routine%s...", " (dry run)" if self._dry_run_active else "")

        try:
            # Step 1: Single API call to get all positions/orders
            broker_state = self.fetch_broker_state()

            # Step 1.5: Daily records - account, positions, new fills (Issue #574)
            record_lines = self.record_daily("morning", broker_state)

            # Step 2: Reconcile with local JSON (no API calls)
            discrepancies = self.reconcile_state(broker_state)
            discrepancies = self._settle_closed_positions(broker_state, discrepancies)

            # Step 2.5: Re-place missing stops, renew aging GTC ones (Issue #586)
            stop_discrepancies = self.check_and_repair_stops(broker_state)
            discrepancies.extend(stop_discrepancies)
            self.last_discrepancies = tuple(discrepancies)
            if any(d.action == "AUTO_FIXED" for d in stop_discrepancies):
                # Later steps (stop adjustments) must see the new stop orders
                broker_state = self.fetch_broker_state(force_refresh=True)

            # Step 3: Check GTT triggers (no API calls) - Issue #340
            gtt_results = self.check_gtt_triggers(broker_state)
            broker_state = self._after_gtt_actions(broker_state, gtt_results)

            # Steps 4-5: Raise stops to their trailing level
            adjustments, modification_results = self.raise_stops(broker_state)

            # Step 5.2: Position alerts against the stops after the raise (no API calls)
            alerts, alert_lines = self._position_alerts(broker_state)

            # Step 5.5: Sync trailing stops to GTT for multi-day persistence (Phase 3, Issue #340)
            self._sync_trailing_stops_to_gtt()

            # Step 6: Save updated local state (not in a dry run)
            self.local_state_manager.state["discrepancies"] = [asdict(d) for d in discrepancies]
            if not self._dry_run_active:
                self.local_state_manager.save()

            # Step 7: Generate human report with alerts
            report = self.generate_routine_report(
                RoutineType.MORNING,
                broker_state,
                discrepancies,
                adjustments,
                alerts,
                modification_results,
            )

            report = self._append_report_sections(report, gtt_results, alert_lines + record_lines)
            report, report_file = self._save_report("morning", report)
            logger.info(f"Morning routine complete. Report saved to {report_file}")
            if raise_errors:
                _check_stops_secured(discrepancies)
            return report

        except Exception as e:
            error_msg = f"Morning routine failed: {e}"
            logger.error(error_msg)
            if raise_errors:
                raise RoutineError(error_msg) from e
            return f"❌ {error_msg}"

    def _evening_routine(self, raise_errors: bool) -> str:
        """The evening routine's steps (see evening_routine)."""
        self._start_routine()
        logger.info("Starting evening routine%s...", " (dry run)" if self._dry_run_active else "")

        try:
            # Similar structure to morning, but focus on EOD analysis
            broker_state = self.fetch_broker_state()
            record_lines = self.record_daily("evening", broker_state)  # Issue #574
            discrepancies = self.reconcile_state(broker_state)
            discrepancies = self._settle_closed_positions(broker_state, discrepancies)

            # Re-place missing stops, renew aging GTC ones (Issue #586)
            stop_discrepancies = self.check_and_repair_stops(broker_state)
            discrepancies.extend(stop_discrepancies)
            self.last_discrepancies = tuple(discrepancies)
            if any(d.action == "AUTO_FIXED" for d in stop_discrepancies):
                # Later steps (the GTT check counts shares held by stops) see the new stops
                broker_state = self.fetch_broker_state(force_refresh=True)

            # Check GTT triggers - Issue #340
            gtt_results = self.check_gtt_triggers(broker_state)
            broker_state = self._after_gtt_actions(broker_state, gtt_results)

            # Raise stops to their trailing level while the market is open, so an
            # overnight gap can't take the day's gains before the morning run. Run
            # after hours (by hand), the raise is left to the morning: thin
            # after-hours prints shouldn't set the stop.
            adjustments, modification_results = [], None
            if is_market_hours():
                adjustments, modification_results = self.raise_stops(broker_state)
            else:
                logger.info("Market closed: the stop raise is left to the morning routine")

            # Position alerts against the stops after the raise
            alerts, alert_lines = self._position_alerts(broker_state)

            # Sync trailing stops to GTT for multi-day persistence (Phase 3, Issue #340)
            self._sync_trailing_stops_to_gtt()

            if not self._dry_run_active:
                self.local_state_manager.save()

            report = self.generate_routine_report(
                RoutineType.EVENING,
                broker_state,
                discrepancies,
                adjustments,
                alerts,
                modification_results,
            )

            report = self._append_report_sections(report, gtt_results, alert_lines + record_lines)
            report, report_file = self._save_report("evening", report)
            logger.info(f"Evening routine complete. Report saved to {report_file}")
            if raise_errors:
                _check_stops_secured(discrepancies)
            return report

        except Exception as e:
            error_msg = f"Evening routine failed: {e}"
            logger.error(error_msg)
            if raise_errors:
                raise RoutineError(error_msg) from e
            return f"❌ {error_msg}"

    def recover_from_crash(self, raise_errors: bool = False, dry_run: Optional[bool] = None) -> str:
        """
        Rebuild state from broker, minimal API calls.
        Use when system restarts or local state is corrupted.

        Args:
            raise_errors: Raise RoutineError on failure instead of returning a message
            dry_run: Rebuild and report, but save no local state (#601). Default: the
                cycle's dry_run setting.
        """
        dry_run = self.dry_run if dry_run is None else dry_run
        with self._dry_run_mode(dry_run):
            return self._recover_from_crash(raise_errors)

    def _recover_from_crash(self, raise_errors: bool) -> str:
        """The crash recovery's steps (see recover_from_crash)."""
        self._start_routine()
        logger.info("Starting crash recovery%s...", " (dry run)" if self._dry_run_active else "")

        try:
            # One API call to get everything
            broker_state = self.fetch_broker_state()

            # Reset local state for recovery
            self.local_state_manager.reset_for_recovery()

            # Auto-discover all positions
            for symbol, broker_pos in broker_state["positions"].items():
                self.local_state_manager.set_position(
                    symbol,
                    {
                        "entry_price": broker_pos["entry_price"],
                        "quantity": broker_pos["quantity"],
                        "entry_time": "UNKNOWN",
                        "source": "CRASH_RECOVERY",
                        "stop_price": None,  # Will need human input
                        "target_price": None,  # Will need human input
                    },
                )

            if not self._dry_run_active:
                self.local_state_manager.save()

            # Generate recovery report
            recovery_lines = [
                "# Crash Recovery Report - "
                f"{get_datetime_now(ZoneInfo(DEFAULT_TIMEZONE)).strftime('%Y-%m-%d %H:%M:%S')} ET",
                "",
                f"Recovered {len(broker_state['positions'])} positions",
                f"Recovered {len(broker_state['orders'])} order groups",
                "",
                "## Recovered Positions",
            ]

            for symbol, pos in self.local_state_manager.positions.items():
                recovery_lines.append(
                    f"- {symbol}: {pos['quantity']} shares @ ${pos['entry_price']:.2f}"
                )

            recovery_lines.extend(
                [
                    "",
                    "⚠️ **MANUAL ACTION REQUIRED:**",
                    "- Review all positions for accuracy",
                    "- Set stop_price and target_price for each position",
                    "- Verify entry times if needed for tax reporting",
                    "",
                ]
            )

            report = "\n".join(recovery_lines)

            report, report_file = self._save_report("recovery", report)
            logger.info(f"Crash recovery complete. Report saved to {report_file}")
            return report

        except Exception as e:
            error_msg = f"Crash recovery failed: {e}"
            logger.error(error_msg)
            if raise_errors:
                raise RoutineError(error_msg) from e
            return f"❌ {error_msg}"

    def _get_report_file_path(self, routine_type: str) -> str:
        """
        Get unique report file path with counter for multiple runs per day.

        Named by the market (ET) date, as the daily records are: an evening run at
        22:44 ET is that day's, whatever the host's timezone.
        """
        date_str = market_today()

        template = self.paths.get("report_templates", {}).get(
            "daily_routine", "reports/daily/{date}_{routine_type}.md"
        )
        base_name = template.format(date=date_str, routine_type=routine_type).replace(".md", "")

        # Check for existing files and append counter if needed
        report_file = f"{base_name}.md"
        counter = 1
        while os.path.exists(report_file):
            counter += 1
            report_file = f"{base_name}_{counter}.md"

        return report_file


def main():
    """Demo the cost-efficient trade cycle"""
    print("=== Cost-Efficient Trade Cycle Demo ===")

    try:
        cycle = CostEfficientTradeCycle()

        print("\n1. Running morning routine...")
        morning_report = cycle.morning_routine()
        print(morning_report)

        print("\n" + "=" * 50)
        print("Morning routine complete!")
        print("Next: Run evening routine at 3:50 PM ET")

    except Exception as e:
        print(f"Demo failed: {e}")
        import traceback

        traceback.print_exc()


if __name__ == "__main__":
    main()
