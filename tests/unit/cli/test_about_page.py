"""
/about reports the market by the calendar the routines use: closed on weekends and holidays,
closed after an early close. The NYSE rules answer here; no calendar request is made.
"""

from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from src.cli.utils import about_page
from src.utils import market_hours

ET = ZoneInfo("America/New_York")


@pytest.fixture
def at(monkeypatch):
    monkeypatch.setattr(market_hours, "_get_alpaca_calendar", lambda: None)
    monkeypatch.setattr(market_hours, "_CALENDAR_CACHE", {})

    def set_time(*args):
        now = datetime(*args, tzinfo=ET)
        monkeypatch.setattr(about_page, "get_datetime_now", lambda tz=None: now)

    return set_time


@pytest.mark.parametrize(
    "when, status, next_event",
    [
        ((2026, 11, 1, 15, 31), "CLOSED", "Opens Mon Nov 02, 09:30 AM ET"),  # Sunday, in hours
        ((2026, 11, 2, 8, 0), "CLOSED", "Opens in 1h 30m"),
        ((2026, 11, 2, 10, 0), "OPEN", "Closes in 6h 0m"),
        ((2026, 11, 2, 16, 0), "CLOSED", "Opens Tue Nov 03, 09:30 AM ET"),
        ((2026, 11, 6, 17, 0), "CLOSED", "Opens Mon Nov 09, 09:30 AM ET"),  # Friday evening
        ((2026, 11, 26, 11, 0), "CLOSED", "Opens Fri Nov 27, 09:30 AM ET"),  # Thanksgiving
        ((2026, 11, 27, 12, 0), "OPEN", "Closes in 1h 0m"),  # early close at 13:00
        ((2026, 11, 27, 13, 30), "CLOSED", "Opens Mon Nov 30, 09:30 AM ET"),
    ],
)
def test_market_status(at, when, status, next_event):
    at(*when)

    _, actual_status, actual_next = about_page._get_nyse_time()

    assert (actual_status, actual_next) == (status, next_event)


def test_the_page_carries_the_project_name(at):
    at(2026, 11, 2, 10, 0)

    page = about_page.show_about(None)

    assert "O P T I O N S C O M M A N D E R" in page
    assert "A G E N T" not in page
