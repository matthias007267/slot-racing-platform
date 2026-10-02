"""Typed application configuration persisted as JSON."""

from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from carrera.core.config.paths import default_database_path


class ConfigError(Exception):
    """The configuration file could not be read."""


class AppConfig(BaseModel):
    model_config = ConfigDict(extra="ignore")

    language: str = "de"
    database_path: Path | None = None
    plugin_overrides: dict[str, bool] = Field(default_factory=dict)
    """Explicit user choice per plugin. Plugins without an entry use their manifest default."""

    def resolved_database_path(self) -> Path:
        return self.database_path or default_database_path()

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
