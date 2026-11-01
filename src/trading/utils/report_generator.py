"""
ReportGenerator - Generate human-readable trading reports.

Extracted from trading_cycle.py as part of #439 refactoring.
Handles report generation for morning/evening routines.
"""

import logging
from dataclasses import dataclass
from enum import Enum
from typing import Any, Dict, List, Optional
from zoneinfo import ZoneInfo

from src.utils.date_utils import DEFAULT_TIMEZONE, get_datetime_now

logger = logging.getLogger(__name__)


class RoutineType(Enum):
    """Type of trading routine."""

    MORNING = "morning"
    EVENING = "evening"
    RECOVERY = "recovery"


@dataclass
class PositionSummary:
    """Summary of a position for reporting."""

    symbol: str
    entry_price: float
    current_price: float
    stop_price: float
    target_price: float
    quantity: int
    unrealized_pl: float
    unrealized_percent: float
    stop_action: str  # "No change", "Move to breakeven", etc.


def _detail_text(details: Dict[str, Any]) -> str:
    """One line for a discrepancy's details: stop/limit, shares, reason, error."""
    parts = []
    for key, label in (("stop_price", "stop"), ("limit_price", "limit")):
        if details.get(key) is not None:
            parts.append(f"{label} ${float(details[key]):.2f}")
    if details.get("quantity"):
        parts.append(f"{details['quantity']} shares")
    if details.get("current_price"):
        parts.append(f"price ${float(details['current_price']):.2f}")
    for key in ("reason", "error"):
        if details.get(key):
            parts.append(str(details[key]))
    return ", ".join(parts) or "no details"


class ReportGenerator:
    """
    Generates human-readable trading reports.

    Creates formatted markdown reports for morning/evening routines.
    """

    def __init__(self):
        """Initialize ReportGenerator."""
        logger.info("ReportGenerator initialized")

    def generate_position_summaries(
        self,
        broker_state: Dict[str, Any],
        local_state: Dict[str, Any],
        adjustments: List[Any],
    ) -> List[PositionSummary]:
        """
        Generate position summaries for reporting.

        Args:
            broker_state: Current broker state
            local_state: Current local state
            adjustments: List of StopAdjustment objects

        Returns:
            List of PositionSummary objects
        """
        summaries = []
        positions = local_state.get("positions", {})

        # Create adjustment lookup
        adjustment_map = {adj.symbol: adj for adj in adjustments}

        for symbol, broker_pos in broker_state["positions"].items():
            if symbol not in positions:
                continue

            local_pos = positions[symbol]
            current_price = float(broker_pos.get("current_price") or 0)
            entry_price = float(local_pos.get("entry_price") or 0)
            quantity = int(broker_pos.get("quantity") or 0)

            # Calculate P&L
            unrealized_pl = (current_price - entry_price) * quantity
            unrealized_percent = ((current_price - entry_price) / entry_price) if entry_price else 0

            # Determine stop action
            stop_action = "No change"
            if symbol in adjustment_map:
                stop_action = adjustment_map[symbol].reason

            summaries.append(
                PositionSummary(
                    symbol=symbol,
                    entry_price=entry_price,
                    current_price=current_price,
                    stop_price=float(local_pos.get("stop_price") or 0),
                    target_price=float(local_pos.get("target_price") or 0),
                    quantity=quantity,
                    unrealized_pl=unrealized_pl,
                    unrealized_percent=unrealized_percent,
                    stop_action=stop_action,
                )
            )

        return summaries

    def generate_routine_report(  # noqa: C901
        self,
        routine_type: RoutineType,
        broker_state: Dict[str, Any],
        local_state: Dict[str, Any],
        discrepancies: List[Any],
        adjustments: List[Any],
        alerts: Optional[List[Any]] = None,
        modification_results: Optional[Dict[str, Any]] = None,
    ) -> str:
        """
        Generate human-readable trading report with alerts.

        Args:
            routine_type: Type of routine (morning/evening/recovery)
            broker_state: Current broker state
            local_state: Current local state
            discrepancies: List of Discrepancy objects
            adjustments: List of StopAdjustment objects
            alerts: Optional list of PositionAlertSummary objects
            modification_results: Optional results from batch order modifications

        Returns:
            Formatted markdown report string
        """
        now = get_datetime_now(ZoneInfo(DEFAULT_TIMEZONE))  # ET, as the file name's date
        report_lines = [
            f"# {routine_type.value.title()} Trading Report - "
            f"{now.strftime('%Y-%m-%d %H:%M:%S')} ET",
            "",
        ]

        # Account summary
        account = broker_state.get("account", {})
        portfolio_value = float(account.get("portfolio_value") or 0)
        cash = float(account.get("cash") or 0)
        buying_power = float(account.get("buying_power") or 0)

        report_lines.extend(
            [
                "## Account Summary",
                f"Portfolio Value: ${portfolio_value:,.2f}",
                f"Available Cash: ${cash:,.2f}",
                f"Buying Power: ${buying_power:,.2f}",
                "",
            ]
        )

        # Position summaries
        summaries = self.generate_position_summaries(broker_state, local_state, adjustments)
        if summaries:
            report_lines.extend(
                [
                    "## Active Positions",
                    "| Symbol | Entry | Current | Stop | Target | P&L | Action |",
                    "|--------|-------|---------|------|--------|-----|--------|",
                ]
            )

            for summary in summaries:
                pl_sign = "+" if summary.unrealized_pl >= 0 else ""
                pl_str = f"{pl_sign}${summary.unrealized_pl:.0f} ({summary.unrealized_percent:.1%})"
                # No target order: the position exits by its trailing stop
                target = f"${summary.target_price:.2f}" if summary.target_price else "none"
                report_lines.append(
                    f"| {summary.symbol} | ${summary.entry_price:.2f} | "
                    f"${summary.current_price:.2f} | ${summary.stop_price:.2f} | "
                    f"{target} | {pl_str} | {summary.stop_action} |"
                )
            report_lines.append("")
        else:
            report_lines.extend(["## Active Positions", "No active positions", ""])

        # Discrepancies
        if discrepancies:
            report_lines.extend(["## Discrepancies Found"])
            for disc in discrepancies:
                severity_emoji = {"HIGH": "🚨", "MEDIUM": "⚠️", "LOW": "ℹ️"}.get(disc.severity, "ℹ️")
                report_lines.append(f"{severity_emoji} {disc.type}: {disc.symbol}")

                if disc.type == "UNKNOWN_POSITION":
                    qty = disc.details["broker_quantity"]
                    entry = disc.details["broker_entry"]
                    report_lines.append(f"   {qty} shares at ${entry:.2f} - {disc.action}")
                elif disc.type == "POSITION_CLOSED":
                    qty = disc.details["local_quantity"]
                    how = disc.details["order_type"] or "order"
                    price = disc.details["fill_price"]
                    report_lines.append(f"   {qty} shares sold @ ${price:.2f} ({how})")
                elif disc.type == "GHOST_POSITION":
                    qty = disc.details["local_quantity"]
                    report_lines.append(f"   Phantom {qty} shares - {disc.action}")
                elif disc.type == "QUANTITY_MISMATCH":
                    broker_qty = disc.details["broker_quantity"]
                    local_qty = disc.details["local_quantity"]
                    report_lines.append(
                        f"   Broker: {broker_qty}, Local: {local_qty} - {disc.action}"
                    )
                else:
                    # Protective stops (#586) and any other type: details plus action
                    report_lines.append(f"   {_detail_text(disc.details)} - {disc.action}")

                report_lines.append("")

        # Position Alerts
        if alerts:
            report_lines.extend(["## Position Alerts"])
            # Count by severity
            critical_count = len([a for a in alerts if a.severity == "CRITICAL"])
            warning_count = len([a for a in alerts if a.severity == "WARNING"])
            info_count = len([a for a in alerts if a.severity == "INFO"])

            report_lines.append(
                f"Total Alerts: {len(alerts)} "
                f"({critical_count} Critical, {warning_count} Warning, {info_count} Info)"
            )
            report_lines.append("")

            for alert in alerts:
                report_lines.append(alert.message)

            report_lines.append("")

        # Stop modifications
        if modification_results:
            report_lines.extend(["## Stop Adjustments"])
            if modification_results["modifications"] > 0:
                report_lines.append(
                    f"✅ {modification_results['modifications']} stops adjusted successfully"
                )

            if modification_results["errors"]:
                report_lines.append("❌ Errors:")
                for error in modification_results["errors"]:
                    report_lines.append(f"   {error}")
            if modification_results.get("dry_run"):
                report_lines.append("🧪 Not sent (dry run):")
                for line in modification_results["dry_run"]:
                    report_lines.append(f"   {line}")
            report_lines.append("")

        # Footer
        next_routine = "evening" if routine_type == RoutineType.MORNING else "morning"
        next_time = "15:50:00" if routine_type == RoutineType.MORNING else "09:20:00"
        report_lines.extend(
            [
                "---",
                f"Generated: {now.strftime('%Y-%m-%d %H:%M:%S')} ET",
                f"Next {next_routine} routine: {next_time}",
                "",
                "**Note**: Stops are synced from the broker's open stop orders each run. A",
                "position without a live stop shows its last known level and is listed under",
                "Discrepancies (re-placed, or left for review).",
            ]
        )

        return "\n".join(report_lines)
