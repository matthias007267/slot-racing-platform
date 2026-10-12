"""Read-only status of the local browser display.

The shell paints this. The races module owns the server. The status never includes
a session token except inside the URL the operator is meant to open.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from enum import StrEnum


class LiveDisplayState(StrEnum):
    NOT_STARTED = "not_started"
    STARTING = "starting"
    READY = "ready"
    ERROR = "error"
    STOPPING = "stopping"


@dataclass(frozen=True, slots=True)
class LiveDisplayStatus:
    """What the dashboard may show. ``url`` is empty unless the display is ready."""

    state: LiveDisplayState
    url: str
    client_count: int
    detail: str

    @property
    def usable(self) -> bool:
        return self.state is LiveDisplayState.READY and bool(self.url)


class LiveDisplay(ABC):
    """The one local browser display. Starting it does not start a race."""

    @abstractmethod
    def status(self) -> LiveDisplayStatus:
        """Current state. This does not open a port."""

    @abstractmethod
    def ensure(self) -> LiveDisplayStatus:
        """Reuse a running server, or start one when the operator enabled it."""

    @abstractmethod
    def enable(self) -> LiveDisplayStatus:
        """Remember that the display should run, then start it once."""


def describe_live_display(
    *,
    phase: str,
    enabled: bool,
    running: bool,
    url: str,
    client_count: int,
    error: str,
) -> LiveDisplayStatus:
    """Map the server flags to one dashboard state.

    A URL is returned only while the server is actually accepting connections.
    ``enabled`` false never reports a usable address, even if an older error
    string is still stored.
    """
    if phase == "stopping":
        return LiveDisplayStatus(LiveDisplayState.STOPPING, "", 0, "")
    if phase == "starting":
        return LiveDisplayStatus(LiveDisplayState.STARTING, "", 0, "")
    if not enabled and not running:
        return LiveDisplayStatus(LiveDisplayState.NOT_STARTED, "", 0, "")
    if running and url and not error:
        return LiveDisplayStatus(LiveDisplayState.READY, url, client_count, "")
    if error:
        return LiveDisplayStatus(LiveDisplayState.ERROR, "", 0, error)
    return LiveDisplayStatus(LiveDisplayState.NOT_STARTED, "", 0, "")
