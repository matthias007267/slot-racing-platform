"""Confirmed start lights for a browser. The race PC is the only authority.

Phase 2C-1 sends lamps the PC has already shown. It does not predict the next
lamp and it does not synchronize clocks. ``planned_go_ns`` is a host timestamp,
never permission to show GO.

``start_phase`` confirms red lamps that are already on. ``authorizes_go`` is
false on every phase. GO exists only in ``start_committed``, and the live view
publishes that document only after ``RaceEngine.start`` has stamped the race.
A scene, a race status, or a timer is not that stamp.

Phase 2C-2 may estimate a clock offset and light red lamps locally. It still
must not turn the lamps out until ``start_committed`` arrives.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, replace

PROTOCOL_VERSION = 2
PHASE_COUNTING = "counting"
RACE_RUNNING = "running"

STATE_IDLE = "idle"
STATE_COUNTING = "counting"
STATE_COMMITTED = "committed"
STATE_REVOKED = "revoked"
STATE_DISCONNECTED = "disconnected"
STATE_RESYNCING = "resyncing"

_OPEN = frozenset({STATE_COUNTING, STATE_COMMITTED, STATE_REVOKED})


@dataclass(frozen=True, slots=True)
class StartDisplay:
    """What a browser may show. ``authorizes_go`` is true only after a commit."""

    state: str = STATE_IDLE
    sequence_id: str | None = None
    lit_lights: int = 0
    step_index: int = -1
    phase_seq: int = -1
    commit_seq: int = -1
    schedule_seq: int = -1
    actual_start_ns: int | None = None
    race_status: str | None = None
    authorizes_go: bool = False

    def to_document(self) -> dict[str, object]:
        return {
            "state": self.state,
            "sequence_id": self.sequence_id,
            "lit_lights": self.lit_lights,
            "step_index": self.step_index,
            "phase_seq": self.phase_seq,
            "commit_seq": self.commit_seq,
            "schedule_seq": self.schedule_seq,
            "actual_start_ns": self.actual_start_ns,
            "race_status": self.race_status,
            "authorizes_go": self.authorizes_go,
        }


def initial_display() -> StartDisplay:
    return StartDisplay()


def start_is_committed(started_at_ns: int | None, race_status: str) -> bool:
    """True when the engine has stamped a running race. A plan is not enough."""
    if started_at_ns is None or isinstance(started_at_ns, bool) or started_at_ns < 0:
        return False
    return race_status == RACE_RUNNING


def phase_document(
    *,
    sequence_id: str,
    step_index: int,
    lit_lights: int,
    at_ns: int | None = None,
) -> dict[str, object]:
    """One red phase the PC has already reached. This document cannot mean GO."""
    document: dict[str, object] = {
        "sequence_id": sequence_id,
        "phase": PHASE_COUNTING,
        "step_index": step_index,
        "lit_lights": lit_lights,
        "authorizes_go": False,
    }
    if at_ns is not None:
        document["at_ns"] = at_ns
    parse_phase_document(document)
    return document


def commit_document(
    *,
    sequence_id: str,
    actual_start_ns: int,
    race_status: str,
) -> dict[str, object]:
    """The race clock is running. ``actual_start_ns`` is the engine stamp."""
    document = {
        "sequence_id": sequence_id,
        "actual_start_ns": actual_start_ns,
        "race_status": race_status,
        "authorizes_go": True,
    }
    parse_commit_document(document)
    return document


def parse_phase_document(document: Mapping[str, object]) -> tuple[str, int, int]:
    """Return sequence, step and lit lamps. Anything that could mean GO is refused."""
    sequence_id = document.get("sequence_id")
    step_index = document.get("step_index")
    lit_lights = document.get("lit_lights")
    if not isinstance(sequence_id, str) or not sequence_id:
        raise ValueError("sequence_id is required")
    if document.get("phase") != PHASE_COUNTING:
        raise ValueError("start_phase only confirms a red counting lamp")
    if document.get("authorizes_go") is not False:
        raise ValueError("start_phase cannot authorize GO")
    if isinstance(step_index, bool) or not isinstance(step_index, int) or step_index < 0:
        raise ValueError("step_index must not be negative")
    if isinstance(lit_lights, bool) or not isinstance(lit_lights, int) or lit_lights < 1:
        raise ValueError("a counting phase lights at least one lamp")
    if "at_ns" in document and _whole(document.get("at_ns")) is None:
        raise ValueError("at_ns must be a host timestamp")
    return sequence_id, step_index, lit_lights


def parse_commit_document(document: Mapping[str, object]) -> tuple[str, int]:
    """Return sequence and the engine stamp. A planned time is not accepted."""
    sequence_id = document.get("sequence_id")
    actual_start_ns = document.get("actual_start_ns")
    if not isinstance(sequence_id, str) or not sequence_id:
        raise ValueError("sequence_id is required")
    if document.get("authorizes_go") is not True:
        raise ValueError("start_committed is the start authorization")
    if document.get("race_status") != RACE_RUNNING:
        raise ValueError("start_committed requires a running race")
    if (
        isinstance(actual_start_ns, bool)
        or not isinstance(actual_start_ns, int)
        or actual_start_ns < 0
    ):
        raise ValueError("actual_start_ns must be the engine stamp")
    return sequence_id, actual_start_ns


def reduce_start_display(state: StartDisplay, event: Mapping[str, object]) -> StartDisplay:
    """Fold one message into the lamps a browser may show.

    The result never sets GO because a scene said ``running`` or because
    ``planned_go_ns`` is in the past. A dropped connection leaves the lamps
    off. The next connection starts from the PC's current documents and does
    not replay earlier lamps.
    """
    kind = event.get("type")
    if kind == "disconnect":
        if state.state == STATE_DISCONNECTED:
            return state
        return replace(state, state=STATE_DISCONNECTED, lit_lights=0, authorizes_go=False)
    if kind == "reconnect":
        if state.state == STATE_IDLE and state.sequence_id is None:
            return state
        return StartDisplay(state=STATE_RESYNCING)
    if kind == "resync_scene":
        if state.state != STATE_RESYNCING:
            return state
        return StartDisplay()
    if kind == "scene" or state.state == STATE_DISCONNECTED:
        return state
    if event.get("protocol_version") != PROTOCOL_VERSION:
        return state
    seq = _whole(event.get("seq"))
    payload = event.get("payload")
    if seq is None or not isinstance(payload, Mapping):
        return state
    if kind == "start_schedule":
        return _schedule(state, seq, payload, revoked=False)
    if kind == "start_schedule_revoked":
        return _schedule(state, seq, payload, revoked=True)
    if kind == "start_phase":
        return _phase(state, seq, payload)
    if kind == "start_committed":
        return _commit(state, seq, payload)
    return state


def _schedule(
    state: StartDisplay,
    seq: int,
    payload: Mapping[str, object],
    *,
    revoked: bool,
) -> StartDisplay:
    if seq <= state.schedule_seq:
        return state
    sequence_id = _text(payload.get("sequence_id"))
    if sequence_id is None:
        return state
    is_revoked = revoked or payload.get("status") == STATE_REVOKED
    if is_revoked:
        if state.sequence_id not in {None, sequence_id}:
            return state
        if state.state == STATE_COMMITTED and state.sequence_id == sequence_id:
            return state
        return replace(
            state,
            state=STATE_REVOKED,
            sequence_id=sequence_id,
            lit_lights=0,
            step_index=-1,
            authorizes_go=False,
            schedule_seq=seq,
        )
    if state.sequence_id == sequence_id and state.state in _OPEN:
        return replace(state, schedule_seq=seq)
    return replace(
        state,
        state=STATE_COUNTING,
        sequence_id=sequence_id,
        lit_lights=0,
        step_index=-1,
        phase_seq=-1,
        commit_seq=-1,
        schedule_seq=seq,
        actual_start_ns=None,
        race_status=None,
        authorizes_go=False,
    )


def _phase(state: StartDisplay, seq: int, payload: Mapping[str, object]) -> StartDisplay:
    if state.state != STATE_COUNTING or seq <= state.phase_seq:
        return state
    try:
        sequence_id, step_index, lit_lights = parse_phase_document(payload)
    except ValueError:
        return state
    if sequence_id != state.sequence_id or step_index <= state.step_index:
        return state
    return replace(
        state,
        lit_lights=lit_lights,
        step_index=step_index,
        phase_seq=seq,
        authorizes_go=False,
    )


def _commit(state: StartDisplay, seq: int, payload: Mapping[str, object]) -> StartDisplay:
    if state.state == STATE_COMMITTED:
        return state
    if state.state != STATE_COUNTING or seq <= state.commit_seq:
        return state
    try:
        sequence_id, actual_start_ns = parse_commit_document(payload)
    except ValueError:
        return state
    if sequence_id != state.sequence_id:
        return state
    return replace(
        state,
        state=STATE_COMMITTED,
        lit_lights=0,
        authorizes_go=True,
        actual_start_ns=actual_start_ns,
        race_status=RACE_RUNNING,
        commit_seq=seq,
    )


def _text(value: object) -> str | None:
    if not isinstance(value, str) or not value:
        return None
    return value


def _whole(value: object) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return value
