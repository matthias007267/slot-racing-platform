"""Base class of all events."""

from __future__ import annotations

from dataclasses import dataclass, fields


@dataclass(frozen=True, slots=True, kw_only=True)
class Event:
    """Immutable event. ``timestamp_ns`` is a monotonic timestamp in integer nanoseconds.

    Every field whose name ends in ``_ns`` is validated to be a non-negative ``int`` so that
    float seconds can never leak into time measurements.
    """

    timestamp_ns: int

    def __post_init__(self) -> None:
        for field in fields(self):
            if not field.name.endswith("_ns"):
                continue
            value = getattr(self, field.name)
            if isinstance(value, bool) or not isinstance(value, int):
                raise TypeError(f"{field.name} must be an int number of nanoseconds")
            if value < 0:
                raise ValueError(f"{field.name} must not be negative")
