"""Where sensor lines come from.

A transport only moves text. Parsing and timing live in one place, so the
simulator and the USB reader cannot drift apart. The serial reader is the
Mega 2560 transport: one baud rate, one port chosen by the caller, and no
port name built in.

Opening the port resets the board. Bytes before the first ``HELLO`` are
discarded. Reading and writing run on a worker thread. The race and the
diagnostic page share the transport the link gives them; they do not open a
second port.
"""

from __future__ import annotations

import threading
import time
from collections import deque
from collections.abc import Callable, Sequence
from typing import Protocol, cast, runtime_checkable

from slot_racing.modules.timing_sensor.clock_sync import SyncSample
from slot_racing.modules.timing_sensor.protocol import (
    BAUD_RATE,
    HelloFrame,
    SensorProtocolError,
    SyncReply,
    format_sync_command,
    parse_sensor_line,
)

_HANDSHAKE_TIMEOUT_NS = 4_000_000_000
_SYNC_GAP_NS = 80_000_000
_PERIODIC_SYNC_NS = 5_000_000_000
_MAX_LINE = 240
_MAX_LINES = 64
_READ_SIZE = 256


@runtime_checkable
class SensorTransport(Protocol):
    """One reader. Race and diagnostics share a single instance; they do not open another."""

    @property
    def connected(self) -> bool:
        """True while this reader is open and, for USB, identified."""

    def open(self) -> None:
        """Start yielding lines. A second open of an open reader does nothing."""

    def close(self) -> None:
        """Stop yielding lines. Safe when already closed."""

    def read_available(self) -> tuple[str, ...]:
        """Lines waiting now, in arrival order. Empty when nothing new has arrived."""


class ByteStream(Protocol):
    """The bytes under a sensor transport. Tests supply this; USB uses pyserial."""

    def read(self, size: int) -> bytes:
        """Return whatever is waiting, or empty. Must not block forever."""

    def write(self, data: bytes) -> int:
        """Write ``data``. Returns how many bytes were accepted."""

    def close(self) -> None:
        """Release the port."""


class SimulatedSensorTransport:
    """In-memory reader. Injected lines are the same text a device transport would yield."""

    def __init__(self) -> None:
        self._open = False
        self._lines: deque[str] = deque()

    @property
    def connected(self) -> bool:
        return self._open

    def open(self) -> None:
        self._open = True

    def close(self) -> None:
        self._open = False
        self._lines.clear()

    def read_available(self) -> tuple[str, ...]:
        if not self._open:
            return ()
        found = tuple(self._lines)
        self._lines.clear()
        return found

    def inject(self, line: str) -> None:
        """Queue one line. Only while this simulator is open."""
        if not isinstance(line, str):
            raise TypeError("line must be text")
        if not self._open:
            raise RuntimeError("simulator is not open")
        self._lines.append(line)


class BufferedSensorTransport:
    """A transport whose lines were already collected. Tests use it as a second reader."""

    def __init__(self, lines: Sequence[str]) -> None:
        self._pending = list(lines)
        self._open = False

    @property
    def connected(self) -> bool:
        return self._open

    def open(self) -> None:
        self._open = True

    def close(self) -> None:
        self._open = False

    def read_available(self) -> tuple[str, ...]:
        if not self._open:
            return ()
        found = tuple(self._pending)
        self._pending.clear()
        return found


class SerialSensorTransport:
    """USB reader for one chosen port.

    ``stream`` lets tests feed bytes without opening a port. The worker is
    started only when no stream is injected. ``pump`` reads once and is what
    the worker and the tests both call.
    """

    def __init__(
        self,
        port: str,
        *,
        stream: ByteStream | None = None,
        clock: Callable[[], int] | None = None,
        threaded: bool = True,
        sync_gap_ns: int = _SYNC_GAP_NS,
    ) -> None:
        if not isinstance(port, str) or not port.strip():
            raise ValueError("serial port name is empty")
        self._port = port.strip()
        self._stream = stream
        self._clock = clock if clock is not None else time.perf_counter_ns
        self._threaded = threaded and stream is None
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._running = False
        self._identified = False
        self._opened_ns = 0
        self._fault: str | None = None
        self._buffer = bytearray()
        self._lines: deque[str] = deque()
        self._warnings: deque[str] = deque()
        self._samples: deque[SyncSample] = deque()
        self._pending_send: dict[int, int] = {}
        self._rounds_left = 0
        self._host_seq = 0
        self._next_sync_at = 0
        self._next_periodic = 0
        self._periodic = False
        self._sync_gap_ns = sync_gap_ns

    @property
    def port(self) -> str:
        return self._port

    @property
    def opened(self) -> bool:
        """True after the port is open, including the handshake before the first hello."""
        return self._running and self._stream is not None

    @property
    def connected(self) -> bool:
        return self.opened and self._identified and self._fault is None

    @property
    def fault(self) -> str | None:
        return self._fault

    def open(self) -> None:
        if self._running:
            return
        self._stop.clear()
        self._fault = None
        self._identified = False
        self._opened_ns = self._clock()
        self._buffer.clear()
        if self._stream is None:
            self._stream = _open_serial(self._port)
        self._running = True
        if self._threaded:
            self._thread = threading.Thread(
                target=self._run, name="slot-racing-sensor", daemon=True
            )
            self._thread.start()

    def close(self) -> None:
        self._stop.set()
        self._running = False
        self._identified = False
        stream = self._stream
        self._stream = None
        if stream is not None:
            stream.close()
        thread = self._thread
        self._thread = None
        if thread is not None and thread.is_alive() and threading.current_thread() is not thread:
            thread.join(timeout=2)
        with self._lock:
            self._lines.clear()
            self._samples.clear()
            self._pending_send.clear()
            self._rounds_left = 0

    def read_available(self) -> tuple[str, ...]:
        if not self._identified or self._fault is not None:
            return ()
        with self._lock:
            found = tuple(self._lines)
            self._lines.clear()
            return found

    def take_warnings(self) -> tuple[str, ...]:
        with self._lock:
            found = tuple(self._warnings)
            self._warnings.clear()
            return found

    def take_sync_samples(self) -> tuple[SyncSample, ...]:
        with self._lock:
            found = tuple(self._samples)
            self._samples.clear()
            return found

    def request_sync(self, rounds: int = 5) -> None:
        """Queue a few clock exchanges. The worker writes them between reads."""
        if isinstance(rounds, bool) or not isinstance(rounds, int) or rounds < 1:
            raise ValueError("sync rounds must be a positive int")
        with self._lock:
            self._rounds_left += rounds
            self._periodic = True
            self._next_sync_at = 0
            # The burst is the measurement. The next sample waits a full period
            # instead of leaving as soon as the burst counter hits zero.
            self._next_periodic = self._clock() + _PERIODIC_SYNC_NS

    def pump(self) -> None:
        """Read and write once. The worker calls this; tests call it directly."""
        if not self._running or self._stop.is_set() or self._stream is None:
            return
        if not self._identified and self._clock() - self._opened_ns > _HANDSHAKE_TIMEOUT_NS:
            self._fail("handshake_timeout")
            return
        self._write_sync()
        try:
            data = self._stream.read(self._read_amount())
        except Exception:
            self._fail("disconnected")
            return
        if data:
            self._consume(data)

    def _read_amount(self) -> int:
        """How many bytes to ask for on this pass.

        pyserial waits until the requested count arrives or the timeout ends.
        A sync reply is much shorter than 256 bytes, so that wait was added to
        every exchange and the sample looked too uncertain to keep. When the
        port can say how many bytes are already buffered, read those. Otherwise
        read one byte, which returns as soon as the reply starts.
        """
        stream = self._stream
        if stream is None:
            return _READ_SIZE
        waiting = getattr(stream, "in_waiting", None)
        if isinstance(waiting, bool) or not isinstance(waiting, int):
            return _READ_SIZE
        if waiting > 0:
            return min(waiting, _READ_SIZE)
        return 1

    def _run(self) -> None:
        while not self._stop.is_set():
            self.pump()

    def _fail(self, code: str) -> None:
        self._fault = code
        self._identified = False
        self._running = False

    def _write_sync(self) -> None:
        stream = self._stream
        if stream is None or not self._identified or self._fault is not None:
            return
        now = self._clock()
        with self._lock:
            if self._rounds_left <= 0:
                if not self._periodic or now < self._next_periodic:
                    return
                self._rounds_left = 1
                self._next_periodic = now + _PERIODIC_SYNC_NS
            if now < self._next_sync_at:
                return
            self._rounds_left -= 1
            self._host_seq += 1
            host_seq = self._host_seq
            self._pending_send[host_seq] = now
            self._next_sync_at = now + self._sync_gap_ns
        line = format_sync_command(host_seq) + "\n"
        try:
            stream.write(line.encode("ascii"))
        except Exception:
            self._fail("disconnected")

    def _consume(self, data: bytes) -> None:
        self._buffer.extend(data)
        while True:
            newline = self._buffer.find(b"\n")
            if newline < 0:
                if len(self._buffer) > _MAX_LINE:
                    self._buffer.clear()
                    self._warn("partial")
                return
            raw = bytes(self._buffer[:newline])
            del self._buffer[: newline + 1]
            if raw.endswith(b"\r"):
                raw = raw[:-1]
            if len(raw) > _MAX_LINE:
                self._warn("partial")
                continue
            try:
                text = raw.decode("ascii")
            except UnicodeDecodeError:
                if self._identified:
                    self._warn("partial")
                continue
            self._accept_line(text)

    def _accept_line(self, text: str) -> None:
        try:
            message = parse_sensor_line(text)
        except SensorProtocolError:
            if self._identified:
                self._queue_line(text)
            return
        if not self._identified:
            if isinstance(message, HelloFrame):
                self._identified = True
                self._queue_line(text)
            return
        if isinstance(message, SyncReply):
            self._complete_sync(message)
        self._queue_line(text)

    def _complete_sync(self, message: SyncReply) -> None:
        with self._lock:
            sent = self._pending_send.pop(message.host_seq, None)
        if sent is None:
            return
        received = self._clock()
        try:
            sample = SyncSample(sent, received, message.device_rx_ns, message.device_tx_ns)
        except (TypeError, ValueError):
            return
        with self._lock:
            self._samples.append(sample)

    def _queue_line(self, text: str) -> None:
        with self._lock:
            if len(self._lines) >= _MAX_LINES:
                self._warnings.append("host_overflow")
                return
            self._lines.append(text)

    def _warn(self, code: str) -> None:
        with self._lock:
            self._warnings.append(code)


def list_serial_ports() -> tuple[str, ...]:
    """Port names the operating system reports. Empty when pyserial cannot list them."""
    try:
        from serial.tools import list_ports
    except ImportError:
        return ()
    names: list[str] = []
    for port in list_ports.comports():
        device = getattr(port, "device", "")
        if isinstance(device, str) and device:
            names.append(device)
    return tuple(names)


def _open_serial(name: str) -> ByteStream:
    """Open ``name`` at the module baud rate. DTR resets an Arduino on open."""
    import serial

    opened = serial.Serial(port=name, baudrate=BAUD_RATE, timeout=0.05, write_timeout=0.5)
    return cast(ByteStream, _SerialPort(opened))


class _SerialPort:
    """Adapt pyserial to :class:`ByteStream` without leaking its types."""

    def __init__(self, port: object) -> None:
        self._port = port

    @property
    def in_waiting(self) -> int:
        """Bytes already buffered. Zero when the port cannot answer."""
        try:
            waiting = self._port.in_waiting  # type: ignore[attr-defined]
        except Exception:
            return 0
        if isinstance(waiting, bool) or not isinstance(waiting, int) or waiting < 1:
            return 0
        return waiting

    def read(self, size: int) -> bytes:
        data = self._port.read(size)  # type: ignore[attr-defined]
        if isinstance(data, bytearray):
            return bytes(data)
        if isinstance(data, bytes):
            return data
        return b""

    def write(self, data: bytes) -> int:
        written = self._port.write(data)  # type: ignore[attr-defined]
        if isinstance(written, int):
            return written
        return len(data)

    def close(self) -> None:
        self._port.close()  # type: ignore[attr-defined]
