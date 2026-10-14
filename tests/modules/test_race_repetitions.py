"""The stored repetition count repeats the existing rotation and then ends the race."""

from __future__ import annotations

import pytest
from pytestqt.qtbot import QtBot
from sqlalchemy import select

from slot_racing.core.domain import RaceId, RaceStatus
from slot_racing.core.errors import ValidationError
from slot_racing.core.statistics import TimeScope, career_summary
from slot_racing.modules.races.models import Lap, Race
from slot_racing.modules.races.planning import build_rotation, pairwise_cycle
from slot_racing.modules.races.types import RaceInfo
from slot_racing.modules.races.ui.heat_gate import HeatGate
from slot_racing.modules.races.ui.races_page import RacesPage
from slot_racing.modules.races.ui.wizard import RaceWizard
from tests.modules.conftest import Env
from tests.modules.test_ui_management import open_page


@pytest.mark.parametrize("repetitions", [1, 2, 3])
def test_repetitions_repeat_the_five_driver_rotation(env: Env, repetitions: int) -> None:
    race = _planned(env, drivers=5, repetitions=repetitions)
    ids = [participant.id for participant in race.participants]
    plan = env.races.heat_plan(race.id)
    base = list(pairwise_cycle(ids))
    assert len(base) == 5
    assert len(plan) == 5 * repetitions
    assert [heat.seats for heat in plan] == base * repetitions
    assert [heat.sequence for heat in plan] == list(range(1, 5 * repetitions + 1))
    for copy in range(repetitions):
        seen = [seat for heat in plan[copy * 5 : (copy + 1) * 5] for seat in heat.seats]
        assert set(seen) == {(driver, lane) for driver in ids for lane in (1, 2)}
        assert len(seen) == len(set(seen))
    reloaded = env.races.require_race(race.id)
    assert reloaded.repetitions == repetitions
    assert len(env.races.heat_plan(reloaded.id)) == 5 * repetitions


def test_three_repetitions_finish_only_after_the_fifteenth_heat(qtbot: QtBot, env: Env) -> None:
    race = _planned(env, drivers=5, repetitions=3)
    ids = [participant.id for participant in race.participants]
    for _ in range(5):
        assert _finish_heat(env, race.id).status is RaceStatus.READY
    briefing = env.races.heat_briefing(race.id)
    assert briefing is not None
    assert (briefing.sequence, briefing.heat_count) == (6, 15)
    gate = HeatGate(env.runtime.translator)
    qtbot.addWidget(gate)
    gate.show_briefing(briefing)
    assert gate.board.title.text() == "Durchlauf 6 von 15"
    for _ in range(5):
        assert _finish_heat(env, race.id).status is RaceStatus.READY
    next_heat = env.races.heat_briefing(race.id)
    assert next_heat is not None and next_heat.sequence == 11
    for _ in range(4):
        assert _finish_heat(env, race.id).status is RaceStatus.READY
    finished = _finish_heat(env, race.id)
    assert finished.status is RaceStatus.FINISHED
    assert not env.races.has_pending_heats(race.id)
    assert [heat.status for heat in env.races.heat_plan(race.id)] == ["completed"] * 15

    laps = env.races.get_laps(race.id)
    assert len(laps) == 30
    assert len({(lap.participant_id, lap.lap_number) for lap in laps}) == 30
    for driver in ids:
        own = [lap for lap in laps if lap.participant_id == driver]
        assert len(own) == 6
        assert len({lap.lane for lap in own}) == 2
    results = env.races.get_results(race.id)
    assert [row.laps_completed for row in results] == [6, 6, 6, 6, 6]
    assert all(row.position is not None for row in results)
    fastest = min(laps, key=lambda lap: lap.lap_time_ns)
    assert env.races.get_results(race.id)[0].best_lap_ns == min(
        lap.lap_time_ns for lap in laps if lap.participant_id == results[0].participant_id
    )

    with env.runtime.database.session() as session:
        stored_lap = session.scalar(
            select(Lap).where(
                Lap.race_id == int(race.id),
                Lap.participant_id == fastest.participant_id,
                Lap.lap_number == fastest.lap_number,
            )
        )
        assert stored_lap is not None
        lap_id = int(stored_lap.id)
    env.races.set_lap_excluded(lap_id, excluded=True)
    assert [row.laps_completed for row in env.races.get_results(race.id)] == [6, 6, 6, 6, 6]
    scope = TimeScope(track_id=int(race.track_id or 0), unassigned=True)
    counted = career_summary(
        env.races.list_completed(),
        scope,
        driver_id=int(
            next(
                person.driver_id
                for person in race.participants
                if person.id == fastest.participant_id
            )
        ),
    )
    assert counted.laps == 5
    remaining = [
        lap.lap_time_ns
        for lap in laps
        if lap.participant_id == fastest.participant_id and lap.lap_number != fastest.lap_number
    ]
    assert counted.best_lap_ns == min(remaining)

    with pytest.raises(ValidationError, match="not_editable"):
        env.races.plan_heats(race.id)
    assert len(env.races.heat_plan(race.id)) == 15
    assert env.races.require_race(race.id).status is RaceStatus.FINISHED


def test_one_lane_and_three_repetitions_give_each_driver_three_starts(env: Env) -> None:
    race = _planned(env, drivers=3, repetitions=3, active_lanes=1)
    ids = [participant.id for participant in race.participants]
    plan = env.races.heat_plan(race.id)
    base = list(build_rotation(ids, 1))
    assert [heat.seats for heat in plan] == base * 3
    assert len(plan) == 9
    for copy in range(3):
        seated = [seat[0] for heat in plan[copy * 3 : (copy + 1) * 3] for seat in heat.seats]
        assert seated == ids
    for index in range(1, 10):
        stored = _finish_heat(env, race.id)
        if index < 9:
            assert stored.status is RaceStatus.READY
        else:
            assert stored.status is RaceStatus.FINISHED
    laps = env.races.get_laps(race.id)
    assert {lap.lane for lap in laps} == {1}
    for driver in ids:
        assert len([lap for lap in laps if lap.participant_id == driver]) == 3


def test_one_repetition_ends_after_the_base_plan(env: Env) -> None:
    race = _planned(env, drivers=5, repetitions=1)
    for _ in range(4):
        assert _finish_heat(env, race.id).status is RaceStatus.READY
    assert _finish_heat(env, race.id).status is RaceStatus.FINISHED
    assert len(env.races.heat_plan(race.id)) == 5


def test_restart_keeps_the_repetition_count_and_the_full_plan(env: Env) -> None:
    race = _planned(env, drivers=5, repetitions=2)
    runner = env.controller.start_race(race.id)
    runner.stop()
    assert env.races.require_race(race.id).status is RaceStatus.ABORTED
    assert len(env.races.heat_plan(race.id)) == 10
    restarted = env.races.restart_aborted(race.id)
    assert restarted.repetitions == 2
    assert restarted.id != race.id
    fresh = env.races.heat_plan(restarted.id)
    fresh_ids = [participant.id for participant in restarted.participants]
    assert len(fresh) == 10
    assert [heat.status for heat in fresh] == ["planned"] * 10
    assert [heat.seats for heat in fresh] == list(pairwise_cycle(fresh_ids)) * 2
    original = env.races.require_race(race.id)
    assert original.status is RaceStatus.ABORTED
    assert len(env.races.heat_plan(race.id)) == 10
    with env.runtime.database.session() as session:
        stored = session.get(Race, int(restarted.id))
        assert stored is not None and stored.repetitions == 2


def test_the_wizard_stores_three_repetitions_as_fifteen_heats(qtbot: QtBot, env: Env) -> None:
    track = env.track("Heimbahn", lanes=2)
    drivers = [env.driver(f"Fahrer {index}") for index in range(1, 6)]
    for driver in drivers:
        env.vehicle(f"Auto {driver.id}", driver_id=driver.id)
    _, page = open_page(qtbot, env, "races")
    assert isinstance(page, RacesPage)
    page.new_race()
    wizard = page.wizard
    assert isinstance(wizard, RaceWizard)
    wizard.name_edit.setText("Langstrecke")
    assert wizard.go_next()
    wizard.track_combo.setCurrentIndex(wizard.track_combo.findData(track.id))
    assert wizard.go_next()
    wizard.repetitions_spin.setValue(3)
    assert wizard.go_next()
    for driver in drivers:
        wizard.driver_combo.setCurrentIndex(wizard.driver_combo.findData(driver.id))
        assert wizard.add_participant()
    assert wizard.go_next()
    stored = env.races.require_race(wizard.race.id) if wizard.race is not None else None
    assert stored is not None
    assert stored.repetitions == 3
    assert len(env.races.heat_plan(stored.id)) == 15
    assert "Durchlauf 6 von 15" in wizard.overview_label.text()
    assert "Durchlauf 15 von 15" in wizard.overview_label.text()


def _planned(
    env: Env, *, drivers: int, repetitions: int, active_lanes: int | None = None
) -> RaceInfo:
    track = env.track(lanes=2)
    race = env.races.create_race("Rotation", track.id, 1, repetitions=repetitions)
    if active_lanes is not None:
        race = env.races.update_race(
            race.id,
            race.name,
            track.id,
            1,
            set_active_lanes=True,
            active_lanes=active_lanes,
            set_repetitions=True,
            repetitions=repetitions,
        )
    for index in range(1, drivers + 1):
        driver, vehicle = env.pair(index)
        env.races.enroll_driver(race.id, driver, vehicle)
    env.races.plan_heats(race.id)
    return env.races.require_race(race.id)


def _finish_heat(env: Env, race_id: RaceId) -> RaceInfo:
    runner = env.controller.start_race(race_id)
    for _ in range(40):
        if not runner.is_active:
            break
        env.clock.advance(1_000_000_000)
        runner.tick()
    assert not runner.is_active
    return env.races.require_race(race_id)
