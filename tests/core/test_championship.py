"""Championship points are derived from stored results. Nothing here invents a place."""

from datetime import datetime

from slot_racing.core.championship import (
    DEFAULT_PLACE_POINTS,
    PointsRule,
    score_championship,
    score_teams,
)
from slot_racing.core.domain import RaceMode, RaceStatus
from slot_racing.core.statistics import HistoryLap, HistoryParticipant, HistoryRace

RULE = PointsRule(DEFAULT_PLACE_POINTS)


def test_default_scheme_scores_place_and_leaves_later_places_at_zero() -> None:
    standing = score_championship(
        [_race(1, (_person(1, 1, "Anna", 1), _person(2, 2, "Ben", 11)))],
        RULE,
    )
    assert DEFAULT_PLACE_POINTS[0] == (1, 25)
    assert [row.driver_label for row in standing] == ["Anna", "Ben"]
    assert standing[0].points == 25
    assert standing[1].points == 0
    assert standing[0].wins == 1
    assert standing[0].podiums == 1
    assert standing[1].gap == 25
    assert standing[0].counted_races == 1


def test_a_custom_scheme_replaces_the_default() -> None:
    rule = PointsRule(((1, 10), (2, 6), (3, 1)))
    standing = score_championship(
        [_race(1, (_person(1, 1, "Anna", 1), _person(2, 2, "Ben", 2)))],
        rule,
    )
    assert [row.points for row in standing] == [10, 6]


def test_fastest_valid_lap_awards_the_bonus_and_a_tie_awards_it_twice() -> None:
    people = (_person(1, 1, "Anna", 1), _person(2, 2, "Ben", 2))
    single = score_championship(
        [_race(1, people, laps=(_lap(1, 8), _lap(2, 9)))],
        PointsRule(DEFAULT_PLACE_POINTS, fastest_lap_bonus=3),
    )
    assert single[0].points == 28
    assert single[0].bonus_points == 3
    assert single[0].results[0].fastest is True
    assert single[1].bonus_points == 0
    shared = score_championship(
        [_race(1, people, laps=(_lap(1, 8), _lap(2, 8)))],
        PointsRule(DEFAULT_PLACE_POINTS, fastest_lap_bonus=3),
    )
    assert shared[0].bonus_points == 3
    assert shared[1].bonus_points == 3


def test_a_disqualified_lap_neither_scores_nor_sets_the_fastest_lap() -> None:
    people = (_person(1, 1, "Anna", 1, disqualified=True), _person(2, 2, "Ben", 2))
    standing = score_championship(
        [_race(1, people, laps=(_lap(1, 4), _lap(2, 9)))],
        PointsRule(DEFAULT_PLACE_POINTS, fastest_lap_bonus=1),
    )
    anna = standing[1] if standing[0].driver_label == "Ben" else standing[0]
    ben = standing[0] if standing[0].driver_label == "Ben" else standing[1]
    assert anna.driver_label == "Anna"
    assert anna.points == 0
    assert anna.results[0].disqualified is True
    assert anna.results[0].classified is False
    assert ben.points == 19
    assert ben.results[0].fastest is True
    assert ben.wins == 0


def test_no_valid_lap_awards_no_bonus() -> None:
    standing = score_championship(
        [_race(1, (_person(1, 1, "Anna", 1),), laps=(_lap(1, 0),))],
        PointsRule(DEFAULT_PLACE_POINTS, fastest_lap_bonus=5),
    )
    assert standing[0].points == 25
    assert standing[0].results[0].fastest is False


def test_drop_removes_the_worst_counting_score_and_keeps_one() -> None:
    races = [
        _race(1, (_person(1, 1, "Anna", 1),)),
        _race(2, (_person(2, 1, "Anna", 2),)),
        _race(3, (_person(3, 1, "Anna", 3),)),
    ]
    rule = PointsRule(((1, 10), (2, 8), (3, 1)), drop_count=1)
    standing = score_championship(races, rule)
    assert standing[0].points == 18
    assert standing[0].counted_races == 2
    assert [result.dropped for result in standing[0].results] == [False, False, True]
    assert standing[0].wins == 1
    heavy = score_championship(races, PointsRule(((1, 10), (2, 8), (3, 1)), drop_count=9))
    assert heavy[0].counted_races == 1
    assert heavy[0].points == 10


def test_equal_scores_drop_the_later_race_first() -> None:
    races = [
        _race(1, (_person(1, 1, "Anna", 1),)),
        _race(2, (_person(2, 1, "Anna", 1),)),
    ]
    standing = score_championship(races, PointsRule(((1, 10),), drop_count=1))
    assert [result.dropped for result in standing[0].results] == [False, True]
    assert standing[0].wins == 2


def test_a_dropped_win_still_counts_when_it_was_worth_fewer_points() -> None:
    races = [
        _race(1, (_person(1, 1, "Anna", 1),)),
        _race(2, (_person(2, 1, "Anna", 2),)),
    ]
    standing = score_championship(races, PointsRule(((1, 1), (2, 10)), drop_count=1))
    assert standing[0].points == 10
    assert standing[0].results[0].dropped is True
    assert standing[0].wins == 1
    assert standing[0].podiums == 2


def test_ties_break_on_wins_then_further_places_and_a_dead_heat_stays_shared() -> None:
    level = PointsRule(((1, 10), (2, 10), (3, 10)))
    decided = score_championship(
        [
            _race(1, (_person(1, 1, "Anna", 1), _person(2, 2, "Ben", 2))),
            _race(2, (_person(3, 1, "Anna", 3), _person(4, 2, "Ben", 2))),
        ],
        level,
    )
    assert [row.driver_label for row in decided] == ["Anna", "Ben"]
    assert [row.points for row in decided] == [20, 20]
    assert decided[0].rank == 1
    assert decided[1].rank == 2
    shared = score_championship(
        [
            _race(1, (_person(1, 1, "Anna", 1), _person(2, 2, "Ben", 2))),
            _race(2, (_person(3, 1, "Anna", 2), _person(4, 2, "Ben", 1))),
        ],
        level,
    )
    assert shared[0].rank == shared[1].rank == 1
    assert shared[0].gap == shared[1].gap == 0
    with_third = score_championship(
        [
            _race(1, (_person(1, 1, "Anna", 1), _person(2, 2, "Ben", 2))),
            _race(2, (_person(3, 1, "Anna", 2), _person(4, 2, "Ben", 1))),
            _race(3, (_person(5, 3, "Cara", 1),)),
        ],
        level,
    )
    ranks = {row.driver_label: row.rank for row in with_third}
    assert ranks["Anna"] == 1
    assert ranks["Ben"] == 1
    assert ranks["Cara"] == 3
    assert next(row for row in with_third if row.driver_label == "Cara").gap == 10


def test_aborted_and_open_races_award_nothing() -> None:
    people = (_person(1, 1, "Anna", 1),)
    aborted = score_championship([_race(1, people, status=RaceStatus.ABORTED)], RULE)
    open_race = score_championship([_race(2, people, status=RaceStatus.CREATED)], RULE)
    assert aborted[0].points == 0
    assert aborted[0].counted_races == 0
    assert aborted[0].results[0].classified is False
    assert open_race[0].points == 0
    assert aborted[0].results[0].dropped is False


def test_a_second_start_of_the_same_driver_is_not_scored_again() -> None:
    standing = score_championship(
        [
            _race(
                1,
                (
                    _person(1, 7, "Anna", 1),
                    _person(9, 7, "Anna", 4),
                    _person(2, 8, "Ben", 2),
                ),
            )
        ],
        RULE,
    )
    anna = next(row for row in standing if row.driver_label == "Anna")
    assert anna.points == 25
    assert anna.results[0].merged is True
    assert anna.results[0].position == 1
    assert len(standing) == 2
    classified = score_championship(
        [_race(1, (_person(2, 7, "Anna", 1, disqualified=True), _person(1, 7, "Anna", 2)))],
        RULE,
    )
    assert classified[0].points == 18
    assert classified[0].results[0].position == 2
    assert classified[0].results[0].merged is True


def test_an_absent_race_stays_visible_without_points() -> None:
    standing = score_championship(
        [
            _race(1, (_person(1, 1, "Anna", 1),)),
            _race(2, (_person(2, 2, "Ben", 1),)),
        ],
        RULE,
    )
    anna = next(row for row in standing if row.driver_id == 1)
    assert anna.points == 25
    assert anna.results[1].absent is True
    assert anna.results[1].total == 0
    assert anna.counted_races == 1


def test_team_points_follow_the_frozen_race_and_ignore_a_later_roster() -> None:
    standings = score_championship(
        [
            _race(1, (_person(1, 1, "Anna", 1), _person(2, 2, "Ben", 2))),
            _race(2, (_person(3, 1, "Anna", 2), _person(4, 2, "Ben", 1))),
        ],
        RULE,
    )
    teams = score_teams(
        standings,
        ((10, "Rot"), (20, "Blau"), (30, "Grün")),
        {(1, 1): 10, (1, 2): 10, (2, 1): 20, (2, 2): 20},
        {20: (1, 2), 30: (9,)},
    )
    by_name = {row.name: row for row in teams}
    assert by_name["Rot"].points == 25 + 18
    assert by_name["Blau"].points == 18 + 25
    assert by_name["Grün"].points == 0
    assert by_name["Grün"].drivers == 1
    assert by_name["Blau"].drivers == 2
    assert by_name["Rot"].rank == 1
    assert by_name["Blau"].rank == 1
    assert by_name["Grün"].rank == 3


def test_dropped_driver_points_do_not_enter_the_team_total() -> None:
    standings = score_championship(
        [
            _race(1, (_person(1, 1, "Anna", 1),)),
            _race(2, (_person(2, 1, "Anna", 2),)),
        ],
        PointsRule(((1, 1), (2, 10)), drop_count=1),
    )
    teams = score_teams(standings, ((4, "Rot"),), {(1, 1): 4, (2, 1): 4}, {4: (1,)})
    assert teams[0].points == 10
    assert teams[0].wins == 1
    assert teams[0].counted_results == 1


def test_an_empty_calendar_has_no_standings() -> None:
    assert score_championship((), RULE) == ()
    assert score_teams((), ((1, "Rot"),), {}, {})[0].points == 0


def _person(
    participant_id: int,
    driver_id: int,
    label: str,
    position: int | None,
    *,
    disqualified: bool = False,
) -> HistoryParticipant:
    return HistoryParticipant(
        participant_id=participant_id,
        driver_id=driver_id,
        driver_label=label,
        vehicle_id=None,
        vehicle_label="",
        lane=1,
        position=position,
        finished=True,
        disqualified=disqualified,
        total_time_ns=1_000,
    )


def _lap(participant_id: int, seconds: int) -> HistoryLap:
    return HistoryLap(
        participant_id=participant_id,
        lap_number=1,
        lap_time_ns=seconds * 1_000_000_000,
        lane=1,
    )


def _race(
    race_id: int,
    people: tuple[HistoryParticipant, ...],
    *,
    laps: tuple[HistoryLap, ...] = (),
    status: RaceStatus = RaceStatus.FINISHED,
) -> HistoryRace:
    return HistoryRace(
        race_id=race_id,
        name=f"Lauf {race_id}",
        track_id=1,
        track_name="Ring",
        layout_id=None,
        status=status,
        mode=RaceMode.LAPS,
        finished_at=datetime(2026, 5, 1, 12, 0),
        participants=people,
        laps=laps,
    )
