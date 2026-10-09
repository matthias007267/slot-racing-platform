"""Best lap number and best lap time on the shared lane HUD."""

from __future__ import annotations

from dataclasses import replace

from PySide6.QtCore import QPoint
from PySide6.QtWidgets import QApplication, QComboBox, QLabel, QSpinBox
from pytestqt.qtbot import QtBot

from slot_racing.core.domain import RaceMode
from slot_racing.modules.races.hud import (
    FIELD_BEST_TIME,
    FIELD_IDS,
    FieldStyle,
    HudConfigurationStore,
    effective_px,
    factory_layout,
)
from slot_racing.modules.races.runner import LiveRow
from slot_racing.modules.races.ui.formatting import best_lap_display, fastest_completed_lap
from slot_racing.modules.races.ui.lane_card import LaneCard, card_top
from tests.modules.test_hud_editor import _database, _shown, _translator

_SLOW = 5_000_000_000
_TIED = 3_026_000_000
_FASTER = 2_400_000_000


def test_the_first_fastest_lap_wins_and_a_later_best_replaces_it() -> None:
    assert fastest_completed_lap(()) is None
    assert best_lap_display(()) == ("-", "-")
    assert fastest_completed_lap((_SLOW,)) == (1, _SLOW)
    assert fastest_completed_lap((_SLOW, _TIED, _TIED)) == (2, _TIED)
    assert best_lap_display((_SLOW, _TIED, _TIED)) == ("2", "3,026 s")
    assert fastest_completed_lap((_SLOW, _TIED, _TIED, _FASTER, _FASTER)) == (4, _FASTER)
    assert best_lap_display((_SLOW, _TIED, _TIED, _FASTER)) == ("4", "2,400 s")


def test_the_lane_card_splits_the_lap_number_and_the_lap_time(qtbot: QtBot) -> None:
    card = LaneCard(_translator(), 1)
    qtbot.addWidget(card)
    card.resize(480, 1400)
    card.show()
    QApplication.processEvents()

    card.show_driver(_row(()), laps=8, mode=RaceMode.LAPS, paused=False, ended=False)
    assert card.best_label.text() == "-"
    assert card.best_time_label.text() == "-"
    number_caption = card.findChild(QLabel, "live-lane-1-best-caption")
    time_caption = card.findChild(QLabel, "live-lane-1-best-time-caption")
    assert number_caption is not None and number_caption.text() == "Beste Runde"
    assert time_caption is not None and time_caption.text() == "Beste Rundenzeit"
    assert card_top(card, card.best_label) < card_top(card, card.best_time_label)

    card.show_driver(
        _row((_SLOW, _TIED, _TIED)), laps=8, mode=RaceMode.LAPS, paused=False, ended=False
    )
    assert card.best_label.text() == "2"
    assert card.best_time_label.text() == "3,026 s"
    assert card.best_time_label.isVisibleTo(card)

    card.show_driver(
        _row((_SLOW, _TIED, _TIED, _FASTER)),
        laps=8,
        mode=RaceMode.LAPS,
        paused=False,
        ended=False,
    )
    assert card.best_label.text() == "4"
    assert card.best_time_label.text() == "2,400 s"

    card.show_driver(_row((_TIED,)), laps=0, mode=RaceMode.TIME_TRIAL, paused=False, ended=False)
    assert card.best_label.text() == "1"
    assert card.best_time_label.text() == "3,026 s"
    assert card.best_time_label.isVisibleTo(card)
    assert not card.total_label.isVisibleTo(card)

    before = card.best_time_label.font().pixelSize()
    card.apply_style(replace(factory_layout(), fields=_fields(scale=150)))
    QApplication.processEvents()
    assert card.best_time_label.font().pixelSize() == effective_px(22, 100, 150)
    assert card.best_time_label.font().pixelSize() > before
    card.apply_style(replace(factory_layout(), fields=_fields(display="hide")))
    assert not card.best_time_label.isVisibleTo(card)
    assert card.best_label.isVisibleTo(card)


def test_the_hud_editor_configures_the_best_lap_time(qtbot: QtBot) -> None:
    store = HudConfigurationStore(_database())
    editor = _shown(qtbot, store)
    best = editor.findChild(QComboBox, "hud-display-best")
    display = editor.findChild(QComboBox, "hud-display-best_time")
    scale = editor.findChild(QSpinBox, "hud-scale-best_time")
    assert best is not None and display is not None and scale is not None
    assert best.mapTo(editor, QPoint(0, 0)).y() < display.mapTo(editor, QPoint(0, 0)).y()

    number = editor.findChild(QLabel, "live-lane-1-best")
    timing = editor.findChild(QLabel, "live-lane-1-best-time")
    assert number is not None and timing is not None
    assert number.text() == "2"
    assert timing.text() == "4,050 s"

    scale.setValue(150)
    display.setCurrentIndex(display.findData("hide"))
    QApplication.processEvents()
    assert editor.selected_layout().field(FIELD_BEST_TIME).scale == 150
    assert editor.selected_layout().field(FIELD_BEST_TIME).display == "hide"
    assert not timing.isVisibleTo(editor)
    editor.save()
    loaded = store.load().selected().field(FIELD_BEST_TIME)
    assert loaded == FieldStyle(False, 150, "hide")
    assert store.load().selected().field("best") == FieldStyle()


def _row(times: tuple[int, ...]) -> LiveRow:
    return LiveRow(
        position=1,
        lane=1,
        driver_label="Zoe",
        vehicle_label="Porsche",
        start_number=7,
        current_lap=len(times) + 1,
        laps_completed=len(times),
        last_lap_ns=times[-1] if times else None,
        total_time_ns=sum(times) if times else None,
        best_lap_ns=min(times) if times else None,
        finished=False,
        lap_times_ns=times,
    )


def _fields(*, scale: int = 100, display: str = "auto") -> tuple[tuple[str, FieldStyle], ...]:
    chosen = FieldStyle(display != "hide", scale, display)
    return tuple(
        (field_id, chosen if field_id == FIELD_BEST_TIME else FieldStyle())
        for field_id in FIELD_IDS
    )
