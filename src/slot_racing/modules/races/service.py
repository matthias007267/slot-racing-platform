"""Race management: configuration, participants, lifecycle persistence and stored results."""

from __future__ import annotations

from collections.abc import Sequence

from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from slot_racing.core.catalog import DriverCatalog, TrackCatalog, TrackInfo, VehicleCatalog
from slot_racing.core.domain import (
    DriverId,
    ParticipantResult,
    RaceId,
    RaceStatus,
    TrackId,
    VehicleId,
)
from slot_racing.core.errors import ValidationError
from slot_racing.core.storage import Database, utc_now
from slot_racing.core.timing_registry import DEFAULT_TIMING_PROVIDER, TimingProviderRegistry
from slot_racing.modules.races.models import Lap, Race, RaceParticipant, Sector
from slot_racing.modules.races.types import LapRecord, ParticipantInfo, RaceInfo, ResultRow

MAX_LAPS = 999
MAX_PROVIDER_ID_LENGTH = 64

_EDITABLE = (RaceStatus.CREATED, RaceStatus.READY)
_LIVE = (RaceStatus.RUNNING, RaceStatus.PAUSED)
_MISSING_LAP_TIME = 2**62


def _stored_standing_key(
    participant: RaceParticipant, laps: list[Lap], target_laps: int
) -> tuple[int, int, int, int]:
    """Order stored laps the way the engine ranks a stopped race.

    Finished cars (lap target reached) come first, by the race time of their finishing lap and
    then by lane. The others follow by more laps, then the smaller race time of the last stored
    lap, then the lower lane. The lane step only breaks an equal time; it is not a shared place.
    """
    completed = len(laps)
    last_time = laps[-1].race_time_ns if laps else _MISSING_LAP_TIME
    if completed >= target_laps:
        return (0, last_time, participant.lane, 0)
    return (1, -completed, last_time, participant.lane)


class RaceService:
    """Owns the race data. Drivers, vehicles and tracks are only read through their catalogs."""

    def __init__(
        self,
        database: Database,
        drivers: DriverCatalog,
        vehicles: VehicleCatalog,
        tracks: TrackCatalog,
        providers: TimingProviderRegistry | None = None,
        default_provider: str | None = None,
    ) -> None:
        self._database = database
        self._drivers = drivers
        self._vehicles = vehicles
        self._tracks = tracks
        self._providers = providers
        self._default_provider = default_provider

    def default_provider_id(self) -> str:
        """Provider for a new race: the configured one if available, else the first available,
        else the id every race had before providers were selectable."""
        if self._providers is not None:
            chosen = self._providers.default_provider_id(self._default_provider)
            if chosen is not None:
                return chosen
        return self._default_provider or DEFAULT_TIMING_PROVIDER

    # --- configuration -------------------------------------------------------------------------

    def create_race(
        self, name: str, track_id: TrackId, laps: int, timing_provider: str | None = None
    ) -> RaceInfo:
        clean_name = self._validate_name(name)
        self._validate_laps(laps)
        provider = self._validate_provider(timing_provider or self.default_provider_id())
        self._require_active_track(track_id)
        with self._database.session() as session:
            race = Race(
                name=clean_name,
                track_id=track_id,
                target_laps=laps,
                timing_provider=provider,
                status=RaceStatus.CREATED.value,
            )
            session.add(race)
            session.flush()
            return self._race_info(session, race)

    def update_race(
        self,
        race_id: RaceId,
        name: str,
        track_id: TrackId,
        laps: int,
        timing_provider: str | None = None,
    ) -> RaceInfo:
        """Change the configuration. ``timing_provider=None`` keeps the stored provider."""
        clean_name = self._validate_name(name)
        self._validate_laps(laps)
        provider = None if timing_provider is None else self._validate_provider(timing_provider)
        with self._database.session() as session:
            race = self._load_editable(session, race_id)
            if track_id != race.track_id:
                track = self._require_active_track(track_id)
                lanes = self._participant_lanes(session, race.id)
                if len(lanes) > track.lane_count or any(lane > track.lane_count for lane in lanes):
                    raise ValidationError("error.race.track_too_small", lanes=track.lane_count)
                race.track_id = track_id
            race.name = clean_name
            race.target_laps = laps
            if provider is not None:
                race.timing_provider = provider
            session.flush()
            return self._race_info(session, race)

    def add_participant(
        self, race_id: RaceId, driver_id: DriverId, vehicle_id: VehicleId, lane: int
    ) -> ParticipantInfo:
        driver = self._drivers.get_driver(driver_id)
        if driver is None:
            raise ValidationError("error.race.driver_unknown")
        if not driver.is_active:
            raise ValidationError("error.race.driver_inactive", driver=driver.label)
        vehicle = self._vehicles.get_vehicle(vehicle_id)
        if vehicle is None:
            raise ValidationError("error.race.vehicle_unknown")
        if not vehicle.is_active:
            raise ValidationError("error.race.vehicle_inactive", vehicle=vehicle.label)

        with self._database.session() as session:
            race = self._load_editable(session, race_id)
            track = self._tracks.get_track(TrackId(race.track_id)) if race.track_id else None
            if track is None:
                raise ValidationError("error.race.no_track")
            existing = list(
                session.scalars(select(RaceParticipant).where(RaceParticipant.race_id == race.id))
            )
            if len(existing) >= track.lane_count:
                raise ValidationError("error.race.too_many_participants", maximum=track.lane_count)
            if not 1 <= lane <= track.lane_count:
                raise ValidationError("error.race.lane_invalid", maximum=track.lane_count)
            if any(p.lane == lane for p in existing):
                raise ValidationError("error.race.lane_taken", lane=lane)
            if any(p.driver_id == driver_id for p in existing):
                raise ValidationError("error.race.driver_duplicate", driver=driver.label)
            participant = RaceParticipant(
                race_id=race.id, driver_id=driver_id, vehicle_id=vehicle_id, lane=lane
            )
            session.add(participant)
            session.flush()
            self._update_readiness(session, race)
            return self._participant_info(participant)

    def update_participant(
        self,
        race_id: RaceId,
        participant_id: int,
        driver_id: DriverId,
        vehicle_id: VehicleId,
        lane: int,
    ) -> ParticipantInfo:
        """Correct driver, vehicle or lane on the existing participant row."""
        driver = self._drivers.get_driver(driver_id)
        if driver is None:
            raise ValidationError("error.race.driver_unknown")
        if not driver.is_active:
            raise ValidationError("error.race.driver_inactive", driver=driver.label)
        vehicle = self._vehicles.get_vehicle(vehicle_id)
        if vehicle is None:
            raise ValidationError("error.race.vehicle_unknown")
        if not vehicle.is_active:
            raise ValidationError("error.race.vehicle_inactive", vehicle=vehicle.label)

        with self._database.session() as session:
            race = self._load_editable(session, race_id)
            participant = session.get(RaceParticipant, participant_id)
            if participant is None or participant.race_id != race.id:
                raise ValidationError("error.race.participant_unknown")
            track = self._tracks.get_track(TrackId(race.track_id)) if race.track_id else None
            if track is None:
                raise ValidationError("error.race.no_track")
            others = [
                other
                for other in session.scalars(
                    select(RaceParticipant).where(RaceParticipant.race_id == race.id)
                )
                if other.id != participant.id
            ]
            if not 1 <= lane <= track.lane_count:
                raise ValidationError("error.race.lane_invalid", maximum=track.lane_count)
            if any(other.lane == lane for other in others):
                raise ValidationError("error.race.lane_taken", lane=lane)
            if any(other.driver_id == driver_id for other in others):
                raise ValidationError("error.race.driver_duplicate", driver=driver.label)
            participant.driver_id = driver_id
            participant.vehicle_id = vehicle_id
            participant.lane = lane
            session.flush()
            self._update_readiness(session, race)
            return self._participant_info(participant)

    def remove_participant(self, race_id: RaceId, participant_id: int) -> None:
        with self._database.session() as session:
            race = self._load_editable(session, race_id)
            participant = session.get(RaceParticipant, participant_id)
            if participant is None or participant.race_id != race.id:
                raise ValidationError("error.race.participant_unknown")
            session.delete(participant)
            session.flush()
            self._update_readiness(session, race)

    def delete_race(self, race_id: RaceId) -> None:
        with self._database.session() as session:
            race = self._load(session, race_id)
            if RaceStatus(race.status) in _LIVE:
                raise ValidationError("error.race.running")
            session.delete(race)

    # --- queries -------------------------------------------------------------------------------

    def list_races(self) -> list[RaceInfo]:
        with self._database.session() as session:
            races = session.scalars(select(Race).order_by(Race.created_at.desc(), Race.id.desc()))
            return [self._race_info(session, race) for race in races]

    def get_race(self, race_id: RaceId) -> RaceInfo | None:
        with self._database.session() as session:
            race = session.get(Race, race_id)
            return None if race is None else self._race_info(session, race)

    def require_race(self, race_id: RaceId) -> RaceInfo:
        race = self.get_race(race_id)
        if race is None:
            raise ValidationError("error.race.not_found")
        return race

    def validate_startable(self, race_id: RaceId) -> RaceInfo:
        """Check everything that must hold at the start. Raises :class:`ValidationError`."""
        race = self.require_race(race_id)
        if race.status not in _EDITABLE:
            raise ValidationError("error.race.not_startable", status=race.status.value)
        if not race.participants:
            raise ValidationError("error.race.no_participants")
        track = None if race.track_id is None else self._tracks.get_track(race.track_id)
        if track is None:
            raise ValidationError("error.race.no_track")
        if not track.is_active:
            raise ValidationError("error.race.track_inactive", track=track.name)
        if self._providers is not None:
            self._providers.check(race.timing_provider, lane_count=len(race.participants))
        for participant in race.participants:
            driver = self._drivers.get_driver(participant.driver_id)
            if driver is None or not driver.is_active:
                raise ValidationError("error.race.driver_inactive", driver=participant.driver_label)
            vehicle = (
                None
                if participant.vehicle_id is None
                else self._vehicles.get_vehicle(participant.vehicle_id)
            )
            if vehicle is None or not vehicle.is_active:
                raise ValidationError(
                    "error.race.vehicle_inactive", vehicle=participant.vehicle_label
                )
        return race

    def get_results(self, race_id: RaceId) -> list[ResultRow]:
        """Stored results in the order the race engine determined (by final position)."""
        with self._database.session() as session:
            self._load(session, race_id)
            rows: list[ResultRow] = []
            for participant in session.scalars(
                select(RaceParticipant).where(RaceParticipant.race_id == race_id)
            ):
                laps = list(
                    session.scalars(
                        select(Lap)
                        .where(Lap.participant_id == participant.id)
                        .order_by(Lap.lap_number)
                    )
                )
                info = self._participant_info(participant)
                rows.append(
                    ResultRow(
                        participant_id=participant.id,
                        position=participant.final_position,
                        driver_label=info.driver_label,
                        vehicle_label=info.vehicle_label,
                        lane=participant.lane,
                        start_number=info.start_number,
                        laps_completed=participant.laps_completed,
                        finished=participant.finished,
                        total_time_ns=participant.total_time_ns,
                        best_lap_ns=participant.best_lap_ns,
                        last_lap_ns=laps[-1].lap_time_ns if laps else None,
                        average_lap_ns=(
                            sum(lap.lap_time_ns for lap in laps) // len(laps) if laps else None
                        ),
                    )
                )
        rows.sort(key=lambda row: (row.position is None, row.position or 0, row.lane))
        return rows

    def get_laps(self, race_id: RaceId) -> list[LapRecord]:
        with self._database.session() as session:
            self._load(session, race_id)
            records: list[LapRecord] = []
            query = (
                select(Lap, RaceParticipant.lane)
                .join(RaceParticipant, RaceParticipant.id == Lap.participant_id)
                .where(Lap.race_id == race_id)
                .order_by(RaceParticipant.lane, Lap.lap_number)
            )
            for lap, lane in session.execute(query):
                sectors = session.scalars(
                    select(Sector.sector_time_ns)
                    .where(Sector.lap_id == lap.id)
                    .order_by(Sector.sector_number)
                )
                records.append(
                    LapRecord(
                        participant_id=lap.participant_id,
                        lane=lane,
                        lap_number=lap.lap_number,
                        lap_time_ns=lap.lap_time_ns,
                        race_time_ns=lap.race_time_ns,
                        sector_times_ns=tuple(sectors),
                    )
                )
            return records

    # --- persistence of the race lifecycle (driven by events through the recorder) -------------

    def record_started(self, race_id: RaceId) -> None:
        self._set_status(race_id, RaceStatus.RUNNING, started=True)

    def record_paused(self, race_id: RaceId) -> None:
        self._set_status(race_id, RaceStatus.PAUSED)

    def record_resumed(self, race_id: RaceId) -> None:
        self._set_status(race_id, RaceStatus.RUNNING)

    def record_lap(
        self,
        race_id: RaceId,
        lane: int,
        lap_number: int,
        lap_time_ns: int,
        race_time_ns: int,
        sector_times_ns: dict[int, int],
    ) -> None:
        with self._database.session() as session:
            participant = session.scalar(
                select(RaceParticipant).where(
                    RaceParticipant.race_id == race_id, RaceParticipant.lane == lane
                )
            )
            if participant is None:
                raise ValidationError("error.race.participant_unknown")
            lap = Lap(
                race_id=race_id,
                participant_id=participant.id,
                lap_number=lap_number,
                lap_time_ns=lap_time_ns,
                race_time_ns=race_time_ns,
            )
            session.add(lap)
            session.flush()
            session.add_all(
                Sector(lap_id=lap.id, sector_number=number, sector_time_ns=time_ns)
                for number, time_ns in sorted(sector_times_ns.items())
            )
            participant.laps_completed = lap_number
            if participant.best_lap_ns is None or lap_time_ns < participant.best_lap_ns:
                participant.best_lap_ns = lap_time_ns

    def record_finished(
        self, race_id: RaceId, results: Sequence[ParticipantResult], *, aborted: bool
    ) -> None:
        with self._database.session() as session:
            race = self._load(session, race_id)
            for result in results:
                session.execute(
                    update(RaceParticipant)
                    .where(RaceParticipant.race_id == race_id, RaceParticipant.lane == result.lane)
                    .values(
                        final_position=result.position,
                        laps_completed=result.laps_completed,
                        finished=result.finished,
                        total_time_ns=result.total_time_ns,
                        best_lap_ns=result.best_lap_ns,
                    )
                )
            race.status = (RaceStatus.ABORTED if aborted else RaceStatus.FINISHED).value
            race.finished_at = utc_now()

    def abort_race(self, race_id: RaceId) -> None:
        """Mark a race that is recorded as running as aborted. Other races are left alone."""
        with self._database.session() as session:
            race = self._load(session, race_id)
            self._abort_if_live(session, race)

    def abort_stale_races(self) -> int:
        """Abort races that were still running when the application ended.

        Completed laps already stored in ``laps`` become the standings: ``laps_completed``,
        ``best_lap_ns``, ``total_time_ns`` (race time of the last stored lap), ``finished``
        (the lap target was reached) and ``final_position``. The open lap is not stored, so it
        is not invented. Crossing order is not stored either; cars that finished the target are
        ordered by the race time of that lap and then by lane, which is the same order the
        engine used whenever those times differ.
        """
        with self._database.session() as session:
            live = [status.value for status in _LIVE]
            stale = list(session.scalars(select(Race).where(Race.status.in_(live))))
            for race in stale:
                self._abort_if_live(session, race)
            return len(stale)

    def _abort_if_live(self, session: Session, race: Race) -> None:
        if RaceStatus(race.status) not in _LIVE:
            return
        self._apply_stored_standings(session, race)
        race.status = RaceStatus.ABORTED.value
        race.finished_at = race.finished_at or utc_now()

    @staticmethod
    def _apply_stored_standings(session: Session, race: Race) -> None:
        participants = list(
            session.scalars(select(RaceParticipant).where(RaceParticipant.race_id == race.id))
        )
        stored = [
            (
                participant,
                list(
                    session.scalars(
                        select(Lap)
                        .where(Lap.participant_id == participant.id)
                        .order_by(Lap.lap_number)
                    )
                ),
            )
            for participant in participants
        ]
        ranked = sorted(
            stored,
            key=lambda item: _stored_standing_key(item[0], item[1], race.target_laps),
        )
        for position, (participant, laps) in enumerate(ranked, start=1):
            participant.laps_completed = len(laps)
            participant.best_lap_ns = min((lap.lap_time_ns for lap in laps), default=None)
            participant.total_time_ns = laps[-1].race_time_ns if laps else None
            participant.finished = len(laps) >= race.target_laps
            participant.final_position = position

    # --- helpers -------------------------------------------------------------------------------

    @staticmethod
    def _validate_provider(provider_id: str) -> str:
        clean = provider_id.strip()
        if not clean:
            raise ValidationError("error.race.provider_required")
        if len(clean) > MAX_PROVIDER_ID_LENGTH:
            raise ValidationError("error.race.provider_invalid")
        return clean

    @staticmethod
    def _validate_name(name: str) -> str:
        clean = name.strip()
        if not clean:
            raise ValidationError("error.race.name.required")
        if len(clean) > 100:
            raise ValidationError("error.race.name.too_long", limit=100)
        return clean

    @staticmethod
    def _validate_laps(laps: int) -> None:
        if not 1 <= laps <= MAX_LAPS:
            raise ValidationError("error.race.laps", maximum=MAX_LAPS)

    def _require_active_track(self, track_id: TrackId) -> TrackInfo:
        track = self._tracks.get_track(track_id)
        if track is None:
            raise ValidationError("error.race.track_unknown")
        if not track.is_active:
            raise ValidationError("error.race.track_inactive", track=track.name)
        return track

    @staticmethod
    def _load(session: Session, race_id: int) -> Race:
        race = session.get(Race, race_id)
        if race is None:
            raise ValidationError("error.race.not_found")
        return race

    def _load_editable(self, session: Session, race_id: int) -> Race:
        race = self._load(session, race_id)
        if RaceStatus(race.status) not in _EDITABLE:
            raise ValidationError("error.race.not_editable")
        return race

    @staticmethod
    def _participant_lanes(session: Session, race_id: int) -> list[int]:
        return list(
            session.scalars(select(RaceParticipant.lane).where(RaceParticipant.race_id == race_id))
        )

    @staticmethod
    def _update_readiness(session: Session, race: Race) -> None:
        count = session.scalar(
            select(func.count())
            .select_from(RaceParticipant)
            .where(RaceParticipant.race_id == race.id)
        )
        race.status = (RaceStatus.READY if count else RaceStatus.CREATED).value

    def _set_status(self, race_id: RaceId, status: RaceStatus, *, started: bool = False) -> None:
        with self._database.session() as session:
            race = self._load(session, race_id)
            race.status = status.value
            if started:
                race.started_at = utc_now()

    def _participant_info(self, participant: RaceParticipant) -> ParticipantInfo:
        driver = self._drivers.get_driver(DriverId(participant.driver_id))
        vehicle_id = None if participant.vehicle_id is None else VehicleId(participant.vehicle_id)
        vehicle = None if vehicle_id is None else self._vehicles.get_vehicle(vehicle_id)
        return ParticipantInfo(
            id=participant.id,
            driver_id=DriverId(participant.driver_id),
            driver_label=driver.label if driver else f"#{participant.driver_id}",
            vehicle_id=None
            if participant.vehicle_id is None
            else VehicleId(participant.vehicle_id),
            vehicle_label=(
                vehicle.label
                if vehicle
                else ("-" if participant.vehicle_id is None else f"#{participant.vehicle_id}")
            ),
            lane=participant.lane,
            start_number=driver.start_number if driver else None,
        )

    def _race_info(self, session: Session, race: Race) -> RaceInfo:
        track = self._tracks.get_track(TrackId(race.track_id)) if race.track_id else None
        participants = session.scalars(
            select(RaceParticipant)
            .where(RaceParticipant.race_id == race.id)
            .order_by(RaceParticipant.lane)
        )
        return RaceInfo(
            id=RaceId(race.id),
            name=race.name,
            track_id=None if race.track_id is None else TrackId(race.track_id),
            track_name=track.name if track else "-",
            lane_count=track.lane_count if track else 0,
            status=RaceStatus(race.status),
            laps=race.target_laps,
            timing_provider=race.timing_provider,
            participants=tuple(self._participant_info(p) for p in participants),
            created_at=race.created_at,
            started_at=race.started_at,
            finished_at=race.finished_at,
        )
