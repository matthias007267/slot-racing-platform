"""Browser start lights follow confirmed PC events and never invent GO."""

from __future__ import annotations

import json
import shutil
import subprocess

import pytest

from slot_racing.modules.races.broadcast import BROADCAST_PROTOCOL_VERSION
from slot_racing.modules.races.live_page import hud_page
from slot_racing.modules.races.start_display import (
    PROTOCOL_VERSION,
    StartDisplay,
    commit_document,
    initial_display,
    phase_document,
    reduce_start_display,
    start_is_committed,
)

_COMMIT = commit_document(sequence_id="seq", actual_start_ns=80, race_status="running")


def _event(kind: str, seq: int, payload: dict[str, object]) -> dict[str, object]:
    return {"protocol_version": PROTOCOL_VERSION, "type": kind, "seq": seq, "payload": payload}


def _fold(events: list[dict[str, object]], start: StartDisplay | None = None) -> StartDisplay:
    state = initial_display() if start is None else start
    for event in events:
        state = reduce_start_display(state, event)
    return state


def _schedule(sequence: str = "seq", seq: int = 1, **extra: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "sequence_id": sequence,
        "status": "active",
        "planned_go_ns": 999,
    }
    payload.update(extra)
    return _event("start_schedule", seq, payload)


def _phase(
    lit: int,
    seq: int,
    *,
    sequence: str = "seq",
    step: int | None = None,
) -> dict[str, object]:
    return _event(
        "start_phase",
        seq,
        phase_document(
            sequence_id=sequence,
            step_index=lit - 1 if step is None else step,
            lit_lights=lit,
            at_ns=1_000 + lit,
        ),
    )


def _commit(seq: int = 1, **overrides: object) -> dict[str, object]:
    payload: dict[str, object] = dict(_COMMIT)
    payload.update(overrides)
    return _event("start_committed", seq, payload)


def test_protocol_version_is_the_broadcast_version() -> None:
    assert PROTOCOL_VERSION == 2
    assert BROADCAST_PROTOCOL_VERSION == PROTOCOL_VERSION


def test_counting_phases_never_authorize_go_and_commit_uses_the_engine_stamp() -> None:
    assert start_is_committed(None, "running") is False
    assert start_is_committed(80, "created") is False
    assert start_is_committed(True, "running") is False
    assert start_is_committed(80, "running") is True
    state = _fold([_schedule(), *[_phase(lit, lit) for lit in range(1, 6)]])
    assert state.state == "counting"
    assert state.lit_lights == 5
    assert state.authorizes_go is False
    assert state.actual_start_ns is None
    released = reduce_start_display(state, _commit(1))
    assert released.state == "committed"
    assert released.authorizes_go is True
    assert released.lit_lights == 0
    assert released.actual_start_ns == 80
    assert reduce_start_display(released, _commit(2)).to_document() == released.to_document()


def test_a_scene_and_a_plan_cannot_release_the_start() -> None:
    counting = _fold([_schedule(), _phase(2, 1)])
    scene = reduce_start_display(
        counting,
        {
            "protocol_version": PROTOCOL_VERSION,
            "type": "scene",
            "seq": 9,
            "payload": {
                "status": "running",
                "lights": {"go": True, "lit_lights": 0},
                "planned_go_ns": 1,
            },
        },
    )
    assert scene == counting
    assert scene.authorizes_go is False
    planned = _fold([_schedule(planned_go_ns=1)])
    assert planned.authorizes_go is False
    assert planned.lit_lights == 0
    wrong = reduce_start_display(counting, _commit(4, sequence_id="other"))
    assert wrong == counting
    paused = reduce_start_display(counting, _commit(4, race_status="paused"))
    assert paused.authorizes_go is False
    old_protocol = dict(_commit(4))
    old_protocol["protocol_version"] = 1
    assert reduce_start_display(counting, old_protocol) == counting


def test_stale_duplicate_and_reordered_phases_do_not_rewind_the_lamps() -> None:
    state = _fold([_schedule(), _phase(1, 1), _phase(2, 2), _phase(4, 4)])
    assert state.lit_lights == 4
    assert reduce_start_display(state, _phase(3, 3)) == state
    assert reduce_start_display(state, _phase(4, 4)) == state
    assert reduce_start_display(state, _phase(5, 2)) == state
    assert reduce_start_display(state, _phase(5, 5, sequence="older")) == state
    jumped = reduce_start_display(state, _phase(5, 6))
    assert jumped.lit_lights == 5
    assert jumped.authorizes_go is False
    released = reduce_start_display(jumped, _commit(1))
    assert reduce_start_display(released, _phase(1, 9)).authorizes_go is True
    assert reduce_start_display(released, _phase(1, 9)).lit_lights == 0


def test_revoke_stops_the_sequence_and_a_new_one_starts_clean() -> None:
    counting = _fold([_schedule(), _phase(1, 1), _phase(3, 3)])
    revoked = reduce_start_display(
        counting,
        _event("start_schedule_revoked", 2, {"sequence_id": "seq", "status": "revoked"}),
    )
    assert revoked.state == "revoked"
    assert revoked.lit_lights == 0
    assert revoked.authorizes_go is False
    assert reduce_start_display(revoked, _phase(4, 4)).state == "revoked"
    assert reduce_start_display(revoked, _commit(1)).authorizes_go is False
    restarted = reduce_start_display(revoked, _schedule("next", seq=3))
    assert restarted.state == "counting"
    assert restarted.sequence_id == "next"
    assert restarted.lit_lights == 0
    assert restarted.authorizes_go is False
    current = reduce_start_display(restarted, _phase(1, 1, sequence="next"))
    old = reduce_start_display(
        current,
        _event("start_schedule_revoked", 4, {"sequence_id": "seq", "status": "revoked"}),
    )
    assert old == current
    assert old.lit_lights == 1


def test_disconnect_does_not_continue_or_release_and_reconnect_does_not_replay() -> None:
    counting = _fold([_schedule(), _phase(1, 1), _phase(5, 5)])
    lost = reduce_start_display(counting, {"type": "disconnect"})
    assert lost.state == "disconnected"
    assert lost.lit_lights == 0
    assert lost.authorizes_go is False
    assert reduce_start_display(lost, _phase(5, 6)) == lost
    assert reduce_start_display(lost, _commit(1)) == lost
    again = reduce_start_display(lost, {"type": "disconnect"})
    assert again == lost
    resync = reduce_start_display(lost, {"type": "reconnect"})
    assert resync.state == "resyncing"
    assert resync.authorizes_go is False
    assert resync.lit_lights == 0
    snapped = _fold(
        [
            _schedule(seq=1),
            _phase(5, 1),
            _commit(1),
        ],
        resync,
    )
    assert snapped.state == "committed"
    assert snapped.lit_lights == 0
    assert snapped.actual_start_ns == 80
    assert snapped.step_index == 4
    during = _fold([_schedule(seq=1), _phase(3, 1)], resync)
    assert during.state == "counting"
    assert during.lit_lights == 3
    assert during.authorizes_go is False
    settled = reduce_start_display(resync, {"type": "resync_scene"})
    assert settled.state == "idle"
    assert settled.authorizes_go is False
    assert reduce_start_display(during, {"type": "resync_scene"}) == during


def test_commit_before_the_schedule_is_not_a_start_and_a_commit_is_not_replayed() -> None:
    early = _fold([_commit(1), _schedule()])
    assert early.authorizes_go is False
    assert early.state == "counting"
    released = reduce_start_display(early, _commit(1))
    assert released.authorizes_go is True
    phase_first = reduce_start_display(released, _phase(2, 8))
    assert phase_first.lit_lights == 0
    assert phase_first.authorizes_go is True


def test_the_page_reducer_matches_the_python_rules() -> None:
    node = shutil.which("node")
    if node is None:
        pytest.skip("node is not installed")
    script = hud_page().decode().split("<script>", 1)[1].split("</script>", 1)[0]
    reducer_end = _function_end(script, "function reduceStart(")
    source = script[script.index("function whole(") : reducer_end]
    cases: list[list[dict[str, object]]] = [
        [_schedule(), *[_phase(lit, lit) for lit in range(1, 6)], _commit(1)],
        [_schedule(), _phase(4, 4), _phase(2, 5), _phase(3, 3)],
        [
            _schedule(),
            _phase(2, 2),
            {
                "type": "scene",
                "protocol_version": 2,
                "seq": 1,
                "payload": {"status": "running", "lights": {"go": True}},
            },
        ],
        [_schedule(), _phase(1, 1), _commit(3, sequence_id="nope")],
        [_schedule(), _phase(5, 5), {"type": "disconnect"}, _commit(1), {"type": "reconnect"}],
        [
            _schedule(),
            _phase(4, 4),
            {"type": "disconnect"},
            {"type": "reconnect"},
            _schedule(seq=2),
            _phase(4, 1),
        ],
        [
            _schedule(),
            _phase(2, 2),
            _event("start_schedule_revoked", 2, {"sequence_id": "seq", "status": "revoked"}),
            _phase(3, 3),
            _schedule("next", seq=3),
            _phase(1, 1, sequence="next"),
            _event("start_schedule_revoked", 4, {"sequence_id": "seq", "status": "revoked"}),
        ],
        [_commit(1), _schedule(planned_go_ns=5)],
    ]
    payload = []
    expected = []
    for events in cases:
        state = initial_display()
        payload.append({"start": state.to_document(), "events": events})
        expected.append(_fold(events).to_document())
    completed = subprocess.run(
        [node, "-e", source + "\n" + _NODE_DRIVER],
        input=json.dumps(payload),
        text=True,
        capture_output=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
    assert json.loads(completed.stdout) == expected


def _function_end(script: str, signature: str) -> int:
    brace = script.index("{", script.index(signature))
    depth = 0
    for index in range(brace, len(script)):
        if script[index] == "{":
            depth += 1
        elif script[index] == "}":
            depth -= 1
            if depth == 0:
                return index + 1
    raise AssertionError("browser reducer is not closed")


_NODE_DRIVER = """
const chunks = [];
process.stdin.on("data", function (chunk) { chunks.push(chunk); });
process.stdin.on("end", function () {
  const cases = JSON.parse(chunks.join(""));
  const output = cases.map(function (item) {
    let state = item.start;
    item.events.forEach(function (event) { state = reduceStart(state, event); });
    return state;
  });
  process.stdout.write(JSON.stringify(output));
});
"""
