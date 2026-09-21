"""Tests for Daily Cleaning's framework-independent models."""

from datetime import datetime
from zoneinfo import ZoneInfo

from custom_components.daily_cleaning.models import RoomState, cleaning_day


def test_cleaning_day_changes_at_three_local_time() -> None:
    """A new cleaning day starts at exactly 03:00 local time."""
    timezone = ZoneInfo("Europe/Oslo")

    assert cleaning_day(datetime(2026, 9, 21, 2, 59, tzinfo=timezone)).isoformat() == (
        "2026-09-20"
    )
    assert cleaning_day(datetime(2026, 9, 21, 3, 0, tzinfo=timezone)).isoformat() == (
        "2026-09-21"
    )


def test_room_state_round_trip() -> None:
    """Persistent room state retains its status and timestamp."""
    original = RoomState(
        needs_cleaning=False,
        last_cleaned=datetime.fromisoformat("2026-09-21T07:42:16+02:00"),
    )

    restored = RoomState.from_dict(original.as_dict())

    assert restored == original
