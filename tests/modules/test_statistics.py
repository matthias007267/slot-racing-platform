"""Lane statistics follow the track. Best times are calculated from stored measurements."""

from __future__ import annotations

from datetime import datetime

import pytest
from pytestqt.qtbot import QtBot

from slot_racing.core.catalog import TimeMeasurementCatalog, TimeMeasurementView, TrackInfo
from slot_racing.core.domain import MAX_LANE_COUNT, TrackId
from slot_racing.core.domain.lanes import MIN_LANE_COUNT
from slot_racing.modules.drivers_vehicles.service import VehicleInput
from slot_racing.modules.races.types import RaceInfo
from slot_racing.modules.statistics.lanes import format_lap_seconds, statistics_for_track
from slot_racing.modules.statistics.ui.page import StatisticsPage
from tests.modules.conftest import Env
from tests.modules.test_ui_management import cells, open_page


def test_a_two_lane_track_lists_only_lane_one_and_two() -> None:
    lines = statistics_for_track(_track(lanes=2), ())
    assert [line.label for line in lines] == ["Bahn 1", "Bahn 2"]
    assert len(lines) == MIN_LANE_COUNT
    assert len(lines) != MAX_LANE_COUNT


def test_a_three_lane_track_stops_at_lane_three() -> None:
    labels = [line.label for line in statistics_for_track(_track(lanes=3), ())]
    assert labels == ["Bahn 1", "Bahn 2", "Bahn 3"]
    assert "Bahn 4" not in labels


def test_a_four_lane_track_lists_every_lane() -> None:
    labels = [line.label for line in statistics_for_track(_track(lanes=4), ())]
    assert labels == ["Bahn 1", "Bahn 2", "Bahn 3", "Bahn 4"]


@pytest.mark.parametrize("lanes", [2, 3, 4])
def test_statistics_follow_two_three_and_four_lanes(lanes: int) -> None:
    labels = [line.label for line in statistics_for_track(_track(lanes=lanes), ())]
    assert labels == [f"Bahn {lane}" for lane in range(1, lanes + 1)]


def test_a_stored_count_outside_the_new_track_range_is_shown_as_stored() -> None:
    labels = [line.label for line in statistics_for_track(_track(lanes=6), ())]
    assert labels == [f"Bahn {lane}" for lane in range(1, 7)]


def test_measurements_stay_on_their_own_lane() -> None:
    track = _track(lanes=2)
    shared = 8_421_000_000
    rows = (
        _measurement(1, track.id, lane=1, driver="Max", vehicle="Porsche 911", time_ns=shared),
        _measurement(2, track.id, lane=2, driver="Peter", vehicle="BMW M4", time_ns=shared),
        _measurement(3, track.id, lane=2, driver="Lisa", vehicle="Audi R8", time_ns=7_000_000_000),
    )
    lines = statistics_for_track(track, rows)
    assert [(line.label, line.driver_label, line.best_time_ns) for line in lines] == [
        ("Bahn 1", "Max", shared),
        ("Bahn 2", "Lisa", 7_000_000_000),
    ]


def test_the_best_time_is_the_fastest_measurement_on_that_lane() -> None:
    track = _track(name="Meine Strecke", lanes=2)
    rows = (
        _measurement(
            1, track.id, lane=1, driver="Max", vehicle="Porsche 911", time_ns=9_100_000_000
        ),
        _measurement(
            2, track.id, lane=1, driver="Max", vehicle="Porsche 911", time_ns=8_421_000_000
        ),
        _measurement(3, track.id, lane=2, driver="Peter", vehicle="BMW M4", time_ns=9_200_000_000),
        _measurement(4, track.id, lane=2, driver="Peter", vehicle="BMW M4", time_ns=8_537_000_000),
        _measurement(5, track.id, lane=4, driver="Fremd", vehicle="Andere", time_ns=1_000_000_000),
    )
    lines = statistics_for_track(track, rows)
    shown = [
        (line.label, line.driver_label, line.vehicle_label, line.best_time_ns) for line in lines
    ]
    assert shown == [
        ("Bahn 1", "Max", "Porsche 911", 8_421_000_000),
        ("Bahn 2", "Peter", "BMW M4", 8_537_000_000),
    ]
    assert [format_lap_seconds(line.best_time_ns) for line in lines] == ["8,421 s", "8,537 s"]


def test_an_equal_time_keeps_the_earlier_measurement() -> None:
    track = _track(lanes=2)
    earlier = datetime(2026, 1, 1, 10, 0, 0)
    later = datetime(2026, 1, 1, 10, 5, 0)
    rows = (
        _measurement(
            2,
            track.id,
            lane=1,
            driver="Später",
            vehicle="Zweitwagen",
            time_ns=8_000_000_000,
            recorded_at=later,
        ),
        _measurement(
            1,
            track.id,
            lane=1,
            driver="Früher",
            vehicle="Erstwagen",
            time_ns=8_000_000_000,
            recorded_at=earlier,
        ),
    )
    (line,) = [item for item in statistics_for_track(track, rows) if item.lane == 1]
    assert line.driver_label == "Früher"
    assert line.vehicle_label == "Erstwagen"
    assert statistics_for_track(track, rows)[1].best_time_ns is None


def test_another_track_does_not_contribute_lanes_or_times() -> None:
    home = _track(track_id=1, name="Kurz", lanes=2)
    other = _track(track_id=2, name="Lang", lanes=4)
    foreign = (
        _measurement(1, other.id, lane=1, driver="Fremd", vehicle="Wagen", time_ns=5_000_000_000),
        _measurement(2, other.id, lane=3, driver="Fremd", vehicle="Wagen", time_ns=5_100_000_000),
        _measurement(3, other.id, lane=4, driver="Fremd", vehicle="Wagen", time_ns=5_200_000_000),
    )
    home_lines = statistics_for_track(home, foreign)
    assert [line.label for line in home_lines] == ["Bahn 1", "Bahn 2"]
    assert all(line.best_time_ns is None for line in home_lines)
    other_lines = statistics_for_track(other, foreign)
    assert [line.label for line in other_lines] == ["Bahn 1", "Bahn 2", "Bahn 3", "Bahn 4"]
    assert other_lines[2].driver_label == "Fremd"
    assert other_lines[0].best_time_ns == 5_000_000_000


def test_stored_measurements_keep_their_lane_and_are_not_rewritten(env: Env) -> None:
    track = env.track("Meine Strecke", lanes=2)
    other = env.track("Andere", lanes=4)
    race = _drive(env, track.id, "Training", "Max", "Porsche", "911", 1, 8_421_000_000)
    _drive(env, track.id, "Training 2", "Peter", "BMW", "M4", 2, 8_537_000_000)
    _drive(env, other.id, "Lang", "Lisa", "Audi", "R8", 4, 7_000_000_000)
    before = env.races.list_time_measurements()
    before_race = env.races.require_race(race.id)
    before_track = env.tracks.get_track(track.id)
    catalog = env.runtime.services.get(TimeMeasurementCatalog)
    lines = statistics_for_track(track, catalog.list_for_track(track.id))
    assert [(line.label, line.driver_label, line.best_time_ns) for line in lines] == [
        ("Bahn 1", "Max", 8_421_000_000),
        ("Bahn 2", "Peter", 8_537_000_000),
    ]
    assert catalog.list_for_track(other.id)[0].lane == 4
    assert env.races.list_time_measurements() == before
    assert env.races.require_race(race.id) == before_race
    assert env.tracks.get_track(track.id) == before_track
    assert len(env.races.list_races()) == 3


def test_a_lap_race_does_not_become_a_lane_record(env: Env) -> None:
    track = env.track(lanes=2)
    race = env.races.create_race("Sonntag", track.id, 3)
    driver = env.driver("Max")
    vehicle = env.vehicle("Porsche", driver.id)
    env.races.add_participant(race.id, driver.id, vehicle.id, 1)
    env.races.record_lap(race.id, 1, 1, 8_000_000_000, 8_000_000_000, {})
    catalog = env.runtime.services.get(TimeMeasurementCatalog)
    lines = statistics_for_track(track, catalog.list_for_track(track.id))
    assert [line.best_time_ns for line in lines] == [None, None]
    assert env.races.list_time_measurements(track_id=track.id) == []


@pytest.mark.parametrize("lanes", [2, 3, 4])
def test_the_statistics_page_shows_only_the_lanes_of_the_track(
    qtbot: QtBot, env: Env, lanes: int
) -> None:
    track = env.track("Strecke", lanes=lanes)
    _, page = open_page(qtbot, env, "statistics")
    assert isinstance(page, StatisticsPage)
    page.track_combo.setCurrentIndex(page.track_combo.findData(track.id))
    labels = [cells(page.table, row)[0] for row in range(page.table.rowCount())]
    assert labels == [f"Bahn {lane}" for lane in range(1, lanes + 1)]
    assert page.table.rowCount() == lanes


def test_the_statistics_page_shows_the_best_time_of_each_lane(qtbot: QtBot, env: Env) -> None:
    home = env.track("Meine Strecke", lanes=2)
    other = env.track("Fremde Strecke", lanes=4)
    _drive(env, home.id, "Training", "Max", "Porsche", "911", 1, 9_100_000_000)
    _drive(env, home.id, "Training 2", "Max", "Porsche", "911", 1, 8_421_000_000)
    _drive(env, home.id, "Training 3", "Peter", "BMW", "M4", 2, 8_537_000_000)
    _drive(env, other.id, "Anders", "Lisa", "Audi", "R8", 4, 6_000_000_000)
    before = env.races.list_time_measurements()
    _, page = open_page(qtbot, env, "statistics")
    assert isinstance(page, StatisticsPage)
    page.track_combo.setCurrentIndex(page.track_combo.findData(home.id))
    assert cells(page.table, 0) == ["Bahn 1", "Max", "Porsche 911", "8,421 s"]
    assert cells(page.table, 1) == ["Bahn 2", "Peter", "BMW M4", "8,537 s"]
    assert page.table.rowCount() == 2
    page.track_combo.setCurrentIndex(page.track_combo.findData(other.id))
    assert page.table.rowCount() == 4
    assert cells(page.table, 0)[0] == "Bahn 1"
    assert cells(page.table, 0)[3] == "-"
    assert cells(page.table, 3) == ["Bahn 4", "Lisa", "Audi R8", "6,000 s"]
    page.refresh()
    assert env.races.list_time_measurements() == before


def test_statistics_without_a_track_shows_no_lanes(qtbot: QtBot, env: Env) -> None:
    _, page = open_page(qtbot, env, "statistics")
    assert isinstance(page, StatisticsPage)
    assert page.table.rowCount() == 0
    assert page.table.isHidden()
    assert not page.empty.isHidden()
    assert "Keine Strecke" in page.empty.text()


def _track(lanes: int, *, name: str = "Strecke", track_id: int = 1) -> TrackInfo:
    return TrackInfo(
        id=TrackId(track_id),
        name=name,
        description=None,
        lane_count=lanes,
        is_active=True,
    )


def _measurement(
    row_id: int,
    track_id: TrackId,
    *,
    lane: int,
    driver: str,
    vehicle: str,
    time_ns: int,
    recorded_at: datetime | None = None,
) -> TimeMeasurementView:
    return TimeMeasurementView(
        id=row_id,
        track_id=track_id,
        lane=lane,
        driver_label=driver,
        vehicle_label=vehicle,
        time_ns=time_ns,
        recorded_at=recorded_at or datetime(2026, 1, 1, 12, 0, row_id),
    )


def _drive(
    env: Env,
    track_id: TrackId,
    race_name: str,
    driver_name: str,
    manufacturer: str,
    model: str,
    lane: int,
    time_ns: int,
) -> RaceInfo:
    race = env.races.create_time_trial(race_name, track_id)
    driver = env.driver(driver_name)
    vehicle = env.vehicles.create_vehicle(
        VehicleInput(
            name=f"{manufacturer} {model}",
            model=model,
            manufacturer=manufacturer,
            driver_id=driver.id,
        )
    )
    env.races.add_participant(race.id, driver.id, vehicle.id, lane)
    env.races.record_lap(race.id, lane, 1, time_ns, time_ns, {})
    return race
