"""Sensor timing provider.

The provider id is ``sensor``. It reads the shared :class:`SensorLink` and the
track's physical sensor map. It does not know which device produced the lines.
"""

from __future__ import annotations

from slot_racing.core.catalog import TrackCatalog
from slot_racing.core.domain import TimingPositionType
from slot_racing.core.errors import ProviderConfigurationError
from slot_racing.core.timing import (
    ProviderAvailability,
    ProviderCapabilities,
    SensorSink,
    TimingSessionSpec,
    TimingSource,
    TimingSourceFactory,
)
from slot_racing.modules.timing_sensor.configuration import (
    DeviceCapacity,
    SensorAssignment,
    require_assignments,
)
from slot_racing.modules.timing_sensor.link import SENSOR_SOURCE_ID, SensorLink
from slot_racing.modules.timing_sensor.store import (
    SensorConfigurationError,
    SensorConfigurationStore,
)


class SensorTimingSource(TimingSource):
    """One race's view of the shared link. The assignment snapshot is taken at start."""

    def __init__(
        self,
        link: SensorLink,
        assignments: tuple[SensorAssignment, ...],
        logical_ids: dict[str, str],
        lanes: frozenset[int],
    ) -> None:
        self._link = link
        self._assignments = assignments
        self._logical_ids = logical_ids
        self._lanes = lanes
        self._sink: SensorSink | None = None
        self._started = False

    @property
    def source_id(self) -> str:
        return SENSOR_SOURCE_ID

    @property
    def is_running(self) -> bool:
        return self._started

    def reported_positions(self) -> frozenset[str]:
        return frozenset(item.position_id for item in self._assignments)

    def start(self, sink: SensorSink) -> None:
        if self._started:
            raise RuntimeError("sensor timing source is already running")
        self._sink = sink
        self._link.acquire()
        self._link.bind_race(self._assignments, self._logical_ids, self._lanes)
        self._started = True

    def stop(self) -> None:
        if not self._started:
            return
        self._started = False
        self._sink = None
        self._link.unbind_race()
        self._link.release()

    def poll(self) -> None:
        if not self._started or self._sink is None:
            return
        self._link.poll()
        sink = self._sink
        for event in self._link.take_events():
            sink(event)


class SensorTimingFactory(TimingSourceFactory):
    """Provider ``sensor``. Available only while a time mapping is installed."""

    def __init__(
        self,
        link: SensorLink,
        store: SensorConfigurationStore,
        tracks: TrackCatalog | None = None,
        capacity: DeviceCapacity | None = None,
    ) -> None:
        self._link = link
        self._store = store
        self._tracks = tracks
        self._capacity = capacity

    @property
    def provider_id(self) -> str:
        return "sensor"

    @property
    def capabilities(self) -> ProviderCapabilities:
        return ProviderCapabilities(supports_test_mode=False, supports_multiple_lanes=True)

    def availability(self) -> ProviderAvailability:
        sync = self._link.sync.value
        if sync == "synchronized":
            return ProviderAvailability.ok()
        if sync == "lost":
            return ProviderAvailability.unavailable("error.timing_provider.sensor_time_lost")
        return ProviderAvailability.unavailable("error.timing_provider.sensor_time_unsynchronized")

    def validate(self, spec: TimingSessionSpec) -> None:
        _checked_assignments(self._store, self._tracks, self._live_capacity(), spec)

    def create_source(self, spec: TimingSessionSpec) -> TimingSource:
        if self._link.race_bound:
            raise ProviderConfigurationError("error.timing_provider.sensor_busy")
        assignments = _checked_assignments(self._store, self._tracks, self._live_capacity(), spec)
        logical = {sensor.position_id: sensor.id for sensor in spec.setup.sensors}
        return SensorTimingSource(self._link, assignments, logical, frozenset(spec.lanes))

    def _live_capacity(self) -> DeviceCapacity | None:
        count = self._link.reported_sensor_count
        if count is None:
            return self._capacity
        return DeviceCapacity(count)


def _checked_assignments(
    store: SensorConfigurationStore,
    tracks: TrackCatalog | None,
    capacity: DeviceCapacity | None,
    spec: TimingSessionSpec,
) -> tuple[SensorAssignment, ...]:
    track_id = spec.track_id
    if track_id is None:
        raise ProviderConfigurationError("error.timing_provider.sensor_track_missing")
    try:
        assignments = store.track_assignments(int(track_id))
    except SensorConfigurationError as error:
        raise ProviderConfigurationError(
            "error.timing_provider.sensor_configuration_invalid"
        ) from error
    start_finish = next(
        position.id
        for position in spec.setup.layout.positions
        if position.type is TimingPositionType.START_FINISH
    )
    require_assignments(
        assignments,
        positions=frozenset(position.id for position in spec.setup.layout.positions),
        lane_count=_lane_count(tracks, spec),
        start_finish_id=start_finish,
        capacity=capacity,
    )
    return assignments


def _lane_count(tracks: TrackCatalog | None, spec: TimingSessionSpec) -> int:
    track_id = spec.track_id
    if tracks is not None and track_id is not None:
        track = tracks.get_track(track_id)
        if track is not None:
            return track.lane_count
    if not spec.lanes:
        return 0
    return max(spec.lanes)
