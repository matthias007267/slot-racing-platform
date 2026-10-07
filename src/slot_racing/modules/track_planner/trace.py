"""Breadcrumbs for the track planner. Names and free text stay out of the log."""

from __future__ import annotations

from slot_racing.core.diagnostics import record


def trace(event: str, **fields: object) -> None:
    result = fields.pop("result", "")
    details = {key: _text(value) for key, value in fields.items()}
    record(
        event,
        module="track_planner",
        page="planner",
        result=_text(result),
        **details,
    )


def _text(value: object) -> str:
    return value if isinstance(value, str) else str(value)
