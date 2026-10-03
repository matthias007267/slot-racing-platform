"""HUD document: default layout, clamping, pixels, version and the settings row."""

from __future__ import annotations

import math

import pytest

from slot_racing.core.storage import Database, Setting
from slot_racing.modules.races.hud import (
    BEST_LAP,
    HUD_CONFIGURATION_KEY,
    HUD_VERSION,
    LIVE_RANKING,
    MIN_SPAN,
    RACE_CLOCK,
    RACE_CONTROLS,
    RACE_HEADER,
    WIDGET_IDS,
    HudCanvas,
    HudConfiguration,
    HudConfigurationStore,
    HudWidgetConfig,
    clamp_widget,
    coerce,
    default_hud_configuration,
    stacking_order,
    to_document,
    to_pixels,
    with_widget,
)


def test_the_standard_layout_covers_the_known_widgets() -> None:
    config = default_hud_configuration()
    assert config.version == HUD_VERSION
    assert config.name == "standard"
    assert (config.canvas.width, config.canvas.height) == (16, 9)
    assert {item.id for item in config.widgets} == set(WIDGET_IDS)
    assert all(item.visible for item in config.widgets)
    header = config.widget(RACE_HEADER)
    clock = config.widget(RACE_CLOCK)
    ranking = config.widget(LIVE_RANKING)
    controls = config.widget(RACE_CONTROLS)
    assert header is not None and clock is not None and ranking is not None and controls is not None
    assert (header.x, header.y, header.width, header.height) == (0.02, 0.02, 0.62, 0.14)
    assert (clock.x, clock.width) == (0.66, 0.32)
    assert ranking.width == 0.62
    assert controls.z_index == 3
    assert controls.z_index > header.z_index


def test_rectangles_stay_inside_the_display() -> None:
    too_wide = HudWidgetConfig("race_clock", True, 0.9, 2.0, 0.5, 5.0, z_index=1)
    clamped = clamp_widget(too_wide)
    assert clamped.width == 0.5
    assert clamped.x == pytest.approx(0.5)
    assert clamped.height == 1.0
    assert clamped.y == 0.0
    assert clamped.x + clamped.width <= 1.0
    assert clamped.y + clamped.height <= 1.0

    broken = clamp_widget(HudWidgetConfig("race_clock", True, math.nan, 0.2, math.inf, 0.2))
    assert broken.x == 0.0
    assert broken.width == MIN_SPAN
    tiny = clamp_widget(HudWidgetConfig("race_clock", True, -1.0, -1.0, 0.01, 0.01))
    assert tiny.width == MIN_SPAN
    assert tiny.height == MIN_SPAN
    assert tiny.x == 0.0
    assert tiny.y == 0.0


def test_normalized_coordinates_become_pixels_for_each_window_size() -> None:
    widget = HudWidgetConfig("panel", True, 0.25, 0.5, 0.5, 0.25)
    full_hd = to_pixels(widget, 1920, 1080)
    assert (full_hd.x, full_hd.y, full_hd.width, full_hd.height) == (480, 540, 960, 270)
    large = to_pixels(widget, 3840, 2160)
    assert (large.x, large.y, large.width, large.height) == (960, 1080, 1920, 540)
    small = to_pixels(widget, 320, 180)
    assert (small.x, small.y, small.width, small.height) == (80, 90, 160, 45)
    assert to_pixels(widget, 0, 400) == to_pixels(widget, 0, 0)
    assert to_pixels(widget, 0, 0).width == 0

    for width, height in ((1920, 1080), (2560, 1440), (3840, 2160), (640, 360)):
        for item in default_hud_configuration().widgets:
            rect = to_pixels(item, width, height)
            assert rect.x >= 0
            assert rect.y >= 0
            assert rect.x + rect.width <= width
            assert rect.y + rect.height <= height
            assert rect.width >= 1
            assert rect.height >= 1


def test_stacking_order_uses_z_index_then_id() -> None:
    low = HudWidgetConfig("b", True, 0.0, 0.0, 0.2, 0.2, z_index=1)
    high = HudWidgetConfig("a", True, 0.0, 0.0, 0.2, 0.2, z_index=3)
    tied = HudWidgetConfig("c", True, 0.0, 0.0, 0.2, 0.2, z_index=3)
    assert [item.id for item in stacking_order([high, low, tied])] == ["b", "a", "c"]


def test_coerce_fills_gaps_and_rejects_a_broken_document() -> None:
    with pytest.raises(ValueError):
        coerce(["nope"])
    with pytest.raises(ValueError):
        coerce({"version": 2, "widgets": []})
    with pytest.raises(ValueError):
        coerce({"widgets": []})

    clock = {
        "id": RACE_CLOCK,
        "visible": False,
        "x": 0.1,
        "y": 0.2,
        "width": 0.3,
        "height": 0.25,
        "z_index": 4,
    }
    parsed = coerce(
        {
            "version": 1,
            "name": "tv",
            "canvas": {"width": 32, "height": 9},
            "widgets": [
                "skip",
                {"id": RACE_CLOCK, "x": True},
                clock,
                clock,
                {
                    "id": "future_panel",
                    "visible": True,
                    "x": 0.0,
                    "y": 0.0,
                    "width": 0.2,
                    "height": 0.2,
                },
            ],
        }
    )
    assert parsed.name == "tv"
    assert (parsed.canvas.width, parsed.canvas.height) == (32, 9)
    stored_clock = parsed.widget(RACE_CLOCK)
    assert stored_clock is not None
    assert stored_clock.visible is False
    assert stored_clock.z_index == 4
    assert parsed.widget("future_panel") is not None
    assert parsed.widget(BEST_LAP) is not None
    assert [item.id for item in parsed.widgets].count(RACE_CLOCK) == 1
    assert set(WIDGET_IDS).issubset({item.id for item in parsed.widgets})


def test_a_bad_canvas_falls_back_without_dropping_the_document() -> None:
    parsed = coerce({"version": 1, "widgets": [], "canvas": {"width": True, "height": 0}})
    assert (parsed.canvas.width, parsed.canvas.height) == (16, 9)
    assert len(parsed.widgets) == len(WIDGET_IDS)


def test_store_saves_loads_and_resets() -> None:
    database = _database()
    store = HudConfigurationStore(database)
    assert store.load().name == "standard"
    assert _row(database) is None

    changed = with_widget(
        default_hud_configuration(),
        HudWidgetConfig(RACE_CLOCK, False, 0.15, 0.15, 0.4, 0.3, z_index=8),
    )
    seen: list[HudConfiguration] = []
    store.add_listener(seen.append)

    def explode(_config: HudConfiguration) -> None:
        raise RuntimeError("listener bug")

    store.add_listener(explode)
    saved = store.save(changed)
    saved_clock = saved.widget(RACE_CLOCK)
    assert saved_clock is not None and saved_clock.visible is False
    assert len(seen) == 1
    loaded = store.load()
    assert loaded.version == HUD_VERSION
    assert loaded.widget(RACE_CLOCK) == saved.widget(RACE_CLOCK)
    assert to_document(loaded)["version"] == 1

    reset = store.reset()
    assert reset.widget(RACE_CLOCK) == default_hud_configuration().widget(RACE_CLOCK)
    assert store.load().widget(RACE_CLOCK) == reset.widget(RACE_CLOCK)


def test_a_stored_document_with_the_wrong_version_uses_the_standard_layout() -> None:
    database = _database()
    store = HudConfigurationStore(database)
    with database.session() as session:
        session.add(Setting(key=HUD_CONFIGURATION_KEY, value={"version": 99, "widgets": []}))
    assert store.load() == default_hud_configuration()

    with database.session() as session:
        row = session.get(Setting, HUD_CONFIGURATION_KEY)
        assert row is not None
        row.value = "not-an-object"
    assert store.load().widgets == default_hud_configuration().widgets


def test_with_widget_appends_a_new_id_and_clamps() -> None:
    config = default_hud_configuration()
    updated = with_widget(
        config, HudWidgetConfig("future_panel", True, 0.8, 0.8, 0.5, 0.5, z_index=2)
    )
    added = updated.widget("future_panel")
    assert added is not None
    assert added.x == pytest.approx(0.5)
    assert added.width == pytest.approx(0.5)
    assert updated.widget(RACE_HEADER) == config.widget(RACE_HEADER)


def _database() -> Database:
    database = Database.in_memory()
    database.migrate()
    return database


def _row(database: Database) -> Setting | None:
    with database.session() as session:
        return session.get(Setting, HUD_CONFIGURATION_KEY)


def test_canvas_placeholder_is_not_a_pixel_size() -> None:
    assert HudCanvas().width == 16
