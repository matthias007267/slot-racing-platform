"""Free sessions count laps per lane and stay outside every regular result."""

from __future__ import annotations

from pytestqt.qtbot import QtBot
from sqlalchemy import func, select

from slot_racing.core.catalog import DriverInfo, VehicleInfo
from slot_racing.core.championship import PointsRule, score_championship
from slot_racing.core.clock import NANOS_PER_SECOND as S
from slot_racing.core.clock import ManualClock
from slot_racing.core.domain import (
    DriverId,
    Participant,
    ParticipantResult,
    RaceId,
    RaceMode,
    RaceStatus,
    TimingLayout,
    VehicleId,
)
from slot_racing.core.events import (
    EventBus,
    EventDispatcher,
    LapCompleted,
    SensorTriggered,
    WinnerDetermined,
)
from slot_racing.core.statistics import TimeScope, career_summary, track_records
from slot_racing.modules.races.engine import RaceConfig, RaceEngine
from slot_racing.modules.races.models import Lap, TimeMeasurement
from slot_racing.modules.races.runner import LiveRow, RaceRunner
from slot_racing.modules.races.types import RaceInfo
from slot_racing.modules.races.ui.live_view import LiveRaceView
from slot_racing.modules.races.ui.races_page import RacesPage
from slot_racing.modules.races.ui.wizard import RaceWizard
from tests.modules.conftest import Env
from tests.modules.test_ui_management import column_text, open_page

_LINE = TimingLayout.from_position_ids(["start_finish"])
# A lap ends at start/finish. The default layout is passed as sector, sector, start/finish.
_LAP_ORDER = ("sector_1", "sector_2", "start_finish")


def test_one_empty_lane_does_not_block_the_other() -> None:
    clock = ManualClock()
    bus = EventBus()
    engine = _engine(bus, clock, lanes=(1,))
    engine.start()
    _cross(bus, 1, 1 * S)
    _cross(bus, 2, 2 * S)
    _cross(bus, 2, 7 * S)
    _cross(bus, 1, 6 * S)
    row = _result(engine, 1)
    assert row.laps_completed == 1
    assert row.best_lap_ns == 5 * S
    assert len(engine.results()) == 1


def test_each_valid_lap_counts_for_its_own_lane_and_keeps_the_best() -> None:
    clock = ManualClock()
    bus = EventBus()
    laps: list[LapCompleted] = []
    bus.subscribe(LapCompleted, laps.append)
    engine = _engine(bus, clock, lanes=(1, 2))
    engine.start()
    _cross(bus, 1, 1 * S)
    _cross(bus, 2, 1 * S)
    assert _result(engine, 1).laps_completed == 0
    assert _result(engine, 1).best_lap_ns is None
    _cross(bus, 1, 6 * S)
    _cross(bus, 1, 12 * S)
    _cross(bus, 2, 9 * S)
    lane_1 = _result(engine, 1)
    lane_2 = _result(engine, 2)
    assert (lane_1.laps_completed, lane_1.best_lap_ns) == (2, 5 * S)
    assert [event.lap_time_ns for event in laps if event.lane == 1] == [5 * S, 6 * S]
    assert (lane_2.laps_completed, lane_2.best_lap_ns) == (1, 8 * S)


def test_a_driver_change_drops_an_open_lap_and_late_events() -> None:
    clock = ManualClock()
    bus = EventBus()
    winners: list[WinnerDetermined] = []
    bus.subscribe(WinnerDetermined, winners.append)
    engine = _engine(bus, clock, lanes=(1, 2))
    engine.start()
    _cross(bus, 1, 1 * S)
    _cross(bus, 1, 6 * S)
    _cross(bus, 2, 1 * S)
    _cross(bus, 2, 8 * S)
    clock.advance(9 * S)
    engine.reassign_lane(Participant(DriverId(3), 1, VehicleId(3)), at_ns=clock.now_ns())
    _cross(bus, 1, 8 * S)
    assert _result(engine, 1).laps_completed == 0
    assert _result(engine, 1).best_lap_ns is None
    assert _result(engine, 2).laps_completed == 1
    assert _result(engine, 2).best_lap_ns == 7 * S
    _cross(bus, 1, 10 * S)
    assert _result(engine, 1).laps_completed == 0
    _cross(bus, 1, 14 * S)
    _cross(bus, 2, 12 * S)
    assert _result(engine, 1).laps_completed == 1
    assert _result(engine, 1).best_lap_ns == 4 * S
    assert _result(engine, 2).laps_completed == 2
    assert _result(engine, 2).best_lap_ns == 4 * S
    engine.stop()
    assert engine.status is RaceStatus.FINISHED
    assert winners == []


def test_a_vehicle_change_and_a_return_to_the_previous_pair_both_reset(env: Env) -> None:
    anna, porsche, ben, ferrari = _people(env)
    race = _free_race(env, ((1, anna, porsche), (2, ben, ferrari)))
    alfa = env.vehicle("Alfa")
    runner = env.controller.start_race(race.id)
    origin = runner.started_at_ns or 0
    _drive_lap(env.controller.events, 1, origin, origin + 5 * S)
    _drive_lap(env.controller.events, 2, origin, origin + 7 * S)
    _cross(env.controller.events, 1, origin + 6 * S, "sector_1")
    env.clock.advance(9 * S)
    assert env.controller.switch_lane(1, anna.id, alfa.id) is True
    _cross(env.controller.events, 1, origin + 8 * S, "sector_2")
    reset = _live_row(runner, 1)
    assert reset.laps_completed == 0
    assert reset.last_lap_ns is None
    assert reset.best_lap_ns is None
    assert _live_row(runner, 2).laps_completed == 1
    assert _live_row(runner, 2).best_lap_ns == 7 * S
    _cross(env.controller.events, 1, origin + 10 * S, "sector_1")
    _cross(env.controller.events, 1, origin + 12 * S, "sector_2")
    _cross(env.controller.events, 1, origin + 15 * S, "start_finish")
    assert _live_row(runner, 1).laps_completed == 1
    assert _live_row(runner, 1).best_lap_ns == 5 * S
    env.clock.advance(7 * S)
    assert env.controller.switch_lane(1, anna.id, porsche.id) is True
    returned = _live_row(runner, 1)
    assert returned.laps_completed == 0
    assert returned.best_lap_ns is None
    assert _live_row(runner, 2).best_lap_ns == 7 * S
    runner.stop()
    assert all(row.position is None for row in env.races.get_results(race.id))
    assert env.races.get_laps(race.id) == []


def test_the_same_selection_and_cancel_keep_the_lap(qtbot: QtBot, env: Env) -> None:
    anna, porsche, ben, ferrari = _people(env)
    race = _free_race(env, ((1, anna, porsche), (2, ben, ferrari)))
    alfa = env.vehicle("Alfa")
    live, runner = _open_live(qtbot, env, race.id)
    origin = runner.started_at_ns or 0
    assert _cell(live, 1, "Runden") == "0"
    assert _cell(live, 1, "Letzte Runde") == "-"
    assert _cell(live, 1, "Beste Runde") == "-"
    assert _cell(live, 2, "Letzte Runde") == "-"
    _drive_lap(env.controller.events, 1, origin, origin + 5 * S)
    _drive_lap(env.controller.events, 2, origin, origin + 4 * S)
    assert _cell(live, 1, "Runden") == "1"
    assert _cell(live, 1, "Letzte Runde") == "0:05.000"
    assert _cell(live, 1, "Beste Runde") == "0:05.000"
    assert _cell(live, 2, "Beste Runde") == "0:04.000"

    live.switch_buttons[1].click()
    dialog = live.lane_switch_dialogs[1]
    assert not dialog.isHidden()
    assert dialog.driver_combo.currentData() == anna.id
    assert dialog.vehicle_combo.currentData() == porsche.id
    _drive_lap(env.controller.events, 2, origin + 4 * S, origin + 9 * S)
    assert _cell(live, 2, "Runden") == "2"
    assert _cell(live, 1, "Runden") == "1"
    dialog.confirm_button.click()
    assert _cell(live, 1, "Runden") == "1"
    assert _cell(live, 1, "Beste Runde") == "0:05.000"
    assert _cell(live, 2, "Runden") == "2"

    live.switch_buttons[1].click()
    dialog = live.lane_switch_dialogs[1]
    dialog.vehicle_combo.setCurrentIndex(dialog.vehicle_combo.findData(alfa.id))
    dialog.cancel_button.click()
    assert dialog.isHidden()
    assert _cell(live, 1, "Runden") == "1"
    seated = env.races.require_race(race.id).participants
    lane_1 = next(person for person in seated if person.lane == 1)
    assert lane_1.vehicle_id == porsche.id


def test_ending_stops_timing_and_leaves_every_statistic_untouched(env: Env) -> None:
    anna, porsche, ben, ferrari = _people(env)
    track = env.track("Ring", lanes=2)
    trial = env.races.create_time_trial("Wertungslauf", track.id)
    env.races.add_participant(trial.id, anna.id, porsche.id, 1)
    env.races.record_lap(trial.id, 1, 1, 8 * S, 8 * S, {})
    env.races.record_finished(
        trial.id,
        (
            ParticipantResult(
                driver_id=anna.id,
                lane=1,
                position=1,
                laps_completed=1,
                finished=True,
                total_time_ns=8 * S,
                best_lap_ns=8 * S,
            ),
        ),
        aborted=False,
    )
    race = env.races.create_free_race("Frei", track.id)
    env.races.set_free_assignments(race.id, ((1, anna.id, porsche.id), (2, ben.id, ferrari.id)))
    runner = env.controller.start_race(race.id)
    origin = runner.started_at_ns or 0
    _drive_lap(env.controller.events, 1, origin, origin + 4 * S)
    assert _live_row(runner, 1).best_lap_ns == 4 * S
    winners: list[WinnerDetermined] = []
    env.controller.events.subscribe(WinnerDetermined, winners.append)
    runner.stop()
    assert runner.is_finished
    assert winners == []
    assert env.races.require_race(race.id).status is RaceStatus.FINISHED
    assert env.races.get_laps(race.id) == []
    assert env.races.list_time_measurements(race_id=race.id) == []
    assert all(row.position is None for row in env.races.get_results(race.id))
    with env.runtime.database.session() as session:
        laps = session.scalar(
            select(func.count()).select_from(Lap).where(Lap.race_id == int(race.id))
        )
        measured = session.scalar(
            select(func.count())
            .select_from(TimeMeasurement)
            .where(TimeMeasurement.race_id == int(race.id))
        )
    assert laps == 0
    assert measured == 0

    completed = env.races.list_completed()
    official = next(item for item in completed if item.mode is RaceMode.TIME_TRIAL)
    scope = TimeScope(track_id=int(track.id), unassigned=True)
    summary = career_summary(completed, scope, driver_id=int(anna.id))
    assert summary.laps == 1
    assert summary.best_lap_ns == 8 * S
    assert [item.race_id for item in summary.history] == [official.race_id]
    records = track_records(completed, scope)
    assert [line.time_ns for line in records] == [8 * S]
    standing = score_championship(completed, PointsRule(((1, 25),), fastest_lap_bonus=3))
    assert standing[0].points == 28
    assert standing[0].counted_races == 1
    best = env.races.personal_best(anna.id, porsche.id, 1, track_id=track.id)
    assert best is not None and best.time_ns == 8 * S
    rankings = env.races.lane_rankings(track_id=track.id)
    assert [place.time_ns for ranking in rankings for place in ranking.places] == [8 * S]


def test_the_wizard_asks_only_for_driver_and_vehicle_per_lane(qtbot: QtBot, env: Env) -> None:
    track = env.track("Heimbahn", lanes=2)
    anna = env.driver("Anna")
    porsche = env.vehicle("Porsche", driver_id=anna.id)
    _, page = open_page(qtbot, env, "races")
    assert isinstance(page, RacesPage)
    page.new_race()
    wizard = page.wizard
    assert isinstance(wizard, RaceWizard)
    wizard.name_edit.setText("Abend")
    assert wizard.go_next()
    wizard.track_combo.setCurrentIndex(wizard.track_combo.findData(track.id))
    assert wizard.go_next()
    wizard.mode_combo.setCurrentIndex(wizard.mode_combo.findData(RaceMode.FREE.value))
    assert wizard.laps_spin.isHidden()
    assert wizard.duration_edit.isHidden()
    assert wizard.repetitions_spin.isHidden()
    assert not wizard.free_hint.isHidden()
    assert wizard.go_next()
    assert not wizard.free_seats.isHidden()
    assert wizard.participant_table.isHidden()
    lane_1 = wizard._free_drivers[1]
    lane_2 = wizard._free_drivers[2]
    lane_1.setCurrentIndex(lane_1.findData(anna.id))
    assert wizard._free_vehicles[1].currentData() == porsche.id
    lane_2.setCurrentIndex(0)
    wizard._free_vehicles[2].setCurrentIndex(0)
    assert wizard.go_next()
    assert "Freies Rennen" in wizard.overview_label.text()
    assert "Wiederholungen" not in wizard.overview_label.text().split()
    stored = env.races.require_race(wizard.race.id) if wizard.race is not None else None
    assert stored is not None
    assert stored.mode is RaceMode.FREE
    assert stored.laps == 0
    assert stored.duration_minutes is None
    assert [(item.lane, item.driver_id, item.vehicle_id) for item in stored.participants] == [
        (1, anna.id, porsche.id)
    ]
    assert env.races.heat_plan(stored.id) == []
    assert page.live.switch_buttons[1].text() == "Fahrer/Auto wechseln – Spur 1"  # noqa: RUF001
    assert page.live.switch_buttons[2].text() == "Fahrer/Auto wechseln – Spur 2"  # noqa: RUF001


def _people(env: Env) -> tuple[DriverInfo, VehicleInfo, DriverInfo, VehicleInfo]:
    anna = env.driver("Anna")
    porsche = env.vehicle("Porsche", driver_id=anna.id)
    ben = env.driver("Ben")
    ferrari = env.vehicle("Ferrari", driver_id=ben.id)
    return anna, porsche, ben, ferrari


def _free_race(env: Env, seats: tuple[tuple[int, DriverInfo, VehicleInfo], ...]) -> RaceInfo:
    track = env.track(lanes=2)
    race = env.races.create_free_race("Frei", track.id)
    env.races.set_free_assignments(
        race.id, tuple((lane, driver.id, vehicle.id) for lane, driver, vehicle in seats)
    )
    return env.races.require_race(race.id)


def _open_live(qtbot: QtBot, env: Env, race_id: RaceId) -> tuple[LiveRaceView, RaceRunner]:
    live = LiveRaceView(
        env.runtime.translator,
        env.controller,
        service=env.races,
        drivers=env.drivers,
        vehicles=env.vehicles,
    )
    qtbot.addWidget(live)
    runner = env.controller.start_race(race_id)
    live.show_runner(runner)
    live._timer.stop()
    return live, runner


def _engine(bus: EventBus, clock: ManualClock, *, lanes: tuple[int, ...]) -> RaceEngine:
    participants = tuple(Participant(DriverId(lane), lane, VehicleId(lane)) for lane in lanes)
    return RaceEngine(
        RaceConfig(RaceId(1), 0, participants, _LINE, mode=RaceMode.FREE),
        bus,
        clock,
    )


def _cross(
    bus: EventDispatcher,
    lane: int,
    timestamp_ns: int,
    position_id: str = "start_finish",
) -> None:
    bus.publish(
        SensorTriggered(
            timestamp_ns=timestamp_ns,
            source_id="manual",
            sensor_id=position_id,
            position_id=position_id,
            lane=lane,
        )
    )


def _drive_lap(bus: EventDispatcher, lane: int, start_ns: int, end_ns: int) -> None:
    span = end_ns - start_ns
    for index, position_id in enumerate(_LAP_ORDER, start=1):
        _cross(bus, lane, start_ns + span * index // len(_LAP_ORDER), position_id)


def _result(engine: RaceEngine, lane: int) -> ParticipantResult:
    return next(result for result in engine.results() if result.lane == lane)


def _live_row(runner: RaceRunner, lane: int) -> LiveRow:
    return next(row for row in runner.snapshot().rows if row.lane == lane)


def _cell(live: LiveRaceView, lane: int, header: str) -> str:
    for row in range(live.table.rowCount()):
        if column_text(live.table, row, "Spur") == str(lane):
            return column_text(live.table, row, header)
    raise AssertionError(f"lane {lane} is not on the live table")
