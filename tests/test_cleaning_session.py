"""Tests for safe automatic room completion."""

import json
from pathlib import Path

import pytest

from custom_components.daily_cleaning.cleaning_session import (
    CleaningObservation,
    CleaningSessionMachine,
    RoomTarget,
    SessionPhase,
)

FIXTURES = json.loads(
    (Path(__file__).parent / "fixtures" / "cleaning_sequences.json").read_text()
)

ROOMS = [
    RoomTarget("0_16", "Living", "Living"),
    RoomTarget("0_17", "Hall", "Hall"),
    RoomTarget("0_18", "Bedroom", "Bedroom"),
    RoomTarget("0_19", "Kitchen", "Kitchen"),
    RoomTarget("0_20", "Bathroom", "Bathroom"),
]


def _run_fixture(name: str) -> frozenset[str]:
    fixture = FIXTURES[name]
    machine = CleaningSessionMachine(ROOMS)
    machine.initialize(8)
    if fixture["targets"] is not None:
        assert machine.set_requested_segments(fixture["targets"], monotonic_now=0)

    completed: set[str] = set()
    for index, values in enumerate(fixture["observations"], start=1):
        raw_state, clean_area, clean_time, current_room = values
        completed.update(
            machine.observe(
                CleaningObservation(
                    raw_state=raw_state,
                    clean_area=clean_area,
                    clean_time=clean_time,
                    current_room=current_room,
                ),
                monotonic_now=float(index),
            )
        )
    return frozenset(completed)


@pytest.mark.parametrize("name", FIXTURES)
def test_validated_cleaning_sequences(name: str) -> None:
    """Minimal anonymized fixtures reproduce every validated scenario."""
    assert _run_fixture(name) == frozenset(FIXTURES[name]["completed"])


def test_app_started_segment_job_uses_current_room_without_exact_id() -> None:
    machine = CleaningSessionMachine(ROOMS)
    machine.initialize(8)

    observations = [
        CleaningObservation(18, 0, 0, "Bathroom"),
        CleaningObservation(18, 1000000, 60, "Bathroom"),
        CleaningObservation(6, 1000000, 70, "Bathroom"),
        CleaningObservation(8, 1000000, 70, "Living"),
    ]
    completed = set().union(
        *(
            machine.observe(observation, monotonic_now=index)
            for index, observation in enumerate(observations, start=1)
        )
    )
    assert completed == {"0_20"}


def test_app_started_segment_job_attributes_first_cleaning_delta_to_new_room() -> None:
    """Do not credit dock/transit room when app cleaning moves into its target."""
    machine = CleaningSessionMachine(ROOMS)
    machine.initialize(8)

    observations = [
        # The robot starts at its dock in Living, but no area has been cleaned.
        CleaningObservation(18, 0, 0, "Living"),
        CleaningObservation(18, 0, 10, "Living"),
        # The first positive cleaning-area delta arrives with Kitchen as the
        # current room. It must belong to Kitchen, not the previous Living room.
        CleaningObservation(18, 1000000, 60, "Kitchen"),
        CleaningObservation(6, 1000000, 70, "Kitchen"),
        # Returning to/docking in Living must not make Living a completed room.
        CleaningObservation(8, 1000000, 70, "Living"),
    ]
    completed = set().union(
        *(
            machine.observe(observation, monotonic_now=index)
            for index, observation in enumerate(observations, start=1)
        )
    )
    assert completed == {"0_19"}


def test_app_started_segment_job_ignores_unmapped_current_room() -> None:
    machine = CleaningSessionMachine(ROOMS)
    machine.initialize(8)

    observations = [
        CleaningObservation(18, 0, 0, "Hallway Between Rooms"),
        CleaningObservation(18, 1000000, 60, "Hallway Between Rooms"),
        CleaningObservation(6, 1000000, 70, "Hallway Between Rooms"),
        CleaningObservation(8, 1000000, 70, "Living"),
    ]
    completed = set().union(
        *(
            machine.observe(observation, monotonic_now=index)
            for index, observation in enumerate(observations, start=1)
        )
    )
    assert not completed


def test_app_started_segment_job_accepts_exact_segment_id() -> None:
    machine = CleaningSessionMachine(ROOMS)
    machine.initialize(8)
    exact_observations = [
        CleaningObservation(18, 0, 0, "Living", segment_id=20),
        CleaningObservation(18, 1000000, 60, "Living", segment_id=20),
        CleaningObservation(6, 1000000, 70, "Living", segment_id=20),
        CleaningObservation(8, 1000000, 70, "Living", segment_id=20),
    ]
    completed = set().union(
        *(
            machine.observe(observation, monotonic_now=index)
            for index, observation in enumerate(exact_observations, start=1)
        )
    )
    assert completed == {"0_20"}


def test_pause_then_resume_cleaning_is_not_an_abort() -> None:
    machine = CleaningSessionMachine(ROOMS)
    machine.initialize(8)
    assert machine.set_requested_segments([20], monotonic_now=0)

    assert not machine.observe(
        CleaningObservation(18, 0, 0, "Bathroom"), monotonic_now=1
    )
    assert not machine.observe(
        CleaningObservation(10, 500000, 30, "Bathroom"), monotonic_now=2
    )
    assert machine.phase is SessionPhase.PAUSED_CLEANING
    assert not machine.observe(
        CleaningObservation(18, 1000000, 60, "Bathroom"), monotonic_now=3
    )
    assert machine.phase is SessionPhase.CLEANING
    assert machine.observe(
        CleaningObservation(6, 1000000, 70, "Bathroom"), monotonic_now=4
    ) == {"0_20"}
    assert not machine.observe(
        CleaningObservation(8, 1000000, 70, "Living"), monotonic_now=5
    )


def test_reload_during_job_cannot_complete_that_job() -> None:
    machine = CleaningSessionMachine(ROOMS)
    machine.initialize(18)
    assert machine.blocked

    assert not machine.observe(
        CleaningObservation(18, 2000000, 120, "Bathroom", segment_id=20),
        monotonic_now=1,
    )
    assert not machine.observe(
        CleaningObservation(6, 2000000, 130, "Bathroom", segment_id=20),
        monotonic_now=2,
    )
    assert not machine.observe(
        CleaningObservation(8, 2000000, 130, "Living"), monotonic_now=3
    )
    assert not machine.blocked


def test_missing_state_and_direct_charging_fail_safe() -> None:
    machine = CleaningSessionMachine(ROOMS)
    machine.initialize(8)
    assert machine.set_requested_segments([20], monotonic_now=0)
    assert not machine.observe(
        CleaningObservation(18, 0, 0, "Bathroom"), monotonic_now=1
    )
    assert not machine.observe(
        CleaningObservation(None, 1000000, 60, "Bathroom"), monotonic_now=2
    )
    assert not machine.observe(
        CleaningObservation(8, 1000000, 60, "Bathroom"), monotonic_now=3
    )


def test_stale_target_plan_is_not_reused() -> None:
    machine = CleaningSessionMachine(ROOMS)
    machine.initialize(8)
    assert machine.set_requested_segments([20], monotonic_now=0)
    observations = [
        CleaningObservation(18, 0, 0, "Bathroom"),
        CleaningObservation(18, 1000000, 60, "Bathroom"),
        CleaningObservation(6, 1000000, 70, "Bathroom"),
        CleaningObservation(8, 1000000, 70, "Living"),
    ]
    completed = set().union(
        *(
            machine.observe(observation, monotonic_now=31 + index)
            for index, observation in enumerate(observations)
        )
    )
    assert not completed


def test_vacuums_have_independent_session_machines() -> None:
    bathroom = CleaningSessionMachine(ROOMS)
    kitchen = CleaningSessionMachine(ROOMS)
    for machine, target in ((bathroom, 20), (kitchen, 19)):
        machine.initialize(8)
        assert machine.set_requested_segments([target], monotonic_now=0)

    bathroom.observe(CleaningObservation(18, 0, 0, "Bathroom"), monotonic_now=1)
    kitchen.observe(CleaningObservation(18, 0, 0, "Kitchen"), monotonic_now=1)
    bathroom.observe(CleaningObservation(18, 1000000, 60, "Bathroom"), monotonic_now=2)
    kitchen.observe(CleaningObservation(18, 2000000, 120, "Kitchen"), monotonic_now=2)
    assert bathroom.observe(
        CleaningObservation(6, 1000000, 70, "Bathroom"), monotonic_now=3
    ) == {"0_20"}
    assert kitchen.observe(
        CleaningObservation(6, 2000000, 130, "Kitchen"), monotonic_now=3
    ) == {"0_19"}

    assert not bathroom.observe(
        CleaningObservation(8, 1000000, 70, "Living"), monotonic_now=4
    )
    assert not kitchen.observe(
        CleaningObservation(8, 2000000, 130, "Living"), monotonic_now=4
    )


def test_whole_home_leaves_rooms_that_were_never_reached_on() -> None:
    machine = CleaningSessionMachine(ROOMS)
    machine.initialize(8)
    assert not machine.observe(CleaningObservation(5, 0, 0, "Living"), monotonic_now=1)
    assert not machine.observe(
        CleaningObservation(5, 1000000, 60, "Living"), monotonic_now=2
    )
    assert machine.observe(
        CleaningObservation(6, 1100000, 70, "Living"), monotonic_now=3
    ) == {"0_16"}
    assert not machine.observe(
        CleaningObservation(8, 1100000, 70, "Living"), monotonic_now=4
    )


def test_app_segment_completes_when_cleaning_ends_before_dock() -> None:
    """Complete the cleaned room at returning, not after travelling to the dock."""
    machine = CleaningSessionMachine(ROOMS)
    machine.initialize(8)

    assert not machine.observe(
        CleaningObservation(18, 0, 0, "Living"), monotonic_now=1
    )
    assert not machine.observe(
        CleaningObservation(18, 0, 10, "Living"), monotonic_now=2
    )
    assert not machine.observe(
        CleaningObservation(18, 1000000, 60, "Kitchen"), monotonic_now=3
    )

    # Cleaning is finished here while current_room still says Kitchen.
    assert machine.observe(
        CleaningObservation(6, 1000000, 70, "Kitchen"), monotonic_now=4
    ) == {"0_19"}

    # Passing through Living and docking there must not complete Living.
    assert not machine.observe(
        CleaningObservation(6, 1000000, 80, "Living"), monotonic_now=5
    )
    assert not machine.observe(
        CleaningObservation(8, 1000000, 80, "Living"), monotonic_now=6
    )


def test_app_segment_requires_meaningful_room_cleaning() -> None:
    """Driving through a room or barely moving there must not complete it."""
    machine = CleaningSessionMachine(ROOMS)
    machine.initialize(8)

    assert not machine.observe(
        CleaningObservation(18, 0, 0, "Living"), monotonic_now=1
    )
    # Small area/time growth is below the room-cleaning threshold.
    assert not machine.observe(
        CleaningObservation(18, 100000, 10, "Hall"), monotonic_now=2
    )
    assert not machine.observe(
        CleaningObservation(18, 1100000, 70, "Kitchen"), monotonic_now=3
    )
    assert machine.observe(
        CleaningObservation(6, 1100000, 80, "Kitchen"), monotonic_now=4
    ) == {"0_19"}


def test_app_segment_does_not_complete_dock_room_after_kitchen() -> None:
    """Only Kitchen completes when an app segment job starts/ends in Living."""
    machine = CleaningSessionMachine(ROOMS)
    machine.initialize(8)

    observations = [
        CleaningObservation(18, 0, 0, "Living"),
        CleaningObservation(18, 0, 10, "Living"),
        CleaningObservation(18, 400000, 30, "Kitchen"),
        CleaningObservation(18, 1000000, 60, "Kitchen"),
        CleaningObservation(6, 1000000, 70, "Kitchen"),
        CleaningObservation(6, 1000000, 80, "Living"),
        CleaningObservation(8, 1000000, 80, "Living"),
    ]
    completed = set().union(
        *(
            machine.observe(observation, monotonic_now=index)
            for index, observation in enumerate(observations, start=1)
        )
    )
    assert completed == {"0_19"}


def test_app_segment_can_complete_multiple_actually_cleaned_rooms() -> None:
    """Sequential app-selected rooms can each complete without crediting dock travel."""
    machine = CleaningSessionMachine(ROOMS)
    machine.initialize(8)

    observations = [
        CleaningObservation(18, 0, 0, "Living"),
        CleaningObservation(18, 500000, 30, "Kitchen"),
        CleaningObservation(18, 1000000, 60, "Kitchen"),
        CleaningObservation(18, 1500000, 90, "Bathroom"),
        CleaningObservation(18, 2000000, 120, "Bathroom"),
        CleaningObservation(6, 2000000, 130, "Bathroom"),
        CleaningObservation(8, 2000000, 130, "Living"),
    ]
    completed = set().union(
        *(
            machine.observe(observation, monotonic_now=index)
            for index, observation in enumerate(observations, start=1)
        )
    )
    assert completed == {"0_19", "0_20"}
