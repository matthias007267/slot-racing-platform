from __future__ import annotations

import pytest

from slot_racing.core.domain import DriverId, RaceId, RaceStatus, TrackId, VehicleId
from slot_racing.core.errors import ValidationError
from tests.modules.conftest import Env


def key_of(error: pytest.ExceptionInfo[ValidationError]) -> str:
    return error.value.key


def test_create_race(env: Env) -> None:
    track = env.track(lanes=3)
    race = env.races.create_race(" Sonntagsrennen ", track.id, 5)
    assert race.name == "Sonntagsrennen"
    assert race.status is RaceStatus.CREATED
    assert (race.track_name, race.lane_count, race.laps) == ("Ring", 3, 5)
    assert race.participants == ()
    assert race.created_at is not None and race.started_at is None
    assert [r.id for r in env.races.list_races()] == [race.id]


def test_race_validation(env: Env) -> None:
    track_id = env.track_id()
    with pytest.raises(ValidationError) as caught:
        env.races.create_race("", track_id, 5)
    assert key_of(caught) == "error.race.name.required"
    with pytest.raises(ValidationError) as caught:
        env.races.create_race("R", track_id, 0)
    assert key_of(caught) == "error.race.laps"
    with pytest.raises(ValidationError) as caught:
        env.races.create_race("R", TrackId(999), 3)
    assert key_of(caught) == "error.race.track_unknown"
    inactive = env.track("Alt")
    env.tracks.set_active(inactive.id, False)
    with pytest.raises(ValidationError) as caught:
        env.races.create_race("R", inactive.id, 3)
    assert key_of(caught) == "error.race.track_inactive"


def test_add_participant_makes_race_ready(env: Env) -> None:
    race = env.races.create_race("R", env.track_id(), 3)
    driver_id, vehicle_id = env.pair(1)
    participant = env.races.add_participant(race.id, driver_id, vehicle_id, 2)
    assert (participant.lane, participant.driver_label, participant.vehicle_label) == (
        2,
        "Driver 1",
        "Car 1 (911)",
    )
    loaded = env.races.require_race(race.id)
    assert loaded.status is RaceStatus.READY
    env.races.remove_participant(race.id, participant.id)
    assert env.races.require_race(race.id).status is RaceStatus.CREATED


def test_update_participant_rewrites_the_same_row(env: Env) -> None:
    race = env.races.create_race("R", env.track_id(lanes=2), 3)
    driver_id, vehicle_id = env.pair(1)
    other_vehicle = env.pair(2)[1]
    added = env.races.add_participant(race.id, driver_id, vehicle_id, 1)
    updated = env.races.update_participant(race.id, added.id, driver_id, other_vehicle, 2)
    assert updated.id == added.id
    assert (updated.vehicle_id, updated.lane, updated.driver_id) == (other_vehicle, 2, driver_id)
    stored = env.races.require_race(race.id)
    assert [item.id for item in stored.participants] == [added.id]


def test_duplicate_lane_is_rejected(env: Env) -> None:
    race = env.races.create_race("R", env.track_id(), 3)
    d1, v1 = env.pair(1)
    d2, v2 = env.pair(2)
    env.races.add_participant(race.id, d1, v1, 1)
    with pytest.raises(ValidationError) as caught:
        env.races.add_participant(race.id, d2, v2, 1)
    assert key_of(caught) == "error.race.lane_taken"
    assert len(env.races.require_race(race.id).participants) == 1


def test_too_many_participants_are_rejected(env: Env) -> None:
    race = env.races.create_race("R", env.track_id(lanes=2), 3)
    for lane in (1, 2):
        driver_id, vehicle_id = env.pair(lane)
        env.races.add_participant(race.id, driver_id, vehicle_id, lane)
    driver_id, vehicle_id = env.pair(3)
    with pytest.raises(ValidationError) as caught:
        env.races.add_participant(race.id, driver_id, vehicle_id, 1)
    assert key_of(caught) == "error.race.too_many_participants"
    assert caught.value.params["maximum"] == 2


def test_lane_must_exist_on_the_track(env: Env) -> None:
    race = env.races.create_race("R", env.track_id(lanes=2), 3)
    driver_id, vehicle_id = env.pair(1)
    for lane in (0, 3):
        with pytest.raises(ValidationError) as caught:
            env.races.add_participant(race.id, driver_id, vehicle_id, lane)
        assert key_of(caught) == "error.race.lane_invalid"


def test_inactive_driver_and_vehicle_are_rejected(env: Env) -> None:
    race = env.races.create_race("R", env.track_id(), 3)
    driver_id, vehicle_id = env.pair(1)
    env.drivers.set_active(driver_id, False)
    with pytest.raises(ValidationError) as caught:
        env.races.add_participant(race.id, driver_id, vehicle_id, 1)
    assert key_of(caught) == "error.race.driver_inactive"
    env.drivers.set_active(driver_id, True)
    env.vehicles.set_active(vehicle_id, False)
    with pytest.raises(ValidationError) as caught:
        env.races.add_participant(race.id, driver_id, vehicle_id, 1)
    assert key_of(caught) == "error.race.vehicle_inactive"


def test_unknown_driver_and_vehicle_are_rejected(env: Env) -> None:
    race = env.races.create_race("R", env.track_id(), 3)
    driver_id, vehicle_id = env.pair(1)
    with pytest.raises(ValidationError) as caught:
        env.races.add_participant(race.id, DriverId(999), vehicle_id, 1)
    assert key_of(caught) == "error.race.driver_unknown"
    with pytest.raises(ValidationError) as caught:
        env.races.add_participant(race.id, driver_id, VehicleId(999), 1)
    assert key_of(caught) == "error.race.vehicle_unknown"


def test_a_vehicle_must_belong_to_the_driver_or_to_nobody(env: Env) -> None:
    race = env.races.create_race("R", env.track_id(lanes=4), 3)
    owner = env.driver("Anna")
    other = env.driver("Ben")
    own_car = env.vehicle("Porsche", driver_id=owner.id)
    free_car = env.vehicle("Ersatz")
    foreign_car = env.vehicle("Ferrari", driver_id=other.id)

    added = env.races.add_participant(race.id, owner.id, own_car.id, 1)
    assert added.vehicle_id == own_car.id
    added_free = env.races.add_participant(race.id, other.id, free_car.id, 2)
    assert added_free.vehicle_id == free_car.id

    third = env.driver("Chris")
    with pytest.raises(ValidationError) as caught:
        env.races.add_participant(race.id, third.id, foreign_car.id, 3)
    assert key_of(caught) == "error.race.vehicle_wrong_driver"
    assert len(env.races.require_race(race.id).participants) == 2


def test_participants_must_be_unique(env: Env) -> None:
    race = env.races.create_race("R", env.track_id(), 3)
    d1, v1 = env.pair(1)
    d2, v2 = env.pair(2)
    env.races.add_participant(race.id, d1, v1, 1)
    with pytest.raises(ValidationError) as caught:
        env.races.add_participant(race.id, d1, v2, 2)
    assert key_of(caught) == "error.race.driver_duplicate"
    with pytest.raises(ValidationError) as caught:
        env.races.add_participant(race.id, d2, v1, 2)
    assert key_of(caught) == "error.race.vehicle_duplicate"


def test_changing_to_a_smaller_track_requires_matching_participants(env: Env) -> None:
    big = env.track_id(lanes=4)
    small = env.track("Klein", lanes=2).id
    race = env.races.create_race("R", big, 3)
    driver_id, vehicle_id = env.pair(1)
    env.races.add_participant(race.id, driver_id, vehicle_id, 3)
    with pytest.raises(ValidationError) as caught:
        env.races.update_race(race.id, "R", small, 3)
    assert key_of(caught) == "error.race.track_too_small"
    assert env.races.require_race(race.id).track_id == big
    renamed = env.races.update_race(race.id, "Neu", big, 7)
    assert (renamed.name, renamed.laps) == ("Neu", 7)


def test_start_requires_participants_and_active_resources(env: Env) -> None:
    race = env.races.create_race("R", env.track_id(), 3)
    with pytest.raises(ValidationError) as caught:
        env.controller.start_race(race.id)
    assert key_of(caught) == "error.race.no_participants"
    driver_id, vehicle_id = env.pair(1)
    env.races.add_participant(race.id, driver_id, vehicle_id, 1)
    env.drivers.set_active(driver_id, False)
    with pytest.raises(ValidationError) as caught:
        env.controller.start_race(race.id)
    assert key_of(caught) == "error.race.driver_inactive"
    assert env.races.require_race(race.id).status is RaceStatus.READY


def test_start_and_end_a_race(env: Env) -> None:
    race = env.races.create_race("R", env.track_id(), 2)
    driver_id, vehicle_id = env.pair(1)
    env.races.add_participant(race.id, driver_id, vehicle_id, 1)

    runner = env.controller.start_race(race.id)
    started = env.races.require_race(race.id)
    assert started.status is RaceStatus.RUNNING
    assert started.started_at is not None
    assert not started.is_editable

    runner.pause()
    assert env.races.require_race(race.id).status is RaceStatus.PAUSED
    runner.resume()
    assert env.races.require_race(race.id).status is RaceStatus.RUNNING

    runner.stop()
    ended = env.races.require_race(race.id)
    assert ended.status is RaceStatus.ABORTED
    assert ended.finished_at is not None
    assert ended.is_over


def test_running_and_finished_races_cannot_be_edited_or_restarted(env: Env) -> None:
    race = env.races.create_race("R", env.track_id(), 2)
    driver_id, vehicle_id = env.pair(1)
    env.races.add_participant(race.id, driver_id, vehicle_id, 1)
    runner = env.controller.start_race(race.id)

    d2, v2 = env.pair(2)
    with pytest.raises(ValidationError) as caught:
        env.races.add_participant(race.id, d2, v2, 2)
    assert key_of(caught) == "error.race.not_editable"
    with pytest.raises(ValidationError) as caught:
        env.races.delete_race(race.id)
    assert key_of(caught) == "error.race.running"
    with pytest.raises(ValidationError) as caught:
        env.controller.start_race(race.id)
    assert key_of(caught) == "error.race.already_running"

    runner.stop()
    with pytest.raises(ValidationError) as caught:
        env.controller.start_race(race.id)
    assert key_of(caught) == "error.race.not_startable"
    env.races.delete_race(race.id)
    assert env.races.get_race(race.id) is None


def test_unknown_race(env: Env) -> None:
    with pytest.raises(ValidationError) as caught:
        env.races.require_race(RaceId(42))
    assert key_of(caught) == "error.race.not_found"


def test_running_race_is_aborted_when_the_module_is_disabled(env: Env) -> None:
    race = env.races.create_race("R", env.track_id(), 2)
    driver_id, vehicle_id = env.pair(1)
    env.races.add_participant(race.id, driver_id, vehicle_id, 1)
    env.controller.start_race(race.id)

    env.runtime.plugins.disable("races")
    ended = env.races.require_race(race.id)
    assert ended.status is RaceStatus.ABORTED
    assert ended.finished_at is not None


def test_races_left_running_by_a_crash_are_aborted(env: Env) -> None:
    race = env.races.create_race("R", env.track_id(), 2)
    env.races.record_started(race.id)
    assert env.races.abort_stale_races() == 1
    assert env.races.require_race(race.id).status is RaceStatus.ABORTED
    assert env.races.abort_stale_races() == 0


def test_a_stale_race_without_laps_gets_an_empty_standing(env: Env) -> None:
    race = env.races.create_race("R", env.track_id(), 4)
    driver_id, vehicle_id = env.pair(1)
    env.races.add_participant(race.id, driver_id, vehicle_id, 1)
    env.races.record_started(race.id)
    assert env.races.abort_stale_races() == 1
    (result,) = env.races.get_results(race.id)
    assert (result.laps_completed, result.best_lap_ns, result.total_time_ns) == (0, None, None)
    assert result.finished is False and result.position == 1
    assert env.races.get_laps(race.id) == []


def test_a_stale_race_rebuilds_standings_from_stored_laps_only(env: Env) -> None:
    race = env.races.create_race("R", env.track_id(lanes=2), 3)
    lane1 = env.pair(1)
    lane2 = env.pair(2)
    env.races.add_participant(race.id, lane1[0], lane1[1], 1)
    env.races.add_participant(race.id, lane2[0], lane2[1], 2)
    env.races.record_started(race.id)
    env.races.record_lap(race.id, 1, 1, 5_000_000_000, 5_000_000_000, {1: 2_000_000_000})
    (during,) = [row for row in env.races.get_results(race.id) if row.lane == 1]
    assert during.laps_completed == 1 and during.best_lap_ns == 5_000_000_000
    env.races.record_lap(race.id, 1, 2, 4_000_000_000, 9_000_000_000, {})
    env.races.record_lap(race.id, 2, 1, 5_000_000_000, 9_000_000_000, {})
    assert env.races.abort_stale_races() == 1
    assert env.races.require_race(race.id).status is RaceStatus.ABORTED
    results = env.races.get_results(race.id)
    assert [(r.lane, r.position, r.laps_completed, r.finished) for r in results] == [
        (1, 1, 2, False),
        (2, 2, 1, False),
    ]
    assert results[0].best_lap_ns == 4_000_000_000
    assert results[0].total_time_ns == 9_000_000_000
    assert len(env.races.get_laps(race.id)) == 3


def test_a_stale_race_orders_equal_times_by_lane_and_finishers_by_race_time(env: Env) -> None:
    tied = env.races.create_race("Tied", env.track_id(lanes=2), 4)
    a = env.pair(1)
    b = env.pair(2)
    env.races.add_participant(tied.id, a[0], a[1], 1)
    env.races.add_participant(tied.id, b[0], b[1], 2)
    env.races.record_started(tied.id)
    env.races.record_lap(tied.id, 2, 1, 5_000_000_000, 5_000_000_000, {})
    env.races.record_lap(tied.id, 1, 1, 5_000_000_000, 5_000_000_000, {})
    env.races.abort_stale_races()
    assert [row.lane for row in env.races.get_results(tied.id)] == [1, 2]

    finished = env.races.create_race("Done", env.track_id(lanes=2), 1)
    c = env.pair(3)
    d = env.pair(4)
    env.races.add_participant(finished.id, c[0], c[1], 1)
    env.races.add_participant(finished.id, d[0], d[1], 2)
    env.races.record_started(finished.id)
    env.races.record_lap(finished.id, 1, 1, 6_000_000_000, 6_000_000_000, {})
    env.races.record_lap(finished.id, 2, 1, 4_000_000_000, 4_000_000_000, {})
    env.races.abort_stale_races()
    results = env.races.get_results(finished.id)
    assert [(row.lane, row.position, row.finished) for row in results] == [
        (2, 1, True),
        (1, 2, True),
    ]
