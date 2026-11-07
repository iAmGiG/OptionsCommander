#!/usr/bin/env python3
"""
Unit tests for PartialExitManager

Issue #248: Implement Partial Position Exits

Tests cover:
- Target calculation (50/50 split)
- Position registration
- Limit order placement for Target 1
- Trailing stop registration for Target 2
- Fill tracking
- Position summary
- Mode manager integration
"""

import unittest
from unittest.mock import Mock, patch

from src.trading.orders.partial_exit_manager import ExitTarget, PartialExitManager, PartialExitState


class TestExitTarget(unittest.TestCase):
    """Test ExitTarget dataclass."""

    def test_exit_target_creation(self):
        """Test creating an ExitTarget."""
        target = ExitTarget(
            target_number=1, quantity=50, ratio=0.5, exit_type="limit", exit_price=105.0
        )

        self.assertEqual(target.target_number, 1)
        self.assertEqual(target.quantity, 50)
        self.assertEqual(target.ratio, 0.5)
        self.assertEqual(target.exit_type, "limit")
        self.assertEqual(target.exit_price, 105.0)
        self.assertFalse(target.filled)
        self.assertIsNone(target.filled_at)


class TestPartialExitState(unittest.TestCase):
    """Test PartialExitState dataclass."""

    def test_partial_exit_state_creation(self):
        """Test creating a PartialExitState."""
        targets = [
            ExitTarget(1, 50, 0.5, "limit", 105.0),
            ExitTarget(2, 50, 0.5, "trailing", None),
        ]

        state = PartialExitState(
            symbol="AAPL",
            entry_price=100.0,
            total_quantity=100,
            targets=targets,
            stop_price=95.0,
        )

        self.assertEqual(state.symbol, "AAPL")
        self.assertEqual(state.total_quantity, 100)
        self.assertEqual(len(state.targets), 2)
        self.assertIsNotNone(state.registered_at)
        self.assertIsNotNone(state.last_updated)

    def test_get_remaining_quantity(self):
        """Test calculating remaining quantity after fills."""
        targets = [
            ExitTarget(1, 50, 0.5, "limit", 105.0, filled=True),
            ExitTarget(2, 50, 0.5, "trailing", None, filled=False),
        ]

        state = PartialExitState(
            symbol="AAPL",
            entry_price=100.0,
            total_quantity=100,
            targets=targets,
            stop_price=95.0,
        )

        self.assertEqual(state.get_remaining_quantity(), 50)

    def test_get_filled_targets(self):
        """Test getting filled targets."""
        targets = [
            ExitTarget(1, 50, 0.5, "limit", 105.0, filled=True),
            ExitTarget(2, 50, 0.5, "trailing", None, filled=False),
        ]

        state = PartialExitState(
            symbol="AAPL",
            entry_price=100.0,
            total_quantity=100,
            targets=targets,
            stop_price=95.0,
        )

        filled = state.get_filled_targets()
        self.assertEqual(len(filled), 1)
        self.assertEqual(filled[0].target_number, 1)

    def test_get_active_targets(self):
        """Test getting active (unfilled) targets."""
        targets = [
            ExitTarget(1, 50, 0.5, "limit", 105.0, filled=True),
            ExitTarget(2, 50, 0.5, "trailing", None, filled=False),
        ]

        state = PartialExitState(
            symbol="AAPL",
            entry_price=100.0,
            total_quantity=100,
            targets=targets,
            stop_price=95.0,
        )

        active = state.get_active_targets()
        self.assertEqual(len(active), 1)
        self.assertEqual(active[0].target_number, 2)


class TestPartialExitManager(unittest.TestCase):
    """Test PartialExitManager class."""

    def setUp(self):
        """Set up test fixtures."""
        self.order_manager = Mock()
        self.order_manager.position_manager = Mock()
        self.trailing_stop_manager = Mock()

        self.config = {
            "enabled": True,
            "targets": 2,
            "split": [0.5, 0.5],
            "target_1_pct": 0.04,  # 4% profit
            "target_2": "trailing",
        }

        self.manager = PartialExitManager(
            order_manager=self.order_manager,
            trailing_stop_manager=self.trailing_stop_manager,
            partial_exit_config=self.config,
        )

    def test_initialization(self):
        """Test PartialExitManager initialization."""
        self.assertEqual(self.manager.config["enabled"], True)
        self.assertEqual(self.manager.config["targets"], 2)
        self.assertEqual(len(self.manager.exit_states), 0)

    def test_calculate_targets_50_50_split(self):
        """Test calculating 50/50 split targets."""
        targets = self.manager._calculate_targets("AAPL", 100.0, 100, 95.0)

        self.assertEqual(len(targets), 2)

        # Target 1: Limit order at 4% profit
        self.assertEqual(targets[0].target_number, 1)
        self.assertEqual(targets[0].quantity, 50)
        self.assertEqual(targets[0].exit_type, "limit")
        self.assertEqual(targets[0].exit_price, 104.0)  # 100 * 1.04

        # Target 2: Trailing stop
        self.assertEqual(targets[1].target_number, 2)
        self.assertEqual(targets[1].quantity, 50)
        self.assertEqual(targets[1].exit_type, "trailing")
        self.assertIsNone(targets[1].exit_price)

    def test_calculate_targets_odd_quantity(self):
        """Test calculating targets with odd quantity (rounding)."""
        targets = self.manager._calculate_targets("AAPL", 100.0, 101, 95.0)

        self.assertEqual(len(targets), 2)
        # First target gets 50 (int(101 * 0.5))
        self.assertEqual(targets[0].quantity, 50)
        # Second target gets remaining 51
        self.assertEqual(targets[1].quantity, 51)
        # Total should be 101
        self.assertEqual(targets[0].quantity + targets[1].quantity, 101)

    def test_register_position_disabled(self):
        """Test registration when partial exits are disabled."""
        self.manager.config["enabled"] = False

        result = self.manager.register_position("AAPL", 100.0, 100, 95.0)

        self.assertIsNone(result)
        self.assertEqual(len(self.manager.exit_states), 0)

    def test_register_position_too_small(self):
        """Test registration with position too small (< 2 shares)."""
        result = self.manager.register_position("AAPL", 100.0, 1, 95.0)

        self.assertIsNone(result)
        self.assertEqual(len(self.manager.exit_states), 0)

    @patch.object(PartialExitManager, "_place_target_orders")
    def test_register_position_success(self, mock_place_orders):
        """Test successful position registration."""
        result = self.manager.register_position("AAPL", 100.0, 100, 95.0, "stop-order-123")

        self.assertIsNotNone(result)
        self.assertIsInstance(result, PartialExitState)
        self.assertEqual(result.symbol, "AAPL")
        self.assertEqual(result.total_quantity, 100)
        self.assertEqual(len(result.targets), 2)
        self.assertEqual(result.stop_order_id, "stop-order-123")

        # Verify state was stored
        self.assertIn("AAPL", self.manager.exit_states)

        # Verify orders were placed
        mock_place_orders.assert_called_once()

    def test_place_limit_exit(self):
        """Test placing limit order for profit target."""
        self.order_manager.place_limit_order.return_value = {"id": "limit-order-456"}

        targets = [ExitTarget(1, 50, 0.5, "limit", 104.0)]
        state = PartialExitState(
            symbol="AAPL",
            entry_price=100.0,
            total_quantity=100,
            targets=targets,
            stop_price=95.0,
        )

        self.manager._place_limit_exit(state, targets[0])

        # Verify limit order was placed
        self.order_manager.place_limit_order.assert_called_once_with(
            symbol="AAPL", qty=50, side="sell", limit_price=104.0
        )

        # Verify order ID was stored
        self.assertEqual(targets[0].order_id, "limit-order-456")

    def test_place_limit_exit_error(self):
        """Test handling error when placing limit order."""
        self.order_manager.place_limit_order.return_value = {"error": "API error"}

        targets = [ExitTarget(1, 50, 0.5, "limit", 104.0)]
        state = PartialExitState(
            symbol="AAPL",
            entry_price=100.0,
            total_quantity=100,
            targets=targets,
            stop_price=95.0,
        )

        # Should not raise exception, just log error
        self.manager._place_limit_exit(state, targets[0])

        # Order ID should remain None
        self.assertIsNone(targets[0].order_id)

    def test_register_trailing_target(self):
        """Test registering trailing stop target."""
        targets = [ExitTarget(2, 50, 0.5, "trailing", None)]
        state = PartialExitState(
            symbol="AAPL",
            entry_price=100.0,
            total_quantity=100,
            targets=targets,
            stop_price=95.0,
            stop_order_id="stop-order-123",
        )

        self.manager._register_trailing_target(state, targets[0])

        # Verify trailing stop manager was called
        self.trailing_stop_manager.register_position.assert_called_once_with(
            symbol="AAPL",
            entry_price=100.0,
            initial_stop=95.0,
            quantity=50,
            stop_order_id="stop-order-123",
        )

    def test_register_trailing_target_no_manager(self):
        """Test handling case where TrailingStopManager is not available."""
        manager_no_trailing = PartialExitManager(
            order_manager=self.order_manager,
            trailing_stop_manager=None,  # No trailing stop manager
            partial_exit_config=self.config,
        )

        targets = [ExitTarget(2, 50, 0.5, "trailing", None)]
        state = PartialExitState(
            symbol="AAPL",
            entry_price=100.0,
            total_quantity=100,
            targets=targets,
            stop_price=95.0,
        )

        # Should not raise exception, just log warning
        manager_no_trailing._register_trailing_target(state, targets[0])

    def test_update_position_fills(self):
        """Test checking and updating position fills."""
        targets = [
            ExitTarget(1, 50, 0.5, "limit", 104.0, order_id="limit-order-456"),
            ExitTarget(2, 50, 0.5, "trailing", None),
        ]

        state = PartialExitState(
            symbol="AAPL",
            entry_price=100.0,
            total_quantity=100,
            targets=targets,
            stop_price=95.0,
        )

        self.manager.exit_states["AAPL"] = state

        # Mock order status check
        self.order_manager.position_manager.get_order.return_value = {"status": "filled"}

        # Update fills
        updated = self.manager.update_position_fills("AAPL")

        self.assertTrue(updated)
        self.assertTrue(targets[0].filled)
        self.assertIsNotNone(targets[0].filled_at)

    def test_get_position_summary(self):
        """Test getting position summary."""
        targets = [
            ExitTarget(1, 50, 0.5, "limit", 104.0, filled=True),
            ExitTarget(2, 50, 0.5, "trailing", None, filled=False),
        ]

        state = PartialExitState(
            symbol="AAPL",
            entry_price=100.0,
            total_quantity=100,
            targets=targets,
            stop_price=95.0,
        )

        self.manager.exit_states["AAPL"] = state

        summary = self.manager.get_position_summary("AAPL")

        self.assertIsNotNone(summary)
        self.assertEqual(summary["symbol"], "AAPL")
        self.assertEqual(summary["total_quantity"], 100)
        self.assertEqual(summary["remaining_quantity"], 50)
        self.assertEqual(summary["filled_targets"], 1)
        self.assertEqual(summary["active_targets"], 1)
        self.assertEqual(len(summary["targets"]), 2)

    def test_get_position_summary_not_found(self):
        """Test getting summary for non-existent position."""
        summary = self.manager.get_position_summary("TSLA")
        self.assertIsNone(summary)

    def test_remove_position(self):
        """Test removing position from tracking."""
        state = PartialExitState(
            symbol="AAPL",
            entry_price=100.0,
            total_quantity=100,
            targets=[],
            stop_price=95.0,
        )

        self.manager.exit_states["AAPL"] = state

        # Remove position
        removed = self.manager.remove_position("AAPL")

        self.assertTrue(removed)
        self.assertNotIn("AAPL", self.manager.exit_states)

    def test_remove_position_not_found(self):
        """Test removing non-existent position."""
        removed = self.manager.remove_position("TSLA")
        self.assertFalse(removed)


class TestPartialExitManagerIntegration(unittest.TestCase):
    """Test integration with trading modes."""

    def test_from_mode_manager(self):
        """Test creating PartialExitManager from TradingModeManager."""
        order_manager = Mock()
        trailing_stop_manager = Mock()
        mode_manager = Mock()
        mode_manager.get_parameters.return_value = Mock(mode=Mock(value="moderate"))

        manager = PartialExitManager.from_mode_manager(
            order_manager, trailing_stop_manager, mode_manager
        )

        self.assertIsNotNone(manager)
        self.assertEqual(manager.order_manager, order_manager)
        self.assertEqual(manager.trailing_stop_manager, trailing_stop_manager)


if __name__ == "__main__":
    unittest.main()


class TestSoftwarePlans(unittest.TestCase):
    """register_software_plan (#584): a plan saved with no order and no trailing change."""

    def setUp(self):
        import tempfile
        from pathlib import Path

        self.dir = tempfile.TemporaryDirectory()
        self.order_manager = Mock()
        self.trailing = Mock()
        self.manager = PartialExitManager(
            self.order_manager, self.trailing, state_file=Path(self.dir.name) / "plans.json"
        )

    def tearDown(self):
        self.dir.cleanup()

    def test_registers_half_the_shares_at_the_target_without_any_order(self):
        state = self.manager.register_software_plan("AAPL", 100.0, 7, target_1_pct=0.05)

        first, second = state.targets
        self.assertEqual((first.exit_type, first.quantity), ("software", 3))
        self.assertAlmostEqual(first.exit_price, 105.0)
        self.assertEqual((second.exit_type, second.quantity), ("trailing", 4))
        self.assertIsNone(first.order_id)
        self.assertEqual(self.order_manager.method_calls, [])
        self.assertEqual(self.trailing.method_calls, [])

    def test_the_plan_is_saved_for_other_processes(self):
        self.manager.register_software_plan("AAPL", 100.0, 10)

        other = PartialExitManager(None, state_file=self.manager.state_file)
        self.assertEqual(other.exit_states["AAPL"].targets[0].exit_type, "software")

    def test_rejects_one_share_and_a_bad_entry(self):
        with self.assertRaises(ValueError):
            self.manager.register_software_plan("AAPL", 100.0, 1)
        with self.assertRaises(ValueError):
            self.manager.register_software_plan("AAPL", 0.0, 10)
        self.assertEqual(self.manager.exit_states, {})

    def test_defaults_to_the_configured_percentage(self):
        state = self.manager.register_software_plan("AAPL", 200.0, 4)

        self.assertAlmostEqual(state.targets[0].exit_price, 208.0)

    def test_a_software_target_price_can_be_changed_until_it_is_sold(self):
        self.manager.register_software_plan("AAPL", 100.0, 10)

        changed, message = self.manager.update_target_price("AAPL", 1, 110.0)

        self.assertTrue(changed)
        self.assertIn("evening routine sells it at this price", message)
        self.assertEqual(self.manager.exit_states["AAPL"].targets[0].exit_price, 110.0)
        self.manager.exit_states["AAPL"].targets[0].order_id = "sell-1"
        self.manager.save()
        self.assertFalse(self.manager.update_target_price("AAPL", 1, 120.0)[0])

    def test_refresh_plan_follows_the_saved_file(self):
        self.manager.register_software_plan("AAPL", 100.0, 10)
        other = PartialExitManager(None, state_file=self.manager.state_file)
        other.update_target_price("AAPL", 1, 111.0)

        self.assertEqual(self.manager.refresh_plan("AAPL").targets[0].exit_price, 111.0)
        other.remove_position("AAPL")
        self.assertIsNone(self.manager.refresh_plan("AAPL"))
        self.assertNotIn("AAPL", self.manager.exit_states)

    def test_from_mode_manager_uses_the_modes_settings(self):
        from src.core.trading_modes import TradingMode, TradingModeManager

        modes = TradingModeManager()
        for mode, pct in (
            (TradingMode.CONSERVATIVE, 0.04),
            (TradingMode.MODERATE, 0.05),
            (TradingMode.AGGRESSIVE, 0.06),
        ):
            manager = PartialExitManager.from_mode_manager(None, None, modes, mode)
            self.assertEqual(manager.config["target_1_pct"], pct)
