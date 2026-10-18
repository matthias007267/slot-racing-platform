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
from dataclasses import dataclass, replace
from enum import StrEnum

from slot_racing.core.events import SensorTriggered
from slot_racing.core.start_window import FalseStartNotice
from slot_racing.modules.timing_sensor.clock_map import DeviceClockMapping, SyncStatus
from slot_racing.modules.timing_sensor.clock_sync import (
    MAX_UNCERTAINTY_NS,
    ClockSync,
    continuous_mapping,
)
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
_CHANGE_LIMIT = 80


@dataclass(frozen=True, slots=True)
class RaceWatch:
    """The last race attempt this link was told about. It holds no personal names."""

    race_id: int | None = None
    mode: str = ""
    state: str = ""
    provider: str = ""
    started_at_ns: int | None = None


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
        self._mute_reason = ""
        self._seen_fault: str | None = None
        self._mapping_key: tuple[int, int, int] | None = None
        self._mapping_origin = ""
        self._changes: deque[tuple[str, str]] = deque(maxlen=_CHANGE_LIMIT)
        self._race_watch = RaceWatch()
        self._window_race_id: int | None = None
        self._window_opened_ns: int | None = None
        self._window_released_ns: int | None = None
        self._window_open = False
        self._false_starts: list[FalseStartNotice] = []
        self._held: deque[SensorTriggered] = deque()
        self._race_view: tuple[dict[str, SensorAssignment], dict[str, str]] | None = None
        self._restore_race_view = False

    @property
    def mode(self) -> TransportMode:
        return self._mode

    @property
    def connected(self) -> bool:
        return self._transport.connected

    @property
    def port_open(self) -> bool:
        """True while the reader is open, even before the protocol hello identifies it."""
        opened = getattr(self._transport, "opened", None)
        if isinstance(opened, bool):
            return opened
        return self.connected

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
    def port(self) -> str:
        found = getattr(self._transport, "port", "")
        return found if isinstance(found, str) else ""

    @property
    def mapping_origin(self) -> str:
        """``measured`` after a protocol exchange, ``manual`` after an explicit mapping."""
        return self._mapping_origin

    @property
    def race_watch(self) -> RaceWatch:
        return self._race_watch

    @property
    def changes(self) -> tuple[tuple[str, str], ...]:
        return tuple(self._changes)

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
        self._change("transport", mode.value)

    def set_time_mapping(
        self,
        mapping: DeviceClockMapping,
        *,
        resume: bool = True,
        origin: str = "manual",
    ) -> None:
        """Replace the clock assignment. Arrival time is not an input.

        ``resume`` is for an explicit mapping. An automatic exchange keeps a
        race muted after a disconnect or a controller restart. ``origin``
        ``manual`` is not a measured USB clock and is not an anchor for a later
        exchange.
        """
        if origin not in {"manual", "measured"}:
            raise ValueError("mapping origin must be manual or measured")
        self._state = with_mapping(self._state, mapping)
        self._mapping_key = (mapping.offset_ns, mapping.numerator, mapping.denominator)
        self._mapping_origin = origin
        if origin == "manual":
            self._last_device_ns = None
            self._last_host_ns = None
            self._change("mapping", "manual")
        if resume:
            self._mute_race = False
            self._mute_reason = ""

    def lose_time_mapping(self) -> None:
        """Lock capture. A mapping that was valid is shown as lost, not as a continued clock."""
        self._state = without_mapping(self._state)
        self._mapping_key = None
        self._mapping_origin = ""
        self._change("mapping", "cleared")

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

    def remember_race_view(
        self,
        assignments: tuple[SensorAssignment, ...] | list[SensorAssignment],
        logical_ids: Mapping[str, str],
    ) -> None:
        """Keep the race map until the race binds it.

        The diagnostic page repaints its own map while the lights are counting.
        That repaint must not hide the sensors this race is about to use.
        """
        self._race_view = (
            {item.physical_id: item for item in assignments},
            dict(logical_ids),
        )
        self._restore_race_view = True
        self._restore_race_view_now()

    def bind_race(
        self,
        assignments: tuple[SensorAssignment, ...] | list[SensorAssignment],
        logical_ids: Mapping[str, str],
        lanes: frozenset[int],
    ) -> None:
        """Snapshot used until :meth:`unbind_race`.

        A crossing captured at or after the release and already read stays queued.
        Anything else that was waiting is dropped.
        """
        self._assignments = {item.physical_id: item for item in assignments}
        self._logical = dict(logical_ids)
        self._race_lanes = lanes
        self._race_bound = True
        self._restore_race_view = False
        self._mute_race = False
        self._mute_reason = ""
        self._pending.clear()
        # A crossing captured at or after the release can already be in the
        # reader. It is a race event. One captured earlier was held back as a
        # false start and is not put back.
        self._pending.extend(self._held)
        self._held.clear()
        self._change("race_bound", "bound")

    def unbind_race(self) -> None:
        """Stop publishing. The clock and the sequence are left as they are."""
        self._race_bound = False
        self._race_lanes = frozenset()
        self._pending.clear()
        self._change("race_unbound", "unbound")

    def inject(self, line: str) -> None:
        """Queue one simulator line. Refused outside simulation mode and while closed."""
        if self._mode is not TransportMode.SIMULATION:
            raise RuntimeError("simulated frames are only accepted in simulation mode")
        transport = self._transport
        if not isinstance(transport, SimulatedSensorTransport):
            raise RuntimeError("simulation mode requires the simulator transport")
        transport.inject(line)

    def request_arm(self) -> None:
        """Ask a serial controller to clear its sensor locks. Other readers ignore this."""
        transport = self._transport
        if isinstance(transport, SerialSensorTransport):
            transport.request_arm()

    def open_start_window(self, race_id: int, opened_ns: int) -> None:
        """Start watching for false starts. Notices from the previous attempt are dropped."""
        if isinstance(race_id, bool) or not isinstance(race_id, int) or race_id < 1:
            raise ValueError("race_id must be a positive int")
        if isinstance(opened_ns, bool) or not isinstance(opened_ns, int) or opened_ns < 0:
            raise ValueError("opened_ns must be a non-negative int")
        self._false_starts.clear()
        self._held.clear()
        self._window_race_id = race_id
        self._window_opened_ns = opened_ns
        self._window_released_ns = None
        self._window_open = True

    def poll_start_window(self) -> tuple[FalseStartNotice, ...]:
        """Read waiting lines. Capture time, not the time this method runs, decides."""
        self.poll()
        return self.false_starts()

    def close_start_window(self, released_ns: int) -> tuple[FalseStartNotice, ...]:
        """Release the race at ``released_ns`` and classify lines already in the reader."""
        if isinstance(released_ns, bool) or not isinstance(released_ns, int) or released_ns < 0:
            return self.false_starts()
        if self._window_opened_ns is None:
            return self.false_starts()
        self._window_released_ns = released_ns
        self._window_open = False
        self.poll()
        return self.false_starts()

    def clear_false_starts(self) -> None:
        """Drop the displayed notices. An open window keeps watching."""
        self._false_starts.clear()

    def false_starts(self) -> tuple[FalseStartNotice, ...]:
        return tuple(self._false_starts)

    def request_sync(self, rounds: int = 5) -> None:
        """Ask the serial reader for a few clock exchanges."""
        transport = self._transport
        if not isinstance(transport, SerialSensorTransport):
            raise RuntimeError("clock sync requires the serial reader")
        transport.request_sync(rounds)

    def poll(self) -> None:
        """Read new lines. Race events stay queued until :meth:`take_events`.

        A line that would be a race event is not queued while this race is muted.
        The log then names that reason instead of calling the line accepted.
        """
        self._restore_race_view_now()
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
            if event is not None and self._race_bound and self._mute_race:
                observation = replace(
                    observation,
                    kind="withheld",
                    detail=self._mute_reason or "muted",
                    delivery="withheld",
                )
            elif event is not None and self._race_bound:
                observation = replace(
                    observation,
                    delivery="published",
                    race_start_ns=self._race_watch.started_at_ns,
                )
            elif observation.kind in {"accepted", "gap"}:
                observation = replace(observation, delivery="received")
            notice = None if event is None else self._false_start_notice(event)
            if notice is not None:
                observation = replace(
                    observation,
                    kind="false_start",
                    delivery="withheld",
                    detail="before_release",
                )
                self._remember_false_start(notice)
                event = None
            elif event is not None and self._hold_until_bound(event):
                self._held.append(event)
                event = None
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

    def note_race_started(self, race_id: int, mode: str, provider: str, started_at_ns: int) -> None:
        """Remember one race attempt. Names of drivers are not accepted here."""
        if isinstance(race_id, bool) or not isinstance(race_id, int):
            return
        if isinstance(started_at_ns, bool) or not isinstance(started_at_ns, int):
            return
        if started_at_ns < 0:
            return
        watched_mode = mode if isinstance(mode, str) else ""
        watched_provider = provider if isinstance(provider, str) else ""
        self._race_watch = RaceWatch(
            race_id=race_id,
            mode=watched_mode,
            state="running",
            provider=watched_provider,
            started_at_ns=started_at_ns,
        )
        self._change("race_started", f"{watched_mode} {watched_provider}".strip())

    def note_race_finished(self, race_id: int) -> None:
        watch = self._race_watch
        if isinstance(race_id, bool) or not isinstance(race_id, int) or watch.race_id != race_id:
            return
        self._race_watch = replace(watch, state="finished")
        self._change("race_finished", watch.mode)

    def note_timing_decision(
        self,
        *,
        timestamp_ns: int,
        source_id: str,
        position_id: str,
        lane: int,
        result: str,
        race_start_ns: int,
    ) -> None:
        """Attach the engine result to the crossing that was already logged."""
        if source_id != self._source_id or not result:
            return
        if isinstance(timestamp_ns, bool) or not isinstance(timestamp_ns, int):
            return
        for index in range(len(self._observations) - 1, -1, -1):
            item = self._observations[index]
            if item.engine_result or item.host_time_ns != timestamp_ns:
                continue
            if item.lane != lane or item.position_id != position_id:
                continue
            delivery = "lap" if result == "completed" else "engine"
            start = race_start_ns if race_start_ns >= 0 else item.race_start_ns
            self._observations[index] = replace(
                item,
                delivery=delivery,
                engine_result=result,
                race_start_ns=start,
            )
            return

    def _remember(self, observation: SensorObservation) -> None:
        if observation.kind in {"hello", "restart"}:
            self._change("session", observation.session_id or "")
            identity = _hello_from_observation(observation)
            if identity is not None:
                self._identity = identity
                self._maybe_request_sync()
            elif observation.kind == "hello":
                self._maybe_request_sync()
        if observation.kind in {"restart", "time_fault"}:
            self._break_race(observation.kind)
        if (
            self._mapping_origin == "measured"
            and observation.kind in {"accepted", "gap"}
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
            reason = self._estimator.add(sample)
            if reason == "uncertainty":
                detail = f"{sample.uncertainty_ns} ns, Grenze {MAX_UNCERTAINTY_NS} ns"
                self._observations.append(
                    SensorObservation(kind="sync_rejected", raw="", detail=detail)
                )
                self._change("sync_rejected", detail)
            if self._estimator.rewound:
                rewound = True
                self._estimator.rewound = False
        if rewound:
            self._break_race("clock")
            return
        mapping = self._estimator.mapping
        if mapping is None:
            return
        # A running race keeps a measured clock. A later sample may only
        # continue an event that was already published. It does not install a
        # new absolute offset, and a manual offset is never that anchor.
        race_holds_measured = (
            self._race_bound
            and not self._mute_race
            and self._state.sync is SyncStatus.SYNCHRONIZED
            and self._mapping_origin == "measured"
        )
        if race_holds_measured and (self._last_device_ns is None or self._last_host_ns is None):
            return
        installed = mapping
        if (
            self._mapping_origin == "measured"
            and self._last_device_ns is not None
            and self._last_host_ns is not None
        ):
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
        self.set_time_mapping(installed, resume=False, origin="measured")
        quality = self._estimator.quality
        detail = f"{quality.samples} {quality.uncertainty_ns}"
        self._change("sync", f"measured {detail}")
        self._observations.append(SensorObservation(kind="sync", raw="", detail=detail))

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
        self._change(kind, fault)
        if kind == "disconnect":
            self._break_race(fault)

    def _break_race(self, detail: str) -> None:
        """Stop a running race from treating the next edges as the same measurement."""
        self._mute_race = True
        self._mute_reason = detail or "muted"
        self._last_device_ns = None
        self._last_host_ns = None
        self._estimator.clear()
        self._mapping_key = None
        self._mapping_origin = ""
        self._state = without_mapping(self._state)
        self._state = replace(self._state, session_id=None, last_sequence=None)
        self._pending.clear()
        self._change("break", detail or "muted")

    def _reset_session(self) -> None:
        self._state = IntakeState()
        self._estimator.clear()
        self._identity = None
        self._last_device_ns = None
        self._last_host_ns = None
        self._mute_race = False
        self._mute_reason = ""
        self._seen_fault = None
        self._mapping_key = None
        self._mapping_origin = ""
        self._pending.clear()
        self._held.clear()
        self._false_starts.clear()
        self._window_race_id = None
        self._window_opened_ns = None
        self._window_released_ns = None
        self._window_open = False
        self._race_view = None
        self._restore_race_view = False

    def _false_start_notice(self, event: SensorTriggered) -> FalseStartNotice | None:
        """A crossing inside the start window and before the release.

        ``event.timestamp_ns`` is the mapped capture time. Lines read after the
        release still count as false starts when that capture time is earlier.
        """
        opened = self._window_opened_ns
        race_id = self._window_race_id
        if opened is None or race_id is None or event.timestamp_ns < opened:
            return None
        released = self._window_released_ns
        if released is None:
            if not self._window_open:
                return None
        elif event.timestamp_ns >= released:
            return None
        return FalseStartNotice(
            race_id=race_id,
            lane=event.lane,
            position_id=event.position_id,
            captured_ns=event.timestamp_ns,
        )

    def _remember_false_start(self, notice: FalseStartNotice) -> None:
        key = (notice.lane, notice.captured_ns)
        if any((item.lane, item.captured_ns) == key for item in self._false_starts):
            return
        self._false_starts.append(notice)
        self._change("false_start", str(notice.lane))

    def _restore_race_view_now(self) -> None:
        if self._race_bound or not self._restore_race_view or self._race_view is None:
            return
        assignments, logical = self._race_view
        self._assignments = dict(assignments)
        self._logical = dict(logical)

    def _hold_until_bound(self, event: SensorTriggered) -> bool:
        """Keep a post-release crossing that arrived before the race reader was bound."""
        released = self._window_released_ns
        if self._race_bound or released is None or self._window_open:
            return False
        return event.timestamp_ns >= released

    def _change(self, kind: str, detail: str) -> None:
        self._changes.append((kind, detail))


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
