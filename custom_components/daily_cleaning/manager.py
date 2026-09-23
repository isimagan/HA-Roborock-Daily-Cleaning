"""State manager for Daily Cleaning."""

from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import date, datetime

from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.event import async_track_time_change
from homeassistant.helpers.storage import Store
from homeassistant.util import dt as dt_util

from .automatic_cleaning import AutomaticCleaningManager
from .const import RESET_TIME, STORAGE_KEY, STORAGE_VERSION
from .diagnostic_manager import DailyCleaningDiagnosticManager
from .models import RoomConfig, RoomState, cleaning_day

_LOGGER = logging.getLogger(__name__)


class DailyCleaningManager:
    """Own persistent state and coordinate all Daily Cleaning entities."""

    def __init__(
        self, hass: HomeAssistant, entry_id: str, rooms: list[RoomConfig]
    ) -> None:
        """Initialize the manager."""
        self.hass = hass
        self.entry_id = entry_id
        self.rooms = {room.key: room for room in rooms}
        self.states: dict[str, RoomState] = {}
        self._store: Store[dict] = Store(hass, STORAGE_VERSION, STORAGE_KEY)
        self._listeners: set[Callable[[], None]] = set()
        self._cleaning_day: date | None = None
        self._unsub_reset: Callable[[], None] | None = None
        self.automatic = AutomaticCleaningManager(
            hass, rooms, self.async_mark_rooms_cleaned
        )
        self.diagnostics = DailyCleaningDiagnosticManager(hass, rooms)

    async def async_initialize(self) -> None:
        """Load persistent state and schedule the daily reset."""
        stored = await self._store.async_load() or {}
        stored_states = stored.get("rooms", {})
        self.states = {
            key: RoomState.from_dict(stored_states.get(key, {})) for key in self.rooms
        }

        stored_day = stored.get("cleaning_day")
        self._cleaning_day = date.fromisoformat(stored_day) if stored_day else None
        current_day = cleaning_day(dt_util.now())
        if self._cleaning_day != current_day:
            await self.async_reset(current_day)
        else:
            await self._async_save()

        self._unsub_reset = async_track_time_change(
            self.hass,
            self._async_scheduled_reset,
            hour=RESET_TIME.hour,
            minute=RESET_TIME.minute,
            second=0,
        )
        try:
            await self.automatic.async_initialize()
        except Exception:  # Automatic detection must never prevent setup.
            _LOGGER.exception("Daily Cleaning automatic detection failed to initialize")
            try:
                await self.automatic.async_shutdown()
            except Exception:
                _LOGGER.exception("Daily Cleaning automatic detection cleanup failed")
        try:
            await self.diagnostics.async_initialize()
        except Exception:  # Diagnostics must never prevent integration setup.
            _LOGGER.exception("DAILY_CLEANING_DIAG failed to initialize")
            try:
                await self.diagnostics.async_shutdown()
            except Exception:
                _LOGGER.exception("DAILY_CLEANING_DIAG failed to clean up")

    async def async_shutdown(self) -> None:
        """Cancel scheduled work when the config entry unloads."""
        if self._unsub_reset is not None:
            self._unsub_reset()
            self._unsub_reset = None
        self._listeners.clear()
        try:
            await self.automatic.async_shutdown()
        except Exception:
            _LOGGER.exception("Daily Cleaning automatic detection failed to stop")
        try:
            await self.diagnostics.async_shutdown()
        except Exception:
            _LOGGER.exception("DAILY_CLEANING_DIAG failed to stop cleanly")

    @callback
    def async_subscribe(self, listener: Callable[[], None]) -> Callable[[], None]:
        """Subscribe to state changes."""
        self._listeners.add(listener)

        @callback
        def unsubscribe() -> None:
            self._listeners.discard(listener)

        return unsubscribe

    async def async_set_room(self, room_key: str, needs_cleaning: bool) -> None:
        """Set whether a room still needs cleaning."""
        state = self.states[room_key]
        if state.needs_cleaning == needs_cleaning:
            return
        state.needs_cleaning = needs_cleaning
        if not needs_cleaning:
            state.last_cleaned = dt_util.now()
        await self._async_save()
        self._notify()

    async def async_mark_rooms_cleaned(self, room_keys: set[str]) -> None:
        """Atomically mark safely completed rooms from one docked session."""
        changed = False
        cleaned_at = dt_util.now()
        for room_key in room_keys:
            state = self.states.get(room_key)
            if state is None or not state.needs_cleaning:
                continue
            state.needs_cleaning = False
            state.last_cleaned = cleaned_at
            changed = True
        if not changed:
            return
        await self._async_save()
        self._notify()

    async def async_reset(self, day: date | None = None) -> None:
        """Start a new cleaning day."""
        self._cleaning_day = day or cleaning_day(dt_util.now())
        for state in self.states.values():
            state.needs_cleaning = True
        await self._async_save()
        self._notify()

    async def _async_scheduled_reset(self, now: datetime) -> None:
        """Handle the 03:00 local-time reset."""
        day = cleaning_day(dt_util.as_local(now))
        if day == self._cleaning_day:
            return
        _LOGGER.debug("Starting cleaning day %s", day)
        await self.async_reset(day)

    async def _async_save(self) -> None:
        await self._store.async_save(
            {
                "cleaning_day": (
                    self._cleaning_day.isoformat() if self._cleaning_day else None
                ),
                "rooms": {key: state.as_dict() for key, state in self.states.items()},
            }
        )

    @callback
    def _notify(self) -> None:
        for listener in tuple(self._listeners):
            listener()
