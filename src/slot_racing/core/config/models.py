"""Typed application configuration persisted as JSON."""

from __future__ import annotations

from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from slot_racing.core.config.paths import default_backup_directory, default_database_path


class ConfigError(Exception):
    """The configuration file could not be read."""


AUDIO_ENABLED_DEFAULT = True
AUDIO_VOLUME_DEFAULT = 70


class AppConfig(BaseModel):
    model_config = ConfigDict(extra="ignore")

    language: str = "de"
    database_path: Path | None = None
    timing_source: str | None = None
    """Provider id preselected for new races. ``None`` uses the first available provider."""
    plugin_overrides: dict[str, bool] = Field(default_factory=dict)
    """Explicit user choice per plugin. Plugins without an entry use their manifest default."""
    window_x: int | None = None
    window_y: int | None = None
    window_width: int | None = None
    window_height: int | None = None
    """Last window geometry. Missing values keep the built-in default size and position."""
    backup_directory: Path | None = None
    """Folder for ``.slbackup`` files. ``None`` uses the default folder next to the data."""
    backup_schedule: Literal["off", "daily", "on_exit"] = "off"
    """Automatic backups. ``off`` creates none, ``daily`` one per day, ``on_exit`` when closing."""
    backup_keep: int = Field(default=10, ge=1, le=100)
    """How many automatic backups to keep. Manual and safety copies are left in place."""
    audio_enabled: bool = AUDIO_ENABLED_DEFAULT
    """Whether race sounds play. The race start does not depend on this."""
    audio_volume: int = Field(default=AUDIO_VOLUME_DEFAULT, ge=0, le=100)
    """Race-sound level from 0 (silent) to 100 (full)."""

    def resolved_database_path(self) -> Path:
        return self.database_path or default_database_path()

    def resolved_backup_directory(self) -> Path:
        return self.backup_directory or default_backup_directory()

    def is_plugin_enabled(self, name: str, enabled_by_default: bool = True) -> bool:
        return self.plugin_overrides.get(name, enabled_by_default)


def load_config(path: Path) -> AppConfig:
    """Load the config. A missing file yields defaults; a broken file raises ``ConfigError``."""
    if not path.exists():
        return AppConfig()
    try:
        return AppConfig.model_validate_json(path.read_text(encoding="utf-8"))
    except (OSError, ValidationError) as error:
        raise ConfigError(f"Cannot read configuration {path}: {error}") from error


def save_config(config: AppConfig, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(config.model_dump_json(indent=2), encoding="utf-8")
