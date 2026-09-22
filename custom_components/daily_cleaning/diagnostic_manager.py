"""Home Assistant wiring for the read-only diagnostic recorder."""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.entity_component import DATA_INSTANCES
from homeassistant.helpers.event import async_track_state_change_event

from .diagnostic_recorder import (
    DiagnosticRecorder,
    DiagnosticSampler,
    attach_room_matches,
    extract_cached_snapshot,
)
from .models import RoomConfig

_LOGGER = logging.getLogger(__name__)

_RELATED_ATTRIBUTE_KEYS = frozenset(
    {"area_id", "room_id", "room_name", "segment_id", "target_segment_id"}
)


class DailyCleaningDiagnosticManager:
    """Manage an independent bounded recorder for every configured vacuum."""

    def __init__(self, hass: HomeAssistant, rooms: list[RoomConfig]) -> None:
        self.hass = hass
        self._configured_rooms = rooms
        self.recorders: dict[str, DiagnosticRecorder] = {}
        self.samplers: dict[str, DiagnosticSampler] = {}
        self._rooms_by_vacuum: dict[str, list[RoomConfig]] = {}
        self._device_ids: dict[str, str | None] = {}
        self._unsub_state_changes: Any = None

    async def async_initialize(self) -> None:
        """Resolve current entity IDs and subscribe without refreshing data."""
        for room in self._configured_rooms:
            entity_id = self._resolve_entity_id(room)
            self._rooms_by_vacuum.setdefault(entity_id, []).append(room)
            self._device_ids[entity_id] = room.roborock_device_id

        for entity_id in self._rooms_by_vacuum:
            recorder = self.recorders[entity_id] = DiagnosticRecorder()
            sampler = self.samplers[entity_id] = DiagnosticSampler(
                recorder,
                lambda entity_id=entity_id: self._snapshot(entity_id),
            )
            sampler.observe("SETUP")

        if self.samplers:
            self._unsub_state_changes = async_track_state_change_event(
                self.hass,
                list(self.samplers),
                self._async_state_changed,
            )

    @callback
    def _async_state_changed(self, event: Any) -> None:
        entity_id = event.data.get("entity_id")
        if sampler := self.samplers.get(entity_id):
            try:
                sampler.observe("STATE_CHANGE")
            except Exception:
                _LOGGER.exception("DAILY_CLEANING_DIAG failed to observe %s", entity_id)

    def _resolve_entity_id(self, room: RoomConfig) -> str:
        """Resolve renames using stable Roborock config-entry and unique IDs."""
        registry = er.async_get(self.hass)
        for entry in er.async_entries_for_config_entry(
            registry, room.roborock_config_entry_id
        ):
            if (
                entry.domain == "vacuum"
                and entry.platform == "roborock"
                and entry.unique_id == room.roborock_entity_unique_id
            ):
                return entry.entity_id
        return room.vacuum_entity_id

    def _snapshot(self, entity_id: str) -> dict[str, Any]:
        """Read only cached Home Assistant and Roborock entity data."""
        state = self.hass.states.get(entity_id)
        component = self.hass.data.get(DATA_INSTANCES, {}).get("vacuum")
        entity = component.get_entity(entity_id) if component is not None else None
        snapshot = extract_cached_snapshot(
            vacuum_entity_id=entity_id,
            ha_state=state.state if state else None,
            entity=entity,
            ha_attributes=state.attributes if state else None,
            related_entities=self._related_room_entities(entity_id),
        )
        return attach_room_matches(snapshot, self._rooms_by_vacuum[entity_id])

    def _related_room_entities(self, vacuum_entity_id: str) -> list[dict[str, Any]]:
        """Return allowlisted cached room data from entities on the same device."""
        device_id = self._device_ids.get(vacuum_entity_id)
        if not device_id:
            return []
        registry = er.async_get(self.hass)
        related: list[dict[str, Any]] = []
        for entry in er.async_entries_for_device(
            registry, device_id, include_disabled_entities=False
        ):
            searchable = " ".join(
                filter(
                    None,
                    (
                        entry.entity_id,
                        entry.name,
                        entry.original_name,
                        entry.unique_id,
                    ),
                )
            ).casefold()
            if "room" not in searchable and "area" not in searchable:
                continue
            if (state := self.hass.states.get(entry.entity_id)) is None:
                continue
            attributes = {
                key: value
                for key, value in state.attributes.items()
                if key in _RELATED_ATTRIBUTE_KEYS
                and isinstance(value, str | int | float | bool | None)
            }
            related.append(
                {
                    "entity_id": entry.entity_id,
                    "state": state.state,
                    "attributes": attributes,
                }
            )
        return related

    def export(self) -> dict[str, list[dict[str, Any]]]:
        """Export all per-vacuum recorder buffers."""
        return {
            entity_id: recorder.export()
            for entity_id, recorder in self.recorders.items()
        }

    async def async_shutdown(self) -> None:
        """Unsubscribe and await every running sampler."""
        if self._unsub_state_changes is not None:
            self._unsub_state_changes()
            self._unsub_state_changes = None
        results = await asyncio.gather(
            *(sampler.async_stop() for sampler in self.samplers.values()),
            return_exceptions=True,
        )
        for result in results:
            if isinstance(result, BaseException):
                _LOGGER.error("DAILY_CLEANING_DIAG sampler cleanup failed: %s", result)
        self.samplers.clear()
