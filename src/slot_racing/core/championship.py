"""Championship points from stored race results.

The functions read plain facts and return plain facts. A race that is not finished awards
nothing. A disqualified driver receives no placement points and no fastest-lap bonus.
One driver is scored once per race: the participant model already allows only one row, and a
second row would still not add a second score. Dropped results stay visible. Nothing here
invents a place or a point.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from slot_racing.core.domain.race import RaceStatus
from slot_racing.core.statistics import HistoryParticipant, HistoryRace, valid_lap_times

DEFAULT_PLACE_POINTS: tuple[tuple[int, int], ...] = (
    (1, 25),
    (2, 18),
    (3, 15),
    (4, 12),
    (5, 10),
    (6, 8),
    (7, 6),
    (8, 4),
    (9, 2),
    (10, 1),
)
TIE_BREAK = "points_wins_places"


@dataclass(frozen=True, slots=True)
class PointsRule:
    """The scheme stored with one championship. Places without a row are worth zero."""

    points_by_place: tuple[tuple[int, int], ...]
    fastest_lap_bonus: int = 0
    drop_count: int = 0

    def points_for(self, place: int) -> int:
        for stored_place, points in self.points_by_place:
            if stored_place == place:
                return points
        return 0


@dataclass(frozen=True, slots=True)
class RaceScore:
    """One driver in one championship race. ``absent`` means the driver did not start."""

    race_id: int
    position: int | None
    place_points: int
    bonus_points: int
    classified: bool
    disqualified: bool
    absent: bool
    dropped: bool
    fastest: bool
    merged: bool

    @property
    def total(self) -> int:
        return self.place_points + self.bonus_points


@dataclass(frozen=True, slots=True)
class DriverStanding:
    rank: int
    driver_id: int
    driver_label: str
    points: int
    counted_races: int
    wins: int
    podiums: int
    gap: int
    bonus_points: int
    results: tuple[RaceScore, ...]


@dataclass(frozen=True, slots=True)
class TeamStanding:
    rank: int
    team_id: int
    name: str
    points: int
    counted_results: int
    drivers: int
    wins: int
    gap: int


def score_championship(
    races: Sequence[HistoryRace],
    rule: PointsRule,
) -> tuple[DriverStanding, ...]:
    """Driver table in championship order. ``races`` is the stored calendar order."""
    scored = [_score_race(race, rule) for race in races]
    driver_ids = sorted({driver_id for race_scores in scored for driver_id in race_scores})
    labels = {
        person.driver_id: person.driver_label for race in races for person in race.participants
    }
    rows: list[_Mutable] = []
    depth = _place_depth(rule, scored)
    for driver_id in driver_ids:
        results = tuple(
            race_scores.get(driver_id) or _absent(races[index].race_id)
            for index, race_scores in enumerate(scored)
        )
        dropped = _dropped_indexes(results, rule.drop_count)
        marked = tuple(
            _copy_score(result, dropped=index in dropped) for index, result in enumerate(results)
        )
        classified = [result for result in marked if result.classified]
        kept = [result for result in classified if not result.dropped]
        rows.append(
            _Mutable(
                driver_id=driver_id,
                driver_label=labels.get(driver_id, f"#{driver_id}"),
                points=sum(result.total for result in kept),
                counted_races=len(kept),
                wins=sum(1 for result in classified if result.position == 1),
                podiums=sum(1 for result in classified if _podium(result)),
                bonus_points=sum(result.bonus_points for result in kept),
                results=marked,
                places=_place_counts(classified, depth),
            )
        )
    return tuple(_ranked(rows))


def score_teams(
    standings: Sequence[DriverStanding],
    teams: Sequence[tuple[int, str]],
    assignments: Mapping[tuple[int, int], int],
    members: Mapping[int, Sequence[int]],
) -> tuple[TeamStanding, ...]:
    """Team points are the drivers' counting race scores for the team frozen on that race.

    A later transfer does not move points. ``assignments`` maps ``(race_id, driver_id)`` to the
    team that was stored for that race. Dropped driver results do not add team points. Wins used
    to break a tie still count.
    """
    depth = _team_depth(standings)
    built: list[_TeamMutable] = []
    for team_id, name in teams:
        points = 0
        counted = 0
        wins = 0
        places = [0] * depth
        seen: set[int] = set(members.get(team_id, ()))
        for driver in standings:
            for result in driver.results:
                if assignments.get((result.race_id, driver.driver_id)) != team_id:
                    continue
                seen.add(driver.driver_id)
                if not result.classified:
                    continue
                if result.position == 1:
                    wins += 1
                if result.position is not None and 1 <= result.position <= depth:
                    places[result.position - 1] += 1
                if result.dropped:
                    continue
                points += result.total
                counted += 1
        built.append(
            _TeamMutable(
                team_id=team_id,
                name=name,
                points=points,
                counted_results=counted,
                drivers=len(seen),
                wins=wins,
                places=tuple(places),
            )
        )
    return tuple(_ranked_teams(built))


@dataclass(slots=True)
class _Mutable:
    driver_id: int
    driver_label: str
    points: int
    counted_races: int
    wins: int
    podiums: int
    bonus_points: int
    results: tuple[RaceScore, ...]
    places: tuple[int, ...]


@dataclass(slots=True)
class _TeamMutable:
    team_id: int
    name: str
    points: int
    counted_results: int
    drivers: int
    wins: int
    places: tuple[int, ...]


def _score_race(race: HistoryRace, rule: PointsRule) -> dict[int, RaceScore]:
    official = race.status is RaceStatus.FINISHED
    chosen = _one_start_each(race.participants)
    fastest = _fastest_time(race, chosen) if official else None
    scores: dict[int, RaceScore] = {}
    for person, merged in chosen:
        classified = official and not person.disqualified and person.position is not None
        own = valid_lap_times(race.laps, person.participant_id)
        best = min(own) if own else None
        is_fastest = classified and fastest is not None and best == fastest
        bonus = rule.fastest_lap_bonus if is_fastest and rule.fastest_lap_bonus > 0 else 0
        scores[person.driver_id] = RaceScore(
            race_id=race.race_id,
            position=person.position,
            place_points=rule.points_for(person.position) if classified and person.position else 0,
            bonus_points=bonus,
            classified=classified,
            disqualified=person.disqualified,
            absent=False,
            dropped=False,
            fastest=is_fastest,
            merged=merged,
        )
    return scores


def _one_start_each(
    participants: Sequence[HistoryParticipant],
) -> tuple[tuple[HistoryParticipant, bool], ...]:
    """Keep a single start per driver. The classified result wins over a second row."""
    grouped: dict[int, list[HistoryParticipant]] = {}
    for person in participants:
        grouped.setdefault(person.driver_id, []).append(person)
    chosen: list[tuple[HistoryParticipant, bool]] = []
    for people in grouped.values():
        best = min(people, key=_start_rank)
        chosen.append((best, len(people) > 1))
    return tuple(chosen)


def _start_rank(person: HistoryParticipant) -> tuple[bool, bool, bool, int, int]:
    classified = person.position is not None and not person.disqualified
    return (
        not classified,
        person.disqualified,
        person.position is None,
        person.position or 0,
        person.participant_id,
    )


def _fastest_time(
    race: HistoryRace, chosen: Sequence[tuple[HistoryParticipant, bool]]
) -> int | None:
    times: list[int] = []
    for person, _merged in chosen:
        if person.disqualified or person.position is None:
            continue
        if race.status is not RaceStatus.FINISHED:
            continue
        valid = valid_lap_times(race.laps, person.participant_id)
        if valid:
            times.append(min(valid))
    return min(times) if times else None


def _absent(race_id: int) -> RaceScore:
    return RaceScore(
        race_id=race_id,
        position=None,
        place_points=0,
        bonus_points=0,
        classified=False,
        disqualified=False,
        absent=True,
        dropped=False,
        fastest=False,
        merged=False,
    )


def _copy_score(result: RaceScore, *, dropped: bool) -> RaceScore:
    return RaceScore(
        race_id=result.race_id,
        position=result.position,
        place_points=result.place_points,
        bonus_points=result.bonus_points,
        classified=result.classified,
        disqualified=result.disqualified,
        absent=result.absent,
        dropped=dropped and result.classified,
        fastest=result.fastest,
        merged=result.merged,
    )


def _dropped_indexes(results: Sequence[RaceScore], drop_count: int) -> set[int]:
    """Drop the worst counting scores. One counting score always remains."""
    scoring = [index for index, result in enumerate(results) if result.classified]
    if drop_count <= 0 or len(scoring) <= 1:
        return set()
    allowed = min(drop_count, len(scoring) - 1)

    def worst(index: int) -> tuple[int, int]:
        return (results[index].total, -index)

    ordered = sorted(scoring, key=worst)
    return set(ordered[:allowed])


def _place_depth(rule: PointsRule, scored: Sequence[Mapping[int, RaceScore]]) -> int:
    places = [place for place, _points in rule.points_by_place]
    for race_scores in scored:
        for result in race_scores.values():
            if result.classified and result.position is not None:
                places.append(result.position)
    return max(places) if places else 1


def _place_counts(results: Sequence[RaceScore], depth: int) -> tuple[int, ...]:
    counts = [0] * depth
    for result in results:
        if result.position is not None and 1 <= result.position <= depth:
            counts[result.position - 1] += 1
    return tuple(counts)


def _team_depth(standings: Sequence[DriverStanding]) -> int:
    depth = 1
    for driver in standings:
        for result in driver.results:
            if result.classified and result.position is not None:
                depth = max(depth, result.position)
    return depth


def _ranked(rows: list[_Mutable]) -> list[DriverStanding]:
    ordered = sorted(rows, key=lambda row: (row.points, row.places, -row.driver_id), reverse=True)
    leader = ordered[0].points if ordered else 0
    ranked: list[DriverStanding] = []
    for index, row in enumerate(ordered):
        same = index > 0 and _same_driver(row, ordered[index - 1])
        rank = ranked[-1].rank if same else index + 1
        ranked.append(
            DriverStanding(
                rank=rank,
                driver_id=row.driver_id,
                driver_label=row.driver_label,
                points=row.points,
                counted_races=row.counted_races,
                wins=row.wins,
                podiums=row.podiums,
                gap=leader - row.points,
                bonus_points=row.bonus_points,
                results=row.results,
            )
        )
    return ranked


def _podium(result: RaceScore) -> bool:
    return result.position is not None and result.position <= 3


def _same_driver(left: _Mutable, right: _Mutable) -> bool:
    return left.points == right.points and left.places == right.places


def _ranked_teams(rows: list[_TeamMutable]) -> list[TeamStanding]:
    ordered = sorted(rows, key=lambda row: (row.points, row.places, -row.team_id), reverse=True)
    leader = ordered[0].points if ordered else 0
    ranked: list[TeamStanding] = []
    for index, row in enumerate(ordered):
        previous = ordered[index - 1] if index else None
        same = (
            previous is not None and row.points == previous.points and row.places == previous.places
        )
        rank = ranked[-1].rank if same else index + 1
        ranked.append(
            TeamStanding(
                rank=rank,
                team_id=row.team_id,
                name=row.name,
                points=row.points,
                counted_results=row.counted_results,
                drivers=row.drivers,
                wins=row.wins,
                gap=leader - row.points,
            )
        )
    return ranked
