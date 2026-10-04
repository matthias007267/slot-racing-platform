"""Selecting the timing provider in the race configuration."""

from __future__ import annotations

from PySide6.QtGui import QStandardItemModel
from PySide6.QtWidgets import QComboBox
from pytestqt.qtbot import QtBot

from slot_racing.core.domain import RaceStatus
from slot_racing.core.errors import ProviderConfigurationError
from slot_racing.core.timing import ProviderAvailability, TimingSourceFactory
from slot_racing.modules.races.ui.live_view import LiveRaceView
from slot_racing.modules.races.ui.races_page import RacesPage
from slot_racing.modules.races.ui.wizard import MODE, NAME, OVERVIEW, START, TRACK, RaceWizard
from tests.modules.conftest import Env
from tests.modules.test_ui_management import configure_race, open_page
from tests.support.timing import FakeTimingFactory


def register(env: Env, factory: TimingSourceFactory) -> None:
    env.runtime.services.register(
        TimingSourceFactory, factory, owner="test", name=factory.provider_id
    )


def offline(provider_id: str = "raspberry_pi") -> FakeTimingFactory:
    return FakeTimingFactory(
        provider_id,
        availability=ProviderAvailability.unavailable("error.timing_provider.unavailable"),
    )


def items(combo: QComboBox) -> list[tuple[str, str, bool]]:
    model = combo.model()
    assert isinstance(model, QStandardItemModel)
    entries = []
    for index in range(combo.count()):
        item = model.item(index)
        assert item is not None
        entries.append((combo.itemText(index), combo.itemData(index), item.isEnabled()))
    return entries


def wizard_at_track_step(qtbot: QtBot, env: Env) -> tuple[RacesPage, RaceWizard]:
    _, page = open_page(qtbot, env, "races")
    assert isinstance(page, RacesPage)
    page.refresh()
    env.track("Heimbahn")
    page.new_race()
    wizard = page.wizard
    wizard.name_edit.setText("Finale")
    assert wizard.go_next()
    assert wizard.step == TRACK
    return page, wizard


def test_the_provider_selection_shows_the_simulation_as_available(qtbot: QtBot, env: Env) -> None:
    _, wizard = wizard_at_track_step(qtbot, env)
    assert items(wizard.provider_combo) == [("Simulation", "simulation", True)]
    assert wizard.provider_combo.currentData() == "simulation"
    assert wizard.provider_status.text() == "Status: verfügbar"


def test_unavailable_providers_are_shown_but_cannot_be_chosen(qtbot: QtBot, env: Env) -> None:
    register(env, offline("camera"))
    register(env, offline("raspberry_pi"))
    _, wizard = wizard_at_track_step(qtbot, env)
    assert items(wizard.provider_combo) == [
        ("Kamera - nicht verfügbar", "camera", False),
        ("raspberry_pi - nicht verfügbar", "raspberry_pi", False),
        ("Simulation", "simulation", True),
    ]
    assert wizard.provider_combo.currentData() == "simulation"

    wizard.provider_combo.setCurrentIndex(wizard.provider_combo.findData("camera"))
    assert wizard.provider_status.text() == "Status: Die gewählte Zeitmessung ist nicht verfügbar."
    assert not wizard.go_next()
    assert "nicht verfügbar" in wizard.status.text()
    assert wizard.step == TRACK

    wizard.provider_combo.setCurrentIndex(wizard.provider_combo.findData("simulation"))
    assert wizard.go_next()
    assert wizard.step == MODE


def test_a_newly_registered_provider_appears_without_ui_changes(qtbot: QtBot, env: Env) -> None:
    _, wizard = wizard_at_track_step(qtbot, env)
    assert wizard.provider_combo.count() == 1
    register(env, FakeTimingFactory("camera"))
    wizard.go_back()
    wizard.name_edit.setText("Finale")
    assert wizard.go_next()
    assert items(wizard.provider_combo) == [
        ("Kamera", "camera", True),
        ("Simulation", "simulation", True),
    ]
    wizard.provider_combo.setCurrentIndex(wizard.provider_combo.findData("camera"))
    assert wizard.go_next()
    assert wizard.step == MODE


def test_the_chosen_provider_is_stored_with_the_race_and_shown_in_the_overview(
    qtbot: QtBot, env: Env
) -> None:
    register(env, FakeTimingFactory("camera"))
    _, wizard = wizard_at_track_step(qtbot, env)
    wizard.provider_combo.setCurrentIndex(wizard.provider_combo.findData("camera"))
    assert wizard.go_next()
    assert wizard.go_next()
    race = wizard.race
    assert race is not None and race.timing_provider == "camera"
    assert env.races.require_race(race.id).timing_provider == "camera"
    wizard.go_back()
    wizard.go_back()
    wizard.go_back()
    assert wizard.step == NAME
    wizard.begin(race.id)
    wizard.go_next()
    assert wizard.provider_combo.currentData() == "camera"


def test_without_any_provider_the_selection_explains_and_blocks(qtbot: QtBot, env: Env) -> None:
    env.runtime.plugins.disable("timing")
    _, wizard = wizard_at_track_step(qtbot, env)
    assert wizard.provider_combo.count() == 0
    assert wizard.provider_status.text() == "Keine Zeitmessung registriert."
    assert not wizard.go_next()
    assert "Zeitmessung" in wizard.status.text()


def test_a_camera_that_cannot_start_explains_that_on_the_wizard(qtbot: QtBot, env: Env) -> None:
    register(
        env,
        FakeTimingFactory(
            "camera",
            create_error=ProviderConfigurationError("error.timing_provider.camera_zones_missing"),
        ),
    )
    anna = env.driver("Anna")
    env.vehicle("Porsche", driver_id=anna.id)
    page, wizard = wizard_at_track_step(qtbot, env)
    wizard.provider_combo.setCurrentIndex(wizard.provider_combo.findData("camera"))
    assert wizard.go_next() and wizard.go_next()
    wizard.driver_combo.setCurrentIndex(wizard.driver_combo.findData(anna.id))
    wizard.lane_combo.setCurrentIndex(wizard.lane_combo.findData(1))
    assert wizard.add_participant()
    assert wizard.go_next() and wizard.step == OVERVIEW
    assert wizard.go_next() and wizard.step == START
    wizard.start_button.click()
    assert wizard.step == START
    assert page.current_view() is page.wizard
    assert "Erkennungszonen" in wizard.status.text()
    race = wizard.race
    assert race is not None
    assert env.races.require_race(race.id).status is RaceStatus.READY


def test_a_valid_camera_provider_starts_the_race(qtbot: QtBot, env: Env) -> None:
    register(env, FakeTimingFactory("camera"))
    anna = env.driver("Anna")
    env.vehicle("Porsche", driver_id=anna.id)
    page, wizard = wizard_at_track_step(qtbot, env)
    wizard.provider_combo.setCurrentIndex(wizard.provider_combo.findData("camera"))
    assert wizard.go_next() and wizard.go_next()
    wizard.driver_combo.setCurrentIndex(wizard.driver_combo.findData(anna.id))
    wizard.lane_combo.setCurrentIndex(wizard.lane_combo.findData(1))
    assert wizard.add_participant()
    assert wizard.go_next() and wizard.go_next()
    wizard.start_button.click()
    assert isinstance(page.current_view(), LiveRaceView)
    race = wizard.race
    assert race is not None
    assert env.races.require_race(race.id).status is RaceStatus.RUNNING
    assert env.races.require_race(race.id).timing_provider == "camera"


def test_a_race_with_the_simulation_still_starts_through_the_ui(qtbot: QtBot, env: Env) -> None:
    _, page = open_page(qtbot, env, "races")
    assert isinstance(page, RacesPage)
    page.refresh()
    configure_race(page, env)
    wizard = page.wizard
    assert wizard.go_next() and wizard.step == OVERVIEW
    assert "Zeitmessung: Simulation" in wizard.overview_label.text()
    assert wizard.go_next() and wizard.step == START
    wizard.start_button.click()
    assert isinstance(page.current_view(), LiveRaceView)
    race = wizard.race
    assert race is not None
    assert env.races.require_race(race.id).status is RaceStatus.RUNNING
    assert env.races.require_race(race.id).timing_provider == "simulation"
