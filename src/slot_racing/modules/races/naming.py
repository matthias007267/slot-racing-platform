"""Suggested names for a new race. Existing stored names are left untouched."""

from __future__ import annotations

from collections.abc import Iterable
from datetime import datetime


def suggest_race_name(taken: Iterable[str], moment: datetime | None = None) -> str:
    """Local ``YYYY-MM-DD HH:mm``. A name that already exists gains `` (2)``, `` (3)``, ..."""
    when = datetime.now().astimezone() if moment is None else moment
    base = when.strftime("%Y-%m-%d %H:%M")
    used = {name.strip() for name in taken}
    if base not in used:
        return base
    number = 2
    while f"{base} ({number})" in used:
        number += 1
    return f"{base} ({number})"


def suggest_distinct_name(source: str, taken: Iterable[str]) -> str:
    """A name based on ``source`` that is never the source itself.

    The first proposal is ``source (2)``. A name that is already stored uses the next number.
    The result stays within the 100 characters a race name allows.
    """
    base = source.strip() or "Rennen"
    used = {name.strip() for name in taken}
    number = 2
    while number < 10000:
        suffix = f" ({number})"
        stem = base
        if len(stem) + len(suffix) > 100:
            stem = stem[: 100 - len(suffix)].rstrip() or "Rennen"
        candidate = f"{stem}{suffix}"
        if candidate not in used:
            return candidate
        number += 1
    return "Rennen (2)"
