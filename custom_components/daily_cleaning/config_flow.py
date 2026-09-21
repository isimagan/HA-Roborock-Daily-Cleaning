"""Config flow for Daily Cleaning."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

import probatio
from homeassistant.components.vacuum import StateVacuumEntity, VacuumEntityFeature
from homeassistant.config_entries import (
    SOURCE_RECONFIGURE,
    ConfigFlow,
    ConfigFlowResult,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers import area_registry as ar
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import selector
from homeassistant.helpers.entity_component import DATA_INSTANCES

from .const import CONF_ROOMS, DOMAIN
from .models import RoomConfig


@dataclass(slots=True)
class VacuumChoice:
    """A loaded Roborock vacuum available to the flow."""

    entity_id: str
    name: str
    config_entry_id: str
    device_id: str | None
    unique_id: str
    entity: StateVacuumEntity
    area_mapping: dict[str, list[str]]


@dataclass(slots=True)
class RoomChoice:
    """A segment discovered from a selected vacuum."""

    token: str
    vacuum: VacuumChoice
    segment_id: str
    segment_name: str
    segment_group: str | None
    suggested_area_id: str | None


def _normalized(value: str) -> str:
    """Normalize a name for conservative name matching."""
    return re.sub(r"[^a-z0-9]", "", value.casefold())


def _available_vacuums(hass: HomeAssistant) -> list[VacuumChoice]:
    """Return loaded Roborock vacuums that expose cleanable segments."""
    registry = er.async_get(hass)
    component = hass.data.get(DATA_INSTANCES, {}).get("vacuum")
    if component is None:
        return []

    choices: list[VacuumChoice] = []
    for registry_entry in registry.entities.values():
        if (
            registry_entry.domain != "vacuum"
            or registry_entry.platform != "roborock"
            or registry_entry.disabled_by is not None
            or not registry_entry.config_entry_id
        ):
            continue
        entity = component.get_entity(registry_entry.entity_id)
        if not isinstance(entity, StateVacuumEntity) or not (
            entity.supported_features & VacuumEntityFeature.CLEAN_AREA
        ):
            continue
        vacuum_options = registry_entry.options.get("vacuum", {})
        choices.append(
            VacuumChoice(
                entity_id=registry_entry.entity_id,
                name=(
                    entity.name
                    or registry_entry.name
                    or registry_entry.original_name
                    or registry_entry.entity_id
                ),
                config_entry_id=registry_entry.config_entry_id,
                device_id=registry_entry.device_id,
                unique_id=registry_entry.unique_id,
                entity=entity,
                area_mapping=dict(vacuum_options.get("area_mapping", {})),
            )
        )
    return sorted(choices, key=lambda item: item.name.casefold())


class DailyCleaningFlowMixin:
    """Shared steps for initial configuration and options."""

    hass: HomeAssistant
    _vacuum_choices: dict[str, VacuumChoice]
    _room_choices: dict[str, RoomChoice]
    _selected_rooms: list[RoomChoice]
    _mapped_rooms: list[RoomConfig]
    _mapping_index: int

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Select one or more linked Roborock vacuums."""
        vacuums = _available_vacuums(self.hass)
        self._vacuum_choices = {item.entity_id: item for item in vacuums}
        if not vacuums:
            return self.async_abort(reason="no_roborock_vacuums")

        if user_input is not None:
            return await self.async_step_rooms(
                {"vacuum_entity_ids": user_input["vacuum_entity_ids"]}
            )

        options = [
            selector.SelectOptionDict(value=item.entity_id, label=item.name)
            for item in vacuums
        ]
        return self.async_show_form(
            step_id="user",
            data_schema=probatio.Schema(
                {
                    probatio.Required("vacuum_entity_ids"): selector.SelectSelector(
                        selector.SelectSelectorConfig(
                            options=options,
                            multiple=True,
                            mode=selector.SelectSelectorMode.DROPDOWN,
                        )
                    )
                }
            ),
        )

    async def async_step_rooms(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Discover and select rooms from the chosen vacuums."""
        if not hasattr(self, "_room_choices"):
            selected_vacuums = user_input["vacuum_entity_ids"] if user_input else []
            rooms: dict[str, RoomChoice] = {}
            area_registry = ar.async_get(self.hass)
            area_names = {
                _normalized(area.name): area.id
                for area in area_registry.async_list_areas()
            }
            index = 0
            for entity_id in selected_vacuums:
                vacuum = self._vacuum_choices[entity_id]
                reverse_mapping = {
                    segment_id: area_id
                    for area_id, segment_ids in vacuum.area_mapping.items()
                    for segment_id in segment_ids
                }
                for segment in await vacuum.entity.async_get_segments():
                    token = f"room_{index}"
                    index += 1
                    rooms[token] = RoomChoice(
                        token=token,
                        vacuum=vacuum,
                        segment_id=segment.id,
                        segment_name=segment.name,
                        segment_group=segment.group,
                        suggested_area_id=(
                            reverse_mapping.get(segment.id)
                            or area_names.get(_normalized(segment.name))
                        ),
                    )
            self._room_choices = rooms

        if not self._room_choices:
            return self.async_abort(reason="no_segments")

        if user_input is not None and "room_tokens" in user_input:
            selected = user_input["room_tokens"]
            if not selected:
                return self.async_show_form(
                    step_id="rooms",
                    data_schema=self._rooms_schema(),
                    errors={"base": "select_at_least_one_room"},
                )
            self._selected_rooms = [self._room_choices[token] for token in selected]
            self._mapped_rooms = []
            self._mapping_index = 0
            return await self.async_step_area()

        return self.async_show_form(step_id="rooms", data_schema=self._rooms_schema())

    def _rooms_schema(self) -> probatio.Schema:
        options = []
        for room in self._room_choices.values():
            group = f" — {room.segment_group}" if room.segment_group else ""
            options.append(
                selector.SelectOptionDict(
                    value=room.token,
                    label=(f"{room.segment_name}{group} ({room.vacuum.name})"),
                )
            )
        return probatio.Schema(
            {
                probatio.Required(
                    "room_tokens", default=[item["value"] for item in options]
                ): selector.SelectSelector(
                    selector.SelectSelectorConfig(
                        options=options,
                        multiple=True,
                        mode=selector.SelectSelectorMode.DROPDOWN,
                    )
                )
            }
        )

    async def async_step_area(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Confirm the HA Area for one selected segment at a time."""
        room = self._selected_rooms[self._mapping_index]
        if user_input is not None:
            area_id = user_input["area_id"]
            area = ar.async_get(self.hass).async_get_area(area_id)
            self._mapped_rooms.append(
                RoomConfig(
                    key=(
                        f"{room.vacuum.config_entry_id}:"
                        f"{room.vacuum.unique_id}:{room.segment_id}"
                    ),
                    name=area.name if area else room.segment_name,
                    area_id=area_id,
                    segment_id=room.segment_id,
                    segment_name=room.segment_name,
                    segment_group=room.segment_group,
                    vacuum_entity_id=room.vacuum.entity_id,
                    roborock_config_entry_id=room.vacuum.config_entry_id,
                    roborock_device_id=room.vacuum.device_id,
                    roborock_entity_unique_id=room.vacuum.unique_id,
                )
            )
            self._mapping_index += 1
            if self._mapping_index == len(self._selected_rooms):
                return self._async_finish(self._mapped_rooms)
            room = self._selected_rooms[self._mapping_index]

        field = (
            probatio.Required("area_id", default=room.suggested_area_id)
            if room.suggested_area_id
            else probatio.Required("area_id")
        )
        return self.async_show_form(
            step_id="area",
            data_schema=probatio.Schema({field: selector.AreaSelector()}),
            description_placeholders={
                "room": room.segment_name,
                "vacuum": room.vacuum.name,
            },
        )

    def _async_finish(self, rooms: list[RoomConfig]) -> ConfigFlowResult:
        """Finish the concrete flow."""
        raise NotImplementedError


class DailyCleaningConfigFlow(DailyCleaningFlowMixin, ConfigFlow, domain=DOMAIN):
    """Handle initial Daily Cleaning configuration."""

    VERSION = 1

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Ensure there is one whole-home configuration."""
        if self.source != SOURCE_RECONFIGURE:
            await self.async_set_unique_id(DOMAIN)
            self._abort_if_unique_id_configured()
        return await super().async_step_user(user_input)

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Start a full reconfiguration of vacuums, rooms, and areas."""
        return await DailyCleaningFlowMixin.async_step_user(self, user_input)

    def _async_finish(self, rooms: list[RoomConfig]) -> ConfigFlowResult:
        data = {CONF_ROOMS: [room.as_dict() for room in rooms]}
        if self.source == SOURCE_RECONFIGURE:
            return self.async_update_reload_and_abort(
                self._get_reconfigure_entry(), data_updates=data
            )
        return self.async_create_entry(
            title="Daily Cleaning",
            data=data,
        )
