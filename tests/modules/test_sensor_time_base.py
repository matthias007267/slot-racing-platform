"""The sensor time base against a board whose clock follows true time.

``_Board`` is a device with its own clock: device time is true host time minus its boot
instant, optionally with a skew. A reply or an event reaches the host only after its
delay, and the host stamps it when it reads it. Neither the mapping nor the expected
value is derived from the other, so a wrong time base shows here. No test opens a serial
port, and none uploads firmware.
"""

from __future__ import annotations

from collections import deque

import pytest

from slot_racing.modules.timing_sensor.configuration import SensorAssignment
from slot_racing.modules.timing_sensor.diagnostics import code_id, sensor_diagnostic
from slot_racing.modules.timing_sensor.link import SensorLink, TransportMode
from slot_racing.modules.timing_sensor.protocol import (
    HelloFrame,
    firmware_supports_arm,
    format_hello,
)
from slot_racing.modules.timing_sensor.transport import SerialSensorTransport

_MS = 1_000_000
_SECOND = 1_000_000_000
_STEP = 2 * _MS
_ASSIGNMENTS = (
    SensorAssignment(physical_id="s1", position_id="start_finish", lane=1),
    SensorAssignment(physical_id="s2", position_id="start_finish", lane=2),
)


class _World:
    def __init__(self) -> None:
        self.now = 50 * _SECOND

    def __call__(self) -> int:
        return self.now


class _Board:
    """A controller with a real clock. Its replies and events travel before the host sees them."""

    def __init__(
        self,
        world: _World,
        *,
        skew: float = 0.0,
        out_ns: int = _MS,
        turn_ns: int = 150_000,
        back_ns: int = _MS,
        firmware: str = "1.1.0",
    ) -> None:
        self.world = world
        self.skew = skew
        self.out_ns = out_ns
        self.turn_ns = turn_ns
        self.back_ns = back_ns
        self.firmware = firmware
        self.session = "b0001"
        self.boot = world.now
        self.sequence = 0
        self.pending: deque[tuple[int, bytes]] = deque()
        self.held = False
        self.requests: list[str] = []
        self.closed = False

    def device(self, true_ns: int) -> int:
        return int((true_ns - self.boot) * (1 + self.skew))

    def hello(self) -> None:
        frame = HelloFrame(self.session, "mega2560", self.firmware, "ns", ("s1", "s2"))
        self._send(self.world.now, format_hello(frame))

    def reboot(self, session: str) -> None:
        self.session = session
        self.boot = self.world.now
        self.sequence = 0
        self.pending.clear()
        self.hello()

    def cross(self, sensor: str, true_ns: int, *, delay_ns: int = _MS) -> None:
        self.sequence += 1
        line = f"PSF/1 {self.session} {self.sequence} {self.device(true_ns)} {sensor}"
        self._send(true_ns + delay_ns, line)

    def _send(self, arrival_ns: int, text: str) -> None:
        self.pending.append((arrival_ns, (text + "\n").encode("ascii")))

    def read(self, size: int) -> bytes:
        if self.held:
            return b""
        found = bytearray()
        while self.pending and self.pending[0][0] <= self.world.now and len(found) < size:
            found.extend(self.pending.popleft()[1])
        return bytes(found)

    def write(self, data: bytes) -> int:
        text = data.decode("ascii").strip()
        self.requests.append(text)
        if text.startswith("PSF/1 SYNC "):
            sequence = int(text.split()[2])
            received = self.world.now + self.out_ns
            sent = received + self.turn_ns
            reply = (
                f"PSF/1 SYNC {self.session} {sequence} {self.device(received)} {self.device(sent)}"
            )
            self._send(sent + self.back_ns, reply)
        return len(data)

    def close(self) -> None:
        self.closed = True


class _Rig:
    def __init__(self, board_options: dict[str, object] | None = None) -> None:
        self.world = _World()
        self.board = _Board(self.world, **(board_options or {}))  # type: ignore[arg-type]
        self.transport = SerialSensorTransport(
            "COM9", stream=self.board, clock=self.world, sync_gap_ns=0
        )
        self.link = SensorLink(self.transport, mode=TransportMode.SERIAL, clock=self.world)
        self.link.bind_view(_ASSIGNMENTS, {"start_finish": "start_finish"})
        self.link.acquire()
        self.board.hello()
        self.run_until(self.world.now + 3 * _SECOND)

    def _busy(self) -> bool:
        transport = self.transport
        return bool(self.board.pending) or transport._rounds_left > 0

    def run_until(self, target_ns: int, *, poll: bool = True) -> None:
        """Move true time to ``target_ns``, pumping the reader on the way."""
        while self.world.now < target_ns:
            transport = self.transport
            if self._busy():
                step = _STEP
            else:
                due = transport._next_periodic if transport._periodic else target_ns
                step = max(_STEP, min(target_ns, due) - self.world.now)
            self.world.now = min(target_ns, self.world.now + step)
            transport.pump()
            if poll:
                self.link.poll()
        if poll:
            self.link.poll()

    def warm_up(self, seconds: int) -> None:
        self.run_until(self.world.now + seconds * _SECOND)

    def cross(self, sensor: str, true_ns: int) -> None:
        self.run_until(true_ns)
        self.board.cross(sensor, true_ns)
        self.run_until(true_ns + 20 * _MS)

    def mapped(self, sequence: int) -> int:
        for item in self.link.observations:
            if item.sequence == sequence and item.host_time_ns is not None:
                return item.host_time_ns
        raise AssertionError(f"event {sequence} was not mapped")


def _classified(rig: _Rig) -> dict[int, tuple[str, str]]:
    return {
        item.sequence: (item.classification, item.classification_reason)
        for item in rig.link.observations
        if item.sequence is not None
    }


@pytest.mark.parametrize("skew", [-0.0109, -0.004, 0.0, 0.004, 0.015])
def test_capture_time_follows_the_true_time_over_a_long_race(skew: float) -> None:
    rig = _Rig({"skew": skew})
    rig.warm_up(60)
    start = rig.world.now
    for index in range(1, 16):
        rig.cross("s1", start + index * 20 * _SECOND)
        assert abs(rig.mapped(index) - (start + index * 20 * _SECOND)) < 5 * _MS
    clock = rig.link.clock_report()
    assert clock.rate_status == "fitted"


def test_a_skew_beyond_the_plausibility_limit_is_reported_and_not_hidden() -> None:
    rig = _Rig({"skew": -0.08})
    rig.warm_up(60)
    clock = rig.link.clock_report()
    assert clock.rate_status == "skew_rejected"
    assert clock.skew_ppm is not None and abs(clock.skew_ppm - 86_957) < 500
    assert rig.link.sync_quality[2:4] == (1, 1)
    payload = sensor_diagnostic(rig.link, track_id=None, positions=(), assignments=())
    connection = payload["connection"]
    assert isinstance(connection, dict)
    assert connection["clock"]["rate_status"] == "skew_rejected"
    assert connection["clock"]["raw_skew_ppm"] == clock.skew_ppm


def test_a_board_clock_one_percent_slow_is_fitted_and_not_left_at_rate_one() -> None:
    rig = _Rig({"skew": -0.01087})
    rig.warm_up(30)
    clock = rig.link.clock_report()
    assert clock.rate_status == "fitted"
    assert clock.skew_ppm is not None and abs(clock.skew_ppm - 10_990) < 150
    numerator, denominator = rig.link.sync_quality[2:4]
    assert abs(numerator / denominator - 1.01099) < 0.0002


def test_lap_times_are_not_shortened_by_a_slow_board_clock() -> None:
    rig = _Rig({"skew": -0.01087})
    rig.warm_up(30)
    start = rig.world.now
    for index in range(1, 9):
        rig.cross("s1", start + index * 12_345 * _MS)
    for index in range(2, 9):
        lap = rig.mapped(index) - rig.mapped(index - 1)
        assert abs(lap - 12_345 * _MS) < 5 * _MS


def test_a_slow_board_clock_does_not_turn_a_late_trigger_into_a_false_start() -> None:
    rig = _Rig({"skew": -0.01087})
    rig.warm_up(30)
    start = rig.world.now
    for index in range(1, 11):
        rig.cross("s1", start + index * 20 * _SECOND)
    rig.warm_up(10)
    _opened, released = _windowed(rig)
    rig.run_until(released)
    rig.link.close_start_window(released, 1)
    rig.cross("s2", released + 600 * _MS)
    assert _classified(rig)[rig.board.sequence] == ("race_crossing", "at_or_after_release")
    assert rig.link.false_starts() == ()


def test_a_sample_off_the_fitted_line_keeps_the_rate_out_of_use() -> None:
    from slot_racing.modules.timing_sensor.clock_sync import SyncSample, clock_report, evaluate

    samples = []
    for index in range(8):
        device = index * _SECOND
        host = 100 * _SECOND + int(device * 1.01)
        if index == 4:
            host += 200 * _MS
        samples.append(
            SyncSample(host - _MS, host + _MS, device - 500_000, device + 500_000, index)
        )
    assert clock_report(tuple(samples)).rate_status == "fit_inconsistent"
    assert (evaluate(tuple(samples)).numerator, evaluate(tuple(samples)).denominator) == (1, 1)


def test_the_burst_alone_does_not_claim_a_fitted_rate() -> None:
    rig = _Rig()
    assert rig.link.clock_report().rate_status in {"span_too_short", "fitted"}
    rig.warm_up(30)
    assert rig.link.clock_report().rate_status == "fitted"


def test_every_exchange_is_logged_with_its_request_and_reason() -> None:
    rig = _Rig()
    rig.warm_up(20)
    log = rig.link.sync_log()
    assert len(log) >= 8
    ids = [item["request_id"] for item in log]
    assert all(isinstance(value, int) for value in ids)
    assert len(set(ids)) == len(ids)
    first = log[0]
    assert first["session_id"] == "b0001"
    assert first["outcome"] == "accepted"
    assert isinstance(first["offset_ns"], int)
    assert isinstance(first["uncertainty_ns"], int) and first["uncertainty_ns"] < 5 * _MS
    assert rig.link.mapping_log()[-1]["id"] == rig.link._mapping_id


def test_a_slow_exchange_is_logged_and_never_enters_the_window() -> None:
    rig = _Rig({"back_ns": 700 * _MS})
    rig.warm_up(20)
    outcomes = {item["outcome"] for item in rig.link.sync_log()}
    assert outcomes == {"uncertainty"}
    assert rig.link.sync_quality[4] is False
    assert rig.link.sync.value == "unsynchronized"


def test_replies_that_arrive_together_are_not_mixed_into_one_exchange() -> None:
    rig = _Rig()
    rig.warm_up(30)
    before = len(rig.link.sync_log())
    rig.board.held = True
    rig.run_until(rig.world.now + 11 * _SECOND)
    rig.board.held = False
    rig.run_until(rig.world.now + 100 * _MS)
    fresh = rig.link.sync_log()[before:]
    assert fresh
    assert {item["outcome"] for item in fresh} == {"uncertainty"}
    clock = rig.link.clock_report()
    assert clock.window_samples >= 4
    true_ns = rig.world.now + _SECOND
    rig.cross("s1", true_ns)
    assert abs(rig.mapped(rig.board.sequence) - true_ns) < 5 * _MS


def test_a_reply_of_another_session_is_not_a_sample() -> None:
    rig = _Rig()
    rig.warm_up(20)
    accepted = len([item for item in rig.link.sync_log() if item["outcome"] == "accepted"])
    rig.board.session = "b0002"
    rig.transport._pending_send[900] = rig.world.now
    rig.board._send(rig.world.now, "PSF/1 SYNC b0002 900 5000 5100")
    rig.run_until(rig.world.now + 10 * _MS)
    log = rig.link.sync_log()
    assert [item["outcome"] for item in log if item["request_id"] == 900] == ["session_mismatch"]
    assert len([item for item in log if item["outcome"] == "accepted"]) == accepted


def test_an_exchange_started_before_a_reboot_is_dropped_after_the_new_hello() -> None:
    rig = _Rig()
    rig.warm_up(20)
    rig.transport._pending_send[901] = rig.world.now
    rig.board.reboot("b0002")
    rig.board._send(rig.world.now + 3 * _MS, "PSF/1 SYNC b0002 901 100 200")
    rig.run_until(rig.world.now + 10 * _MS)
    late = [item for item in rig.link.sync_log() if item["request_id"] == 901]
    assert [item["outcome"] for item in late] == ["unknown_request"]


def _windowed(rig: _Rig, *, begin_s: int = 2, go_s: int = 5) -> tuple[int, int]:
    opened = rig.world.now + begin_s * _SECOND
    rig.run_until(opened)
    rig.link.open_start_window(1, opened)
    return opened, opened + go_s * _SECOND


@pytest.mark.parametrize("lane_sensor", ["s1", "s2"])
def test_a_trigger_after_the_visible_release_is_a_race_crossing_on_both_lanes(
    lane_sensor: str,
) -> None:
    rig = _Rig({"skew": -0.003})
    rig.warm_up(40)
    _opened, released = _windowed(rig)
    rig.cross(lane_sensor, released - 2 * _SECOND)
    rig.run_until(released)
    rig.link.close_start_window(released, 1)
    rig.cross(lane_sensor, released + 60 * _MS)
    rig.cross(lane_sensor, released + 4 * _SECOND)
    found = _classified(rig)
    last = rig.board.sequence
    assert found[last - 2] == ("false_start", "release_pending")
    assert rig.link.false_starts()[0].lane == (1 if lane_sensor == "s1" else 2)
    assert found[last - 1] == ("race_crossing", "at_or_after_release")
    assert found[last] == ("race_crossing", "at_or_after_release")


def test_a_start_window_decides_by_capture_time_even_when_the_ui_is_late() -> None:
    rig = _Rig()
    rig.warm_up(40)
    _opened, released = _windowed(rig)
    rig.run_until(released - 1_500 * _MS)
    rig.board.cross("s1", released - 1_200 * _MS)
    rig.board.cross("s2", released + 300 * _MS)
    rig.run_until(released + 2 * _SECOND, poll=False)
    rig.link.close_start_window(released, 1)
    found = _classified(rig)
    assert found[1] == ("false_start", "before_release")
    assert found[2] == ("race_crossing", "at_or_after_release")
    late = next(item for item in rig.link.observations if item.sequence == 2)
    assert late.host_time_ns is not None and late.host_time_ns > released


def test_the_release_boundary_has_no_tolerance_in_either_direction() -> None:
    rig = _Rig()
    rig.warm_up(40)
    _opened, released = _windowed(rig)
    rig.run_until(released - 2 * _SECOND)
    rig.link.close_start_window(released, 1)
    clock = rig.link.clock_report()
    assert clock.anchor_uncertainty_ns is not None
    margin = clock.anchor_uncertainty_ns * 2
    rig.cross("s1", released - margin)
    rig.cross("s2", released + margin)
    found = _classified(rig)
    assert found[1][0] == "false_start"
    assert found[2][0] == "race_crossing"
    rig.link.open_start_window(1, rig.world.now)
    rig.link.close_start_window(rig.mapped(2) + 5 * _SECOND, 1)
    assert [item for item in rig.link.false_starts() if item.race_id == 1] == []


def test_a_repeated_start_uses_its_own_release_and_markers() -> None:
    rig = _Rig()
    rig.warm_up(40)
    first_open, first_release = _windowed(rig)
    rig.link.note_start_marker(1, "ui_release_requested", first_release)
    rig.run_until(first_release)
    rig.link.close_start_window(first_release, 1)
    rig.cross("s1", first_release + 700 * _MS)
    second_open, second_release = _windowed(rig, begin_s=6)
    rig.cross("s1", second_release - 800 * _MS)
    rig.run_until(second_release)
    rig.link.close_start_window(second_release, 1)
    rig.cross("s1", second_release + 100 * _MS)
    found = _classified(rig)
    assert found[1][0] == "race_crossing"
    assert found[2][0] == "false_start"
    assert found[3][0] == "race_crossing"
    sequences = rig.link.start_sequences()
    assert [item["sequence_id"] for item in sequences] == [1, 2]
    assert sequences[0]["begin_ns"] == first_open
    assert sequences[0]["markers"] == {"ui_release_requested": first_release}
    assert sequences[1]["begin_ns"] == second_open
    assert sequences[1]["markers"] == {}


def test_a_session_change_makes_a_time_base_error_and_not_a_false_start() -> None:
    rig = _Rig()
    rig.warm_up(40)
    _opened, released = _windowed(rig)
    rig.run_until(released - 3 * _SECOND)
    rig.board.reboot("b0002")
    rig.board.cross("s1", rig.world.now, delay_ns=0)
    rig.run_until(rig.world.now + 4 * _MS)
    last = [item for item in rig.link.observations if item.sequence is not None][-1]
    assert last.classification == "time_base_error"
    assert rig.link.false_starts() == ()
    rig.warm_up(30)
    rig.cross("s2", rig.world.now + 100 * _MS)
    after = _classified(rig)
    assert after[rig.board.sequence][0] in {"race_crossing", "false_start"}
    sessions = {entry["session_id"] for entry in rig.link.mapping_log()}
    assert "b0002" in sessions


def test_events_carry_the_mapping_and_the_receive_time_as_diagnostic_only() -> None:
    rig = _Rig({"skew": 0.002})
    rig.warm_up(40)
    true_ns = rig.world.now + _SECOND
    rig.run_until(true_ns)
    rig.board.cross("s1", true_ns, delay_ns=700 * _MS)
    rig.run_until(true_ns + 2 * _SECOND)
    item = next(entry for entry in rig.link.observations if entry.sequence == 1)
    assert item.host_time_ns is not None and abs(item.host_time_ns - true_ns) < 5 * _MS
    assert item.receive_host_ns is not None
    assert item.receive_host_ns - item.host_time_ns >= 690 * _MS
    assert item.mapping_id == rig.link.mapping_log()[-1]["id"]
    payload = sensor_diagnostic(
        rig.link, track_id=1, positions=("start_finish",), assignments=_ASSIGNMENTS
    )
    events = payload["events"]
    assert isinstance(events, list)
    shown = events[0]
    assert shown["receive_minus_capture_ns"] == item.receive_host_ns - item.host_time_ns
    assert shown["converted_host_time_ns"] == item.host_time_ns
    assert isinstance(shown["mapping_age_ns"], int)
    ids = {entry["id"] for entry in rig.link.mapping_log()}
    assert shown["mapping_id"] in ids


def test_the_export_names_the_code_the_firmware_and_its_arm_support() -> None:
    old = _Rig({"firmware": "1.0.0"})
    old.warm_up(10)
    new = _Rig({"firmware": "1.1.0"})
    new.warm_up(10)
    first = sensor_diagnostic(old.link, track_id=None, positions=(), assignments=())
    second = sensor_diagnostic(new.link, track_id=None, positions=(), assignments=())
    assert first["build"]["code_id"] == code_id() == second["build"]["code_id"]  # type: ignore[index]
    assert first["firmware"]["capabilities"]["arm"] is False  # type: ignore[index]
    assert second["firmware"]["capabilities"]["arm"] is True  # type: ignore[index]
    assert firmware_supports_arm("garbage") is None
    assert len(code_id()) == 16
    assert second["connection"]["sync_log"]  # type: ignore[index]
    assert second["connection"]["mappings"]  # type: ignore[index]
