"""Device clock to the host's monotonic nanosecond clock.

The mapping is supplied explicitly. Nothing in this module derives an offset
or a rate from the arrival of a message. Without a mapping, callers must not
publish a race event.

``host_ns = offset_ns + device_time_ns * numerator // denominator``.

``numerator / denominator`` is the host nanoseconds represented by one device
tick. Integer division is the whole conversion, so a test can replay it.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class SyncStatus(StrEnum):
    """Whether captured device times may be published."""

    UNSYNCHRONIZED = "unsynchronized"
    SYNCHRONIZED = "synchronized"
    LOST = "lost"


@dataclass(frozen=True, slots=True)
class DeviceClockMapping:
    """One explicit assignment of the device clock onto host time.

    Replacing a mapping discards the previous offset and rate. It does not
    continue them.
    """

    offset_ns: int
    numerator: int = 1
    denominator: int = 1

    def __post_init__(self) -> None:
        _require_int(self.offset_ns, "offset_ns")
        _require_int(self.numerator, "numerator")
        _require_int(self.denominator, "denominator")
        if self.numerator < 1 or self.denominator < 1:
            raise ValueError("clock rate must be positive")


def to_host_ns(device_time_ns: int, mapping: DeviceClockMapping) -> int:
    """Host nanoseconds for one device timestamp. Raises when the result is unusable."""
    if not isinstance(mapping, DeviceClockMapping):
        raise TypeError("mapping must be a DeviceClockMapping")
    _require_int(device_time_ns, "device_time_ns")
    if device_time_ns < 0:
        raise ValueError("device_time_ns must not be negative")
    host_ns = mapping.offset_ns + device_time_ns * mapping.numerator // mapping.denominator
    if host_ns < 0:
        raise ValueError("host time must not be negative")
    return host_ns


def _require_int(value: object, name: str) -> None:
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError(f"{name} must be an int")
