"""Per-user storage locations. ``CARRERA_HOME`` overrides everything."""

from __future__ import annotations

import os
import sys
from pathlib import Path

_APP_DIR_NAME = "CarreraRacingPlatform"


def app_data_dir() -> Path:
    override = os.environ.get("CARRERA_HOME")
    if override:
        return Path(override)
    if sys.platform == "win32":
        base = Path(os.environ.get("APPDATA") or Path.home() / "AppData" / "Roaming")
    else:
        base = Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local" / "share")
    return base / _APP_DIR_NAME


def default_config_path() -> Path:
    return app_data_dir() / "config.json"


def default_database_path() -> Path:
    return app_data_dir() / "carrera.db"
