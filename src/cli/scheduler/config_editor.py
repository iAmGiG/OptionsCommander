"""
Scheduler config editor - interactive configuration editing.

Provides validation and persistence for scheduler configuration.
Extracted from scheduler_cli.py (Issue #440).
"""

import json
import logging
import os
from datetime import time as dt_time
from typing import TYPE_CHECKING, Any, Dict, Optional

import yaml

from .message_loader import get_emoji
from src.trading.scheduling.daily_scheduler import (
    effective_flags,
    load_scheduler_config,
    save_flags,
)
from src.trading.scheduling.scheduler_state import get_scheduler_state_manager

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)


class SchedulerConfigEditor:
    """
    Interactive scheduler configuration editor.

    Handles:
    - Loading config from YAML/JSON
    - Interactive editing with validation
    - Saving config changes
    - Default configuration generation
    """

    # Default configuration values
    DEFAULT_CONFIG = {
        "enabled": True,
        "market_timezone": "America/New_York",
        "morning_routine_time": "09:20:00",
        "evening_routine_time": "15:50:00",
        "max_retries": 3,
        "retry_delay_seconds": 60,
        "enable_notifications": False,
        "dry_run": False,
    }

    def __init__(self, config_file: str = None):
        """
        Initialize config editor.

        Args:
            config_file: Path to config file. Auto-detects YAML/JSON if not provided.
        """
        if config_file:
            self.config_file = config_file
        else:
            # Check for YAML first, fallback to JSON
            yaml_path = "config_defaults/scheduler_config.yaml"
            json_path = "config_defaults/scheduler_config.json"
            self.config_file = yaml_path if os.path.exists(yaml_path) else json_path

    def load(self) -> Dict[str, Any]:
        """
        Load configuration from file.

        Returns:
            Configuration dictionary
        """
        if not os.path.exists(self.config_file):
            return self.DEFAULT_CONFIG.copy()

        try:
            with open(self.config_file, "r", encoding="utf-8") as f:
                if self.config_file.endswith((".yaml", ".yml")):
                    return yaml.safe_load(f) or self.DEFAULT_CONFIG.copy()
                else:
                    return json.load(f)
        except Exception as e:
            logger.warning(f"Error loading config: {e}")
            return self.DEFAULT_CONFIG.copy()

    def save(self, config: Dict[str, Any]) -> bool:
        """
        Save configuration to file.

        Args:
            config: Configuration dictionary to save

        Returns:
            True if saved successfully
        """
        try:
            os.makedirs(os.path.dirname(self.config_file), exist_ok=True)

            with open(self.config_file, "w", encoding="utf-8") as f:
                if self.config_file.endswith((".yaml", ".yml")):
                    yaml.safe_dump(config, f, default_flow_style=False, sort_keys=False)
                else:
                    json.dump(config, f, indent=2)
            return True
        except Exception as e:
            logger.error(f"Error saving config: {e}")
            return False

    def validate_time(self, time_str: str) -> Optional[str]:
        """
        Validate time string format.

        Args:
            time_str: Time string to validate (HH:MM:SS or HH:MM)

        Returns:
            Normalized time string (HH:MM:SS) or None if invalid
        """
        try:
            # Handle various formats
            if len(time_str.split(":")) == 2:
                time_str = f"{time_str}:00"

            dt_time.fromisoformat(time_str)
            return time_str
        except ValueError:
            return None

    def validate_retries(self, value: str) -> Optional[int]:
        """
        Validate retry count.

        Args:
            value: String value to validate

        Returns:
            Integer retries (1-10) or None if invalid
        """
        try:
            retries = int(value)
            if 1 <= retries <= 10:
                return retries
        except ValueError:
            pass
        return None

    async def edit_interactive(self, scheduler=None) -> Optional[Dict[str, Any]]:  # noqa: C901
        """
        Run interactive configuration editor.

        Args:
            scheduler: Optional scheduler instance to reload after save

        Returns:
            Updated configuration or None if cancelled
        """
        print(f"\n{get_emoji('pencil', '📝')} Scheduler Configuration Editor")
        print("=" * 70)

        config = self.load()
        state_manager = get_scheduler_state_manager()
        enabled_choice = None  # saved to state/user.db only if changed here

        print("\nCurrent settings:")
        print(f"  1. Enabled: {effective_flags(config, state_manager)['enabled']}")
        print(f"  2. Morning routine: {config.get('morning_routine_time', '09:20:00')}")
        print(f"  3. Evening routine: {config.get('evening_routine_time', '15:50:00')}")
        print(f"  4. Max retries: {config.get('max_retries', 3)}")
        print(f"  5. Dry run mode: {config.get('dry_run', False)}")
        print("  0. Save and exit")
        print("  q. Cancel")

        while True:
            choice = input("\nEdit setting (1-5, 0 to save, q to cancel): ").strip()

            if choice == "q":
                print("Cancelled")
                return None
            elif choice == "0":
                if self.save(config):
                    if enabled_choice is not None:
                        save_flags(config, state_manager, enabled=enabled_choice)
                    print(f"{get_emoji('check_green', '✅')} Configuration saved!")

                    if scheduler:
                        print(f"{get_emoji('refresh', '🔄')} Reloading scheduler...")
                        try:
                            # In place: the CLI, its monitor and wizard keep this object,
                            # so a new DailyScheduler here would be discarded and 'test
                            # morning' would run with the old dry_run (E6-01)
                            scheduler.config = load_scheduler_config(self.config_file)
                            scheduler.tasks = scheduler._create_default_tasks()
                            print(f"{get_emoji('check_green', '✅')} Scheduler reloaded")
                        except Exception as e:
                            print(f"{get_emoji('warning', '⚠️')} Could not reload: {e}")
                else:
                    print(f"{get_emoji('cross_red', '❌')} Failed to save configuration")

                return config

            elif choice == "1":
                value = input("Enable scheduler? (yes/no): ").strip().lower()
                enabled_choice = value in ["yes", "y", "true"]
                config["enabled"] = enabled_choice

            elif choice == "2":
                value = input("Morning routine time (HH:MM:SS): ").strip()
                validated = self.validate_time(value)
                if validated:
                    config["morning_routine_time"] = validated
                else:
                    print(f"{get_emoji('cross_red', '❌')} Invalid time format. Use HH:MM:SS")

            elif choice == "3":
                value = input("Evening routine time (HH:MM:SS): ").strip()
                validated = self.validate_time(value)
                if validated:
                    config["evening_routine_time"] = validated
                else:
                    print(f"{get_emoji('cross_red', '❌')} Invalid time format. Use HH:MM:SS")

            elif choice == "4":
                value = input("Max retries (1-10): ").strip()
                validated = self.validate_retries(value)
                if validated:
                    config["max_retries"] = validated
                else:
                    print(f"{get_emoji('cross_red', '❌')} Value must be between 1 and 10")

            elif choice == "5":
                value = input("Enable dry run mode? (yes/no): ").strip().lower()
                config["dry_run"] = value in ["yes", "y", "true"]

            else:
                print("Invalid choice")

    def set_enabled(self, enabled: bool, scheduler=None) -> bool:
        """
        Quick enable/disable scheduler.

        Args:
            enabled: True to enable, False to disable
            scheduler: Optional scheduler instance to update

        Returns:
            True if changed successfully
        """
        config = self.load()
        config["enabled"] = enabled

        if self.save(config) and save_flags(config, get_scheduler_state_manager(), enabled=enabled):
            status = "enabled" if enabled else "disabled"
            emoji = get_emoji("check_green", "✅") if enabled else get_emoji("cross_red", "❌")
            print(f"\n{emoji} Scheduler {status}")

            if scheduler:
                scheduler.config["enabled"] = enabled  # the saved flag above is what counts

            return True

        print(f"{get_emoji('cross_red', '❌')} Failed to update config")
        return False
