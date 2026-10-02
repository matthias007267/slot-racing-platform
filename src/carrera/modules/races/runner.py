"""Runs one race: wires the engine to a timing source and exposes live standings.

Nothing here knows a UI toolkit. A host (the live view) calls :meth:`RaceRunner.tick` regularly,
which lets host-driven timing sources deliver their events; everything else is event driven.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Sequence
from dataclasses import dataclass

from carrera.core.clock import Clock
from carrera.core.domain import (
    Participant,
    ParticipantResult,
    RaceId,
    RaceStatus,
    TimingLayout,
)
from carrera.core.errors import ValidationError
from carrera.core.events import EventDispatcher, LapCompleted, RaceFinished, Subscription
from carrera.core.timing import TimingSessionSpec, TimingSourceFactory
from carrera.modules.races.engine import RaceConfig, RaceEngine
from carrera.modules.races.service import RaceService
from carrera.modules.races.types import ParticipantInfo, RaceInfo

logger = logging.getLogger(__name__)

DEFAULT_LAYOUT = TimingLayout.from_sensor_ids(["start_finish", "sector_1", "sector_2"])
"""Timing points used until tracks describe their own sensors."""


@dataclass(frozen=True, slots=True)
class LiveRow:
    position: int
    lane: int
    driver_label: str
    vehicle_label: str
    current_lap: int
    laps_completed: int
    last_lap_ns: int | None
    total_time_ns: int | None
    best_lap_ns: int | None
    finished: bool


@dataclass(frozen=True, slots=True)
class RaceSnapshot:
    race_id: RaceId
    name: str
    track_name: str
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
        self._participants = {p.lane: p for p in race.participants}
        self._last_lap_ns: dict[int, int] = {}
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
            status=self._engine.status,
            aborted=self._aborted,
            laps=self.race.laps,
            elapsed_ns=self._engine.elapsed_ns(),
            rows=rows,
            source_errors=tuple(errors),
        )

    def _row(self, result: ParticipantResult) -> LiveRow:
        info: ParticipantInfo = self._participants[result.lane]
        return LiveRow(
            position=result.position,
            lane=result.lane,
            driver_label=info.driver_label,
            vehicle_label=info.vehicle_label,
            current_lap=min(result.laps_completed + 1, self.race.laps),
            laps_completed=result.laps_completed,
            last_lap_ns=self._last_lap_ns.get(result.lane),
            total_time_ns=result.total_time_ns,
            best_lap_ns=result.best_lap_ns,
            finished=result.finished,
        )

    def _on_lap(self, event: LapCompleted) -> None:
        if event.race_id == self.race.id:
            self._last_lap_ns[event.lane] = event.lap_time_ns

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
        factories: Callable[[], Sequence[TimingSourceFactory]],
        preferred_source: str | None = None,
        storage_errors: Callable[[], Sequence[str]] = lambda: (),
        layout: TimingLayout = DEFAULT_LAYOUT,
    ) -> None:
        self._service = service
        self._bus = bus
        self._clock = clock
        self._factories = factories
        self._preferred_source = preferred_source
        self._storage_errors = storage_errors
        self._layout = layout
        self.active: RaceRunner | None = None

    def start_race(self, race_id: RaceId) -> RaceRunner:
        """Validate the race, wire a fresh timing source and engine, and start. The result is
        also available as :attr:`active`. Raises :class:`ValidationError`."""
        if self.active is not None and self.active.is_active:
            raise ValidationError("error.race.already_running")
        race = self._service.validate_startable(race_id)
        factory = self._select_factory()
        lanes = tuple(p.lane for p in race.participants)
        source = factory.create_source(
            TimingSessionSpec(layout=self._layout, lanes=lanes, laps=race.laps)
        )
        config = RaceConfig(
            race_id=race.id,
            laps=race.laps,
            participants=tuple(
                Participant(driver_id=p.driver_id, lane=p.lane, vehicle_id=p.vehicle_id)
                for p in race.participants
            ),
            layout=self._layout,
        )
        engine = RaceEngine(config, self._bus, self._clock, [source])
        runner = RaceRunner(race, engine, self._bus, self._storage_errors)
        try:
            runner.start()
        except Exception:
            runner.close()
            self._service.abort_race(race.id)
            raise
        if self.active is not None:
            self.active.close()
        self.active = runner
        return runner

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

    def _select_factory(self) -> TimingSourceFactory:
        factories = list(self._factories())
        if not factories:
            raise ValidationError("error.race.no_timing_source")
        if self._preferred_source is not None:
            for factory in factories:
                if factory.name == self._preferred_source:
                    return factory
            raise ValidationError("error.race.timing_source_missing", name=self._preferred_source)
        return sorted(factories, key=lambda f: f.name)[0]
