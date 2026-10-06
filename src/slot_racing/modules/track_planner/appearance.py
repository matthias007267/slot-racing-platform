"""Colours for the track painter. One palette, used by the plan and the library.

Colour coding tints the roadway only. Slots, the centre line and the edges
keep the colours below in both modes. Shoulder ink is reserved for a later
change; no part supplies shoulder geometry yet.
"""

from __future__ import annotations

from slot_racing.modules.track_planner.parts import CURVE, LANE_PITCH_MM, PartSpec

# Stroke widths in millimetres, as a fraction of the lane pitch. They scale with
# the part. The painter may thicken a stroke so it stays visible when zoomed out.
SLOT_RIM_MM = LANE_PITCH_MM * 0.16
SLOT_CORE_MM = LANE_PITCH_MM * 0.09
CENTER_WIDTH_MM = LANE_PITCH_MM * 0.045
EDGE_WIDTH_MM = LANE_PITCH_MM * 0.025
START_BAND_MM = LANE_PITCH_MM * 0.16
START_SQUARE_MM = LANE_PITCH_MM * 0.25

# The ordinary rail: dark anthracite, clearly separate from the canvas, not pure black.
ROADWAY = "#3E4652"
ROADWAY_EDGE = "#6B7482"
# The groove is darker than the roadway. The rim stays darker too, so the slot
# does not read as a light stripe.
SLOT_RIM = "#232830"
SLOT_CORE = "#101318"
CENTER_LINE = "#E4E8EE"
START_LIGHT = "#F3F5F7"
START_DARK = "#16181C"
# Used only when a future shoulder layer is present.
SHOULDER = "#3E4650"

# Muted, distinct tints. Radii follow the catalogue centre lines.
_CURVE_RADII = (
    (300.0, "r1"),
    (500.0, "r2"),
    (700.0, "r3"),
    (900.0, "r4"),
)

CODED: dict[str, str] = {
    "straight": "#6B7C86",
    "r1": "#C45C48",
    "r2": "#D4A24C",
    "r3": "#3E8F6A",
    "r4": "#3D74A6",
    "curve": "#4E8C9A",
    "switch": "#8E62A0",
    "lane_change": "#C4843E",
    "crossing": "#B56B58",
    "pitlane": "#2E8A78",
    "special": "#8A7348",
    "border": "#7A7062",
    "support": "#5C656E",
}


def curve_class(radius_mm: float) -> str:
    """``r1`` … ``r4`` for the catalogue radii, otherwise a generic curve."""
    for radius, name in _CURVE_RADII:
        if abs(radius_mm - radius) <= 1.0:
            return name
    return "curve"


def roadway_fill(spec: PartSpec, *, coded: bool) -> str:
    """Roadway tint. Coding off keeps every rail the same anthracite."""
    if spec.category in {"border", "support"}:
        return CODED[spec.category]
    if not coded:
        return ROADWAY
    if spec.category == CURVE:
        return CODED[curve_class(spec.radius_mm or 0.0)]
    return CODED.get(spec.category, CODED["special"])
