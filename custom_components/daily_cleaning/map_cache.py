"""Read segment targets from Roborock's existing map cache without refreshing."""

from collections.abc import Mapping, Sequence
from enum import Enum
from typing import Any


def cached_map_segments(entity: object | None) -> tuple[list[int] | None, int | None]:
    """Return cached job targets and actual room ID, if safely available."""
    coordinator = _value(entity, "coordinator")
    properties = _value(coordinator, "properties_api")
    home = _value(properties, "home")
    map_data = _value(_value(home, "_map_content"), "map_data")
    if map_data is None:
        maps = _value(home, "home_map_content")
        current_map = _value(_value(properties, "status"), "current_map")
        if isinstance(current_map, Enum):
            current_map = current_map.value
        # Never pick an arbitrary map (or a sole map with a mismatching flag).
        if isinstance(maps, Mapping) and isinstance(current_map, str | int):
            map_data = _value(maps.get(current_map), "map_data")

    blocks = _value(map_data, "blocks")
    segments = None
    if (
        isinstance(blocks, Sequence)
        and not isinstance(blocks, str)
        and all(isinstance(item, int) and not isinstance(item, bool) for item in blocks)
    ):
        segments = list(blocks)
    room = _value(map_data, "vacuum_room")
    if not isinstance(room, int) or isinstance(room, bool):
        room = None
    return segments, room


def _value(source: object, name: str) -> Any:
    try:
        value = (
            source.get(name)
            if isinstance(source, Mapping)
            else getattr(source, name, None)
        )
        return None if callable(value) else value
    except Exception:
        # An unavailable/changing cache must only disable automatic attribution.
        return None
