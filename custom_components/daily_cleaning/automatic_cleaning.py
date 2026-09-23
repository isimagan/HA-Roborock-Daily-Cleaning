"""Home Assistant wiring for safe automatic room completion."""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable, Mapping, Sequence
from enum import Enum
from typing import Any

from homeassistant.const import (
    ATTR_DOMAIN,
    ATTR_ENTITY_ID,
    ATTR_SERVICE,
    ATTR_SERVICE_DATA,
    EVENT_CALL_SERVICE,
    STATE_UNAVAILABLE,
    STATE_UNKNOWN,
)
from homeassistant.core import Event, HomeAssistant, callback
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.entity_component import DATA_INSTANCES
from homeassistant.helpers.event import async_track_state_change_event

from .cleaning_session import (
    CleaningObservation,
    CleaningSessionMachine,
    RoomTarget,
)
from .models import RoomConfig

_LOGGER = logging.getLogger(__name__)

_SAMPLE_INTERVAL_SECONDS = 2.0
_ACTIVE_HA_STATES = {"cleaning", "paused", "returning"}


class AutomaticCleaningManager:
    """Observe cached Roborock data and manage one machine per vacuum."""

    def __init__(
        self,
        hass: HomeAssistant,
        rooms: list[RoomConfig],
        mark_cleaned: Callable[[set[str]], Awaitable[None]],
    ) -> None:
        self.hass = hass
        self._configured_rooms = rooms
        self._mark_cleaned = mark_cleaned
        self._rooms_by_vacuum: dict[str, list[RoomConfig]] = {}
        self._area_segments_by_vacuum: dict[str, dict[str, list[str]]] = {}
        self._machines: dict[str, CleaningSessionMachine] = {}
        self._current_room_entities: dict[str, str | None] = {}
        self._vacuum_by_room_entity: dict[str, str] = {}
        self._sampling_tasks: dict[str, asyncio.Task[None]] = {}
        self._completion_tasks: set[asyncio.Task[None]] = set()
        self._unsub_state_changes: Callable[[], None] | None = None
        self._unsub_service_calls: Callable[[], None] | None = None

    async def async_initialize(self) -> None:
        """Resolve entities, synchronize machines, and start listeners."""
        for room in self._configured_rooms:
            entity_id = self._resolve_entity_id(room)
            self._rooms_by_vacuum.setdefault(entity_id, []).append(room)

        for entity_id, rooms in self._rooms_by_vacuum.items():
            self._area_segments_by_vacuum[entity_id] = self._area_mapping(entity_id)
            machine = CleaningSessionMachine(
                [
                    RoomTarget(
                        segment_id=room.segment_id,
                        name=room.name,
                        segment_name=room.segment_name,
                    )
                    for room in rooms
                ]
            )
            self._machines[entity_id] = machine
            room_entity_id = self._find_current_room_entity(rooms)
            self._current_room_entities[entity_id] = room_entity_id
            if room_entity_id is not None:
                self._vacuum_by_room_entity[room_entity_id] = entity_id
            machine.initialize(self._snapshot(entity_id).raw_state)

        if not self._machines:
            return
        self._unsub_state_changes = async_track_state_change_event(
            self.hass,
            [*self._machines, *self._vacuum_by_room_entity],
            self._async_state_changed,
        )
        self._unsub_service_calls = self.hass.bus.async_listen(
            EVENT_CALL_SERVICE, self._async_service_called
        )

    @callback
    def _async_state_changed(self, event: Event[Any]) -> None:
        entity_id = event.data.get("entity_id")
        vacuum_entity_id = self._vacuum_by_room_entity.get(entity_id, entity_id)
        if vacuum_entity_id not in self._machines:
            return
        self._observe(vacuum_entity_id)
        self._ensure_sampler(vacuum_entity_id)

    @callback
    def _async_service_called(self, event: Event[Any]) -> None:
        if event.data.get(ATTR_DOMAIN) != "vacuum":
            return
        service = event.data.get(ATTR_SERVICE)
        service_data = event.data.get(ATTR_SERVICE_DATA)
        if not isinstance(service_data, Mapping):
            return

        for entity_id, machine in self._machines.items():
            if not _targets_entity(service_data.get(ATTR_ENTITY_ID), entity_id):
                continue
            targets: list[str | int] | None = None
            if service == "clean_area":
                targets = self._targets_from_areas(
                    entity_id, service_data.get("cleaning_area_id")
                )
            elif service == "send_command" and _is_segment_command(
                service_data.get("command")
            ):
                targets = _segments_from_command_params(service_data.get("params"))
            else:
                machine.clear_requested_segments()
                continue
            if targets is None or not machine.set_requested_segments(
                targets, monotonic_now=time.monotonic()
            ):
                _LOGGER.debug(
                    "Daily Cleaning ignored uncertain segment targets for %s",
                    entity_id,
                )

    def _observe(self, entity_id: str) -> None:
        try:
            completed = self._machines[entity_id].observe(
                self._snapshot(entity_id), monotonic_now=time.monotonic()
            )
        except Exception:
            # Automatic completion must never interfere with the vacuum or setup.
            _LOGGER.exception(
                "Daily Cleaning automatic observer failed safely for %s", entity_id
            )
            return
        if not completed:
            return
        room_keys = {
            room.key
            for room in self._rooms_by_vacuum[entity_id]
            if room.segment_id in completed
        }
        if not room_keys:
            return
        task = self.hass.async_create_task(
            self._mark_cleaned(room_keys),
            "Daily Cleaning automatic room completion",
        )
        self._completion_tasks.add(task)
        task.add_done_callback(self._completion_done)

    @callback
    def _completion_done(self, task: asyncio.Task[None]) -> None:
        self._completion_tasks.discard(task)
        if task.cancelled():
            return
        if error := task.exception():
            _LOGGER.error("Daily Cleaning could not persist completion: %s", error)

    def _ensure_sampler(self, entity_id: str) -> None:
        task = self._sampling_tasks.get(entity_id)
        if task is not None and not task.done():
            return
        if not self._should_sample(entity_id):
            return
        self._sampling_tasks[entity_id] = asyncio.create_task(
            self._async_sample(entity_id)
        )

    async def _async_sample(self, entity_id: str) -> None:
        try:
            while self._should_sample(entity_id):
                await asyncio.sleep(_SAMPLE_INTERVAL_SECONDS)
                self._observe(entity_id)
        except asyncio.CancelledError:
            raise
        except Exception:
            _LOGGER.exception(
                "Daily Cleaning automatic sampler failed safely for %s", entity_id
            )
        finally:
            current = asyncio.current_task()
            if self._sampling_tasks.get(entity_id) is current:
                self._sampling_tasks.pop(entity_id, None)

    def _should_sample(self, entity_id: str) -> bool:
        if self._machines[entity_id].active:
            return True
        state = self.hass.states.get(entity_id)
        return state is not None and state.state in _ACTIVE_HA_STATES

    def _snapshot(self, entity_id: str) -> CleaningObservation:
        """Read the already loaded entity/coordinator without refreshing it."""
        state = self.hass.states.get(entity_id)
        component = self.hass.data.get(DATA_INSTANCES, {}).get("vacuum")
        entity = component.get_entity(entity_id) if component is not None else None
        sources = _status_sources(entity)
        if state is not None:
            sources.append(state.attributes)

        raw_state = _simple_value(
            _first_value(
                sources,
                ("state",),
                ("work_status",),
                ("device_state",),
                ("status",),
            )
        )
        cleaning_info = _first_value(
            sources,
            ("cleaning_info",),
            ("status_info", "cleaning_info"),
        )
        info_sources = [cleaning_info] if cleaning_info is not None else []
        current_room = _simple_value(
            _first_value(
                sources,
                ("current_room",),
                ("current_area",),
            )
        )
        if room_entity_id := self._current_room_entities.get(entity_id):
            room_state = self.hass.states.get(room_entity_id)
            if room_state is not None and room_state.state not in {
                STATE_UNKNOWN,
                STATE_UNAVAILABLE,
            }:
                current_room = room_state.state

        segment_id = _first_value(
            info_sources,
            ("segment_id",),
            ("current_segment_id",),
        )
        if segment_id is None:
            segment_id = _first_value(sources, ("segment_id",))
        target_segment_id = _first_value(info_sources, ("target_segment_id",))
        if target_segment_id is None:
            target_segment_id = _first_value(sources, ("target_segment_id",))

        return CleaningObservation(
            raw_state=_integer(raw_state),
            clean_area=_number(
                _first_value(
                    sources,
                    ("clean_area",),
                    ("cleaning_area",),
                    ("cleaned_area",),
                )
            ),
            clean_time=_number(
                _first_value(
                    sources,
                    ("clean_time",),
                    ("cleaning_time",),
                    ("duration",),
                )
            ),
            current_room=(current_room if isinstance(current_room, str) else None),
            segment_id=_simple_value(segment_id),
            target_segment_id=_simple_value(target_segment_id),
        )

    def _resolve_entity_id(self, room: RoomConfig) -> str:
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

    def _find_current_room_entity(self, rooms: list[RoomConfig]) -> str | None:
        device_id = next(
            (room.roborock_device_id for room in rooms if room.roborock_device_id),
            None,
        )
        if device_id is None:
            return None
        registry = er.async_get(self.hass)
        for entry in er.async_entries_for_device(
            registry, device_id, include_disabled_entities=False
        ):
            identity = " ".join(
                filter(
                    None,
                    (entry.entity_id, entry.unique_id, entry.name, entry.original_name),
                )
            ).casefold()
            if entry.domain == "sensor" and (
                "current_room" in identity or "current room" in identity
            ):
                return entry.entity_id
        return None

    def _area_mapping(self, entity_id: str) -> dict[str, list[str]]:
        """Read the exact cached mapping used by vacuum.clean_area."""
        entry = er.async_get(self.hass).async_get(entity_id)
        if entry is None:
            return {}
        options = entry.options.get("vacuum", {})
        mapping = options.get("area_mapping")
        if not isinstance(mapping, Mapping):
            return {}
        result: dict[str, list[str]] = {}
        for area_id, segment_ids in mapping.items():
            if not isinstance(area_id, str):
                continue
            values = _string_list(segment_ids)
            if values:
                result[area_id] = values
        return result

    def _targets_from_areas(self, entity_id: str, area_value: Any) -> list[str] | None:
        area_ids = _string_list(area_value)
        if not area_ids:
            return None
        targets: list[str] = []
        area_mapping = self._area_segments_by_vacuum[entity_id]
        for area_id in area_ids:
            matches = area_mapping.get(area_id)
            if not matches:
                return None
            for segment_id in matches:
                if segment_id not in targets:
                    targets.append(segment_id)
        return targets

    async def async_shutdown(self) -> None:
        """Unsubscribe and cancel every sampler/completion task."""
        if self._unsub_state_changes is not None:
            self._unsub_state_changes()
            self._unsub_state_changes = None
        if self._unsub_service_calls is not None:
            self._unsub_service_calls()
            self._unsub_service_calls = None

        tasks = [*self._sampling_tasks.values(), *self._completion_tasks]
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        self._sampling_tasks.clear()
        self._completion_tasks.clear()
        self._machines.clear()
        self._area_segments_by_vacuum.clear()
        self._vacuum_by_room_entity.clear()


def _get_value(source: object, name: str) -> Any:
    try:
        if isinstance(source, Mapping):
            return source.get(name)
        return getattr(source, name, None)
    except Exception:
        return None


def _get_path(source: object, path: Sequence[str]) -> Any:
    value: Any = source
    for name in path:
        if value is None:
            return None
        value = _get_value(value, name)
    return value


def _first_value(sources: Sequence[object], *paths: Sequence[str]) -> Any:
    for source in sources:
        for path in paths:
            value = _get_path(source, path)
            if value is not None and not callable(value):
                return value
    return None


def _status_sources(entity: object | None) -> list[object]:
    if entity is None:
        return []
    coordinator = _get_value(entity, "coordinator")
    coordinator_api = _get_value(coordinator, "api")
    coordinator_api_status = _get_value(coordinator_api, "status")
    properties_api = _get_value(coordinator, "properties_api")
    coordinator_data = _get_value(coordinator, "data")
    candidates = [
        _get_value(entity, "_status_trait"),
        _get_value(properties_api, "status"),
        _get_value(coordinator_api_status, "status"),
        coordinator_api_status,
        _get_value(coordinator_data, "status"),
        _get_value(coordinator_data, "status_info"),
        coordinator_data,
    ]
    return [candidate for candidate in candidates if candidate is not None]


def _simple_value(value: Any) -> str | int | float | bool | None:
    if value is None or isinstance(value, str | int | float | bool):
        return value
    if isinstance(value, Enum):
        return _simple_value(value.value)
    return None


def _integer(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float) and value.is_integer():
        return int(value)
    if isinstance(value, str):
        try:
            return int(value)
        except ValueError:
            return None
    return None


def _number(value: Any) -> float | None:
    simple = _simple_value(value)
    if isinstance(simple, bool) or not isinstance(simple, int | float):
        return None
    return float(simple) if simple >= 0 else None


def _targets_entity(value: Any, entity_id: str) -> bool:
    if isinstance(value, str):
        return value == entity_id
    if isinstance(value, Sequence) and not isinstance(value, str | bytes):
        return entity_id in value
    return False


def _is_segment_command(value: Any) -> bool:
    return isinstance(value, str) and value.casefold() == "app_segment_clean"


def _segments_from_command_params(value: Any) -> list[str | int] | None:
    if isinstance(value, Mapping):
        return _segment_values(value.get("segments"))
    if isinstance(value, Sequence) and not isinstance(value, str | bytes):
        if len(value) == 1 and isinstance(value[0], Mapping):
            return _segment_values(value[0].get("segments"))
        return _segment_values(value)
    return None


def _segment_values(value: Any) -> list[str | int] | None:
    if not isinstance(value, Sequence) or isinstance(value, str | bytes):
        return None
    if not value or any(
        isinstance(item, bool) or not isinstance(item, str | int) for item in value
    ):
        return None
    return list(value)


def _string_list(value: Any) -> list[str] | None:
    if isinstance(value, str):
        return [value]
    if isinstance(value, Sequence) and not isinstance(value, str | bytes):
        if value and all(isinstance(item, str) for item in value):
            return list(value)
    return None
