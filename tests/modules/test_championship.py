"""Championship storage, scoring from real races, and the championship screens."""

from __future__ import annotations

from datetime import date

import pytest
from alembic import command
from PySide6.QtWidgets import QDialog
from pytestqt.qtbot import QtBot
from sqlalchemy import inspect, select

from slot_racing.core.catalog import DriverInfo
from slot_racing.core.championship import DEFAULT_PLACE_POINTS, DriverStanding
from slot_racing.core.domain import DriverId, ParticipantResult, RaceId
from slot_racing.core.errors import ValidationError
from slot_racing.core.storage import Database
from slot_racing.core.storage.database import alembic_config
from slot_racing.modules.championships.service import (
    ACTIVE,
    COMPLETED,
    PLANNED,
    ChampionshipInput,
    ChampionshipService,
)
from slot_racing.modules.championships.ui.page import (
    ChampionshipDialog,
    ChampionshipsPage,
    RaceResultDialog,
)
from slot_racing.modules.drivers_vehicles.models import Driver
from slot_racing.modules.races.models import Race, RaceParticipant
from slot_racing.modules.races.types import RaceInfo
from slot_racing.modules.tracks.models import Track
from tests.modules.conftest import Env
from tests.modules.test_ui_management import cells, open_page, runner_for


def test_create_edit_status_and_delete_keep_the_races(env: Env) -> None:
    service = _service(env)
    created = service.create(
        ChampionshipInput(
            "Sommercup",
            2026,
            description="Club",
            starts_on=date(2026, 4, 1),
            ends_on=date(2026, 10, 1),
            teams_enabled=True,
        )
    )
    assert created.status == PLANNED
    assert created.teams_enabled is True
    board = service.board(created.id)
    assert board.points == DEFAULT_PLACE_POINTS
    updated = service.update(
        created.id,
        ChampionshipInput("Herbstcup", 2027, description=None, teams_enabled=False),
    )
    assert updated.name == "Herbstcup"
    assert updated.teams_enabled is False
    assert service.set_status(created.id, ACTIVE).status == ACTIVE
    race, _anna, _ben = _finish(env, "Lauf")
    service.add_race(created.id, int(race.id))
    assert service.set_status(created.id, COMPLETED).closed is True
    with pytest.raises(ValidationError) as closed:
        service.update(created.id, ChampionshipInput("Neu", 2027))
    assert closed.value.key == "error.championship.closed"
    with pytest.raises(ValidationError):
        service.set_status(created.id, ACTIVE)
    service.delete(created.id)
    assert service.list_championships() == ()
    assert env.races.get_completed(race.id) is not None


def test_invalid_identity_and_status_are_rejected(env: Env) -> None:
    service = _service(env)
    with pytest.raises(ValidationError) as name:
        service.create(ChampionshipInput("  ", 2026))
    assert name.value.key == "error.championship.name"
    with pytest.raises(ValidationError) as season:
        service.create(ChampionshipInput("Cup", 1800))
    assert season.value.key == "error.championship.season"
    with pytest.raises(ValidationError) as period:
        service.create(
            ChampionshipInput("Cup", 2026, starts_on=date(2026, 6, 2), ends_on=date(2026, 6, 1))
        )
    assert period.value.key == "error.championship.period"
    created = service.create(ChampionshipInput("Cup", 2026))
    with pytest.raises(ValidationError):
        service.set_status(created.id, "archived")
    service.set_status(created.id, COMPLETED)
    with pytest.raises(ValidationError):
        service.set_status(created.id, PLANNED)


def test_race_order_removal_and_a_single_championship_per_race(env: Env) -> None:
    service = _service(env)
    cup = service.create(ChampionshipInput("Cup", 2026)).id
    other = service.create(ChampionshipInput("Andere", 2026)).id
    first, _anna, _ben = _finish(env, "Eins")
    second, _anna, _ben = _finish(env, "Zwei")
    third, _anna, _ben = _finish(env, "Drei")
    open_race = env.races.create_race("Offen", env.track().id, 3)
    for race in (first, second, third, open_race):
        service.add_race(cup, int(race.id))
    service.move_race(cup, int(first.id), 1)
    service.move_race(cup, int(third.id), -1)
    assert [race.name for race in service.board(cup).races] == ["Zwei", "Drei", "Eins", "Offen"]
    service.remove_race(cup, int(second.id))
    assert [race.name for race in service.board(cup).races] == ["Drei", "Eins", "Offen"]
    assert env.races.get_completed(first.id) is not None
    with pytest.raises(ValidationError) as taken:
        service.add_race(other, int(first.id))
    assert taken.value.key == "error.championship.race_taken"
    with pytest.raises(ValidationError) as missing:
        service.add_race(cup, 999_999)
    assert missing.value.key == "error.championship.race_missing"
    service.set_status(cup, COMPLETED)
    with pytest.raises(ValidationError):
        service.remove_race(cup, int(first.id))


def test_scheme_bonus_drops_and_result_changes_update_the_table(env: Env) -> None:
    service = _service(env)
    cup = service.create(ChampionshipInput("Cup", 2026)).id
    first, anna, ben = _finish(env, "Eins")
    second, _anna, _ben = _finish(env, "Zwei", anna_time=7_000_000_000, ben_time=8_000_000_000)
    service.add_race(cup, int(first.id))
    service.add_race(cup, int(second.id))
    board = service.board(cup)
    anna_row = _standing(board.standings, anna.id)
    assert anna_row.points == 50
    assert anna_row.gap == 0
    service.save_points(cup, ((1, 10), (2, 6)), fastest_lap_bonus=2, drop_count=1)
    scored = service.board(cup)
    anna_row = _standing(scored.standings, anna.id)
    assert anna_row.points == 12
    assert anna_row.bonus_points == 2
    assert anna_row.counted_races == 1
    assert anna_row.wins == 2
    third, _anna, _ben = _finish(
        env,
        "Drei",
        anna_time=9_000_000_000,
        ben_time=7_000_000_000,
        anna_position=2,
        ben_position=1,
    )
    service.add_race(cup, int(third.id))
    with_third = service.board(cup)
    assert _standing(with_third.standings, anna.id).points == 24
    assert _standing(with_third.standings, ben.id).points == 18
    _set_disqualified(env, first.id, anna.id)
    after = service.board(cup)
    anna_after = _standing(after.standings, anna.id)
    assert anna_after.results[0].disqualified is True
    assert anna_after.results[0].place_points == 0
    assert anna_after.results[0].bonus_points == 0
    assert anna_after.points == 12
    assert _standing(after.standings, ben.id).points == 20
    aborted, _anna, _ben = _abort(env, "Abbruch")
    service.add_race(cup, int(aborted.id))
    aborted_board = service.board(cup)
    aborted_score = next(
        result
        for result in _standing(aborted_board.standings, anna.id).results
        if result.race_id == int(aborted.id)
    )
    assert aborted_score.total == 0
    assert aborted_score.classified is False


def test_an_open_race_scores_only_after_it_is_finished(env: Env) -> None:
    service = _service(env)
    cup = service.create(ChampionshipInput("Cup", 2026)).id
    track = env.track()
    anna = env.driver("Anna")
    ben = env.driver("Ben")
    porsche = env.vehicle("Porsche", anna.id)
    bmw = env.vehicle("BMW", ben.id)
    race = env.races.create_race("Offen", track.id, 1)
    env.races.add_participant(race.id, anna.id, porsche.id, 1)
    env.races.add_participant(race.id, ben.id, bmw.id, 2)
    service.add_race(cup, int(race.id))
    assert service.board(cup).standings == ()
    env.races.record_lap(race.id, 1, 1, 8_000_000_000, 8_000_000_000, {})
    env.races.record_finished(
        race.id,
        [ParticipantResult(anna.id, 1, 1, 1, True, 8_000_000_000, 8_000_000_000)],
        aborted=False,
    )
    standing = service.board(cup).standings
    assert standing[0].driver_label == "Anna"
    assert standing[0].points == 25


def test_team_history_survives_a_roster_change_until_refresh(env: Env) -> None:
    service = _service(env)
    cup = service.create(ChampionshipInput("Cup", 2026, teams_enabled=True)).id
    red = service.add_team(cup, "Rot")
    blue = service.add_team(cup, "Blau")
    race, anna, ben = _finish(env, "Lauf")
    service.add_race(cup, int(race.id))
    service.assign_driver(cup, int(anna.id), red.id)
    service.assign_driver(cup, int(ben.id), red.id)
    frozen = service.board(cup)
    assert frozen.teams[0].name == "Rot"
    assert frozen.teams[0].points == 25 + 18
    assert frozen.teams[0].drivers == 2
    service.assign_driver(cup, int(anna.id), blue.id)
    held = service.board(cup)
    assert {row.name: row.points for row in held.teams}["Rot"] == 43
    assert {row.name: row.points for row in held.teams}["Blau"] == 0
    with pytest.raises(ValidationError) as used:
        service.delete_team(cup, red.id)
    assert used.value.key == "error.championship.team_in_use"
    service.refresh_race_teams(cup, int(race.id))
    moved = {row.name: row.points for row in service.board(cup).teams}
    assert moved["Rot"] == 18
    assert moved["Blau"] == 25
    with pytest.raises(ValidationError):
        service.delete_team(cup, blue.id)
    service.remove_race(cup, int(race.id))
    service.delete_team(cup, blue.id)
    assert "Blau" not in {row.name for row in service.board(cup).team_rows}
    assert env.races.get_completed(race.id) is not None


def test_teams_can_be_switched_off_without_losing_the_driver_table(env: Env) -> None:
    service = _service(env)
    cup = service.create(ChampionshipInput("Cup", 2026, teams_enabled=True)).id
    team = service.add_team(cup, "Rot")
    race, anna, _ben = _finish(env, "Lauf")
    service.add_race(cup, int(race.id))
    service.assign_driver(cup, int(anna.id), team.id)
    service.board(cup)
    service.update(cup, ChampionshipInput("Cup", 2026, teams_enabled=False))
    board = service.board(cup)
    assert board.teams == ()
    assert board.standings[0].points == 25
    assert board.team_rows[0].name == "Rot"
    with pytest.raises(ValidationError) as off:
        service.add_team(cup, "Blau")
    assert off.value.key == "error.championship.teams_off"


def test_deleting_a_race_or_a_driver_follows_the_foreign_keys(env: Env) -> None:
    service = _service(env)
    cup = service.create(ChampionshipInput("Cup", 2026, teams_enabled=True)).id
    team = service.add_team(cup, "Rot")
    race, anna, _ben = _finish(env, "Lauf")
    other, _anna, _ben = _finish(env, "Andere")
    service.add_race(cup, int(race.id))
    service.add_race(cup, int(other.id))
    service.assign_driver(cup, int(anna.id), team.id)
    service.board(cup)
    guest = env.driver("Gast")
    service.assign_driver(cup, int(guest.id), team.id)
    env.drivers.delete_driver(guest.id)
    assert all(member.driver_id != int(guest.id) for member in service.board(cup).members)
    with pytest.raises(ValidationError) as racing:
        env.drivers.delete_driver(DriverId(anna.id))
    assert racing.value.key == "error.driver.in_use"
    env.races.delete_race(race.id)
    remaining = service.board(cup)
    assert [row.name for row in remaining.races] == ["Andere"]
    assert env.races.get_completed(other.id) is not None
    service.delete(cup)
    assert env.races.get_completed(other.id) is not None


def test_migration_adds_championship_tables_and_keeps_existing_races() -> None:
    database = Database.in_memory()
    database.migrate("0017")
    with database.session() as session:
        track = Track(name="Ring", lane_count=2, is_active=True)
        session.add(track)
        session.flush()
        session.add(Race(name="Historisch", track_id=track.id, target_laps=5))
        session.flush()
    database.migrate()
    assert database.schema_revision() == "0018"
    tables = set(inspect(database.engine).get_table_names())
    assert {
        "championships",
        "championship_points",
        "championship_races",
        "championship_teams",
        "championship_members",
        "championship_race_teams",
    } <= tables
    with database.session() as session:
        assert session.scalar(select(Race.name)) == "Historisch"
        session.add(Driver(name="Anna", is_active=True))
    _downgrade(database, "0017")
    assert database.schema_revision() == "0017"
    assert "championships" not in set(inspect(database.engine).get_table_names())
    with database.session() as session:
        assert session.scalar(select(Race.name)) == "Historisch"
        assert session.scalar(select(Driver.name)) == "Anna"
    database.migrate()
    assert database.schema_revision() == "0018"
    database.dispose()


@pytest.mark.parametrize("attempt", [1, 2])
def test_the_championship_page_scores_a_calendar(qtbot: QtBot, env: Env, attempt: int) -> None:
    del attempt
    first, _anna, _ben = _finish(env, "Lauf 1")
    _finish(env, "Lauf 2")
    _window, page = open_page(qtbot, env, "championships")
    assert isinstance(page, ChampionshipsPage)
    page.refresh()
    assert page.table.rowCount() == 0
    assert not page.empty.isHidden()

    def fill_new(dialog: QDialog) -> None:
        assert isinstance(dialog, ChampionshipDialog)
        dialog.name_edit.setText("Sommercup")
        dialog.season_edit.setValue(2026)

    page.dialog_runner = runner_for(fill_new)
    page.add()
    assert cells(page.table, 0) == ["Sommercup", "2026", "Geplant", "0"]
    page.table.selectRow(0)
    page.open_selected()
    assert page.teams_group.isHidden()
    page.race_choices.setCurrentIndex(page.race_choices.findText("Lauf 1"))
    page.add_race()
    page.race_choices.setCurrentIndex(page.race_choices.findText("Lauf 2"))
    page.add_race()
    assert [cells(page.races_table, row)[1] for row in range(2)] == ["Lauf 1", "Lauf 2"]
    assert cells(page.standings_table, 0)[1] == "Anna"
    assert cells(page.standings_table, 0)[2] == "50"
    assert cells(page.standings_table, 1)[2] == "36"
    assert cells(page.standings_table, 1)[7] == "14"
    page.races_table.selectRow(0)
    assert cells(page.race_scores, 0)[0] == "Anna"
    assert cells(page.race_scores, 0)[4] == "25"
    opened: list[RaceResultDialog] = []

    def show_result(dialog: QDialog) -> int:
        assert isinstance(dialog, RaceResultDialog)
        opened.append(dialog)
        return 0

    page.dialog_runner = show_result
    page.open_selected_race()
    assert len(opened) == 1
    assert opened[0].report.table.rowCount() == 2
    page.bonus_edit.setValue(1)
    page.drops_edit.setValue(1)
    page.save_rules()
    assert cells(page.standings_table, 0)[2] == "26"
    page.show_list()

    def enable_teams(dialog: QDialog) -> None:
        assert isinstance(dialog, ChampionshipDialog)
        dialog.teams_box.setChecked(True)

    page.dialog_runner = runner_for(enable_teams)
    page.table.selectRow(0)
    page.edit_selected()
    page.open_selected()
    assert not page.teams_group.isHidden()
    page.team_name.setText("Rot")
    page.add_team()
    assert cells(page.team_table, 0)[0] == "Rot"
    page.show_list()
    page.confirm = lambda _text: True
    page.table.selectRow(0)
    page.archive_selected()
    assert cells(page.table, 0)[2] == "Abgeschlossen"
    page.open_selected()
    assert not page.closed_label.isHidden()
    assert not page.add_race_button.isEnabled()
    assert not page.save_rules_button.isEnabled()
    page.show_list()
    page.delete_selected()
    assert page.table.rowCount() == 0
    assert env.races.get_completed(first.id) is not None


def _downgrade(database: Database, revision: str) -> None:
    config = alembic_config()
    with database.engine.connect() as connection:
        config.attributes["connection"] = connection
        command.downgrade(config, revision)


def _service(env: Env) -> ChampionshipService:
    return env.runtime.services.get(ChampionshipService)


def _standing(standings: tuple[DriverStanding, ...], driver_id: DriverId) -> DriverStanding:
    return next(row for row in standings if row.driver_id == int(driver_id))


def _finish(
    env: Env,
    name: str,
    *,
    anna_time: int = 8_000_000_000,
    ben_time: int = 9_000_000_000,
    anna_position: int = 1,
    ben_position: int = 2,
) -> tuple[RaceInfo, DriverInfo, DriverInfo]:
    track = env.track()
    anna = _person(env, "Anna")
    ben = _person(env, "Ben")
    porsche = env.vehicle("Porsche", anna.id)
    bmw = env.vehicle("BMW", ben.id)
    race = env.races.create_race(name, track.id, 1)
    env.races.add_participant(race.id, anna.id, porsche.id, 1)
    env.races.add_participant(race.id, ben.id, bmw.id, 2)
    env.races.record_lap(race.id, 1, 1, anna_time, anna_time, {})
    env.races.record_lap(race.id, 2, 1, ben_time, ben_time, {})
    results = [
        ParticipantResult(anna.id, 1, anna_position, 1, True, anna_time, anna_time),
        ParticipantResult(ben.id, 2, ben_position, 1, True, ben_time, ben_time),
    ]
    env.races.record_finished(race.id, results, aborted=False)
    return race, anna, ben


def _abort(env: Env, name: str) -> tuple[RaceInfo, DriverInfo, DriverInfo]:
    track = env.track()
    anna = _person(env, "Anna")
    ben = _person(env, "Ben")
    porsche = env.vehicle("Porsche", anna.id)
    bmw = env.vehicle("BMW", ben.id)
    race = env.races.create_race(name, track.id, 1)
    env.races.add_participant(race.id, anna.id, porsche.id, 1)
    env.races.add_participant(race.id, ben.id, bmw.id, 2)
    env.races.record_finished(
        race.id,
        [
            ParticipantResult(anna.id, 1, 1, 0, False, None, None),
            ParticipantResult(ben.id, 2, 2, 0, False, None, None),
        ],
        aborted=True,
    )
    return race, anna, ben


def _person(env: Env, name: str) -> DriverInfo:
    found = next((driver for driver in env.drivers.list_drivers() if driver.name == name), None)
    return env.driver(name) if found is None else found


def _set_disqualified(env: Env, race_id: RaceId, driver_id: DriverId) -> None:
    database = env.runtime.services.get(Database)
    with database.session() as session:
        row = session.scalar(
            select(RaceParticipant).where(
                RaceParticipant.race_id == int(race_id),
                RaceParticipant.driver_id == int(driver_id),
            )
        )
        assert row is not None
        row.disqualified = True
