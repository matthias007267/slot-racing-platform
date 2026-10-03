"""Read-only access to stored time-trial measurements.

Statistics and other modules use :class:`TimeMeasurementCatalog` instead of importing the race
service. This adapter does not score, store, or replace a best time.
"""

from __future__ import annotations

from slot_racing.core.catalog import TimeMeasurementCatalog, TimeMeasurementView
from slot_racing.core.domain import TrackId
from slot_racing.modules.races.service import RaceService


class StoredTimeMeasurements(TimeMeasurementCatalog):
    """Delegates to the measurements the time-trial mode already stores."""

    def __init__(self, service: RaceService) -> None:
        self._service = service

    def list_for_track(self, track_id: TrackId) -> list[TimeMeasurementView]:
        return [
            TimeMeasurementView(
                id=row.id,
                track_id=row.track_id,
                lane=row.lane,
                driver_label=row.driver_label,
                vehicle_label=row.vehicle_label,
                time_ns=row.time_ns,
                recorded_at=row.recorded_at,
            )
            for row in self._service.list_time_measurements(track_id=track_id)
        ]
