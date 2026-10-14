"""The live ranking stays on screen between heats and is part of the HUD layout."""

from __future__ import annotations

from dataclasses import replace

from PySide6.QtCore import QPoint, QRect
from PySide6.QtWidgets import QApplication, QTableWidget
from pytestqt.qtbot import QtBot

from slot_racing.core.domain import RaceId, RaceMode, RaceStatus
from slot_racing.modules.races.hud import (
    BETWEEN_RANKING_FONT_BASE,
    VIEW_BETWEEN,
    HudConfigurationStore,
    between_ranking_pixels,
    effective_px,
    factory_layout,
    visible_ranking_columns,
)
from slot_racing.modules.races.hud_scene import HudPresentation, HudViewport, build_hud_scene
from slot_racing.modules.races.runner import LiveRow, RaceRunner, RaceSnapshot
from slot_racing.modules.races.types import RaceInfo
from slot_racing.modules.races.ui.between_ranking import BetweenSurface
from slot_racing.modules.races.ui.live_view import LiveRaceView
from tests.database import migrated_database
from tests.modules.conftest import Env
from tests.modules.test_hud_editor import _shown
from tests.modules.test_ui_management import column_text


def test_five_drivers_stay_ranked_between_three_repetitions(qtbot: QtBot, env: Env) -> None:
    env.driver("Unbeteiligt")
    race = _planned(env)
    names = {person.driver_label for person in race.participants}
    live = LiveRaceView(env.runtime.translator, env.controller, service=env.races)
    qtbot.addWidget(live)
    live.resize(1280, 860)
    live.show()
    after_five: dict[str, int] = {}
    after_ten: dict[str, int] = {}
    for heat in range(1, 16):
        runner = env.controller.start_race(race.id)
        _finish(env, runner)
        live.show_runner(runner)
        QApplication.processEvents()
        snapshot = runner.snapshot()
        if heat == 15:
            assert env.races.require_race(race.id).status is RaceStatus.FINISHED
            assert live.heat_gate.isHidden()
            break
        assert env.races.require_race(race.id).status is RaceStatus.READY
        assert not live.heat_gate.isHidden()
        _assert_same_ranking(live, snapshot, names)
        laps = _lap_map(live.heat_gate.ranking.table)
        if heat == 1:
            missing = [
                column_text(live.heat_gate.ranking.table, row, "Beste Runde") for row in range(5)
            ]
            assert "-" in missing
            _assert_gate_stays_usable(live)
        if heat == 5:
            assert set(laps.values()) == {2}
            after_five = laps
            _assert_scene_matches(env, snapshot, race.id)
        if heat == 6:
            _assert_increased_only_for_seated(laps, after_five, snapshot)
        if heat == 10:
            assert set(laps.values()) == {4}
            after_ten = laps
        if heat == 11:
            _assert_increased_only_for_seated(laps, after_ten, snapshot)
        briefing_rows = live.heat_gate.ranking.table.rowCount()
        briefing = env.races.heat_briefing(race.id)
        assert briefing is not None
        live.heat_gate.show_briefing(briefing)
        assert live.heat_gate.ranking.table.rowCount() == briefing_rows
        assert _lap_map(live.heat_gate.ranking.table) == laps


def test_the_between_ranking_is_edited_in_the_hud_and_kept(qtbot: QtBot, env: Env) -> None:
    store = HudConfigurationStore(migrated_database())
    editor = _shown(qtbot, store)
    editor.select_view(VIEW_BETWEEN)
    qtbot.waitUntil(lambda: editor.between.surface.width() > 200)
    frame = editor.selected_layout().between_ranking
    editor.set_between_ranking(
        replace(frame, x=0.08, y=0.12, width=0.40, height=0.55, font_scale=150)
    )
    editor.move_ranking_column("best_lap", -1)
    editor.set_ranking_column_visible("status", False)
    editor.set_ranking_column_visible("vehicle", True)
    preview = editor.between
    surface = preview.surface
    QApplication.processEvents()
    placed = between_ranking_pixels(
        editor.selected_layout().between_ranking, surface.width(), surface.height()
    )
    assert preview.ranking.geometry() == QRect(placed.x, placed.y, placed.width, placed.height)
    assert preview.ranking.table.font().pixelSize() == effective_px(
        BETWEEN_RANKING_FONT_BASE, 150, 100
    )
    headers = _headers(preview.ranking.table)
    assert headers.index("Beste Runde") == headers.index("Letzte Runde") - 1
    assert "Status" not in headers
    assert "Fahrzeug" in headers
    assert {column_text(preview.ranking.table, row, "Fahrer") for row in range(5)} == {
        "Anna",
        "Ben",
        "Chris",
        "Dana",
        "Erik",
    }
    erik = _row_named(preview.ranking.table, "Erik")
    assert column_text(preview.ranking.table, erik, "Beste Runde") == "-"
    assert column_text(preview.ranking.table, erik, "Letzte Runde") == "-"
    editor.save()
    reloaded = _shown(qtbot, store)
    reloaded.select_view(VIEW_BETWEEN)
    assert reloaded.selected_layout().between_ranking == editor.selected_layout().between_ranking
    assert _headers(reloaded.between.ranking.table) == headers

    race = _planned(env)
    live = LiveRaceView(env.runtime.translator, env.controller, store=store, service=env.races)
    qtbot.addWidget(live)
    live.resize(1280, 860)
    live.show()
    runner = env.controller.start_race(race.id)
    _finish(env, runner)
    live.show_runner(runner)
    QApplication.processEvents()
    qtbot.waitUntil(lambda: live.heat_gate.surface.width() > 200)
    assert not live.heat_gate.isHidden()
    assert _headers(live.heat_gate.ranking.table) == headers
    gate_rect = between_ranking_pixels(
        editor.selected_layout().between_ranking,
        live.heat_gate.surface.width(),
        live.heat_gate.surface.height(),
    )
    assert live.heat_gate.ranking.geometry() == QRect(
        gate_rect.x, gate_rect.y, gate_rect.width, gate_rect.height
    )
    preview.surface.resize(live.heat_gate.surface.size())
    QApplication.processEvents()
    assert preview.ranking.geometry() == live.heat_gate.ranking.geometry()
    assert (
        live.heat_gate.ranking.table.font().pixelSize() == preview.ranking.table.font().pixelSize()
    )
    assert live.heat_gate.start_button.isEnabled()


def test_many_drivers_scroll_instead_of_losing_rows(qtbot: QtBot, env: Env) -> None:
    surface = BetweenSurface(env.runtime.translator)
    qtbot.addWidget(surface)
    names = [f"Fahrer {index}" for index in range(1, 13)]
    rows = tuple(
        LiveRow(
            position=index,
            lane=0,
            driver_label=name,
            vehicle_label="Wagen mit einem langen Namen",
            start_number=index,
            current_lap=0,
            laps_completed=0,
            last_lap_ns=None,
            total_time_ns=None,
            best_lap_ns=None,
            finished=False,
            lap_times_ns=(),
            participant_id=index,
            seated=False,
        )
        for index, name in enumerate(names, start=1)
    )
    snapshot = RaceSnapshot(
        race_id=RaceId(1),
        name="Feld",
        track_name="Heimbahn",
        timing_provider="simulation",
        status=RaceStatus.FINISHED,
        aborted=False,
        laps=1,
        elapsed_ns=0,
        rows=(),
        source_errors=(),
        standings=rows,
    )
    surface.resize(220, 110)
    surface.show()
    surface.show_standings(snapshot, factory_layout().between_ranking)
    QApplication.processEvents()
    table = surface.ranking.table
    assert table.rowCount() == 12
    assert {column_text(table, row, "Fahrer") for row in range(12)} == set(names)
    assert table.verticalScrollBar().maximum() > 0
    assert all(not table.isColumnHidden(column) for column in range(table.columnCount()))
    assert table.horizontalScrollBar().maximum() > 0
    assert visible_ranking_columns(factory_layout().between_ranking) == tuple(
        _header_id(table, column) for column in range(table.columnCount())
    )


def _planned(env: Env) -> RaceInfo:
    track = env.track(lanes=2)
    race = env.races.create_race("Feld", track.id, 1, repetitions=3)
    for index in range(1, 6):
        driver, vehicle = env.pair(index)
        env.races.enroll_driver(race.id, driver, vehicle)
    env.races.plan_heats(race.id)
    return env.races.require_race(race.id)


def _finish(env: Env, runner: RaceRunner) -> None:
    for _ in range(40):
        if not runner.is_active:
            break
        env.clock.advance(1_000_000_000)
        runner.tick()
    assert not runner.is_active


def _assert_same_ranking(live: LiveRaceView, snapshot: RaceSnapshot, names: set[str]) -> None:
    table = live.heat_gate.ranking.table
    shown = [column_text(table, row, "Fahrer") for row in range(table.rowCount())]
    assert shown == [row.driver_label for row in snapshot.standings]
    assert len(shown) == len(set(shown)) == 5
    assert set(shown) == names
    assert "Unbeteiligt" not in shown
    assert [column_text(table, row, "Platz") for row in range(5)] == ["1", "2", "3", "4", "5"]
    live_names = [column_text(live.table, row, "Fahrer") for row in range(live.table.rowCount())]
    assert live_names == shown
    for row, standing in enumerate(snapshot.standings):
        assert column_text(table, row, "Runden") == str(standing.laps_completed)
        assert column_text(table, row, "Beste Runde") == column_text(live.table, row, "Beste Runde")
        assert column_text(table, row, "Letzte Runde") == column_text(
            live.table, row, "Letzte Runde"
        )
        assert column_text(table, row, "Gesamtzeit") == column_text(live.table, row, "Gesamtzeit")


def _assert_increased_only_for_seated(
    laps: dict[str, int], before: dict[str, int], snapshot: RaceSnapshot
) -> None:
    seated = {row.driver_label for row in snapshot.rows}
    assert seated
    assert set(laps) == set(before)
    for name, count in laps.items():
        assert count == before[name] + (1 if name in seated else 0)


def _assert_scene_matches(env: Env, snapshot: RaceSnapshot, race_id: RaceId) -> None:
    briefing = env.races.heat_briefing(race_id)
    assert briefing is not None
    scene = build_hud_scene(
        factory_layout(),
        snapshot,
        HudViewport(1280, 720),
        HudPresentation(
            translator=env.runtime.translator,
            mode=RaceMode.LAPS,
            lane_count=2,
            view=VIEW_BETWEEN,
            briefing=briefing,
        ),
    )
    assert scene.briefing is not None
    panel = scene.panel("ranking")
    assert panel.width > 0 and panel.height > 0
    assert panel.x + panel.width <= scene.viewport.width
    assert panel.y + panel.height <= scene.viewport.height
    assert scene.ranking.scrolls is True
    assert scene.ranking.fit_columns is False
    assert [row.cells[1] for row in scene.ranking.rows] == [
        row.driver_label for row in snapshot.standings
    ]
    assert [row.cells[2] for row in scene.ranking.rows] == [
        str(row.laps_completed) for row in snapshot.standings
    ]


def _assert_gate_stays_usable(live: LiveRaceView) -> None:
    gate = live.heat_gate
    QApplication.processEvents()
    assert gate.start_button.isEnabled()
    assert gate.postpone_button.isEnabled()
    assert gate.disqualify_button.isEnabled()
    surface = QRect(gate.surface.mapTo(gate, QPoint(0, 0)), gate.surface.size())
    button = QRect(gate.start_button.mapTo(gate, QPoint(0, 0)), gate.start_button.size())
    assert button.top() >= surface.bottom()
    assert not surface.intersects(button)


def _lap_map(table: QTableWidget) -> dict[str, int]:
    return {
        column_text(table, row, "Fahrer"): int(column_text(table, row, "Runden"))
        for row in range(table.rowCount())
    }


def _headers(table: QTableWidget) -> list[str]:
    labels: list[str] = []
    for column in range(table.columnCount()):
        item = table.horizontalHeaderItem(column)
        assert item is not None
        labels.append(item.text())
    return labels


def _row_named(table: QTableWidget, name: str) -> int:
    for row in range(table.rowCount()):
        if column_text(table, row, "Fahrer") == name:
            return row
    raise AssertionError(name)


def _header_id(table: QTableWidget, column: int) -> str:
    labels = {
        "Platz": "position",
        "Fahrer": "driver",
        "Runden": "laps",
        "Letzte Runde": "last_lap",
        "Beste Runde": "best_lap",
        "Gesamtzeit": "total_time",
        "Status": "status",
    }
    item = table.horizontalHeaderItem(column)
    assert item is not None
    return labels[item.text()]
