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
