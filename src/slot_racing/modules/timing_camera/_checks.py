"""Small runtime checks for the camera detection types.

These are programming errors of the detection component, not user facing validation.
"""

from __future__ import annotations


def require_int(name: str, value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError(f"{name} must be an int")
    return value


def require_range(name: str, value: object, low: int, high: int | None = None) -> int:
    number = require_int(name, value)
    if number < low or (high is not None and number > high):
        if high is None:
            raise ValueError(f"{name} must be >= {low}")
        raise ValueError(f"{name} must be between {low} and {high}")
    return number


def require_position_id(value: object) -> str:
    if not isinstance(value, str):
        raise TypeError("position_id must be a str")
    if value.strip() == "" or value != value.strip():
        raise ValueError("position_id must be a non-empty identifier")
    return value
