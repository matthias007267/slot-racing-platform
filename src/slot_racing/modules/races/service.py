"""Race management: configuration, participants, lifecycle persistence and stored results."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import replace
from datetime import datetime

from sqlalchemy import delete, func, select, update
from sqlalchemy.orm import Session

from slot_racing.core.catalog import (
    DriverCatalog,
    RaceHistoryCatalog,
    RaceIndexEntry,
    TrackCatalog,
    TrackInfo,
    VehicleCatalog,
)
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
from slot_racing.core.statistics import HistoryRace, LayoutRevision
from slot_racing.core.storage import Database, utc_now
from slot_racing.core.timing_registry import DEFAULT_TIMING_PROVIDER, TimingProviderRegistry
from slot_racing.modules.races import heats
from slot_racing.modules.races.history import load_completed, load_layouts, load_one
from slot_racing.modules.races.models import Lap, Race, RaceParticipant, Sector, TimeMeasurement
from slot_racing.modules.races.naming import suggest_distinct_name
from slot_racing.modules.races.planning import effective_lane_count
from slot_racing.modules.races.types import (
    FieldStanding,
    HeatBriefing,
    HeatInfo,
    LaneRanking,
    LapRecord,
    ParticipantInfo,
    RaceInfo,
    RestartPreview,
    RestartSeat,
    ResultRow,
    TimeBest,
    TimeMeasurementInfo,
)

MAX_LAPS = 999
MAX_REPETITIONS = 99
MAX_DURATION_MINUTES = 999
MAX_PROVIDER_ID_LENGTH = 64

_EDITABLE = (RaceStatus.CREATED, RaceStatus.READY)
_LIVE = (RaceStatus.RUNNING, RaceStatus.PAUSED)
_RESTARTABLE = (RaceStatus.FINISHED, RaceStatus.ABORTED)
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


def _counting_measurements(rows: Sequence[TimeMeasurement]) -> list[TimeMeasurement]:
    """Measurements that may set a best time. An excluded or non-positive row stays stored."""
    return [row for row in rows if not row.statistics_excluded and row.time_ns > 0]


def _mark_measurement(
    session: Session, lap: Lap, participant: RaceParticipant | None, *, excluded: bool
) -> None:
    """Give the time-trial row of this lap the same statistics mark. Other rows stay put."""
    linked = list(session.scalars(select(TimeMeasurement).where(TimeMeasurement.lap_id == lap.id)))
    if linked:
        for row in linked:
            row.statistics_excluded = excluded
        return
    if participant is None:
        return
    lane = lap.lane if lap.lane is not None else participant.lane
    if lane is None:
        return
    pending = list(
        session.scalars(
            select(TimeMeasurement)
            .where(
                TimeMeasurement.race_id == lap.race_id,
                TimeMeasurement.driver_id == participant.driver_id,
                TimeMeasurement.lane == lane,
                TimeMeasurement.time_ns == lap.lap_time_ns,
                TimeMeasurement.lap_id.is_(None),
                TimeMeasurement.statistics_excluded.is_(not excluded),
            )
            .order_by(TimeMeasurement.id)
        )
    )
    if not pending:
        return
    pending[0].lap_id = lap.id
    pending[0].statistics_excluded = excluded


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


class RaceService(RaceHistoryCatalog):
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
        self,
        name: str,
        track_id: TrackId,
        laps: int,
        timing_provider: str | None = None,
        *,
        repetitions: int = 1,
    ) -> RaceInfo:
        clean_name = self._validate_name(name)
        self._validate_laps(laps)
        self._validate_repetitions(repetitions)
        provider = self._validate_provider(timing_provider or self.default_provider_id())
        self._require_active_track(track_id)
        with self._database.session() as session:
            race = Race(
                name=clean_name,
                track_id=track_id,
                track_layout_id=self._tracks.current_layout_id(track_id),
                target_laps=laps,
                mode=RaceMode.LAPS.value,
                timing_provider=provider,
                duration_minutes=None,
                repetitions=repetitions,
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
        *,
        repetitions: int = 1,
    ) -> RaceInfo:
        """A time trial has no lap target. Each measured lap is stored as its own result.

        ``duration_minutes`` limits one heat. ``None`` keeps the session open until it is stopped,
        which is how time trials created before a duration existed behave.
        """
        clean_name = self._validate_name(name)
        provider = self._validate_provider(timing_provider or self.default_provider_id())
        self._validate_repetitions(repetitions)
        self._require_active_track(track_id)
        if duration_minutes is not None:
            self._validate_duration(duration_minutes)
        with self._database.session() as session:
            race = Race(
                name=clean_name,
                track_id=track_id,
                track_layout_id=self._tracks.current_layout_id(track_id),
                target_laps=0,
                mode=RaceMode.TIME_TRIAL.value,
                timing_provider=provider,
                duration_minutes=duration_minutes,
                repetitions=repetitions,
                status=RaceStatus.CREATED.value,
            )
            session.add(race)
            session.flush()
            return self._race_info(session, race)

    def create_free_race(
        self,
        name: str,
        track_id: TrackId,
        timing_provider: str | None = None,
    ) -> RaceInfo:
        """A free session keeps the track and the timing layout and adds no race target.

        Each lane later receives a driver and a vehicle. Lap count, duration and repetitions
        are not part of this mode. The session runs until it is stopped.
        """
        clean_name = self._validate_name(name)
        provider = self._validate_provider(timing_provider or self.default_provider_id())
        self._require_active_track(track_id)
        with self._database.session() as session:
            race = Race(
                name=clean_name,
                track_id=track_id,
                track_layout_id=self._tracks.current_layout_id(track_id),
                target_laps=0,
                mode=RaceMode.FREE.value,
                timing_provider=provider,
                duration_minutes=None,
                repetitions=1,
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
        active_lanes: int | None = None,
        set_active_lanes: bool = False,
        repetitions: int | None = None,
        set_repetitions: bool = False,
    ) -> RaceInfo:
        """Change the configuration. ``timing_provider=None`` keeps the stored provider.

        ``mode=None`` keeps the stored mode. A time trial does not store a lap target.
        ``set_active_lanes`` stores how many of a two-lane track this race uses.
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
                race.track_layout_id = self._tracks.current_layout_id(track_id)
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
            if set_repetitions:
                if repetitions is None:
                    raise ValidationError("error.race.repetitions", maximum=MAX_REPETITIONS)
                self._validate_repetitions(repetitions)
                race.repetitions = repetitions
            physical = self._physical_lanes(race)
            if set_active_lanes:
                self._store_active_lanes(race, physical, active_lanes)
            else:
                self._keep_active_lanes(race, physical)
            session.flush()
            if chosen is RaceMode.FREE:
                heats.clear_plan(session, race.id)
            elif (
                physical > 0
                and heats.uses_heats(session, race.id)
                and not heats.has_completed(session, race.id)
            ):
                heats.replace_open_plan(
                    session, race, effective_lane_count(race.active_lanes, physical)
                )
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
        """Add a driver without a lane. An existing heat plan is rebuilt from the new roster."""
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
            self._replan_open_heats(session, race)
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

    def set_free_assignments(
        self,
        race_id: RaceId,
        seats: Sequence[tuple[int, DriverId, VehicleId]],
    ) -> RaceInfo:
        """Replace the roster of a free session with one driver and vehicle per occupied lane.

        A lane that is left out stays empty. One occupied lane is enough to drive the other.
        Heat plans are removed: this mode does not rotate drivers.
        """
        if not seats:
            raise ValidationError("error.race.no_participants")
        checked: list[tuple[int, DriverId, VehicleId]] = []
        seen_drivers: set[int] = set()
        seen_lanes: set[int] = set()
        for lane, driver_id, vehicle_id in seats:
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
            if int(driver_id) in seen_drivers:
                raise ValidationError("error.race.driver_duplicate", driver=driver.label)
            if lane in seen_lanes:
                raise ValidationError("error.race.lane_taken", lane=lane)
            seen_drivers.add(int(driver_id))
            seen_lanes.add(lane)
            checked.append((lane, driver_id, vehicle_id))
        with self._database.session() as session:
            race = self._load_editable(session, race_id)
            if RaceMode(race.mode) is not RaceMode.FREE:
                raise ValidationError("error.race.mode_unknown")
            track = self._tracks.get_track(TrackId(race.track_id)) if race.track_id else None
            if track is None:
                raise ValidationError("error.race.no_track")
            for lane, _driver_id, _vehicle_id in checked:
                if not 1 <= lane <= track.lane_count:
                    raise ValidationError("error.race.lane_invalid", maximum=track.lane_count)
            heats.clear_plan(session, race.id)
            existing = list(
                session.scalars(select(RaceParticipant).where(RaceParticipant.race_id == race.id))
            )
            for participant in existing:
                session.delete(participant)
            session.flush()
            for lane, driver_id, vehicle_id in checked:
                session.add(
                    RaceParticipant(
                        race_id=race.id,
                        driver_id=driver_id,
                        vehicle_id=vehicle_id,
                        lane=lane,
                    )
                )
            session.flush()
            self._update_readiness(session, race)
            return self._race_info(session, race)

    def switch_free_lane(
        self,
        race_id: RaceId,
        lane: int,
        driver_id: DriverId,
        vehicle_id: VehicleId,
    ) -> tuple[bool, ParticipantInfo]:
        """Change one lane of a running free session.

        The same driver and vehicle return ``False`` and leave every counter untouched.
        A different driver or vehicle, including a return to an earlier pair, replaces the
        assignment and drops that lane's stored laps. The other lane is not written.
        """
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
            race = self._load(session, race_id)
            if RaceMode(race.mode) is not RaceMode.FREE:
                raise ValidationError("error.race.not_switchable")
            if RaceStatus(race.status) not in (RaceStatus.RUNNING, RaceStatus.PAUSED):
                raise ValidationError("error.race.not_switchable")
            track = self._tracks.get_track(TrackId(race.track_id)) if race.track_id else None
            if track is None:
                raise ValidationError("error.race.no_track")
            if not 1 <= lane <= track.lane_count:
                raise ValidationError("error.race.lane_invalid", maximum=track.lane_count)
            seated = list(
                session.scalars(select(RaceParticipant).where(RaceParticipant.race_id == race.id))
            )
            current = next((person for person in seated if person.lane == lane), None)
            others = [person for person in seated if person is not current]
            if any(person.driver_id == driver_id for person in others):
                raise ValidationError("error.race.driver_duplicate", driver=driver.label)
            if (
                current is not None
                and current.driver_id == driver_id
                and current.vehicle_id == vehicle_id
            ):
                return False, self._participant_info(current)
            if current is None:
                current = RaceParticipant(
                    race_id=race.id,
                    driver_id=driver_id,
                    vehicle_id=vehicle_id,
                    lane=lane,
                )
                session.add(current)
                session.flush()
            else:
                self._clear_participant_laps(session, current.id)
                current.driver_id = driver_id
                current.vehicle_id = vehicle_id
                current.lane = lane
                current.laps_completed = 0
                current.best_lap_ns = None
                current.total_time_ns = None
                current.final_position = None
                current.finished = False
                session.flush()
            return True, self._participant_info(current)

    def reset_free_measurements(self, race_id: RaceId) -> None:
        """Delete the stored times of one running free session.

        Drivers, vehicles, lanes, the race row and every other race stay. The same clearing
        used when a lane changes hands is applied to each participant, without writing a new
        assignment.
        """
        with self._database.session() as session:
            race = self._load(session, race_id)
            if RaceMode(race.mode) is not RaceMode.FREE:
                raise ValidationError("error.race.not_resettable")
            if RaceStatus(race.status) not in (RaceStatus.RUNNING, RaceStatus.PAUSED):
                raise ValidationError("error.race.not_resettable")
            seated = list(
                session.scalars(select(RaceParticipant).where(RaceParticipant.race_id == race.id))
            )
            session.execute(delete(TimeMeasurement).where(TimeMeasurement.race_id == race.id))
            for participant in seated:
                self._clear_participant_laps(session, participant.id)
                participant.laps_completed = 0
                participant.best_lap_ns = None
                participant.total_time_ns = None
                participant.final_position = None
                participant.finished = False

    def plan_heats(self, race_id: RaceId) -> list[HeatInfo]:
        """Build or rebuild the open heats from the drivers and the lanes this race uses."""
        with self._database.session() as session:
            race = self._load_editable(session, race_id)
            if RaceMode(race.mode) is RaceMode.FREE:
                heats.clear_plan(session, race.id)
                return []
            if self._physical_lanes(race) < 1:
                raise ValidationError("error.race.no_track")
            heats.replace_open_plan(session, race, self._planning_lanes(race))
            return heats.list_heats(session, race.id)

    def _replan_open_heats(self, session: Session, race: Race) -> None:
        """Keep a roster change on the same rotation. Races without heats are left alone."""
        if not heats.uses_heats(session, race.id) or race.track_id is None:
            return
        if self._physical_lanes(race) < 1:
            return
        heats.replace_open_plan(session, race, self._planning_lanes(race))

    def heat_plan(self, race_id: RaceId) -> list[HeatInfo]:
        with self._database.session() as session:
            self._load(session, race_id)
            return heats.list_heats(session, race_id)

    def live_field(self, race_id: RaceId) -> tuple[FieldStanding, ...]:
        """Standings of every driver enrolled in this race.

        A free session and a race without heats return an empty tuple: the live engine already
        lists exactly the drivers who are on track. Heat races include drivers who are waiting
        out the current heat. The figures are the laps stored for this race, across every heat
        and repetition, in the same order the race result uses. Drivers who were never enrolled
        are not included.
        """
        with self._database.session() as session:
            race = self._load(session, race_id)
            if race.mode == RaceMode.FREE.value or not heats.uses_heats(session, race.id):
                return ()
            standings: list[FieldStanding] = []
            for participant, laps in heats.live_ranked(session, race):
                info = self._participant_info(participant)
                positive = [lap.lap_time_ns for lap in laps if lap.lap_time_ns > 0]
                last = laps[-1].lap_time_ns if laps else None
                total = laps[-1].race_time_ns if laps else None
                standings.append(
                    FieldStanding(
                        participant_id=participant.id,
                        driver_label=info.driver_label,
                        vehicle_label=info.vehicle_label,
                        start_number=info.start_number,
                        lane=participant.lane,
                        laps_completed=len(laps),
                        last_lap_ns=last if last is not None and last > 0 else None,
                        best_lap_ns=min(positive) if positive else None,
                        total_time_ns=total if total is not None and total > 0 else None,
                        lap_times_ns=tuple(lap.lap_time_ns for lap in laps),
                        finished=bool(participant.finished),
                    )
                )
            return tuple(standings)

    def heat_briefing(self, race_id: RaceId) -> HeatBriefing | None:
        with self._database.session() as session:
            race = self._load(session, race_id)
            lane_count = self._planning_lanes(race)
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
            self._require_race_track(session, race)
            lane_count = self._planning_lanes(race)
            heats.postpone(session, race, participant_id, lane_count)
            return self._briefing_in_session(session, race, lane_count)

    def disqualify_driver(self, race_id: RaceId, participant_id: int) -> bool:
        """Drop the driver from heats that have not started.

        Returns True when that was the last open heat and the race is now over.
        """
        with self._database.session() as session:
            race = self._load(session, race_id)
            if RaceStatus(race.status) not in _EDITABLE:
                raise ValidationError("error.race.not_editable")
            self._require_race_track(session, race)
            finished = heats.disqualify(session, race, participant_id, self._planning_lanes(race))
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
            self._replan_open_heats(session, race)
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

    def list_completed(self) -> tuple[HistoryRace, ...]:
        tracks = {int(track.id): track.name for track in self._tracks.list_tracks()}
        with self._database.session() as session:
            return load_completed(session, self._drivers, self._vehicles, tracks)

    def get_completed(self, race_id: RaceId) -> HistoryRace | None:
        tracks = {int(track.id): track.name for track in self._tracks.list_tracks()}
        with self._database.session() as session:
            return load_one(session, race_id, self._drivers, self._vehicles, tracks)

    def list_layouts(self, track_id: TrackId) -> tuple[LayoutRevision, ...]:
        with self._database.session() as session:
            return load_layouts(session, track_id)

    def list_index(self) -> tuple[RaceIndexEntry, ...]:
        with self._database.session() as session:
            races = session.scalars(select(Race).order_by(Race.id)).all()
            return tuple(
                RaceIndexEntry(
                    race_id=int(race.id),
                    name=race.name,
                    status=RaceStatus(race.status),
                    finished_at=race.finished_at,
                )
                for race in races
            )

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

    def set_lap_excluded(self, lap_id: int, *, excluded: bool) -> None:
        """Mark or restore one lap for statistics. The race result is not rewritten.

        Position, lap count, total time and the stored best lap stay on the participant.
        The lap row keeps its time. A time-trial measurement of that lap receives the same mark.
        """
        with self._database.session() as session:
            lap = session.get(Lap, lap_id)
            if lap is None:
                raise ValidationError("error.statistics.lap_unknown")
            lap.statistics_excluded = excluded
            participant = session.get(RaceParticipant, lap.participant_id)
            _mark_measurement(session, lap, participant, excluded=excluded)

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
            rows = _counting_measurements(
                [
                    row
                    for row in self._measurement_rows(session, track_id=track_id)
                    if row.driver_id == driver_id
                    and row.vehicle_id == vehicle_id
                    and row.lane == lane
                    and (track_id is None or row.track_id == track_id)
                ]
            )
            best = _best_row(rows)
            return None if best is None else self._time_best(best)

    def driver_bests_on_lane(self, lane: int, *, track_id: TrackId | None = None) -> list[TimeBest]:
        """Fastest driver on this lane first. Each driver keeps only that lane's best time."""
        with self._database.session() as session:
            rows = _counting_measurements(
                [
                    row
                    for row in self._measurement_rows(session, track_id=track_id)
                    if row.lane == lane
                ]
            )
            grouped = _best_by(rows, lambda row: (row.track_id, row.driver_id, row.lane))
            return [self._time_best(row) for row in sorted(grouped, key=_measurement_order)]

    def vehicle_bests_on_lane(
        self, vehicle_id: VehicleId, lane: int, *, track_id: TrackId | None = None
    ) -> list[TimeBest]:
        """How fast this vehicle was driven on this lane, one row per driver, fastest first."""
        with self._database.session() as session:
            rows = _counting_measurements(
                [
                    row
                    for row in self._measurement_rows(session, track_id=track_id)
                    if row.vehicle_id == vehicle_id and row.lane == lane
                ]
            )
            grouped = _best_by(
                rows,
                lambda row: (row.track_id, row.driver_id, row.vehicle_id, row.lane),
            )
            return [self._time_best(row) for row in sorted(grouped, key=_measurement_order)]

    def overall_bests(self, *, track_id: TrackId | None = None) -> list[TimeBest]:
        """Personal bests of driver + vehicle + lane, fastest first. Every row names its lane."""
        with self._database.session() as session:
            rows = _counting_measurements(self._measurement_rows(session, track_id=track_id))
            grouped = _best_by(
                rows, lambda row: (row.track_id, row.driver_id, row.vehicle_id, row.lane)
            )
            return [self._time_best(row) for row in sorted(grouped, key=_measurement_order)]

    def lane_rankings(self, *, track_id: TrackId | None = None) -> list[LaneRanking]:
        """One ranking per lane that has a measurement. Lanes are never combined into one list."""
        with self._database.session() as session:
            rows = _counting_measurements(self._measurement_rows(session, track_id=track_id))
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
            race = self._load(session, race_id)
            if race.mode == RaceMode.FREE.value:
                return
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
            if race.mode == RaceMode.TIME_TRIAL.value:
                session.add(
                    TimeMeasurement(
                        race_id=race.id,
                        track_id=race.track_id,
                        track_layout_id=race.track_layout_id,
                        driver_id=participant.driver_id,
                        vehicle_id=participant.vehicle_id,
                        lane=lane,
                        time_ns=lap_time_ns,
                        recorded_at=utc_now(),
                        lap_id=lap.id,
                    )
                )

    def record_finished(
        self, race_id: RaceId, results: Sequence[ParticipantResult], *, aborted: bool
    ) -> None:
        with self._database.session() as session:
            race = self._load(session, race_id)
            if race.mode == RaceMode.FREE.value:
                race.status = (RaceStatus.ABORTED if aborted else RaceStatus.FINISHED).value
                race.finished_at = utc_now()
                return
            if heats.uses_heats(session, race.id):
                for result in results:
                    heats.accumulate_heat_time(session, race.id, result.lane, result.total_time_ns)
                if heats.finish_heat(
                    session, race, aborted=aborted, lane_count=self._planning_lanes(race)
                ):
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

    def restart_preview(self, race_id: RaceId) -> RestartPreview:
        """Describe a new race copied from a finished or aborted one. Nothing is stored."""
        with self._database.session() as session:
            source = self._load(session, race_id)
            self._require_restartable(source)
            names = list(session.scalars(select(Race.name)))
            previous = list(
                session.scalars(
                    select(RaceParticipant)
                    .where(RaceParticipant.race_id == source.id)
                    .order_by(RaceParticipant.id)
                )
            )
            planned = heats.uses_heats(session, source.id)
            track = self._tracks.get_track(TrackId(source.track_id)) if source.track_id else None
            lane_count = 0 if track is None else track.lane_count
            seats = self._restart_seats(previous, lane_count=lane_count, check_lane=not planned)
            return RestartPreview(
                source_id=RaceId(source.id),
                source_name=source.name,
                suggested_name=suggest_distinct_name(source.name, names),
                track_id=None if source.track_id is None else TrackId(source.track_id),
                track_name=track.name if track else "-",
                lane_count=lane_count,
                mode=RaceMode(source.mode),
                laps=source.target_laps,
                timing_provider=source.timing_provider,
                duration_minutes=source.duration_minutes,
                active_lanes=effective_lane_count(source.active_lanes, lane_count),
                repetitions=source.repetitions,
                uses_heats=planned,
                seats=tuple(seats),
            )

    def restart_aborted(
        self,
        race_id: RaceId,
        *,
        name: str | None = None,
        seats: Sequence[tuple[DriverId, VehicleId, int | None]] | None = None,
    ) -> RaceInfo:
        """Open a new race from a finished or aborted one and leave the source in the history.

        Mode, track, timing layout, participants, lanes, repetitions and the lap or time
        target are copied. The name is never the source name. Laps, measurements, results,
        progress and exclusion marks are not. A heat race gets a fresh plan from the same
        drivers instead of the heats that were already driven.
        """
        with self._database.session() as session:
            source = self._load(session, race_id)
            self._require_restartable(source)
            if source.track_id is None:
                raise ValidationError("error.race.no_track")
            self._require_active_track(TrackId(source.track_id))
            taken = list(session.scalars(select(Race.name)))
            chosen = self._validate_name(
                suggest_distinct_name(source.name, taken) if name is None else name
            )
            if chosen == source.name.strip():
                raise ValidationError("error.race.restart_name")
            planned = heats.uses_heats(session, source.id)
            track = self._tracks.get_track(TrackId(source.track_id))
            lane_count = 0 if track is None else track.lane_count
            copied = self._restart_choices(session, source.id, seats)
            self._require_restart_seats(copied, lane_count=lane_count, check_lane=not planned)
            race = Race(
                name=chosen,
                track_id=source.track_id,
                track_layout_id=source.track_layout_id,
                target_laps=source.target_laps,
                mode=source.mode,
                timing_provider=source.timing_provider,
                duration_minutes=source.duration_minutes,
                active_lanes=source.active_lanes,
                repetitions=source.repetitions,
                status=RaceStatus.CREATED.value,
            )
            session.add(race)
            session.flush()
            for driver_id, vehicle_id, lane in copied:
                session.add(
                    RaceParticipant(
                        race_id=race.id,
                        driver_id=int(driver_id),
                        vehicle_id=int(vehicle_id),
                        lane=None if planned else lane,
                    )
                )
            session.flush()
            if planned:
                heats.replace_open_plan(session, race, self._planning_lanes(race))
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

    def _require_restartable(self, race: Race) -> None:
        if RaceStatus(race.status) not in _RESTARTABLE:
            raise ValidationError("error.race.not_restartable")

    def _restart_choices(
        self,
        session: Session,
        source_id: int,
        seats: Sequence[tuple[DriverId, VehicleId, int | None]] | None,
    ) -> list[tuple[DriverId, VehicleId, int | None]]:
        if seats is not None:
            return [(DriverId(driver), VehicleId(vehicle), lane) for driver, vehicle, lane in seats]
        previous = session.scalars(
            select(RaceParticipant)
            .where(RaceParticipant.race_id == source_id)
            .order_by(RaceParticipant.id)
        )
        return [
            (
                DriverId(row.driver_id),
                VehicleId(row.vehicle_id or 0),
                row.lane,
            )
            for row in previous
        ]

    def _restart_seats(
        self,
        previous: Sequence[RaceParticipant],
        *,
        lane_count: int,
        check_lane: bool,
    ) -> list[RestartSeat]:
        seats: list[RestartSeat] = []
        seen_lanes: set[int] = set()
        seen_drivers: set[int] = set()
        for row in previous:
            driver_id = DriverId(row.driver_id)
            vehicle_id = None if row.vehicle_id is None else VehicleId(row.vehicle_id)
            issue = self._assignment_issue(
                driver_id,
                vehicle_id,
                row.lane,
                lane_count=lane_count,
                check_lane=check_lane,
                seen_lanes=seen_lanes,
                seen_drivers=seen_drivers,
            )
            driver = self._drivers.get_driver(driver_id)
            vehicle = None if vehicle_id is None else self._vehicles.get_vehicle(vehicle_id)
            if issue is None:
                key, driver_name, vehicle_name, lane_value, maximum = None, "", "", None, None
            else:
                key, driver_name, vehicle_name, lane_value, maximum = issue
            seats.append(
                RestartSeat(
                    driver_id=driver_id,
                    driver_label=driver.label if driver else f"#{row.driver_id}",
                    vehicle_id=vehicle_id,
                    vehicle_label=vehicle.label if vehicle else "-",
                    lane=row.lane,
                    issue_key=key,
                    issue_driver=driver_name,
                    issue_vehicle=vehicle_name,
                    issue_lane=lane_value,
                    issue_maximum=maximum,
                )
            )
        return seats

    def _require_restart_seats(
        self,
        seats: Sequence[tuple[DriverId, VehicleId, int | None]],
        *,
        lane_count: int,
        check_lane: bool,
    ) -> None:
        seen_lanes: set[int] = set()
        seen_drivers: set[int] = set()
        for driver_id, vehicle_id, lane in seats:
            issue = self._assignment_issue(
                driver_id,
                vehicle_id,
                lane,
                lane_count=lane_count,
                check_lane=check_lane,
                seen_lanes=seen_lanes,
                seen_drivers=seen_drivers,
            )
            if issue is None:
                continue
            key, driver_name, vehicle_name, lane_value, maximum = issue
            raise ValidationError(
                key,
                driver=driver_name,
                vehicle=vehicle_name,
                lane=0 if lane_value is None else lane_value,
                maximum=0 if maximum is None else maximum,
            )

    def _assignment_issue(
        self,
        driver_id: DriverId,
        vehicle_id: VehicleId | None,
        lane: int | None,
        *,
        lane_count: int,
        check_lane: bool,
        seen_lanes: set[int],
        seen_drivers: set[int],
    ) -> tuple[str, str, str, int | None, int | None] | None:
        """Return a translation key and the values it fills, or nothing when the seat counts."""
        driver = self._drivers.get_driver(driver_id)
        if driver is None:
            return ("error.race.driver_unknown", "", "", None, None)
        if not driver.is_active:
            return ("error.race.driver_inactive", driver.label, "", None, None)
        if driver_id in seen_drivers:
            return ("error.race.driver_duplicate", driver.label, "", None, None)
        seen_drivers.add(int(driver_id))
        if vehicle_id is None:
            return ("error.race.vehicle_unknown", "", "", None, None)
        vehicle = self._vehicles.get_vehicle(vehicle_id)
        if vehicle is None:
            return ("error.race.vehicle_unknown", "", "", None, None)
        if not vehicle.is_active:
            return ("error.race.vehicle_inactive", "", vehicle.label, None, None)
        if not check_lane:
            return None
        if lane is None:
            return ("error.race.lane_missing", driver.label, "", None, None)
        if not 1 <= lane <= lane_count:
            return ("error.race.lane_invalid", "", "", lane, lane_count)
        if lane in seen_lanes:
            return ("error.race.lane_taken", "", "", lane, None)
        seen_lanes.add(lane)
        return None

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
        if RaceMode(race.mode) is RaceMode.FREE:
            return
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
        if chosen not in (RaceMode.LAPS, RaceMode.TIME_TRIAL, RaceMode.FREE):
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
    def _validate_repetitions(repetitions: int) -> None:
        if not 1 <= repetitions <= MAX_REPETITIONS:
            raise ValidationError("error.race.repetitions", maximum=MAX_REPETITIONS)

    @staticmethod
    def _validate_duration(minutes: int) -> None:
        if not 1 <= minutes <= MAX_DURATION_MINUTES:
            raise ValidationError("error.race.duration_invalid")

    def has_heat_plan(self, race_id: RaceId) -> bool:
        with self._database.session() as session:
            return heats.uses_heats(session, race_id)

    def _physical_lanes(self, race: Race) -> int:
        track = self._tracks.get_track(TrackId(race.track_id)) if race.track_id else None
        return 0 if track is None else track.lane_count

    def _planning_lanes(self, race: Race) -> int:
        return effective_lane_count(race.active_lanes, self._physical_lanes(race))

    @staticmethod
    def _store_active_lanes(race: Race, physical: int, requested: int | None) -> None:
        """Store a one-or-two choice. Any other track keeps planning on its physical lanes."""
        if physical != 2:
            if requested not in (None, physical):
                raise ValidationError("error.race.active_lanes")
            race.active_lanes = None
            return
        if requested == 1:
            race.active_lanes = 1
            return
        if requested in (None, 2):
            race.active_lanes = 2
            return
        raise ValidationError("error.race.active_lanes")

    @staticmethod
    def _keep_active_lanes(race: Race, physical: int) -> None:
        """Drop a stored choice that the current track cannot express. Empty stays empty."""
        if physical == 2 and race.active_lanes in (1, 2):
            return
        race.active_lanes = None

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
    def _clear_participant_laps(session: Session, participant_id: int) -> None:
        """Drop one lane's stored laps. A free-session change must not keep the old stint."""
        lap_ids = list(session.scalars(select(Lap.id).where(Lap.participant_id == participant_id)))
        if not lap_ids:
            return
        session.execute(delete(Sector).where(Sector.lap_id.in_(lap_ids)))
        session.execute(delete(TimeMeasurement).where(TimeMeasurement.lap_id.in_(lap_ids)))
        session.execute(delete(Lap).where(Lap.id.in_(lap_ids)))

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
            excluded=bool(row.statistics_excluded),
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
            active_lanes=effective_lane_count(race.active_lanes, track.lane_count if track else 0),
            repetitions=race.repetitions,
        )
