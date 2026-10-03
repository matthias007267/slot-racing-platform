"""Lane count is a track setting: new tracks start at 2, and 2, 3 or 4 can be chosen."""

from __future__ import annotations

import pytest
from pytestqt.qtbot import QtBot

from slot_racing.core.errors import ValidationError
from slot_racing.modules.races.ui.races_page import RacesPage
from slot_racing.modules.races.ui.wizard import MODE, PARTICIPANTS, TRACK
from slot_racing.modules.tracks.service import TrackInput
from tests.modules.conftest import Env
from tests.modules.test_ui_management import cells, open_page


@pytest.mark.parametrize("lanes", [2, 3, 4])
def test_a_lap_race_uses_each_configured_lane(env: Env, lanes: int) -> None:
    track = env.track("Strecke", lanes=lanes)
    race = env.races.create_race("Rennen", track.id, 2)
    driver_id, vehicle_id = env.pair(1)
    env.races.add_participant(race.id, driver_id, vehicle_id, 1)
    extra_driver, extra_vehicle = env.pair(lanes + 1)
    with pytest.raises(ValidationError) as caught:
        env.races.add_participant(race.id, extra_driver, extra_vehicle, lanes + 1)
    assert caught.value.key == "error.race.lane_invalid"
    for lane in range(2, lanes + 1):
        next_driver, next_vehicle = env.pair(lane)
        env.races.add_participant(race.id, next_driver, next_vehicle, lane)
    runner = env.controller.start_race(race.id)
    assert [row.lane for row in runner.snapshot().rows] == list(range(1, lanes + 1))
    runner.stop()
    assert env.races.require_race(race.id).lane_count == lanes


@pytest.mark.parametrize("lanes", [2, 3, 4])
def test_the_race_wizard_offers_only_the_tracks_lanes(qtbot: QtBot, env: Env, lanes: int) -> None:
    track = env.track("Strecke", lanes=lanes)
    _, page = open_page(qtbot, env, "races")
    assert isinstance(page, RacesPage)
    page.new_race()
    wizard = page.wizard
    wizard.name_edit.setText("Rennen")
    assert wizard.go_next()
    assert wizard.step == TRACK
    wizard.track_combo.setCurrentIndex(wizard.track_combo.findData(track.id))
    assert wizard.go_next()
    assert wizard.step == MODE
    assert wizard.go_next()
    assert wizard.step == PARTICIPANTS
    offered = [wizard.lane_combo.itemData(index) for index in range(wizard.lane_combo.count())]
    assert offered == list(range(1, lanes + 1))
    wizard.go_back()
    assert wizard.step == MODE
    wizard.mode_combo.setCurrentIndex(wizard.mode_combo.findData("time_trial"))
    assert wizard.go_next()
    assert wizard.step == PARTICIPANTS
    again = [wizard.lane_combo.itemData(index) for index in range(wizard.lane_combo.count())]
    assert again == offered


@pytest.mark.parametrize("lanes", [2, 3, 4])
def test_the_time_trial_board_lists_only_configured_lanes(
    qtbot: QtBot, env: Env, lanes: int
) -> None:
    track = env.track("Heimbahn", lanes=lanes)
    race = env.races.create_time_trial("Training", track.id)
    driver = env.driver("Max")
    vehicle = env.vehicle("Porsche", driver_id=driver.id)
    env.races.add_participant(race.id, driver.id, vehicle.id, 1)
    _, page = open_page(qtbot, env, "races")
    assert isinstance(page, RacesPage)
    page.refresh()
    page.table.selectRow(0)
    page.buttons["start"].click()
    live = page.live
    assert live.board.records_table.rowCount() == lanes
    assert live.board.active_table.rowCount() == lanes
    assert [cells(live.board.records_table, row)[0] for row in range(lanes)] == [
        str(number) for number in range(1, lanes + 1)
    ]
    assert cells(live.board.active_table, 0) == ["1", "Max", "Porsche 911"]
    for row in range(1, lanes):
        assert cells(live.board.active_table, row) == [str(row + 1), "frei", "-"]
    page.show_results(race.id)
    assert page.results.records_table.rowCount() == lanes
    assert [cells(page.results.records_table, row)[0] for row in range(lanes)] == [
        str(number) for number in range(1, lanes + 1)
    ]


def test_a_new_track_does_not_shrink_an_existing_race(env: Env) -> None:
    track = env.track("Alt", lanes=4)
    race = env.races.create_time_trial("Gestern", track.id)
    lisa = env.driver("Lisa")
    audi = env.vehicle("Audi", driver_id=lisa.id)
    env.races.add_participant(race.id, lisa.id, audi.id, 4)
    env.races.record_lap(race.id, 4, 1, 8_000_000_000, 8_000_000_000, {})
    fresh = env.tracks.create_track(TrackInput(name="Neu"))
    assert fresh.lane_count == 2
    stored_track = env.tracks.get_track(track.id)
    assert stored_track is not None and stored_track.lane_count == 4
    stored = env.races.require_race(race.id)
    assert stored.lane_count == 4
    assert [(item.driver_label, item.lane) for item in stored.participants] == [("Lisa", 4)]
    measured = env.races.list_time_measurements(race_id=race.id)
    assert [(row.lane, row.driver_label, row.time_ns) for row in measured] == [
        (4, "Lisa", 8_000_000_000)
    ]
