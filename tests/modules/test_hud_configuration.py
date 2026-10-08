"""HUD document: scales, shares, the light frame, version and the settings row."""

from __future__ import annotations

import math
from dataclasses import replace

import pytest

from slot_racing.core.storage import Database, Setting
from slot_racing.modules.races.hud import (
    HUD_CONFIGURATION_KEY,
    HUD_VERSION,
    MIN_SPAN,
    STANDARD_LAYOUT_ID,
    FieldStyle,
    HudConfiguration,
    HudConfigurationStore,
    HudWidgetConfig,
    LightFrame,
    add_layout,
    clamp_widget,
    coerce,
    default_hud_configuration,
    delete_layout,
    effective_px,
    factory_layout,
    mark_default,
    replace_layout,
    snap_scale,
    to_document,
    to_pixels,
)


def test_the_factory_layout_is_selected_and_the_default() -> None:
    config = default_hud_configuration()
    assert config.version == HUD_VERSION
    assert config.selected_id == STANDARD_LAYOUT_ID
    assert config.default_id == STANDARD_LAYOUT_ID
    layout = config.default_layout()
    assert layout.builtin is True
    assert layout.font_scale == 100
    assert layout.alignment == "center"
    assert layout.field("driver") == FieldStyle(True, 100)
    assert layout.field("lap").visible is True
    assert (layout.clock_share, layout.status_share) == (1, 1)
    assert (layout.lanes_share, layout.ranking_share) == (3, 1)
    assert layout.lights == LightFrame()


def test_effective_type_keeps_the_hierarchy_inside_the_scale_range() -> None:
    assert effective_px(36, 100, 100) == 36
    assert effective_px(32, 100, 100) == 32
    assert effective_px(26, 100, 100) == 26
    assert effective_px(20, 100, 100) == 20
    assert effective_px(36, 150, 150) == 81
    assert effective_px(32, 150, 100) == 48
    assert effective_px(36, 150, 150) > effective_px(32, 150, 100)
    assert effective_px(20, 70, 70) >= 8
    assert effective_px(36, 70, 70) > effective_px(20, 70, 70)
    assert snap_scale(73) == 75
    assert snap_scale(999) == 150
    assert snap_scale(True) == 100
    assert snap_scale("nope") == 100


def test_rectangles_stay_inside_the_display() -> None:
    too_wide = HudWidgetConfig("start_lights", True, 0.9, 2.0, 0.5, 5.0, z_index=1)
    clamped = clamp_widget(too_wide)
    assert clamped.width == 0.5
    assert clamped.x == pytest.approx(0.5)
    assert clamped.height == 1.0
    assert clamped.y == 0.0
    assert clamped.x + clamped.width <= 1.0
    assert clamped.y + clamped.height <= 1.0

    broken = clamp_widget(HudWidgetConfig("start_lights", True, math.nan, 0.2, math.inf, 0.2))
    assert broken.x == 0.0
    assert broken.width == MIN_SPAN
    tiny = clamp_widget(HudWidgetConfig("start_lights", True, -1.0, -1.0, 0.01, 0.01))
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

    frame = factory_layout().lights.as_config()
    for width, height in ((1920, 1080), (2560, 1440), (3840, 2160), (640, 360)):
        rect = to_pixels(frame, width, height)
        assert rect.x >= 0
        assert rect.y >= 0
        assert rect.x + rect.width <= width
        assert rect.y + rect.height <= height
        assert rect.width >= 1
        assert rect.height >= 1


def test_a_version_one_document_becomes_the_factory_layout() -> None:
    parsed = coerce(
        {
            "version": 1,
            "name": "tv",
            "canvas": {"width": 32, "height": 9},
            "widgets": [
                {
                    "id": "race_clock",
                    "visible": False,
                    "x": 0.1,
                    "y": 0.2,
                    "width": 0.3,
                    "height": 0.25,
                    "z_index": 4,
                }
            ],
        }
    )
    assert parsed == default_hud_configuration()
    with pytest.raises(ValueError):
        coerce(["nope"])
    with pytest.raises(ValueError):
        coerce({"version": 9, "layouts": []})


def test_a_partial_document_keeps_known_fields_and_the_factory_layout() -> None:
    parsed = coerce(
        {
            "version": 2,
            "selected_id": "missing",
            "default_id": "layout-2",
            "layouts": [
                {
                    "id": "layout-2",
                    "name": "Abend",
                    "font_scale": 73,
                    "alignment": "sideways",
                    "clock_share": 9,
                    "fields": [
                        {"id": "driver", "visible": False, "scale": 999},
                        {"id": "retired_widget", "visible": False, "scale": 100},
                        "skip",
                    ],
                    "lights": {"visible": False, "x": 2, "y": -1, "width": 0.01, "height": 0.4},
                }
            ],
        }
    )
    assert parsed.selected_id == STANDARD_LAYOUT_ID
    assert parsed.default_id == "layout-2"
    assert parsed.layout(STANDARD_LAYOUT_ID) is not None
    custom = parsed.layout("layout-2")
    assert custom is not None
    assert custom.font_scale == 75
    assert custom.alignment == "center"
    assert custom.clock_share == 6
    assert custom.field("driver") == FieldStyle(False, 150)
    assert custom.field("lap") == FieldStyle()
    assert custom.lights.visible is False
    assert custom.lights.width == MIN_SPAN
    assert custom.lights.x + custom.lights.width <= 1


def test_deleting_the_default_layout_falls_back_to_the_factory_layout() -> None:
    factory = default_hud_configuration()
    custom = replace(factory_layout(), id="layout-2", name="Abend", builtin=False, font_scale=130)
    document = mark_default(add_layout(factory, custom), "layout-2")
    assert document.default_id == "layout-2"
    deleted = delete_layout(document, "layout-2")
    assert deleted.layout("layout-2") is None
    assert deleted.default_id == STANDARD_LAYOUT_ID
    assert deleted.selected_id == STANDARD_LAYOUT_ID
    assert deleted.default_layout().builtin is True
    assert delete_layout(deleted, STANDARD_LAYOUT_ID) == deleted


def test_store_saves_loads_and_resets() -> None:
    database = _database()
    store = HudConfigurationStore(database)
    assert store.load().default_id == STANDARD_LAYOUT_ID
    assert _row(database) is None

    layout = replace(store.load().default_layout(), font_scale=130)
    changed = replace_layout(store.load(), layout)
    seen: list[HudConfiguration] = []
    store.add_listener(seen.append)

    def explode(_config: HudConfiguration) -> None:
        raise RuntimeError("listener bug")

    store.add_listener(explode)
    saved = store.save(changed)
    assert saved.default_layout().font_scale == 130
    assert len(seen) == 1
    loaded = store.load()
    assert loaded.version == HUD_VERSION
    assert loaded.default_layout().font_scale == 130
    assert to_document(loaded)["version"] == HUD_VERSION

    reset = store.reset()
    assert reset.default_layout().font_scale == 100
    assert store.load_default_layout().font_scale == 100


def test_a_stored_document_with_the_wrong_version_uses_the_factory_layout() -> None:
    database = _database()
    store = HudConfigurationStore(database)
    with database.session() as session:
        session.add(Setting(key=HUD_CONFIGURATION_KEY, value={"version": 99, "widgets": []}))
    assert store.load() == default_hud_configuration()

    with database.session() as session:
        row = session.get(Setting, HUD_CONFIGURATION_KEY)
        assert row is not None
        row.value = "not-an-object"
    assert store.load().default_layout() == default_hud_configuration().default_layout()


def _database() -> Database:
    database = Database.in_memory()
    database.migrate()
    return database


def _row(database: Database) -> Setting | None:
    with database.session() as session:
        return session.get(Setting, HUD_CONFIGURATION_KEY)
