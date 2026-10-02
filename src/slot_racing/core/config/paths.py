"""Per-user storage locations. ``SLOT_RACING_HOME`` overrides everything.

The project used a different (manufacturer-branded) name before it became manufacturer-neutral.
Data written under that name is still found, never moved or deleted, so an existing installation
keeps its database and configuration. See ``docs/adr/0010-vendor-neutral-naming.md``.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

_APP_DIR_NAME = "SlotRacingPlatform"
_DATABASE_NAME = "slot_racing.db"

_LEGACY_HOME_VARIABLE = "CARRERA_HOME"
_LEGACY_APP_DIR_NAME = "CarreraRacingPlatform"
_LEGACY_DATABASE_NAME = "carrera.db"


def _base_dir() -> Path:
    if sys.platform == "win32":
        return Path(os.environ.get("APPDATA") or Path.home() / "AppData" / "Roaming")
    return Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local" / "share")


def app_data_dir() -> Path:
    override = os.environ.get("SLOT_RACING_HOME") or os.environ.get(_LEGACY_HOME_VARIABLE)
    if override:
        return Path(override)
    current = _base_dir() / _APP_DIR_NAME
    legacy = _base_dir() / _LEGACY_APP_DIR_NAME
    if not current.exists() and legacy.is_dir():
        return legacy
    return current


def default_config_path() -> Path:
    return app_data_dir() / "config.json"


def default_database_path() -> Path:
    directory = app_data_dir()
    current = directory / _DATABASE_NAME
    legacy = directory / _LEGACY_DATABASE_NAME
    if not current.exists() and legacy.is_file():
        return legacy
    return current
