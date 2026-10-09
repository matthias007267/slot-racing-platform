"""Result tables keep several full rows, and the page scrolls instead of crushing them."""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass

import pytest
from PySide6.QtCore import QPoint
from PySide6.QtGui import QFont, QFontMetrics, QPalette
from PySide6.QtWidgets import QApplication, QScrollArea, QTableWidget, QWidget
from pytestqt.qtbot import QtBot

from slot_racing.core.domain import DriverId, ParticipantResult, RaceId, TrackId
from slot_racing.modules.races.types import RaceInfo
from slot_racing.modules.races.ui.results_view import _VISIBLE_DATA_ROWS, ResultsView
from slot_racing.uikit.theme import apply_theme
from tests.modules.conftest import Env

_FIVE_ROW_TABLES = (
    "measurements_table",
    "bests_table",
    "ranking_table",
)


@dataclass(frozen=True)
class _Appearance:
    style: str
    sheet: str
    palette: QPalette
    font: QFont


@pytest.fixture
def themed() -> Iterator[QApplication]:
    app = QApplication.instance()
    assert isinstance(app, QApplication)
    saved = _capture(app)
    apply_theme(app)
    try:
        yield app
    finally:
        _restore(app, saved)


def test_many_rows_stay_about_five_high_and_the_page_scrolls(
    qtbot: QtBot, env: Env, themed: QApplication
) -> None:
    del themed
    view = _shown(qtbot, env, lanes=4, laps=8, history=1)
    for name in _FIVE_ROW_TABLES:
        table = getattr(view, name)
        assert table.rowCount() > _VISIBLE_DATA_ROWS
        _assert_full_rows(table, _VISIBLE_DATA_ROWS)
        assert table.verticalScrollBar().maximum() > 0
    records = view.records_table
    assert records.rowCount() == 4
    _assert_full_rows(records, 4)
    view.resize(900, 420)
    QApplication.processEvents()
    assert view.results_scroll.verticalScrollBar().maximum() > 0
    for name in _FIVE_ROW_TABLES:
        _assert_full_rows(getattr(view, name), _VISIBLE_DATA_ROWS)
    _assert_full_rows(records, records.rowCount())
    _assert_reaches(view, view.report.chart)
    _assert_reaches(view, view.back_button)


def test_a_short_table_shrinks_and_a_larger_font_grows_the_rows(
    qtbot: QtBot, env: Env, themed: QApplication
) -> None:
    view = _shown(qtbot, env, lanes=2, laps=1)
    short = view.measurements_table
    assert short.rowCount() == 2
    _assert_full_rows(short, 2)
    tall_view = _shown(qtbot, env, lanes=2, laps=8)
    tall = tall_view.measurements_table
    assert tall.rowCount() > _VISIBLE_DATA_ROWS
    assert short.height() < tall.height()

    original = QFont(themed.font())
    before = short.rowHeight(0)
    try:
        for factor in (1.25, 1.5, 2.0):
            _scale_font(themed, view, original, factor)
            QApplication.processEvents()
            assert short.rowHeight(0) > before
            assert short.rowHeight(0) >= short.fontMetrics().height()
            _assert_full_rows(short, 2)
    finally:
        themed.setFont(original)
        view.setFont(original)


def test_window_sizes_do_not_cut_a_result_row(qtbot: QtBot, env: Env, themed: QApplication) -> None:
    del themed
    view = _shown(qtbot, env, lanes=4, laps=7)
    for width, height in ((720, 480), (1280, 800), (1600, 900)):
        view.resize(width, height)
        QApplication.processEvents()
        for name in ("records_table", *_FIVE_ROW_TABLES):
            table = getattr(view, name)
            shown = min(max(table.rowCount(), 1), _VISIBLE_DATA_ROWS)
            _assert_full_rows(table, shown)
        assert isinstance(view.results_scroll, QScrollArea)
        if height <= 480:
            assert view.results_scroll.verticalScrollBar().maximum() > 0


def _shown(qtbot: QtBot, env: Env, *, lanes: int, laps: int, history: int = 0) -> ResultsView:
    track = env.track(f"Heimbahn {lanes}-{laps}-{history}", lanes=lanes)
    for generation in range(history):
        _trial(env, track.id, lanes=lanes, laps=1, tag=f"Historie {generation}")
    race, drivers = _trial(env, track.id, lanes=lanes, laps=laps, tag="Training")
    _finish(env, race.id, drivers, laps)
    view = ResultsView(env.runtime.translator, env.races)
    qtbot.addWidget(view)
    view.resize(1100, 700)
    view.show()
    view.show_race(race.id)
    QApplication.processEvents()
    return view


def _trial(
    env: Env, track_id: TrackId, *, lanes: int, laps: int, tag: str
) -> tuple[RaceInfo, list[tuple[DriverId, int]]]:
    race = env.races.create_time_trial(tag, track_id)
    drivers: list[tuple[DriverId, int]] = []
    for lane in range(1, lanes + 1):
        driver = env.driver(f"{tag} Fahrer {lane}")
        vehicle = env.vehicle(f"{tag} Wagen {lane}", driver_id=driver.id)
        env.races.add_participant(race.id, driver.id, vehicle.id, lane)
        drivers.append((driver.id, lane))
        for lap in range(1, laps + 1):
            env.races.record_lap(
                race.id, lane, lap, 3_000_000_000 + lap * 10_000_000, lap * 3_000_000_000, {}
            )
    return race, drivers


def _finish(env: Env, race_id: RaceId, drivers: list[tuple[DriverId, int]], laps: int) -> None:
    best = 3_010_000_000
    total = laps * best
    env.races.record_finished(
        race_id,
        [
            ParticipantResult(driver_id, lane, place, laps, True, total, best)
            for place, (driver_id, lane) in enumerate(drivers, start=1)
        ],
        aborted=False,
    )


def _assert_full_rows(table: QTableWidget, shown: int) -> None:
    row_height = table.rowHeight(0)
    assert row_height >= table.fontMetrics().height()
    if table.rowCount():
        assert row_height >= table.sizeHintForRow(0)
        for column in range(table.columnCount()):
            item = table.item(0, column)
            if item is not None:
                assert row_height >= QFontMetrics(item.font()).height()
    assert table.viewport().height() + 1 >= shown * row_height
    assert table.viewport().height() < (shown + 1) * row_height
    header = table.horizontalHeader()
    assert header.height() >= header.fontMetrics().height()


def _assert_reaches(view: ResultsView, target: QWidget) -> None:
    content = view.results_scroll.widget()
    assert content is not None
    assert target.isVisibleTo(content)
    top = target.mapTo(content, QPoint(0, 0)).y()
    bar = view.results_scroll.verticalScrollBar()
    bar.setValue(min(bar.maximum(), max(0, top)))
    QApplication.processEvents()
    viewport = view.results_scroll.viewport()
    origin = target.mapTo(viewport, QPoint(0, 0))
    assert origin.y() < viewport.height()
    assert origin.y() + target.height() > 0


def _scale_font(app: QApplication, view: ResultsView, original: QFont, factor: float) -> None:
    scaled = QFont(original)
    point = original.pointSizeF() if original.pointSizeF() > 0 else 10.0
    scaled.setPointSizeF(point * factor)
    app.setFont(scaled)
    view.setFont(scaled)


def _capture(app: QApplication) -> _Appearance:
    style = app.style().objectName()
    if not style:
        class_name = app.style().metaObject().className()
        if "Fusion" in class_name:
            style = "Fusion"
        elif "Windows" in class_name:
            style = "Windows"
    return _Appearance(style, app.styleSheet(), QPalette(app.palette()), QFont(app.font()))


def _restore(app: QApplication, saved: _Appearance) -> None:
    if saved.style:
        app.setStyle(saved.style)
    app.setStyleSheet(saved.sheet)
    app.setPalette(saved.palette)
    app.setFont(saved.font)
