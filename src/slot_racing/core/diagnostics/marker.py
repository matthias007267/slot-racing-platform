"""Remember whether the previous process reached a normal shutdown.

A missing clean mark means the previous session ended without ``mark_clean``.
That includes a crash, a killed process and a power loss. It is not proof of a
defect in the application.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path


@dataclass(frozen=True, slots=True)
class SessionMarker:
    session_id: str
    status: str
    started_at: str
    ended_at: str = ""

    @property
    def clean(self) -> bool:
        return self.status == "clean"


def marker_path(data_dir: Path) -> Path:
    return data_dir / "session-marker.json"


def read_marker(path: Path) -> SessionMarker | None:
    """The stored marker, or ``None`` when this is the first session.

    A file that cannot be read is treated as an unfinished session. The caller
    still starts.
    """
    if not path.exists():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return SessionMarker(session_id="", status="running", started_at="")
    if not isinstance(payload, dict):
        return SessionMarker(session_id="", status="running", started_at="")
    return SessionMarker(
        session_id=_text(payload.get("session_id")),
        status=_text(payload.get("status")) or "running",
        started_at=_text(payload.get("started_at")),
        ended_at=_text(payload.get("ended_at")),
    )


def write_marker(path: Path, marker: SessionMarker) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "session_id": marker.session_id,
                "status": marker.status,
                "started_at": marker.started_at,
                "ended_at": marker.ended_at,
            },
            indent=2,
        ),
        encoding="utf-8",
    )


def running_marker(session_id: str) -> SessionMarker:
    return SessionMarker(
        session_id=session_id,
        status="running",
        started_at=datetime.now().isoformat(timespec="seconds"),
    )


def clean_marker(marker: SessionMarker) -> SessionMarker:
    return SessionMarker(
        session_id=marker.session_id,
        status="clean",
        started_at=marker.started_at,
        ended_at=datetime.now().isoformat(timespec="seconds"),
    )


def _text(value: object) -> str:
    return value if isinstance(value, str) else ""
