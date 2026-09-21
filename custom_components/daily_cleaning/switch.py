"""Room switches for Daily Cleaning."""

from __future__ import annotations

from typing import Any

from homeassistant.components.switch import SwitchEntity
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import DailyCleaningConfigEntry
from .const import (
    ATTR_AREA_ID,
    ATTR_LAST_CLEANED,
    ATTR_ROBOROCK_CONFIG_ENTRY_ID,
    ATTR_ROBOROCK_DEVICE_ID,
    ATTR_ROBOROCK_ENTITY_UNIQUE_ID,
    ATTR_ROBOROCK_SEGMENT_ID,
    ATTR_ROBOROCK_SEGMENT_NAME,
    ATTR_VACUUM_ENTITY_ID,
)
from .entity import DailyCleaningEntity
from .manager import DailyCleaningManager
from .models import RoomConfig


async def async_setup_entry(
    hass: HomeAssistant,
    entry: DailyCleaningConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up room switches."""
    async_add_entities(
        [
            DailyCleaningRoomSwitch(entry.runtime_data, room)
            for room in entry.runtime_data.rooms.values()
        ]
    )


class DailyCleaningRoomSwitch(DailyCleaningEntity, SwitchEntity):
    """A switch representing whether one room needs cleaning."""

    _attr_icon = "mdi:broom"

    def __init__(self, manager: DailyCleaningManager, room: RoomConfig) -> None:
        """Initialize the room switch."""
        super().__init__(manager)
        self.room = room
        self._attr_name = room.name
        self._attr_unique_id = room.key

    async def async_added_to_hass(self) -> None:
        """Subscribe and assign this entity to its configured HA Area."""
        await super().async_added_to_hass()
        if self.entity_id is None:
            return
        registry = er.async_get(self.hass)
        if (registry_entry := registry.async_get(self.entity_id)) and (
            registry_entry.area_id != self.room.area_id
        ):
            registry.async_update_entity(self.entity_id, area_id=self.room.area_id)

    @property
    def is_on(self) -> bool:
        """Return true when this room still needs cleaning."""
        return self.manager.states[self.room.key].needs_cleaning

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return stable room and Roborock linkage metadata."""
        state = self.manager.states[self.room.key]
        return {
            ATTR_AREA_ID: self.room.area_id,
            ATTR_ROBOROCK_SEGMENT_ID: self.room.segment_id,
            ATTR_ROBOROCK_SEGMENT_NAME: self.room.segment_name,
            ATTR_VACUUM_ENTITY_ID: self.room.vacuum_entity_id,
            ATTR_ROBOROCK_CONFIG_ENTRY_ID: (self.room.roborock_config_entry_id),
            ATTR_ROBOROCK_DEVICE_ID: self.room.roborock_device_id,
            ATTR_ROBOROCK_ENTITY_UNIQUE_ID: self.room.roborock_entity_unique_id,
            ATTR_LAST_CLEANED: (
                state.last_cleaned.isoformat() if state.last_cleaned else None
            ),
        }

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Mark the room as needing cleaning."""
        await self.manager.async_set_room(self.room.key, True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Mark the room as cleaned."""
        await self.manager.async_set_room(self.room.key, False)

    @callback
    def async_registry_entry_updated(self) -> None:
        """Keep the configured area assignment authoritative."""
        super().async_registry_entry_updated()
