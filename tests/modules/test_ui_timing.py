"""UI tests for the timing configuration of a track: editor, test mode and wizard."""

from __future__ import annotations

from collections.abc import Callable

from PySide6.QtWidgets import QDialog, QTableWidget
from pytestqt.qtbot import QtBot

from carrera.app.main_window import MainWindow
from carrera.core.catalog import TrackInfo
from carrera.core.domain import TimingSetup
from carrera.core.timing import TimingSetupService
from carrera.modules.tracks.ui import timing_wizard
from carrera.modules.tracks.ui.timing_dialog import PositionDialog
from carrera.modules.tracks.ui.tracks_area import TracksArea
from tests.modules.conftest import Env


def cells(table: QTableWidget, row: int) -> list[str]:
    return [
        item.text() if (item := table.item(row, column)) else ""
        for column in range(table.columnCount())
    ]


def column(table: QTableWidget, index: int) -> list[str]:
    return [cells(table, row)[index] for row in range(table.rowCount())]


def open_area(qtbot: QtBot, env: Env) -> tuple[TracksArea, TrackInfo]:
    track = env.track("Heimbahn")
    window = MainWindow(env.runtime)
    qtbot.addWidget(window)
    window.select("tracks")
    area = window.current_page()
    assert isinstance(area, TracksArea)
    area.tracks_page.refresh()
    area.show_config(track.id)
    return area, track


def dialog_filler(fill: Callable[[PositionDialog], None]) -> Callable[[QDialog], int]:
    def run(dialog: QDialog) -> int:
        assert isinstance(dialog, PositionDialog)
        fill(dialog)
        dialog.accept()
        return int(dialog.result())

    return run


def stored(env: Env, track: TrackInfo) -> TimingSetup | None:
    return env.runtime.services.get(TimingSetupService).get_setup(track.id)


def test_track_page_opens_the_timing_configuration(qtbot: QtBot, env: Env) -> None:
    env.track("Heimbahn")
    window = MainWindow(env.runtime)
    qtbot.addWidget(window)
    window.select("tracks")
    area = window.current_page()
    assert isinstance(area, TracksArea)
    page = area.tracks_page
    page.refresh()
    assert not page.timing_button.isEnabled()
    page.select_id(page._rows[0].id)
    assert page.timing_button.isEnabled()
    page.timing_button.click()
    assert area.current_page() is area.config_view
    area.config_view.back_button.click()
    assert area.current_page() is page


def test_unconfigured_track_shows_the_default_layout(qtbot: QtBot, env: Env) -> None:
    area, track = open_area(qtbot, env)
    view = area.config_view
    assert view.title.text().endswith("Heimbahn")
    assert view.is_default and view.hint.text()
    assert column(view.table, 1) == ["Start/Ziel", "Sektor 1", "Sektor 2"]
    assert column(view.table, 3) == ["start_finish", "sensor_01", "sensor_02"]
    assert stored(env, track) is None


def test_add_a_position(qtbot: QtBot, env: Env) -> None:
    area, _ = open_area(qtbot, env)
    view = area.config_view
    view.add_button.click()
    assert view.row_count() == 4
    assert cells(view.table, 3)[:4] == ["4", "Sektor 3", "Sektor", "sensor_03"]


def test_remove_a_position_but_not_start_finish(qtbot: QtBot, env: Env) -> None:
    area, _ = open_area(qtbot, env)
    view = area.config_view
    view.select_position("start_finish")
    assert not view.remove_button.isEnabled()
    assert not view.up_button.isEnabled()
    view.select_position("sector_1")
    assert view.remove_button.isEnabled()
    view.remove_button.click()
    assert column(view.table, 1) == ["Start/Ziel", "Sektor 1"]
    assert column(view.table, 3) == ["start_finish", "sensor_02"]


def test_move_a_position(qtbot: QtBot, env: Env) -> None:
    area, _ = open_area(qtbot, env)
    view = area.config_view
    view.select_position("sector_2")
    view.up_button.click()
    assert [e.position_id for e in view.draft.entries] == ["start_finish", "sector_2", "sector_1"]
    view.down_button.click()
    assert [e.position_id for e in view.draft.entries][1:] == ["sector_1", "sector_2"]


def test_assign_and_edit_a_sensor(qtbot: QtBot, env: Env) -> None:
    area, _ = open_area(qtbot, env)
    view = area.config_view
    view.select_position("sector_1")

    def fill(dialog: PositionDialog) -> None:
        dialog.name_edit.setText("Kurve")
        dialog.sensor_id_edit.setText("S-17")
        dialog.hardware_id_edit.setText("GPIO 17")

    view.dialog_runner = dialog_filler(fill)
    view.edit_button.click()
    assert cells(view.table, 1) == ["2", "Kurve", "Sektor", "S-17", "", "GPIO 17", "Ja"]


def test_the_sensor_can_be_deactivated(qtbot: QtBot, env: Env) -> None:
    area, track = open_area(qtbot, env)
    view = area.config_view
    view.select_position("sector_2")
    view.toggle_button.click()
    assert cells(view.table, 2)[6] == "Nein"
    view.toggle_button.click()
    assert cells(view.table, 2)[6] == "Ja"
    view.toggle_button.click()
    view.save_button.click()
    assert "inaktiv" in view.status.text()
    saved = stored(env, track)
    assert saved is not None and not saved.is_usable


def test_an_invalid_sensor_edit_is_rejected_with_a_clear_message(qtbot: QtBot, env: Env) -> None:
    area, _ = open_area(qtbot, env)
    view = area.config_view
    view.select_position("sector_1")
    messages: list[str] = []

    def duplicate(dialog: QDialog) -> int:
        assert isinstance(dialog, PositionDialog)
        dialog.sensor_id_edit.setText("start_finish")
        dialog.accept()
        messages.append(dialog.message.text())
        return int(dialog.result())

    view.dialog_runner = duplicate
    view.edit_button.click()
    assert "start_finish" in messages[0] and "doppelt" in messages[0]
    assert column(view.table, 3) == ["start_finish", "sensor_01", "sensor_02"]


def test_an_invalid_configuration_is_not_saved(qtbot: QtBot, env: Env) -> None:
    area, track = open_area(qtbot, env)
    view = area.config_view
    view.add_button.click()
    view.draft.entries[-1].sensor_id = ""
    view.save_button.click()
    assert "Sensor-ID" in view.status.text()
    assert stored(env, track) is None
    view.draft.entries[-1].sensor_id = "sensor_01"
    view.save_button.click()
    assert "doppelt" in view.status.text()
    assert stored(env, track) is None


def test_a_valid_configuration_is_saved_and_shown_again(qtbot: QtBot, env: Env) -> None:
    area, track = open_area(qtbot, env)
    view = area.config_view
    view.add_button.click()
    view.select_position("sector_1")
    view.dialog_runner = dialog_filler(lambda d: d.hardware_id_edit.setText("GPIO 17"))
    view.edit_button.click()
    view.save_button.click()
    assert view.status.text() == "Timing-Konfiguration gespeichert."
    assert not view.is_default and view.hint.text() == ""

    saved = stored(env, track)
    assert saved is not None and len(saved.layout.positions) == 4
    assert saved.sensor_at("sector_1").hardware_id == "GPIO 17"

    area.show_tracks()
    area.show_config(track.id)
    assert column(area.config_view.table, 1) == ["Start/Ziel", "Sektor 1", "Sektor 2", "Sektor 3"]
    assert cells(area.config_view.table, 1)[5] == "GPIO 17"


def test_reset_returns_to_the_default(qtbot: QtBot, env: Env) -> None:
    area, track = open_area(qtbot, env)
    view = area.config_view
    view.add_button.click()
    view.save_button.click()
    assert stored(env, track) is not None
    view.confirm = lambda text: False
    view.reset_button.click()
    assert stored(env, track) is not None
    view.confirm = lambda text: True
    view.reset_button.click()
    assert stored(env, track) is None
    assert view.row_count() == 3 and view.is_default


def test_test_mode_shows_a_simulated_event(qtbot: QtBot, env: Env) -> None:
    area, _ = open_area(qtbot, env)
    area.config_view.test_button.click()
    view = area.test_view
    assert area.current_page() is view
    assert view.count_label.text() == "0" and view.table.rowCount() == 0

    env.clock.advance(1_500_000_000)
    view.trigger_button.click()
    assert view.count_label.text() == "1"
    assert cells(view.table, 0) == ["1", "0,000 s", "sensor_01", "Sektor 1"]
    assert view.last_position_label.text() == "Sektor 1"
    assert view.last_sensor_label.text() == "sensor_01"

    env.clock.advance(2_000_000_000)
    view.trigger_button.click()
    assert view.table.rowCount() == 2
    assert view.last_time_label.text() == "2,000 s"
    assert view.order_label.text() == "Sektor 1 → Sektor 2"
    assert view.order_state_label.text() == "Entspricht dem Layout"

    view.lap_button.click()
    assert view.count_label.text() == "5"
    view.reset_button.click()
    assert view.count_label.text() == "0" and view.table.rowCount() == 0
    view.back_button.click()
    assert area.current_page() is area.config_view


def test_test_mode_uses_the_unsaved_layout_and_does_not_touch_the_database(
    qtbot: QtBot, env: Env
) -> None:
    area, track = open_area(qtbot, env)
    area.config_view.add_button.click()
    area.config_view.test_button.click()
    area.test_view.trigger_lap()
    assert column(area.test_view.table, 3) == ["Sektor 1", "Sektor 2", "Sektor 3", "Start/Ziel"]
    assert stored(env, track) is None


def test_test_mode_refuses_an_invalid_configuration(qtbot: QtBot, env: Env) -> None:
    area, _ = open_area(qtbot, env)
    area.config_view.add_button.click()
    area.config_view.draft.entries[-1].sensor_id = ""
    area.config_view.test_button.click()
    assert area.current_page() is area.config_view
    assert area.config_view.status.text()


def test_test_mode_explains_inactive_sensors(qtbot: QtBot, env: Env) -> None:
    area, _ = open_area(qtbot, env)
    area.config_view.select_position("sector_1")
    area.config_view.toggle_button.click()
    area.config_view.test_button.click()
    view = area.test_view
    view.trigger_button.click()
    assert "deaktiviert" in view.status.text()
    assert view.count_label.text() == "0"


def test_timing_configuration_is_unavailable_without_the_timing_plugin(
    qtbot: QtBot, env: Env
) -> None:
    track = env.track("Heimbahn")
    env.runtime.plugins.disable("timing")
    window = MainWindow(env.runtime)
    qtbot.addWidget(window)
    window.select("tracks")
    area = window.current_page()
    assert isinstance(area, TracksArea)
    area.show_config(track.id)
    view = area.config_view
    assert "nicht aktiv" in view.status.text()
    assert not view.save_button.isEnabled() and not view.add_button.isEnabled()


def test_wizard_leads_through_all_six_steps_and_saves(qtbot: QtBot, env: Env) -> None:
    track = env.track("Heimbahn")
    env.track("Andere")
    window = MainWindow(env.runtime)
    qtbot.addWidget(window)
    window.select("tracks")
    area = window.current_page()
    assert isinstance(area, TracksArea)
    area.show_wizard(track)
    wizard = area.wizard
    assert area.current_page() is wizard
    assert wizard.step == timing_wizard.TRACK
    assert "Schritt 1 von 6" in wizard.step_label.text()
    assert wizard.track_combo.currentText() == "Heimbahn"
    assert not wizard.back_button.isEnabled()

    wizard.next_button.click()
    assert wizard.step == timing_wizard.POSITIONS
    wizard.add_button.click()
    assert wizard.positions_table.rowCount() == 4
    wizard.positions_table.selectRow(1)
    wizard.remove_button.click()
    assert column(wizard.positions_table, 1) == ["Start/Ziel", "Sektor 1", "Sektor 2"]

    wizard.next_button.click()
    assert wizard.step == timing_wizard.SENSORS
    wizard.default_ids_button.click()
    wizard.sensors_table.selectRow(1)
    wizard.dialog_runner = dialog_filler(lambda d: d.hardware_id_edit.setText("GPIO 5"))
    wizard.assign_button.click()
    assert column(wizard.sensors_table, 1) == ["start_finish", "sensor_01", "sensor_02"]
    assert column(wizard.sensors_table, 2) == ["", "GPIO 5", ""]

    wizard.next_button.click()
    assert wizard.step == timing_wizard.ORDER
    wizard.order_table.selectRow(2)
    wizard.up_button.click()
    assert column(wizard.order_table, 2) == ["start_finish", "sensor_02", "sensor_01"]

    wizard.next_button.click()
    assert wizard.step == timing_wizard.TEST
    wizard.test_view.trigger_button.click()
    assert wizard.test_view.count_label.text() == "1"
    assert wizard.test_view.last_sensor_label.text() == "sensor_02"

    wizard.next_button.click()
    assert wizard.step == timing_wizard.SAVE
    assert wizard.next_button.isHidden() is not False
    assert column(wizard.summary_table, 2) == ["start_finish", "sensor_02", "sensor_01"]
    wizard.save_button.click()

    assert area.current_page() is area.config_view
    saved = stored(env, track)
    assert saved is not None
    assert [p.id for p in saved.layout.positions] == ["start_finish", "sector_3", "sector_2"]
    assert saved.sensor_at("sector_2").hardware_id == "GPIO 5"


def test_wizard_blocks_an_invalid_sensor_step_and_can_be_cancelled(qtbot: QtBot, env: Env) -> None:
    track = env.track("Heimbahn")
    window = MainWindow(env.runtime)
    qtbot.addWidget(window)
    window.select("tracks")
    area = window.current_page()
    assert isinstance(area, TracksArea)
    area.show_wizard(track)
    wizard = area.wizard
    wizard.next_button.click()
    wizard.next_button.click()
    wizard.draft.entries[1].sensor_id = ""
    wizard.next_button.click()
    assert wizard.step == timing_wizard.SENSORS
    assert "Sensor" in wizard.status.text()
    wizard.back_button.click()
    assert wizard.step == timing_wizard.POSITIONS
    wizard.cancel_button.click()
    assert area.current_page() is area.tracks_page
    assert stored(env, track) is None


def test_wizard_needs_a_track(qtbot: QtBot, env: Env) -> None:
    window = MainWindow(env.runtime)
    qtbot.addWidget(window)
    window.select("tracks")
    area = window.current_page()
    assert isinstance(area, TracksArea)
    area.show_wizard()
    area.wizard.next_button.click()
    assert area.wizard.step == timing_wizard.TRACK
    assert "Strecke" in area.wizard.status.text()
