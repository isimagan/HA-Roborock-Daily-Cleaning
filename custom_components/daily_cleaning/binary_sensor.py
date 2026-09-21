"""Whole-home status sensor for Daily Cleaning."""

from __future__ import annotations

from homeassistant.components.binary_sensor import BinarySensorEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import DailyCleaningConfigEntry
from .const import DOMAIN
from .entity import DailyCleaningEntity
from .manager import DailyCleaningManager


async def async_setup_entry(
    hass: HomeAssistant,
    entry: DailyCleaningConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up the whole-home status sensor."""
    async_add_entities([DailyCleaningBinarySensor(entry.runtime_data)])


class DailyCleaningBinarySensor(DailyCleaningEntity, BinarySensorEntity):
    """Report whether any configured room still needs cleaning."""

    _attr_icon = "mdi:home-alert"
    _attr_name = "Daily cleaning"
    _attr_unique_id = DOMAIN

    def __init__(self, manager: DailyCleaningManager) -> None:
        """Initialize the sensor."""
        super().__init__(manager)

    @property
    def is_on(self) -> bool:
        """Return true if at least one room needs cleaning."""
        return any(state.needs_cleaning for state in self.manager.states.values())

    @property
    def extra_state_attributes(self) -> dict[str, object]:
        """Return a useful whole-home summary."""
        cleaned = [
            room.name
            for key, room in self.manager.rooms.items()
            if not self.manager.states[key].needs_cleaning
        ]
        remaining = [
            room.name
            for key, room in self.manager.rooms.items()
            if self.manager.states[key].needs_cleaning
        ]
        return {
            "rooms_total": len(self.manager.rooms),
            "rooms_cleaned": len(cleaned),
            "rooms_remaining": len(remaining),
            "cleaned_rooms": cleaned,
            "remaining_rooms": remaining,
        }
