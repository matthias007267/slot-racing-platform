"""Read-only race view for the dashboard. No race rules live here."""

from __future__ import annotations

from slot_racing.core.catalog import RaceCatalog, RaceSummary, StandingLine
from slot_racing.core.domain import RaceStatus
from slot_racing.modules.races.runner import RaceController
from slot_racing.modules.races.service import RaceService
from slot_racing.modules.races.types import ResultRow

_STANDINGS = 3


class RaceOverview(RaceCatalog):
    """Adapts the existing race service and the active runner. It does not start or score races."""

    def __init__(self, service: RaceService, controller: RaceController) -> None:
        self._service = service
        self._controller = controller

    def count_races(self) -> int:
        return len(self._service.list_races())

    def active_summary(self) -> RaceSummary | None:
        runner = self._controller.active
        if runner is None:
            return None
        snapshot = runner.snapshot()
        status = (
            RaceStatus.ABORTED
            if snapshot.aborted and snapshot.status is RaceStatus.FINISHED
            else snapshot.status
        )
        standings = tuple(
            StandingLine(
                position=row.position,
                driver_label=row.driver_label,
                best_lap_ns=row.best_lap_ns,
                total_time_ns=row.total_time_ns,
            )
            for row in snapshot.rows[:_STANDINGS]
        )
        return RaceSummary(
            name=snapshot.name,
            status=status,
            track_name=snapshot.track_name,
            timing_provider=snapshot.timing_provider,
            laps=snapshot.laps,
            standings=standings,
        )

    def latest_summary(self) -> RaceSummary | None:
        races = self._service.list_races()
        if not races:
            return None
        race = races[0]
        return RaceSummary(
            name=race.name,
            status=race.status,
            track_name=race.track_name,
            timing_provider=race.timing_provider,
            laps=race.laps,
        )

    def latest_result(self) -> RaceSummary | None:
        for race in self._service.list_races():
            if not race.is_over:
                continue
            rows = self._service.get_results(race.id)
            return RaceSummary(
                name=race.name,
                status=race.status,
                track_name=race.track_name,
                timing_provider=race.timing_provider,
                laps=race.laps,
                standings=_standings(rows),
            )
        return None


def _standings(rows: list[ResultRow]) -> tuple[StandingLine, ...]:
    ordered = sorted(rows, key=lambda row: (row.position is None, row.position or 0))
    return tuple(
        StandingLine(
            position=row.position,
            driver_label=row.driver_label,
            best_lap_ns=row.best_lap_ns,
            total_time_ns=row.total_time_ns,
        )
        for row in ordered[:_STANDINGS]
    )
