"""Tests for the bounded read-only diagnostic recorder."""

import asyncio
from enum import IntEnum
from types import SimpleNamespace

from custom_components.daily_cleaning.diagnostic_recorder import (
    DiagnosticRecorder,
    DiagnosticSampler,
    attach_room_matches,
    extract_cached_snapshot,
)
from custom_components.daily_cleaning.diagnostics import build_diagnostics_payload


class RawState(IntEnum):
    """Representative Roborock raw state."""

    CLEANING = 5


def _snapshot(
    state: str,
    *,
    segment: int | None = None,
    target: int | None = None,
) -> dict:
    return {
        "vacuum_entity_id": "vacuum.bob",
        "ha_state": state,
        "raw_state": {"value": 5, "name": "CLEANING"},
        "in_cleaning": 1,
        "in_returning": 0,
        "cleaning_info": {
            "segment_id": segment,
            "target_segment_id": target,
        },
        "clean_time": 30,
        "clean_area": 123,
        "clean_progress": None,
        "additional_status": {},
        "related_entities": [],
        "daily_cleaning_room": None,
        "daily_cleaning_rooms": [],
    }


def test_recorder_is_bounded() -> None:
    recorder = DiagnosticRecorder(maxlen=3)
    for index in range(5):
        recorder.record(
            _snapshot("cleaning", segment=index),
            force=True,
            monotonic_now=float(index),
        )
    assert len(recorder.export()) == 3
    assert recorder.export()[0]["cleaning_info"]["segment_id"] == 2


def test_missing_roborock_raw_fields_are_supported() -> None:
    snapshot = extract_cached_snapshot(
        vacuum_entity_id="vacuum.basic",
        ha_state="cleaning",
        entity=SimpleNamespace(),
    )
    assert snapshot["raw_state"] == {"value": None, "name": None}
    assert snapshot["in_cleaning"] is None
    assert snapshot["cleaning_info"]["segment_id"] is None


def test_broken_private_property_does_not_escape() -> None:
    class BrokenStatus:
        @property
        def state(self) -> object:
            raise RuntimeError("unsupported on this model")

    snapshot = extract_cached_snapshot(
        vacuum_entity_id="vacuum.basic",
        ha_state="idle",
        entity=SimpleNamespace(_status_trait=BrokenStatus()),
    )
    assert snapshot["raw_state"] == {"value": None, "name": None}


def test_extracts_cached_fields_and_room_match() -> None:
    status = SimpleNamespace(
        state=RawState.CLEANING,
        in_cleaning=1,
        in_returning=0,
        clean_time=42,
        clean_area=1234,
        clean_percent=25,
        cleaning_info=SimpleNamespace(segment_id=16, target_segment_id=17),
    )
    entity = SimpleNamespace(_status_trait=status)
    room = SimpleNamespace(segment_id="7_16", area_id="living_room", name="Living")
    snapshot = extract_cached_snapshot(
        vacuum_entity_id="vacuum.bob", ha_state="cleaning", entity=entity
    )
    attach_room_matches(snapshot, [room])
    assert snapshot["raw_state"] == {"value": 5, "name": "CLEANING"}
    assert snapshot["cleaning_info"] == {
        "segment_id": 16,
        "target_segment_id": 17,
    }
    assert snapshot["daily_cleaning_room"]["matched_area_id"] == "living_room"


def test_extracts_nested_cached_q10_status() -> None:
    status = SimpleNamespace(state=RawState.CLEANING, in_cleaning=1)
    entity = SimpleNamespace(
        coordinator=SimpleNamespace(
            api=SimpleNamespace(status=SimpleNamespace(status=status))
        )
    )
    snapshot = extract_cached_snapshot(
        vacuum_entity_id="vacuum.q10", ha_state="cleaning", entity=entity
    )
    assert snapshot["raw_state"] == {"value": 5, "name": "CLEANING"}
    assert snapshot["in_cleaning"] == 1


def test_segment_and_target_segment_changes() -> None:
    recorder = DiagnosticRecorder()
    recorder.record(_snapshot("cleaning", segment=16, target=17), monotonic_now=1)
    segment_change = recorder.record(
        _snapshot("cleaning", segment=17, target=17), monotonic_now=2
    )
    target_change = recorder.record(
        _snapshot("cleaning", segment=17, target=18), monotonic_now=3
    )
    assert "SEGMENT_CHANGE" in segment_change["events"]
    assert "TARGET_SEGMENT_CHANGE" in target_change["events"]


def test_cleaning_to_returning_and_docked() -> None:
    returning_recorder = DiagnosticRecorder()
    returning_recorder.record(_snapshot("cleaning", segment=16), monotonic_now=1)
    returning = returning_recorder.record(
        _snapshot("returning", segment=16), monotonic_now=2
    )

    docked_recorder = DiagnosticRecorder()
    docked_recorder.record(_snapshot("cleaning", segment=16), monotonic_now=1)
    docked = docked_recorder.record(_snapshot("docked", segment=16), monotonic_now=2)
    assert "RETURNING" in returning["events"]
    assert "DOCKED" in docked["events"]


def test_multiple_vacuums_have_independent_buffers() -> None:
    upstairs = DiagnosticRecorder()
    downstairs = DiagnosticRecorder()
    upstairs.record(_snapshot("cleaning", segment=20), monotonic_now=1)
    downstairs.record(_snapshot("cleaning", segment=10), monotonic_now=1)
    assert upstairs.export()[0]["cleaning_info"]["segment_id"] == 20
    assert downstairs.export()[0]["cleaning_info"]["segment_id"] == 10


def test_diagnostics_output_is_sanitized() -> None:
    room = SimpleNamespace(
        name="Living",
        area_id="living_room",
        segment_id="16",
        segment_name="Living room",
        segment_group=None,
        vacuum_entity_id="vacuum.bob",
        token="must-not-appear",
    )
    diagnostic_export = {
        "vacuum.bob": [
            {
                "ha_state": "cleaning",
                "access_token": "secret",
                "mqtt_password": "secret",
            }
        ]
    }
    manager = SimpleNamespace(
        rooms={"room": room},
        diagnostics=SimpleNamespace(export=lambda: diagnostic_export),
    )
    result = build_diagnostics_payload(manager)
    rendered = repr(result)
    assert "must-not-appear" not in rendered
    assert "secret" not in rendered
    assert result["room_mapping"][0]["segment_id"] == "16"


def test_sampler_stops_without_leaving_task() -> None:
    async def run_test() -> None:
        current = _snapshot("cleaning", segment=16)
        recorder = DiagnosticRecorder()
        sampler = DiagnosticSampler(
            recorder,
            lambda: current,
            interval=0.001,
            post_cleaning=10,
        )
        sampler.observe()
        assert sampler.running
        await asyncio.sleep(0)
        await sampler.async_stop()
        assert not sampler.running

        sampler.observe("SETUP")
        assert sampler.running
        await sampler.async_stop()
        assert not sampler.running

    asyncio.run(run_test())


def test_session_end_after_post_cleaning_tail() -> None:
    async def run_test() -> None:
        state = {"value": "cleaning"}
        sampler = DiagnosticSampler(
            DiagnosticRecorder(),
            lambda: _snapshot(state["value"], segment=16),
            interval=0.001,
            post_cleaning=0,
        )
        sampler.observe()
        state["value"] = "docked"
        await asyncio.sleep(0.01)
        assert not sampler.running
        assert "SESSION_END" in sampler.recorder.export()[-1]["events"]

    asyncio.run(run_test())
