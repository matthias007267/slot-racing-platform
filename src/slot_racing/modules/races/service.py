"""Race management: configuration, participants, lifecycle persistence and stored results."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import replace
from datetime import datetime

from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from slot_racing.core.catalog import DriverCatalog, TrackCatalog, TrackInfo, VehicleCatalog
from slot_racing.core.diagnostics import record
from slot_racing.core.domain import (
    DriverId,
    ParticipantResult,
    RaceId,
    RaceMode,
    RaceStatus,
    TrackId,
    VehicleId,
)
from slot_racing.core.domain.scoring import scoring_for, time_trial_stored_key
from slot_racing.core.errors import ValidationError
from slot_racing.core.storage import Database, utc_now
from slot_racing.core.timing_registry import DEFAULT_TIMING_PROVIDER, TimingProviderRegistry
from slot_racing.modules.races import heats
from slot_racing.modules.races.models import Lap, Race, RaceParticipant, Sector, TimeMeasurement
from slot_racing.modules.races.types import (
    HeatBriefing,
    HeatInfo,
    LaneRanking,
    LapRecord,
    ParticipantInfo,
    RaceInfo,
    ResultRow,
    TimeBest,
    TimeMeasurementInfo,
)

MAX_LAPS = 999
MAX_DURATION_MINUTES = 999
MAX_PROVIDER_ID_LENGTH = 64

_EDITABLE = (RaceStatus.CREATED, RaceStatus.READY)
_LIVE = (RaceStatus.RUNNING, RaceStatus.PAUSED)
_MISSING_LAP_TIME = 2**62


def _measurement_order(row: TimeMeasurement) -> tuple[int, datetime, int]:
    """Faster times win. An equal time keeps the earlier measurement."""
    return (row.time_ns, row.recorded_at, row.id)


def parse_duration_minutes(text: str) -> int:
    """Whole minutes from a free-text field. Decimals, zero and text are rejected."""
    clean = text.strip()
    if not clean.isdigit():
        raise ValidationError("error.race.duration_invalid")
    minutes = int(clean)
    if not 1 <= minutes <= MAX_DURATION_MINUTES:
        raise ValidationError("error.race.duration_invalid")
    return minutes


def _shown_lane(current: int | None, laps: Sequence[Lap]) -> int:
    if current is not None:
        return current
    if laps and laps[-1].lane is not None:
        return laps[-1].lane
    return 0


def _best_row(rows: Sequence[TimeMeasurement]) -> TimeMeasurement | None:
    if not rows:
        return None
    return min(rows, key=_measurement_order)


def _best_by(
    rows: Sequence[TimeMeasurement],
    key: Callable[[TimeMeasurement], tuple[object, ...]],
) -> list[TimeMeasurement]:
    chosen: dict[tuple[object, ...], TimeMeasurement] = {}
    for row in rows:
        group = key(row)
        current = chosen.get(group)
        if current is None or _measurement_order(row) < _measurement_order(current):
            chosen[group] = row
    return list(chosen.values())


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
    lane = participant.lane or 0
    if completed >= target_laps:
        return (0, last_time, lane, 0)
    return (1, -completed, last_time, lane)


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
                mode=RaceMode.LAPS.value,
                timing_provider=provider,
                duration_minutes=None,
                status=RaceStatus.CREATED.value,
            )
            session.add(race)
            session.flush()
            return self._race_info(session, race)

    def create_time_trial(
        self,
        name: str,
        track_id: TrackId,
        timing_provider: str | None = None,
        duration_minutes: int | None = None,
    ) -> RaceInfo:
        """A time trial has no lap target. Each measured lap is stored as its own result.

        ``duration_minutes`` limits one heat. ``None`` keeps the session open until it is stopped,
        which is how time trials created before a duration existed behave.
        """
        clean_name = self._validate_name(name)
        provider = self._validate_provider(timing_provider or self.default_provider_id())
        self._require_active_track(track_id)
        if duration_minutes is not None:
            self._validate_duration(duration_minutes)
        with self._database.session() as session:
            race = Race(
                name=clean_name,
                track_id=track_id,
                target_laps=0,
                mode=RaceMode.TIME_TRIAL.value,
                timing_provider=provider,
                duration_minutes=duration_minutes,
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
        mode: RaceMode | None = None,
        *,
        duration_minutes: int | None = None,
        set_duration: bool = False,
    ) -> RaceInfo:
        """Change the configuration. ``timing_provider=None`` keeps the stored provider.

        ``mode=None`` keeps the stored mode. A time trial does not store a lap target.
        """
        clean_name = self._validate_name(name)
        provider = None if timing_provider is None else self._validate_provider(timing_provider)
        chosen = None if mode is None else self._validate_mode(mode)
        if chosen is RaceMode.LAPS:
            self._validate_laps(laps)
        with self._database.session() as session:
            race = self._load_editable(session, race_id)
            if chosen is None:
                chosen = self._validate_mode(RaceMode(race.mode))
            if chosen is RaceMode.LAPS:
                self._validate_laps(laps)
            if track_id != race.track_id:
                track = self._require_active_track(track_id)
                lanes = [
                    lane for lane in self._participant_lanes(session, race.id) if lane is not None
                ]
                planned = heats.uses_heats(session, race.id)
                too_many = not planned and len(lanes) > track.lane_count
                if too_many or any(lane > track.lane_count for lane in lanes):
                    raise ValidationError("error.race.track_too_small", lanes=track.lane_count)
                race.track_id = track_id
            race.name = clean_name
            race.mode = chosen.value
            race.target_laps = laps if chosen is RaceMode.LAPS else 0
            if chosen is RaceMode.LAPS:
                race.duration_minutes = None
            elif set_duration:
                if duration_minutes is not None:
                    self._validate_duration(duration_minutes)
                race.duration_minutes = duration_minutes
            if provider is not None:
                race.timing_provider = provider
            session.flush()
            if heats.uses_heats(session, race.id) and not heats.has_completed(session, race.id):
                current = self._tracks.get_track(TrackId(race.track_id)) if race.track_id else None
                if current is not None:
                    heats.replace_open_plan(session, race, current.lane_count)
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

    def enroll_driver(
        self, race_id: RaceId, driver_id: DriverId, vehicle_id: VehicleId
    ) -> ParticipantInfo:
        """Add a driver without a lane. The heat plan assigns every lane later."""
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
            if race.track_id is None or self._tracks.get_track(TrackId(race.track_id)) is None:
                raise ValidationError("error.race.no_track")
            existing = list(
                session.scalars(select(RaceParticipant).where(RaceParticipant.race_id == race.id))
            )
            if any(p.driver_id == driver_id for p in existing):
                raise ValidationError("error.race.driver_duplicate", driver=driver.label)
            participant = RaceParticipant(
                race_id=race.id, driver_id=driver_id, vehicle_id=vehicle_id, lane=None
            )
            session.add(participant)
            session.flush()
            self._update_readiness(session, race)
            return self._participant_info(participant)

    def update_enrolled(
        self,
        race_id: RaceId,
        participant_id: int,
        driver_id: DriverId,
        vehicle_id: VehicleId,
    ) -> ParticipantInfo:
        """Change driver or vehicle and leave the lane to the heat plan."""
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
            others = [
                other
                for other in session.scalars(
                    select(RaceParticipant).where(RaceParticipant.race_id == race.id)
                )
                if other.id != participant.id
            ]
            if any(other.driver_id == driver_id for other in others):
                raise ValidationError("error.race.driver_duplicate", driver=driver.label)
            participant.driver_id = driver_id
            participant.vehicle_id = vehicle_id
            session.flush()
            return self._participant_info(participant)

    def plan_heats(self, race_id: RaceId) -> list[HeatInfo]:
        """Build or rebuild the open heats from the current drivers and the track's lane count."""
        with self._database.session() as session:
            race = self._load_editable(session, race_id)
            track = self._tracks.get_track(TrackId(race.track_id)) if race.track_id else None
            if track is None:
                raise ValidationError("error.race.no_track")
            heats.replace_open_plan(session, race, track.lane_count)
            return heats.list_heats(session, race.id)

    def heat_plan(self, race_id: RaceId) -> list[HeatInfo]:
        with self._database.session() as session:
            self._load(session, race_id)
            return heats.list_heats(session, race_id)

    def heat_briefing(self, race_id: RaceId) -> HeatBriefing | None:
        with self._database.session() as session:
            race = self._load(session, race_id)
            track = self._tracks.get_track(TrackId(race.track_id)) if race.track_id else None
            lane_count = track.lane_count if track else 0
            names = {
                participant.id: self._participant_info(participant).driver_label
                for participant in session.scalars(
                    select(RaceParticipant).where(RaceParticipant.race_id == race.id)
                )
            }
            return heats.briefing(session, race, lane_count, names)

    def has_pending_heats(self, race_id: RaceId) -> bool:
        with self._database.session() as session:
            return heats.has_pending(session, race_id)

    def postpone_driver(self, race_id: RaceId, participant_id: int) -> HeatBriefing | None:
        with self._database.session() as session:
            race = self._load(session, race_id)
            if RaceStatus(race.status) not in _EDITABLE:
                raise ValidationError("error.race.not_editable")
            track = self._require_race_track(session, race)
            heats.postpone(session, race, participant_id, track.lane_count)
            return self._briefing_in_session(session, race, track.lane_count)

    def disqualify_driver(self, race_id: RaceId, participant_id: int) -> bool:
        """Drop the driver from heats that have not started.

        Returns True when that was the last open heat and the race is now over.
        """
        with self._database.session() as session:
            race = self._load(session, race_id)
            if RaceStatus(race.status) not in _EDITABLE:
                raise ValidationError("error.race.not_editable")
            track = self._require_race_track(session, race)
            finished = heats.disqualify(session, race, participant_id, track.lane_count)
            if finished:
                race.status = RaceStatus.FINISHED.value
                race.finished_at = race.finished_at or utc_now()
            return finished

    def activate_next_heat(self, race_id: RaceId) -> RaceInfo:
        """Seat the next heat. A race without heats is returned unchanged."""
        with self._database.session() as session:
            race = self._load(session, race_id)
            if heats.uses_heats(session, race.id) and not heats.assign_next(session, race):
                raise ValidationError("error.race.not_startable", status=race.status)
            info = self._race_info(session, race)
        if not info.participants:
            return info
        seated = tuple(
            participant for participant in info.participants if participant.lane is not None
        )
        if len(seated) == len(info.participants):
            return info
        return replace(info, participants=seated)

    def cancel_heat_start(self, race_id: RaceId) -> None:
        with self._database.session() as session:
            race = self._load(session, race_id)
            heats.revert_running_heat(session, race)

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
        if not self._drivers.list_drivers():
            raise ValidationError("error.race.no_drivers")
        competing = tuple(
            participant for participant in race.participants if not participant.disqualified
        )
        if not competing:
            raise ValidationError("error.race.no_participants")
        track = None if race.track_id is None else self._tracks.get_track(race.track_id)
        if track is None:
            raise ValidationError("error.race.no_track")
        if not track.is_active:
            raise ValidationError("error.race.track_inactive", track=track.name)
        if self._providers is not None:
            self._providers.check(
                race.timing_provider, lane_count=self._provider_lane_count(race, len(competing))
            )
        if self.has_heat_plan(race.id) and not self.has_pending_heats(race.id):
            raise ValidationError("error.race.not_startable", status=race.status.value)
        for participant in competing:
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
                        lane=_shown_lane(participant.lane, laps),
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
                shown_lane = lap.lane if lap.lane is not None else (lane or 0)
                sectors = session.scalars(
                    select(Sector.sector_time_ns)
                    .where(Sector.lap_id == lap.id)
                    .order_by(Sector.sector_number)
                )
                records.append(
                    LapRecord(
                        participant_id=lap.participant_id,
                        lane=shown_lane,
                        lap_number=lap.lap_number,
                        lap_time_ns=lap.lap_time_ns,
                        race_time_ns=lap.race_time_ns,
                        sector_times_ns=tuple(sectors),
                    )
                )
            return records

    def list_time_measurements(
        self, *, race_id: RaceId | None = None, track_id: TrackId | None = None
    ) -> list[TimeMeasurementInfo]:
        """Every stored measurement, oldest first. History is not replaced by a later best time."""
        with self._database.session() as session:
            rows = self._measurement_rows(session, race_id=race_id, track_id=track_id)
            return [self._measurement_info(row) for row in rows]

    def personal_best(
        self,
        driver_id: DriverId,
        vehicle_id: VehicleId | None,
        lane: int,
        *,
        track_id: TrackId | None = None,
    ) -> TimeBest | None:
        """Best time of one driver with one vehicle on one lane. Other lanes stay untouched."""
        with self._database.session() as session:
            rows = [
                row
                for row in self._measurement_rows(session, track_id=track_id)
                if row.driver_id == driver_id
                and row.vehicle_id == vehicle_id
                and row.lane == lane
                and (track_id is None or row.track_id == track_id)
            ]
            best = _best_row(rows)
            return None if best is None else self._time_best(best)

    def driver_bests_on_lane(self, lane: int, *, track_id: TrackId | None = None) -> list[TimeBest]:
        """Fastest driver on this lane first. Each driver keeps only that lane's best time."""
        with self._database.session() as session:
            rows = [
                row
                for row in self._measurement_rows(session, track_id=track_id)
                if row.lane == lane
            ]
            grouped = _best_by(rows, lambda row: (row.track_id, row.driver_id, row.lane))
            return [self._time_best(row) for row in sorted(grouped, key=_measurement_order)]

    def vehicle_bests_on_lane(
        self, vehicle_id: VehicleId, lane: int, *, track_id: TrackId | None = None
    ) -> list[TimeBest]:
        """How fast this vehicle was driven on this lane, one row per driver, fastest first."""
        with self._database.session() as session:
            rows = [
                row
                for row in self._measurement_rows(session, track_id=track_id)
                if row.vehicle_id == vehicle_id and row.lane == lane
            ]
            grouped = _best_by(
                rows,
                lambda row: (row.track_id, row.driver_id, row.vehicle_id, row.lane),
            )
            return [self._time_best(row) for row in sorted(grouped, key=_measurement_order)]

    def overall_bests(self, *, track_id: TrackId | None = None) -> list[TimeBest]:
        """Personal bests of driver + vehicle + lane, fastest first. Every row names its lane."""
        with self._database.session() as session:
            rows = self._measurement_rows(session, track_id=track_id)
            grouped = _best_by(
                rows, lambda row: (row.track_id, row.driver_id, row.vehicle_id, row.lane)
            )
            return [self._time_best(row) for row in sorted(grouped, key=_measurement_order)]

    def lane_rankings(self, *, track_id: TrackId | None = None) -> list[LaneRanking]:
        """One ranking per lane that has a measurement. Lanes are never combined into one list."""
        with self._database.session() as session:
            rows = self._measurement_rows(session, track_id=track_id)
            lanes = sorted({row.lane for row in rows})
            rankings: list[LaneRanking] = []
            for lane in lanes:
                grouped = _best_by(
                    [row for row in rows if row.lane == lane],
                    lambda row: (row.track_id, row.driver_id, row.lane),
                )
                places = tuple(
                    self._time_best(row) for row in sorted(grouped, key=_measurement_order)
                )
                rankings.append(LaneRanking(lane=lane, places=places))
            return rankings

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
            stored_number = lap_number
            race_time = race_time_ns
            if heats.uses_heats(session, race_id):
                stored_number = heats.stored_lap_number(session, participant.id, lap_number)
                race_time = heats.heat_time_base(session, race_id, participant) + race_time_ns
            lap = Lap(
                race_id=race_id,
                participant_id=participant.id,
                lane=lane,
                lap_number=stored_number,
                lap_time_ns=lap_time_ns,
                race_time_ns=race_time,
            )
            session.add(lap)
            session.flush()
            session.add_all(
                Sector(lap_id=lap.id, sector_number=sector_number, sector_time_ns=time_ns)
                for sector_number, time_ns in sorted(sector_times_ns.items())
            )
            participant.laps_completed = stored_number
            if participant.best_lap_ns is None or lap_time_ns < participant.best_lap_ns:
                participant.best_lap_ns = lap_time_ns
            race = self._load(session, race_id)
            if race.mode == RaceMode.TIME_TRIAL.value:
                session.add(
                    TimeMeasurement(
                        race_id=race.id,
                        track_id=race.track_id,
                        driver_id=participant.driver_id,
                        vehicle_id=participant.vehicle_id,
                        lane=lane,
                        time_ns=lap_time_ns,
                        recorded_at=utc_now(),
                    )
                )

    def record_finished(
        self, race_id: RaceId, results: Sequence[ParticipantResult], *, aborted: bool
    ) -> None:
        with self._database.session() as session:
            race = self._load(session, race_id)
            if heats.uses_heats(session, race.id):
                for result in results:
                    heats.accumulate_heat_time(session, race.id, result.lane, result.total_time_ns)
                if heats.finish_heat(session, race, aborted=aborted):
                    race.status = RaceStatus.READY.value
                    return
                race.status = (RaceStatus.ABORTED if aborted else RaceStatus.FINISHED).value
                race.finished_at = utc_now()
                return
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

    def restart_aborted(self, race_id: RaceId) -> RaceInfo:
        """Open a new race from an aborted one and leave the aborted race in the history.

        Mode, track, participants, lanes and the lap or time target are copied.
        Laps, times and the aborted status are not. A heat race gets a fresh
        plan from the same drivers instead of the heats that were already driven.
        """
        with self._database.session() as session:
            source = self._load(session, race_id)
            if RaceStatus(source.status) is not RaceStatus.ABORTED:
                raise ValidationError("error.race.not_restartable")
            if source.track_id is None:
                raise ValidationError("error.race.no_track")
            track = self._require_active_track(TrackId(source.track_id))
            race = Race(
                name=source.name,
                track_id=source.track_id,
                track_layout_id=source.track_layout_id,
                target_laps=source.target_laps,
                mode=source.mode,
                timing_provider=source.timing_provider,
                duration_minutes=source.duration_minutes,
                status=RaceStatus.CREATED.value,
            )
            session.add(race)
            session.flush()
            previous = list(
                session.scalars(
                    select(RaceParticipant)
                    .where(RaceParticipant.race_id == source.id)
                    .order_by(RaceParticipant.id)
                )
            )
            planned = heats.uses_heats(session, source.id)
            for old in previous:
                session.add(
                    RaceParticipant(
                        race_id=race.id,
                        driver_id=old.driver_id,
                        vehicle_id=old.vehicle_id,
                        lane=None if planned else old.lane,
                    )
                )
            session.flush()
            if planned:
                heats.replace_open_plan(session, race, track.lane_count)
            self._update_readiness(session, race)
            info = self._race_info(session, race)
        record(
            "RACE_RESTART",
            module="races",
            page="races",
            result="created",
            race_id=int(info.id),
            source_race_id=int(race_id),
        )
        return info

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
        mode = RaceMode(race.mode)
        if mode is RaceMode.TIME_TRIAL:
            ranked = sorted(
                stored,
                key=lambda item: time_trial_stored_key(
                    min((lap.lap_time_ns for lap in item[1]), default=None),
                    item[0].lane or 0,
                ),
            )
        else:
            ranked = sorted(
                stored,
                key=lambda item: _stored_standing_key(item[0], item[1], race.target_laps),
            )
        scoring = scoring_for(mode)
        for position, (participant, laps) in enumerate(ranked, start=1):
            participant.laps_completed = len(laps)
            participant.best_lap_ns = min((lap.lap_time_ns for lap in laps), default=None)
            participant.total_time_ns = laps[-1].race_time_ns if laps else None
            participant.finished = scoring.participant_finished(len(laps), race.target_laps)
            participant.final_position = position

    # --- helpers -------------------------------------------------------------------------------

    @staticmethod
    def _validate_mode(mode: RaceMode) -> RaceMode:
        chosen = RaceMode(mode)
        if chosen not in (RaceMode.LAPS, RaceMode.TIME_TRIAL):
            raise ValidationError("error.race.mode_unknown")
        return chosen

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

    @staticmethod
    def _validate_duration(minutes: int) -> None:
        if not 1 <= minutes <= MAX_DURATION_MINUTES:
            raise ValidationError("error.race.duration_invalid")

    def has_heat_plan(self, race_id: RaceId) -> bool:
        with self._database.session() as session:
            return heats.uses_heats(session, race_id)

    def _provider_lane_count(self, race: RaceInfo, competing: int) -> int:
        briefing = self.heat_briefing(race.id)
        if briefing is None:
            return competing
        seated = sum(1 for seat in briefing.seats if seat.participant_id is not None)
        return seated or competing

    def _require_race_track(self, session: Session, race: Race) -> TrackInfo:
        del session
        track = self._tracks.get_track(TrackId(race.track_id)) if race.track_id else None
        if track is None:
            raise ValidationError("error.race.no_track")
        return track

    def _briefing_in_session(
        self, session: Session, race: Race, lane_count: int
    ) -> HeatBriefing | None:
        names = {
            participant.id: self._participant_info(participant).driver_label
            for participant in session.scalars(
                select(RaceParticipant).where(RaceParticipant.race_id == race.id)
            )
        }
        return heats.briefing(session, race, lane_count, names)

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
        if heats.has_completed(session, race.id):
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

    def _measurement_rows(
        self,
        session: Session,
        *,
        race_id: RaceId | None = None,
        track_id: TrackId | None = None,
    ) -> list[TimeMeasurement]:
        query = select(TimeMeasurement).order_by(TimeMeasurement.recorded_at, TimeMeasurement.id)
        if race_id is not None:
            query = query.where(TimeMeasurement.race_id == race_id)
        if track_id is not None:
            query = query.where(TimeMeasurement.track_id == track_id)
        return list(session.scalars(query))

    def _measurement_info(self, row: TimeMeasurement) -> TimeMeasurementInfo:
        driver_label, vehicle_label, vehicle_id = self._entry_labels(row.driver_id, row.vehicle_id)
        return TimeMeasurementInfo(
            id=row.id,
            race_id=RaceId(row.race_id),
            track_id=None if row.track_id is None else TrackId(row.track_id),
            driver_id=DriverId(row.driver_id),
            driver_label=driver_label,
            vehicle_id=vehicle_id,
            vehicle_label=vehicle_label,
            lane=row.lane,
            time_ns=row.time_ns,
            recorded_at=row.recorded_at,
        )

    def _time_best(self, row: TimeMeasurement) -> TimeBest:
        info = self._measurement_info(row)
        return TimeBest(
            measurement_id=info.id,
            driver_id=info.driver_id,
            driver_label=info.driver_label,
            vehicle_id=info.vehicle_id,
            vehicle_label=info.vehicle_label,
            lane=info.lane,
            track_id=info.track_id,
            time_ns=info.time_ns,
            recorded_at=info.recorded_at,
        )

    def _entry_labels(
        self, driver_id: int, vehicle_id: int | None
    ) -> tuple[str, str, VehicleId | None]:
        driver = self._drivers.get_driver(DriverId(driver_id))
        typed_vehicle = None if vehicle_id is None else VehicleId(vehicle_id)
        vehicle = None if typed_vehicle is None else self._vehicles.get_vehicle(typed_vehicle)
        return (
            driver.label if driver else f"#{driver_id}",
            vehicle.label if vehicle else ("-" if vehicle_id is None else f"#{vehicle_id}"),
            typed_vehicle,
        )

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
            disqualified=participant.disqualified,
        )

    def _race_info(self, session: Session, race: Race) -> RaceInfo:
        track = self._tracks.get_track(TrackId(race.track_id)) if race.track_id else None
        participants = session.scalars(
            select(RaceParticipant)
            .where(RaceParticipant.race_id == race.id)
            .order_by(RaceParticipant.lane, RaceParticipant.id)
        )
        return RaceInfo(
            id=RaceId(race.id),
            name=race.name,
            track_id=None if race.track_id is None else TrackId(race.track_id),
            track_name=track.name if track else "-",
            lane_count=track.lane_count if track else 0,
            status=RaceStatus(race.status),
            mode=RaceMode(race.mode),
            laps=race.target_laps,
            timing_provider=race.timing_provider,
            participants=tuple(self._participant_info(p) for p in participants),
            created_at=race.created_at,
            started_at=race.started_at,
            finished_at=race.finished_at,
            duration_minutes=race.duration_minutes,
        )
