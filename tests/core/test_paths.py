from __future__ import annotations

from pathlib import Path

import pytest

from slot_racing.core.config.paths import app_data_dir, default_config_path, default_database_path

LEGACY_HOME = "CARRERA_HOME"
LEGACY_DIR = "CarreraRacingPlatform"
LEGACY_DB = "carrera.db"


@pytest.fixture
def clean_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    monkeypatch.delenv("SLOT_RACING_HOME", raising=False)
    monkeypatch.delenv(LEGACY_HOME, raising=False)
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
    monkeypatch.setenv("APPDATA", str(tmp_path))
    return tmp_path


def test_a_fresh_installation_uses_the_neutral_names(clean_env: Path) -> None:
    assert app_data_dir() == clean_env / "SlotRacingPlatform"
    assert default_database_path().name == "slot_racing.db"
    assert default_config_path().name == "config.json"


def test_the_home_variable_overrides_the_location(
    clean_env: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("SLOT_RACING_HOME", str(clean_env / "custom"))
    assert app_data_dir() == clean_env / "custom"


def test_the_legacy_home_variable_still_works(
    clean_env: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(LEGACY_HOME, str(clean_env / "old"))
    assert app_data_dir() == clean_env / "old"


def test_the_new_home_variable_wins_over_the_legacy_one(
    clean_env: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(LEGACY_HOME, str(clean_env / "old"))
    monkeypatch.setenv("SLOT_RACING_HOME", str(clean_env / "new"))
    assert app_data_dir() == clean_env / "new"


def test_an_existing_legacy_data_directory_is_kept_in_place(clean_env: Path) -> None:
    legacy = clean_env / LEGACY_DIR
    legacy.mkdir()
    assert app_data_dir() == legacy


def test_the_new_data_directory_wins_when_both_exist(clean_env: Path) -> None:
    (clean_env / LEGACY_DIR).mkdir()
    (clean_env / "SlotRacingPlatform").mkdir()
    assert app_data_dir() == clean_env / "SlotRacingPlatform"


def test_an_existing_legacy_database_file_is_still_used(
    clean_env: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("SLOT_RACING_HOME", str(clean_env))
    (clean_env / LEGACY_DB).write_bytes(b"")
    assert default_database_path() == clean_env / LEGACY_DB


def test_the_new_database_file_wins_when_both_exist(
    clean_env: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("SLOT_RACING_HOME", str(clean_env))
    (clean_env / LEGACY_DB).write_bytes(b"")
    (clean_env / "slot_racing.db").write_bytes(b"")
    assert default_database_path() == clean_env / "slot_racing.db"
