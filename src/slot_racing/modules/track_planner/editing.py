"""Qt-free edits of one part: grooves, centre lines, contours, side edges and rail joints.

Every function takes a :class:`PartSpec` and returns a new one, so the editor history is a list of
frozen definitions. The geometry itself (lines, arcs, cubic Bézier curves, splitting, closest
point, length) lives in :mod:`geometry`. This module only decides which stored span changes.

Layers address the stored lists of the definition:

``groove``  ``slot_paths`` of kind ``slot``. One path per numbered lane.
``center``  ``slot_paths`` of kind ``center``. The dashed line between two lanes.
``contour`` ``contours``. Drawn edge strokes; a closed one also defines the filled ``outline``.
``edge``    ``border_edges``. A side a border may occupy. Not a rail joint.
``attach``  ``attachment.edge``. The mating edge of a border, in the host's frame.

Rail joints stay ``track`` connectors. Nothing here makes a border edge a rail joint.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass, replace
from itertools import pairwise

from slot_racing.core.domain.lanes import MAX_LANE_COUNT
from slot_racing.core.errors import ValidationError
from slot_racing.modules.track_planner.attachment import (
    HOST_CURVE_BANKED,
    HOST_CURVE_FLAT,
    HOST_CUSTOM,
    HOST_STRAIGHT,
    STRIP_WIDTH_MM,
    placed_outline,
)
from slot_racing.modules.track_planner.figure import derived_slot_paths
from slot_racing.modules.track_planner.geometry import (
    POSITION_GAP_MM,
    ArcSegment,
    CubicBezier,
    GeometryPath,
    LineSegment,
    Segment,
    closest_on_segment,
    split_segment,
)
from slot_racing.modules.track_planner.parametric import draft_from_spec
from slot_racing.modules.track_planner.parts import (
    BORDER,
    BORDER_SLOTS,
    CURVE,
    LANE_PITCH_MM,
    PATH_CENTER,
    PATH_CONTOUR,
    PATH_SLOT,
    SPAN_ARC,
    SPAN_BEZIER,
    SPAN_LINE,
    SPECIAL,
    STRAIGHT,
    TRACK,
    AttachmentProfile,
    BorderEdge,
    ConnectorSpec,
    PartSpec,
    SlotPath,
    SlotSpan,
    as_segment,
    driving_paths,
    track_width,
    validate_part_geometry,
)

LAYER_GROOVE = "groove"
LAYER_CENTER = "center"
LAYER_CONTOUR = "contour"
LAYER_EDGE = "edge"
LAYER_ATTACH = "attach"
LAYERS = (LAYER_GROOVE, LAYER_CENTER, LAYER_CONTOUR, LAYER_EDGE, LAYER_ATTACH)

TANGENT_KINK = "kink"
TANGENT_SMOOTH = "smooth"
TANGENT_SYMMETRIC = "symmetric"
TANGENT_MODES = (TANGENT_KINK, TANGENT_SMOOTH, TANGENT_SYMMETRIC)

# A groove must end this close to its rail joint plane, and run this close to its axis.
JOINT_PLANE_MM = 1.0
JOINT_DIRECTION_DEG = 5.0
_ARC_STEP_DEG = 6.0
_BEZIER_STEPS = 24
_MIN_SWEEP_DEG = 0.01

# Why a part cannot be drawn on. ``""`` means it can.
SUPPORT_OK = ""
SUPPORT_CORRUPT = "corrupt"
SUPPORT_CATEGORY = "category"
SUPPORT_BRANCH = "branch"
SUPPORT_STANDARD_BORDER = "standard_border"
SUPPORT_END_PIECE = "end_piece"


@dataclass(frozen=True, slots=True)
class PathRef:
    """One stored path. ``index`` counts inside the list the layer names."""

    layer: str
    index: int = 0


@dataclass(frozen=True, slots=True)
class PointRef:
    """One handle of one segment: ``start``/``end``, ``center``/``mid`` or ``p0``..``p3``."""

    path: PathRef
    segment: int
    handle: str


@dataclass(frozen=True, slots=True)
class EditIssue:
    """A problem that must be fixed before the part may be saved."""

    key: str
    where: str = ""


# --------------------------------------------------------------------------------------------
# Spans and segments


def span_of(segment: Segment) -> SlotSpan:
    if isinstance(segment, ArcSegment):
        return SlotSpan.arc(
            segment.cx, segment.cy, segment.radius_mm, segment.start_deg, segment.sweep_deg
        )
    if isinstance(segment, CubicBezier):
        return SlotSpan.bezier(
            segment.p0x,
            segment.p0y,
            segment.p1x,
            segment.p1y,
            segment.p2x,
            segment.p2y,
            segment.p3x,
            segment.p3y,
        )
    return SlotSpan.line(segment.x0, segment.y0, segment.x1, segment.y1)


def handle_points(span: SlotSpan) -> dict[str, tuple[float, float]]:
    """Every handle of ``span`` in the part's own millimetres."""
    segment = as_segment(span)
    if isinstance(segment, ArcSegment):
        return {
            "center": (segment.cx, segment.cy),
            "start": segment.start(),
            "mid": segment.point_at(0.5),
            "end": segment.end(),
        }
    if isinstance(segment, CubicBezier):
        p0, p1, p2, p3 = segment.control_points()
        return {"p0": p0, "p1": p1, "p2": p2, "p3": p3}
    return {"start": segment.start(), "end": segment.end()}


def node_handles(span: SlotSpan) -> tuple[str, str]:
    """The handles that are the segment's two ends."""
    if span.kind == SPAN_BEZIER:
        return ("p0", "p3")
    return ("start", "end")


def span_start(span: SlotSpan) -> tuple[float, float]:
    return as_segment(span).start()


def span_end(span: SlotSpan) -> tuple[float, float]:
    return as_segment(span).end()


def span_length_mm(span: SlotSpan) -> float:
    return as_segment(span).length_mm()


def spans_length_mm(spans: Sequence[SlotSpan]) -> float:
    return sum(span_length_mm(span) for span in spans)


def is_closed(spans: Sequence[SlotSpan]) -> bool:
    if len(spans) < 2:
        return False
    start = span_start(spans[0])
    end = span_end(spans[-1])
    return math.hypot(start[0] - end[0], start[1] - end[1]) <= POSITION_GAP_MM


def move_handle(span: SlotSpan, handle: str, x_mm: float, y_mm: float) -> SlotSpan:
    """Move one handle. An arc keeps its radius while an end handle turns it."""
    _finite(x_mm, y_mm)
    segment = as_segment(span)
    if isinstance(segment, ArcSegment):
        return _move_arc_handle(segment, handle, x_mm, y_mm)
    if isinstance(segment, CubicBezier):
        points = list(segment.control_points())
        index = {"p0": 0, "p1": 1, "p2": 2, "p3": 3}.get(handle)
        if index is None:
            raise ValidationError("error.planner.geometry")
        points[index] = (x_mm, y_mm)
        return SlotSpan.bezier(*(value for point in points for value in point))
    if handle == "start":
        return SlotSpan.line(x_mm, y_mm, segment.x1, segment.y1)
    if handle == "end":
        return SlotSpan.line(segment.x0, segment.y0, x_mm, y_mm)
    raise ValidationError("error.planner.geometry")


def _move_arc_handle(arc: ArcSegment, handle: str, x_mm: float, y_mm: float) -> SlotSpan:
    if handle == "center":
        return span_of(arc.translated(x_mm - arc.cx, y_mm - arc.cy))
    dx = x_mm - arc.cx
    dy = y_mm - arc.cy
    if handle == "mid":
        radius = math.hypot(dx, dy)
        if radius <= 0.0:
            raise ValidationError("error.planner.geometry")
        return SlotSpan.arc(arc.cx, arc.cy, radius, arc.start_deg, arc.sweep_deg)
    if dx == 0.0 and dy == 0.0:
        raise ValidationError("error.planner.geometry")
    angle = math.degrees(math.atan2(dy, dx))
    far = arc.start_deg + arc.sweep_deg
    if handle == "start":
        start = angle
        sweep = _directed(far - start, arc.sweep_deg)
    elif handle == "end":
        start = arc.start_deg
        sweep = _directed(angle - start, arc.sweep_deg)
    else:
        raise ValidationError("error.planner.geometry")
    if abs(sweep) < _MIN_SWEEP_DEG:
        raise ValidationError("error.planner.geometry")
    return SlotSpan.arc(arc.cx, arc.cy, arc.radius_mm, start, sweep)


def _directed(delta_deg: float, like: float) -> float:
    """``delta_deg`` folded into the same turning direction as ``like``, up to a full turn."""
    if like >= 0.0:
        folded = delta_deg % 360.0
        return folded if folded > 0.0 else 360.0 if abs(delta_deg) > 1.0 else 0.0
    folded = -((-delta_deg) % 360.0)
    return folded if folded < 0.0 else -360.0 if abs(delta_deg) > 1.0 else 0.0


def translate_span(span: SlotSpan, dx: float, dy: float) -> SlotSpan:
    return span_of(as_segment(span).translated(dx, dy))


def rotate_span(
    span: SlotSpan, rotation_deg: float, origin: tuple[float, float] = (0.0, 0.0)
) -> SlotSpan:
    return span_of(as_segment(span).rotated(rotation_deg, origin))


def arc_through(
    start: tuple[float, float], via: tuple[float, float], end: tuple[float, float]
) -> SlotSpan | None:
    """The circular arc that starts at ``start``, passes ``via`` and ends at ``end``.

    ``None`` when the three points lie on one line, so no circle exists.
    """
    ax, ay = start
    bx, by = via
    cx, cy = end
    determinant = 2.0 * (ax * (by - cy) + bx * (cy - ay) + cx * (ay - by))
    if abs(determinant) < 1e-9:
        return None
    a2 = ax * ax + ay * ay
    b2 = bx * bx + by * by
    c2 = cx * cx + cy * cy
    centre_x = (a2 * (by - cy) + b2 * (cy - ay) + c2 * (ay - by)) / determinant
    centre_y = (a2 * (cx - bx) + b2 * (ax - cx) + c2 * (bx - ax)) / determinant
    radius = math.hypot(ax - centre_x, ay - centre_y)
    start_deg = math.degrees(math.atan2(ay - centre_y, ax - centre_x))
    via_deg = math.degrees(math.atan2(by - centre_y, bx - centre_x))
    end_deg = math.degrees(math.atan2(cy - centre_y, cx - centre_x))
    forward = (end_deg - start_deg) % 360.0
    via_forward = (via_deg - start_deg) % 360.0
    sweep = forward if via_forward <= forward else forward - 360.0
    if abs(sweep) < _MIN_SWEEP_DEG:
        return None
    return SlotSpan.arc(centre_x, centre_y, radius, start_deg, sweep)


def bezier_between(
    start: tuple[float, float],
    end: tuple[float, float],
    start_tangent_deg: float | None = None,
) -> SlotSpan:
    """A cubic with its handles a third of the chord along the ends.

    With ``start_tangent_deg`` the first handle continues that heading, so the curve joins the
    previous segment without a kink.
    """
    chord = math.hypot(end[0] - start[0], end[1] - start[1])
    if chord <= 1e-9:
        raise ValidationError("error.planner.geometry")
    third = chord / 3.0
    if start_tangent_deg is None:
        first = (
            start[0] + (end[0] - start[0]) / 3.0,
            start[1] + (end[1] - start[1]) / 3.0,
        )
    else:
        radians = math.radians(start_tangent_deg)
        first = (start[0] + math.cos(radians) * third, start[1] + math.sin(radians) * third)
    second = (
        start[0] + (end[0] - start[0]) * 2.0 / 3.0,
        start[1] + (end[1] - start[1]) * 2.0 / 3.0,
    )
    return SlotSpan.bezier(start[0], start[1], *first, *second, end[0], end[1])


# --------------------------------------------------------------------------------------------
# Paths


def explicit(spec: PartSpec) -> PartSpec:
    """The same part with stored, numbered grooves. Derived straights and curves are written out.

    A switch or crossing is never passed here: the editor refuses it first.
    """
    if spec.attachment is not None:
        return spec
    if spec.slot_paths:
        return replace(spec, slot_paths=_numbered(spec.slot_paths))
    slots, centers = derived_slot_paths(spec)
    return replace(spec, slot_paths=_numbered(slots) + centers)


def _numbered(paths: tuple[SlotPath, ...]) -> tuple[SlotPath, ...]:
    result: list[SlotPath] = []
    grooves = 0
    for path in paths:
        if path.kind != PATH_SLOT:
            result.append(path)
            continue
        lane = path.lane if path.lane is not None else grooves + 1
        grooves += 1
        result.append(path if path.lane == lane else replace(path, lane=lane))
    return tuple(result)


def path_refs(spec: PartSpec) -> tuple[PathRef, ...]:
    refs: list[PathRef] = []
    for index, path in enumerate(spec.slot_paths):
        refs.append(PathRef(LAYER_GROOVE if path.kind == PATH_SLOT else LAYER_CENTER, index))
    refs.extend(PathRef(LAYER_CONTOUR, index) for index in range(len(spec.contours)))
    refs.extend(PathRef(LAYER_EDGE, index) for index in range(len(spec.border_edges)))
    if spec.attachment is not None and spec.attachment.edge:
        refs.append(PathRef(LAYER_ATTACH, 0))
    return tuple(refs)


def spans_of(spec: PartSpec, ref: PathRef) -> tuple[SlotSpan, ...]:
    try:
        if ref.layer in {LAYER_GROOVE, LAYER_CENTER}:
            return spec.slot_paths[ref.index].spans
        if ref.layer == LAYER_CONTOUR:
            return spec.contours[ref.index].spans
        if ref.layer == LAYER_EDGE:
            return spec.border_edges[ref.index].spans
        if ref.layer == LAYER_ATTACH and spec.attachment is not None:
            return spec.attachment.edge
    except IndexError:
        pass
    raise ValidationError("error.planner.geometry")


def lane_of(spec: PartSpec, ref: PathRef) -> int | None:
    if ref.layer == LAYER_GROOVE:
        return spec.slot_paths[ref.index].lane
    return None


def slot_of(spec: PartSpec, ref: PathRef) -> str | None:
    if ref.layer == LAYER_EDGE:
        return spec.border_edges[ref.index].slot
    if ref.layer == LAYER_ATTACH and spec.attachment is not None and spec.attachment.slots:
        return spec.attachment.slots[0]
    return None


def geometry_path(spec: PartSpec, ref: PathRef) -> GeometryPath:
    return GeometryPath(tuple(as_segment(span) for span in spans_of(spec, ref)))


def with_spans(spec: PartSpec, ref: PathRef, spans: tuple[SlotSpan, ...]) -> PartSpec:
    """Replace the spans of one path. An empty list removes the path."""
    if not spans:
        return remove_path(spec, ref)
    if ref.layer in {LAYER_GROOVE, LAYER_CENTER}:
        paths = list(spec.slot_paths)
        paths[ref.index] = replace(paths[ref.index], spans=spans)
        return replace(spec, slot_paths=tuple(paths))
    if ref.layer == LAYER_CONTOUR:
        contours = list(spec.contours)
        contours[ref.index] = replace(contours[ref.index], spans=spans)
        return replace(spec, contours=tuple(contours))
    if ref.layer == LAYER_EDGE:
        edges = list(spec.border_edges)
        edges[ref.index] = replace(edges[ref.index], spans=spans)
        return replace(spec, border_edges=tuple(edges))
    if ref.layer == LAYER_ATTACH and spec.attachment is not None:
        return replace(spec, attachment=replace(spec.attachment, edge=spans))
    raise ValidationError("error.planner.geometry")


def remove_path(spec: PartSpec, ref: PathRef) -> PartSpec:
    spans_of(spec, ref)
    if ref.layer in {LAYER_GROOVE, LAYER_CENTER}:
        paths = list(spec.slot_paths)
        del paths[ref.index]
        return replace(spec, slot_paths=tuple(paths))
    if ref.layer == LAYER_CONTOUR:
        contours = list(spec.contours)
        del contours[ref.index]
        return replace(spec, contours=tuple(contours))
    if ref.layer == LAYER_EDGE:
        edges = list(spec.border_edges)
        del edges[ref.index]
        return replace(spec, border_edges=tuple(edges))
    if spec.attachment is None:
        raise ValidationError("error.planner.geometry")
    return replace(spec, attachment=replace(spec.attachment, edge=()))


def add_path(
    spec: PartSpec,
    layer: str,
    spans: tuple[SlotSpan, ...],
    *,
    lane: int | None = None,
    slot: str | None = None,
) -> tuple[PartSpec, PathRef]:
    """Append one path to ``layer``. A groove gets the next free lane when ``lane`` is empty."""
    if not spans:
        raise ValidationError("error.planner.geometry")
    for span in spans:
        as_segment(span)
    if layer == LAYER_GROOVE:
        base = explicit(spec)
        taken = {path.lane for path in base.slot_paths if path.kind == PATH_SLOT}
        number = (
            lane
            if lane is not None
            else next((n for n in range(1, MAX_LANE_COUNT + 1) if n not in taken), None)
        )
        if number is None or not 1 <= number <= MAX_LANE_COUNT:
            raise ValidationError("error.planner.lane")
        if number in taken:
            raise ValidationError("error.planner.editor.lane_twice")
        added = replace(base, slot_paths=(*base.slot_paths, SlotPath(spans, PATH_SLOT, number)))
        return _grow_lanes(added), PathRef(LAYER_GROOVE, len(added.slot_paths) - 1)
    if layer == LAYER_CENTER:
        base = explicit(spec)
        added = replace(base, slot_paths=(*base.slot_paths, SlotPath(spans, PATH_CENTER)))
        return added, PathRef(LAYER_CENTER, len(added.slot_paths) - 1)
    if layer == LAYER_CONTOUR:
        contour = replace(spec, contours=(*spec.contours, SlotPath(spans, PATH_CONTOUR)))
        return contour, PathRef(LAYER_CONTOUR, len(contour.contours) - 1)
    if layer == LAYER_EDGE:
        if slot not in BORDER_SLOTS or any(edge.slot == slot for edge in spec.border_edges):
            raise ValidationError("error.planner.editor.edge_slots")
        edged = replace(spec, border_edges=(*spec.border_edges, BorderEdge(str(slot), spans)))
        return edged, PathRef(LAYER_EDGE, len(edged.border_edges) - 1)
    if layer == LAYER_ATTACH:
        profile = spec.attachment
        if profile is None:
            raise ValidationError("error.planner.editor.attach_edge")
        chosen = (slot,) if slot in BORDER_SLOTS else profile.slots[:1] or ("left",)
        attached = replace(
            spec,
            attachment=replace(profile, host_shape=HOST_CUSTOM, edge=spans, slots=chosen),
        )
        return attached, PathRef(LAYER_ATTACH, 0)
    raise ValidationError("error.planner.geometry")


def _grow_lanes(spec: PartSpec) -> PartSpec:
    """Every rail joint lists every groove lane, so two parts that join agree on the lanes."""
    lanes = tuple(
        sorted({path.lane for path in spec.slot_paths if path.kind == PATH_SLOT and path.lane})
    )
    if not lanes:
        return spec
    connectors = tuple(
        replace(item, lanes=lanes) if item.kind == TRACK else item for item in spec.connectors
    )
    return replace(spec, connectors=connectors, lane_count=len(lanes))


# --------------------------------------------------------------------------------------------
# Segment operations


def split_span(spec: PartSpec, ref: PathRef, index: int, t: float = 0.5) -> PartSpec:
    """Cut one segment into two that draw the same curve."""
    spans = list(spans_of(spec, ref))
    if not 0 <= index < len(spans):
        raise ValidationError("error.planner.geometry")
    first, second = split_segment(as_segment(spans[index]), t)
    spans[index : index + 1] = [span_of(first), span_of(second)]
    return with_spans(spec, ref, tuple(spans))


def split_at_point(spec: PartSpec, ref: PathRef, index: int, x_mm: float, y_mm: float) -> PartSpec:
    """Add a node where the segment is closest to ``(x, y)``."""
    spans = spans_of(spec, ref)
    if not 0 <= index < len(spans):
        raise ValidationError("error.planner.geometry")
    t, _distance = closest_on_segment(as_segment(spans[index]), x_mm, y_mm)
    t = min(max(t, 0.02), 0.98)
    return split_span(spec, ref, index, t)


def delete_span(spec: PartSpec, ref: PathRef, index: int) -> PartSpec:
    """Remove one segment. A gap in the middle stays visible and blocks saving until closed."""
    spans = list(spans_of(spec, ref))
    if not 0 <= index < len(spans):
        raise ValidationError("error.planner.geometry")
    del spans[index]
    return with_spans(spec, ref, tuple(spans))


def remove_node(spec: PartSpec, ref: PathRef, index: int) -> PartSpec:
    """Remove the node at the start of ``index``.

    The two neighbouring segments become one straight segment. At the first node the first
    segment goes away; a Bézier control point is handled by :func:`reset_handle`.
    """
    spans = list(spans_of(spec, ref))
    if not 0 <= index < len(spans):
        raise ValidationError("error.planner.geometry")
    if index == 0:
        del spans[0]
    else:
        start = span_start(spans[index - 1])
        end = span_end(spans[index])
        spans[index - 1 : index + 1] = [SlotSpan.line(start[0], start[1], end[0], end[1])]
    return with_spans(spec, ref, tuple(spans))


def reset_handle(spec: PartSpec, ref: PathRef, index: int, handle: str) -> PartSpec:
    """Put a Bézier control point back a third of the chord from its end."""
    spans = list(spans_of(spec, ref))
    span = spans[index]
    if span.kind != SPAN_BEZIER or handle not in {"p1", "p2"}:
        raise ValidationError("error.planner.geometry")
    start = (span.x0, span.y0)
    end = (span.x1, span.y1)
    fraction = 1.0 / 3.0 if handle == "p1" else 2.0 / 3.0
    point = (start[0] + (end[0] - start[0]) * fraction, start[1] + (end[1] - start[1]) * fraction)
    spans[index] = move_handle(span, handle, point[0], point[1])
    return with_spans(spec, ref, tuple(spans))


def convert_span(spec: PartSpec, ref: PathRef, index: int, kind: str) -> PartSpec:
    """Line to Bézier keeps the shape. Bézier to line keeps only the two ends."""
    spans = list(spans_of(spec, ref))
    span = spans[index]
    start, end = span_start(span), span_end(span)
    if kind == SPAN_BEZIER and span.kind == SPAN_LINE:
        spans[index] = bezier_between(start, end)
    elif kind == SPAN_LINE and span.kind == SPAN_BEZIER:
        spans[index] = SlotSpan.line(start[0], start[1], end[0], end[1])
    elif kind == span.kind:
        return spec
    else:
        raise ValidationError("error.planner.geometry")
    return with_spans(spec, ref, tuple(spans))


def move_point(
    spec: PartSpec,
    point: PointRef,
    x_mm: float,
    y_mm: float,
    *,
    join: bool = True,
) -> PartSpec:
    """Move one handle. A node drags the touching ends of its neighbours when ``join`` is set.

    A Bézier anchor takes its control point along. An arc end can only turn on its circle, so
    the neighbour follows the point that really results.
    """
    spans = list(spans_of(spec, point.path))
    index = point.segment
    if not 0 <= index < len(spans):
        raise ValidationError("error.planner.geometry")
    old = spans[index]
    node_handle = point.handle in node_handles(old)
    before = handle_points(old)[point.handle]
    moved = move_handle(old, point.handle, x_mm, y_mm)
    spans[index] = moved
    if point.handle in {"p0", "p3"}:
        spans[index] = _drag_control(old, moved, point.handle)
    if node_handle and join:
        after = handle_points(spans[index])[point.handle]
        closed = is_closed(spans_of(spec, point.path))
        for other in _partners(spans_of(spec, point.path), index, point.handle, before, closed):
            neighbour, handle = other
            target = move_handle(spans[neighbour], handle, after[0], after[1])
            if handle in {"p0", "p3"}:
                target = _drag_control(spans[neighbour], target, handle)
            spans[neighbour] = target
    return with_spans(spec, point.path, tuple(spans))


def _drag_control(old: SlotSpan, moved: SlotSpan, handle: str) -> SlotSpan:
    """Keep the control point next to a moved Bézier anchor at the same offset."""
    if moved.kind != SPAN_BEZIER:
        return moved
    anchor_old = handle_points(old)[handle]
    anchor_new = handle_points(moved)[handle]
    dx = anchor_new[0] - anchor_old[0]
    dy = anchor_new[1] - anchor_old[1]
    control = "p1" if handle == "p0" else "p2"
    cx, cy = handle_points(moved)[control]
    return move_handle(moved, control, cx + dx, cy + dy)


def _partners(
    spans: tuple[SlotSpan, ...],
    index: int,
    handle: str,
    where: tuple[float, float],
    closed: bool,
) -> list[tuple[int, str]]:
    """Segments whose end touched ``where`` before the move."""
    found: list[tuple[int, str]] = []
    last_handle = node_handles(spans[index])[1]
    count = len(spans)
    if handle == last_handle:
        neighbour = index + 1 if index + 1 < count else (0 if closed and count > 1 else None)
        side = "start"
    else:
        neighbour = index - 1 if index > 0 else (count - 1 if closed and count > 1 else None)
        side = "end"
    if neighbour is None or neighbour == index:
        return found
    target = node_handles(spans[neighbour])[0 if side == "start" else 1]
    spot = handle_points(spans[neighbour])[target]
    if math.hypot(spot[0] - where[0], spot[1] - where[1]) <= POSITION_GAP_MM:
        found.append((neighbour, target))
    return found


def translate_path(spec: PartSpec, ref: PathRef, dx: float, dy: float) -> PartSpec:
    _finite(dx, dy)
    return with_spans(
        spec, ref, tuple(translate_span(span, dx, dy) for span in spans_of(spec, ref))
    )


def rotate_path(
    spec: PartSpec, ref: PathRef, rotation_deg: float, origin: tuple[float, float]
) -> PartSpec:
    _finite(rotation_deg, *origin)
    return with_spans(
        spec,
        ref,
        tuple(rotate_span(span, rotation_deg, origin) for span in spans_of(spec, ref)),
    )


def reverse_path(spec: PartSpec, ref: PathRef) -> PartSpec:
    """Run the same path the other way round."""
    spans = tuple(_reversed_span(span) for span in reversed(spans_of(spec, ref)))
    return with_spans(spec, ref, spans)


def _reversed_span(span: SlotSpan) -> SlotSpan:
    segment = as_segment(span)
    if isinstance(segment, ArcSegment):
        return SlotSpan.arc(
            segment.cx,
            segment.cy,
            segment.radius_mm,
            segment.start_deg + segment.sweep_deg,
            -segment.sweep_deg,
        )
    if isinstance(segment, CubicBezier):
        p0, p1, p2, p3 = segment.control_points()
        return SlotSpan.bezier(*p3, *p2, *p1, *p0)
    return SlotSpan.line(segment.x1, segment.y1, segment.x0, segment.y0)


def append_span(
    spec: PartSpec, ref: PathRef, span: SlotSpan, *, at_start: bool = False
) -> PartSpec:
    spans = spans_of(spec, ref)
    as_segment(span)
    return with_spans(spec, ref, (span, *spans) if at_start else (*spans, span))


def close_gap(spec: PartSpec, ref: PathRef, joint: int) -> PartSpec:
    """Bridge a gap between segment ``joint`` and the next one with a straight segment."""
    spans = list(spans_of(spec, ref))
    if not 0 <= joint < len(spans) - 1:
        raise ValidationError("error.planner.geometry")
    start = span_end(spans[joint])
    end = span_start(spans[joint + 1])
    if math.hypot(end[0] - start[0], end[1] - start[1]) <= POSITION_GAP_MM:
        return spec
    spans.insert(joint + 1, SlotSpan.line(start[0], start[1], end[0], end[1]))
    return with_spans(spec, ref, tuple(spans))


def close_path(spec: PartSpec, ref: PathRef) -> PartSpec:
    """Add the straight segment that returns a contour to its first node."""
    spans = spans_of(spec, ref)
    if len(spans) < 1:
        raise ValidationError("error.planner.geometry")
    start = span_start(spans[0])
    end = span_end(spans[-1])
    if math.hypot(start[0] - end[0], start[1] - end[1]) <= POSITION_GAP_MM:
        return spec
    return with_spans(spec, ref, (*spans, SlotSpan.line(end[0], end[1], start[0], start[1])))


def cut_path(spec: PartSpec, ref: PathRef, joint: int) -> tuple[PartSpec, PathRef]:
    """Separate a path at the node after segment ``joint`` into two paths."""
    spans = spans_of(spec, ref)
    if not 0 <= joint < len(spans) - 1:
        raise ValidationError("error.planner.geometry")
    head, tail = spans[: joint + 1], spans[joint + 1 :]
    cut = with_spans(spec, ref, head)
    layer = ref.layer
    if layer in (LAYER_EDGE, LAYER_ATTACH):
        raise ValidationError("error.planner.editor.edge_slots")
    return add_path(cut, layer, tail)


def join_paths(spec: PartSpec, first: PathRef, second: PathRef) -> PartSpec:
    """Append ``second`` to ``first``. A gap between them is bridged by a straight segment."""
    if first.layer != second.layer or first == second or first.layer in {LAYER_EDGE, LAYER_ATTACH}:
        raise ValidationError("error.planner.geometry")
    head = list(spans_of(spec, first))
    tail = spans_of(spec, second)
    end = span_end(head[-1])
    start = span_start(tail[0])
    if math.hypot(end[0] - start[0], end[1] - start[1]) > POSITION_GAP_MM:
        head.append(SlotSpan.line(end[0], end[1], start[0], start[1]))
    merged = with_spans(spec, first, (*head, *tail))
    drop = second
    if second.index > first.index:
        return remove_path(merged, drop)
    return remove_path(merged, PathRef(second.layer, second.index))


def set_tangent(spec: PartSpec, ref: PathRef, joint: int, mode: str) -> PartSpec:
    """Shape the node after segment ``joint``: kink, smooth (same heading) or symmetric handles."""
    if mode not in TANGENT_MODES:
        raise ValidationError("error.planner.geometry")
    spans = list(spans_of(spec, ref))
    if not 0 <= joint < len(spans) - 1:
        raise ValidationError("error.planner.geometry")
    if mode == TANGENT_KINK:
        return spec
    left, right = spans[joint], spans[joint + 1]
    node = span_end(left)
    if right.kind == SPAN_BEZIER:
        heading = math.radians(as_segment(left).tangent_deg(1.0))
        length = math.hypot(right.c1x - right.x0, right.c1y - right.y0)
        if mode == TANGENT_SYMMETRIC and left.kind == SPAN_BEZIER:
            length = math.hypot(left.x1 - left.c2x, left.y1 - left.c2y)
        point = (node[0] + math.cos(heading) * length, node[1] + math.sin(heading) * length)
        spans[joint + 1] = move_handle(right, "p1", point[0], point[1])
    elif left.kind == SPAN_BEZIER:
        heading = math.radians(as_segment(right).tangent_deg(0.0))
        length = math.hypot(left.x1 - left.c2x, left.y1 - left.c2y)
        point = (node[0] - math.cos(heading) * length, node[1] - math.sin(heading) * length)
        spans[joint] = move_handle(left, "p2", point[0], point[1])
    else:
        return spec
    return with_spans(spec, ref, tuple(spans))


def joint_report(spec: PartSpec, ref: PathRef) -> tuple[tuple[float, float], ...]:
    """``(gap_mm, tangent_delta_deg)`` of every node between two segments of the path."""
    path = geometry_path(spec, ref)
    return tuple((joint.gap_mm, joint.tangent_delta_deg) for joint in path.joints())


def set_line(
    spec: PartSpec,
    ref: PathRef,
    index: int,
    *,
    length: float | None = None,
    angle: float | None = None,
) -> PartSpec:
    """Change a line's length or heading by moving its end node. Joined nodes follow."""
    spans = spans_of(spec, ref)
    span = spans[index]
    if span.kind != SPAN_LINE:
        raise ValidationError("error.planner.geometry")
    dx = span.x1 - span.x0
    dy = span.y1 - span.y0
    current = math.hypot(dx, dy)
    heading = math.atan2(dy, dx) if current > 0.0 else 0.0
    new_length = current if length is None else _finite(length)[0]
    new_heading = heading if angle is None else math.radians(_finite(angle)[0])
    if new_length <= 0.0:
        raise ValidationError("error.planner.geometry")
    target = (
        span.x0 + math.cos(new_heading) * new_length,
        span.y0 + math.sin(new_heading) * new_length,
    )
    return move_point(spec, PointRef(ref, index, "end"), target[0], target[1])


def set_arc(
    spec: PartSpec,
    ref: PathRef,
    index: int,
    *,
    center: tuple[float, float] | None = None,
    radius: float | None = None,
    start: float | None = None,
    sweep: float | None = None,
) -> PartSpec:
    spans = list(spans_of(spec, ref))
    span = spans[index]
    if span.kind != SPAN_ARC:
        raise ValidationError("error.planner.geometry")
    cx, cy = (span.x0, span.y0) if center is None else _finite(*center)
    spans[index] = SlotSpan.arc(
        cx,
        cy,
        span.radius_mm if radius is None else _finite(radius)[0],
        span.start_deg if start is None else _finite(start)[0],
        span.sweep_deg if sweep is None else _finite(sweep)[0],
    )
    as_segment(spans[index])
    if abs(spans[index].sweep_deg) < _MIN_SWEEP_DEG:
        raise ValidationError("error.planner.geometry")
    return with_spans(spec, ref, tuple(spans))


def flip_arc(spec: PartSpec, ref: PathRef, index: int) -> PartSpec:
    """Run an arc the other way round the same circle, from the same start angle."""
    span = spans_of(spec, ref)[index]
    if span.kind != SPAN_ARC:
        raise ValidationError("error.planner.geometry")
    return set_arc(spec, ref, index, sweep=-span.sweep_deg)


# --------------------------------------------------------------------------------------------
# Lanes


def groove_refs(spec: PartSpec) -> tuple[PathRef, ...]:
    return tuple(ref for ref in path_refs(spec) if ref.layer == LAYER_GROOVE)


def lane_numbers(spec: PartSpec) -> tuple[int, ...]:
    return tuple(
        sorted({path.lane for path in spec.slot_paths if path.kind == PATH_SLOT and path.lane})
    )


def rail_connectors(spec: PartSpec) -> tuple[ConnectorSpec, ...]:
    return tuple(item for item in spec.connectors if item.kind == TRACK)


def add_lane(spec: PartSpec) -> tuple[PartSpec, PathRef]:
    """A new groove between the first two rail joints, on the next free lane."""
    rails = rail_connectors(spec)
    if len(rails) < 2:
        raise ValidationError("error.planner.editor.connectors")
    first, second = rails[0], rails[1]
    if math.hypot(second.x_mm - first.x_mm, second.y_mm - first.y_mm) <= 1e-6:
        raise ValidationError("error.planner.editor.connectors")
    line = SlotSpan.line(first.x_mm, first.y_mm, second.x_mm, second.y_mm)
    return add_path(spec, LAYER_GROOVE, (line,))


def remove_lane(spec: PartSpec, lane: int) -> PartSpec:
    """Remove a groove and close the numbering. Joints drop the lane too."""
    base = explicit(spec)
    lanes = lane_numbers(base)
    if lane not in lanes or len(lanes) <= 1:
        raise ValidationError("error.planner.lane")

    def shifted(number: int) -> int:
        return number - 1 if number > lane else number

    paths = tuple(
        replace(path, lane=shifted(path.lane) if path.lane else path.lane)
        for path in base.slot_paths
        if not (path.kind == PATH_SLOT and path.lane == lane)
    )
    remaining = tuple(shifted(number) for number in lanes if number != lane)
    connectors = tuple(
        replace(item, lanes=remaining) if item.kind == TRACK else item for item in base.connectors
    )
    return replace(base, slot_paths=paths, connectors=connectors, lane_count=len(remaining))


def set_lane(spec: PartSpec, ref: PathRef, lane: int) -> PartSpec:
    """Give a groove another lane number. A groove that already has it takes the old number."""
    base = explicit(spec)
    if ref.layer != LAYER_GROOVE:
        raise ValidationError("error.planner.geometry")
    lanes = lane_numbers(base)
    if lane not in lanes:
        raise ValidationError("error.planner.lane")
    current = base.slot_paths[ref.index].lane
    paths = list(base.slot_paths)
    for index, path in enumerate(paths):
        if path.kind != PATH_SLOT:
            continue
        if index == ref.index:
            paths[index] = replace(path, lane=lane)
        elif path.lane == lane:
            paths[index] = replace(path, lane=current)
    return replace(base, slot_paths=tuple(paths))


# --------------------------------------------------------------------------------------------
# Rail joints


def set_connector(
    spec: PartSpec,
    index: int,
    *,
    x_mm: float | None = None,
    y_mm: float | None = None,
    direction_deg: float | None = None,
    lanes: tuple[int, ...] | None = None,
) -> PartSpec:
    if not 0 <= index < len(spec.connectors):
        raise ValidationError("error.planner.geometry")
    item = spec.connectors[index]
    updated = replace(
        item,
        x_mm=item.x_mm if x_mm is None else _finite(x_mm)[0],
        y_mm=item.y_mm if y_mm is None else _finite(y_mm)[0],
        direction_deg=(
            item.direction_deg if direction_deg is None else _finite(direction_deg)[0] % 360.0
        ),
        lanes=item.lanes if lanes is None else tuple(lanes),
    )
    connectors = list(spec.connectors)
    connectors[index] = updated
    return replace(spec, connectors=tuple(connectors))


def add_connector(spec: PartSpec, x_mm: float, y_mm: float, direction_deg: float) -> PartSpec:
    """A new rail joint with every lane of the part. Borders have none."""
    if spec.attachment is not None:
        raise ValidationError("error.planner.editor.connectors")
    _finite(x_mm, y_mm, direction_deg)
    lanes = lane_numbers(spec) or tuple(range(1, spec.lane_count + 1))
    names = {item.name for item in spec.connectors}
    name = next(label for label in _names() if label not in names)
    created = ConnectorSpec(name, x_mm, y_mm, 0.0, direction_deg % 360.0, TRACK, lanes)
    return replace(spec, connectors=(*spec.connectors, created))


def remove_connector(spec: PartSpec, index: int) -> PartSpec:
    if not 0 <= index < len(spec.connectors):
        raise ValidationError("error.planner.geometry")
    if spec.connectors[index].kind == TRACK and len(rail_connectors(spec)) <= 2:
        raise ValidationError("error.planner.editor.connectors")
    connectors = list(spec.connectors)
    del connectors[index]
    return replace(spec, connectors=tuple(connectors))


def _names() -> tuple[str, ...]:
    return ("a", "b", "c", "d", "e", "f", "g", "h")


# --------------------------------------------------------------------------------------------
# Contours, outline and side edges


def outline_from_spans(spans: Sequence[SlotSpan]) -> tuple[tuple[float, float], ...]:
    """Sample a closed contour into the polygon the plan fills and hit-tests."""
    points: list[tuple[float, float]] = []
    for span in spans:
        segment = as_segment(span)
        if isinstance(segment, LineSegment):
            steps = 1
        elif isinstance(segment, ArcSegment):
            steps = max(1, math.ceil(abs(segment.sweep_deg) / _ARC_STEP_DEG))
        else:
            steps = _BEZIER_STEPS
        for step in range(steps):
            points.append(segment.point_at(step / steps))
    return tuple(points)


def contour_from_polygon(points: Sequence[tuple[float, float]]) -> tuple[SlotSpan, ...]:
    """The straight segments of a closed polygon."""
    if len(points) < 3:
        raise ValidationError("error.planner.editor.contour")
    spans = [
        SlotSpan.line(a[0], a[1], b[0], b[1])
        for a, b in zip(points, (*points[1:], points[0]), strict=True)
    ]
    return tuple(spans)


def with_outline_from_contour(spec: PartSpec) -> PartSpec:
    """Make the filled polygon follow the first closed contour. Open strokes leave it alone."""
    for path in spec.contours:
        if is_closed(path.spans):
            polygon = outline_from_spans(path.spans)
            if len(polygon) >= 3:
                return replace(spec, outline=polygon)
    return spec


def contour_from_outline(spec: PartSpec) -> PartSpec:
    """Write the filled polygon out as an editable closed contour. Needs no contour yet."""
    if any(is_closed(path.spans) for path in spec.contours):
        return spec
    spans = contour_from_polygon(spec.outline)
    return add_path(spec, LAYER_CONTOUR, spans)[0]


def side_edges(spec: PartSpec, *, curve: bool = False) -> tuple[BorderEdge, ...]:
    """Straight side edges along the first two rail joints, half the width to each side."""
    rails = rail_connectors(spec)
    if len(rails) < 2:
        raise ValidationError("error.planner.editor.connectors")
    first, second = rails[0], rails[1]
    dx = second.x_mm - first.x_mm
    dy = second.y_mm - first.y_mm
    length = math.hypot(dx, dy)
    if length <= 1e-6:
        raise ValidationError("error.planner.editor.connectors")
    width = spec.width_mm or track_width(spec.lane_count)
    nx = dy / length * width / 2.0
    ny = -dx / length * width / 2.0
    names = ("outer", "inner") if curve else ("left", "right")
    return tuple(
        BorderEdge(
            name,
            (
                SlotSpan.line(
                    first.x_mm + sign * nx,
                    first.y_mm + sign * ny,
                    second.x_mm + sign * nx,
                    second.y_mm + sign * ny,
                ),
            ),
        )
        for name, sign in zip(names, (1.0, -1.0), strict=True)
    )


def with_side_edges(spec: PartSpec) -> PartSpec:
    """Add straight left and right edges where none exist, so a custom border can fit."""
    if spec.border_edges or spec.attachment is not None:
        return spec
    return replace(spec, border_edges=side_edges(spec))


def offset_strip(edge: Sequence[SlotSpan], width_mm: float, *, left: bool) -> tuple[SlotSpan, ...]:
    """Closed contour of a strip that lies ``width_mm`` beside ``edge``.

    Lines and arcs are offset exactly. A Bézier span is sampled into short lines, which is
    noted in the editor. ``left`` means the left of the travel direction in the plan's y-down
    frame.
    """
    if width_mm <= 0.0 or not math.isfinite(width_mm) or not edge:
        raise ValidationError("error.planner.editor.width")
    sign = -1.0 if left else 1.0
    far: list[SlotSpan] = []
    for span in edge:
        far.extend(_offset_span(span, width_mm * sign))
    near = tuple(edge)
    closing_start = span_end(near[-1])
    closing_end = span_end(far[-1])
    back = tuple(_reversed_span(span) for span in reversed(far))
    start_a = span_start(near[0])
    start_b = span_start(far[0])
    return (
        *near,
        SlotSpan.line(closing_start[0], closing_start[1], closing_end[0], closing_end[1]),
        *back,
        SlotSpan.line(start_b[0], start_b[1], start_a[0], start_a[1]),
    )


def _offset_span(span: SlotSpan, distance: float) -> tuple[SlotSpan, ...]:
    segment = as_segment(span)
    if isinstance(segment, LineSegment):
        length = segment.length_mm()
        nx = (segment.y1 - segment.y0) / length * distance
        ny = -(segment.x1 - segment.x0) / length * distance
        return (SlotSpan.line(segment.x0 + nx, segment.y0 + ny, segment.x1 + nx, segment.y1 + ny),)
    if isinstance(segment, ArcSegment):
        outward = distance if segment.sweep_deg < 0.0 else -distance
        radius = segment.radius_mm + outward
        if radius <= 0.0:
            raise ValidationError("error.planner.editor.width")
        return (SlotSpan.arc(segment.cx, segment.cy, radius, segment.start_deg, segment.sweep_deg),)
    pieces: list[SlotSpan] = []
    points = [segment.point_at(index / _BEZIER_STEPS) for index in range(_BEZIER_STEPS + 1)]
    for first, second in pairwise(points):
        length = math.hypot(second[0] - first[0], second[1] - first[1])
        if length <= 1e-9:
            continue
        nx = (second[1] - first[1]) / length * distance
        ny = -(second[0] - first[0]) / length * distance
        pieces.append(SlotSpan.line(first[0] + nx, first[1] + ny, second[0] + nx, second[1] + ny))
    return tuple(_chain(pieces))


def _chain(pieces: list[SlotSpan]) -> list[SlotSpan]:
    """Join sampled offset lines end to end so the path has no gap."""
    chained: list[SlotSpan] = []
    for piece in pieces:
        if chained:
            previous = chained[-1]
            piece = SlotSpan.line(previous.x1, previous.y1, piece.x1, piece.y1)
        chained.append(piece)
    return chained


def strip_from_edge(spec: PartSpec, width_mm: float, *, left: bool) -> PartSpec:
    """Draw a border strip beside its mating edge: closed contour, filled outline, width."""
    profile = spec.attachment
    if profile is None or not profile.edge:
        raise ValidationError("error.planner.editor.attach_edge")
    spans = offset_strip(profile.edge, width_mm, left=left)
    contour = SlotPath(spans, PATH_CONTOUR)
    rebuilt = replace(spec, contours=(contour,), width_mm=width_mm)
    return with_outline_from_contour(rebuilt)


def copy_edge(spec: PartSpec, host: PartSpec, slot: str) -> PartSpec:
    """Take the mating edge from a host's side edge, so this border fits that host exactly."""
    profile = spec.attachment
    source = next((item.spans for item in host.border_edges if item.slot == slot), None)
    if profile is None or source is None:
        raise ValidationError("error.planner.editor.attach_edge")
    return replace(
        spec,
        attachment=replace(
            profile,
            host_shape=HOST_CUSTOM,
            slots=(slot,),
            edge=source,
            host_lanes=host.lane_count,
        ),
    )


def set_edge_slot(spec: PartSpec, ref: PathRef, slot: str) -> PartSpec:
    """Move an edge to another side: left or right on a straight, inner or outer on a curve."""
    if slot not in BORDER_SLOTS:
        raise ValidationError("error.planner.editor.edge_slots")
    if ref.layer == LAYER_EDGE:
        if any(
            edge.slot == slot for index, edge in enumerate(spec.border_edges) if index != ref.index
        ):
            raise ValidationError("error.planner.editor.edge_slots")
        edges = list(spec.border_edges)
        edges[ref.index] = replace(edges[ref.index], slot=slot)
        return replace(spec, border_edges=tuple(edges))
    if ref.layer == LAYER_ATTACH and spec.attachment is not None:
        return replace(spec, attachment=replace(spec.attachment, slots=(slot,)))
    raise ValidationError("error.planner.geometry")


def to_custom_border(spec: PartSpec) -> PartSpec:
    """Turn a catalogue-style border into one with its own mating edge and contour.

    The standard profile matches a host by family and measures. A custom one matches the edge
    geometry, so the strip then fits only a host with that same edge. The first slot is kept.
    """
    profile = spec.attachment
    if profile is None or spec.category != BORDER:
        raise ValidationError("error.planner.editor.attach_edge")
    if profile.host_shape == HOST_CUSTOM and profile.edge:
        return spec
    slot = profile.slots[0] if profile.slots else "left"
    road = (profile.host_lanes or spec.lane_count) * LANE_PITCH_MM / 2.0
    width = spec.width_mm if spec.width_mm and spec.width_mm > 0 else STRIP_WIDTH_MM
    outline = placed_outline(spec, slot)
    if profile.host_shape == HOST_STRAIGHT:
        half = (profile.host_length_mm or 0.0) / 2.0
        y_edge = -road if slot == "left" else road
        edge = (SlotSpan.line(-half, y_edge, half, y_edge),)
    elif profile.host_shape in {HOST_CURVE_FLAT, HOST_CURVE_BANKED}:
        radius = profile.host_radius_mm or 0.0
        angle = profile.host_angle_deg or 0.0
        edge_radius = radius + road if slot == "outer" else max(radius - road, 0.1)
        edge = (SlotSpan.arc(0.0, 0.0, edge_radius, -angle / 2.0, angle),)
    else:
        raise ValidationError("error.planner.editor.attach_edge")
    left = _strip_is_left(edge, outline)
    contour = SlotPath(offset_strip(edge, width, left=left), PATH_CONTOUR)
    custom = replace(
        spec,
        attachment=AttachmentProfile(
            HOST_CUSTOM, (slot,), host_lanes=profile.host_lanes, edge=edge
        ),
        contours=(contour,),
        outline=outline,
        width_mm=width,
    )
    return with_outline_from_contour(custom)


def _strip_is_left(edge: tuple[SlotSpan, ...], outline: Sequence[tuple[float, float]]) -> bool:
    """True when the strip outline lies on the left of the edge's travel direction."""
    segment = as_segment(edge[0])
    mid = segment.point_at(0.5)
    heading = math.radians(segment.tangent_deg(0.5))
    normal = (math.sin(heading), -math.cos(heading))
    cx = sum(point[0] for point in outline) / len(outline)
    cy = sum(point[1] for point in outline) / len(outline)
    side = (cx - mid[0]) * normal[0] + (cy - mid[1]) * normal[1]
    return side < 0.0


# --------------------------------------------------------------------------------------------
# What may be drawn on


def support_reason(spec: PartSpec, *, corrupt: bool = False) -> str:
    """``""`` when the part can be drawn on, else why not.

    Switches, lane changes, crossings and pit parts have routes that branch, and the planner
    has no switching logic. The editor shows them and keeps their metadata editable, but it
    never writes a geometry for them.
    """
    if corrupt:
        return SUPPORT_CORRUPT
    if spec.category not in {STRAIGHT, CURVE, SPECIAL, BORDER}:
        return SUPPORT_CATEGORY
    if spec.category == BORDER and spec.attachment is None:
        return SUPPORT_END_PIECE
    if spec.attachment is not None:
        profile = spec.attachment
        if profile.host_shape != HOST_CUSTOM or not profile.edge:
            return SUPPORT_STANDARD_BORDER
        return SUPPORT_OK
    if _branches(spec):
        return SUPPORT_BRANCH
    return SUPPORT_OK


def _branches(spec: PartSpec) -> bool:
    """True when a rail joint lane is the end of more than one groove."""
    ends: dict[tuple[int, int], int] = {}
    for path in driving_paths(spec):
        for point in (path.start(), path.end()):
            bound = _bind(spec, point, path.lane)
            if bound is None:
                continue
            ends[bound] = ends.get(bound, 0) + 1
    return any(count > 1 for count in ends.values())


def _bind(spec: PartSpec, point: tuple[float, float], lane: int | None) -> tuple[int, int] | None:
    """The rail connector and lane a groove end belongs to, by plane and lane number."""
    best: tuple[float, tuple[int, int]] | None = None
    for index, connector in enumerate(spec.connectors):
        if connector.kind != TRACK or (lane is not None and lane not in connector.lanes):
            continue
        radians = math.radians(connector.direction_deg)
        dx = point[0] - connector.x_mm
        dy = point[1] - connector.y_mm
        along = dx * math.cos(radians) + dy * math.sin(radians)
        if abs(along) > JOINT_PLANE_MM:
            continue
        across = abs(-dx * math.sin(radians) + dy * math.cos(radians))
        if across > LANE_PITCH_MM * MAX_LANE_COUNT:
            continue
        key_lane = lane if lane is not None else connector.lanes[0]
        if best is None or across < best[0]:
            best = (across, (index, key_lane))
    return None if best is None else best[1]


# --------------------------------------------------------------------------------------------
# Finishing and validation


def finalize(spec: PartSpec, original: PartSpec) -> PartSpec:
    """Bring measures, category and outline in line with edited geometry.

    A straight or curve whose body the parameter form can no longer express becomes a special
    part. Its stored grooves and contours stay; it simply stops claiming a radius or a standard
    shape. Parts the form can still express keep their category and measures.
    """
    result = with_outline_from_contour(spec)
    if result.attachment is None and result.slot_paths:
        lanes = lane_numbers(result)
        if lanes:
            result = replace(result, lane_count=len(lanes))
    if result.category in {STRAIGHT, CURVE, SPECIAL} and result.attachment is None:
        if draft_from_spec(result) is None:
            result = _as_special(result, original)
    elif (
        result.attachment is not None and result.attachment.edge and draft_from_spec(result) is None
    ):
        result = replace(
            result,
            length_mm=spans_length_mm(result.attachment.edge),
            radius_mm=None,
            angle_deg=None,
        )
    return result


def _as_special(spec: PartSpec, original: PartSpec) -> PartSpec:
    contours = spec.contours
    if not contours and _arc_like(spec):
        contours = _arc_contours(spec)
    rails = rail_connectors(spec)
    length = spec.length_mm
    if len(rails) >= 2 and (
        length is None or spec.connectors != original.connectors or spec.radius_mm is not None
    ):
        length = math.hypot(rails[1].x_mm - rails[0].x_mm, rails[1].y_mm - rails[0].y_mm)
    return replace(
        spec,
        category=SPECIAL,
        length_mm=length,
        radius_mm=None,
        angle_deg=None,
        contours=contours,
    )


def _arc_like(spec: PartSpec) -> bool:
    return bool(spec.radius_mm and spec.angle_deg and spec.length_mm is None)


def _arc_contours(spec: PartSpec) -> tuple[SlotPath, ...]:
    radius = float(spec.radius_mm or 0.0)
    angle = float(spec.angle_deg or 0.0)
    width = spec.width_mm or track_width(spec.lane_count)
    outer = radius + width / 2.0
    inner = max(radius - width / 2.0, 0.1)
    start = -angle / 2.0
    return (
        SlotPath((SlotSpan.arc(0.0, 0.0, outer, start, angle),), PATH_CONTOUR),
        SlotPath((SlotSpan.arc(0.0, 0.0, inner, start, angle),), PATH_CONTOUR),
    )


def validate_editing(spec: PartSpec) -> tuple[EditIssue, ...]:
    """Every reason the edited part must not be saved yet. An empty tuple means it may be."""
    issues: list[EditIssue] = []
    try:
        validate_part_geometry(spec)
    except ValidationError as error:
        issues.append(EditIssue(error.key))
    _check_gaps(spec, issues)
    if spec.attachment is None:
        _check_rail(spec, issues)
    else:
        _check_border(spec, issues)
    _check_edges(spec, issues)
    for index, contour in enumerate(spec.contours):
        if not is_closed(contour.spans) and not _is_edge_stroke(contour):
            issues.append(EditIssue("error.planner.editor.contour_open", str(index + 1)))
    if len(spec.outline) < 3:
        issues.append(EditIssue("error.planner.editor.contour"))
    return tuple(issues)


def _is_edge_stroke(contour: SlotPath) -> bool:
    """A curve stores its road edges as single open arcs. That is a stroke, not an outline."""
    return len(contour.spans) == 1 and contour.spans[0].kind == SPAN_ARC


def require_valid(spec: PartSpec) -> None:
    issues = validate_editing(spec)
    if issues:
        raise ValidationError(issues[0].key)


def _check_gaps(spec: PartSpec, issues: list[EditIssue]) -> None:
    for ref in path_refs(spec):
        try:
            report = joint_report(spec, ref)
        except ValidationError:
            continue
        if any(gap > POSITION_GAP_MM for gap, _delta in report):
            issues.append(EditIssue("error.planner.editor.gap", _label(spec, ref)))


def _label(spec: PartSpec, ref: PathRef) -> str:
    if ref.layer == LAYER_GROOVE:
        lane = spec.slot_paths[ref.index].lane
        return f"{lane}" if lane else f"{ref.index + 1}"
    return f"{ref.index + 1}"


def _check_rail(spec: PartSpec, issues: list[EditIssue]) -> None:
    rails = rail_connectors(spec)
    if len(rails) < 2:
        issues.append(EditIssue("error.planner.editor.connectors"))
        return
    grooves = driving_paths(spec)
    if not grooves:
        issues.append(EditIssue("error.planner.editor.no_lane"))
        return
    seen: set[int] = set()
    ends: dict[tuple[int, int], int] = {}
    for path in grooves:
        if path.lane in seen:
            issues.append(EditIssue("error.planner.editor.lane_twice", str(path.lane)))
        seen.add(path.lane or 0)
        for point, outward in (
            (path.start(), (path.tangent_at_start_deg() + 180.0) % 360.0),
            (path.end(), path.tangent_at_end_deg()),
        ):
            bound = _bind(spec, point, path.lane)
            if bound is None:
                issues.append(EditIssue("error.planner.editor.groove_end", str(path.lane)))
                continue
            ends[bound] = ends.get(bound, 0) + 1
            connector = spec.connectors[bound[0]]
            turn = abs((connector.direction_deg - outward + 180.0) % 360.0 - 180.0)
            if turn > JOINT_DIRECTION_DEG:
                issues.append(EditIssue("error.planner.editor.direction", str(path.lane)))
    if any(count > 1 for count in ends.values()):
        issues.append(EditIssue("error.planner.editor.branch"))
    for index, connector in enumerate(spec.connectors):
        if connector.kind != TRACK:
            continue
        for lane in connector.lanes:
            if (index, lane) not in ends:
                issues.append(EditIssue("error.planner.editor.lane_missing", str(lane)))


def _check_border(spec: PartSpec, issues: list[EditIssue]) -> None:
    profile = spec.attachment
    assert profile is not None
    if spec.connectors or spec.slot_paths:
        issues.append(EditIssue("error.planner.editor.attach_edge"))
    if profile.host_shape == HOST_CUSTOM and not profile.edge:
        issues.append(EditIssue("error.planner.editor.attach_edge"))
    if not profile.slots or any(slot not in BORDER_SLOTS for slot in profile.slots):
        issues.append(EditIssue("error.planner.editor.edge_slots"))


def _check_edges(spec: PartSpec, issues: list[EditIssue]) -> None:
    slots = [edge.slot for edge in spec.border_edges]
    if len(set(slots)) != len(slots):
        issues.append(EditIssue("error.planner.editor.edge_slots"))
    straight = {"left", "right"}
    curved = {"inner", "outer"}
    if set(slots) & straight and set(slots) & curved:
        issues.append(EditIssue("error.planner.editor.edge_slots"))


def _finite(*values: float) -> tuple[float, ...]:
    for value in values:
        if (
            isinstance(value, bool)
            or not isinstance(value, (int, float))
            or not math.isfinite(value)
        ):
            raise ValidationError("error.planner.geometry")
    return tuple(float(value) for value in values)
