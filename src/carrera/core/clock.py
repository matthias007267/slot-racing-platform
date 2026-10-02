"""Monotonic time sources measured in integer nanoseconds."""

from __future__ import annotations

import time
from typing import Protocol

NANOS_PER_SECOND = 1_000_000_000


class Clock(Protocol):
    """Source of monotonic timestamps in integer nanoseconds."""

    def now_ns(self) -> int: ...


class MonotonicClock:
    """Real clock. ``perf_counter_ns`` is monotonic and high resolution on Windows too."""

    def now_ns(self) -> int:
        return time.perf_counter_ns()


class ManualClock:
    """Deterministic clock for simulation and tests. It only ever moves forward."""

    def __init__(self, start_ns: int = 0) -> None:
        if start_ns < 0:
            raise ValueError("start_ns must not be negative")
        self._now_ns = start_ns

    def now_ns(self) -> int:
        return self._now_ns

    def set(self, timestamp_ns: int) -> None:
        if timestamp_ns < self._now_ns:
            raise ValueError("a monotonic clock cannot move backwards")
        self._now_ns = timestamp_ns

    def advance(self, delta_ns: int) -> None:
        if delta_ns < 0:
            raise ValueError("delta_ns must not be negative")
        self._now_ns += delta_ns
