"""Stored heats: planning, postponement, disqualification and the hand-off between heats.

Races without heat rows keep the single session they had before heats existed.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from slot_racing.core.domain import RaceId, RaceMode, RaceStatus
from slot_racing.core.domain.scoring import time_trial_stored_key
from slot_racing.core.errors import ValidationError
from slot_racing.modules.races.models import Lap, Race, RaceHeat, RaceHeatEntry, RaceParticipant
from slot_racing.modules.races.planning import build_rotation, plan_remaining
from slot_racing.modules.races.types import HeatBriefing, HeatInfo, HeatSeat, LaneChange

PLANNED = "planned"
RUNNING = "running"
COMPLETED = "completed"
ASSIGNED = "assigned"
ENTRY_COMPLETED = "completed"

_MISSING = 2**62


def uses_heats(session: Session, race_id: int) -> bool:
    found = session.scalar(select(RaceHeat.id).where(RaceHeat.race_id == race_id).limit(1))
    return found is not None


def has_pending(session: Session, race_id: int) -> bool:
    found = session.scalar(
        select(RaceHeat.id).where(RaceHeat.race_id == race_id, RaceHeat.status == PLANNED).limit(1)
    )
    return found is not None


def has_completed(session: Session, race_id: int) -> bool:
    found = session.scalar(
        select(RaceHeat.id)
        .where(RaceHeat.race_id == race_id, RaceHeat.status == COMPLETED)
        .limit(1)
    )
    return found is not None


def list_heats(session: Session, race_id: int) -> list[HeatInfo]:
    heats = session.scalars(
        select(RaceHeat).where(RaceHeat.race_id == race_id).order_by(RaceHeat.sequence)
    )
    return [_heat_info(session, heat) for heat in heats]


def replace_open_plan(session: Session, race: Race, lane_count: int) -> None:
    """Rebuild every heat that has not been driven. Completed heats stay as they are."""
    if _running(session, race.id) is not None:
        raise ValidationError("error.race.not_editable")
    participants = _participants(session, race.id)
    _delete_planned(session, race.id)
    if has_completed(session, race.id):
        heats = plan_remaining(
            _obligations(participants, lane_count, _completed_pairs(session, race.id)), lane_count
        )
    else:
        active = [participant.id for participant in participants if not participant.disqualified]
        heats = build_rotation(active, lane_count)
    _insert_heats(session, race.id, heats)


def postpone(session: Session, race: Race, participant_id: int, lane_count: int) -> None:
    """Leave this driver out of the next heat and plan them into a later one."""
    if _running(session, race.id) is not None:
        raise ValidationError("error.race.not_editable")
    participant = _require_participant(session, race.id, participant_id)
    if participant.disqualified:
        raise ValidationError("error.race.disqualified")
    obligations = _obligations(
        _participants(session, race.id), lane_count, _completed_pairs(session, race.id)
    )
    if not any(driver == participant.id for driver, _lane in obligations):
        raise ValidationError("error.race.nothing_to_postpone")
    heats = plan_remaining(obligations, lane_count, deferred=frozenset({participant.id}))
    _delete_planned(session, race.id)
    _insert_heats(session, race.id, heats)


def disqualify(session: Session, race: Race, participant_id: int, lane_count: int) -> bool:
    """Drop the driver from every heat that has not started. Completed laps stay stored.

    Returns whether the race itself is now over because nobody still has a heat to drive.
    """
    if _running(session, race.id) is not None:
        raise ValidationError("error.race.not_editable")
    participant = _require_participant(session, race.id, participant_id)
    participant.disqualified = True
    participants = _participants(session, race.id)
    heats = plan_remaining(
        _obligations(participants, lane_count, _completed_pairs(session, race.id)),
        lane_count,
    )
    _delete_planned(session, race.id)
    _insert_heats(session, race.id, heats)
    if heats or not has_completed(session, race.id):
        return False
    _rank(session, race)
    for driver in _participants(session, race.id):
        driver.finished = not driver.disqualified and driver.laps_completed > 0
    return True


def assign_next(session: Session, race: Race) -> bool:
    """Put the next planned heat on the participants' current lanes. Others wait with no lane."""
    heat = session.scalar(
        select(RaceHeat)
        .where(RaceHeat.race_id == race.id, RaceHeat.status == PLANNED)
        .order_by(RaceHeat.sequence)
        .limit(1)
    )
    if heat is None:
        return False
    for participant in _participants(session, race.id):
        participant.lane = None
    session.flush()
    for entry in _entries(session, heat.id):
        if entry.state != ASSIGNED:
            continue
        driver = session.get(RaceParticipant, entry.participant_id)
        if driver is None or driver.disqualified:
            continue
        driver.lane = entry.lane
    heat.status = RUNNING
    session.flush()
    return True


def revert_running_heat(session: Session, race: Race) -> None:
    """A start that failed before the race was running gives the heat back to the plan."""
    if RaceStatus(race.status) in (RaceStatus.RUNNING, RaceStatus.PAUSED):
        return
    heat = _running(session, race.id)
    if heat is not None:
        heat.status = PLANNED


def finish_heat(session: Session, race: Race, *, aborted: bool) -> bool:
    """Close the heat that is on track.

    Returns True when the race stays open for another heat. The caller's ranking then waits.
    """
    heat = _running(session, race.id)
    if heat is None:
        return False
    for entry in _entries(session, heat.id):
        entry.state = ENTRY_COMPLETED
    heat.status = COMPLETED
    for participant in _participants(session, race.id):
        laps = _laps(session, participant.id)
        participant.laps_completed = len(laps)
        participant.best_lap_ns = min((lap.lap_time_ns for lap in laps), default=None)
    pending = has_pending(session, race.id)
    if aborted or not pending:
        _rank(session, race)
        for participant in _participants(session, race.id):
            participant.finished = not participant.disqualified and participant.laps_completed > 0
        return False
    for participant in _participants(session, race.id):
        participant.lane = None
        participant.finished = False
        participant.final_position = None
    return True


def accumulate_heat_time(
    session: Session, race_id: int, lane: int, heat_total_ns: int | None
) -> None:
    """Add this heat's race clock to the driver's stored total. Ignored when no heat is running."""
    if _running(session, race_id) is None or heat_total_ns is None:
        return
    participant = session.scalar(
        select(RaceParticipant).where(
            RaceParticipant.race_id == race_id, RaceParticipant.lane == lane
        )
    )
    if participant is None:
        return
    participant.total_time_ns = (participant.total_time_ns or 0) + heat_total_ns


def stored_lap_number(session: Session, participant_id: int, lap_number: int) -> int:
    """Keep the engine's lap number unless that heat restarts at a number already stored."""
    taken = session.scalar(
        select(Lap.id).where(Lap.participant_id == participant_id, Lap.lap_number == lap_number)
    )
    if taken is None:
        return lap_number
    highest = session.scalar(
        select(Lap.lap_number)
        .where(Lap.participant_id == participant_id)
        .order_by(Lap.lap_number.desc())
        .limit(1)
    )
    return (highest or 0) + 1


def heat_time_base(session: Session, race_id: int, participant: RaceParticipant) -> int:
    """Race time already stored from earlier heats. Zero when this race has no heat plan."""
    if not uses_heats(session, race_id):
        return 0
    return participant.total_time_ns or 0


def briefing(
    session: Session,
    race: Race,
    lane_count: int,
    names: dict[int, str],
) -> HeatBriefing | None:
    heat = session.scalar(
        select(RaceHeat)
        .where(RaceHeat.race_id == race.id, RaceHeat.status == PLANNED)
        .order_by(RaceHeat.sequence)
        .limit(1)
    )
    if heat is None:
        return None
    previous = session.scalar(
        select(RaceHeat)
        .where(RaceHeat.race_id == race.id, RaceHeat.status == COMPLETED)
        .order_by(RaceHeat.sequence.desc())
        .limit(1)
    )
    previous_lane = {}
    if previous is not None:
        previous_lane = {
            entry.participant_id: entry.lane for entry in _entries(session, previous.id)
        }
    seated = {entry.lane: entry for entry in _entries(session, heat.id) if entry.state == ASSIGNED}
    seats: list[HeatSeat] = []
    changes: list[LaneChange] = []
    for lane in range(1, lane_count + 1):
        entry = seated.get(lane)
        if entry is None:
            seats.append(HeatSeat(lane=lane, participant_id=None, driver_label=""))
            continue
        label = names.get(entry.participant_id, "")
        seats.append(HeatSeat(lane=lane, participant_id=entry.participant_id, driver_label=label))
        changes.append(
            LaneChange(
                driver_label=label,
                from_lane=previous_lane.get(entry.participant_id),
                to_lane=lane,
            )
        )
    all_heats = list_heats(session, race.id)
    drivers = tuple(
        (participant.id, names.get(participant.id, ""))
        for participant in _participants(session, race.id)
        if not participant.disqualified
    )
    return HeatBriefing(
        race_id=RaceId(race.id),
        sequence=heat.sequence,
        heat_count=len(all_heats),
        seats=tuple(seats),
        changes=tuple(changes),
        drivers=drivers,
    )


def _rank(session: Session, race: Race) -> None:
    participants = _participants(session, race.id)
    stored = [(participant, _laps(session, participant.id)) for participant in participants]
    mode = RaceMode(race.mode)
    if mode is RaceMode.TIME_TRIAL:
        ranked = sorted(stored, key=lambda item: _time_trial_key(item[0], item[1]))
    else:
        ranked = sorted(stored, key=lambda item: _lap_key(item[0], item[1], race.target_laps))
    for position, (participant, laps) in enumerate(ranked, start=1):
        participant.laps_completed = len(laps)
        participant.best_lap_ns = min((lap.lap_time_ns for lap in laps), default=None)
        participant.total_time_ns = laps[-1].race_time_ns if laps else participant.total_time_ns
        participant.final_position = position


def _lap_key(
    participant: RaceParticipant, laps: list[Lap], target: int
) -> tuple[int, int, int, int]:
    """More completed laps first, then the smaller combined race time.

    ``target`` is unused: every heat already applied it, and the standing sums those heats.
    Disqualified drivers keep their laps and are placed after the drivers still in the race.
    """
    del target
    last = laps[-1].race_time_ns if laps else _MISSING
    lane = _tie_lane(participant, laps)
    group = 1 if participant.disqualified else 0
    return (group, -len(laps), last, lane)


def _time_trial_key(
    participant: RaceParticipant, laps: list[Lap]
) -> tuple[int, int, int, int, int]:
    best = min(laps, key=lambda lap: lap.lap_time_ns) if laps else None
    lane = best.lane if best is not None and best.lane is not None else _tie_lane(participant, laps)
    key = time_trial_stored_key(None if best is None else best.lap_time_ns, lane)
    group = 1 if participant.disqualified else 0
    return (group, *key)


def _tie_lane(participant: RaceParticipant, laps: list[Lap]) -> int:
    if laps and laps[-1].lane is not None:
        return laps[-1].lane
    return participant.lane or 0


def _obligations(
    participants: list[RaceParticipant],
    lane_count: int,
    completed: set[tuple[int, int]],
) -> list[tuple[int, int]]:
    pairs: list[tuple[int, int]] = []
    for participant in participants:
        if participant.disqualified:
            continue
        for lane in range(1, lane_count + 1):
            if (participant.id, lane) not in completed:
                pairs.append((participant.id, lane))
    return pairs


def _completed_pairs(session: Session, race_id: int) -> set[tuple[int, int]]:
    rows = session.execute(
        select(RaceHeatEntry.participant_id, RaceHeatEntry.lane)
        .join(RaceHeat, RaceHeat.id == RaceHeatEntry.heat_id)
        .where(RaceHeat.race_id == race_id, RaceHeat.status == COMPLETED)
    )
    return {(participant_id, lane) for participant_id, lane in rows}


def _insert_heats(
    session: Session,
    race_id: int,
    heats: tuple[tuple[tuple[int, int], ...], ...] | list[tuple[tuple[int, int], ...]],
) -> None:
    sequence = session.scalar(
        select(RaceHeat.sequence)
        .where(RaceHeat.race_id == race_id)
        .order_by(RaceHeat.sequence.desc())
        .limit(1)
    )
    start = sequence or 0
    for offset, seats in enumerate(heats, start=1):
        heat = RaceHeat(race_id=race_id, sequence=start + offset, status=PLANNED)
        session.add(heat)
        session.flush()
        for participant_id, lane in seats:
            session.add(
                RaceHeatEntry(
                    heat_id=heat.id,
                    participant_id=participant_id,
                    lane=lane,
                    state=ASSIGNED,
                )
            )
    session.flush()


def _delete_planned(session: Session, race_id: int) -> None:
    planned = list(
        session.scalars(
            select(RaceHeat).where(RaceHeat.race_id == race_id, RaceHeat.status == PLANNED)
        )
    )
    for heat in planned:
        session.delete(heat)
    session.flush()


def _participants(session: Session, race_id: int) -> list[RaceParticipant]:
    return list(
        session.scalars(
            select(RaceParticipant)
            .where(RaceParticipant.race_id == race_id)
            .order_by(RaceParticipant.id)
        )
    )


def _entries(session: Session, heat_id: int) -> list[RaceHeatEntry]:
    return list(
        session.scalars(
            select(RaceHeatEntry)
            .where(RaceHeatEntry.heat_id == heat_id)
            .order_by(RaceHeatEntry.lane)
        )
    )


def _laps(session: Session, participant_id: int) -> list[Lap]:
    return list(
        session.scalars(
            select(Lap).where(Lap.participant_id == participant_id).order_by(Lap.lap_number)
        )
    )


def _running(session: Session, race_id: int) -> RaceHeat | None:
    return session.scalar(
        select(RaceHeat).where(RaceHeat.race_id == race_id, RaceHeat.status == RUNNING).limit(1)
    )


def _require_participant(session: Session, race_id: int, participant_id: int) -> RaceParticipant:
    participant = session.get(RaceParticipant, participant_id)
    if participant is None or participant.race_id != race_id:
        raise ValidationError("error.race.participant_unknown")
    return participant


def _heat_info(session: Session, heat: RaceHeat) -> HeatInfo:
    seats = tuple(
        (entry.participant_id, entry.lane)
        for entry in _entries(session, heat.id)
        if entry.state == ASSIGNED or heat.status == COMPLETED
    )
    return HeatInfo(sequence=heat.sequence, status=heat.status, seats=seats)
