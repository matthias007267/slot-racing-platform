"""Drawable geometry of one part, in local millimetres.

The outline and the joints stay the snap and hit geometry. This module only
describes what to paint: the roadway, the slots, the centre line and the long
edges. Inner and outer shoulders are separate layers and stay empty until a
later change adds them. Nothing here assumes that ink may not lie outside the
roadway.

A part with stored slot paths uses those. Every other part gets the ordinary
lanes from its measures, so an older library row still draws.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from functools import lru_cache
from itertools import pairwise

from slot_racing.modules.track_planner.parts import (
    BORDER,
    CROSSING,
    CURVE,
    LANE_CHANGE,
    LANE_PITCH_MM,
    PATH_CENTER,
    PATH_SLOT,
    PITLANE,
    SPAN_ARC,
    SPAN_LINE,
    SPECIAL,
    SUPPORT,
    SWITCH,
    PartSpec,
    SlotPath,
    SlotSpan,
    curve_lane_radius,
    lane_change_paths,
    lane_offset_mm,
    track_width,
)

# Dashes are a fraction of the lane pitch so they scale with the part, not with
# a screen pixel. Both ends keep half a gap, so two joined parts meet on a gap.
CENTER_DASH_MM = LANE_PITCH_MM * 0.26
CENTER_GAP_MM = LANE_PITCH_MM * 0.16


@dataclass(frozen=True, slots=True)
class RoadArc:
    """Annular sector. The circle centre is the local origin, as on a curve."""

    radius_mm: float
    angle_deg: float
    width_mm: float

    @property
    def outer_mm(self) -> float:
        return self.radius_mm + self.width_mm / 2.0

    @property
    def inner_mm(self) -> float:
        return max(self.radius_mm - self.width_mm / 2.0, 0.0)

    @property
    def start_deg(self) -> float:
        return -self.angle_deg / 2.0


@dataclass(frozen=True, slots=True)
class TrackFigure:
    """Everything the painter needs. Shoulders are reserved and normally empty."""

    roadway: tuple[tuple[float, float], ...]
    road_arc: RoadArc | None
    slots: tuple[SlotPath, ...]
    centerlines: tuple[SlotPath, ...]
    edges: tuple[SlotPath, ...]
    outer_shoulder: tuple[tuple[float, float], ...] = ()
    inner_shoulder: tuple[tuple[float, float], ...] = ()


def track_figure(spec: PartSpec) -> TrackFigure:
    """Geometry for ``spec``. The same definition returns the same object."""
    return _cached_figure(spec)


@lru_cache(maxsize=512)
def _cached_figure(spec: PartSpec) -> TrackFigure:
    return _build_figure(spec)


def _build_figure(spec: PartSpec) -> TrackFigure:
    if spec.category in {BORDER, SUPPORT}:
        return _strip(spec)
    if _is_arc(spec):
        return _arc_figure(spec)
    if spec.category == CROSSING:
        return _crossing(spec)
    slots, centers = _slots_and_centers(spec)
    length = spec.length_mm or 0.0
    width = spec.width_mm or track_width(spec.lane_count)
    return TrackFigure(
        roadway=spec.outline,
        road_arc=None,
        slots=slots,
        centerlines=_dashed(centers),
        edges=_long_edges(length, width, _side_mouths(spec)),
    )


def _strip(spec: PartSpec) -> TrackFigure:
    """A border or support piece is not a rail. No slots are invented for it."""
    return TrackFigure(
        roadway=spec.outline,
        road_arc=None,
        slots=(),
        centerlines=(),
        edges=_outline_edges(spec.outline),
    )


def _is_arc(spec: PartSpec) -> bool:
    return (
        spec.category in {CURVE, SPECIAL}
        and spec.radius_mm is not None
        and spec.angle_deg is not None
        and spec.radius_mm > 0.0
        and spec.angle_deg > 0.0
    )


def _arc_figure(spec: PartSpec) -> TrackFigure:
    radius = float(spec.radius_mm or 0.0)
    angle = float(spec.angle_deg or 0.0)
    width = spec.width_mm or track_width(spec.lane_count)
    road = RoadArc(radius, angle, width)
    slots = tuple(
        SlotPath(
            (
                SlotSpan.arc(
                    0.0,
                    0.0,
                    curve_lane_radius(radius, index, spec.lane_count),
                    road.start_deg,
                    angle,
                ),
            )
        )
        for index in range(spec.lane_count)
    )
    centers: tuple[SlotPath, ...] = ()
    if spec.lane_count >= 2:
        radii = [
            curve_lane_radius(radius, index, spec.lane_count) for index in range(spec.lane_count)
        ]
        centers = tuple(
            SlotPath(
                (SlotSpan.arc(0.0, 0.0, (left + right) / 2.0, road.start_deg, angle),),
                PATH_CENTER,
            )
            for left, right in pairwise(radii)
        )
    edges = (
        SlotPath((SlotSpan.arc(0.0, 0.0, road.outer_mm, road.start_deg, angle),)),
        SlotPath((SlotSpan.arc(0.0, 0.0, road.inner_mm, road.start_deg, angle),)),
    )
    return TrackFigure(
        roadway=spec.outline,
        road_arc=road,
        slots=slots,
        centerlines=_dashed(centers),
        edges=edges,
    )


def _crossing(spec: PartSpec) -> TrackFigure:
    side = spec.length_mm or 0.0
    half = side / 2.0
    slots: list[SlotPath] = []
    centers: list[SlotPath] = []
    for index in range(spec.lane_count):
        offset = lane_offset_mm(index, spec.lane_count)
        slots.append(SlotPath((SlotSpan.line(-half, offset, half, offset),)))
        slots.append(SlotPath((SlotSpan.line(offset, -half, offset, half),)))
    if spec.lane_count >= 2:
        centers.append(SlotPath((SlotSpan.line(-half, 0.0, half, 0.0),), PATH_CENTER))
        centers.append(SlotPath((SlotSpan.line(0.0, -half, 0.0, half),), PATH_CENTER))
    return TrackFigure(
        roadway=spec.outline,
        road_arc=None,
        slots=tuple(slots),
        centerlines=_dashed(tuple(centers)),
        edges=_outline_edges(spec.outline),
    )


def _slots_and_centers(spec: PartSpec) -> tuple[tuple[SlotPath, ...], tuple[SlotPath, ...]]:
    if spec.slot_paths:
        slots = tuple(path for path in spec.slot_paths if path.kind == PATH_SLOT)
        centers = tuple(path for path in spec.slot_paths if path.kind == PATH_CENTER)
        return slots, centers
    return _derived_slots(spec)


def _derived_slots(spec: PartSpec) -> tuple[tuple[SlotPath, ...], tuple[SlotPath, ...]]:
    length = spec.length_mm or 0.0
    if spec.category == SWITCH:
        return _split(lane_change_paths(length, "left"))
    if spec.category == LANE_CHANGE:
        return _split(lane_change_paths(length, "both"))
    if spec.category == PITLANE:
        return _pit_paths(spec)
    count = spec.lane_count
    slots = tuple(
        SlotPath(
            (
                SlotSpan.line(
                    -length / 2.0,
                    lane_offset_mm(index, count),
                    length / 2.0,
                    lane_offset_mm(index, count),
                ),
            )
        )
        for index in range(count)
    )
    return slots, _centers_between(length, count)


def _pit_paths(spec: PartSpec) -> tuple[tuple[SlotPath, ...], tuple[SlotPath, ...]]:
    length = spec.length_mm or 0.0
    axial = [
        connector
        for connector in spec.connectors
        if abs(connector.y_mm) < 1.0 and abs(connector.direction_deg % 180.0) < 1.0
    ]
    count = len(axial[0].lanes) if axial else spec.lane_count
    slots = [
        SlotPath(
            (
                SlotSpan.line(
                    -length / 2.0,
                    lane_offset_mm(index, count),
                    length / 2.0,
                    lane_offset_mm(index, count),
                ),
            )
        )
        for index in range(count)
    ]
    slots.extend(_spurs(spec, count))
    return tuple(slots), _centers_between(length, count)


def _closest(values: list[float], target: float) -> float:
    best = values[0]
    for value in values[1:]:
        if abs(value - target) < abs(best - target):
            best = value
    return best


def _spurs(spec: PartSpec, lane_count: int) -> tuple[SlotPath, ...]:
    """A side joint gets a quarter-circle groove from the nearest through lane."""
    if spec.length_mm is None:
        return ()
    half = spec.length_mm / 2.0
    offsets = [lane_offset_mm(index, lane_count) for index in range(lane_count)]
    spurs: list[SlotPath] = []
    for connector in spec.connectors:
        on_end = abs(abs(connector.x_mm) - half) < 1.5 and abs(connector.y_mm) < 1.5
        if on_end or not offsets:
            continue
        offset = _closest(offsets, connector.y_mm)
        radius = abs(connector.y_mm - offset)
        if radius < 1.0:
            continue
        if connector.y_mm > offset:
            start_deg, sweep = -90.0, 90.0
        else:
            start_deg, sweep = 90.0, -90.0
        spurs.append(
            SlotPath(
                (
                    SlotSpan.arc(
                        connector.x_mm - radius,
                        connector.y_mm,
                        radius,
                        start_deg,
                        sweep,
                    ),
                )
            )
        )
    return tuple(spurs)


def _centers_between(length: float, lane_count: int) -> tuple[SlotPath, ...]:
    if lane_count < 2:
        return ()
    half = length / 2.0
    offsets = [lane_offset_mm(index, lane_count) for index in range(lane_count)]
    return tuple(
        SlotPath(
            (SlotSpan.line(-half, (left + right) / 2.0, half, (left + right) / 2.0),),
            PATH_CENTER,
        )
        for left, right in pairwise(offsets)
    )


def _split(paths: tuple[SlotPath, ...]) -> tuple[tuple[SlotPath, ...], tuple[SlotPath, ...]]:
    slots = tuple(path for path in paths if path.kind == PATH_SLOT)
    centers = tuple(path for path in paths if path.kind == PATH_CENTER)
    return slots, centers


def _side_mouths(spec: PartSpec) -> tuple[tuple[float, float, float], ...]:
    """Gaps in a long edge where a side joint leaves the roadway. ``(y, x, half gap)``."""
    if spec.length_mm is None or spec.width_mm is None:
        return ()
    half_l = spec.length_mm / 2.0
    half_w = spec.width_mm / 2.0
    mouths: list[tuple[float, float, float]] = []
    for connector in spec.connectors:
        on_side = abs(abs(connector.y_mm) - half_w) < 1.5
        interior = abs(abs(connector.x_mm) - half_l) > 1.5
        if on_side and interior:
            mouths.append((connector.y_mm, connector.x_mm, track_width(len(connector.lanes)) / 2.0))
    return tuple(mouths)


def _long_edges(
    length: float, width: float, mouths: tuple[tuple[float, float, float], ...]
) -> tuple[SlotPath, ...]:
    """The two long boundaries. Joint faces are not stroked, so joined parts stay open."""
    if length <= 0.0 or width <= 0.0:
        return ()
    half_l = length / 2.0
    half_w = width / 2.0
    paths: list[SlotPath] = []
    for y in (-half_w, half_w):
        cuts = sorted(
            (center - gap, center + gap) for edge_y, center, gap in mouths if abs(edge_y - y) < 1.0
        )
        cursor = -half_l
        spans: list[SlotSpan] = []
        for left, right in cuts:
            left = max(left, -half_l)
            right = min(right, half_l)
            if left - cursor > 1.0:
                spans.append(SlotSpan.line(cursor, y, left, y))
            cursor = max(cursor, right)
        if half_l - cursor > 1.0:
            spans.append(SlotSpan.line(cursor, y, half_l, y))
        if spans:
            paths.append(SlotPath(tuple(spans)))
    return tuple(paths)


def _outline_edges(outline: tuple[tuple[float, float], ...]) -> tuple[SlotPath, ...]:
    if len(outline) < 2:
        return ()
    spans = [
        SlotSpan.line(x0, y0, x1, y1)
        for (x0, y0), (x1, y1) in zip(outline, outline[1:] + outline[:1], strict=True)
    ]
    return (SlotPath(tuple(spans)),)


def _dashed(paths: tuple[SlotPath, ...]) -> tuple[SlotPath, ...]:
    dashed: list[SlotPath] = []
    for path in paths:
        spans: list[SlotSpan] = []
        for span in path.spans:
            spans.extend(_dash_span(span))
        if spans:
            dashed.append(SlotPath(tuple(spans), PATH_CENTER))
    return tuple(dashed)


def _dash_span(span: SlotSpan) -> tuple[SlotSpan, ...]:
    if span.kind == SPAN_ARC:
        length = abs(math.radians(span.sweep_deg)) * span.radius_mm
        pieces = _dash_fractions(length, CENTER_DASH_MM, CENTER_GAP_MM)
        return tuple(
            SlotSpan.arc(
                span.x0,
                span.y0,
                span.radius_mm,
                span.start_deg + span.sweep_deg * start,
                span.sweep_deg * (end - start),
            )
            for start, end in pieces
        )
    if span.kind != SPAN_LINE:
        return ()
    length = math.hypot(span.x1 - span.x0, span.y1 - span.y0)
    pieces = _dash_fractions(length, CENTER_DASH_MM, CENTER_GAP_MM)
    return tuple(
        SlotSpan.line(
            span.x0 + (span.x1 - span.x0) * start,
            span.y0 + (span.y1 - span.y0) * start,
            span.x0 + (span.x1 - span.x0) * end,
            span.y0 + (span.y1 - span.y0) * end,
        )
        for start, end in pieces
    )


def _dash_fractions(length: float, dash: float, gap: float) -> tuple[tuple[float, float], ...]:
    """Even dashes with the same margin at both ends. The margin is about half a gap."""
    if length <= 1.0:
        return ()
    usable = length - gap
    if usable < dash * 0.45:
        pad = 0.18
        return ((pad, 1.0 - pad),)
    count = max(1, round(usable / (dash + gap)))
    while count > 1 and (count * dash + (count - 1) * gap) > length:
        count -= 1
    used = count * dash + (count - 1) * gap
    if used > length:
        pad = 0.22
        return ((pad, 1.0 - pad),)
    margin = (length - used) / 2.0
    pieces: list[tuple[float, float]] = []
    cursor = margin
    for _ in range(count):
        pieces.append((cursor / length, (cursor + dash) / length))
        cursor += dash + gap
    return tuple(pieces)
