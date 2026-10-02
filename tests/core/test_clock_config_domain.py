from pathlib import Path

import pytest

from carrera.core.clock import ManualClock, MonotonicClock
from carrera.core.config import AppConfig, ConfigError, load_config, save_config
from carrera.core.domain import Participant, SensorRole, TimingLayout
from carrera.core.domain.ids import DriverId
from carrera.core.i18n import Translator


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


def test_invalid_config_raises(tmp_path: Path) -> None:
    path = tmp_path / "config.json"
    path.write_text("{not json", encoding="utf-8")
    with pytest.raises(ConfigError):
        load_config(path)


def test_layout_from_sensor_ids_names_points() -> None:
    layout = TimingLayout.from_sensor_ids(["sf", "a", "b"])
    assert [p.label for p in layout.points] == ["START_FINISH", "SECTOR_1", "SECTOR_2"]
    assert [p.sensor_id for p in layout.lap_sequence] == ["a", "b", "sf"]
    assert layout.sector_count == 3
    assert layout.points[0].role is SensorRole.START_FINISH


def test_layout_validation() -> None:
    with pytest.raises(ValueError, match="at least"):
        TimingLayout(())
    with pytest.raises(ValueError, match="unique"):
        TimingLayout.from_sensor_ids(["a", "a"])


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
