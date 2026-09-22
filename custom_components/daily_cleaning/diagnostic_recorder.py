"""Bounded, read-only Roborock diagnostic recording helpers."""

from __future__ import annotations

import asyncio
import logging
import time
from collections import deque
from collections.abc import Callable, Mapping, Sequence
from datetime import datetime
from enum import Enum
from typing import Any

_LOGGER = logging.getLogger(__name__)

MAX_OBSERVATIONS = 1000
SAMPLE_INTERVAL_SECONDS = 2.0
POST_CLEANING_SECONDS = 20.0
HEARTBEAT_SECONDS = 60.0

_STATUS_FIELDS = {
    "in_cleaning": ("in_cleaning",),
    "in_returning": ("in_returning",),
    "clean_time": ("clean_time", "cleaning_time", "duration"),
    "clean_area": ("clean_area", "cleaning_area", "cleaned_area"),
    "clean_progress": ("clean_percent", "clean_progress", "progress"),
}

_EXTRA_FIELDS = (
    "back_type",
    "charge_status",
    "clean_type",
    "current_area",
    "current_area_id",
    "current_cleaning_mode_name",
    "current_room",
    "current_room_id",
    "current_segment_id",
    "finish_reason",
    "in_fresh_state",
    "is_exploring",
    "is_locating",
    "map_status",
    "next_segment_id",
    "repeat",
    "room_id",
    "seq_type",
    "start_type",
    "target_room_id",
    "task_type",
    "work_status",
)


def _get_value(source: object, name: str) -> Any:
    """Read a dict key or attribute without allowing failures to escape."""
    try:
        if isinstance(source, Mapping):
            return source.get(name)
        return getattr(source, name, None)
    except Exception:  # Diagnostics must tolerate model-specific properties.
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


def _json_value(value: Any) -> str | int | float | bool | None:
    """Convert only simple, explicitly selected values to JSON-safe values."""
    if value is None or isinstance(value, str | int | float | bool):
        return value
    if isinstance(value, Enum):
        enum_value = value.value
        if isinstance(enum_value, str | int | float | bool):
            return enum_value
    return None


def _enum_value(value: Any) -> dict[str, str | int | float | bool | None]:
    """Represent raw state with both machine value and readable name."""
    if value is None:
        return {"value": None, "name": None}
    raw = _json_value(value)
    name = _get_value(value, "name")
    if name is None:
        name = _get_value(value, "display_name")
    if name is None and isinstance(value, str | int | float | bool):
        name = str(value)
    elif name is None:
        name = type(value).__name__
    return {"value": raw, "name": _json_value(name)}


def discover_status_sources(entity: object | None) -> list[object]:
    """Find cached status objects without calling methods or APIs."""
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


def extract_cached_snapshot(
    *,
    vacuum_entity_id: str,
    ha_state: str | None,
    entity: object | None,
    ha_attributes: Mapping[str, Any] | None = None,
    related_entities: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Extract allowlisted diagnostic fields from already cached objects."""
    sources = discover_status_sources(entity)
    if ha_attributes:
        sources.append(ha_attributes)

    raw_state = _first_value(
        sources,
        ("state",),
        ("work_status",),
        ("device_state",),
        ("status",),
    )
    cleaning_info = _first_value(
        sources,
        ("cleaning_info",),
        ("status_info", "cleaning_info"),
    )
    info_sources = [cleaning_info] if cleaning_info is not None else []

    observation: dict[str, Any] = {
        "vacuum_entity_id": vacuum_entity_id,
        "ha_state": ha_state,
        "raw_state": _enum_value(raw_state),
    }
    for output_name, aliases in _STATUS_FIELDS.items():
        value = _first_value(
            sources,
            *((alias,) for alias in aliases),
            *(("cleaning_info", alias) for alias in aliases),
        )
        observation[output_name] = _json_value(value)

    segment_id = _first_value(
        info_sources,
        ("segment_id",),
        ("current_segment_id",),
    )
    if segment_id is None:
        segment_id = _first_value(sources, ("segment_id",), ("current_segment_id",))
    target_segment_id = _first_value(info_sources, ("target_segment_id",))
    if target_segment_id is None:
        target_segment_id = _first_value(sources, ("target_segment_id",))
    observation["cleaning_info"] = {
        "segment_id": _json_value(segment_id),
        "target_segment_id": _json_value(target_segment_id),
    }

    observation["additional_status"] = {
        field: _json_value(_first_value(sources, (field,))) for field in _EXTRA_FIELDS
    }
    observation["related_entities"] = related_entities or []
    return observation


def _segment_matches(configured_id: str, observed_id: Any) -> bool:
    if observed_id is None:
        return False
    observed = str(observed_id)
    configured = str(configured_id)
    return configured == observed or configured.rsplit("_", maxsplit=1)[-1] == observed


def attach_room_matches(
    snapshot: dict[str, Any], rooms: Sequence[object]
) -> dict[str, Any]:
    """Attach configured room metadata for matching current/target segments."""
    info = snapshot["cleaning_info"]
    current = info.get("segment_id")
    target = info.get("target_segment_id")
    matches: list[dict[str, Any]] = []
    for room in rooms:
        configured_id = str(_get_value(room, "segment_id"))
        matched_as = None
        if _segment_matches(configured_id, current):
            matched_as = "segment_id"
        elif _segment_matches(configured_id, target):
            matched_as = "target_segment_id"
        if matched_as is None:
            continue
        matches.append(
            {
                "matched_on": matched_as,
                "matched_segment_id": configured_id,
                "matched_area_id": _json_value(_get_value(room, "area_id")),
                "matched_room_name": _json_value(_get_value(room, "name")),
                "vacuum_entity_id": snapshot["vacuum_entity_id"],
            }
        )
    snapshot["daily_cleaning_room"] = matches[0] if matches else None
    snapshot["daily_cleaning_rooms"] = matches
    return snapshot


class DiagnosticRecorder:
    """Keep changed observations in a bounded in-memory buffer."""

    def __init__(self, maxlen: int = MAX_OBSERVATIONS) -> None:
        self.observations: deque[dict[str, Any]] = deque(maxlen=maxlen)
        self._last_signature: object | None = None
        self._last_recorded_monotonic = 0.0

    def record(
        self,
        snapshot: dict[str, Any],
        *,
        requested_event: str | None = None,
        force: bool = False,
        now: datetime | None = None,
        monotonic_now: float | None = None,
        heartbeat_seconds: float = HEARTBEAT_SECONDS,
    ) -> dict[str, Any] | None:
        """Record changed data, explicit events, or a sparse heartbeat."""
        timestamp = now or datetime.now().astimezone()
        monotonic_value = (
            monotonic_now if monotonic_now is not None else time.monotonic()
        )
        signature = _freeze(snapshot)
        previous = self.observations[-1] if self.observations else None
        events = self._derive_events(previous, snapshot, requested_event)
        changed = signature != self._last_signature
        heartbeat_due = (
            self._last_recorded_monotonic == 0
            or monotonic_value - self._last_recorded_monotonic >= heartbeat_seconds
        )
        if not (force or changed or events or heartbeat_due):
            return None
        if not events and not changed:
            events.append("HEARTBEAT")
        elif not events:
            events.append("SAMPLE_CHANGE")

        observation = {
            "timestamp": timestamp.isoformat(),
            **snapshot,
            "event": events[0],
            "events": events,
            "reason": ",".join(events),
        }
        self.observations.append(observation)
        self._last_signature = signature
        self._last_recorded_monotonic = monotonic_value
        _LOGGER.debug("DAILY_CLEANING_DIAG %s", observation)
        return observation

    @staticmethod
    def _derive_events(
        previous: dict[str, Any] | None,
        snapshot: dict[str, Any],
        requested_event: str | None,
    ) -> list[str]:
        events: list[str] = []
        if requested_event and requested_event != "SAMPLE":
            events.append(requested_event)
        previous_state = previous.get("ha_state") if previous else None
        state = snapshot.get("ha_state")
        if previous is not None and state != previous_state:
            _append_unique(events, "STATE_CHANGE")
        if state == "cleaning" and previous_state != "cleaning":
            _append_unique(events, "SESSION_START")
        if state == "returning" and previous_state != "returning":
            _append_unique(events, "RETURNING")
        if state == "docked" and previous_state != "docked":
            _append_unique(events, "DOCKED")

        if previous is not None:
            old_info = previous.get("cleaning_info", {})
            new_info = snapshot.get("cleaning_info", {})
            if old_info.get("segment_id") != new_info.get("segment_id"):
                _append_unique(events, "SEGMENT_CHANGE")
            if old_info.get("target_segment_id") != new_info.get("target_segment_id"):
                _append_unique(events, "TARGET_SEGMENT_CHANGE")
        return events

    def export(self) -> list[dict[str, Any]]:
        """Return an independent JSON-ready copy of the buffer."""
        return [dict(item) for item in self.observations]


class DiagnosticSampler:
    """Sample one vacuum's cached state during and shortly after cleaning."""

    def __init__(
        self,
        recorder: DiagnosticRecorder,
        snapshot_factory: Callable[[], dict[str, Any]],
        *,
        interval: float = SAMPLE_INTERVAL_SECONDS,
        post_cleaning: float = POST_CLEANING_SECONDS,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.recorder = recorder
        self._snapshot_factory = snapshot_factory
        self._interval = interval
        self._post_cleaning = post_cleaning
        self._clock = clock
        self._task: asyncio.Task[None] | None = None
        self._last_cleaning = 0.0

    @property
    def running(self) -> bool:
        return self._task is not None and not self._task.done()

    def observe(self, requested_event: str = "STATE_CHANGE") -> None:
        """Record an immediate observation and start sampling if cleaning."""
        snapshot = self._snapshot_factory()
        now = self._clock()
        self.recorder.record(
            snapshot, requested_event=requested_event, monotonic_now=now
        )
        if snapshot.get("ha_state") == "cleaning":
            self._last_cleaning = now
            if not self.running:
                self._task = asyncio.create_task(self._run())

    async def _run(self) -> None:
        try:
            while True:
                await asyncio.sleep(self._interval)
                snapshot = self._snapshot_factory()
                now = self._clock()
                if snapshot.get("ha_state") == "cleaning":
                    self._last_cleaning = now
                self.recorder.record(
                    snapshot, requested_event="SAMPLE", monotonic_now=now
                )
                if (
                    snapshot.get("ha_state") != "cleaning"
                    and now - self._last_cleaning >= self._post_cleaning
                ):
                    self.recorder.record(
                        snapshot,
                        requested_event="SESSION_END",
                        force=True,
                        monotonic_now=now,
                    )
                    return
        except asyncio.CancelledError:
            raise
        finally:
            self._task = None

    async def async_stop(self) -> None:
        """Cancel and await the sampler task."""
        task = self._task
        if task is None:
            return
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        self._task = None


def _append_unique(values: list[str], value: str) -> None:
    if value not in values:
        values.append(value)


def _freeze(value: Any) -> Any:
    if isinstance(value, Mapping):
        return tuple(sorted((str(key), _freeze(item)) for key, item in value.items()))
    if isinstance(value, list | tuple):
        return tuple(_freeze(item) for item in value)
    return value
