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


def test_app_started_segment_job_without_targets_fails_safe() -> None:
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
    assert not completed


def test_app_started_segment_job_attributes_first_cleaning_delta_to_new_room() -> None:
    """Do not credit dock/transit room when app cleaning moves into its target."""
    machine = CleaningSessionMachine(ROOMS)
    machine.initialize(8)

    observations = [
        # The robot starts at its dock in Living, but no area has been cleaned.
        CleaningObservation(18, 0, 0, "Living", map_segments=(19,)),
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
        CleaningObservation(18, 0, 0, "Living", map_segments=(19,)), monotonic_now=1
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
        CleaningObservation(18, 0, 0, "Living", map_segments=(19,)), monotonic_now=1
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
        CleaningObservation(18, 0, 0, "Living", map_segments=(19,)),
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
        CleaningObservation(18, 0, 0, "Living", map_segments=(20, 19)),
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


def _run_app_observations(observations: list[CleaningObservation]) -> set[str]:
    machine = CleaningSessionMachine(ROOMS)
    machine.initialize(8)
    completed: set[str] = set()
    for index, observation in enumerate(observations, start=1):
        result = machine.observe(observation, monotonic_now=index)
        if observation.raw_state == 18:
            assert not result
        if machine.active:
            # Verify transit rooms never acquire evidence, not just the output.
            assert not ({"0_16", "0_17"} & machine._session.evidence.keys())
        completed.update(result)
    return completed


def test_kitchen_targets_arrive_after_dock_room_exceeds_threshold() -> None:
    assert _run_app_observations(
        [
            CleaningObservation(18, 0, 0, "Living"),
            CleaningObservation(18, 280000, 28, "Living"),
            CleaningObservation(18, 300000, 30, "Living", map_segments=(19,)),
            CleaningObservation(18, 350000, 35, "Kitchen"),
            CleaningObservation(18, 700000, 65, "Kitchen"),
            CleaningObservation(6, 700000, 70, "Kitchen"),
            CleaningObservation(6, 2000000, 150, "Living"),
            CleaningObservation(8, 2000000, 150, "Living"),
        ]
    ) == {"0_19"}


def test_bathroom_map_targets_known_at_dock_ignore_transit_growth() -> None:
    assert _run_app_observations(
        [
            CleaningObservation(18, 0, 0, "Living", map_segments=(20,)),
            CleaningObservation(18, 280000, 28, "Living"),
            CleaningObservation(18, 560000, 56, "Hall"),
            CleaningObservation(18, 570000, 60, "Bathroom"),
            CleaningObservation(18, 900000, 90, "Bathroom"),
            CleaningObservation(18, 1500000, 150, "Hall"),
            CleaningObservation(6, 2000000, 200, "Living"),
        ]
    ) == {"0_20"}


@pytest.mark.parametrize(
    "room_order", [("Bathroom", "Kitchen"), ("Kitchen", "Bathroom")]
)
def test_map_multi_room_allowlist_does_not_impose_order(
    room_order: tuple[str, str],
) -> None:
    first, second = room_order
    assert _run_app_observations(
        [
            CleaningObservation(18, 0, 0, "Hall", map_segments=(20, 19)),
            CleaningObservation(18, 280000, 28, "Hall"),
            CleaningObservation(18, 560000, 56, "Living"),
            CleaningObservation(18, 600000, 60, first),
            CleaningObservation(18, 1000000, 100, first),
            CleaningObservation(18, 1300000, 130, "Hall"),
            CleaningObservation(18, 1400000, 140, second),
            CleaningObservation(18, 1800000, 180, second),
            CleaningObservation(6, 1800000, 190, second),
            CleaningObservation(6, 2500000, 250, "Living"),
            CleaningObservation(8, 2500000, 250, "Living"),
        ]
    ) == {"0_20", "0_19"}


def test_pre_target_growth_is_not_retroactively_credited() -> None:
    assert not _run_app_observations(
        [
            CleaningObservation(18, 0, 0, "Kitchen"),
            CleaningObservation(18, 280000, 28, "Kitchen"),
            CleaningObservation(18, 1000000, 100, "Kitchen", map_segments=(19,)),
            CleaningObservation(18, 1100000, 110, "Kitchen"),
            CleaningObservation(6, 1100000, 110, "Kitchen"),
        ]
    )


def test_map_targets_are_locked_despite_cache_changes() -> None:
    assert _run_app_observations(
        [
            CleaningObservation(18, 0, 0, "Living", map_segments=(19,)),
            CleaningObservation(18, 300000, 30, "Kitchen"),
            CleaningObservation(18, 700000, 70, "Bathroom", map_segments=(20,)),
            CleaningObservation(18, 1200000, 120, "Bathroom", map_segments=()),
            CleaningObservation(6, 1200000, 120, "Bathroom"),
        ]
    ) == {"0_19"}


def test_no_targets_never_credits_room_names_or_map_location() -> None:
    assert not _run_app_observations(
        [
            CleaningObservation(18, 0, 0, "Living", map_vacuum_room=16),
            CleaningObservation(18, 280000, 28, "Living", map_segments=()),
            CleaningObservation(18, 1000000, 100, "Kitchen", map_vacuum_room=19),
            CleaningObservation(6, 1000000, 120, "Kitchen"),
        ]
    )


def test_numeric_map_location_takes_precedence_over_room_name() -> None:
    assert _run_app_observations(
        [
            CleaningObservation(
                18, 0, 0, "Kitchen", map_segments=(19,), map_vacuum_room=16
            ),
            CleaningObservation(18, 280000, 28, "Kitchen", map_vacuum_room=17),
            CleaningObservation(18, 300000, 30, "Living", map_vacuum_room=19),
            CleaningObservation(18, 700000, 70, "Living", map_vacuum_room=19),
            CleaningObservation(6, 700000, 80, "Living", map_vacuum_room=19),
        ]
    ) == {"0_19"}


@pytest.mark.parametrize("abort_state", [10, None, 17, 11, 8])
def test_map_segment_jobs_preserve_abort_fail_safes(abort_state: int | None) -> None:
    assert not _run_app_observations(
        [
            CleaningObservation(18, 0, 0, "Living", map_segments=(19, 20)),
            CleaningObservation(18, 300000, 30, "Kitchen"),
            CleaningObservation(abort_state, 300000, 30, "Kitchen"),
            CleaningObservation(6, 1000000, 100, "Kitchen"),
            CleaningObservation(8, 1000000, 100, "Living"),
        ]
    )


def test_map_job_pause_resume_keeps_targets() -> None:
    assert _run_app_observations(
        [
            CleaningObservation(18, 0, 0, "Living", map_segments=(19,)),
            CleaningObservation(18, 300000, 30, "Kitchen"),
            CleaningObservation(10, 300000, 30, "Kitchen"),
            CleaningObservation(18, 600000, 60, "Kitchen", map_segments=(20,)),
            CleaningObservation(6, 600000, 70, "Kitchen"),
        ]
    ) == {"0_19"}


def test_ha_plan_remains_authoritative_over_map_targets() -> None:
    machine = CleaningSessionMachine(ROOMS)
    machine.initialize(8)
    assert machine.set_requested_segments([20], monotonic_now=0)
    assert not machine.observe(
        CleaningObservation(
            18, 0, 0, "Bathroom", map_segments=(19,), map_vacuum_room=19
        ),
        monotonic_now=1,
    )
    assert not machine.observe(
        CleaningObservation(18, 300000, 30, "Bathroom", map_segments=(19,)),
        monotonic_now=2,
    )
    assert machine.observe(
        CleaningObservation(6, 300000, 30, "Bathroom"), monotonic_now=3
    ) == {"0_20"}


def test_unvisited_or_under_cleaned_selected_rooms_do_not_complete() -> None:
    assert _run_app_observations(
        [
            CleaningObservation(18, 0, 0, "Hall", map_segments=(20, 19)),
            CleaningObservation(18, 300000, 30, "Kitchen"),
            CleaningObservation(18, 310000, 31, "Bathroom"),
            CleaningObservation(6, 310000, 31, "Bathroom"),
        ]
    ) == {"0_19"}


def test_first_targets_during_return_cannot_complete_any_room() -> None:
    assert not _run_app_observations(
        [
            CleaningObservation(18, 0, 0, "Kitchen"),
            CleaningObservation(18, 300000, 30, "Kitchen"),
            CleaningObservation(6, 1000000, 100, "Kitchen", map_segments=(19,)),
            CleaningObservation(8, 1000000, 100, "Living"),
        ]
    )


def test_transit_growth_on_first_return_cannot_confirm_last_target() -> None:
    assert not _run_app_observations(
        [
            CleaningObservation(18, 0, 0, "Living", map_segments=(19,)),
            CleaningObservation(18, 100000, 10, "Kitchen"),
            CleaningObservation(6, 1000000, 100, "Living"),
        ]
    )


def test_map_allowlist_replaces_and_discards_pre_target_exact_evidence() -> None:
    machine = CleaningSessionMachine(ROOMS)
    machine.initialize(8)
    observations = [
        CleaningObservation(18, 0, 0, "Living", segment_id=16),
        CleaningObservation(18, 300000, 30, "Living", segment_id=16),
        CleaningObservation(
            18, 600000, 60, "Living", segment_id=16, map_segments=(19,)
        ),
        CleaningObservation(18, 900000, 90, "Living", segment_id=16),
        CleaningObservation(18, 1000000, 100, "Kitchen", segment_id=19),
        CleaningObservation(18, 1400000, 140, "Kitchen", segment_id=19),
        CleaningObservation(6, 1400000, 140, "Kitchen", segment_id=19),
    ]
    completed = set().union(
        *(
            machine.observe(obs, monotonic_now=i)
            for i, obs in enumerate(observations, 1)
        )
    )
    assert completed == {"0_19"}


def test_unconfigured_map_targets_never_enable_name_or_exact_fallback() -> None:
    assert not _run_app_observations(
        [
            CleaningObservation(18, 0, 0, "Living", map_segments=(99,)),
            CleaningObservation(18, 1000000, 100, "Kitchen", segment_id=19),
            CleaningObservation(6, 1000000, 100, "Kitchen", segment_id=19),
        ]
    )


def test_map_targets_do_not_survive_into_next_session() -> None:
    machine = CleaningSessionMachine(ROOMS)
    machine.initialize(8)
    for targets, room, expected in [
        ((19,), "Kitchen", {"0_19"}),
        (None, "Bathroom", set()),
    ]:
        assert not machine.observe(
            CleaningObservation(18, 0, 0, room, map_segments=targets), monotonic_now=1
        )
        assert not machine.observe(
            CleaningObservation(18, 300000, 30, room), monotonic_now=2
        )
        assert (
            machine.observe(CleaningObservation(6, 300000, 30, room), monotonic_now=3)
            == expected
        )
        assert not machine.observe(
            CleaningObservation(8, 300000, 30, "Living"), monotonic_now=4
        )


def test_reload_with_map_targets_does_not_adopt_active_job() -> None:
    machine = CleaningSessionMachine(ROOMS)
    machine.initialize(18)
    assert not machine.observe(
        CleaningObservation(18, 1000000, 100, "Kitchen", map_segments=(19,)),
        monotonic_now=1,
    )
    assert not machine.observe(
        CleaningObservation(6, 2000000, 200, "Kitchen", map_segments=(19,)),
        monotonic_now=2,
    )
