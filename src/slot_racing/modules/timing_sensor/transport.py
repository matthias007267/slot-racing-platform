"""Where provisional sensor lines come from.

A transport only yields text. Parsing and timing live in one place, so the
simulator and a later USB reader cannot drift apart. This module does not open
a serial port.
"""

from __future__ import annotations

from collections import deque
from collections.abc import Sequence
from typing import Protocol


class SensorTransport(Protocol):
    """One reader. Race and diagnostics share a single instance; they do not open another."""

    @property
    def connected(self) -> bool:
        """True while this reader is open."""

    def open(self) -> None:
        """Start yielding lines. A second open of an open reader does nothing."""

    def close(self) -> None:
        """Stop yielding lines. Safe when already closed."""

    def read_available(self) -> tuple[str, ...]:
        """Lines waiting now, in arrival order. Empty when nothing new has arrived."""


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
        """Queue one provisional line. Only while this simulator is open."""
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
