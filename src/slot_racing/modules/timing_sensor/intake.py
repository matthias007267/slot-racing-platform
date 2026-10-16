"""Turn one provisional line into a diagnostic observation and, sometimes, a race event.

Session id and event number stay here. A duplicate is not forwarded. A gap is
recorded and is not filled with invented events. A new session, or an event
number that goes backwards, drops the time mapping. Later lines produce no
race event until a new mapping is supplied.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, replace

from slot_racing.core.events import SensorTriggered
from slot_racing.modules.timing_sensor.clock_map import (
    DeviceClockMapping,
    SyncStatus,
    to_host_ns,
)
from slot_racing.modules.timing_sensor.configuration import SensorAssignment
from slot_racing.modules.timing_sensor.protocol import (
    ErrorFrame,
    HelloFrame,
    SensorFrame,
    SensorProtocolError,
    StatusFrame,
    SyncCommand,
    SyncReply,
    parse_sensor_line,
)

_FORWARDED = frozenset({"accepted", "gap"})


@dataclass(frozen=True, slots=True)
class IntakeState:
    """Sequence and clock assignment remembered across lines. The mapping is never inferred."""

    mapping: DeviceClockMapping | None = None
    sync: SyncStatus = SyncStatus.UNSYNCHRONIZED
    session_id: str | None = None
    last_sequence: int | None = None


@dataclass(frozen=True, slots=True)
class SensorObservation:
    """One line as the diagnostic page shows it. ``gap`` is how many numbers were skipped."""

    kind: str
    raw: str
    session_id: str | None = None
    sequence: int | None = None
    device_time_ns: int | None = None
    physical_id: str | None = None
    position_id: str | None = None
    lane: int | None = None
    host_time_ns: int | None = None
    gap: int = 0
    detail: str = ""


def consume_line(
    state: IntakeState,
    line: str,
    *,
    assignments: Mapping[str, SensorAssignment],
    logical_ids: Mapping[str, str],
    active_lanes: frozenset[int] | None,
    source_id: str,
) -> tuple[IntakeState, SensorObservation | None, SensorTriggered | None]:
    """Interpret ``line``. Blank lines and comments yield no observation.

    ``active_lanes`` is the race's lanes. ``None`` means no race filter: the
    observation is still built, and the caller decides whether to publish.
    """
    try:
        message = parse_sensor_line(line)
    except SensorProtocolError as error:
        return (
            state,
            SensorObservation(kind="parse_error", raw=line, detail=error.reason),
            None,
        )
    if message is None or isinstance(message, SyncCommand):
        return state, None, None
    if isinstance(message, HelloFrame):
        return _hello(state, message, line)
    if isinstance(message, StatusFrame):
        kind = "rollover" if message.code == "TIME" else "status"
        return state, _control(message.session_id, line, kind, message.code), None
    if isinstance(message, ErrorFrame):
        return _error(state, message, line)
    if isinstance(message, SyncReply):
        return state, _control(message.session_id, line, "sync", str(message.host_seq)), None
    frame = message
    restart = _restart(state, frame, line)
    if restart is not None:
        return restart
    if state.last_sequence is not None and frame.sequence == state.last_sequence:
        return state, _observation(frame, line, "duplicate"), None
    # Sequence numbers start at 1. A first line above 1 skipped the earlier edges.
    if state.last_sequence is None:
        gap = frame.sequence - 1
    elif frame.sequence > state.last_sequence + 1:
        gap = frame.sequence - state.last_sequence - 1
    else:
        gap = 0
    advanced = replace(state, session_id=frame.session_id, last_sequence=frame.sequence)
    assignment = assignments.get(frame.physical_id)
    if assignment is None:
        return advanced, _observation(frame, line, "unassigned", gap=gap), None
    logical_id = logical_ids.get(assignment.position_id)
    if logical_id is None:
        return advanced, _observation(frame, line, "unassigned", assignment, gap=gap), None
    host_ns, clock_failed = _host_time(state, frame)
    if state.sync is not SyncStatus.SYNCHRONIZED or state.mapping is None or clock_failed:
        kind = "clock_error" if clock_failed else "unsynchronized"
        return (
            advanced,
            _observation(frame, line, kind, assignment, host_ns, gap),
            None,
        )
    if active_lanes is not None and assignment.lane not in active_lanes:
        return (
            advanced,
            _observation(frame, line, "ignored", assignment, host_ns, gap),
            None,
        )
    kind = "gap" if gap else "accepted"
    observation = _observation(frame, line, kind, assignment, host_ns, gap)
    if kind not in _FORWARDED or host_ns is None:
        return advanced, observation, None
    event = SensorTriggered(
        timestamp_ns=host_ns,
        source_id=source_id,
        sensor_id=logical_id,
        position_id=assignment.position_id,
        lane=assignment.lane,
    )
    return advanced, observation, event


def with_mapping(state: IntakeState, mapping: DeviceClockMapping) -> IntakeState:
    """Install ``mapping`` as the only time base. The previous one is discarded."""
    if not isinstance(mapping, DeviceClockMapping):
        raise TypeError("mapping must be a DeviceClockMapping")
    return replace(state, mapping=mapping, sync=SyncStatus.SYNCHRONIZED)


def without_mapping(state: IntakeState) -> IntakeState:
    """Drop the time base. A base that was valid becomes :attr:`SyncStatus.LOST`."""
    if state.sync is SyncStatus.UNSYNCHRONIZED:
        return replace(state, mapping=None)
    return replace(state, mapping=None, sync=SyncStatus.LOST)


def _hello(
    state: IntakeState, frame: HelloFrame, raw: str
) -> tuple[IntakeState, SensorObservation, None]:
    changed = state.session_id is not None and frame.session_id != state.session_id
    detail = f"{frame.device} {frame.firmware} {' '.join(frame.sensors)}"
    if not changed:
        updated = replace(state, session_id=frame.session_id)
        return updated, _control(frame.session_id, raw, "hello", detail), None
    locked = IntakeState(
        mapping=None,
        sync=SyncStatus.UNSYNCHRONIZED
        if state.sync is SyncStatus.UNSYNCHRONIZED
        else SyncStatus.LOST,
        session_id=frame.session_id,
        last_sequence=None,
    )
    return locked, _control(frame.session_id, raw, "restart", detail), None


def _error(
    state: IntakeState, frame: ErrorFrame, raw: str
) -> tuple[IntakeState, SensorObservation, None]:
    if frame.code == "BUFFER":
        return state, _control(frame.session_id, raw, "buffer", str(frame.detail)), None
    if frame.code == "TIME":
        locked = replace(
            state,
            mapping=None,
            sync=SyncStatus.UNSYNCHRONIZED
            if state.sync is SyncStatus.UNSYNCHRONIZED
            else SyncStatus.LOST,
        )
        return locked, _control(frame.session_id, raw, "time_fault", str(frame.detail)), None
    return state, _control(frame.session_id, raw, "protocol", frame.code), None


def _control(session_id: str, raw: str, kind: str, detail: str) -> SensorObservation:
    return SensorObservation(kind=kind, raw=raw, session_id=session_id, detail=detail)


def _restart(
    state: IntakeState, frame: SensorFrame, raw: str
) -> tuple[IntakeState, SensorObservation, None] | None:
    new_session = state.session_id is not None and frame.session_id != state.session_id
    rewound = state.last_sequence is not None and frame.sequence < state.last_sequence
    if not new_session and not rewound:
        return None
    locked = IntakeState(
        mapping=None,
        sync=SyncStatus.LOST,
        session_id=frame.session_id,
        last_sequence=frame.sequence,
    )
    detail = "session" if new_session else "sequence"
    return locked, _observation(frame, raw, "restart", detail=detail), None


def _host_time(state: IntakeState, frame: SensorFrame) -> tuple[int | None, bool]:
    if state.mapping is None or state.sync is not SyncStatus.SYNCHRONIZED:
        return None, False
    try:
        return to_host_ns(frame.device_time_ns, state.mapping), False
    except ValueError:
        return None, True


def _observation(
    frame: SensorFrame,
    raw: str,
    kind: str,
    assignment: SensorAssignment | None = None,
    host_time_ns: int | None = None,
    gap: int = 0,
    detail: str = "",
) -> SensorObservation:
    return SensorObservation(
        kind=kind,
        raw=raw,
        session_id=frame.session_id,
        sequence=frame.sequence,
        device_time_ns=frame.device_time_ns,
        physical_id=frame.physical_id,
        position_id=None if assignment is None else assignment.position_id,
        lane=None if assignment is None else assignment.lane,
        host_time_ns=host_time_ns,
        gap=gap,
        detail=detail,
    )
