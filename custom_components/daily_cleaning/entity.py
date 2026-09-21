"""Base entity for Daily Cleaning."""

from homeassistant.helpers.entity import Entity

from .manager import DailyCleaningManager


class DailyCleaningEntity(Entity):
    """Base class for entities backed by the state manager."""

    _attr_should_poll = False

    def __init__(self, manager: DailyCleaningManager) -> None:
        """Initialize the entity."""
        self.manager = manager

    async def async_added_to_hass(self) -> None:
        """Subscribe to manager changes."""
        self.async_on_remove(self.manager.async_subscribe(self.async_write_ha_state))
