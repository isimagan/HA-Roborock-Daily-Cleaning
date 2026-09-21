"""The Daily Cleaning integration."""

from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from .const import CONF_ROOMS, PLATFORMS
from .manager import DailyCleaningManager
from .models import RoomConfig

type DailyCleaningConfigEntry = ConfigEntry[DailyCleaningManager]


async def async_setup_entry(
    hass: HomeAssistant, entry: DailyCleaningConfigEntry
) -> bool:
    """Set up Daily Cleaning from a config entry."""
    rooms = [RoomConfig.from_dict(room) for room in entry.data[CONF_ROOMS]]
    manager = DailyCleaningManager(hass, entry.entry_id, rooms)
    await manager.async_initialize()
    entry.runtime_data = manager
    entry.async_on_unload(manager.async_shutdown)
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(
    hass: HomeAssistant, entry: DailyCleaningConfigEntry
) -> bool:
    """Unload a config entry."""
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
