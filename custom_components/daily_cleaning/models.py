"""Data models for Daily Cleaning."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import date, datetime, timedelta
from typing import Any

from .const import RESET_TIME


def cleaning_day(now: datetime) -> date:
    """Return the cleaning-day date for a local datetime."""
    if now.timetz().replace(tzinfo=None) < RESET_TIME:
        return (now - timedelta(days=1)).date()
    return now.date()


@dataclass(frozen=True, slots=True)
class RoomConfig:
    """A configured room linked to a Roborock segment and HA area."""

    key: str
    name: str
    area_id: str
    segment_id: str
    segment_name: str
    segment_group: str | None
    vacuum_entity_id: str
    roborock_config_entry_id: str
    roborock_device_id: str | None
    roborock_entity_unique_id: str

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> RoomConfig:
        """Create a room config from config-entry data."""
        return cls(**data)

    def as_dict(self) -> dict[str, Any]:
        """Return a serializable representation."""
        return asdict(self)


@dataclass(slots=True)
class RoomState:
    """Persistent mutable state for a configured room."""

    needs_cleaning: bool = True
    last_cleaned: datetime | None = None

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> RoomState:
        """Create room state from storage data."""
        last_cleaned = data.get("last_cleaned")
        return cls(
            needs_cleaning=bool(data.get("needs_cleaning", True)),
            last_cleaned=(
                datetime.fromisoformat(last_cleaned) if last_cleaned else None
            ),
        )

    def as_dict(self) -> dict[str, Any]:
        """Return a serializable representation."""
        return {
            "needs_cleaning": self.needs_cleaning,
            "last_cleaned": (
                self.last_cleaned.isoformat() if self.last_cleaned else None
            ),
        }
