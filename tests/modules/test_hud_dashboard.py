"""Motorsport HUD: readable panels, live snapshot data, and the existing race controls."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QFontMetrics
from PySide6.QtWidgets import QApplication, QWidget
from pytestqt.qtbot import QtBot

from slot_racing.core.clock import NANOS_PER_SECOND, format_duration
from slot_racing.core.domain import RaceStatus
from slot_racing.modules.races.ui.formatting import EMPTY_DISPLAY, format_lap_progress
from slot_racing.modules.races.ui.hud_widgets import (
    COLUMN_BEST,
    COLUMN_DRIVER,
    COLUMN_LAPS,
    COLUMN_POSITION,
    COLUMN_VEHICLE,
    LEADER_ROLE,
    BestLapWidget,
    DriverHighlightWidget,
    LapProgressWidget,
    LastLapWidget,
    LiveRankingWidget,
    RaceControlsWidget,
    RaceHeaderWidget,
    RaceMessageWidget,
    RaceStatusWidget,
)
from slot_racing.modules.races.ui.live_view import LiveRaceView
from slot_racing.modules.races.ui.races_page import RacesPage
from slot_racing.modules.races.ui.results_view import ResultsView
from slot_racing.uikit import fill_table
from tests.modules.conftest import Env
from tests.modules.test_hud_editor import _translator
from tests.modules.test_live_race_ui import _ready_race
from tests.modules.test_ui_management import column_text, open_page
from tests.support.start_sequence import release_start_lights


def test_lap_progress_shows_the_target_and_an_open_race() -> None:
    assert format_lap_progress(12, 30) == "12 / 30"
    assert format_lap_progress(1, 2) == "1 / 2"
    assert format_lap_progress(0, 0) == EMPTY_DISPLAY
    assert format_lap_progress(4, -1) == "4"


def test_progress_widget_hides_the_bar_without_a_target(qtbot: QtBot) -> None:
    widget = LapProgressWidget(_translator())
    qtbot.addWidget(widget)
    widget.show_counts(12, 30, 11)
    assert widget.laps_label.text() == "12 / 30"
    assert widget.progress.value() == 11
    assert not widget.progress.isHidden()
    widget.show_counts(0, 0, 0)
    assert widget.laps_label.text() == EMPTY_DISPLAY
    assert widget.progress.isHidden()


def test_empty_lap_and_message_widgets_do_not_invent_a_value(qtbot: QtBot) -> None:
    translator = _translator()
    last = LastLapWidget(translator)
    best = BestLapWidget(translator)
    message = RaceMessageWidget(translator)
    qtbot.addWidget(last)
    qtbot.addWidget(best)
    qtbot.addWidget(message)
    assert last.value_label.text() == EMPTY_DISPLAY
    assert best.value_label.text() == EMPTY_DISPLAY
    assert message.message_label.text() == EMPTY_DISPLAY
    assert translator.translate("race.status.ready") == "Bereit"
    assert translator.translate("race.status.running") == "Läuft"
    assert translator.translate("race.status.paused") == "Pausiert"
    assert translator.translate("race.status.finished") == "Beendet"


def test_the_ranking_scrolls_vertically_and_keeps_the_given_order(qtbot: QtBot) -> None:
    ranking = LiveRankingWidget(_translator())
    qtbot.addWidget(ranking)
    names = [f"Fahrer {index:02d}" for index in range(12)]
    fill_table(
        ranking.table,
        [
            (
                str(index + 1),
                name,
                "Porsche",
                "7",
                "1",
                "3",
                "4/10",
                "0:08.000",
                "0:08.000",
                "0:24.000",
                "3/10",
                "Fährt",
            )
            for index, name in enumerate(names)
        ],
    )
    ranking.resize(260, 150)
    ranking.show()
    QApplication.processEvents()
    ranking.present()
    shown = []
    for row in range(12):
        item = ranking.table.item(row, COLUMN_DRIVER)
        assert item is not None
        shown.append(item.text())
    assert shown == names
    leader = ranking.table.item(0, COLUMN_POSITION)
    assert leader is not None and leader.data(LEADER_ROLE) is True and leader.font().bold()
    second = ranking.table.item(1, COLUMN_POSITION)
    assert second is not None and second.data(LEADER_ROLE) is False
    assert ranking.table.verticalScrollBar().maximum() > 0
    assert ranking.table.horizontalScrollBarPolicy() == Qt.ScrollBarPolicy.ScrollBarAlwaysOff
    assert ranking.table.isColumnHidden(COLUMN_VEHICLE)
    assert not ranking.table.isColumnHidden(COLUMN_DRIVER)
    assert not ranking.table.isColumnHidden(COLUMN_LAPS)
    assert not ranking.table.isColumnHidden(COLUMN_BEST)

    ranking.resize(640, 280)
    QApplication.processEvents()
    ranking.present()
    assert not ranking.table.isColumnHidden(COLUMN_VEHICLE)
    assert ranking.table.isColumnHidden(3)

    ranking.resize(1200, 400)
    QApplication.processEvents()
    ranking.present()
    assert not ranking.table.isColumnHidden(3)
    assert not ranking.table.isColumnHidden(COLUMN_BEST)
    _assert_children_inside(ranking)


def _show_narrow_header(qtbot: QtBot, pixel_size: int | None) -> RaceHeaderWidget:
    header = RaceHeaderWidget(_translator())
    qtbot.addWidget(header)
    if pixel_size is not None:
        font = header.font()
        font.setPixelSize(pixel_size)
        header.setFont(font)
    header.name_label.setText("Finale")
    header.header_status.setText("Läuft")
    header.track_label.setText("Heimbahn")
    header.participants_label.setText("6 Teilnehmer")
    header.provider_label.setText("Simulation")
    header.show()
    header.resize(226, 47)
    QApplication.processEvents()
    return header


def _assert_header_facts_fit(header: RaceHeaderWidget) -> None:
    """The stage's rectangle sticks, and every fact is fully visible inside it."""
    assert header.width() == 226
    assert header.height() == 47
    for label in (
        header.header_status,
        header.track_label,
        header.participants_label,
        header.provider_label,
    ):
        assert label.isVisible()
        assert label.width() >= label.fontMetrics().horizontalAdvance(label.text())
        assert label.height() + 1 >= label.fontMetrics().height()
        assert label.x() >= -1
        assert label.y() >= -1
        assert label.x() + label.width() <= header.width() + 1
        assert label.y() + label.height() <= header.height() + 1


def test_narrow_header_keeps_status_track_field_and_timing_source(qtbot: QtBot) -> None:
    _assert_header_facts_fit(_show_narrow_header(qtbot, None))
    # A face as wide as a typical Windows UI font must use the same rectangle.
    _assert_header_facts_fit(_show_narrow_header(qtbot, 18))


def _fill_narrow_ranking(ranking: LiveRankingWidget) -> list[str]:
    names = ["Max Müller", "Anna Berger", "Peter Schmidt", "Thomas Weber", "Lisa König", "Zoe"]
    fill_table(
        ranking.table,
        [
            (
                str(index + 1),
                name,
                "Porsche 911",
                "7",
                "1",
                "0",
                "1/8",
                "-",
                "-",
                "-",
                "0/8",
                "Fährt",
            )
            for index, name in enumerate(names)
        ],
        list(range(1, 7)),
    )
    return names


def _assert_names_and_lap_counts_fit(ranking: LiveRankingWidget, names: list[str]) -> None:
    """Names and lap counts stay whole. The panel keeps the narrow rectangle."""
    assert ranking.width() == 226
    assert ranking.height() == 155
    table = ranking.table
    assert table.horizontalScrollBar().maximum() == 0
    bold = table.font()
    bold.setBold(True)
    name_need = max(QFontMetrics(bold).horizontalAdvance(name) for name in names) + 16
    assert table.columnWidth(COLUMN_DRIVER) >= name_need - 2
    assert not table.isColumnHidden(COLUMN_LAPS)
    lap_need = 0
    for row in range(table.rowCount()):
        item = table.item(row, COLUMN_LAPS)
        assert item is not None
        lap_need = max(lap_need, table.fontMetrics().horizontalAdvance(item.text()))
    assert table.columnWidth(COLUMN_LAPS) >= lap_need + 16
    assert table.isColumnHidden(COLUMN_BEST) or table.columnWidth(COLUMN_BEST) >= 64


def test_narrow_ranking_keeps_names_and_the_lap_count(qtbot: QtBot) -> None:
    ranking = LiveRankingWidget(_translator())
    qtbot.addWidget(ranking)
    names = _fill_narrow_ranking(ranking)
    ranking.show()
    ranking.resize(226, 155)
    QApplication.processEvents()
    ranking.present()
    _assert_names_and_lap_counts_fit(ranking, names)

    wide = LiveRankingWidget(_translator())
    qtbot.addWidget(wide)
    font = wide.table.font()
    font.setPixelSize(18)
    wide.table.setFont(font)
    wide_names = _fill_narrow_ranking(wide)
    wide.show()
    wide.resize(226, 155)
    QApplication.processEvents()
    wide.present()
    _assert_names_and_lap_counts_fit(wide, wide_names)


def test_short_highlight_keeps_the_driver_name(qtbot: QtBot) -> None:
    widget = DriverHighlightWidget(_translator())
    qtbot.addWidget(widget)
    widget.show_driver(
        position="P1",
        name="Max Müller",
        vehicle="Porsche 911",
        lap="1/8",
        last=EMPTY_DISPLAY,
        best=EMPTY_DISPLAY,
    )
    widget.show()
    widget.setFixedSize(116, 87)
    QApplication.processEvents()
    assert widget.height() == 87
    assert widget.name_label.text() == "MAX MÜLLER"
    assert widget.name_label.height() >= 16
    assert not widget.last_label.isVisible()
    assert not widget.best_label.isVisible()
    widget.setFixedSize(155, 180)
    QApplication.processEvents()
    assert widget.last_label.isVisible()
    assert widget.last_label.text().startswith("Letzte\n")
    assert widget.detail.text()


def test_short_status_and_message_keep_a_readable_line(qtbot: QtBot) -> None:
    translator = _translator()
    status = RaceStatusWidget(translator)
    message = RaceMessageWidget(translator)
    qtbot.addWidget(status)
    qtbot.addWidget(message)
    status.status_label.setText("● Läuft")
    message.message_label.setText("Rennen gestartet")
    status.show()
    message.show()
    status.resize(116, 40)
    message.resize(167, 54)
    QApplication.processEvents()
    assert status.status_label.height() + 1 >= status.status_label.fontMetrics().height()
    assert message.message_label.height() + 1 >= message.message_label.fontMetrics().height()
    assert not message.warning.isVisible()


def test_narrow_controls_keep_full_labels_on_two_rows(qtbot: QtBot) -> None:
    controls = RaceControlsWidget(_translator())
    qtbot.addWidget(controls)
    controls.show()
    controls.resize(175, 54)
    QApplication.processEvents()
    _assert_two_rows(controls)
    for button in (
        controls.pause_button,
        controls.resume_button,
        controls.stop_button,
        controls.results_button,
        controls.back_button,
    ):
        assert button.fontMetrics().horizontalAdvance(button.text()) <= button.width()


def test_controls_stay_on_two_rows_when_the_panel_is_small_or_large(qtbot: QtBot) -> None:
    controls = RaceControlsWidget(_translator())
    qtbot.addWidget(controls)
    controls.show()
    for size in ((220, 72), (280, 90), (640, 160)):
        controls.resize(*size)
        QApplication.processEvents()
        _assert_two_rows(controls)
        _assert_children_inside(controls)
    assert controls.pause_button.text() == "Pause"
    assert controls.resume_button.text() == "Fortsetzen"
    assert controls.stop_button.text() == "Abbrechen"
    assert controls.results_button.text() == "Ergebnisse"
    assert controls.back_button.text() == "Zurück"


def test_the_dashboard_follows_a_simulated_race_through_pause_and_finish(
    qtbot: QtBot, env: Env
) -> None:
    live, page = _running_race(qtbot, env)
    runner = live.runner
    assert runner is not None and runner.status is RaceStatus.RUNNING
    assert live.status_label.text() == "● Läuft"
    assert live.messages.message_label.text() == "Rennen gestartet"
    assert live.time_label.text() == format_duration(runner.snapshot().elapsed_ns)
    assert live.lanes.cards[1].lap_label.text() == "1 / 2"
    assert live.pause_button.isEnabled()
    assert not live.resume_button.isEnabled()
    assert live.stop_button.isEnabled()
    assert not live.results_button.isEnabled()
    assert live.back_button.isEnabled()
    assert live.lanes.cards[1].position_label.text() == "P1"
    assert live.lanes.cards[1].driver_label.text() == "Zoe"
    assert "Porsche" in live.lanes.cards[1].vehicle_label.text()
    assert live.lanes.cards[1].last_label.text() == "-"

    env.clock.advance(3 * NANOS_PER_SECOND)
    live.refresh()
    assert live.time_label.text() == "0:03.000"
    assert live.time_label.text() == format_duration(runner.snapshot().elapsed_ns)

    for _ in range(80):
        if any(row.laps_completed >= 1 for row in runner.snapshot().rows):
            break
        env.clock.advance(100_000_000)
        live.refresh()
    snapshot = runner.snapshot()
    leader_row = snapshot.rows[0]
    assert leader_row.laps_completed >= 1
    assert [column_text(live.table, index, "Fahrer") for index in range(2)] == [
        row.driver_label for row in snapshot.rows
    ]
    assert column_text(live.table, 0, "Platz") == "1"
    assert column_text(live.table, 0, "Runden") == str(leader_row.laps_completed)
    card = live.lanes.cards[leader_row.lane]
    last = column_text(live.table, 0, "Letzte Runde")
    assert last == format_duration(leader_row.last_lap_ns)
    assert card.last_label.text() == last
    assert card.driver_label.text() == leader_row.driver_label
    assert card.best_label.text() == last
    assert card.lap_label.text() == f"{leader_row.current_lap} / {snapshot.laps}"
    finished_names = {row.driver_label for row in snapshot.rows if row.laps_completed >= 1}
    assert live.messages.message_label.text() in {
        f"Fahrer {name} hat Runde 1 abgeschlossen" for name in finished_names
    }
    leader = live.table.item(0, 0)
    assert leader is not None and leader.data(LEADER_ROLE) is True

    frozen = live.time_label.text()
    frozen_laps = [column_text(live.table, index, "Runden") for index in range(2)]
    live.pause_button.click()
    assert runner.snapshot().status is RaceStatus.PAUSED
    assert live.status_label.text() == "● Pausiert"
    assert live.messages.message_label.text() == "Rennen pausiert"
    assert not live.pause_button.isEnabled()
    assert live.resume_button.isEnabled()
    assert live.stop_button.isEnabled()
    env.clock.advance(5 * NANOS_PER_SECOND)
    live.refresh()
    assert live.time_label.text() == frozen
    assert [column_text(live.table, index, "Runden") for index in range(2)] == frozen_laps

    live.pause_button.click()
    assert runner.snapshot().status is RaceStatus.PAUSED
    elapsed = runner.snapshot().elapsed_ns
    live.resume_button.click()
    assert runner.snapshot().status is RaceStatus.RUNNING
    assert live.status_label.text() == "● Läuft"
    assert live.messages.message_label.text() == "Rennen fortgesetzt"
    assert live.pause_button.isEnabled()
    assert not live.resume_button.isEnabled()
    env.clock.advance(NANOS_PER_SECOND)
    live.refresh()
    assert live.time_label.text() == format_duration(elapsed + NANOS_PER_SECOND)
    assert live.time_label.text() == format_duration(runner.snapshot().elapsed_ns)

    seen_second_lap = False
    for _ in range(120):
        if column_text(live.table, 0, "Runden") == "2":
            seen_second_lap = True
        if isinstance(page.current_view(), ResultsView):
            break
        env.clock.advance(NANOS_PER_SECOND)
        live.refresh()
    assert seen_second_lap
    assert isinstance(page.current_view(), ResultsView)
    assert live.status_label.text() == "● Beendet"
    assert live.messages.message_label.text() == "Rennen beendet"
    assert not live.pause_button.isEnabled()
    assert not live.resume_button.isEnabled()
    assert not live.stop_button.isEnabled()
    assert live.results_button.isEnabled()
    assert live.back_button.isEnabled()
    finished_at = live.time_label.text()
    assert finished_at == format_duration(runner.snapshot().elapsed_ns)
    assert [column_text(live.table, index, "Fahrer") for index in range(2)] == [
        row.driver_label for row in runner.snapshot().rows
    ]
    assert column_text(live.table, 0, "Platz") == "1"
    assert live.lanes.cards[1].driver_label.text() == "Zoe"
    assert live.lanes.cards[1].best_label.text() != "-"
    assert live.lanes.cards[1].last_label.text() != "-"
    env.clock.advance(5 * NANOS_PER_SECOND)
    live.refresh()
    assert live.time_label.text() == finished_at
    assert not live._timer.isActive()
    live.results_button.click()
    assert isinstance(page.current_view(), ResultsView)
    stored = env.races.get_results(runner.race.id)
    assert column_text(page.results.table, 0, "Fahrer") == stored[0].driver_label


def test_panels_keep_their_content_inside_at_several_sizes(qtbot: QtBot, env: Env) -> None:
    window, live, _page = _shown_race(qtbot, env)
    sizes = ((640, 400), (1100, 700), (1600, 1000), (1800, 520), (720, 1100))
    fonts: list[int] = []
    for width, height in sizes:
        window.resize(width, height)
        QApplication.processEvents()
        assert window.width() == width
        assert window.height() == height
        runner = live.runner
        assert runner is not None
        assert live.time_label.text() == format_duration(runner.snapshot().elapsed_ns)
        _assert_two_rows(live.controls)
        policy = live.ranking.table.horizontalScrollBarPolicy()
        assert policy == Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        assert live.stage.horizontalScrollBarPolicy() == Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        for panel in (
            live.clock,
            live.ranking,
            live.lanes,
            live.status,
            live.messages,
            live.controls,
        ):
            _assert_children_inside(panel)
        fonts.append(live.time_label.font().pixelSize())
    assert fonts[2] > fonts[0]


def _running_race(qtbot: QtBot, env: Env) -> tuple[LiveRaceView, RacesPage]:
    window, live, page = _shown_race(qtbot, env)
    assert window is not None
    return live, page


def _shown_race(qtbot: QtBot, env: Env) -> tuple[QWidget, LiveRaceView, RacesPage]:
    _ready_race(env, laps=2)
    window, page = open_page(qtbot, env, "races")
    assert isinstance(page, RacesPage)
    page.refresh()
    page.table.selectRow(0)
    page.buttons["start"].click()
    release_start_lights(page.live)
    window.show()
    QApplication.processEvents()
    assert isinstance(page.live, LiveRaceView)
    return window, page.live, page


def _assert_two_rows(controls: RaceControlsWidget) -> None:
    pause = controls.pause_button.geometry().center().y()
    resume = controls.resume_button.geometry().center().y()
    stop = controls.stop_button.geometry().center().y()
    results = controls.results_button.geometry().center().y()
    back = controls.back_button.geometry().center().y()
    assert abs(pause - resume) <= 3
    assert abs(stop - results) <= 3
    assert abs(results - back) <= 3
    assert stop > pause
    xs = [
        controls.stop_button.geometry().center().x(),
        controls.results_button.geometry().center().x(),
        controls.back_button.geometry().center().x(),
    ]
    assert xs[0] < xs[1] < xs[2]


def _assert_children_inside(widget: QWidget) -> None:
    for child in widget.children():
        if not isinstance(child, QWidget) or not child.isVisible():
            continue
        geo = child.geometry()
        assert geo.left() >= -1
        assert geo.top() >= -1
        assert geo.right() <= widget.width() + 1
        assert geo.bottom() <= widget.height() + 1
