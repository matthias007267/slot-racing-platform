"""UI tests for the master data pages and the race area. They drive widgets through their
public attributes and methods and avoid anything pixel or layout specific."""

from __future__ import annotations

from collections.abc import Callable
from typing import cast

import pytest
from PySide6.QtWidgets import QCheckBox, QComboBox, QDialog, QPushButton, QSpinBox, QTableWidget
from pytestqt.qtbot import QtBot
from sqlalchemy.exc import OperationalError

from slot_racing.app.main_window import MainWindow
from slot_racing.core.domain import RaceStatus
from slot_racing.modules.drivers_vehicles.models import Driver
from slot_racing.modules.drivers_vehicles.service import VehicleInput
from slot_racing.modules.drivers_vehicles.ui.drivers_page import DriverDialog, DriversPage
from slot_racing.modules.drivers_vehicles.ui.vehicles_page import VehicleDialog, VehiclesPage
from slot_racing.modules.races.ui.live_view import LiveRaceView
from slot_racing.modules.races.ui.races_page import RacesPage
from slot_racing.modules.races.ui.results_view import ResultsView
from slot_racing.modules.races.ui.wizard import MODE, NAME, OVERVIEW, PARTICIPANTS, START, TRACK
from slot_racing.modules.tracks.ui.tracks_area import TracksArea
from slot_racing.modules.tracks.ui.tracks_page import TrackDialog, TracksPage
from tests.modules.conftest import Env


def open_page(qtbot: QtBot, env: Env, page_id: str) -> tuple[MainWindow, object]:
    window = MainWindow(env.runtime)
    qtbot.addWidget(window)
    window.select(page_id)
    return window, window.current_page()


def cells(table: QTableWidget, row: int) -> list[str]:
    return [
        item.text() if (item := table.item(row, column)) else ""
        for column in range(table.columnCount())
    ]


def column_text(table: QTableWidget, row: int, header: str) -> str:
    for column in range(table.columnCount()):
        header_item = table.horizontalHeaderItem(column)
        if header_item is not None and header_item.text() == header:
            item = table.item(row, column)
            return "" if item is None else item.text()
    raise AssertionError(f"missing column {header}")


def runner_for(fill: Callable[[QDialog], None]) -> Callable[[QDialog], int]:
    def run(dialog: QDialog) -> int:
        fill(dialog)
        dialog.accept()
        return int(dialog.result())

    return run


@pytest.mark.parametrize(
    ("page_id", "page_type"),
    [
        ("drivers", DriversPage),
        ("vehicles", VehiclesPage),
        ("tracks", TracksArea),
        ("races", RacesPage),
    ],
)
def test_pages_open_from_the_navigation(
    qtbot: QtBot, env: Env, page_id: str, page_type: type
) -> None:
    window, page = open_page(qtbot, env, page_id)
    assert isinstance(page, page_type)
    assert window.current_id() == page_id


def test_driver_page_add_edit_deactivate_delete(qtbot: QtBot, env: Env) -> None:
    env.vehicles.create_vehicle(VehicleInput(name="Start", model="Nr", start_number=7))
    _, page = open_page(qtbot, env, "drivers")
    assert isinstance(page, DriversPage)
    page.refresh()
    assert page.row_count() == 0

    def fill_new(dialog: QDialog) -> None:
        assert isinstance(dialog, DriverDialog)
        dialog.name_edit.setText("Anna Schmidt")
        dialog.display_name_edit.setText("Anni")
        dialog.start_number_edit.setValue(7)

    page.dialog_runner = runner_for(fill_new)
    page.add()
    assert page.row_count() == 1
    assert cells(page.table, 0) == ["Anna Schmidt", "Anni", "7", "Ja"]

    page.select_id(env.drivers.list_drivers()[0].id)

    def fill_edit(dialog: QDialog) -> None:
        assert isinstance(dialog, DriverDialog)
        assert dialog.name_edit.text() == "Anna Schmidt"
        dialog.name_edit.setText("Anna Meier")

    page.dialog_runner = runner_for(fill_edit)
    page.edit_selected()
    assert cells(page.table, 0)[0] == "Anna Meier"

    page.toggle_active_selected()
    assert cells(page.table, 0)[3] == "Nein"
    assert page.toggle_button.text() == "Aktivieren"

    page.confirm = lambda _text: False
    page.delete_selected()
    assert page.row_count() == 1
    page.confirm = lambda _text: True
    page.delete_selected()
    assert page.row_count() == 0


def test_driver_dialog_shows_validation_errors_and_stays_open(qtbot: QtBot, env: Env) -> None:
    env.driver("Anna", number=7)
    _, page = open_page(qtbot, env, "drivers")
    assert isinstance(page, DriversPage)
    messages: list[str] = []

    def fill(dialog: QDialog) -> None:
        assert isinstance(dialog, DriverDialog)
        dialog.name_edit.setText("Ben")
        dialog.start_number_edit.setValue(7)

    def run(dialog: QDialog) -> int:
        fill(dialog)
        dialog.accept()
        assert isinstance(dialog, DriverDialog)
        messages.append(dialog.message.text())
        return int(dialog.result())

    page.dialog_runner = run
    page.add()
    assert messages == ["Die Startnummer 7 ist bereits vergeben."]
    assert len(env.drivers.list_drivers()) == 1

    page.dialog_runner = runner_for(lambda d: cast(DriverDialog, d).name_edit.setText(""))
    messages.clear()
    page.add()
    assert len(env.drivers.list_drivers()) == 1


def test_database_errors_are_shown_instead_of_crashing(
    qtbot: QtBot, env: Env, monkeypatch: pytest.MonkeyPatch
) -> None:
    _, page = open_page(qtbot, env, "drivers")
    assert isinstance(page, DriversPage)

    def broken(*_args: object, **_kwargs: object) -> None:
        raise OperationalError("select", {}, Exception("database is locked"))

    monkeypatch.setattr(env.drivers, "list_drivers", broken)
    page.refresh()
    assert "Datenbank" in page.status.text()


def test_deleting_a_driver_in_use_explains_why(qtbot: QtBot, env: Env) -> None:
    driver_id, vehicle_id = env.pair(1)
    race = env.races.create_race("R", env.track_id(), 2)
    env.races.add_participant(race.id, driver_id, vehicle_id, 1)
    _, page = open_page(qtbot, env, "drivers")
    assert isinstance(page, DriversPage)
    page.refresh()
    page.select_id(driver_id)
    page.confirm = lambda _text: True
    page.delete_selected()
    assert "Deaktivieren Sie ihn" in page.status.text()
    assert page.row_count() == 1


def test_vehicle_page_assigns_and_unassigns_a_driver(qtbot: QtBot, env: Env) -> None:
    driver = env.driver("Anna")
    _, page = open_page(qtbot, env, "vehicles")
    assert isinstance(page, VehiclesPage)
    page.refresh()

    def fill(dialog: QDialog) -> None:
        assert isinstance(dialog, VehicleDialog)
        dialog.name_edit.setText("Rennwagen")
        dialog.model_edit.setText("Porsche 911")
        dialog.driver_combo.setCurrentIndex(dialog.driver_combo.findData(driver.id))

    page.dialog_runner = runner_for(fill)
    page.add()
    assert cells(page.table, 0)[:5] == ["Rennwagen", "Porsche 911", "", "", "Anna"]

    page.select_id(env.vehicles.list_vehicles()[0].id)
    page.unassign_selected()
    assert cells(page.table, 0)[4] == ""
    assert env.vehicles.list_vehicles()[0].driver_id is None


def test_vehicle_dialog_saves_and_clears_scale_and_notes(qtbot: QtBot, env: Env) -> None:
    _, page = open_page(qtbot, env, "vehicles")
    assert isinstance(page, VehiclesPage)

    def fill(dialog: QDialog) -> None:
        assert isinstance(dialog, VehicleDialog)
        dialog.name_edit.setText("Rennwagen")
        dialog.model_edit.setText("911")
        dialog.scale_edit.setText("1:32")
        dialog.notes_edit.setPlainText("neue Reifen")

    page.dialog_runner = runner_for(fill)
    page.add()
    stored = env.vehicles.list_vehicles()[0]
    assert (stored.scale, stored.notes) == ("1:32", "neue Reifen")

    page.select_id(stored.id)

    def edit(dialog: QDialog) -> None:
        assert isinstance(dialog, VehicleDialog)
        assert dialog.scale_edit.text() == "1:32"
        assert dialog.notes_edit.toPlainText() == "neue Reifen"
        dialog.scale_edit.setText("")
        dialog.notes_edit.setPlainText("   ")

    page.dialog_runner = runner_for(edit)
    page.edit_selected()
    cleared = env.vehicles.get_vehicle(stored.id)
    assert cleared is not None
    assert cleared.scale is None and cleared.notes is None


def test_driver_page_shows_the_selected_drivers_vehicles(qtbot: QtBot, env: Env) -> None:
    anna = env.driver("Anna")
    ben = env.driver("Ben")
    solo = env.driver("Solo")
    env.vehicle("Porsche", driver_id=anna.id)
    env.vehicle("Ferrari", driver_id=ben.id)
    _, page = open_page(qtbot, env, "drivers")
    assert isinstance(page, DriversPage)
    page.refresh()
    assert page.vehicles_table.rowCount() == 0
    assert page.vehicles_empty.text() == "Wählen Sie einen Fahrer aus, um seine Fahrzeuge zu sehen."

    page.select_id(anna.id)
    assert cells(page.vehicles_table, 0) == ["Porsche", "911"]
    assert page.vehicles_empty.text() == ""

    page.select_id(ben.id)
    assert cells(page.vehicles_table, 0) == ["Ferrari", "911"]

    page.select_id(solo.id)
    assert page.vehicles_table.rowCount() == 0
    assert page.vehicles_empty.text() == "Diesem Fahrer ist kein Fahrzeug zugeordnet."


def test_start_number_picker_offers_only_defined_numbers(qtbot: QtBot, env: Env) -> None:
    with env.runtime.database.session() as session:
        session.add(Driver(name="Bee", start_number="B", is_active=True))
    env.vehicles.create_vehicle(VehicleInput(name="Wagen", model="GT", start_number=7))
    _, page = open_page(qtbot, env, "drivers")
    assert isinstance(page, DriversPage)
    seen: dict[str, object] = {}

    def inspect(dialog: QDialog) -> int:
        assert isinstance(dialog, DriverDialog)
        picker = dialog.start_number_edit
        assert dialog.findChild(QSpinBox) is None
        seen["choices"] = picker.choices()
        picker.setValue(5000)
        seen["blocked"] = picker.value()
        picker.setValue("B")
        seen["selected"] = picker.value()
        down = dialog.findChild(QPushButton, "driver-start-number-down")
        assert down is not None
        down.click()
        seen["after_down"] = picker.value()
        return int(QDialog.DialogCode.Rejected)

    page.dialog_runner = inspect
    page.add()
    assert seen == {
        "choices": ["7", "B"],
        "blocked": None,
        "selected": "B",
        "after_down": 7,
    }


def test_wizard_offers_every_vehicle_and_suggests_the_favorite(qtbot: QtBot, env: Env) -> None:
    track = env.track("Heimbahn", lanes=2)
    anna = env.driver("Anna")
    ben = env.driver("Ben")
    porsche = env.vehicle("Porsche", driver_id=anna.id)
    spare = env.vehicle("Ersatz")
    ferrari = env.vehicle("Ferrari", driver_id=ben.id)
    alfa = env.vehicles.create_vehicle(
        VehicleInput(name="Alfa", model="Giulia", driver_id=anna.id, is_favorite=True)
    )
    _, page = open_page(qtbot, env, "races")
    assert isinstance(page, RacesPage)
    page.new_race()
    wizard = page.wizard
    wizard.name_edit.setText("Finale")
    assert wizard.go_next()
    wizard.track_combo.setCurrentIndex(wizard.track_combo.findData(track.id))
    assert wizard.go_next()
    assert wizard.go_next()
    assert wizard.step == PARTICIPANTS
    every = {porsche.id, spare.id, ferrari.id, alfa.id}

    def offered(driver_id: int) -> set[object]:
        wizard.driver_combo.setCurrentIndex(wizard.driver_combo.findData(driver_id))
        return {
            wizard.vehicle_combo.itemData(index) for index in range(wizard.vehicle_combo.count())
        }

    assert offered(anna.id) == every
    assert wizard.vehicle_combo.currentData() == alfa.id
    wizard.vehicle_combo.setCurrentIndex(wizard.vehicle_combo.findData(ferrari.id))
    wizard.lane_combo.setCurrentIndex(wizard.lane_combo.findData(1))
    assert wizard.add_participant()
    assert wizard.race is not None
    stored = env.races.require_race(wizard.race.id)
    assert stored.participants[0].driver_id == anna.id
    assert stored.participants[0].vehicle_id == ferrari.id

    assert offered(ben.id) == every
    assert wizard.vehicle_combo.currentData() == ferrari.id


def test_vehicle_dialog_saves_a_favorite_only_with_a_driver(qtbot: QtBot, env: Env) -> None:
    driver = env.driver("Anna")
    _, page = open_page(qtbot, env, "vehicles")
    assert isinstance(page, VehiclesPage)

    def fill(dialog: QDialog) -> None:
        assert isinstance(dialog, VehicleDialog)
        favorite = dialog.findChild(QCheckBox, "vehicle-favorite")
        assert favorite is not None
        assert not favorite.isEnabled()
        dialog.name_edit.setText("Rennwagen")
        dialog.model_edit.setText("Porsche 911")
        dialog.driver_combo.setCurrentIndex(dialog.driver_combo.findData(driver.id))
        assert favorite.isEnabled()
        favorite.setChecked(True)
        dialog.driver_combo.setCurrentIndex(0)
        assert not favorite.isChecked()
        dialog.driver_combo.setCurrentIndex(dialog.driver_combo.findData(driver.id))
        favorite.setChecked(True)

    page.dialog_runner = runner_for(fill)
    page.add()
    stored = env.vehicles.list_vehicles()[0]
    assert stored.is_favorite and stored.driver_id == driver.id
    assert cells(page.table, 0)[5] == "Ja"


def test_vehicle_dialog_requires_a_model(qtbot: QtBot, env: Env) -> None:
    _, page = open_page(qtbot, env, "vehicles")
    assert isinstance(page, VehiclesPage)
    page.dialog_runner = runner_for(lambda d: cast(VehicleDialog, d).name_edit.setText("Auto"))
    page.add()
    assert env.vehicles.list_vehicles() == []


def test_track_page_validates_the_lane_count(qtbot: QtBot, env: Env) -> None:
    _, area = open_page(qtbot, env, "tracks")
    assert isinstance(area, TracksArea)
    page: TracksPage = area.tracks_page
    page.refresh()
    seen: list[str] = []

    def invalid(dialog: QDialog) -> int:
        assert isinstance(dialog, TrackDialog)
        dialog.name_edit.setText("Zu viele Spuren")
        dialog.lane_count_edit.setValue(9)
        dialog.accept()
        seen.append(dialog.message.text())
        return int(dialog.result())

    page.dialog_runner = invalid
    page.add()
    assert "Spurenzahl" in seen[0]
    assert env.tracks.list_tracks() == []

    def valid(dialog: QDialog) -> None:
        assert isinstance(dialog, TrackDialog)
        dialog.name_edit.setText("Heimbahn")
        dialog.lane_count_edit.setValue(4)

    page.dialog_runner = runner_for(valid)
    page.add()
    assert cells(page.table, 0) == ["Heimbahn", "4", "", "Ja"]


def configure_race(page: RacesPage, env: Env) -> None:
    track = env.track("Heimbahn", lanes=2)
    anna = env.driver("Anna")
    ben = env.driver("Ben")
    porsche = env.vehicle("Porsche", driver_id=anna.id)
    ferrari = env.vehicle("Ferrari", driver_id=ben.id)
    page.new_race()
    wizard = page.wizard
    assert wizard.step == NAME
    wizard.name_edit.setText("Finale")
    assert wizard.go_next()
    assert wizard.step == TRACK
    wizard.track_combo.setCurrentIndex(wizard.track_combo.findData(track.id))
    assert wizard.go_next()
    assert wizard.step == MODE
    wizard.laps_spin.setValue(2)
    assert wizard.go_next()
    assert wizard.step == PARTICIPANTS
    for lane, driver, vehicle in ((1, anna, porsche), (2, ben, ferrari)):
        wizard.driver_combo.setCurrentIndex(wizard.driver_combo.findData(driver.id))
        assert wizard.vehicle_combo.currentData() == vehicle.id  # the driver's own vehicle
        wizard.lane_combo.setCurrentIndex(wizard.lane_combo.findData(lane))
        assert wizard.add_participant()


def test_race_flow_through_the_ui(qtbot: QtBot, env: Env) -> None:
    _, page = open_page(qtbot, env, "races")
    assert isinstance(page, RacesPage)
    page.refresh()
    configure_race(page, env)
    wizard = page.wizard
    assert wizard.participant_table.rowCount() == 2

    assert wizard.go_next()
    assert wizard.step == OVERVIEW
    overview = wizard.overview_label.text()
    assert "Finale" in overview and "Heimbahn" in overview
    assert "Spur 1: Anna auf Porsche" in overview
    assert wizard.go_next()
    assert wizard.step == START
    assert not wizard.next_button.isEnabled()

    wizard.start_button.click()
    assert isinstance(page.current_view(), LiveRaceView)
    live = page.live
    assert live.status_label.text().endswith("Läuft")
    assert live.name_label.text() == "Finale"
    assert live.track_label.text().endswith("Heimbahn")
    assert live.provider_label.text().endswith("Simulation")
    assert live.laps_label.text().endswith("2")
    assert live.table.rowCount() == 2
    assert column_text(live.table, 0, "Fahrer") == "Anna"
    assert column_text(live.table, 0, "Fahrzeug") == "Porsche (911)"

    for _ in range(60):
        env.clock.advance(100_000_000)
        live.refresh()
    assert column_text(live.table, 0, "Aktuelle Runde") == "2/2"
    assert column_text(live.table, 0, "Runden") == "1"
    assert column_text(live.table, 0, "Letzte Runde") != "-"

    live.confirm = lambda _text: False
    live.stop_race()
    assert live.runner is not None and live.runner.is_active
    live.toggle_pause()
    assert live.status_label.text().endswith("Pausiert")
    live.toggle_pause()

    for _ in range(100):
        env.clock.advance(100_000_000)
        live.refresh()
        if not live.runner or not live.runner.is_active:
            break
    assert isinstance(page.current_view(), ResultsView)
    results = page.results
    assert results.table.rowCount() == 2
    assert column_text(results.table, 0, "Platz") == "1"
    assert column_text(results.table, 0, "Fahrer") == "Anna"
    assert column_text(results.table, 0, "Fahrzeug") == "Porsche (911)"
    assert column_text(results.table, 0, "Spur") == "1"
    assert column_text(results.table, 0, "Runden") == "2"
    assert column_text(results.table, 0, "Status") == "Fertig"
    for header in ("Gesamtzeit", "Beste Runde", "Letzte Runde", "Durchschnitt"):
        assert column_text(results.table, 0, header) != "-"
    assert results.laps_table.rowCount() == 4

    results.back_button.click()
    assert page.current_view() is page.list_page
    assert cells(page.table, 0)[:3] == ["Finale", "Heimbahn", "Beendet"]


def test_wizard_updates_vehicle_and_lane_on_the_same_participant(qtbot: QtBot, env: Env) -> None:
    _, page = open_page(qtbot, env, "races")
    assert isinstance(page, RacesPage)
    configure_race(page, env)
    spare = env.vehicle("Ersatz")
    wizard = page.wizard
    assert wizard.race is not None
    original = wizard.race.participants[0]
    wizard.participant_table.selectRow(0)
    assert wizard.edit_selected_participant()
    assert wizard.edit_participant_button.text() == "Teilnehmer aktualisieren"
    wizard.vehicle_combo.setCurrentIndex(wizard.vehicle_combo.findData(spare.id))
    wizard.lane_combo.setCurrentIndex(wizard.lane_combo.findData(original.lane))
    assert wizard.edit_selected_participant()
    stored = env.races.require_race(wizard.race.id)
    updated = next(item for item in stored.participants if item.id == original.id)
    assert updated.vehicle_id == spare.id
    assert updated.lane == original.lane
    assert updated.driver_id == original.driver_id
    assert len(stored.participants) == 2
    assert wizard.edit_participant_button.text() == "Teilnehmer bearbeiten"


def test_wizard_reports_rule_violations_and_keeps_going(qtbot: QtBot, env: Env) -> None:
    _, page = open_page(qtbot, env, "races")
    assert isinstance(page, RacesPage)
    configure_race(page, env)
    wizard = page.wizard

    wizard.lane_combo.setCurrentIndex(wizard.lane_combo.findData(1))
    assert not wizard.add_participant()
    assert "Spuren" in wizard.status.text()  # track is full
    assert wizard.participant_table.rowCount() == 2

    wizard.participant_table.selectRow(0)
    assert wizard.remove_selected_participant()
    assert wizard.participant_table.rowCount() == 1

    wizard.driver_combo.setCurrentIndex(
        wizard.driver_combo.findData(env.drivers.list_drivers()[0].id)
    )
    wizard.lane_combo.setCurrentIndex(wizard.lane_combo.findData(2))
    assert not wizard.add_participant()  # lane 2 is taken
    assert "Spur 2 ist bereits vergeben" in wizard.status.text()


def test_wizard_shows_lap_racing_as_the_only_mode(qtbot: QtBot, env: Env) -> None:
    env.track("Heimbahn", lanes=2)
    _, page = open_page(qtbot, env, "races")
    assert isinstance(page, RacesPage)
    page.new_race()
    wizard = page.wizard
    wizard.name_edit.setText("Finale")
    assert wizard.go_next()
    wizard.track_combo.setCurrentIndex(0)
    assert wizard.go_next()
    assert wizard.step == MODE
    assert wizard.mode_value.text() == "Rundenrennen"
    assert wizard.findChild(QComboBox, "race-mode") is None


def test_wizard_blocks_empty_steps(qtbot: QtBot, env: Env) -> None:
    _, page = open_page(qtbot, env, "races")
    assert isinstance(page, RacesPage)
    page.new_race()
    wizard = page.wizard
    assert not wizard.go_next()
    assert "Namen" in wizard.status.text()
    wizard.name_edit.setText("R")
    assert wizard.go_next()
    assert not wizard.go_next()  # no track exists
    assert "Strecke" in wizard.status.text()
    wizard.go_back()
    assert wizard.step == NAME


def test_starting_without_participants_shows_the_reason(qtbot: QtBot, env: Env) -> None:
    race = env.races.create_race("Leer", env.track_id(), 2)
    _, page = open_page(qtbot, env, "races")
    assert isinstance(page, RacesPage)
    page.refresh()
    page.table.selectRow(0)
    page.start_selected()
    assert "keine Teilnehmer" in page.status.text()
    assert page.current_view() is page.list_page
    assert env.races.require_race(race.id).status is RaceStatus.CREATED


def test_race_page_without_timing_module_reports_instead_of_crashing(
    qtbot: QtBot, env: Env
) -> None:
    driver_id, vehicle_id = env.pair(1)
    race = env.races.create_race("R", env.track_id(), 2)
    env.races.add_participant(race.id, driver_id, vehicle_id, 1)
    env.runtime.plugins.disable("timing")
    _, page = open_page(qtbot, env, "races")
    assert isinstance(page, RacesPage)
    page.refresh()
    page.table.selectRow(0)
    page.start_selected()
    assert "Zeitmessung" in page.status.text()


def test_navigation_hides_races_when_a_required_module_is_disabled(qtbot: QtBot, env: Env) -> None:
    window, _ = open_page(qtbot, env, "races")
    env.runtime.plugins.disable("tracks")
    assert "races" not in window.navigation_ids()
    assert "tracks" not in window.navigation_ids()
    assert window.current_id() == "dashboard"
