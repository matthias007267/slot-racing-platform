"""Runs one race: wires the engine to a timing source and exposes live standings.

Nothing here knows a UI toolkit. A host (the live view) calls :meth:`RaceRunner.tick` regularly,
which lets host-driven timing sources deliver their events; everything else is event driven.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Sequence
from dataclasses import dataclass

from slot_racing.core.clock import Clock
from slot_racing.core.diagnostics import record
from slot_racing.core.domain import (
    Participant,
    ParticipantResult,
    RaceId,
    RaceMode,
    RaceStatus,
    TimingSetup,
    TrackId,
    default_timing_setup,
)
from slot_racing.core.errors import ValidationError
from slot_racing.core.events import EventDispatcher, LapCompleted, RaceFinished, Subscription
from slot_racing.core.timing import TimingSessionSpec, TimingSetupService
from slot_racing.core.timing_registry import TimingProviderRegistry
from slot_racing.modules.races.engine import RaceConfig, RaceEngine
from slot_racing.modules.races.service import RaceService
from slot_racing.modules.races.types import ParticipantInfo, RaceInfo

logger = logging.getLogger(__name__)

# The timing source of a time trial still needs a positive lap count so a simulation can keep
# producing laps. The race engine ignores that number and ends the session only when it is stopped.
TIME_TRIAL_TIMING_LAPS = 100


@dataclass(frozen=True, slots=True)
class LiveRow:
    position: int
    lane: int
    driver_label: str
    vehicle_label: str
    start_number: int | str | None
    current_lap: int
    laps_completed: int
    last_lap_ns: int | None
    total_time_ns: int | None
    best_lap_ns: int | None
    finished: bool
    lap_times_ns: tuple[int, ...]
    """Completed lap times in lap order, taken from ``LapCompleted`` events."""


@dataclass(frozen=True, slots=True)
class RaceSnapshot:
    race_id: RaceId
    name: str
    track_name: str
    timing_provider: str
    status: RaceStatus
    aborted: bool
    laps: int
    elapsed_ns: int
    rows: tuple[LiveRow, ...]
    source_errors: tuple[str, ...]


class RaceRunner:
    def __init__(
        self,
        race: RaceInfo,
        engine: RaceEngine,
        bus: EventDispatcher,
        storage_errors: Callable[[], Sequence[str]] = lambda: (),
    ) -> None:
        self.race = race
        self._engine = engine
        self._storage_errors = storage_errors
        self._participants: dict[int, ParticipantInfo] = {}
        for participant in race.participants:
            if participant.lane is not None:
                self._participants[participant.lane] = participant
        self._lap_times_ns: dict[int, list[int]] = {}
        self._aborted = False
        self._subscriptions: list[Subscription] = [
            bus.subscribe(LapCompleted, self._on_lap),
            bus.subscribe(RaceFinished, self._on_finished),
        ]

    @property
    def status(self) -> RaceStatus:
        return self._engine.status

    @property
    def is_active(self) -> bool:
        return self._engine.status in (RaceStatus.RUNNING, RaceStatus.PAUSED)

    @property
    def is_finished(self) -> bool:
        return self._engine.status is RaceStatus.FINISHED

    @property
    def storage_errors(self) -> Sequence[str]:
        return self._storage_errors()

    def start(self) -> None:
        self._engine.start()

    def pause(self) -> None:
        self._engine.pause()

    def resume(self) -> None:
        self._engine.resume()

    def stop(self) -> None:
        """End the race early. The standings so far are final."""
        self._engine.stop()

    def tick(self) -> None:
        self._engine.poll_sources()
        self._stop_when_duration_elapsed()

    def _stop_when_duration_elapsed(self) -> None:
        minutes = self.race.duration_minutes
        if minutes is None or not self.is_active:
            return
        if self._engine.elapsed_ns() >= minutes * 60 * 1_000_000_000:
            self.stop()

    def close(self) -> None:
        for subscription in self._subscriptions:
            subscription.cancel()
        self._subscriptions.clear()
        self._engine.close()

    def snapshot(self) -> RaceSnapshot:
        results = self._engine.results()
        rows = tuple(self._row(result) for result in results)
        errors = [f"{source}: {message}" for source, message in self._engine.source_errors.items()]
        errors.extend(self.storage_errors)
        return RaceSnapshot(
            race_id=self.race.id,
            name=self.race.name,
            track_name=self.race.track_name,
            timing_provider=self.race.timing_provider,
            status=self._engine.status,
            aborted=self._aborted,
            laps=self.race.laps,
            elapsed_ns=self._engine.elapsed_ns(),
            rows=rows,
            source_errors=tuple(errors),
        )

    def _row(self, result: ParticipantResult) -> LiveRow:
        info: ParticipantInfo = self._participants[result.lane]
        lap_times = tuple(self._lap_times_ns.get(result.lane, ()))
        if self.race.mode is RaceMode.LAPS:
            current_lap = min(result.laps_completed + 1, self.race.laps)
        elif self.is_active or result.laps_completed == 0:
            current_lap = result.laps_completed + 1
        else:
            current_lap = result.laps_completed
        return LiveRow(
            position=result.position,
            lane=result.lane,
            driver_label=info.driver_label,
            vehicle_label=info.vehicle_label,
            start_number=info.start_number,
            current_lap=current_lap,
            laps_completed=result.laps_completed,
            last_lap_ns=lap_times[-1] if lap_times else None,
            total_time_ns=result.total_time_ns,
            best_lap_ns=result.best_lap_ns,
            finished=result.finished,
            lap_times_ns=lap_times,
        )

    def _on_lap(self, event: LapCompleted) -> None:
        if event.race_id == self.race.id:
            self._lap_times_ns.setdefault(event.lane, []).append(event.lap_time_ns)

    def _on_finished(self, event: RaceFinished) -> None:
        if event.race_id == self.race.id:
            self._aborted = event.aborted


class RaceController:
    """Starts races and keeps track of the one that is currently running."""

    def __init__(
        self,
        service: RaceService,
        bus: EventDispatcher,
        clock: Clock,
        providers: TimingProviderRegistry,
        storage_errors: Callable[[], Sequence[str]] = lambda: (),
        setups: Callable[[], TimingSetupService | None] = lambda: None,
    ) -> None:
        self._service = service
        self._bus = bus
        self._clock = clock
        self._providers = providers
        self._storage_errors = storage_errors
        self._setups = setups
        self.active: RaceRunner | None = None

    @property
    def events(self) -> EventDispatcher:
        """Bus the running race publishes on. The live view redraws from these events."""
        return self._bus

    def start_race(self, race_id: RaceId) -> RaceRunner:
        """Validate the race, resolve its timing provider, wire a fresh timing source and engine,
        and start. The result is also available as :attr:`active`. Raises
        :class:`ValidationError` (provider problems are :class:`TimingProviderError`)."""
        if self.active is not None and self.active.is_active:
            raise ValidationError("error.race.already_running")
        runner = self._assemble(race_id)
        record("RACE_OPEN", module="races", page="races", result="open", race_id=int(race_id))
        self._start_runner(runner)
        if self.active is not None:
            self.active.close()
        self.active = runner
        return runner

    def prepare_race(self, race_id: RaceId) -> RaceRunner:
        """Wire the race and leave the clock stopped.

        The live view uses this while the start lights are on. :meth:`start_prepared`
        is the same start the immediate path uses, so the engine still decides
        when the clock and the first lap begin. The timing provider stays stopped
        until then.
        """
        if self.active is not None and not self.active.is_finished:
            raise ValidationError("error.race.already_running")
        runner = self._assemble(race_id)
        record("RACE_OPEN", module="races", page="races", result="prepared", race_id=int(race_id))
        if self.active is not None:
            self.active.close()
        self.active = runner
        return runner

    def start_prepared(self) -> None:
        """Start the race that :meth:`prepare_race` left waiting."""
        runner = self.active
        if runner is None or runner.status is not RaceStatus.CREATED:
            status = "none" if runner is None else runner.status.value
            raise ValidationError("error.race.not_startable", status=status)
        self._start_runner(runner)

    def _assemble(self, race_id: RaceId) -> RaceRunner:
        race = self._service.activate_next_heat(self._service.validate_startable(race_id).id)
        setup = self._timing_setup(race.track_id)
        source_laps = race.laps if race.mode is RaceMode.LAPS else TIME_TRIAL_TIMING_LAPS
        lanes: list[int] = []
        domain_participants: list[Participant] = []
        for participant in race.participants:
            lane = participant.lane
            if lane is None:
                continue
            lanes.append(lane)
            domain_participants.append(
                Participant(
                    driver_id=participant.driver_id,
                    lane=lane,
                    vehicle_id=participant.vehicle_id,
                )
            )
        if not lanes:
            raise ValidationError("error.race.no_participants")
        spec = TimingSessionSpec(
            setup=setup,
            lanes=tuple(lanes),
            laps=source_laps,
            race_id=race.id,
            track_id=race.track_id,
        )
        source = self._providers.create_source(race.timing_provider, spec)
        config = RaceConfig(
            race_id=race.id,
            laps=race.laps,
            participants=tuple(domain_participants),
            layout=setup.layout,
            mode=race.mode,
        )
        engine = RaceEngine(config, self._bus, self._clock, [source])
        return RaceRunner(race, engine, self._bus, self._storage_errors)

    def _start_runner(self, runner: RaceRunner) -> None:
        race_id = int(runner.race.id)
        record(
            "RACE_START_REQUEST",
            module="races",
            page="races",
            result="requested",
            race_id=race_id,
        )
        try:
            runner.start()
        except Exception:
            record(
                "RACE_START_REQUEST",
                module="races",
                page="races",
                result="failed",
                race_id=race_id,
            )
            runner.close()
            if self.active is runner:
                self.active = None
            self._service.cancel_heat_start(runner.race.id)
            self._service.abort_race(runner.race.id)
            raise
        record("RACE_STARTED", module="races", page="races", result="running", race_id=race_id)

    def shutdown(self) -> None:
        """Abort a running race so its results are stored, then release the runner."""
        runner, self.active = self.active, None
        if runner is None:
            return
        try:
            if runner.is_active:
                runner.stop()
        finally:
            runner.close()

    def release_for_restore(self) -> None:
        """Drop the in-memory race before a backup replaces the database file.

        Stored rows are left as they are. Stopping the race here would write a new result into
        the database that is about to be replaced or kept.
        """
        runner, self.active = self.active, None
        if runner is not None:
            runner.close()

    def _timing_setup(self, track_id: TrackId | None) -> TimingSetup:
        """The track's stored timing configuration, or the default layout for tracks that have
        none yet (or when no timing module stores configurations)."""
        service = self._setups()
        stored = None if service is None or track_id is None else service.get_setup(track_id)
        return stored if stored is not None else default_timing_setup()
