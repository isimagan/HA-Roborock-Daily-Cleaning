"""Cached map extraction never invokes Roborock APIs or guesses a saved map."""

from types import SimpleNamespace

import pytest

from custom_components.daily_cleaning.map_cache import cached_map_segments


def _entity(home: object, current_map: int | None = 0) -> SimpleNamespace:
    return SimpleNamespace(
        coordinator=SimpleNamespace(
            properties_api=SimpleNamespace(
                home=home, status=SimpleNamespace(current_map=current_map)
            )
        )
    )


@pytest.mark.parametrize(
    "blocks, expected", [(bytes([19]), [19]), (bytes([20, 19]), [20, 19])]
)
def test_cached_bytes_targets(blocks: bytes, expected: list[int]) -> None:
    home = SimpleNamespace(
        _map_content=SimpleNamespace(
            map_data=SimpleNamespace(blocks=blocks, vacuum_room=16)
        )
    )
    assert cached_map_segments(_entity(home)) == (expected, 16)


def test_fallback_selects_only_current_map() -> None:
    home = SimpleNamespace(
        home_map_content={
            0: SimpleNamespace(map_data={"blocks": bytes([19]), "vacuum_room": 17}),
            1: SimpleNamespace(map_data={"blocks": bytes([20]), "vacuum_room": 20}),
        }
    )
    assert cached_map_segments(_entity(home)) == ([19], 17)
    assert cached_map_segments(_entity(home, 1)) == ([20], 20)
    assert cached_map_segments(_entity(home, 2)) == (None, None)
    assert cached_map_segments(_entity(home, None)) == (None, None)


def test_live_map_precedes_saved_map() -> None:
    home = SimpleNamespace(
        _map_content=SimpleNamespace(
            map_data={"blocks": bytes([20]), "vacuum_room": 16}
        ),
        home_map_content={0: SimpleNamespace(map_data={"blocks": bytes([19])})},
    )
    assert cached_map_segments(_entity(home)) == ([20], 16)


@pytest.mark.parametrize("blocks", [None, "19", [True], [19, "20"], object()])
def test_malformed_targets_fail_safe(blocks: object) -> None:
    home = SimpleNamespace(_map_content=SimpleNamespace(map_data={"blocks": blocks}))
    assert cached_map_segments(_entity(home)) == (None, None)


def test_missing_or_unavailable_cache_fails_safe() -> None:
    class UnavailableHome:
        @property
        def _map_content(self) -> None:
            raise RuntimeError("Cache unavailable")

    assert cached_map_segments(None) == (None, None)
    assert cached_map_segments(_entity(UnavailableHome())) == (None, None)
