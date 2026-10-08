"""HUD document: scales, shares, the light frame, version and the settings row."""

from __future__ import annotations

import math
from dataclasses import replace

import pytest

from slot_racing.core.storage import Database, Setting
from slot_racing.modules.races.hud import (
    HUD_CONFIGURATION_KEY,
    HUD_VERSION,
    HUD_WINDOW_KEY,
    LIGHT_ASPECT,
    LIGHT_SCALE_MIN,
    LIGHT_STANDARD_WIDTH,
    MIN_SPAN,
    STANDARD_LAYOUT_ID,
    FieldStyle,
    HudConfiguration,
    HudConfigurationStore,
    HudWidgetConfig,
    HudWindowPlacement,
    LightFrame,
    add_layout,
    clamp_widget,
    coerce,
    default_hud_configuration,
    delete_layout,
    effective_light_scale,
    effective_px,
    factory_layout,
    light_frame,
    light_frame_from_box,
    light_pixels,
    light_scale_limits,
    mark_default,
    replace_layout,
    snap_scale,
    to_document,
    to_pixels,
    window_placement,
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


def test_a_saved_light_scale_roundtrips_and_old_rectangles_become_one_scale() -> None:
    stored = light_frame(x=0.12, y=0.18, scale=160, visible=True)
    document = replace_layout(
        default_hud_configuration(),
        replace(factory_layout(), lights=stored),
    )
    loaded = coerce(to_document(document))
    assert loaded.selected().lights.scale == 160
    assert loaded.selected().lights.width == pytest.approx(LIGHT_STANDARD_WIDTH * 1.6)
    assert loaded.selected().font_scale == 100

    legacy = coerce(
        {
            "version": 2,
            "selected_id": "standard",
            "default_id": "standard",
            "layouts": [
                {
                    "id": "standard",
                    "name": "Standard",
                    "builtin": True,
                    "lights": {"visible": True, "x": 0.2, "y": 0.1, "width": 0.4, "height": 0.9},
                }
            ],
        }
    )
    lights = legacy.selected().lights
    assert lights.scale == 80
    wide = light_pixels(lights, 1600, 900)
    tall = light_pixels(lights, 900, 1600)
    assert abs(wide.width / wide.height - LIGHT_ASPECT) < 0.02
    assert abs(tall.width / tall.height - LIGHT_ASPECT) < 0.02
    low, high = light_scale_limits(1600, 900)
    assert low == LIGHT_SCALE_MIN
    assert high == 200
    filled = light_pixels(light_frame(scale=high), 1600, 900)
    assert filled.width >= 1600 - 2
    assert filled.x + filled.width <= 1600
    assert filled.y + filled.height <= 900


def test_a_short_surface_limits_the_scale_and_keeps_the_aspect() -> None:
    low, high = light_scale_limits(1600, 120)
    assert low == LIGHT_SCALE_MIN
    assert high < 100
    frame = light_frame(x=0.4, y=0.8, scale=180)
    assert frame.scale == 180
    assert effective_light_scale(180, 1600, 120) == high
    pixels = light_pixels(frame, 1600, 120)
    assert pixels.width <= 1600
    assert pixels.height <= 120
    assert pixels.x + pixels.width <= 1600
    assert pixels.y + pixels.height <= 120
    assert abs(pixels.width / pixels.height - LIGHT_ASPECT) < 0.05
    wide = light_pixels(frame, 2560, 1440)
    small = light_pixels(frame, 640, 360)
    assert abs(wide.width / 2560 - small.width / 640) < 0.02
    dragged = light_frame_from_box(30, 40, 900, 40, 1600, 900, visible=True)
    placed = light_pixels(dragged, 1600, 900)
    assert dragged.scale == 112
    assert placed.height > 40
    assert abs(placed.width / placed.height - LIGHT_ASPECT) < 0.02
    filled = light_frame_from_box(0, 0, 4000, 80, 1600, 900, visible=True)
    assert filled.scale == light_scale_limits(1600, 900)[1]
    box = light_pixels(filled, 1600, 900)
    assert box.width > 92 * 7.6
    assert box.x + box.width <= 1600
    assert box.y + box.height <= 900


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
    assert custom.lights.scale == LIGHT_SCALE_MIN
    assert custom.lights.width == pytest.approx(LIGHT_STANDARD_WIDTH * LIGHT_SCALE_MIN / 100)
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


def test_the_editor_window_is_stored_apart_from_the_layout_document() -> None:
    database = _database()
    store = HudConfigurationStore(database)
    assert store.load_window() == HudWindowPlacement()
    store.save_window(
        HudWindowPlacement(x=20, y=40, width=1280, height=800, maximized=True, match_live=False)
    )
    assert store.load_window() == HudWindowPlacement(
        x=20, y=40, width=1280, height=800, maximized=True, match_live=False
    )
    assert store.load().version == HUD_VERSION
    with database.session() as session:
        layout = session.get(Setting, HUD_CONFIGURATION_KEY)
        window = session.get(Setting, HUD_WINDOW_KEY)
        assert layout is None
        assert window is not None
        assert window.value["maximized"] is True
        assert "layouts" not in window.value


def test_a_broken_editor_window_falls_back_to_the_default_placement() -> None:
    assert window_placement(None) == HudWindowPlacement()
    assert window_placement({"width": "wide", "match_live": False}) == HudWindowPlacement(
        match_live=False
    )
    assert window_placement({}).match_live is True
    database = _database()
    store = HudConfigurationStore(database)
    with database.session() as session:
        session.add(Setting(key=HUD_WINDOW_KEY, value=["nope"]))
    assert store.load_window() == HudWindowPlacement()


def _database() -> Database:
    database = Database.in_memory()
    database.migrate()
    return database


def _row(database: Database) -> Setting | None:
    with database.session() as session:
        return session.get(Setting, HUD_CONFIGURATION_KEY)
