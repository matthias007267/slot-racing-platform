"""Configurable race HUD. Geometry only: no race timing and no Qt.

Widgets are placed in fractions of the display, so one layout fits a window, a
television or a later fullscreen view. Pixels are computed when the view is drawn.
The document lives in the global settings table under :data:`HUD_CONFIGURATION_KEY`.
A missing or broken document becomes the standard layout instead of crashing.
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
HUD_VERSION = 1
MIN_SPAN = 0.06
"""Smallest width or height, as a fraction of the display, so a widget stays readable."""

RACE_HEADER = "race_header"
RACE_CLOCK = "race_clock"
LAP_PROGRESS = "lap_progress"
LIVE_RANKING = "live_ranking"
DRIVER_HIGHLIGHT = "driver_highlight"
LAST_LAP = "last_lap"
BEST_LAP = "best_lap"
RACE_STATUS = "race_status"
RACE_MESSAGE = "race_message"
RACE_CONTROLS = "race_controls"

WIDGET_IDS: tuple[str, ...] = (
    RACE_HEADER,
    RACE_CLOCK,
    LAP_PROGRESS,
    LIVE_RANKING,
    DRIVER_HIGHLIGHT,
    LAST_LAP,
    BEST_LAP,
    RACE_STATUS,
    RACE_MESSAGE,
    RACE_CONTROLS,
)


@dataclass(frozen=True, slots=True)
class HudCanvas:
    """Reference aspect of the layout, not a pixel resolution."""

    width: int = 16
    height: int = 9


@dataclass(frozen=True, slots=True)
class HudWidgetConfig:
    """One element. ``x`` and ``y`` are the top-left corner in fractions of the display."""

    id: str
    visible: bool
    x: float
    y: float
    width: float
    height: float
    z_index: int = 0


@dataclass(frozen=True, slots=True)
class HudConfiguration:
    """The one active layout. A later version can hold several named layouts."""

    version: int
    canvas: HudCanvas
    widgets: tuple[HudWidgetConfig, ...]
    name: str = "standard"

    def widget(self, widget_id: str) -> HudWidgetConfig | None:
        return next((item for item in self.widgets if item.id == widget_id), None)


@dataclass(frozen=True, slots=True)
class PixelRect:
    x: int
    y: int
    width: int
    height: int


def default_hud_configuration() -> HudConfiguration:
    """16:9 racing layout. Header and clock on top, ranking beside progress and the
    highlighted driver, last and best lap underneath, messages and controls along the bottom.
    """
    return HudConfiguration(
        version=HUD_VERSION,
        canvas=HudCanvas(width=16, height=9),
        name="standard",
        widgets=(
            _box(RACE_HEADER, 0.02, 0.02, 0.62, 0.14, z_index=1),
            _box(RACE_CLOCK, 0.66, 0.02, 0.32, 0.14, z_index=1),
            _box(LIVE_RANKING, 0.02, 0.18, 0.62, 0.46, z_index=1),
            _box(LAP_PROGRESS, 0.66, 0.18, 0.32, 0.20, z_index=1),
            _box(RACE_STATUS, 0.66, 0.40, 0.32, 0.12, z_index=1),
            _box(DRIVER_HIGHLIGHT, 0.66, 0.54, 0.32, 0.26, z_index=2),
            _box(LAST_LAP, 0.02, 0.66, 0.30, 0.14, z_index=1),
            _box(BEST_LAP, 0.34, 0.66, 0.30, 0.14, z_index=1),
            _box(RACE_MESSAGE, 0.02, 0.82, 0.46, 0.16, z_index=1),
            _box(RACE_CONTROLS, 0.50, 0.82, 0.48, 0.16, z_index=3),
        ),
    )


def clamp_widget(widget: HudWidgetConfig) -> HudWidgetConfig:
    """Keep the rectangle inside the display and at least :data:`MIN_SPAN` on each side."""
    width = _clamp_span(widget.width)
    height = _clamp_span(widget.height)
    return replace(
        widget,
        x=_clamp_origin(widget.x, width),
        y=_clamp_origin(widget.y, height),
        width=width,
        height=height,
    )


def stacking_order(
    widgets: tuple[HudWidgetConfig, ...] | list[HudWidgetConfig],
) -> list[HudWidgetConfig]:
    """Low ``z_index`` first, so the last widget is painted on top. Ids break ties."""
    return sorted(widgets, key=lambda item: (item.z_index, item.id))


def to_pixels(widget: HudWidgetConfig, canvas_width: int, canvas_height: int) -> PixelRect:
    """Map a normalized rectangle onto a display. The result stays inside the canvas."""
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


def with_widget(config: HudConfiguration, widget: HudWidgetConfig) -> HudConfiguration:
    """Replace the element with this id, or append it. The rectangle is clamped."""
    updated = clamp_widget(widget)
    if config.widget(updated.id) is None:
        widgets = (*config.widgets, updated)
    else:
        widgets = tuple(updated if item.id == updated.id else item for item in config.widgets)
    return replace(config, widgets=widgets)


def to_document(config: HudConfiguration) -> dict[str, Any]:
    return {
        "version": config.version,
        "name": config.name,
        "canvas": {"width": config.canvas.width, "height": config.canvas.height},
        "widgets": [
            {
                "id": item.id,
                "visible": item.visible,
                "x": item.x,
                "y": item.y,
                "width": item.width,
                "height": item.height,
                "z_index": item.z_index,
            }
            for item in config.widgets
        ],
    }


def coerce(payload: object) -> HudConfiguration:
    """Read a stored document. Raises ``ValueError`` when the document cannot be used.

    A single broken element is skipped. Missing known elements are filled from the standard
    layout. An unknown version is rejected so the caller can fall back to that layout.
    """
    if not isinstance(payload, dict):
        raise ValueError("HUD configuration must be an object")
    if payload.get("version") != HUD_VERSION:
        raise ValueError("unsupported HUD configuration version")
    canvas = _canvas(payload.get("canvas"))
    raw_widgets = payload.get("widgets")
    if not isinstance(raw_widgets, list):
        raise ValueError("HUD widgets must be a list")
    parsed: list[HudWidgetConfig] = []
    seen: set[str] = set()
    for item in raw_widgets:
        widget = _widget(item)
        if widget is None or widget.id in seen:
            continue
        seen.add(widget.id)
        parsed.append(clamp_widget(widget))
    for default in default_hud_configuration().widgets:
        if default.id not in seen:
            parsed.append(default)
    name = payload.get("name")
    return HudConfiguration(
        version=HUD_VERSION,
        canvas=canvas,
        widgets=tuple(parsed),
        name=name if isinstance(name, str) and name else "standard",
    )


class HudConfigurationStore:
    """Load and save the active HUD layout in the settings table."""

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
        """The saved layout, or the standard layout when nothing usable is stored."""
        with self._database.session() as session:
            row = session.get(Setting, HUD_CONFIGURATION_KEY)
            if row is None:
                return default_hud_configuration()
            payload = row.value
        try:
            return coerce(payload)
        except ValueError:
            logger.warning("Stored race HUD configuration is invalid; using the standard layout")
            return default_hud_configuration()

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
        """Replace the saved layout with the standard layout."""
        return self.save(default_hud_configuration())

    def _notify(self, configuration: HudConfiguration) -> None:
        for listener in list(self._listeners):
            try:
                listener(configuration)
            except Exception:
                logger.exception("HUD configuration listener failed")


def _box(
    widget_id: str, x: float, y: float, width: float, height: float, *, z_index: int
) -> HudWidgetConfig:
    return HudWidgetConfig(
        id=widget_id, visible=True, x=x, y=y, width=width, height=height, z_index=z_index
    )


def _clamp_span(value: float) -> float:
    if not math.isfinite(value):
        return MIN_SPAN
    return min(1.0, max(MIN_SPAN, value))


def _clamp_origin(value: float, span: float) -> float:
    if not math.isfinite(value):
        return 0.0
    return min(max(0.0, value), 1.0 - span)


def _canvas(value: object) -> HudCanvas:
    if not isinstance(value, dict):
        return HudCanvas()
    return HudCanvas(
        width=_positive_int(value.get("width"), 16),
        height=_positive_int(value.get("height"), 9),
    )


def _positive_int(value: object, default: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        return default
    return value


def _widget(value: object) -> HudWidgetConfig | None:
    if not isinstance(value, dict):
        return None
    widget_id = value.get("id")
    if not isinstance(widget_id, str) or not widget_id:
        return None
    x = _number(value.get("x"))
    y = _number(value.get("y"))
    width = _number(value.get("width"))
    height = _number(value.get("height"))
    if x is None or y is None or width is None or height is None:
        return None
    visible = value.get("visible")
    z_index = value.get("z_index", 0)
    if isinstance(z_index, bool) or not isinstance(z_index, int):
        z_index = 0
    return HudWidgetConfig(
        id=widget_id,
        visible=visible if isinstance(visible, bool) else True,
        x=x,
        y=y,
        width=width,
        height=height,
        z_index=z_index,
    )


def _number(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)
