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

from slot_racing.core.storage import Database, Setting

logger = logging.getLogger(__name__)

HUD_CONFIGURATION_KEY = "ui.race_hud.configuration"
HUD_VERSION = 2
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

FIELD_DRIVER = "driver"
FIELD_LAP = "lap"
FIELD_LANE = "lane"
FIELD_POSITION = "position"
FIELD_VEHICLE = "vehicle"
FIELD_START = "start"
FIELD_LAST = "last"
FIELD_BEST = "best"
FIELD_TOTAL = "total"
FIELD_STATUS = "status"

FIELD_IDS: tuple[str, ...] = (
    FIELD_LANE,
    FIELD_POSITION,
    FIELD_DRIVER,
    FIELD_VEHICLE,
    FIELD_START,
    FIELD_LAP,
    FIELD_LAST,
    FIELD_BEST,
    FIELD_TOTAL,
    FIELD_STATUS,
)

FIELD_BASE_PX: dict[str, int] = {
    FIELD_DRIVER: 36,
    FIELD_LAP: 32,
    FIELD_LANE: 26,
    FIELD_POSITION: 26,
    FIELD_VEHICLE: 24,
    FIELD_START: 20,
    FIELD_LAST: 22,
    FIELD_BEST: 22,
    FIELD_TOTAL: 22,
    FIELD_STATUS: 20,
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


@dataclass(frozen=True, slots=True)
class LightFrame:
    """Start gantry, as fractions of the HUD surface. It is not part of the flow."""

    visible: bool = True
    x: float = 0.25
    y: float = 0.30
    width: float = 0.50
    height: float = 0.22

    def as_config(self) -> HudWidgetConfig:
        return HudWidgetConfig(
            "start_lights", self.visible, self.x, self.y, self.width, self.height
        )


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

    def field(self, field_id: str) -> FieldStyle:
        for key, style in self.fields:
            if key == field_id:
                return style
        return FieldStyle()


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
    if version != HUD_VERSION:
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
            {"id": field_id, "visible": style.visible, "scale": style.scale}
            for field_id, style in layout.fields
        ],
        "lights": {
            "visible": lights.visible,
            "x": lights.x,
            "y": lights.y,
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
    )


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
            parsed[field_id] = FieldStyle(
                visible=visible if isinstance(visible, bool) else True,
                scale=snap_scale(item.get("scale", 100)),
            )
    return tuple((field_id, parsed.get(field_id, FieldStyle())) for field_id in FIELD_IDS)


def _lights(value: object) -> LightFrame:
    base = LightFrame()
    if not isinstance(value, dict):
        return base
    visible = value.get("visible")
    frame = LightFrame(
        visible=visible if isinstance(visible, bool) else True,
        x=_number(value.get("x"), base.x),
        y=_number(value.get("y"), base.y),
        width=_number(value.get("width"), base.width),
        height=_number(value.get("height"), base.height),
    )
    clamped = clamp_widget(frame.as_config())
    return LightFrame(
        visible=frame.visible,
        x=clamped.x,
        y=clamped.y,
        width=clamped.width,
        height=clamped.height,
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
