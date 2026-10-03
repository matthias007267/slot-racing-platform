"""The dashboard catalog only reads races that already exist."""

from __future__ import annotations

from slot_racing.core.catalog import RaceCatalog
from slot_racing.core.domain import RaceStatus
from tests.modules.conftest import Env


def test_overview_reports_counts_and_the_latest_result(env: Env) -> None:
    catalog = env.runtime.services.get(RaceCatalog)
    assert catalog.count_races() == 0
    assert catalog.active_summary() is None
    assert catalog.latest_summary() is None
    assert catalog.latest_result() is None

    race = env.races.create_race("Abend", env.track_id(), 3)
    created = catalog.latest_summary()
    assert created is not None
    assert created.name == "Abend"
    assert created.status is RaceStatus.CREATED
    assert created.standings == ()
    assert catalog.count_races() == 1
    assert catalog.latest_result() is None

    driver_id, vehicle_id = env.pair(1)
    env.races.add_participant(race.id, driver_id, vehicle_id, 1)
    runner = env.controller.start_race(race.id)
    active = catalog.active_summary()
    assert active is not None
    assert active.status is RaceStatus.RUNNING
    assert active.standings[0].driver_label == "Driver 1"

    runner.stop()
    ended = catalog.active_summary()
    assert ended is not None
    assert ended.status is RaceStatus.ABORTED
    result = catalog.latest_result()
    assert result is not None
    assert result.name == "Abend"
    assert result.status is RaceStatus.ABORTED
    assert result.standings[0].driver_label == "Driver 1"
