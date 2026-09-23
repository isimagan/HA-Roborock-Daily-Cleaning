"""Fail-safe Roborock cleaning-session state machine.

This module is framework independent. It decides which configured segments are
safe to mark as cleaned, but never changes Home Assistant state itself.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any

RAW_CLEANING = 5
RAW_RETURNING_HOME = 6
RAW_CHARGING = 8
RAW_PAUSED = 10
RAW_SPOT_CLEANING = 11
RAW_GOING_TO_TARGET = 16
RAW_ZONED_CLEANING = 17
RAW_SEGMENT_CLEANING = 18

_RAW_IDLE = 3
_ALLOWED_CLEANING_STATES = {RAW_CLEANING, RAW_SEGMENT_CLEANING}
_IGNORED_JOB_STATES = {
    RAW_SPOT_CLEANING,
    RAW_GOING_TO_TARGET,
    RAW_ZONED_CLEANING,
}
_SAFE_TERMINAL_STATES = {_RAW_IDLE, RAW_CHARGING}
_PLAN_MAX_AGE_SECONDS = 30.0


class SessionMode(Enum):
    """Supported cleaning modes."""

    WHOLE_HOME = "whole_home"
    SEGMENTS = "segments"


class SessionPhase(Enum):
    """Phases that affect completion semantics."""

    CLEANING = "cleaning"
    PAUSED_CLEANING = "paused_cleaning"
    PENDING_RETURN = "pending_return"
    PAUSED_RETURN = "paused_return"


@dataclass(frozen=True, slots=True)
class RoomTarget:
    """A configured room that can be completed by the state machine."""

    segment_id: str
    name: str
    segment_name: str


@dataclass(frozen=True, slots=True)
class CleaningObservation:
    """One cached Roborock observation."""

    raw_state: int | None
    clean_area: float | None = None
    clean_time: float | None = None
    current_room: str | None = None
    segment_id: str | int | None = None
    target_segment_id: str | int | None = None


@dataclass(slots=True)
class _Evidence:
    area: float = 0.0
    duration: float = 0.0

    @property
    def confirms_cleaning(self) -> bool:
        """Require both kinds of session-local growth."""
        return self.area > 0 and self.duration > 0


@dataclass(slots=True)
class _Session:
    mode: SessionMode
    raw_cleaning_state: int
    requested_segments: tuple[str, ...] | None
    phase: SessionPhase = SessionPhase.CLEANING
    return_was_normal: bool = False
    active_segment: str | None = None
    active_requested_index: int | None = None
    completed_before_active: set[str] = field(default_factory=set)
    evidence: dict[str, _Evidence] = field(default_factory=dict)
    last_area: float | None = None
    last_time: float | None = None


class CleaningSessionMachine:
    """Track one vacuum and emit segments only after confirmed docking."""

    def __init__(self, rooms: list[RoomTarget]) -> None:
        self._rooms = {str(room.segment_id): room for room in rooms}
        raw_segments: dict[str, set[str]] = {}
        for segment_id in self._rooms:
            raw_id = _raw_segment_id(segment_id)
            raw_segments.setdefault(raw_id, set()).add(segment_id)
        self._raw_to_segment = {
            raw_id: next(iter(segment_ids))
            for raw_id, segment_ids in raw_segments.items()
            if len(segment_ids) == 1
        }

        names: dict[str, set[str]] = {}
        for room in rooms:
            for name in (room.name, room.segment_name):
                normalized = _normalize_name(name)
                if not normalized:
                    continue
                names.setdefault(normalized, set()).add(room.segment_id)
        self._name_to_segment = {
            name: next(iter(segment_ids))
            for name, segment_ids in names.items()
            if len(segment_ids) == 1
        }

        self._initialized = False
        self._blocked_until_terminal = True
        self._pending_segments: tuple[str, ...] | None = None
        self._pending_segments_at = 0.0
        self._session: _Session | None = None

    @property
    def active(self) -> bool:
        """Return whether a valid cleaning session is in progress."""
        return self._session is not None

    @property
    def blocked(self) -> bool:
        """Return whether startup/recovery uncertainty blocks new sessions."""
        return self._blocked_until_terminal

    @property
    def phase(self) -> SessionPhase | None:
        """Expose the current phase for diagnostics and tests."""
        return self._session.phase if self._session else None

    def initialize(self, raw_state: int | None) -> None:
        """Synchronize at setup without adopting a job already in progress."""
        self._initialized = True
        self._session = None
        self._pending_segments = None
        self._blocked_until_terminal = raw_state not in _SAFE_TERMINAL_STATES

    def set_requested_segments(
        self, segment_ids: list[str | int], *, monotonic_now: float
    ) -> bool:
        """Remember an exact, imminent HA-requested segment order."""
        if self._session is not None or self._blocked_until_terminal:
            return False
        canonical: list[str] = []
        for segment_id in segment_ids:
            resolved = self._canonical_segment(segment_id)
            if resolved is None:
                self._pending_segments = None
                return False
            if resolved not in canonical:
                canonical.append(resolved)
        if not canonical:
            self._pending_segments = None
            return False
        self._pending_segments = tuple(canonical)
        self._pending_segments_at = monotonic_now
        return True

    def clear_requested_segments(self) -> None:
        """Discard a target plan that can no longer be trusted."""
        self._pending_segments = None

    def observe(
        self, observation: CleaningObservation, *, monotonic_now: float
    ) -> frozenset[str]:
        """Consume one observation and return newly completed segment IDs."""
        raw_state = observation.raw_state
        if not self._initialized:
            self.initialize(raw_state)
            return frozenset()

        self._expire_plan(monotonic_now)

        if self._blocked_until_terminal:
            if raw_state in _SAFE_TERMINAL_STATES:
                self._blocked_until_terminal = False
            return frozenset()

        if self._session is None:
            if raw_state in _IGNORED_JOB_STATES:
                self.clear_requested_segments()
                return frozenset()
            if raw_state in _ALLOWED_CLEANING_STATES:
                self._start_session(observation, monotonic_now)
            return frozenset()

        session = self._session
        if raw_state in _IGNORED_JOB_STATES:
            self._invalidate_until_terminal()
            return frozenset()

        if raw_state in _ALLOWED_CLEANING_STATES and (
            raw_state != session.raw_cleaning_state
        ):
            self._invalidate_until_terminal()
            return frozenset()

        if raw_state is None:
            self._invalidate_until_terminal()
            return frozenset()
        if raw_state == _RAW_IDLE:
            self._reset_session()
            return frozenset()
        if raw_state not in {
            session.raw_cleaning_state,
            RAW_PAUSED,
            RAW_RETURNING_HOME,
            RAW_CHARGING,
        }:
            self._invalidate_until_terminal()
            return frozenset()

        if raw_state in {
            session.raw_cleaning_state,
            RAW_PAUSED,
            RAW_RETURNING_HOME,
            RAW_CHARGING,
        }:
            self._record_metric_deltas(session, observation)

        if raw_state == session.raw_cleaning_state:
            if session.phase is SessionPhase.PAUSED_CLEANING:
                session.phase = SessionPhase.CLEANING
            elif session.phase in {
                SessionPhase.PENDING_RETURN,
                SessionPhase.PAUSED_RETURN,
            }:
                self._invalidate_until_terminal()
                return frozenset()
            self._update_active_segment(session, observation)
            return frozenset()

        if raw_state == RAW_PAUSED:
            if session.phase is SessionPhase.CLEANING:
                session.phase = SessionPhase.PAUSED_CLEANING
            elif session.phase is SessionPhase.PENDING_RETURN:
                session.phase = SessionPhase.PAUSED_RETURN
            return frozenset()

        if raw_state == RAW_RETURNING_HOME:
            if session.phase is SessionPhase.CLEANING:
                session.return_was_normal = True
                session.phase = SessionPhase.PENDING_RETURN
            elif session.phase is SessionPhase.PAUSED_CLEANING:
                session.return_was_normal = False
                session.phase = SessionPhase.PENDING_RETURN
            elif session.phase is SessionPhase.PAUSED_RETURN:
                session.phase = SessionPhase.PENDING_RETURN
            return frozenset()

        if raw_state == RAW_CHARGING:
            if session.phase not in {
                SessionPhase.PENDING_RETURN,
                SessionPhase.PAUSED_RETURN,
            }:
                self._reset_session()
                return frozenset()
            completed = self._completed_at_dock(session)
            self._reset_session()
            return frozenset(completed)

        return frozenset()

    def _start_session(
        self, observation: CleaningObservation, monotonic_now: float
    ) -> None:
        requested: tuple[str, ...] | None = None
        if observation.raw_state == RAW_SEGMENT_CLEANING:
            if (
                self._pending_segments is not None
                and monotonic_now - self._pending_segments_at <= _PLAN_MAX_AGE_SECONDS
            ):
                requested = self._pending_segments
        self._pending_segments = None
        session = _Session(
            mode=(
                SessionMode.WHOLE_HOME
                if observation.raw_state == RAW_CLEANING
                else SessionMode.SEGMENTS
            ),
            raw_cleaning_state=observation.raw_state,
            requested_segments=requested,
            last_area=_number(observation.clean_area),
            last_time=_number(observation.clean_time),
        )
        self._session = session
        self._update_active_segment(session, observation)

    def _record_metric_deltas(
        self, session: _Session, observation: CleaningObservation
    ) -> None:
        area = _number(observation.clean_area)
        duration = _number(observation.clean_time)
        area_delta = _positive_delta(session.last_area, area)
        time_delta = _positive_delta(session.last_time, duration)
        session.last_area = area if area is not None else session.last_area
        session.last_time = duration if duration is not None else session.last_time
        if session.active_segment is None:
            return
        evidence = session.evidence.setdefault(session.active_segment, _Evidence())
        evidence.area += area_delta
        evidence.duration += time_delta

    def _update_active_segment(
        self, session: _Session, observation: CleaningObservation
    ) -> None:
        if session.mode is SessionMode.WHOLE_HOME:
            segment = self._segment_for_room(observation.current_room)
            if segment is not None:
                session.active_segment = segment
            return

        exact_segment = self._canonical_segment(observation.segment_id)
        candidate = exact_segment or self._segment_for_room(observation.current_room)
        if session.requested_segments is None:
            # Without an HA request, only Roborock's exact segment_id is safe.
            if exact_segment is None:
                return
            if session.active_segment != exact_segment:
                self._finish_active_if_confirmed(session)
                session.active_segment = exact_segment
            return

        if candidate is None:
            return
        try:
            candidate_index = session.requested_segments.index(candidate)
        except ValueError:
            return

        active_index = session.active_requested_index
        if active_index is None:
            if candidate_index != 0:
                return
            session.active_requested_index = 0
            session.active_segment = candidate
            return
        if candidate_index == active_index:
            return
        if candidate_index != active_index + 1:
            return
        self._finish_active_if_confirmed(session)
        session.active_requested_index = candidate_index
        session.active_segment = candidate

    def _finish_active_if_confirmed(self, session: _Session) -> None:
        active = session.active_segment
        if active is not None and self._is_confirmed(session, active):
            session.completed_before_active.add(active)

    def _completed_at_dock(self, session: _Session) -> set[str]:
        if session.mode is SessionMode.WHOLE_HOME:
            if not session.return_was_normal:
                return set()
            return {
                segment_id
                for segment_id in self._rooms
                if self._is_confirmed(session, segment_id)
            }

        completed = set(session.completed_before_active)
        if (
            session.return_was_normal
            and session.active_segment is not None
            and self._is_confirmed(session, session.active_segment)
        ):
            completed.add(session.active_segment)
        return completed

    def _is_confirmed(self, session: _Session, segment_id: str) -> bool:
        evidence = session.evidence.get(segment_id)
        return evidence is not None and evidence.confirms_cleaning

    def _canonical_segment(self, segment_id: Any) -> str | None:
        if segment_id is None or isinstance(segment_id, bool):
            return None
        value = str(segment_id)
        if value in self._rooms:
            return value
        return self._raw_to_segment.get(_raw_segment_id(value))

    def _segment_for_room(self, room_name: str | None) -> str | None:
        return self._name_to_segment.get(_normalize_name(room_name))

    def _expire_plan(self, monotonic_now: float) -> None:
        if (
            self._pending_segments is not None
            and monotonic_now - self._pending_segments_at > _PLAN_MAX_AGE_SECONDS
        ):
            self._pending_segments = None

    def _invalidate_until_terminal(self) -> None:
        self._session = None
        self._pending_segments = None
        self._blocked_until_terminal = True

    def _reset_session(self) -> None:
        self._session = None
        self._pending_segments = None


def _normalize_name(value: str | None) -> str:
    if not isinstance(value, str):
        return ""
    return " ".join(value.casefold().split())


def _raw_segment_id(segment_id: str) -> str:
    return str(segment_id).rsplit("_", maxsplit=1)[-1]


def _number(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    numeric = float(value)
    return numeric if numeric >= 0 else None


def _positive_delta(previous: float | None, current: float | None) -> float:
    if previous is None or current is None or current <= previous:
        return 0.0
    return current - previous
