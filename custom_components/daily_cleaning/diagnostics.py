"""Diagnostics support for Daily Cleaning."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from .const import VERSION

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant

    from . import DailyCleaningConfigEntry
    from .manager import DailyCleaningManager

_ROOM_FIELDS = (
    "name",
    "area_id",
    "segment_id",
    "segment_name",
    "segment_group",
    "vacuum_entity_id",
)

_SENSITIVE_KEY_PARTS = (
    "account",
    "credential",
    "email",
    "map_image",
    "mqtt",
    "password",
    "secret",
    "token",
    "username",
)


def build_diagnostics_payload(manager: DailyCleaningManager) -> dict[str, Any]:
    """Build diagnostics from an explicit, secret-free allowlist."""
    rooms = []
    for room in manager.rooms.values():
        rooms.append({field: getattr(room, field, None) for field in _ROOM_FIELDS})
    payload = {
        "integration": {
            "version": VERSION,
            "configured_rooms": len(rooms),
            "configured_vacuums": len({room["vacuum_entity_id"] for room in rooms}),
        },
        "room_mapping": rooms,
        "diagnostic_recorders": manager.diagnostics.export(),
    }
    return _sanitize(payload)


def _sanitize(value: Any) -> Any:
    """Recursively remove sensitive keys as defense in depth."""
    if isinstance(value, dict):
        return {
            key: _sanitize(item)
            for key, item in value.items()
            if not any(part in str(key).casefold() for part in _SENSITIVE_KEY_PARTS)
        }
    if isinstance(value, list):
        return [_sanitize(item) for item in value]
    return value


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: DailyCleaningConfigEntry
) -> dict[str, Any]:
    """Return sanitized diagnostics for a config entry."""
    return build_diagnostics_payload(entry.runtime_data)
