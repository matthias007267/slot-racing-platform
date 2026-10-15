"""The one open sensor reader.

A race and the diagnostic page acquire this link. The underlying transport is
opened on the first acquire and closed on the last release. Simulated lines
are accepted only while the link is in simulation mode.
"""

from __future__ import annotations

from collections import deque
from collections.abc import Mapping
from enum import StrEnum

from slot_racing.core.events import SensorTriggered
from slot_racing.modules.timing_sensor.clock_map import DeviceClockMapping, SyncStatus
from slot_racing.modules.timing_sensor.configuration import SensorAssignment
from slot_racing.modules.timing_sensor.intake import (
    IntakeState,
    SensorObservation,
    consume_line,
    with_mapping,
    without_mapping,
)
from slot_racing.modules.timing_sensor.transport import SensorTransport, SimulatedSensorTransport

SENSOR_SOURCE_ID = "sensor"
_LOG_LIMIT = 400


class TransportMode(StrEnum):
    """How lines arrive. Only the simulator exists until a device transport is added."""

    SIMULATION = "simulation"


class SensorLink:
    """Shared intake. ``poll`` reads lines once; the race takes the queued events."""

    def __init__(
        self,
        transport: SensorTransport | None = None,
        *,
        mode: TransportMode = TransportMode.SIMULATION,
        source_id: str = SENSOR_SOURCE_ID,
    ) -> None:
        if not isinstance(mode, TransportMode):
            raise TypeError("mode must be a TransportMode")
        self._transport = transport if transport is not None else SimulatedSensorTransport()
        self._mode = mode
        self._source_id = source_id
        self._holders = 0
        self._state = IntakeState()
        self._assignments: dict[str, SensorAssignment] = {}
        self._logical: dict[str, str] = {}
        self._race_lanes: frozenset[int] = frozenset()
        self._race_bound = False
        self._observations: deque[SensorObservation] = deque(maxlen=_LOG_LIMIT)
        self._pending: deque[SensorTriggered] = deque()

    @property
    def mode(self) -> TransportMode:
        return self._mode

    @property
    def connected(self) -> bool:
        return self._transport.connected

    @property
    def sync(self) -> SyncStatus:
        return self._state.sync

    @property
    def race_bound(self) -> bool:
        return self._race_bound

    @property
    def observations(self) -> tuple[SensorObservation, ...]:
        return tuple(self._observations)

    def acquire(self) -> None:
        """Hold the transport open. A second holder does not open it again."""
        if self._holders == 0:
            self._transport.open()
        self._holders += 1

    def release(self) -> None:
        """Drop one hold. The transport closes when the last holder is gone."""
        if self._holders == 0:
            return
        self._holders -= 1
        if self._holders == 0:
            self._transport.close()

    def force_close(self) -> None:
        """Close regardless of holders. Used when the plugin stops."""
        self._holders = 0
        self._race_bound = False
        self._pending.clear()
        self._transport.close()

    def set_time_mapping(self, mapping: DeviceClockMapping) -> None:
        """Replace the clock assignment. Arrival time is not an input."""
        self._state = with_mapping(self._state, mapping)

    def lose_time_mapping(self) -> None:
        """Lock capture. A mapping that was valid is shown as lost, not as a continued clock."""
        self._state = without_mapping(self._state)

    def bind_view(
        self,
        assignments: tuple[SensorAssignment, ...] | list[SensorAssignment],
        logical_ids: Mapping[str, str],
    ) -> None:
        """Assignments for the diagnostic page. A running race keeps its own snapshot."""
        if self._race_bound:
            return
        self._assignments = {item.physical_id: item for item in assignments}
        self._logical = dict(logical_ids)

    def bind_race(
        self,
        assignments: tuple[SensorAssignment, ...] | list[SensorAssignment],
        logical_ids: Mapping[str, str],
        lanes: frozenset[int],
    ) -> None:
        """Snapshot used until :meth:`unbind_race`. Events queued earlier are dropped."""
        self._assignments = {item.physical_id: item for item in assignments}
        self._logical = dict(logical_ids)
        self._race_lanes = lanes
        self._race_bound = True
        self._pending.clear()

    def unbind_race(self) -> None:
        """Stop publishing. The clock and the sequence are left as they are."""
        self._race_bound = False
        self._race_lanes = frozenset()
        self._pending.clear()

    def inject(self, line: str) -> None:
        """Queue one simulator line. Refused outside simulation mode and while closed."""
        if self._mode is not TransportMode.SIMULATION:
            raise RuntimeError("simulated frames are only accepted in simulation mode")
        transport = self._transport
        if not isinstance(transport, SimulatedSensorTransport):
            raise RuntimeError("simulation mode requires the simulator transport")
        transport.inject(line)

    def poll(self) -> None:
        """Read new lines. Race events stay queued until :meth:`take_events`."""
        if not self._transport.connected:
            return
        lanes = self._race_lanes if self._race_bound else None
        for line in self._transport.read_available():
            state, observation, event = consume_line(
                self._state,
                line,
                assignments=self._assignments,
                logical_ids=self._logical,
                active_lanes=lanes,
                source_id=self._source_id,
            )
            self._state = state
            if observation is None:
                continue
            self._observations.append(observation)
            if event is not None and self._race_bound:
                self._pending.append(event)

    def take_events(self) -> tuple[SensorTriggered, ...]:
        """Race events parsed since the last take. The diagnostic page does not call this."""
        found = tuple(self._pending)
        self._pending.clear()
        return found
