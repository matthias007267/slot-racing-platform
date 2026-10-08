"""Load ended races as plain facts. Ranking and averages stay in ``core.statistics``."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from slot_racing.core.catalog import DriverCatalog, VehicleCatalog
from slot_racing.core.domain import DriverId, RaceId, RaceMode, RaceStatus, TrackId, VehicleId
from slot_racing.core.statistics import HistoryLap, HistoryParticipant, HistoryRace, LayoutRevision
from slot_racing.core.storage.base import Base
from slot_racing.modules.races.models import Lap, Race, RaceParticipant

_ENDED = (RaceStatus.FINISHED.value, RaceStatus.ABORTED.value)
_CURRENT_PLAN = "plan"


def load_completed(
    session: Session,
    drivers: DriverCatalog,
    vehicles: VehicleCatalog,
    tracks: dict[int, str],
) -> tuple[HistoryRace, ...]:
    races = session.scalars(select(Race).where(Race.status.in_(_ENDED)).order_by(Race.id)).all()
    return tuple(_race(session, race, drivers, vehicles, tracks) for race in races)


def load_one(
    session: Session,
    race_id: RaceId,
    drivers: DriverCatalog,
    vehicles: VehicleCatalog,
    tracks: dict[int, str],
) -> HistoryRace | None:
    race = session.get(Race, int(race_id))
    if race is None or race.status not in _ENDED:
        return None
    return _race(session, race, drivers, vehicles, tracks)


def load_layouts(session: Session, track_id: TrackId) -> tuple[LayoutRevision, ...]:
    table = Base.metadata.tables["track_layouts"]
    rows = session.execute(
        select(table.c.id, table.c.name)
        .where(table.c.track_id == int(track_id))
        .order_by(table.c.id)
    ).all()
    current = [row for row in rows if row.name == _CURRENT_PLAN]
    archived = [row for row in rows if row.name != _CURRENT_PLAN]
    ordered = current + archived
    return tuple(
        LayoutRevision(
            layout_id=int(row.id), track_id=int(track_id), current=row.name == _CURRENT_PLAN
        )
        for row in ordered
    )


def _race(
    session: Session,
    race: Race,
    drivers: DriverCatalog,
    vehicles: VehicleCatalog,
    tracks: dict[int, str],
) -> HistoryRace:
    participants = session.scalars(
        select(RaceParticipant)
        .where(RaceParticipant.race_id == race.id)
        .order_by(RaceParticipant.id)
    ).all()
    laps = session.scalars(
        select(Lap).where(Lap.race_id == race.id).order_by(Lap.participant_id, Lap.lap_number)
    ).all()
    track_id = None if race.track_id is None else int(race.track_id)
    return HistoryRace(
        race_id=int(race.id),
        name=race.name,
        track_id=track_id,
        track_name=tracks.get(track_id or -1, "-"),
        layout_id=None if race.track_layout_id is None else int(race.track_layout_id),
        status=RaceStatus(race.status),
        mode=RaceMode(race.mode),
        finished_at=race.finished_at,
        participants=tuple(_participant(row, drivers, vehicles) for row in participants),
        laps=tuple(
            HistoryLap(
                participant_id=int(lap.participant_id),
                lap_number=int(lap.lap_number),
                lap_time_ns=int(lap.lap_time_ns),
                lane=None if lap.lane is None else int(lap.lane),
            )
            for lap in laps
        ),
    )


def _participant(
    row: RaceParticipant, drivers: DriverCatalog, vehicles: VehicleCatalog
) -> HistoryParticipant:
    driver = drivers.get_driver(DriverId(row.driver_id))
    vehicle_id = None if row.vehicle_id is None else VehicleId(row.vehicle_id)
    vehicle = None if vehicle_id is None else vehicles.get_vehicle(vehicle_id)
    return HistoryParticipant(
        participant_id=int(row.id),
        driver_id=int(row.driver_id),
        driver_label=driver.label if driver else f"#{row.driver_id}",
        vehicle_id=None if vehicle_id is None else int(vehicle_id),
        vehicle_label=vehicle.label
        if vehicle
        else ("-" if vehicle_id is None else f"#{vehicle_id}"),
        lane=None if row.lane is None else int(row.lane),
        position=row.final_position,
        finished=bool(row.finished),
        disqualified=bool(row.disqualified),
        total_time_ns=row.total_time_ns,
    )
