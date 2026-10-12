"""Lap-chart axes, heat bands, tooltips and zoom. Stored results stay unchanged."""

from __future__ import annotations

from PySide6.QtCore import QPoint, QPointF, Qt
from PySide6.QtGui import QWheelEvent
from PySide6.QtWidgets import QApplication, QCheckBox
from pytestqt.qtbot import QtBot
from sqlalchemy import select

from slot_racing.core.domain import ParticipantResult, RaceId, RaceStatus
from slot_racing.core.lap_chart import build_lap_chart
from slot_racing.core.statistics import race_report
from slot_racing.modules.races.models import Lap, RaceHeat, RaceHeatEntry, Sector
from slot_racing.modules.races.types import RaceInfo
from slot_racing.uikit.chart import ChartPoint, ChartRun, ChartSeries, LineChart, driver_color
from slot_racing.uikit.report_view import RaceReportView
from tests.core.test_lap_chart import _heat, _person, _race, _times, _two_by_six
from tests.modules.conftest import Env

_S = 1_000_000_000


def test_the_chart_labels_laps_inside_each_heat_and_times_in_seconds(
    qtbot: QtBot, env: Env
) -> None:
    view = _show(qtbot, env, _two_by_six())
    chart = view.chart
    assert chart.run_labels() == ("Durchlauf 1", "Durchlauf 2")
    assert chart.x_tick_labels() == ("1", "2", "3", "4", "5", "6") * 2
    assert view.chart_note.isHidden()
    assert all(label.endswith(" s") for label in chart.y_tick_labels())
    assert _seconds(chart.y_tick_labels())
    matthias = chart.series[0]
    assert matthias.name == "Matthias"
    assert matthias.color == driver_color(1)
    assert {point.run_number for point in matthias.points} == {1, 2}
    seventh = next(point for point in matthias.points if point.x == 7)
    assert "Durchlauf: 2" in seventh.tip
    assert "Spur: 1" in seventh.tip
    assert "Runde im Durchlauf: 1" in seventh.tip
    assert "Gesamtrunde: 7" in seventh.tip
    assert "Rundenzeit: 0:05.123" in seventh.tip
    assert "Fahrzeug: Porsche" in seventh.tip
    assert "<b>Matthias</b>" in seventh.tip
    assert chart.tip_at(_dot(chart, 7, 5.123)) == seventh.tip
    odd, even = _band_colors(chart)
    assert odd != even


def test_hiding_a_driver_keeps_heat_marks_colors_and_rescales_the_times(
    qtbot: QtBot, env: Env
) -> None:
    view = _show(qtbot, env, _two_by_six())
    chart = view.chart
    runs = chart.runs
    slow = view.findChild(QCheckBox, "race-report-series-2")
    assert slow is not None
    full_scale = _max_tick(chart)
    slow.setChecked(False)
    chart.grab()
    assert chart.runs == runs
    assert chart.run_labels() == ("Durchlauf 1", "Durchlauf 2")
    assert len(chart.series) == 1
    assert chart.series[0].color == driver_color(1)
    assert _max_tick(chart) < full_scale
    slow.setChecked(True)
    chart.grab()
    assert len(chart.series) == 2
    assert chart.series[1].color == driver_color(2)


def test_zoom_changes_the_window_and_not_the_lap_data(qtbot: QtBot, env: Env) -> None:
    view = _show(qtbot, env, _two_by_six())
    chart = view.chart
    before = _plotted(chart)
    place = view.table.item(0, 0)
    assert place is not None
    report_before = place.text()
    chart.zoom_horizontal(0.45, anchor=9)
    window = chart.x_window()
    assert window is not None
    assert window[1] - window[0] < 12
    chart.grab()
    assert chart.x_tick_labels()
    assert len(chart.x_tick_labels()) < 12
    assert set(chart.x_tick_labels()) <= {"1", "2", "3", "4", "5", "6"}
    assert _plotted(chart) == before
    hovered = chart.tip_at(_dot(chart, 7, 5.123))
    assert "Gesamtrunde: 7" in hovered
    assert "Rundenzeit: 0:05.123" in hovered
    view.reset_zoom.click()
    assert chart.x_window() is None
    assert chart.y_window() is None
    assert _plotted(chart) == before
    place_after = view.table.item(0, 0)
    assert place_after is not None
    assert place_after.text() == report_before


def test_a_long_race_can_zoom_without_dropping_measurements(qtbot: QtBot) -> None:
    points = tuple(
        ChartPoint(index, 4.5 + (index % 5) * 0.1, tip=f"Runde {index}") for index in range(1, 121)
    )
    chart = LineChart()
    qtbot.addWidget(chart)
    chart.resize(860, 320)
    chart.set_series(
        (ChartSeries("Anna", points, driver_color(1)),),
        runs=(ChartRun("Durchlauf 1", 1, 120, 1),),
        axes=True,
    )
    chart.show()
    chart.grab()
    before = _plotted(chart)
    chart.zoom_horizontal(0.2, anchor=80)
    window = chart.x_window()
    assert window is not None
    assert window[1] - window[0] < 40
    chart.grab()
    assert _plotted(chart) == before
    assert chart.tip_at(_dot(chart, 80, 4.5 + (80 % 5) * 0.1)) == "Runde 80"
    _wheel(chart, 120)
    assert chart.x_window() != window
    chart.reset_view()
    assert chart.x_window() is None
    assert _plotted(chart) == before


def test_an_unaligned_race_names_the_absolute_lap_and_says_the_heat_is_unknown(
    qtbot: QtBot, env: Env
) -> None:
    race = _race(
        (_person(1, "Matthias", "Porsche"),),
        _times(1, 1, (5 * _S,) * 4),
        (_heat(1, (1, 1)), _heat(2, (1, 1))),
    )
    view = _show(qtbot, env, race)
    assert not view.chart_note.isHidden()
    assert "Gesamtrunde" in view.chart_note.text()
    assert view.chart.run_labels() == ()
    assert view.chart.x_tick_labels()[:4] == ("1", "2", "3", "4")
    tip = view.chart.series[0].points[0].tip
    assert "Durchlauf: unbekannt" in tip
    assert "Gesamtrunde: 1" in tip
    assert "Fahrzeug: Porsche" in tip


def test_a_plain_chart_keeps_drawing_without_axes(qtbot: QtBot) -> None:
    chart = LineChart()
    qtbot.addWidget(chart)
    chart.resize(240, 180)
    chart.set_series((ChartSeries("Trend", (ChartPoint(1, 8.0), ChartPoint(2, 7.5))),))
    chart.show()
    chart.grab()
    assert chart.x_tick_labels() == ()
    assert chart.y_tick_labels() == ()
    assert chart.run_labels() == ()
    assert chart.x_window() is None


def test_building_the_chart_does_not_change_stored_results(qtbot: QtBot, env: Env) -> None:
    track = env.track("Ring", lanes=2)
    race = env.races.create_race("Bericht", track.id, 3)
    anna = env.driver("Anna")
    ben = env.driver("Ben")
    env.races.add_participant(race.id, anna.id, env.vehicle("Porsche", anna.id).id, 1)
    env.races.add_participant(race.id, ben.id, env.vehicle("BMW", ben.id).id, 2)
    env.races.record_lap(race.id, 1, 1, 5 * _S, 5 * _S, {1: 2 * _S, 2: 3 * _S})
    env.races.record_lap(
        race.id, 1, 2, 5_200_000_000, 10_200_000_000, {1: 2_100_000_000, 2: 3_100_000_000}
    )
    env.races.record_lap(
        race.id, 1, 3, 4_800_000_000, 15 * _S, {1: 1_900_000_000, 2: 2_900_000_000}
    )
    env.races.record_lap(race.id, 2, 1, 6 * _S, 6 * _S, {1: 3 * _S, 2: 3 * _S})
    env.races.record_lap(
        race.id, 2, 2, 5_500_000_000, 11_500_000_000, {1: 2_500_000_000, 2: 3_000_000_000}
    )
    env.races.record_finished(
        race.id,
        [
            ParticipantResult(anna.id, 1, 1, 3, True, 15 * _S, 4_800_000_000),
            ParticipantResult(ben.id, 2, 2, 2, True, 11_500_000_000, 5_500_000_000),
        ],
        aborted=False,
    )
    before_rows = _stored(env, race.id)
    before_results = _result_facts(env, race.id)
    before_laps = _lap_facts(env, race.id)
    loaded = env.races.get_completed(race.id)
    assert loaded is not None
    before_report = race_report(loaded)
    assert before_results[0][2:] == (3, 15 * _S, 4_800_000_000, 4_800_000_000, 5 * _S)
    assert before_laps[0][5] == (2 * _S, 3 * _S)
    chart = build_lap_chart(loaded)
    assert chart.series[0].points[0].absolute_lap == before_laps[0][2]
    view = RaceReportView(env.runtime.translator)
    qtbot.addWidget(view)
    view.show_race(loaded)
    assert race_report(loaded) == before_report
    assert _stored(env, race.id) == before_rows
    assert _result_facts(env, race.id) == before_results
    assert _lap_facts(env, race.id) == before_laps
    again = env.races.get_completed(race.id)
    assert again is not None
    assert race_report(again) == before_report


def test_completed_heats_align_the_chart_and_leave_the_database_unchanged(env: Env) -> None:
    race = _enrolled(env, lanes=2, drivers=3, laps=6)
    for _ in range(3):
        _finish_heat(env, race.id)
    stored = env.races.require_race(race.id)
    assert stored.status is RaceStatus.FINISHED
    before_rows = _stored(env, race.id)
    before_results = _result_facts(env, race.id)
    before_laps = _lap_facts(env, race.id)
    loaded = env.races.get_completed(race.id)
    assert loaded is not None and loaded.heats
    before_report = race_report(loaded)
    chart = build_lap_chart(loaded)
    assert chart.aligned is True
    assert [run.run_number for run in chart.runs] == [1, 2, 3]
    seventh = [
        point for series in chart.series for point in series.points if point.absolute_lap == 7
    ]
    assert len(seventh) == 3
    assert all(
        point.lap_in_run == 1 and point.run_number is not None and point.run_number > 1
        for point in seventh
    )
    assert any(point.run_number == 2 for point in seventh)
    assert race_report(loaded) == before_report
    assert _stored(env, race.id) == before_rows
    assert _result_facts(env, race.id) == before_results
    assert _lap_facts(env, race.id) == before_laps
    assert {row[2] for row in before_laps} >= {1, 7}


def _show(qtbot: QtBot, env: Env, race: object) -> RaceReportView:
    view = RaceReportView(env.runtime.translator)
    qtbot.addWidget(view)
    view.resize(1100, 760)
    view.show_race(race)  # type: ignore[arg-type]
    view.show()
    QApplication.processEvents()
    view.chart.grab()
    return view


def _dot(chart: LineChart, x: float, y: float) -> QPoint:
    frame = chart._frame
    assert frame is not None
    located = frame.place(x, y)
    return QPoint(int(located.x()), int(located.y()))


def _band_colors(chart: LineChart) -> tuple[object, object]:
    frame = chart._frame
    assert frame is not None
    image = chart.grab().toImage()
    ratio = image.devicePixelRatio()

    def sample(data_x: float) -> object:
        located = frame.place(data_x, frame.y0 + (frame.y1 - frame.y0) * 0.04)
        return image.pixelColor(int(located.x() * ratio), int(located.y() * ratio)).rgb()

    return sample(1.5), sample(7.5)


def _seconds(labels: tuple[str, ...]) -> bool:
    assert 2 <= len(labels) <= 6
    values = []
    for label in labels:
        number, unit = label.split(" ")
        assert unit == "s"
        values.append(float(number.replace(",", ".")))
    assert values == sorted(values)
    assert max(values) - min(values) >= 1
    return True


def _max_tick(chart: LineChart) -> float:
    labels = chart.y_tick_labels()
    assert labels
    return max(float(label.split(" ")[0].replace(",", ".")) for label in labels)


def _plotted(chart: LineChart) -> tuple[tuple[float, float, str, int | None], ...]:
    return tuple(
        (point.x, point.y, point.tip, point.run_number)
        for series in chart.series
        for point in series.points
    )


def _wheel(chart: LineChart, steps: int) -> None:
    position = QPointF(chart.width() / 2, chart.height() / 2)
    event = QWheelEvent(
        position,
        position,
        QPoint(0, 0),
        QPoint(0, steps),
        Qt.MouseButton.NoButton,
        Qt.KeyboardModifier.NoModifier,
        Qt.ScrollPhase.NoScrollPhase,
        False,
    )
    QApplication.sendEvent(chart, event)


def _result_facts(env: Env, race_id: RaceId) -> tuple[tuple[object, ...], ...]:
    return tuple(
        (
            row.position,
            row.driver_label,
            row.laps_completed,
            row.total_time_ns,
            row.best_lap_ns,
            row.last_lap_ns,
            row.average_lap_ns,
        )
        for row in env.races.get_results(race_id)
    )


def _lap_facts(
    env: Env, race_id: RaceId
) -> tuple[tuple[int, int, int, int, int, tuple[int, ...]], ...]:
    return tuple(
        (
            lap.participant_id,
            lap.lane,
            lap.lap_number,
            lap.lap_time_ns,
            lap.race_time_ns,
            lap.sector_times_ns,
        )
        for lap in env.races.get_laps(race_id)
    )


def _stored(env: Env, race_id: RaceId) -> tuple[object, ...]:
    with env.runtime.database.session() as session:
        laps = session.scalars(
            select(Lap).where(Lap.race_id == int(race_id)).order_by(Lap.id)
        ).all()
        lap_rows = tuple(
            (
                lap.id,
                lap.participant_id,
                lap.lane,
                lap.lap_number,
                lap.lap_time_ns,
                lap.race_time_ns,
            )
            for lap in laps
        )
        sector_rows = tuple(
            tuple(
                (sector.sector_number, sector.sector_time_ns)
                for sector in session.scalars(
                    select(Sector).where(Sector.lap_id == lap.id).order_by(Sector.sector_number)
                )
            )
            for lap in laps
        )
        heats = session.scalars(
            select(RaceHeat).where(RaceHeat.race_id == int(race_id)).order_by(RaceHeat.sequence)
        ).all()
        heat_rows = []
        for heat in heats:
            entries = session.scalars(
                select(RaceHeatEntry)
                .where(RaceHeatEntry.heat_id == heat.id)
                .order_by(RaceHeatEntry.lane, RaceHeatEntry.participant_id)
            ).all()
            heat_rows.append(
                (
                    heat.sequence,
                    heat.status,
                    tuple((entry.participant_id, entry.lane, entry.state) for entry in entries),
                )
            )
    return lap_rows, sector_rows, tuple(heat_rows)


def _enrolled(env: Env, *, lanes: int, drivers: int, laps: int) -> RaceInfo:
    track = env.track(lanes=lanes)
    race = env.races.create_race("Finale", track.id, laps)
    for index in range(1, drivers + 1):
        driver, vehicle = env.pair(index)
        env.races.enroll_driver(race.id, driver, vehicle)
    env.races.plan_heats(race.id)
    return env.races.require_race(race.id)


def _finish_heat(env: Env, race_id: RaceId) -> None:
    runner = env.controller.start_race(race_id)
    for _ in range(80):
        if not runner.is_active:
            break
        env.clock.advance(1_000_000_000)
        runner.tick()
    assert not runner.is_active
