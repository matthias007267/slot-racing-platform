"""One camera open at a time.

A race and the setup preview both need the device. Opening it twice would
fight over the same USB camera, so they share a lease. The second request is
refused and the device stays with whoever already holds it.
"""

from __future__ import annotations

import threading


class CameraBusyError(Exception):
    """The camera is already open for a race or for the setup preview."""


class CameraLease:
    """Process-local lock for the physical camera.

    The same owner may acquire more than once. A different owner is refused
    until every acquire has been released.
    """

    RACE = "race"
    PREVIEW = "preview"

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._owner: str | None = None
        self._count = 0

    def holder(self) -> str | None:
        with self._lock:
            return self._owner

    def try_acquire(self, owner: str) -> bool:
        if not isinstance(owner, str) or owner.strip() == "":
            raise ValueError("owner must be a non-empty string")
        with self._lock:
            if self._owner is None:
                self._owner = owner
                self._count = 1
                return True
            if self._owner != owner:
                return False
            self._count += 1
            return True

    def release(self, owner: str) -> None:
        with self._lock:
            if self._owner != owner:
                return
            self._count -= 1
            if self._count <= 0:
                self._owner = None
                self._count = 0
