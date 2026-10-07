"""A short, ordered memory of the last relevant application actions."""

from __future__ import annotations

import threading
from collections import deque
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime

from slot_racing.core.diagnostics.sanitize import is_secret_key, sanitize_text

DEFAULT_CAPACITY = 200
_REDACTED = "<REDACTED>"


@dataclass(frozen=True, slots=True)
class Breadcrumb:
    """One structured action. Fields are already safe to write into a bundle."""

    timestamp: str
    event: str
    module: str
    page: str
    result: str
    fields: tuple[tuple[str, str], ...]

    def render(self) -> str:
        parts = [
            self.timestamp,
            f"event={self.event}",
            f"module={self.module}",
            f"page={self.page}",
        ]
        parts.extend(f"{key}={value}" for key, value in self.fields)
        parts.append(f"result={self.result}")
        return " ".join(parts)


class BreadcrumbLog:
    """Thread-safe ring. Adding past the capacity drops the oldest entry."""

    def __init__(self, capacity: int = DEFAULT_CAPACITY) -> None:
        if capacity < 1:
            raise ValueError("breadcrumb capacity must be at least 1")
        self.capacity = capacity
        self._items: deque[Breadcrumb] = deque(maxlen=capacity)
        self._lock = threading.Lock()

    def add(
        self,
        event: str,
        *,
        module: str,
        page: str = "",
        result: str = "",
        fields: Mapping[str, object] | None = None,
    ) -> Breadcrumb:
        item = Breadcrumb(
            timestamp=_timestamp(),
            event=_token(event),
            module=_token(module),
            page=_token(page),
            result=sanitize_text(_token(result)),
            fields=_fields(fields or {}),
        )
        with self._lock:
            self._items.append(item)
        return item

    def snapshot(self) -> tuple[Breadcrumb, ...]:
        with self._lock:
            return tuple(self._items)

    def render(self) -> str:
        lines = [item.render() for item in self.snapshot()]
        return "\n".join(lines) + ("\n" if lines else "")


def _timestamp() -> str:
    moment = datetime.now()
    return moment.strftime("%H:%M:%S.") + f"{moment.microsecond // 1000:03d}"


def _token(value: object) -> str:
    text = sanitize_text(str(value).replace("\n", " ").strip())
    return text


def _fields(fields: Mapping[str, object]) -> tuple[tuple[str, str], ...]:
    rendered: list[tuple[str, str]] = []
    for key, value in fields.items():
        name = _token(key).replace(" ", "_")
        if not name:
            continue
        rendered.append((name, _REDACTED if is_secret_key(name) else _token(value)))
    return tuple(rendered)
