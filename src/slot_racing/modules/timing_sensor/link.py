"""The one open sensor reader.

A race and the diagnostic page acquire this link. The underlying transport is
opened on the first acquire and closed on the last release. Simulated lines
are accepted only while the link is in simulation mode. USB uses the same
intake. A dropped connection or a new controller session stops race events
until the next race is bound; the clock is not carried across that break.
"""

from __future__ import annotations

from collections import deque
from collections.abc import Mapping
from dataclasses import replace
from enum import StrEnum

from slot_racing.core.events import SensorTriggered
from slot_racing.modules.timing_sensor.clock_map import DeviceClockMapping, SyncStatus
from slot_racing.modules.timing_sensor.clock_sync import ClockSync, continuous_mapping
from slot_racing.modules.timing_sensor.configuration import SensorAssignment
from slot_racing.modules.timing_sensor.intake import (
    IntakeState,
    SensorObservation,
    consume_line,
    with_mapping,
    without_mapping,
)
from slot_racing.modules.timing_sensor.protocol import HelloFrame
from slot_racing.modules.timing_sensor.transport import (
    SensorTransport,
    SerialSensorTransport,
    SimulatedSensorTransport,
)

SENSOR_SOURCE_ID = "sensor"
_LOG_LIMIT = 400


class TransportMode(StrEnum):
    """How lines arrive. USB and the simulator share this link."""

    SIMULATION = "simulation"
    SERIAL = "serial"


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
        self._estimator = ClockSync()
        self._identity: HelloFrame | None = None
        self._last_device_ns: int | None = None
        self._last_host_ns: int | None = None
        self._mute_race = False
        self._seen_fault: str | None = None
        self._mapping_key: tuple[int, int, int] | None = None

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

    @property
    def identity(self) -> HelloFrame | None:
        return self._identity

    @property
    def reported_sensor_count(self) -> int | None:
        identity = self._identity
        if identity is None:
            return None
        return len(identity.sensors)

    @property
    def sync_quality(self) -> tuple[int, int, int, int, bool]:
        """Samples, uncertainty, numerator, denominator, and whether the window was accepted."""
        quality = self._estimator.quality
        return (
            quality.samples,
            quality.uncertainty_ns,
            quality.numerator,
            quality.denominator,
            quality.accepted,
        )

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

    def use_transport(self, transport: SensorTransport, mode: TransportMode) -> None:
        """Replace the reader. Refused while a race holds the snapshot.

        The new transport is opened before the old one closes, so a failure to
        open leaves the previous reader in place. The clock and the sequence
        start over: a simulator mapping is not a device clock.
        """
        if self._race_bound:
            raise RuntimeError("sensor reader is held by a race")
        if not isinstance(mode, TransportMode):
            raise TypeError("mode must be a TransportMode")
        if not isinstance(transport, SensorTransport):
            raise TypeError("transport must be a SensorTransport")
        if self._holders > 0:
            transport.open()
        previous = self._transport
        self._transport = transport
        self._mode = mode
        if self._holders > 0:
            previous.close()
        self._reset_session()

    def set_time_mapping(self, mapping: DeviceClockMapping, *, resume: bool = True) -> None:
        """Replace the clock assignment. Arrival time is not an input.

        ``resume`` is for an explicit mapping. An automatic exchange keeps a
        race muted after a disconnect or a controller restart.
        """
        self._state = with_mapping(self._state, mapping)
        self._mapping_key = (mapping.offset_ns, mapping.numerator, mapping.denominator)
        if resume:
            self._mute_race = False

    def lose_time_mapping(self) -> None:
        """Lock capture. A mapping that was valid is shown as lost, not as a continued clock."""
        self._state = without_mapping(self._state)
        self._mapping_key = None

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
        self._mute_race = False
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

    def request_sync(self, rounds: int = 5) -> None:
        """Ask the serial reader for a few clock exchanges."""
        transport = self._transport
        if not isinstance(transport, SerialSensorTransport):
            raise RuntimeError("clock sync requires the serial reader")
        transport.request_sync(rounds)

    def poll(self) -> None:
        """Read new lines. Race events stay queued until :meth:`take_events`."""
        self._note_transport()
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
            self._remember(observation)
            self._observations.append(observation)
            if event is None or not self._race_bound or self._mute_race:
                continue
            self._pending.append(event)
        self._apply_sync()

    def take_events(self) -> tuple[SensorTriggered, ...]:
        """Race events parsed since the last take. The diagnostic page does not call this."""
        found = tuple(self._pending)
        self._pending.clear()
        return found

    def _remember(self, observation: SensorObservation) -> None:
        if observation.kind in {"hello", "restart"}:
            identity = _hello_from_observation(observation)
            if identity is not None:
                self._identity = identity
                self._maybe_request_sync()
            elif observation.kind == "hello":
                self._maybe_request_sync()
        if observation.kind in {"restart", "time_fault"}:
            self._break_race(observation.kind)
        if (
            observation.kind in {"accepted", "gap"}
            and observation.device_time_ns is not None
            and observation.host_time_ns is not None
            and not self._mute_race
        ):
            self._last_device_ns = observation.device_time_ns
            self._last_host_ns = observation.host_time_ns

    def _maybe_request_sync(self) -> None:
        if self._mode is not TransportMode.SERIAL:
            return
        transport = self._transport
        if isinstance(transport, SerialSensorTransport) and transport.connected:
            transport.request_sync(5)

    def _apply_sync(self) -> None:
        transport = self._transport
        if not isinstance(transport, SerialSensorTransport):
            return
        rewound = False
        for sample in transport.take_sync_samples():
            self._estimator.add(sample)
            if self._estimator.rewound:
                rewound = True
                self._estimator.rewound = False
        if rewound:
            self._break_race("clock")
            return
        mapping = self._estimator.mapping
        if mapping is None:
            return
        installed = mapping
        if self._last_device_ns is not None and self._last_host_ns is not None:
            anchored = continuous_mapping(
                mapping,
                last_device_ns=self._last_device_ns,
                last_host_ns=self._last_host_ns,
            )
            if anchored is None:
                return
            installed = anchored
        key = (installed.offset_ns, installed.numerator, installed.denominator)
        if key == self._mapping_key:
            return
        self.set_time_mapping(installed, resume=False)
        quality = self._estimator.quality
        self._observations.append(
            SensorObservation(
                kind="sync",
                raw="",
                detail=f"{quality.samples} {quality.uncertainty_ns}",
            )
        )

    def _note_transport(self) -> None:
        transport = self._transport
        warnings = getattr(transport, "take_warnings", None)
        if callable(warnings):
            for code in warnings():
                if isinstance(code, str):
                    self._observations.append(SensorObservation(kind=code, raw="", detail=code))
        fault = getattr(transport, "fault", None)
        if not isinstance(fault, str) or fault == self._seen_fault:
            return
        self._seen_fault = fault
        kind = "handshake" if fault.startswith("handshake") or fault == "protocol" else "disconnect"
        self._observations.append(SensorObservation(kind=kind, raw="", detail=fault))
        if kind == "disconnect":
            self._break_race(fault)

    def _break_race(self, detail: str) -> None:
        """Stop a running race from treating the next edges as the same measurement."""
        del detail
        self._mute_race = True
        self._last_device_ns = None
        self._last_host_ns = None
        self._estimator.clear()
        self._mapping_key = None
        self._state = without_mapping(self._state)
        self._state = replace(self._state, session_id=None, last_sequence=None)
        self._pending.clear()

    def _reset_session(self) -> None:
        self._state = IntakeState()
        self._estimator.clear()
        self._identity = None
        self._last_device_ns = None
        self._last_host_ns = None
        self._mute_race = False
        self._seen_fault = None
        self._mapping_key = None
        self._pending.clear()


def _hello_from_observation(observation: SensorObservation) -> HelloFrame | None:
    if observation.session_id is None:
        return None
    parts = observation.detail.split()
    if len(parts) < 3:
        return None
    device, firmware, *sensors = parts
    try:
        return HelloFrame(observation.session_id, device, firmware, "ns", tuple(sensors))
    except (TypeError, ValueError):
        return None
