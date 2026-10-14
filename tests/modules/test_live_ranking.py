"""The live ranking lists every driver enrolled in the current race."""

from __future__ import annotations

from pytestqt.qtbot import QtBot

from slot_racing.core.domain import RaceMode, RaceStatus
from slot_racing.modules.races.hud import factory_layout
from slot_racing.modules.races.hud_scene import HudPresentation, HudViewport, build_hud_scene
from slot_racing.modules.races.runner import LiveRow, RaceRunner, RaceSnapshot
from slot_racing.modules.races.types import RaceInfo
from slot_racing.modules.races.ui.live_view import LiveRaceView
from tests.modules.conftest import Env
from tests.modules.test_ui_management import column_text


def test_five_drivers_stay_visible_across_heats_and_repetitions(qtbot: QtBot, env: Env) -> None:
    env.driver("Unbeteiligt")
    race = _planned(env, drivers=5, repetitions=2)
    names = {person.driver_label for person in race.participants}
    assert len(names) == 5
    assert "Unbeteiligt" not in names

    runner = env.controller.start_race(race.id)
    live, snapshot = _paint(qtbot, env, runner)
    _assert_once(live, names)
    waiting = _rows_where(live, "Spur", "-")
    assert len(waiting) == 3
    for row in waiting:
        assert column_text(live.table, row, "Runden") == "0"
        assert column_text(live.table, row, "Beste Runde") == "-"
        assert column_text(live.table, row, "Letzte Runde") == "-"
        assert column_text(live.table, row, "Gesamtzeit") == "-"
        assert column_text(live.table, row, "Status") == "Wartet"
    seated = {row.driver_label for row in snapshot.rows}
    assert len(seated) == 2
    assert {card.driver_label.text() for card in live.lanes.cards.values()} == seated
    scene = build_hud_scene(
        factory_layout(),
        snapshot,
        HudViewport(1280, 720),
        HudPresentation(translator=env.runtime.translator, mode=RaceMode.LAPS, lane_count=2),
    )
    assert {row.cells[1] for row in scene.ranking.rows} == names
    assert len(scene.ranking.rows) == 5
    assert sum(card.occupied for card in scene.lanes) == 2
    _finish(env, runner)

    runner = env.controller.start_race(race.id)
    standings = runner.snapshot().standings
    _assert_field(standings, names)
    sat_out = [row for row in standings if not row.seated]
    assert any(row.laps_completed > 0 and row.best_lap_ns is not None for row in sat_out)
    assert any(row.laps_completed == 0 and row.best_lap_ns is None for row in standings)
    _finish(env, runner)

    for _ in range(3):
        _finish(env, env.controller.start_race(race.id))
    assert env.races.require_race(race.id).status is RaceStatus.READY

    runner = env.controller.start_race(race.id)
    standings = runner.snapshot().standings
    _assert_field(standings, names)
    assert [row.laps_completed for row in standings] == [2, 2, 2, 2, 2]
    assert all(row.best_lap_ns is not None for row in standings)
    live, _snapshot = _paint(qtbot, env, runner)
    _assert_once(live, names)
    assert _column(live, "Runden") == ["2", "2", "2", "2", "2"]
    assert "-" not in _column(live, "Beste Runde")

    runner.stop()
    assert env.races.require_race(race.id).status is RaceStatus.ABORTED
    restarted = env.races.restart_aborted(race.id)
    assert restarted.id != race.id
    fresh = env.controller.start_race(restarted.id)
    field = fresh.snapshot().standings
    _assert_field(field, {person.driver_label for person in restarted.participants})
    assert [row.laps_completed for row in field] == [0, 0, 0, 0, 0]
    assert all(row.best_lap_ns is None for row in field)
    fresh.stop()


def test_a_time_trial_lists_every_enrolled_driver(env: Env) -> None:
    env.driver("Unbeteiligt")
    track = env.track(lanes=2)
    race = env.races.create_time_trial("Zeit", track.id)
    for index in range(1, 4):
        driver, vehicle = env.pair(index)
        env.races.enroll_driver(race.id, driver, vehicle)
    env.races.plan_heats(race.id)
    names = {person.driver_label for person in env.races.require_race(race.id).participants}
    runner = env.controller.start_race(race.id)
    standings = runner.snapshot().standings
    _assert_field(standings, names)
    assert sum(row.seated for row in standings) == 2
    assert any(not row.seated and row.best_lap_ns is None for row in standings)
    assert "Unbeteiligt" not in {row.driver_label for row in standings}
    runner.stop()


def test_a_free_race_shows_only_the_current_assignment(qtbot: QtBot, env: Env) -> None:
    anna = env.driver("Anna")
    porsche = env.vehicle("Porsche", driver_id=anna.id)
    ben = env.driver("Ben")
    ferrari = env.vehicle("Ferrari", driver_id=ben.id)
    cara = env.driver("Cara")
    lotus = env.vehicle("Lotus", driver_id=cara.id)
    env.driver("Unbeteiligt")
    track = env.track(lanes=2)
    race = env.races.create_free_race("Frei", track.id)
    env.races.set_free_assignments(race.id, ((1, anna.id, porsche.id), (2, ben.id, ferrari.id)))
    runner = env.controller.start_race(race.id)
    live, _snapshot = _paint(qtbot, env, runner)
    assert set(_column(live, "Fahrer")) == {"Anna", "Ben"}
    assert "Cara" not in _column(live, "Fahrer")
    assert "Unbeteiligt" not in _column(live, "Fahrer")
    assert live.table.rowCount() == 2

    assert env.controller.switch_lane(1, cara.id, lotus.id) is True
    live.stage.show_standings(
        runner.snapshot(), mode=RaceMode.FREE, lane_count=runner.race.active_lanes or 2
    )
    assert set(_column(live, "Fahrer")) == {"Cara", "Ben"}
    assert "Anna" not in _column(live, "Fahrer")
    switched = _rows_where(live, "Fahrer", "Cara")
    assert len(switched) == 1
    row = switched[0]
    assert column_text(live.table, row, "Runden") == "0"
    assert column_text(live.table, row, "Beste Runde") == "-"
    assert column_text(live.table, row, "Letzte Runde") == "-"
    assert env.races.get_laps(race.id) == []
    runner.stop()


def _planned(env: Env, *, drivers: int, repetitions: int) -> RaceInfo:
    track = env.track(lanes=2)
    race = env.races.create_race("Feld", track.id, 1, repetitions=repetitions)
    for index in range(1, drivers + 1):
        driver, vehicle = env.pair(index)
        env.races.enroll_driver(race.id, driver, vehicle)
    env.races.plan_heats(race.id)
    return env.races.require_race(race.id)


def _paint(qtbot: QtBot, env: Env, runner: RaceRunner) -> tuple[LiveRaceView, RaceSnapshot]:
    live = LiveRaceView(env.runtime.translator, env.controller, service=env.races)
    qtbot.addWidget(live)
    snapshot = runner.snapshot()
    live.stage.show_standings(
        snapshot, mode=runner.race.mode, lane_count=runner.race.active_lanes or 2
    )
    return live, snapshot


def _finish(env: Env, runner: RaceRunner) -> None:
    for _ in range(40):
        if not runner.is_active:
            break
        env.clock.advance(1_000_000_000)
        runner.tick()
    assert not runner.is_active
    assert env.races.require_race(runner.race.id).status is not RaceStatus.RUNNING


def _column(live: LiveRaceView, header: str) -> list[str]:
    return [column_text(live.table, row, header) for row in range(live.table.rowCount())]


def _rows_where(live: LiveRaceView, header: str, text: str) -> list[int]:
    return [row for row, value in enumerate(_column(live, header)) if value == text]


def _assert_once(live: LiveRaceView, names: set[str]) -> None:
    shown = _column(live, "Fahrer")
    assert len(shown) == len(set(shown)) == len(names)
    assert set(shown) == names
    assert "Unbeteiligt" not in shown
    assert _column(live, "Platz") == [str(place) for place in range(1, len(names) + 1)]


def _assert_field(standings: tuple[LiveRow, ...], names: set[str]) -> None:
    labels = [row.driver_label for row in standings]
    assert len(labels) == len(set(labels)) == len(names)
    assert set(labels) == names
    assert "Unbeteiligt" not in labels
