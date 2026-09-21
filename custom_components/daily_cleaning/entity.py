"""Base entity for Daily Cleaning."""

from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity import Entity

from .const import DOMAIN, VERSION
from .manager import DailyCleaningManager


class DailyCleaningEntity(Entity):
    """Base class for entities backed by the state manager."""

    _attr_should_poll = False
    _attr_has_entity_name = True

    def __init__(self, manager: DailyCleaningManager) -> None:
        """Initialize the entity."""
        self.manager = manager
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, manager.entry_id)},
            manufacturer="isimagan",
            model="Daily Cleaning",
            name="Daily Cleaning",
            sw_version=VERSION,
        )

    async def async_added_to_hass(self) -> None:
        """Subscribe to manager changes."""
        self.async_on_remove(self.manager.async_subscribe(self.async_write_ha_state))
