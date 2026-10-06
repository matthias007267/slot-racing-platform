from pathlib import Path

import pytest
from pydantic import ValidationError as ModelValidationError

from slot_racing.core.clock import ManualClock, MonotonicClock, format_duration
from slot_racing.core.config import AppConfig, ConfigError, load_config, save_config
from slot_racing.core.domain import Participant, TimingLayout, TimingPositionType
from slot_racing.core.domain.ids import DriverId
from slot_racing.core.errors import ValidationError
from slot_racing.core.i18n import Translator


def test_manual_clock_moves_forward_only() -> None:
    clock = ManualClock(10)
    clock.advance(5)
    clock.set(20)
    assert clock.now_ns() == 20
    with pytest.raises(ValueError, match="backwards"):
        clock.set(19)


def test_monotonic_clock_returns_increasing_integers() -> None:
    clock = MonotonicClock()
    first, second = clock.now_ns(), clock.now_ns()
    assert isinstance(first, int)
    assert second >= first


def test_config_defaults_and_plugin_overrides() -> None:
    config = AppConfig()
    assert config.language == "de"
    assert config.is_plugin_enabled("races")
    assert not config.is_plugin_enabled("timing_camera", enabled_by_default=False)
    config.plugin_overrides["timing_camera"] = True
    assert config.is_plugin_enabled("timing_camera", enabled_by_default=False)


def test_config_roundtrip_and_missing_file(tmp_path: Path) -> None:
    path = tmp_path / "sub" / "config.json"
    assert load_config(path) == AppConfig()
    config = AppConfig(language="en", plugin_overrides={"statistics": False})
    save_config(config, path)
    assert load_config(path) == config


def test_audio_settings_default_roundtrip_and_old_files(tmp_path: Path) -> None:
    config = AppConfig()
    assert config.audio_enabled is True
    assert config.audio_volume == 70
    path = tmp_path / "config.json"
    path.write_text('{"language": "en", "backup_keep": 4}', encoding="utf-8")
    loaded = load_config(path)
    assert loaded.language == "en"
    assert loaded.backup_keep == 4
    assert loaded.audio_enabled is True
    assert loaded.audio_volume == 70
    loaded.audio_enabled = False
    loaded.audio_volume = 0
    save_config(loaded, path)
    again = load_config(path)
    assert again.audio_enabled is False
    assert again.audio_volume == 0
    assert AppConfig(audio_volume=100).audio_volume == 100
    with pytest.raises(ModelValidationError):
        AppConfig(audio_volume=-1)
    with pytest.raises(ModelValidationError):
        AppConfig(audio_volume=101)
    path.write_text('{"audio_volume": 140}', encoding="utf-8")
    with pytest.raises(ConfigError):
        load_config(path)


def test_invalid_config_raises(tmp_path: Path) -> None:
    path = tmp_path / "config.json"
    path.write_text("{not json", encoding="utf-8")
    with pytest.raises(ConfigError):
        load_config(path)


def test_layout_from_position_ids_names_positions() -> None:
    layout = TimingLayout.from_position_ids(["sf", "a", "b"])
    assert [p.label for p in layout.positions] == ["START_FINISH", "SECTOR_1", "SECTOR_2"]
    assert [p.id for p in layout.lap_sequence] == ["a", "b", "sf"]
    assert layout.sector_count == 3
    assert layout.positions[0].type is TimingPositionType.START_FINISH


def test_layout_validation() -> None:
    with pytest.raises(ValueError, match="no_positions"):
        TimingLayout(())
    with pytest.raises(ValueError, match="position_duplicate"):
        TimingLayout.from_position_ids(["a", "a"])


def test_participant_lane_must_be_positive() -> None:
    with pytest.raises(ValueError, match="lane"):
        Participant(DriverId(1), 0)


def test_translator_falls_back_to_default_language_then_key() -> None:
    translator = Translator("en")
    translator.add_catalog("de", {"hello": "Hallo"})
    translator.add_catalog("en", {"bye": "Bye"})
    assert translator.translate("bye") == "Bye"
    assert translator.translate("hello") == "Hallo"
    assert translator.translate("unknown") == "unknown"
    assert translator.translate("unknown", "Fallback") == "Fallback"


def test_format_duration() -> None:
    assert format_duration(None) == "-"
    assert format_duration(0) == "0:00.000"
    assert format_duration(5_432_000_000) == "0:05.432"
    assert format_duration(75_001_000_000) == "1:15.001"
    assert format_duration(3_723_400_000_000) == "1:02:03.400"


def test_translator_formats_placeholders() -> None:
    translator = Translator()
    translator.add_catalog("de", {"hello": "Hallo {name}", "broken": "Hallo {unknown}"})
    assert translator.format("hello", name="Anna") == "Hallo Anna"
    assert translator.format("broken", name="Anna") == "Hallo {unknown}"
    assert translator.format("missing.key") == "missing.key"


def test_validation_error_carries_key_and_params() -> None:
    error = ValidationError("error.x", limit=3)
    assert error.key == "error.x"
    assert error.params == {"limit": 3}
    assert str(error) == "error.x"
