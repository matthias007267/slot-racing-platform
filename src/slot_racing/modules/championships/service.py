"""Championship configuration. Points are calculated from the stored races, not copied."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from sqlalchemy import delete, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from slot_racing.core.catalog import DriverCatalog, RaceHistoryCatalog, RaceIndexEntry
from slot_racing.core.championship import (
    DEFAULT_PLACE_POINTS,
    TIE_BREAK,
    DriverStanding,
    PointsRule,
    TeamStanding,
    score_championship,
    score_teams,
)
from slot_racing.core.domain import DriverId, RaceId, RaceMode, RaceStatus
from slot_racing.core.errors import ValidationError
from slot_racing.core.statistics import HistoryRace
from slot_racing.core.storage import Database, utc_now
from slot_racing.modules.championships.models import (
    Championship,
    ChampionshipMember,
    ChampionshipPoint,
    ChampionshipRaceLink,
    ChampionshipRaceTeam,
    ChampionshipTeam,
)

PLANNED = "planned"
ACTIVE = "active"
COMPLETED = "completed"
_STATUSES = (PLANNED, ACTIVE, COMPLETED)
_FORWARD = {PLANNED: {ACTIVE, COMPLETED}, ACTIVE: {COMPLETED}, COMPLETED: set()}


@dataclass(frozen=True, slots=True)
class ChampionshipInput:
    name: str
    season_year: int
    description: str | None = None
    starts_on: date | None = None
    ends_on: date | None = None
    teams_enabled: bool = False


@dataclass(frozen=True, slots=True)
class ChampionshipInfo:
    id: int
    name: str
    description: str | None
    season_year: int
    starts_on: date | None
    ends_on: date | None
    status: str
    teams_enabled: bool
    fastest_lap_bonus: int
    drop_count: int
    tie_break: str
    race_count: int

    @property
    def closed(self) -> bool:
        return self.status == COMPLETED


@dataclass(frozen=True, slots=True)
class LinkedRace:
    race_id: int
    name: str
    status: RaceStatus
    sort_order: int


@dataclass(frozen=True, slots=True)
class TeamInfo:
    id: int
    name: str


@dataclass(frozen=True, slots=True)
class MemberInfo:
    driver_id: int
    driver_label: str
    team_id: int
    team_name: str


@dataclass(frozen=True, slots=True)
class ChampionshipBoard:
    info: ChampionshipInfo
    races: tuple[LinkedRace, ...]
    standings: tuple[DriverStanding, ...]
    teams: tuple[TeamStanding, ...]
    team_rows: tuple[TeamInfo, ...]
    members: tuple[MemberInfo, ...]
    points: tuple[tuple[int, int], ...]
    available: tuple[RaceIndexEntry, ...]


class ChampionshipService:
    def __init__(
        self, database: Database, history: RaceHistoryCatalog, drivers: DriverCatalog
    ) -> None:
        self._database = database
        self._history = history
        self._drivers = drivers

    def list_championships(self) -> tuple[ChampionshipInfo, ...]:
        with self._database.session() as session:
            rows = session.scalars(select(Championship).order_by(Championship.id)).all()
            return tuple(self._info(session, row) for row in rows)

    def get(self, championship_id: int) -> ChampionshipInfo:
        with self._database.session() as session:
            return self._info(session, self._load(session, championship_id))

    def create(self, data: ChampionshipInput) -> ChampionshipInfo:
        name, description, year, starts, ends = _identity(data)
        with self._database.session() as session:
            row = Championship(
                name=name,
                description=description,
                season_year=year,
                starts_on=starts,
                ends_on=ends,
                status=PLANNED,
                teams_enabled=data.teams_enabled,
                fastest_lap_bonus=0,
                drop_count=0,
                tie_break=TIE_BREAK,
                created_at=utc_now(),
            )
            session.add(row)
            session.flush()
            for place, points in DEFAULT_PLACE_POINTS:
                session.add(ChampionshipPoint(championship_id=row.id, place=place, points=points))
            session.flush()
            return self._info(session, row)

    def update(self, championship_id: int, data: ChampionshipInput) -> ChampionshipInfo:
        name, description, year, starts, ends = _identity(data)
        with self._database.session() as session:
            row = self._load_open(session, championship_id)
            row.name = name
            row.description = description
            row.season_year = year
            row.starts_on = starts
            row.ends_on = ends
            row.teams_enabled = data.teams_enabled
            session.flush()
            return self._info(session, row)

    def set_status(self, championship_id: int, status: str) -> ChampionshipInfo:
        if status not in _STATUSES:
            raise ValidationError("error.championship.status")
        with self._database.session() as session:
            row = self._load(session, championship_id)
            if status not in _FORWARD[row.status]:
                raise ValidationError("error.championship.status")
            if status == COMPLETED:
                self._freeze(session, row)
            row.status = status
            session.flush()
            return self._info(session, row)

    def delete(self, championship_id: int) -> None:
        with self._database.session() as session:
            session.delete(self._load(session, championship_id))

    def save_points(
        self,
        championship_id: int,
        points: tuple[tuple[int, int], ...],
        *,
        fastest_lap_bonus: int,
        drop_count: int,
    ) -> ChampionshipInfo:
        scheme = _valid_points(points, fastest_lap_bonus, drop_count)
        with self._database.session() as session:
            row = self._load_open(session, championship_id)
            session.execute(
                delete(ChampionshipPoint).where(ChampionshipPoint.championship_id == row.id)
            )
            for place, value in scheme:
                session.add(ChampionshipPoint(championship_id=row.id, place=place, points=value))
            row.fastest_lap_bonus = fastest_lap_bonus
            row.drop_count = drop_count
            session.flush()
            return self._info(session, row)

    def add_race(self, championship_id: int, race_id: int) -> None:
        known = {entry.race_id for entry in self._history.list_index()}
        if race_id not in known:
            raise ValidationError("error.championship.race_missing")
        with self._database.session() as session:
            row = self._load_open(session, championship_id)
            current = session.scalar(
                select(func.max(ChampionshipRaceLink.sort_order)).where(
                    ChampionshipRaceLink.championship_id == row.id
                )
            )
            session.add(
                ChampionshipRaceLink(
                    championship_id=row.id,
                    race_id=race_id,
                    sort_order=1 if current is None else int(current) + 1,
                )
            )
            try:
                session.flush()
            except IntegrityError as error:
                raise ValidationError("error.championship.race_taken") from error

    def remove_race(self, championship_id: int, race_id: int) -> None:
        with self._database.session() as session:
            row = self._load_open(session, championship_id)
            link = session.scalar(
                select(ChampionshipRaceLink).where(
                    ChampionshipRaceLink.championship_id == row.id,
                    ChampionshipRaceLink.race_id == race_id,
                )
            )
            if link is None:
                raise ValidationError("error.championship.race_missing")
            session.execute(
                delete(ChampionshipRaceTeam).where(
                    ChampionshipRaceTeam.championship_id == row.id,
                    ChampionshipRaceTeam.race_id == race_id,
                )
            )
            session.delete(link)
            session.flush()
            _compact(session, row.id)

    def move_race(self, championship_id: int, race_id: int, delta: int) -> None:
        if delta not in (-1, 1):
            raise ValidationError("error.championship.order")
        with self._database.session() as session:
            row = self._load_open(session, championship_id)
            links = list(
                session.scalars(
                    select(ChampionshipRaceLink)
                    .where(ChampionshipRaceLink.championship_id == row.id)
                    .order_by(ChampionshipRaceLink.sort_order, ChampionshipRaceLink.id)
                )
            )
            index = next((i for i, link in enumerate(links) if link.race_id == race_id), None)
            if index is None:
                raise ValidationError("error.championship.race_missing")
            other = index + delta
            if other < 0 or other >= len(links):
                return
            first = links[index]
            second = links[other]
            first_order = first.sort_order
            second_order = second.sort_order
            first.sort_order = -1
            session.flush()
            second.sort_order = first_order
            session.flush()
            first.sort_order = second_order

    def add_team(self, championship_id: int, name: str) -> TeamInfo:
        clean = _name(name, "error.championship.team_name")
        with self._database.session() as session:
            row = self._load_open(session, championship_id)
            if not row.teams_enabled:
                raise ValidationError("error.championship.teams_off")
            team = ChampionshipTeam(championship_id=row.id, name=clean)
            session.add(team)
            try:
                session.flush()
            except IntegrityError as error:
                raise ValidationError("error.championship.team_name") from error
            return TeamInfo(id=int(team.id), name=team.name)

    def delete_team(self, championship_id: int, team_id: int) -> None:
        with self._database.session() as session:
            row = self._load_open(session, championship_id)
            team = session.get(ChampionshipTeam, team_id)
            if team is None or team.championship_id != row.id:
                raise ValidationError("error.championship.team_missing")
            try:
                session.delete(team)
                session.flush()
            except IntegrityError as error:
                raise ValidationError("error.championship.team_in_use") from error

    def assign_driver(self, championship_id: int, driver_id: int, team_id: int | None) -> None:
        """Set the current team. Snapshots of finished races stay as they were."""
        if self._drivers.get_driver(DriverId(driver_id)) is None:
            raise ValidationError("error.championship.driver_missing")
        with self._database.session() as session:
            row = self._load_open(session, championship_id)
            member = session.scalar(
                select(ChampionshipMember).where(
                    ChampionshipMember.championship_id == row.id,
                    ChampionshipMember.driver_id == driver_id,
                )
            )
            if team_id is None:
                if member is not None:
                    session.delete(member)
                return
            team = session.get(ChampionshipTeam, team_id)
            if team is None or team.championship_id != row.id:
                raise ValidationError("error.championship.team_missing")
            if member is None:
                session.add(
                    ChampionshipMember(
                        championship_id=row.id,
                        team_id=team.id,
                        driver_id=driver_id,
                        assigned_at=utc_now(),
                    )
                )
            else:
                member.team_id = team.id
                member.assigned_at = utc_now()

    def refresh_race_teams(self, championship_id: int, race_id: int) -> None:
        """Replace the frozen teams of one finished race with the current roster."""
        with self._database.session() as session:
            row = self._load_open(session, championship_id)
            link = session.scalar(
                select(ChampionshipRaceLink).where(
                    ChampionshipRaceLink.championship_id == row.id,
                    ChampionshipRaceLink.race_id == race_id,
                )
            )
            if link is None:
                raise ValidationError("error.championship.race_missing")
            finished = {
                race.race_id: race
                for race in self._history.list_completed()
                if race.race_id == race_id and race.status is RaceStatus.FINISHED
            }
            if race_id not in finished:
                raise ValidationError("error.championship.race_open")
            session.execute(
                delete(ChampionshipRaceTeam).where(
                    ChampionshipRaceTeam.championship_id == row.id,
                    ChampionshipRaceTeam.race_id == race_id,
                )
            )
            session.flush()
            self._freeze(session, row, only_race=race_id)

    def board(self, championship_id: int) -> ChampionshipBoard:
        with self._database.session() as session:
            row = self._load(session, championship_id)
            self._freeze(session, row)
            session.flush()
            return self._board(session, row)

    def completed_race(self, race_id: int) -> HistoryRace | None:
        return self._history.get_completed(RaceId(race_id))

    def _board(self, session: Session, row: Championship) -> ChampionshipBoard:
        links = list(
            session.scalars(
                select(ChampionshipRaceLink)
                .where(ChampionshipRaceLink.championship_id == row.id)
                .order_by(ChampionshipRaceLink.sort_order, ChampionshipRaceLink.id)
            )
        )
        index = {entry.race_id: entry for entry in self._history.list_index()}
        completed = {race.race_id: race for race in self._history.list_completed()}
        races = tuple(_calendar(links, index))
        ordered = tuple(_history_for(link, completed, index) for link in links)
        rule = _rule(row, _points(session, row.id))
        standings = score_championship(ordered, rule)
        team_rows = tuple(
            TeamInfo(id=int(team.id), name=team.name)
            for team in session.scalars(
                select(ChampionshipTeam)
                .where(ChampionshipTeam.championship_id == row.id)
                .order_by(ChampionshipTeam.name, ChampionshipTeam.id)
            )
        )
        members = _members(session, row.id, self._drivers)
        assignments: dict[tuple[int, int], int] = {}
        snapshots = session.scalars(
            select(ChampionshipRaceTeam).where(ChampionshipRaceTeam.championship_id == row.id)
        )
        for snap in snapshots:
            if snap.team_id is not None:
                assignments[(int(snap.race_id), int(snap.driver_id))] = int(snap.team_id)
        roster: dict[int, list[int]] = {}
        for member in members:
            roster.setdefault(member.team_id, []).append(member.driver_id)
        teams = (
            score_teams(
                standings,
                tuple((team.id, team.name) for team in team_rows),
                assignments,
                roster,
            )
            if row.teams_enabled
            else ()
        )
        used = set(session.scalars(select(ChampionshipRaceLink.race_id)).all())
        available = tuple(
            entry for entry in self._history.list_index() if entry.race_id not in used
        )
        return ChampionshipBoard(
            info=self._info(session, row),
            races=races,
            standings=standings,
            teams=teams,
            team_rows=team_rows,
            members=members,
            points=_points(session, row.id),
            available=available,
        )

    def _freeze(self, session: Session, row: Championship, *, only_race: int | None = None) -> None:
        links = list(
            session.scalars(
                select(ChampionshipRaceLink).where(ChampionshipRaceLink.championship_id == row.id)
            )
        )
        completed = {
            race.race_id: race
            for race in self._history.list_completed()
            if race.status is RaceStatus.FINISHED
        }
        members = {
            int(member.driver_id): int(member.team_id)
            for member in session.scalars(
                select(ChampionshipMember).where(ChampionshipMember.championship_id == row.id)
            )
        }
        names = {
            int(team.id): team.name
            for team in session.scalars(
                select(ChampionshipTeam).where(ChampionshipTeam.championship_id == row.id)
            )
        }
        existing = {
            (int(snap.race_id), int(snap.driver_id))
            for snap in session.scalars(
                select(ChampionshipRaceTeam).where(ChampionshipRaceTeam.championship_id == row.id)
            )
        }
        for link in links:
            if only_race is not None and link.race_id != only_race:
                continue
            race = completed.get(link.race_id)
            if race is None:
                continue
            seen: set[int] = set()
            for person in race.participants:
                if person.driver_id in seen:
                    continue
                seen.add(person.driver_id)
                if (link.race_id, person.driver_id) in existing:
                    continue
                team_id = members.get(person.driver_id)
                session.add(
                    ChampionshipRaceTeam(
                        championship_id=row.id,
                        race_id=link.race_id,
                        driver_id=person.driver_id,
                        team_id=team_id,
                        team_name=None if team_id is None else names.get(team_id),
                    )
                )

    def _info(self, session: Session, row: Championship) -> ChampionshipInfo:
        count = session.scalar(
            select(func.count())
            .select_from(ChampionshipRaceLink)
            .where(ChampionshipRaceLink.championship_id == row.id)
        )
        return ChampionshipInfo(
            id=int(row.id),
            name=row.name,
            description=row.description,
            season_year=int(row.season_year),
            starts_on=row.starts_on,
            ends_on=row.ends_on,
            status=row.status,
            teams_enabled=bool(row.teams_enabled),
            fastest_lap_bonus=int(row.fastest_lap_bonus),
            drop_count=int(row.drop_count),
            tie_break=row.tie_break,
            race_count=int(count or 0),
        )

    def _load(self, session: Session, championship_id: int) -> Championship:
        row = session.get(Championship, championship_id)
        if row is None:
            raise ValidationError("error.championship.not_found")
        return row

    def _load_open(self, session: Session, championship_id: int) -> Championship:
        row = self._load(session, championship_id)
        if row.status == COMPLETED:
            raise ValidationError("error.championship.closed")
        return row


def _identity(data: ChampionshipInput) -> tuple[str, str | None, int, date | None, date | None]:
    name = _name(data.name, "error.championship.name")
    description = (data.description or "").strip() or None
    if description is not None and len(description) > 500:
        raise ValidationError("error.championship.description", limit=500)
    if data.season_year < 1950 or data.season_year > 2100:
        raise ValidationError("error.championship.season")
    if data.starts_on is not None and data.ends_on is not None and data.ends_on < data.starts_on:
        raise ValidationError("error.championship.period")
    return name, description, data.season_year, data.starts_on, data.ends_on


def _name(value: str, key: str) -> str:
    clean = value.strip()
    if not clean:
        raise ValidationError(key)
    if len(clean) > 100:
        raise ValidationError(f"{key}.long", limit=100)
    return clean


def _valid_points(
    points: tuple[tuple[int, int], ...], bonus: int, drop_count: int
) -> tuple[tuple[int, int], ...]:
    if bonus < 0 or drop_count < 0:
        raise ValidationError("error.championship.points")
    if not points:
        raise ValidationError("error.championship.points")
    seen: set[int] = set()
    cleaned: list[tuple[int, int]] = []
    for place, value in points:
        if place < 1 or value < 0 or place in seen:
            raise ValidationError("error.championship.points")
        seen.add(place)
        cleaned.append((place, value))
    return tuple(sorted(cleaned))


def _compact(session: Session, championship_id: int) -> None:
    """Renumber the calendar from 1. The unique order is parked below zero first."""
    links = list(
        session.scalars(
            select(ChampionshipRaceLink)
            .where(ChampionshipRaceLink.championship_id == championship_id)
            .order_by(ChampionshipRaceLink.sort_order, ChampionshipRaceLink.id)
        )
    )
    for index, link in enumerate(links, start=1):
        link.sort_order = -index
    session.flush()
    for index, link in enumerate(links, start=1):
        link.sort_order = index


def _points(session: Session, championship_id: int) -> tuple[tuple[int, int], ...]:
    rows = session.scalars(
        select(ChampionshipPoint)
        .where(ChampionshipPoint.championship_id == championship_id)
        .order_by(ChampionshipPoint.place)
    ).all()
    return tuple((int(row.place), int(row.points)) for row in rows)


def _rule(row: Championship, points: tuple[tuple[int, int], ...]) -> PointsRule:
    return PointsRule(
        points_by_place=points,
        fastest_lap_bonus=int(row.fastest_lap_bonus),
        drop_count=int(row.drop_count),
    )


def _members(
    session: Session, championship_id: int, drivers: DriverCatalog
) -> tuple[MemberInfo, ...]:
    rows = session.scalars(
        select(ChampionshipMember)
        .where(ChampionshipMember.championship_id == championship_id)
        .order_by(ChampionshipMember.id)
    ).all()
    found: list[MemberInfo] = []
    for row in rows:
        team = session.get(ChampionshipTeam, row.team_id)
        driver = drivers.get_driver(DriverId(row.driver_id))
        if team is None or driver is None:
            continue
        found.append(
            MemberInfo(
                driver_id=int(row.driver_id),
                driver_label=driver.label,
                team_id=int(team.id),
                team_name=team.name,
            )
        )
    return tuple(found)


def _calendar(
    links: list[ChampionshipRaceLink], index: dict[int, RaceIndexEntry]
) -> list[LinkedRace]:
    races: list[LinkedRace] = []
    for link in links:
        entry = index.get(link.race_id)
        races.append(
            LinkedRace(
                race_id=int(link.race_id),
                name=entry.name if entry is not None else f"#{link.race_id}",
                status=entry.status if entry is not None else RaceStatus.CREATED,
                sort_order=int(link.sort_order),
            )
        )
    return races


def _history_for(
    link: ChampionshipRaceLink,
    completed: dict[int, HistoryRace],
    index: dict[int, RaceIndexEntry],
) -> HistoryRace:
    stored = completed.get(link.race_id)
    if stored is not None:
        return stored
    entry = index.get(link.race_id)
    return HistoryRace(
        race_id=int(link.race_id),
        name=entry.name if entry is not None else f"#{link.race_id}",
        track_id=None,
        track_name="",
        layout_id=None,
        status=entry.status if entry is not None else RaceStatus.CREATED,
        mode=RaceMode.LAPS,
        finished_at=entry.finished_at if entry is not None else None,
        participants=(),
        laps=(),
    )
