"""Lane statistics for one track.

The track decides which lanes exist. This module keeps no lane-count setting of its own and
does not fill the view up to the maximum a new track may have. A best time is the fastest
stored measurement on that lane. It is calculated here and never written back.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime

from slot_racing.core.catalog import TimeMeasurementView, TrackInfo


@dataclass(frozen=True, slots=True)
class LaneStatistic:
    """Best stored time on one lane of one track. Empty when that lane has no measurement."""

    lane: int
    label: str
    driver_label: str | None
    vehicle_label: str | None
    best_time_ns: int | None


def statistics_for_track(
    track: TrackInfo, measurements: Sequence[TimeMeasurementView]
) -> tuple[LaneStatistic, ...]:
    """One line per lane of ``track``, from lane 1 through ``track.lane_count``.

    Measurements that belong to another track, or to a lane this track does not have, are
    ignored. Lane 1 and lane 2 stay separate even when the measured time is the same.
    """
    lanes = _lanes_of(track.lane_count)
    grouped: dict[int, list[TimeMeasurementView]] = {lane: [] for lane in lanes}
    for row in measurements:
        if row.track_id != track.id:
            continue
        bucket = grouped.get(row.lane)
        if bucket is not None:
            bucket.append(row)
    return tuple(_line(lane, grouped[lane]) for lane in lanes)


def format_lap_seconds(duration_ns: int | None) -> str:
    """One lap as German seconds, for example ``8,421 s``. A missing time stays ``-``."""
    if duration_ns is None:
        return "-"
    millis = duration_ns // 1_000_000
    seconds, fraction = divmod(millis, 1000)
    return f"{seconds},{fraction:03d} s"


def lane_statistic_cells(line: LaneStatistic) -> tuple[str, str, str, str]:
    """Bahn, driver, vehicle and best time, as the statistics table shows them."""
    return (
        line.label,
        line.driver_label or "-",
        line.vehicle_label or "-",
        format_lap_seconds(line.best_time_ns),
    )


def _lanes_of(lane_count: int) -> tuple[int, ...]:
    """The lanes a track actually has. The count comes from the track, not from a fixed list."""
    if lane_count < 1:
        return ()
    return tuple(range(1, lane_count + 1))


def _line(lane: int, rows: Sequence[TimeMeasurementView]) -> LaneStatistic:
    best = _best(rows)
    return LaneStatistic(
        lane=lane,
        label=f"Bahn {lane}",
        driver_label=None if best is None else best.driver_label,
        vehicle_label=None if best is None else best.vehicle_label,
        best_time_ns=None if best is None else best.time_ns,
    )


def _best(rows: Sequence[TimeMeasurementView]) -> TimeMeasurementView | None:
    """Fastest time on this lane. An equal time keeps the earlier measurement."""
    if not rows:
        return None
    return min(rows, key=_order)


def _order(row: TimeMeasurementView) -> tuple[int, tuple[int, float], int]:
    return (row.time_ns, _stamp(row.recorded_at), row.id)


def _stamp(moment: datetime | None) -> tuple[int, float]:
    if moment is None:
        return (0, 0.0)
    return (1, moment.timestamp())
