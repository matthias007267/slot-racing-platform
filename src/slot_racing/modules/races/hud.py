"""Saved live-HUD layout. Numbers only: no race timing and no Qt.

The document lives in the global settings table under :data:`HUD_CONFIGURATION_KEY`.
A missing or broken document becomes the factory layout instead of crashing.
A version-1 free-form document is kept as the factory layout; its old rectangles
are ignored because the live view no longer places panels by hand.
"""

from __future__ import annotations

import logging
import math
from collections.abc import Callable
from dataclasses import dataclass, replace
from typing import Any

from sqlalchemy.orm.attributes import flag_modified

from slot_racing.core.domain import RaceMode
from slot_racing.core.storage import Database, Setting

logger = logging.getLogger(__name__)

HUD_CONFIGURATION_KEY = "ui.race_hud.configuration"
HUD_WINDOW_KEY = "ui.race_hud.window"
HUD_VERSION = 3
"""Version 2 is the live layout without per-view text. It still loads."""
MIN_SPAN = 0.06
"""Smallest width or height, as a fraction of the display, so a frame stays usable."""

SCALE_MIN = 70
SCALE_MAX = 150
SCALE_STEP = 5
FONT_FLOOR = 8
FONT_CEILING = 96
SHARE_MIN = 1
SHARE_MAX = 6
STANDARD_LAYOUT_ID = "standard"

# The gantry is drawn from these units. One unit is one lamp. Width and height
# stay in this ratio at every scale, in the editor and in a live race.
LIGHT_PAD_X = 0.50
LIGHT_PAD_Y = 0.42
LIGHT_GAP = 0.40
LIGHT_LAMP_COUNT = 5
LIGHT_UNIT_WIDTH = 2 * LIGHT_PAD_X + LIGHT_LAMP_COUNT + (LIGHT_LAMP_COUNT - 1) * LIGHT_GAP
LIGHT_UNIT_HEIGHT = 2 * LIGHT_PAD_Y + 1.0
LIGHT_ASPECT = LIGHT_UNIT_WIDTH / LIGHT_UNIT_HEIGHT
"""Pixel width divided by pixel height. The frame never leaves this ratio."""

LIGHT_STANDARD_WIDTH = 0.50
"""Share of the HUD width at 100%. That is the factory gantry."""

LIGHT_STANDARD_HEIGHT = 0.22
"""Stored companion of the factory width. Placement uses :data:`LIGHT_ASPECT`."""

LIGHT_SCALE_MIN = 20
LIGHT_SCALE_MAX = 200
"""100% is the factory width. 200% is the full HUD width, when the height still fits."""


@dataclass(frozen=True, slots=True)
class HudWindowPlacement:
    """Editor-window chrome. Stored apart from the layout document."""

    x: int | None = None
    y: int | None = None
    width: int | None = None
    height: int | None = None
    maximized: bool = False
    match_live: bool = True


FIELD_DRIVER = "driver"
FIELD_LAP = "lap"
FIELD_LANE = "lane"
FIELD_POSITION = "position"
FIELD_VEHICLE = "vehicle"
FIELD_START = "start"
FIELD_LAST = "last"
FIELD_BEST = "best"
FIELD_BEST_TIME = "best_time"
FIELD_TOTAL = "total"
FIELD_STATUS = "status"
FIELD_REMAINING_LAPS = "remaining_laps"
FIELD_REMAINING_TIME = "remaining_time"

FIELD_IDS: tuple[str, ...] = (
    FIELD_LANE,
    FIELD_POSITION,
    FIELD_DRIVER,
    FIELD_VEHICLE,
    FIELD_START,
    FIELD_LAP,
    FIELD_REMAINING_LAPS,
    FIELD_REMAINING_TIME,
    FIELD_LAST,
    FIELD_BEST,
    FIELD_BEST_TIME,
    FIELD_TOTAL,
    FIELD_STATUS,
)

VIEW_PRESTART = "prestart"
VIEW_LIVE = "live"
VIEW_BETWEEN = "between"
VIEW_RESULTS = "results"
VIEW_IDS: tuple[str, ...] = (VIEW_PRESTART, VIEW_LIVE, VIEW_BETWEEN, VIEW_RESULTS)

TEXT_DRIVER = "driver"
TEXT_VEHICLE = "vehicle"
TEXT_PLACE = "place"
TEXT_TIME = "time"
TEXT_HEADER = "header"
TEXT_ROW = "row"
TEXT_ROLES: tuple[str, ...] = (
    TEXT_DRIVER,
    TEXT_VEHICLE,
    TEXT_PLACE,
    TEXT_TIME,
    TEXT_HEADER,
    TEXT_ROW,
)

DISPLAY_AUTO = "auto"
DISPLAY_ALWAYS = "always"
DISPLAY_HIDE = "hide"
DISPLAY_MODES: tuple[str, ...] = (DISPLAY_AUTO, DISPLAY_ALWAYS, DISPLAY_HIDE)
PANEL_RECORDS = "records"

FIELD_BASE_PX: dict[str, int] = {
    FIELD_DRIVER: 36,
    FIELD_LAP: 32,
    FIELD_LANE: 26,
    FIELD_POSITION: 26,
    FIELD_VEHICLE: 24,
    FIELD_START: 20,
    FIELD_LAST: 22,
    FIELD_BEST: 22,
    FIELD_BEST_TIME: 22,
    FIELD_TOTAL: 22,
    FIELD_STATUS: 20,
    FIELD_REMAINING_LAPS: 22,
    FIELD_REMAINING_TIME: 22,
}
CAPTION_RATIO = 0.55

ALIGNMENTS: tuple[str, ...] = ("center", "left", "right")


@dataclass(frozen=True, slots=True)
class HudWidgetConfig:
    """One rectangle in fractions of a surface. Used for the start-light overlay."""

    id: str
    visible: bool
    x: float
    y: float
    width: float
    height: float
    z_index: int = 0


@dataclass(frozen=True, slots=True)
class PixelRect:
    x: int
    y: int
    width: int
    height: int


@dataclass(frozen=True, slots=True)
class FieldStyle:
    visible: bool = True
    scale: int = 100
    display: str = DISPLAY_AUTO


@dataclass(frozen=True, slots=True)
class LightFrame:
    """Start gantry on the HUD surface. It is not part of the flow.

    ``scale`` is the size, in percent of the factory gantry. ``width`` and
    ``height`` repeat that scale as fractions so an older reader still sees a
    size. The pixels always come from :func:`light_pixels`, which keeps
    :data:`LIGHT_ASPECT`.
    """

    visible: bool = True
    x: float = 0.25
    y: float = 0.30
    width: float = LIGHT_STANDARD_WIDTH
    height: float = LIGHT_STANDARD_HEIGHT
    scale: int = 100

    def as_config(self) -> HudWidgetConfig:
        return HudWidgetConfig(
            "start_lights", self.visible, self.x, self.y, self.width, self.height
        )


@dataclass(frozen=True, slots=True)
class ViewStyle:
    """Typography for one race surface. 100 is the size that surface already used.

    ``panels`` holds display modes for blocks that are not a lane field, such as
    the time-trial records table. A missing panel stays automatic.
    Named layouts remain the place for a later optional HUD profile.
    """

    font_scale: int = 100
    texts: tuple[tuple[str, int], ...] = ()
    panels: tuple[tuple[str, str], ...] = ()

    def text_scale(self, role: str) -> int:
        for key, scale in self.texts:
            if key == role:
                return snap_scale(scale)
        return 100

    def panel(self, panel_id: str) -> str:
        for key, mode in self.panels:
            if key == panel_id and mode in DISPLAY_MODES:
                return mode
        return DISPLAY_AUTO


@dataclass(frozen=True, slots=True)
class HudLayout:
    """One named presentation of the fixed live HUD."""

    id: str
    name: str
    builtin: bool
    font_scale: int = 100
    alignment: str = "center"
    fields: tuple[tuple[str, FieldStyle], ...] = ()
    clock_share: int = 1
    status_share: int = 1
    lanes_share: int = 3
    ranking_share: int = 1
    lights: LightFrame = LightFrame()
    views: tuple[tuple[str, ViewStyle], ...] = ()

    def field(self, field_id: str) -> FieldStyle:
        for key, style in self.fields:
            if key == field_id:
                return style
        return FieldStyle()

    def view(self, view_id: str) -> ViewStyle:
        for key, style in self.views:
            if key == view_id:
                return style
        return ViewStyle()


@dataclass(frozen=True, slots=True)
class HudConfiguration:
    """Every saved layout, which one the editor shows, and which one a new race loads."""

    version: int
    selected_id: str
    default_id: str
    layouts: tuple[HudLayout, ...]

    def layout(self, layout_id: str) -> HudLayout | None:
        return next((item for item in self.layouts if item.id == layout_id), None)

    def selected(self) -> HudLayout:
        found = self.layout(self.selected_id) or self.layout(self.default_id)
        if found is not None:
            return found
        return self.layouts[0]

    def default_layout(self) -> HudLayout:
        found = self.layout(self.default_id) or self.layout(STANDARD_LAYOUT_ID)
        if found is not None:
            return found
        return self.layouts[0]


def factory_layout() -> HudLayout:
    """The built-in presentation. It cannot be deleted."""
    return HudLayout(
        id=STANDARD_LAYOUT_ID,
        name="Standard",
        builtin=True,
        font_scale=100,
        alignment="center",
        fields=tuple((field_id, FieldStyle()) for field_id in FIELD_IDS),
        clock_share=1,
        status_share=1,
        lanes_share=3,
        ranking_share=1,
        lights=LightFrame(),
        views=tuple((view_id, ViewStyle()) for view_id in VIEW_IDS),
    )


def default_hud_configuration() -> HudConfiguration:
    """One factory layout, selected and marked as the default."""
    standard = factory_layout()
    return HudConfiguration(
        version=HUD_VERSION,
        selected_id=standard.id,
        default_id=standard.id,
        layouts=(standard,),
    )


def effective_px(base: int, font_scale: int, field_scale: int) -> int:
    """Base size times the global scale times the field scale, clamped to a readable range."""
    scaled = round(base * snap_scale(font_scale) * snap_scale(field_scale) / 10_000)
    return min(FONT_CEILING, max(FONT_FLOOR, scaled))


def caption_px(value_px: int) -> int:
    return min(value_px, max(FONT_FLOOR, round(value_px * CAPTION_RATIO)))


def snap_scale(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        return 100
    snapped = int(round(float(value) / SCALE_STEP) * SCALE_STEP)
    return min(SCALE_MAX, max(SCALE_MIN, snapped))


def snap_share(value: object, default: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        return default
    return min(SHARE_MAX, max(SHARE_MIN, value))


def snap_alignment(value: object) -> str:
    if isinstance(value, str) and value in ALIGNMENTS:
        return value
    return "center"


def snap_display(value: object) -> str:
    if isinstance(value, str) and value in DISPLAY_MODES:
        return value
    return DISPLAY_AUTO


def field_shown(style: FieldStyle, field_id: str, mode: RaceMode, *, available: bool) -> bool:
    """Whether one lane field is drawn.

    ``auto`` follows the race mode. ``always`` draws the field when a real value
    exists. ``hide``, and a legacy ``visible`` of false, draw nothing.
    A missing value is never replaced with a made-up number.
    """
    if style.display == DISPLAY_HIDE or not style.visible or not available:
        return False
    if style.display == DISPLAY_ALWAYS:
        return True
    if field_id in (FIELD_TOTAL, FIELD_REMAINING_LAPS):
        return mode is RaceMode.LAPS
    if field_id == FIELD_REMAINING_TIME:
        return mode is RaceMode.TIME_TRIAL
    return True


def with_view(layout: HudLayout, view_id: str, style: ViewStyle) -> HudLayout:
    """Replace one surface on a layout. The other surfaces stay as they are."""
    if view_id not in VIEW_IDS:
        return layout
    found = False
    views: list[tuple[str, ViewStyle]] = []
    for key, item in layout.views:
        if key == view_id:
            views.append((view_id, style))
            found = True
        else:
            views.append((key, item))
    if not found:
        views.append((view_id, style))
    return replace(layout, views=tuple(views))


def clamp_widget(widget: HudWidgetConfig) -> HudWidgetConfig:
    """Keep the rectangle inside the surface and at least :data:`MIN_SPAN` on each side."""
    width = _clamp_span(widget.width)
    height = _clamp_span(widget.height)
    return replace(
        widget,
        x=_clamp_origin(widget.x, width),
        y=_clamp_origin(widget.y, height),
        width=width,
        height=height,
    )


def snap_light_scale(value: object) -> int:
    """A gantry scale in percent, inside the absolute range."""
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        return 100
    return min(LIGHT_SCALE_MAX, max(LIGHT_SCALE_MIN, round(float(value))))


def light_width_fraction(scale: int) -> float:
    return LIGHT_STANDARD_WIDTH * snap_light_scale(scale) / 100


def light_height_fraction(scale: int) -> float:
    return LIGHT_STANDARD_HEIGHT * snap_light_scale(scale) / 100


def light_frame(
    *,
    visible: bool = True,
    x: float = 0.25,
    y: float = 0.30,
    scale: int = 100,
) -> LightFrame:
    """One proportional gantry. Width and height fractions follow ``scale``."""
    snapped = snap_light_scale(scale)
    return LightFrame(
        visible=visible,
        x=x,
        y=y,
        width=light_width_fraction(snapped),
        height=light_height_fraction(snapped),
        scale=snapped,
    )


def normalize_light(frame: LightFrame) -> LightFrame:
    """Prefer ``scale``. A width that does not belong to that scale is an old rectangle."""
    expected = light_width_fraction(frame.scale)
    if abs(frame.width - expected) > 0.004:
        scale = snap_light_scale(frame.width / LIGHT_STANDARD_WIDTH * 100)
    else:
        scale = snap_light_scale(frame.scale)
    return light_frame(visible=frame.visible, x=frame.x, y=frame.y, scale=scale)


def light_scale_limits(surface_w: int, surface_h: int) -> tuple[int, int]:
    """Percent range whose box fits this HUD. The top end is the largest that still fits."""
    if surface_w < 2 or surface_h < 2:
        return LIGHT_SCALE_MIN, LIGHT_SCALE_MAX
    high = LIGHT_SCALE_MIN
    for scale in range(LIGHT_SCALE_MAX, LIGHT_SCALE_MIN - 1, -1):
        width = round(surface_w * LIGHT_STANDARD_WIDTH * scale / 100)
        height = round(width / LIGHT_ASPECT)
        if 1 <= width <= surface_w and 1 <= height <= surface_h:
            high = scale
            break
    return LIGHT_SCALE_MIN, high


def effective_light_scale(scale: int, surface_w: int, surface_h: int) -> int:
    """The scale actually drawn. A stored value above the surface limit is lowered for display."""
    snapped = snap_light_scale(scale)
    if surface_w < 2 or surface_h < 2:
        return snapped
    low, high = light_scale_limits(surface_w, surface_h)
    return min(high, max(low, snapped))


def light_pixels(frame: LightFrame, surface_w: int, surface_h: int) -> PixelRect:
    """The gantry on this surface. The box stays inside and keeps :data:`LIGHT_ASPECT`."""
    if surface_w <= 0 or surface_h <= 0:
        return PixelRect(0, 0, 0, 0)
    scale = effective_light_scale(frame.scale, surface_w, surface_h)
    width = max(1, round(surface_w * LIGHT_STANDARD_WIDTH * scale / 100))
    height = max(1, round(width / LIGHT_ASPECT))
    if height > surface_h:
        height = surface_h
        width = max(1, min(surface_w, round(height * LIGHT_ASPECT)))
    if width > surface_w:
        width = surface_w
        height = max(1, min(surface_h, round(width / LIGHT_ASPECT)))
    x = min(max(0, round(frame.x * surface_w)), max(0, surface_w - width))
    y = min(max(0, round(frame.y * surface_h)), max(0, surface_h - height))
    return PixelRect(x, y, width, height)


def light_frame_from_box(
    x_px: int,
    y_px: int,
    width_px: int,
    height_px: int,
    surface_w: int,
    surface_h: int,
    *,
    visible: bool,
) -> LightFrame:
    """A drag result. The width chooses the scale. The height is the locked aspect."""
    del height_px
    if surface_w <= 0 or surface_h <= 0:
        return light_frame(visible=visible)
    raw = width_px / surface_w / LIGHT_STANDARD_WIDTH * 100
    scale = effective_light_scale(snap_light_scale(raw), surface_w, surface_h)
    draft = light_frame(visible=visible, x=x_px / surface_w, y=y_px / surface_h, scale=scale)
    placed = light_pixels(draft, surface_w, surface_h)
    return light_frame(
        visible=visible,
        x=placed.x / surface_w,
        y=placed.y / surface_h,
        scale=scale,
    )


def to_pixels(widget: HudWidgetConfig, canvas_width: int, canvas_height: int) -> PixelRect:
    """Map a normalized rectangle onto a surface. The result stays inside it."""
    clamped = clamp_widget(widget)
    if canvas_width <= 0 or canvas_height <= 0:
        return PixelRect(0, 0, 0, 0)
    x = round(clamped.x * canvas_width)
    y = round(clamped.y * canvas_height)
    width = max(1, round(clamped.width * canvas_width))
    height = max(1, round(clamped.height * canvas_height))
    x = max(0, min(x, canvas_width - 1))
    y = max(0, min(y, canvas_height - 1))
    if x + width > canvas_width:
        width = canvas_width - x
    if y + height > canvas_height:
        height = canvas_height - y
    return PixelRect(x, y, max(1, width), max(1, height))


def replace_layout(document: HudConfiguration, layout: HudLayout) -> HudConfiguration:
    if document.layout(layout.id) is None:
        layouts = (*document.layouts, layout)
    else:
        layouts = tuple(layout if item.id == layout.id else item for item in document.layouts)
    return _with_standard(replace(document, layouts=layouts))


def add_layout(document: HudConfiguration, layout: HudLayout) -> HudConfiguration:
    return _with_standard(
        replace(document, selected_id=layout.id, layouts=(*document.layouts, layout))
    )


def delete_layout(document: HudConfiguration, layout_id: str) -> HudConfiguration:
    """Drop one saved layout. The factory layout stays. A deleted default falls back to it."""
    target = document.layout(layout_id)
    if target is None or target.builtin:
        return document
    layouts = tuple(item for item in document.layouts if item.id != layout_id)
    default_id = document.default_id if document.default_id != layout_id else STANDARD_LAYOUT_ID
    selected_id = document.selected_id if document.selected_id != layout_id else default_id
    return _with_standard(
        replace(document, layouts=layouts, default_id=default_id, selected_id=selected_id)
    )


def mark_default(document: HudConfiguration, layout_id: str) -> HudConfiguration:
    if document.layout(layout_id) is None:
        return document
    return replace(document, default_id=layout_id, selected_id=layout_id)


def window_placement(payload: object) -> HudWindowPlacement:
    """Read a stored editor window. A broken value becomes the default placement."""
    if not isinstance(payload, dict):
        return HudWindowPlacement()
    match_live = payload.get("match_live")
    return HudWindowPlacement(
        x=_optional_int(payload.get("x")),
        y=_optional_int(payload.get("y")),
        width=_optional_int(payload.get("width")),
        height=_optional_int(payload.get("height")),
        maximized=payload.get("maximized") is True,
        match_live=True if match_live is None else bool(match_live),
    )


def _optional_int(value: object) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return value


def to_document(config: HudConfiguration) -> dict[str, Any]:
    return {
        "version": config.version,
        "selected_id": config.selected_id,
        "default_id": config.default_id,
        "layouts": [_layout_document(item) for item in config.layouts],
    }


def coerce(payload: object) -> HudConfiguration:
    """Read a stored document. Raises ``ValueError`` when the document cannot be used.

    Version 1 is the old free-form HUD. Those rectangles no longer describe the live
    view, so the factory layout is used and nothing is dropped on the floor as an error.
    """
    if not isinstance(payload, dict):
        raise ValueError("HUD configuration must be an object")
    version = payload.get("version")
    if version == 1:
        return default_hud_configuration()
    if version not in (2, HUD_VERSION):
        raise ValueError("unsupported HUD configuration version")
    raw_layouts = payload.get("layouts")
    if not isinstance(raw_layouts, list):
        raise ValueError("HUD layouts must be a list")
    parsed: list[HudLayout] = []
    seen: set[str] = set()
    for item in raw_layouts:
        layout = _layout(item)
        if layout is None or layout.id in seen:
            continue
        seen.add(layout.id)
        parsed.append(layout)
    if STANDARD_LAYOUT_ID not in seen:
        parsed.insert(0, factory_layout())
    known = {item.id for item in parsed}
    selected_id = _known_id(payload.get("selected_id"), known)
    default_id = _known_id(payload.get("default_id"), known)
    return HudConfiguration(
        version=HUD_VERSION,
        selected_id=selected_id,
        default_id=default_id,
        layouts=tuple(parsed),
    )


class HudConfigurationStore:
    """Load and save the HUD layouts in the settings table."""

    def __init__(self, database: Database) -> None:
        self._database = database
        self._listeners: list[Callable[[HudConfiguration], None]] = []

    def add_listener(self, listener: Callable[[HudConfiguration], None]) -> Callable[[], None]:
        self._listeners.append(listener)

        def remove() -> None:
            if listener in self._listeners:
                self._listeners.remove(listener)

        return remove

    def load(self) -> HudConfiguration:
        """The saved document, or the factory document when nothing usable is stored."""
        with self._database.session() as session:
            row = session.get(Setting, HUD_CONFIGURATION_KEY)
            if row is None:
                return default_hud_configuration()
            payload = row.value
        try:
            return coerce(payload)
        except ValueError:
            logger.warning("Stored race HUD configuration is invalid; using the factory layout")
            return default_hud_configuration()

    def load_default_layout(self) -> HudLayout:
        """The layout a new live view uses. A missing mark falls back to the factory layout."""
        return self.load().default_layout()

    def save(self, configuration: HudConfiguration) -> HudConfiguration:
        """Store a normalized document and tell listeners. Returns the document that was written."""
        if not isinstance(configuration, HudConfiguration):
            raise TypeError("configuration must be a HudConfiguration")
        normalized = coerce(to_document(configuration))
        document = to_document(normalized)
        with self._database.session() as session:
            row = session.get(Setting, HUD_CONFIGURATION_KEY)
            if row is None:
                session.add(Setting(key=HUD_CONFIGURATION_KEY, value=document))
            else:
                row.value = document
                flag_modified(row, "value")
        self._notify(normalized)
        return normalized

    def reset(self) -> HudConfiguration:
        """Replace the saved document with the factory layout."""
        return self.save(default_hud_configuration())

    def load_window(self) -> HudWindowPlacement:
        """The last editor-window size, or the defaults when nothing usable is stored."""
        with self._database.session() as session:
            row = session.get(Setting, HUD_WINDOW_KEY)
            payload = None if row is None else row.value
        return window_placement(payload)

    def save_window(self, placement: HudWindowPlacement) -> None:
        """Remember the editor window. The layout document stays untouched."""
        if not isinstance(placement, HudWindowPlacement):
            raise TypeError("placement must be a HudWindowPlacement")
        document = {
            "x": placement.x,
            "y": placement.y,
            "width": placement.width,
            "height": placement.height,
            "maximized": placement.maximized,
            "match_live": placement.match_live,
        }
        with self._database.session() as session:
            row = session.get(Setting, HUD_WINDOW_KEY)
            if row is None:
                session.add(Setting(key=HUD_WINDOW_KEY, value=document))
            else:
                row.value = document
                flag_modified(row, "value")

    def _notify(self, configuration: HudConfiguration) -> None:
        for listener in list(self._listeners):
            try:
                listener(configuration)
            except Exception:
                logger.exception("HUD configuration listener failed")


def _layout_document(layout: HudLayout) -> dict[str, Any]:
    lights = layout.lights
    return {
        "id": layout.id,
        "name": layout.name,
        "builtin": layout.builtin,
        "font_scale": layout.font_scale,
        "alignment": layout.alignment,
        "clock_share": layout.clock_share,
        "status_share": layout.status_share,
        "lanes_share": layout.lanes_share,
        "ranking_share": layout.ranking_share,
        "fields": [
            {
                "id": field_id,
                "visible": style.visible,
                "scale": style.scale,
                "display": style.display,
            }
            for field_id, style in layout.fields
        ],
        "views": [_view_document(view_id, style) for view_id, style in layout.views],
        "lights": {
            "visible": lights.visible,
            "x": lights.x,
            "y": lights.y,
            "scale": lights.scale,
            "width": lights.width,
            "height": lights.height,
        },
    }


def _layout(value: object) -> HudLayout | None:
    if not isinstance(value, dict):
        return None
    layout_id = value.get("id")
    if not isinstance(layout_id, str) or not layout_id:
        return None
    name = value.get("name")
    builtin = layout_id == STANDARD_LAYOUT_ID or value.get("builtin") is True
    if layout_id == STANDARD_LAYOUT_ID:
        builtin = True
    base = factory_layout()
    return HudLayout(
        id=layout_id,
        name=name if isinstance(name, str) and name else layout_id,
        builtin=builtin,
        font_scale=snap_scale(value.get("font_scale", 100)),
        alignment=snap_alignment(value.get("alignment")),
        fields=_fields(value.get("fields")),
        clock_share=snap_share(value.get("clock_share"), base.clock_share),
        status_share=snap_share(value.get("status_share"), base.status_share),
        lanes_share=snap_share(value.get("lanes_share"), base.lanes_share),
        ranking_share=snap_share(value.get("ranking_share"), base.ranking_share),
        lights=_lights(value.get("lights")),
        views=_views(value.get("views")),
    )


def _view_document(view_id: str, style: ViewStyle) -> dict[str, Any]:
    return {
        "id": view_id,
        "font_scale": style.font_scale,
        "texts": [{"id": role, "scale": scale} for role, scale in style.texts],
        "panels": [{"id": panel_id, "display": mode} for panel_id, mode in style.panels],
    }


def _views(value: object) -> tuple[tuple[str, ViewStyle], ...]:
    parsed: dict[str, ViewStyle] = {}
    if isinstance(value, list):
        for item in value:
            if not isinstance(item, dict):
                continue
            view_id = item.get("id")
            if not isinstance(view_id, str) or view_id not in VIEW_IDS:
                continue
            parsed[view_id] = ViewStyle(
                font_scale=snap_scale(item.get("font_scale", 100)),
                texts=_text_scales(item.get("texts")),
                panels=_panels(item.get("panels")),
            )
    if not parsed:
        return ()
    return tuple((view_id, parsed[view_id]) for view_id in VIEW_IDS if view_id in parsed)


def _text_scales(value: object) -> tuple[tuple[str, int], ...]:
    if not isinstance(value, list):
        return ()
    found: dict[str, int] = {}
    for item in value:
        if not isinstance(item, dict):
            continue
        role = item.get("id")
        if isinstance(role, str) and role in TEXT_ROLES:
            found[role] = snap_scale(item.get("scale", 100))
    return tuple((role, found[role]) for role in TEXT_ROLES if role in found)


def _panels(value: object) -> tuple[tuple[str, str], ...]:
    if not isinstance(value, list):
        return ()
    found: list[tuple[str, str]] = []
    for item in value:
        if not isinstance(item, dict):
            continue
        panel_id = item.get("id")
        mode = item.get("display")
        if (
            isinstance(panel_id, str)
            and panel_id
            and isinstance(mode, str)
            and mode in DISPLAY_MODES
        ):
            found.append((panel_id, mode))
    return tuple(found)


def _fields(value: object) -> tuple[tuple[str, FieldStyle], ...]:
    parsed: dict[str, FieldStyle] = {}
    if isinstance(value, list):
        for item in value:
            if not isinstance(item, dict):
                continue
            field_id = item.get("id")
            if not isinstance(field_id, str) or field_id not in FIELD_BASE_PX:
                continue
            visible = item.get("visible")
            shown = visible if isinstance(visible, bool) else True
            parsed[field_id] = FieldStyle(
                visible=shown,
                scale=snap_scale(item.get("scale", 100)),
                display=snap_display(item.get("display")),
            )
    return tuple((field_id, parsed.get(field_id, FieldStyle())) for field_id in FIELD_IDS)


def _lights(value: object) -> LightFrame:
    base = LightFrame()
    if not isinstance(value, dict):
        return base
    visible = value.get("visible")
    shown = visible if isinstance(visible, bool) else True
    if "scale" in value:
        scale = snap_light_scale(value.get("scale"))
    else:
        width = _number(value.get("width"), base.width)
        scale = snap_light_scale(width / LIGHT_STANDARD_WIDTH * 100)
    frame = light_frame(
        visible=shown,
        x=_number(value.get("x"), base.x),
        y=_number(value.get("y"), base.y),
        scale=scale,
    )
    width = frame.width
    return light_frame(
        visible=frame.visible,
        x=min(max(0.0, frame.x), max(0.0, 1.0 - width)),
        y=min(max(0.0, frame.y), 1.0),
        scale=frame.scale,
    )


def _with_standard(document: HudConfiguration) -> HudConfiguration:
    if document.layout(STANDARD_LAYOUT_ID) is not None:
        return document
    layouts = (factory_layout(), *document.layouts)
    default_id = _kept(document.default_id, layouts, STANDARD_LAYOUT_ID)
    selected_id = _kept(document.selected_id, layouts, default_id)
    return replace(document, layouts=layouts, default_id=default_id, selected_id=selected_id)


def _known_id(value: object, known: set[str]) -> str:
    if isinstance(value, str) and value in known:
        return value
    return STANDARD_LAYOUT_ID


def _kept(layout_id: str, layouts: tuple[HudLayout, ...], fallback: str) -> str:
    if any(item.id == layout_id for item in layouts):
        return layout_id
    return fallback


def _number(value: object, default: float) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        return default
    return float(value)


def _clamp_span(value: float) -> float:
    if not math.isfinite(value):
        return MIN_SPAN
    return min(1.0, max(MIN_SPAN, value))


def _clamp_origin(value: float, span: float) -> float:
    if not math.isfinite(value):
        return 0.0
    return min(max(0.0, value), 1.0 - span)
