"""Dashboard adapter for the existing live broadcast. It does not open a second server."""

from __future__ import annotations

from pathlib import Path

from slot_racing.core.config import AppConfig, save_config
from slot_racing.core.live_display import LiveDisplay, LiveDisplayStatus, describe_live_display
from slot_racing.modules.races.ui.live_server import LiveBroadcast


class BroadcastDisplay(LiveDisplay):
    def __init__(
        self,
        broadcast: LiveBroadcast,
        config: AppConfig,
        config_path: Path | None,
    ) -> None:
        self._broadcast = broadcast
        self._config = config
        self._path = config_path

    def status(self) -> LiveDisplayStatus:
        diagnostics = self._broadcast.diagnostics()
        return describe_live_display(
            phase=self._broadcast.phase,
            enabled=self._config.live_broadcast_enabled,
            running=self._broadcast.is_running(),
            url=self._broadcast.access_url,
            client_count=diagnostics.client_count,
            error=diagnostics.error,
        )

    def ensure(self) -> LiveDisplayStatus:
        self._broadcast.ensure_started(
            enabled=self._config.live_broadcast_enabled,
            port=self._config.live_broadcast_port,
        )
        return self.status()

    def enable(self) -> LiveDisplayStatus:
        self._config.live_broadcast_enabled = True
        if self._path is not None:
            save_config(self._config, self._path)
        return self.ensure()
