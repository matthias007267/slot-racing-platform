"""Stores race results from the standardized race events.

The recorder is a plain event subscriber. It does not know where the events come from, and the
engine does not know the recorder exists.
"""

from __future__ import annotations

import logging
from collections.abc import Callable

from slot_racing.core.events import (
    EventDispatcher,
    LapCompleted,
    RaceFinished,
    RacePaused,
    RaceResumed,
    RaceStarted,
    SectorCompleted,
    Subscription,
)
from slot_racing.modules.races.service import RaceService

logger = logging.getLogger(__name__)


class RaceRecorder:
    """Persists lifecycle changes, laps and sectors, and the final standings.

    Storage failures must not interrupt a running race: they are logged and collected in
    :attr:`errors`, which the UI shows as a warning.
    """

    def __init__(self, service: RaceService, bus: EventDispatcher) -> None:
        self._service = service
        self.errors: list[str] = []
        self._sectors: dict[tuple[int, int, int], dict[int, int]] = {}
        self._subscriptions: list[Subscription] = [
            bus.subscribe(RaceStarted, self._on_started),
            bus.subscribe(RacePaused, self._on_paused),
            bus.subscribe(RaceResumed, self._on_resumed),
            bus.subscribe(SectorCompleted, self._on_sector),
            bus.subscribe(LapCompleted, self._on_lap),
            bus.subscribe(RaceFinished, self._on_finished),
        ]

    def close(self) -> None:
        for subscription in self._subscriptions:
            subscription.cancel()
        self._subscriptions.clear()

    def _on_started(self, event: RaceStarted) -> None:
        self._guard("start", lambda: self._service.record_started(event.race_id))

    def _on_paused(self, event: RacePaused) -> None:
        self._guard("pause", lambda: self._service.record_paused(event.race_id))

    def _on_resumed(self, event: RaceResumed) -> None:
        self._guard("resume", lambda: self._service.record_resumed(event.race_id))

    def _on_sector(self, event: SectorCompleted) -> None:
        key = (event.race_id, event.lane, event.lap_number)
        self._sectors.setdefault(key, {})[event.sector_number] = event.sector_time_ns

    def _on_lap(self, event: LapCompleted) -> None:
        sectors = self._sectors.pop((event.race_id, event.lane, event.lap_number), {})
        self._guard(
            "lap",
            lambda: self._service.record_lap(
                event.race_id,
                event.lane,
                event.lap_number,
                event.lap_time_ns,
                event.race_time_ns,
                sectors,
            ),
        )

    def _on_finished(self, event: RaceFinished) -> None:
        self._sectors = {key: v for key, v in self._sectors.items() if key[0] != event.race_id}
        self._guard(
            "result",
            lambda: self._service.record_finished(
                event.race_id, event.results, aborted=event.aborted
            ),
        )

    def _guard(self, what: str, action: Callable[[], None]) -> None:
        try:
            action()
        except Exception as error:
            logger.exception("Could not store race %s", what)
            self.errors.append(f"{what}: {type(error).__name__}: {error}")
