"""The start lights follow a monotonic schedule. The engine still owns the race start."""

from __future__ import annotations

import json
import time

from pytestqt.qtbot import QtBot

from slot_racing.core.clock import ManualClock
from slot_racing.core.domain import RaceStatus
from slot_racing.core.events import (
    Event,
    LapCompleted,
    RaceStarted,
    SectorCompleted,
    SensorTriggered,
)
from slot_racing.modules.races.runner import RaceRunner
from slot_racing.modules.races.start_schedule import (
    DEFAULT_LIGHT_COUNT,
    PROTOCOL_VERSION,
    REASON_BEFORE_PLAN,
    REASON_ENGINE_LATE,
    REASON_SKIPPED,
    TIME_BASE,
    TIMESTAMP_MEANING,
    StartSchedule,
    StartSchedulePhase,
    StartScheduleStatus,
)
from slot_racing.modules.races.ui.live_view import LiveRaceView
from slot_racing.modules.races.ui.races_page import RacesPage
from slot_racing.modules.races.ui.start_cue import (
    START_LIGHT_COUNT,
    StartCue,
    StartPhase,
)
from tests.modules.conftest import Env
from tests.modules.test_ui_management import open_page

INTERVAL_NS = 1_000_000_000
_JSON_SCALARS = (str, int, float, bool, type(None))


def _schedule(reference_ns: int = 5_000) -> StartSchedule:
    return StartSchedule.build(
        reference_ns=reference_ns,
        step_interval_ns=INTERVAL_NS,
        sequence_id="sequence-1",
    )


def test_the_default_sequence_keeps_five_lamps_and_the_existing_spacing() -> None:
    schedule = _schedule()
    assert START_LIGHT_COUNT == DEFAULT_LIGHT_COUNT == 5
    assert schedule.light_count == 5
    assert schedule.step_interval_ns == INTERVAL_NS
    assert [step.sound_id for step in schedule.steps] == [
        "light-1",
        "light-2",
        "light-3",
        "light-4",
        "light-5",
        "go",
    ]
    assert schedule.steps[-1].phase is StartSchedulePhase.START_SIGNAL
    assert schedule.steps[-1].lit_lights == 0
    assert schedule.planned_go_ns == schedule.reference_ns + 5 * INTERVAL_NS
    assert schedule.finished_ns == schedule.planned_go_ns + INTERVAL_NS
    assert schedule.status is StartScheduleStatus.ACTIVE


def test_phase_boundaries_are_exact_and_deterministic() -> None:
    schedule = _schedule()
    assert schedule.phase_at(schedule.reference_ns - 1).phase is StartSchedulePhase.IDLE
    assert schedule.phase_at(schedule.reference_ns - 1).may_start is False
    for step in schedule.steps:
        before = schedule.phase_at(step.at_ns - 1)
        at = schedule.phase_at(step.at_ns)
        after = schedule.phase_at(step.at_ns + 1)
        assert at.step is not None and at.step.index == step.index
        assert at.phase is step.phase
        assert at == schedule.phase_at(step.at_ns)
        if step.index == 0:
            assert before.phase is StartSchedulePhase.IDLE
        else:
            assert before.step is not None and before.step.index == step.index - 1
        if step.phase is StartSchedulePhase.START_SIGNAL:
            assert before.may_start is False
            assert at.may_start is True
            assert after.phase is StartSchedulePhase.START_SIGNAL
            assert after.may_start is True
        else:
            assert at.may_start is False
            assert after.step is not None and after.step.index == step.index
    assert schedule.phase_at(schedule.finished_ns - 1).phase is StartSchedulePhase.START_SIGNAL
    assert schedule.next_wake_ns(schedule.reference_ns) == schedule.reference_ns + INTERVAL_NS
    assert schedule.next_wake_ns(schedule.planned_go_ns) == schedule.finished_ns
    assert schedule.next_wake_ns(schedule.finished_ns) is None
    assert schedule.revoke().next_wake_ns(schedule.reference_ns) is None
    done = schedule.phase_at(schedule.finished_ns)
    assert done.phase is StartSchedulePhase.COMPLETE
    assert done.step is None
    assert done.may_start is True
    assert schedule.status is StartScheduleStatus.ACTIVE


def test_a_late_read_skips_past_phases_and_an_early_read_does_not() -> None:
    schedule = _schedule()
    early = schedule.phase_at(schedule.reference_ns + INTERVAL_NS - 1)
    assert early.step is not None and early.step.lit_lights == 1
    jumped = schedule.phase_at(schedule.reference_ns + (2 * INTERVAL_NS) + (INTERVAL_NS // 2))
    assert jumped.step is not None
    assert jumped.step.lit_lights == 3
    assert jumped.step.sound_id == "light-3"
    assert jumped.due_index == 2
    assert jumped.may_start is False
    assert schedule.phase_at(schedule.planned_go_ns - 1).may_start is False
    assert schedule.to_document() == _schedule().to_document()


def test_revoking_a_schedule_leaves_the_original_plan_unchanged() -> None:
    schedule = _schedule()
    revoked = schedule.revoke()
    assert revoked is not schedule
    assert schedule.revoke() is not schedule
    assert schedule.status is StartScheduleStatus.ACTIVE
    assert revoked.status is StartScheduleStatus.REVOKED
    assert revoked.revoke() is revoked
    assert schedule.phase_at(schedule.planned_go_ns).may_start is True
    assert revoked.phase_at(schedule.finished_ns + INTERVAL_NS).may_start is False
    assert revoked.phase_at(schedule.finished_ns + INTERVAL_NS).phase is StartSchedulePhase.REVOKED
    assert revoked.sequence_id == schedule.sequence_id
    assert revoked.planned_go_ns == schedule.planned_go_ns


def test_sequence_ids_are_unique_unless_one_is_requested() -> None:
    first = StartSchedule.build(reference_ns=0, step_interval_ns=INTERVAL_NS)
    second = StartSchedule.build(reference_ns=0, step_interval_ns=INTERVAL_NS)
    named = StartSchedule.build(reference_ns=0, step_interval_ns=INTERVAL_NS, sequence_id="fixed")
    assert first.sequence_id != second.sequence_id
    assert named.sequence_id == "fixed"


def test_the_document_is_json_and_does_not_pretend_to_be_browser_time() -> None:
    schedule = _schedule()
    document = schedule.to_document()
    loaded = json.loads(json.dumps(document))
    assert loaded["protocol_version"] == PROTOCOL_VERSION == 1
    assert loaded["time_base"] == TIME_BASE
    assert loaded["timestamp_meaning"] == TIMESTAMP_MEANING
    assert "browser" in TIMESTAMP_MEANING
    assert loaded["sequence_id"] == "sequence-1"
    assert loaded["status"] == "active"
    assert loaded["step_interval_ns"] == INTERVAL_NS
    assert loaded["planned_go_ns"] == schedule.planned_go_ns
    assert [phase["phase"] for phase in loaded["phases"]] == ["counting"] * 5 + ["start_signal"]
    assert [phase["at_ns"] for phase in loaded["phases"]] == [step.at_ns for step in schedule.steps]
    revoked = json.loads(json.dumps(schedule.revoke().to_document()))
    assert revoked["status"] == "revoked"
    assert revoked["phases"] == loaded["phases"]
    assert _json_values(loaded)
    assert schedule.to_document() == document


def test_phase_queries_stay_cheap() -> None:
    schedule = _schedule(0)
    started = time.perf_counter()
    for offset in range(20_000):
        schedule.phase_at(offset * 1_000)
    assert time.perf_counter() - started < 1


def test_the_cue_follows_the_clock_and_starts_once_at_go(qtbot: QtBot) -> None:
    del qtbot
    clock = ManualClock()
    heard: list[str] = []
    started: list[int] = []
    cue = StartCue(lambda: started.append(clock.now_ns()), interval_ms=1000, clock=clock)
    cue.changed.connect(lambda step: heard.append(step.sound_id))
    cue.begin()
    assert heard == ["light-1"]
    assert cue.current is not None and cue.current.phase is StartPhase.COUNTING
    cue.wake()
    cue.wake()
    assert heard == ["light-1"]
    assert started == []
    clock.advance((2 * INTERVAL_NS) + (INTERVAL_NS // 2))
    cue.wake()
    assert heard == ["light-1", "light-3"]
    assert cue.current is not None and cue.current.lit_lights == 3
    assert started == []
    cue.wake()
    assert cue.current is not None and cue.current.lit_lights == 3
    clock.advance((2 * INTERVAL_NS) + (INTERVAL_NS // 2) + 50_000_000)
    cue.wake()
    assert started == [clock.now_ns()]
    assert heard == ["light-1", "light-3", "go"]
    deviation = cue.deviation
    assert deviation is not None
    assert deviation.canceled is False
    assert deviation.deviation_ns == 50_000_000
    assert deviation.deviation_ms == 50
    assert deviation.deviation_ns >= 0
    assert deviation.delay_reason == REASON_SKIPPED
    cue.wake()
    cue.advance()
    assert started == [deviation.actual_start_ns]
    assert cue.current is None


def test_stepping_a_manual_clock_matches_the_old_lamp_order(qtbot: QtBot) -> None:
    del qtbot
    clock = ManualClock()
    heard: list[str] = []
    started: list[str] = []
    cue = StartCue(lambda: started.append("go"), interval_ms=1000, clock=clock)
    cue.changed.connect(lambda step: heard.append(step.sound_id))
    cue.begin()
    for lit in range(2, START_LIGHT_COUNT + 1):
        cue.advance()
        assert cue.current is not None and cue.current.lit_lights == lit
        assert started == []
    cue.advance()
    assert cue.current is not None and cue.current.phase is StartPhase.START_SIGNAL
    assert started == ["go"]
    assert heard == [f"light-{lit}" for lit in range(1, 6)] + ["go"]
    deviation = cue.deviation
    assert deviation is not None and deviation.deviation_ns == 0
    assert deviation.delay_reason is None
    cue.record_actual_start(deviation.planned_go_ns + 2_000_000)
    assert cue.deviation is not None
    assert cue.deviation.deviation_ns == 2_000_000
    assert cue.deviation.delay_reason == REASON_ENGINE_LATE
    cue.record_actual_start(deviation.planned_go_ns - 1)
    assert cue.deviation is not None
    assert cue.deviation.deviation_ns == -1
    assert cue.deviation.delay_reason == REASON_BEFORE_PLAN
    cue.advance()
    assert cue.current is None
    assert started == ["go"]


def test_a_revoked_cue_cannot_start_and_a_new_one_can(qtbot: QtBot) -> None:
    del qtbot
    clock = ManualClock(1_000)
    started: list[str] = []
    first = StartCue(lambda: started.append("old"), interval_ms=1000, clock=clock)
    first.begin()
    assert first.schedule is not None
    first_id = first.schedule.sequence_id
    first.stop()
    assert started == []
    assert first.current is None
    assert first.deviation is not None and first.deviation.canceled is True
    assert first.schedule is not None and first.schedule.status is StartScheduleStatus.REVOKED
    clock.advance(10 * INTERVAL_NS)
    first.wake()
    first.advance()
    first.stop()
    assert started == []
    assert first.deviation is not None and first.deviation.canceled is True

    second = StartCue(lambda: started.append("new"), interval_ms=1000, clock=clock)
    second.begin()
    assert second.schedule is not None
    assert second.schedule.sequence_id != first_id
    assert second.current is not None and second.current.lit_lights == 1
    for _ in range(START_LIGHT_COUNT):
        second.advance()
    assert started == ["new"]
    assert second.deviation is not None and second.deviation.canceled is False
    assert second.deviation.deviation_ns == 0
    started_at = second.deviation.actual_start_ns
    second.stop()
    assert started == ["new"]
    assert second.deviation is not None
    assert second.deviation.canceled is False
    assert second.deviation.actual_start_ns == started_at


def test_stopping_one_nanosecond_before_go_blocks_the_start(qtbot: QtBot) -> None:
    del qtbot
    clock = ManualClock()
    started: list[str] = []
    cue = StartCue(lambda: started.append("go"), interval_ms=1000, clock=clock)
    cue.begin()
    schedule = cue.schedule
    assert schedule is not None
    clock.set(schedule.planned_go_ns - 1)
    cue.wake()
    assert started == []
    assert cue.current is not None and cue.current.lit_lights == 5
    cue.stop()
    clock.set(schedule.finished_ns + INTERVAL_NS)
    cue.wake()
    assert started == []
    assert cue.deviation is not None and cue.deviation.actual_start_ns is None


def _status(runner: RaceRunner) -> RaceStatus:
    """Read the engine status. A direct ``is`` check would stick for mypy."""
    return runner.status


def _page(qtbot: QtBot, env: Env, interval_ms: int = 1000) -> RacesPage:
    window, page = open_page(qtbot, env, "races")
    assert isinstance(page, RacesPage)
    window.resize(1100, 700)
    window.show()
    page.live.cue_interval_ms = interval_ms
    return page


def _open(page: RacesPage, env: Env, name: str = "Start") -> LiveRaceView:
    track = env.track(name)
    race = env.races.create_race(name, track.id, 3)
    driver_id, vehicle_id = env.pair(1)
    env.races.add_participant(race.id, driver_id, vehicle_id, 1)
    assert page.start_race(race.id)
    live = page.live
    assert isinstance(live, LiveRaceView)
    return live


def test_timer_ticks_do_not_count_the_lamps(qtbot: QtBot, env: Env) -> None:
    page = _page(qtbot, env, interval_ms=1)
    live = _open(page, env)
    runner = live.runner
    assert runner is not None
    qtbot.wait(40)
    assert live.start_lights.lit_lights == 1
    assert _status(runner) is RaceStatus.CREATED
    assert runner.started_at_ns is None
    assert runner.snapshot().elapsed_ns == 0


def test_a_late_clock_skips_lamps_and_records_the_start_delta(qtbot: QtBot, env: Env) -> None:
    page = _page(qtbot, env)
    seen: list[Event] = []
    env.controller.events.subscribe(RaceStarted, seen.append)
    env.controller.events.subscribe(LapCompleted, seen.append)
    live = _open(page, env, "Spaet")
    runner = live.runner
    assert runner is not None
    race_id = runner.race.id
    stored = env.races.require_race(race_id)
    assert stored.started_at is None
    assert stored.status is RaceStatus.READY
    assert env.races.get_laps(race_id) == []
    env.clock.advance((2 * INTERVAL_NS) + (INTERVAL_NS // 2))
    live.sync_start_cue()
    assert live.start_lights.lit_lights == 3
    assert not live.start_lights.showing_go
    assert _status(runner) is RaceStatus.CREATED
    assert runner.snapshot().elapsed_ns == 0
    assert env.races.get_laps(race_id) == []
    assert seen == []
    env.clock.advance((2 * INTERVAL_NS) + (INTERVAL_NS // 2) + 50_000_000)
    live.sync_start_cue()
    assert live.start_lights.showing_go
    assert _status(runner) is RaceStatus.RUNNING
    assert len([event for event in seen if isinstance(event, RaceStarted)]) == 1
    started_at = runner.started_at_ns
    deviation = live.start_deviation
    assert started_at is not None and deviation is not None
    assert deviation.canceled is False
    assert deviation.planned_go_ns == started_at - 50_000_000
    assert deviation.actual_start_ns == started_at
    assert deviation.deviation_ns == 50_000_000
    assert deviation.deviation_ns >= 0
    assert deviation.delay_reason == REASON_SKIPPED
    live.sync_start_cue()
    live.advance_start_cue()
    assert len([event for event in seen if isinstance(event, RaceStarted)]) == 1
    assert runner.started_at_ns == deviation.actual_start_ns
    assert env.races.get_laps(race_id) == []
    assert not any(isinstance(event, LapCompleted) for event in seen)


def test_a_stepped_start_matches_the_plan_and_pause_still_freezes_the_clock(
    qtbot: QtBot, env: Env
) -> None:
    page = _page(qtbot, env)
    live = _open(page, env, "Plan")
    runner = live.runner
    assert runner is not None
    for lit in (2, 3, 4, 5):
        live.advance_start_cue()
        assert live.start_lights.lit_lights == lit
        assert _status(runner) is RaceStatus.CREATED
        assert runner.snapshot().elapsed_ns == 0
    live.advance_start_cue()
    assert live.start_lights.showing_go
    assert _status(runner) is RaceStatus.RUNNING
    deviation = live.start_deviation
    assert deviation is not None and deviation.deviation_ns == 0
    assert deviation.actual_start_ns == runner.started_at_ns
    assert env.races.require_race(runner.race.id).started_at is not None
    frozen = runner.snapshot().elapsed_ns
    runner.pause()
    env.clock.advance(3 * INTERVAL_NS)
    assert _status(runner) is RaceStatus.PAUSED
    assert runner.snapshot().elapsed_ns == frozen
    runner.resume()
    env.clock.advance(INTERVAL_NS)
    assert runner.snapshot().elapsed_ns == frozen + INTERVAL_NS
    assert env.races.get_laps(runner.race.id) == []


def test_cancel_restart_and_a_sensor_before_go(qtbot: QtBot, env: Env) -> None:
    page = _page(qtbot, env)
    sectors: list[Event] = []
    starts: list[Event] = []
    env.controller.events.subscribe(SectorCompleted, sectors.append)
    env.controller.events.subscribe(RaceStarted, starts.append)
    live = _open(page, env, "Abbruch")
    runner = live.runner
    assert runner is not None
    race_id = runner.race.id
    position_id = _expected_position(runner)
    _cross(env, env.clock.now_ns(), lane=1, position_id=position_id)
    live.advance_start_cue()
    assert live.start_lights.lit_lights == 2
    live.hide()
    assert _status(runner) is RaceStatus.CREATED
    assert live.start_deviation is not None and live.start_deviation.canceled is True
    env.clock.advance(30 * INTERVAL_NS)
    live.sync_start_cue()
    assert _status(runner) is RaceStatus.CREATED
    assert starts == []
    assert sectors == []
    assert env.races.get_laps(race_id) == []
    assert env.races.require_race(race_id).started_at is None

    live.show()
    live.open_for_start(runner)
    assert live.start_lights.lit_lights == 1
    assert _status(runner) is RaceStatus.CREATED
    for _ in range(START_LIGHT_COUNT):
        live.advance_start_cue()
    assert _status(runner) is RaceStatus.RUNNING
    assert len(starts) == 1
    started_at = runner.started_at_ns
    assert started_at is not None and started_at > 0
    _cross(env, started_at - 1, lane=1, position_id=position_id)
    assert sectors == []
    assert env.races.get_laps(race_id) == []
    _cross(env, started_at, lane=1, position_id=position_id)
    assert len(sectors) == 1
    assert isinstance(sectors[0], SectorCompleted)
    assert sectors[0].lane == 1
    assert env.races.get_laps(race_id) == []
    live.advance_start_cue()
    assert _status(runner) is RaceStatus.RUNNING
    assert runner.started_at_ns == started_at
    assert len(starts) == 1


def _expected_position(runner: RaceRunner) -> str:
    """First point the engine expects after GO. The lap sequence ends at start/finish."""
    return str(runner._engine.config.layout.lap_sequence[0].id)


def _cross(env: Env, timestamp_ns: int, lane: int, position_id: str) -> None:
    env.controller.events.publish(
        SensorTriggered(
            timestamp_ns=timestamp_ns,
            source_id="test",
            sensor_id="sensor_01",
            position_id=position_id,
            lane=lane,
        )
    )


def _json_values(value: object) -> bool:
    if isinstance(value, dict):
        return all(isinstance(key, str) and _json_values(item) for key, item in value.items())
    if isinstance(value, list):
        return all(_json_values(item) for item in value)
    return isinstance(value, _JSON_SCALARS)
