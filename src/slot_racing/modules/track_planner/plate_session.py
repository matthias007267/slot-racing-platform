"""Qt-free session for one floor plate and its driving lanes.

The session stores the plate, the lane paths, the selection and one undo
stack. Hover and an in-progress drag are not geometry. Nothing here imports Qt.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass
from itertools import pairwise

from slot_racing.core.domain.lanes import MAX_LANE_COUNT
from slot_racing.core.errors import ValidationError
from slot_racing.modules.track_planner.editing import (
    arc_between,
    contour_from_polygon,
    is_closed,
    outline_from_spans,
    span_end,
    span_length_mm,
    span_start,
    spans_length_mm,
)
from slot_racing.modules.track_planner.geometry import POSITION_GAP_MM, GeometryPath, validate_path
from slot_racing.modules.track_planner.parametric import with_metadata
from slot_racing.modules.track_planner.parts import (
    DRAFT,
    PATH_CONTOUR,
    PATH_SLOT,
    SPAN_ARC,
    SPAN_LINE,
    PartSpec,
    SlotPath,
    SlotSpan,
    as_segment,
    is_plate_draft,
)

# A stored corner closer than this to its neighbour is not a segment.
_MIN_EDGE_MM = POSITION_GAP_MM
# A closed plate thinner than this has no usable area.
_MIN_AREA_MM2 = 1.0
_HISTORY = 200

_COORDS = "error.planner.plate.coords"
_ZERO = "error.planner.plate.zero"
_SHORT = "error.planner.plate.short"
_OPEN = "error.planner.plate.open"
_DEGENERATE = "error.planner.plate.degenerate"
_CROSSING = "error.planner.plate.crossing"
_EXISTS = "error.planner.plate.exists"
_LANE_RADIUS = "error.planner.lane.radius"
_LANE_ZERO = "error.planner.lane.zero"
_LANE_OPEN = "error.planner.lane.open"
_LANE_SHORT = "error.planner.lane.short"
_LANE_LIMIT = "error.planner.lane.limit"
_LANE_PLATE = "error.planner.lane.plate"
_LANE_IDLE = "error.planner.lane.idle"
_LANE_DONE = "error.planner.lane.done"
_LANE_OUTSIDE = "error.planner.lane.outside"
_LANE_GAP = "error.planner.lane.gap"
_LANE_ARC = "error.planner.lane.arc"


@dataclass(frozen=True, slots=True)
class _Lane:
    """One open groove. ``tail`` is the first point before any segment exists."""

    spans: tuple[SlotSpan, ...]
    tail: tuple[float, float] | None
    finished: bool


@dataclass(frozen=True, slots=True)
class _State:
    points: tuple[tuple[float, float], ...]
    closed: bool
    lanes: tuple[_Lane, ...] = ()


def _finite_point(x_mm: float, y_mm: float) -> bool:
    return (
        not isinstance(x_mm, bool)
        and not isinstance(y_mm, bool)
        and isinstance(x_mm, (int, float))
        and isinstance(y_mm, (int, float))
        and math.isfinite(x_mm)
        and math.isfinite(y_mm)
    )


def _distance(left: tuple[float, float], right: tuple[float, float]) -> float:
    return math.hypot(right[0] - left[0], right[1] - left[1])


def _area_mm2(points: Sequence[tuple[float, float]]) -> float:
    total = 0.0
    for start, end in zip(points, (*points[1:], points[0]), strict=True):
        total += start[0] * end[1] - end[0] * start[1]
    return abs(total) / 2.0


def _edges_cross(points: Sequence[tuple[float, float]]) -> bool:
    count = len(points)
    if count < 4:
        return False

    def orient(
        start: tuple[float, float], end: tuple[float, float], point: tuple[float, float]
    ) -> float:
        return (end[0] - start[0]) * (point[1] - start[1]) - (end[1] - start[1]) * (
            point[0] - start[0]
        )

    def proper(index: int, other: int) -> bool:
        first, second = points[index], points[(index + 1) % count]
        third, fourth = points[other], points[(other + 1) % count]
        left = orient(first, second, third) * orient(first, second, fourth)
        right = orient(third, fourth, first) * orient(third, fourth, second)
        return left < 0.0 and right < 0.0

    for index in range(count):
        for other in range(index + 1, count):
            adjacent = other == index + 1 or (index == 0 and other == count - 1)
            if not adjacent and proper(index, other):
                return True
    return False


def plate_issues(points: Sequence[tuple[float, float]], *, closed: bool) -> tuple[str, ...]:
    """Reasons this contour cannot be stored. An open chain is not an error here."""
    if any(not _finite_point(point[0], point[1]) for point in points):
        return (_COORDS,)
    found: list[str] = []
    sequence = list(pairwise(points))
    if closed and len(points) >= 2:
        sequence.append((points[-1], points[0]))
    if any(_distance(start, end) <= _MIN_EDGE_MM for start, end in sequence):
        found.append(_ZERO)
    if not closed:
        return tuple(found)
    if len(points) < 3:
        found.append(_SHORT)
        return tuple(found)
    if _edges_cross(points):
        found.append(_CROSSING)
    elif _area_mm2(points) < _MIN_AREA_MM2:
        found.append(_DEGENERATE)
    return tuple(found)


def plate_part(
    points: Sequence[tuple[float, float]],
    lanes: Sequence[Sequence[SlotSpan]] = (),
) -> PartSpec:
    """One closed line plate and its grooves. Identity fields are placeholders."""
    issues = plate_issues(points, closed=True)
    if issues:
        raise ValidationError(issues[0])
    spans = contour_from_polygon(points)
    paths = tuple(
        SlotPath(tuple(path), PATH_SLOT, index + 1) for index, path in enumerate(lanes) if path
    )
    return PartSpec(
        article_number="V",
        scale="1:24",
        name="Vorschau",
        category=DRAFT,
        length_mm=None,
        width_mm=None,
        height_mm=None,
        radius_mm=None,
        angle_deg=None,
        # The column is required. Grooves live in ``slot_paths``; an empty list is not a lane.
        lane_count=max(1, len(paths)),
        connectors=(),
        outline=outline_from_spans(spans),
        slot_paths=paths,
        contours=(SlotPath(spans, PATH_CONTOUR),),
    )


def stored_plate(
    points: Sequence[tuple[float, float]],
    *,
    article_number: str,
    scale: str,
    name: str,
    manufacturer: str,
    description: str,
) -> PartSpec:
    """A plate ready for the library. Raises when the contour or the identity is invalid."""
    return with_metadata(
        plate_part(points),
        article_number=article_number,
        scale=scale,
        name=name,
        manufacturer=manufacturer,
        description=description,
    )


def stored_sketch(
    points: Sequence[tuple[float, float]],
    lanes: Sequence[Sequence[SlotSpan]],
    *,
    article_number: str,
    scale: str,
    name: str,
    manufacturer: str,
    description: str,
) -> PartSpec:
    """A plate and its grooves, still a draft. Raises when the body is invalid."""
    spec = plate_part(points, lanes)
    for path in spec.slot_paths:
        validate_path(
            GeometryPath(tuple(as_segment(span) for span in path.spans), "slot", path.lane)
        )
    return with_metadata(
        spec,
        article_number=article_number,
        scale=scale,
        name=name,
        manufacturer=manufacturer,
        description=description,
    )


def _outline_corners(spec: PartSpec) -> tuple[tuple[float, float], ...] | None:
    if not is_plate_draft(spec) or spec.connectors or spec.border_edges:
        return None
    if spec.attachment is not None or len(spec.contours) != 1:
        return None
    spans = spec.contours[0].spans
    if len(spans) < 3 or any(span.kind != SPAN_LINE for span in spans) or not is_closed(spans):
        return None
    for previous, span in pairwise(spans):
        if _distance((previous.x1, previous.y1), (span.x0, span.y0)) > _MIN_EDGE_MM:
            return None
    return tuple((span.x0, span.y0) for span in spans)


def draft_lanes(spec: PartSpec) -> tuple[tuple[SlotSpan, ...], ...] | None:
    """Editable grooves of a plate draft. ``None`` means the body must stay locked."""
    if not is_plate_draft(spec):
        return None
    lanes: list[tuple[SlotSpan, ...]] = []
    for path in spec.slot_paths:
        if path.kind != PATH_SLOT or path.slot is not None or not path.spans:
            return None
        if path.lane != len(lanes) + 1 or path.lane > MAX_LANE_COUNT:
            return None
        if any(span.kind not in {SPAN_LINE, SPAN_ARC} for span in path.spans):
            return None
        for previous, span in pairwise(path.spans):
            if _distance(span_end(previous), span_start(span)) > _MIN_EDGE_MM:
                return None
        try:
            validate_path(GeometryPath(tuple(as_segment(span) for span in path.spans), "slot"))
        except ValidationError:
            return None
        lanes.append(path.spans)
    return tuple(lanes)


def plate_corners(spec: PartSpec) -> tuple[tuple[float, float], ...] | None:
    """Corners of a line plate. ``None`` means this body must not be point-edited."""
    corners = _outline_corners(spec)
    if corners is None or draft_lanes(spec) is None:
        return None
    return corners


def _vertices_of(lane: _Lane) -> tuple[tuple[float, float], ...]:
    if not lane.spans:
        return () if lane.tail is None else (lane.tail,)
    points = [span_start(span) for span in lane.spans]
    points.append(span_end(lane.spans[-1]))
    return tuple(points)


def _arc_flags(span: SlotSpan) -> tuple[bool, bool]:
    """``(right, long)``. A half turn counts as the short variant."""
    return span.sweep_deg >= 0.0, abs(span.sweep_deg) > 180.0 + 1e-4


def _retarget(
    span: SlotSpan, start: tuple[float, float], end: tuple[float, float]
) -> SlotSpan | None:
    """Same kind of segment between new ends. ``None`` keeps the previous geometry."""
    if _distance(start, end) <= _MIN_EDGE_MM:
        return None
    if span.kind == SPAN_LINE:
        return SlotSpan.line(start[0], start[1], end[0], end[1])
    if span.kind != SPAN_ARC:
        return None
    right, long = _arc_flags(span)
    return arc_between(start, end, span.radius_mm, right=right, long=long)


def _chord(span: SlotSpan) -> float:
    return _distance(span_start(span), span_end(span))


def _failure(span: SlotSpan, start: tuple[float, float], end: tuple[float, float]) -> str:
    if _distance(start, end) <= _MIN_EDGE_MM:
        return _LANE_ZERO
    if span.kind == SPAN_ARC and span.radius_mm + 1e-6 < _distance(start, end) / 2.0:
        return _LANE_RADIUS
    return _LANE_ARC


def _inside(polygon: Sequence[tuple[float, float]], point: tuple[float, float]) -> bool:
    """True when ``point`` is inside the plate or on its boundary."""
    if len(polygon) < 3:
        return False
    edges = list(pairwise(polygon))
    edges.append((polygon[-1], polygon[0]))
    for start, end in edges:
        if (
            as_segment(SlotSpan.line(start[0], start[1], end[0], end[1])).closest(
                point[0], point[1]
            )[1]
            <= _MIN_EDGE_MM
        ):
            return True
    inside = False
    previous = polygon[-1]
    for corner in polygon:
        crosses = (corner[1] > point[1]) != (previous[1] > point[1])
        if crosses:
            span = (point[1] - corner[1]) / (previous[1] - corner[1])
            crossing = corner[0] + (previous[0] - corner[0]) * span
            if point[0] < crossing:
                inside = not inside
        previous = corner
    return inside


class PlateSession:
    """One floor plate: corners, selection, hover and undo."""

    def __init__(
        self,
        points: Sequence[tuple[float, float]] = (),
        *,
        closed: bool = False,
        lanes: Sequence[Sequence[SlotSpan]] = (),
    ) -> None:
        stored = tuple(_Lane(tuple(spans), None, True) for spans in lanes)
        self._state = _State(tuple((float(x), float(y)) for x, y in points), closed, stored)
        self._undo: list[_State] = []
        self._redo: list[_State] = []
        self._selected: int | None = None
        self._hover: tuple[float, float] | None = None
        self._preview: tuple[int, float, float] | None = None
        self._move_index: int | None = None
        self._lane_point: tuple[int, int] | None = None
        self._lane_span: tuple[int, int] | None = None
        self._active: int | None = len(stored) - 1 if stored else None
        self._lane_move: tuple[int, int] | None = None
        self._lane_preview: tuple[int, int, float, float] | None = None
        self._span_preview: tuple[int, int, SlotSpan] | None = None
        self._flash: str | None = None
        self._saved = self.geometry_token()

    @property
    def points(self) -> tuple[tuple[float, float], ...]:
        return self._state.points

    @property
    def closed(self) -> bool:
        return self._state.closed

    @property
    def selected(self) -> int | None:
        return self._selected

    @property
    def hover(self) -> tuple[float, float] | None:
        return self._hover

    @property
    def flash(self) -> str | None:
        return self._flash

    @property
    def lanes(self) -> tuple[_Lane, ...]:
        return self._state.lanes

    @property
    def active_lane(self) -> int | None:
        return self._active

    @property
    def lane_point(self) -> tuple[int, int] | None:
        return self._lane_point

    @property
    def lane_span(self) -> tuple[int, int] | None:
        return self._lane_span

    def geometry_token(self) -> tuple[object, ...]:
        lanes = tuple(
            (
                lane.finished,
                lane.tail,
                tuple(
                    (
                        span.kind,
                        span.x0,
                        span.y0,
                        span.x1,
                        span.y1,
                        span.radius_mm,
                        span.start_deg,
                        span.sweep_deg,
                    )
                    for span in lane.spans
                ),
            )
            for lane in self._state.lanes
        )
        return (self._state.points, self._state.closed, lanes)

    def capture(self) -> None:
        """Treat the current contour as the stored one."""
        self._saved = self.geometry_token()

    def geometry_dirty(self) -> bool:
        return self.geometry_token() != self._saved

    def display_points(self) -> tuple[tuple[float, float], ...]:
        preview = self._preview
        if preview is None:
            return self.points
        index, x_mm, y_mm = preview
        if not 0 <= index < len(self.points):
            return self.points
        items = list(self.points)
        items[index] = (x_mm, y_mm)
        return tuple(items)

    def can_undo(self) -> bool:
        return bool(self._undo)

    def can_redo(self) -> bool:
        return bool(self._redo)

    def can_close(self) -> bool:
        return not self.closed and not plate_issues(self.points, closed=True)

    def can_replace(self) -> bool:
        return bool(self.points) or self.closed

    def perimeter_mm(self) -> float | None:
        points = self.display_points()
        if len(points) < 2:
            return None
        pairs = list(pairwise(points))
        if self.closed:
            pairs.append((points[-1], points[0]))
        if not pairs:
            return None
        return sum(_distance(start, end) for start, end in pairs)

    def status_key(self) -> str | None:
        if self._flash:
            return self._flash
        if not self.closed:
            return None
        issues = plate_issues(self.display_points(), closed=True)
        return issues[0] if issues else None

    def save_issue(self) -> str | None:
        """Why the committed body must not be written. ``None`` means it may."""
        if self.lanes and not self.closed:
            return _LANE_PLATE
        if not self.closed:
            return _SHORT if len(self.points) < 3 else _OPEN
        issues = plate_issues(self.points, closed=True)
        if issues:
            return issues[0]
        return self._lane_save_issue()

    def set_hover(self, point: tuple[float, float] | None) -> None:
        if point is not None and not _finite_point(point[0], point[1]):
            point = None
        self._hover = None if point is None else (float(point[0]), float(point[1]))

    def select(self, index: int | None) -> None:
        if index is not None and not 0 <= index < len(self.display_points()):
            index = None
        self._selected = index
        if index is not None:
            self._lane_point = None
            self._lane_span = None

    def hit(self, x_mm: float, y_mm: float, tolerance_mm: float) -> int | None:
        if not _finite_point(x_mm, y_mm) or tolerance_mm < 0:
            return None
        best: int | None = None
        best_distance = tolerance_mm
        for index, point in enumerate(self.display_points()):
            distance = _distance(point, (x_mm, y_mm))
            if distance <= tolerance_mm and (best is None or distance < best_distance):
                best = index
                best_distance = distance
        return best

    def near_start(self, x_mm: float, y_mm: float, tolerance_mm: float) -> bool:
        if not self.points or self.closed or not _finite_point(x_mm, y_mm):
            return False
        return _distance(self.points[0], (x_mm, y_mm)) <= tolerance_mm

    def add_point(self, x_mm: float, y_mm: float) -> bool:
        self._flash = None
        if self.closed:
            self._flash = _EXISTS
            return False
        if not _finite_point(x_mm, y_mm):
            self._flash = _COORDS
            return False
        point = (float(x_mm), float(y_mm))
        if self.points and _distance(self.points[-1], point) <= _MIN_EDGE_MM:
            self._flash = _ZERO
            return False
        self._commit(_State((*self.points, point), False, self.lanes))
        self._selected = len(self.points) - 1
        return True

    def close(self) -> bool:
        self.cancel_move()
        self._flash = None
        if self.closed:
            return False
        issues = plate_issues(self.points, closed=True)
        if issues:
            self._flash = issues[0]
            return False
        self._commit(_State(self.points, True, self.lanes))
        self._hover = None
        return True

    def replace(self) -> None:
        """Drop the plate so a new one can be drawn. Undo restores it."""
        self.cancel_move()
        if not self.can_replace():
            return
        self._flash = None
        self._hover = None
        self._selected = None
        self._commit(_State((), False, self.lanes))

    def cancel_open(self) -> None:
        """Drop an unfinished chain. A closed plate only loses its selection."""
        self.cancel_move()
        self._flash = None
        self._hover = None
        if self.closed or not self.points:
            self._selected = None
            return
        self._selected = None
        self._commit(_State((), False, self.lanes))

    def reject_second(self) -> None:
        self._flash = _EXISTS

    def begin_move(self, index: int) -> None:
        if not 0 <= index < len(self.points):
            return
        self._move_index = index
        self._lane_move = None
        self._lane_preview = None
        self._preview = None
        self._selected = index
        self._lane_point = None
        self._lane_span = None

    def preview_move(self, x_mm: float, y_mm: float) -> None:
        if self._lane_move is not None:
            self._preview_lane_vertex(*self._lane_move, x_mm, y_mm)
            return
        if self._move_index is None:
            return
        if not _finite_point(x_mm, y_mm):
            self._flash = _COORDS
            return
        self._preview = (self._move_index, float(x_mm), float(y_mm))

    def preview_selected(self, x_mm: float, y_mm: float) -> None:
        if self._lane_point is not None:
            self._preview_lane_vertex(*self._lane_point, x_mm, y_mm)
            return
        if self._selected is None or not _finite_point(x_mm, y_mm):
            return
        self._preview = (self._selected, float(x_mm), float(y_mm))

    def commit_move(self) -> None:
        lane_preview = self._lane_preview
        self._lane_preview = None
        self._lane_move = None
        preview = self._preview
        self._preview = None
        self._move_index = None
        if lane_preview is not None:
            lane, index, x_mm, y_mm = lane_preview
            self._write_lane_vertex(lane, index, x_mm, y_mm)
            return
        if preview is None:
            return
        index, x_mm, y_mm = preview
        self._write_point(index, x_mm, y_mm)

    def commit_selected(self, x_mm: float, y_mm: float) -> bool:
        self._preview = None
        self._lane_preview = None
        if self._lane_point is not None:
            lane, index = self._lane_point
            return self._write_lane_vertex(lane, index, x_mm, y_mm)
        if self._selected is None:
            return False
        return self._write_point(self._selected, x_mm, y_mm)

    def cancel_move(self) -> None:
        self._preview = None
        self._move_index = None
        self._lane_preview = None
        self._lane_move = None
        self._span_preview = None

    def undo(self) -> bool:
        self.cancel_move()
        if not self._undo:
            return False
        self._redo.append(self._state)
        self._state = self._undo.pop()
        self._repair_selection()
        self._flash = None
        self._hover = None
        return True

    def redo(self) -> bool:
        self.cancel_move()
        if not self._redo:
            return False
        self._undo.append(self._state)
        self._state = self._redo.pop()
        self._repair_selection()
        self._flash = None
        self._hover = None
        return True

    def _write_point(self, index: int, x_mm: float, y_mm: float) -> bool:
        if not 0 <= index < len(self.points):
            return False
        if not _finite_point(x_mm, y_mm):
            self._flash = _COORDS
            return False
        point = (float(x_mm), float(y_mm))
        if self.points[index] == point:
            return False
        items = list(self.points)
        items[index] = point
        self._flash = None
        self._commit(_State(tuple(items), self.closed, self.lanes))
        self._selected = index
        return True

    def _commit(self, state: _State) -> None:
        if state == self._state:
            return
        self._undo.append(self._state)
        if len(self._undo) > _HISTORY:
            self._undo.pop(0)
        self._state = state
        self._redo.clear()

    def _repair_selection(self) -> None:
        if self._selected is not None and not 0 <= self._selected < len(self.points):
            self._selected = None
        if self._active is not None and not 0 <= self._active < len(self.lanes):
            self._active = len(self.lanes) - 1 if self.lanes else None
        point = self._lane_point
        if point is not None and not self._vertex_exists(*point):
            self._lane_point = None
        span = self._lane_span
        if span is not None and not self._span_exists(*span):
            self._lane_span = None

    def lane_vertices(self, index: int) -> tuple[tuple[float, float], ...]:
        return self._shown_vertices(index)

    def lane_spans(self, index: int) -> tuple[SlotSpan, ...]:
        return self._shown_spans(index)

    def lane_finished(self, index: int) -> bool:
        return self.lanes[index].finished

    def unfinished_lane(self) -> int | None:
        for index, lane in enumerate(self.lanes):
            if not lane.finished:
                return index
        return None

    def can_add_lane(self) -> bool:
        return self.closed and self.unfinished_lane() is None and len(self.lanes) < MAX_LANE_COUNT

    def can_finish_lane(self) -> bool:
        index = self._active
        return (
            index is not None
            and 0 <= index < len(self.lanes)
            and not self.lanes[index].finished
            and bool(self.lanes[index].spans)
        )

    def can_remove_lane(self) -> bool:
        return self._focus_lane() is not None

    def can_insert_point(self) -> bool:
        span = self._lane_span
        if span is None or not self._span_exists(*span):
            return False
        return self.lanes[span[0]].spans[span[1]].kind == SPAN_LINE

    def can_remove_lane_point(self) -> bool:
        point = self._lane_point
        if point is None or not self._vertex_exists(*point):
            return False
        lane, index = point
        vertices = _vertices_of(self.lanes[lane])
        spans = self.lanes[lane].spans
        if not 0 < index < len(vertices) - 1:
            return False
        return spans[index - 1].kind == SPAN_LINE and spans[index].kind == SPAN_LINE

    def lane_length_mm(self, index: int) -> float | None:
        spans = self._shown_spans(index)
        if not spans:
            return None
        return spans_length_mm(spans)

    def segment_length_mm(self) -> float | None:
        span = self._shown_selected_span()
        if span is None:
            return None
        return span_length_mm(span)

    def selected_span(self) -> SlotSpan | None:
        return self._shown_selected_span()

    def point_to_other_lanes_mm(self) -> float | None:
        """Shortest distance from the selected lane point to another lane.

        This is a point-to-path distance, not a constant lane pitch.
        """
        point = self._shown_lane_point()
        if point is None:
            return None
        lane_index, vertex = point
        origin = self._shown_vertices(lane_index)[vertex]
        best: float | None = None
        for index, _lane in enumerate(self.lanes):
            if index == lane_index:
                continue
            for span in self._shown_spans(index):
                _t, distance = as_segment(span).closest(origin[0], origin[1])
                best = distance if best is None else min(best, distance)
        return best

    def point_to_plate_edge_mm(self) -> float | None:
        """Shortest distance from the selected lane point to the plate boundary."""
        point = self._shown_lane_point()
        if point is None or len(self.display_points()) < 2:
            return None
        origin = self._shown_vertices(point[0])[point[1]]
        corners = self.display_points()
        edges = list(pairwise(corners))
        if self.closed:
            edges.append((corners[-1], corners[0]))
        best: float | None = None
        for start, end in edges:
            segment = SlotSpan.line(start[0], start[1], end[0], end[1])
            _t, distance = as_segment(segment).closest(origin[0], origin[1])
            best = distance if best is None else min(best, distance)
        return best

    def warning_key(self) -> str | None:
        if self._lanes_outside():
            return _LANE_OUTSIDE
        return None

    def add_lane(self) -> bool:
        self._flash = None
        if not self.closed:
            self._flash = _LANE_PLATE
            return False
        if self.unfinished_lane() is not None:
            self._flash = _LANE_OPEN
            return False
        if len(self.lanes) >= MAX_LANE_COUNT:
            self._flash = _LANE_LIMIT
            return False
        lanes = (*self.lanes, _Lane((), None, False))
        self._commit(_State(self.points, self.closed, lanes))
        self._active = len(self.lanes) - 1
        self._selected = None
        self._lane_point = None
        self._lane_span = None
        return True

    def add_lane_point(self, x_mm: float, y_mm: float) -> bool:
        self._flash = None
        index = self.unfinished_lane()
        if index is None:
            self._flash = _LANE_DONE if self.lanes else _LANE_IDLE
            return False
        if not _finite_point(x_mm, y_mm):
            self._flash = _COORDS
            return False
        point = (float(x_mm), float(y_mm))
        lane = self.lanes[index]
        vertices = _vertices_of(lane)
        if vertices and _distance(vertices[-1], point) <= _MIN_EDGE_MM:
            self._flash = _LANE_ZERO
            return False
        if not vertices:
            updated = _Lane((), point, False)
        else:
            start = vertices[-1]
            span = SlotSpan.line(start[0], start[1], point[0], point[1])
            updated = _Lane((*lane.spans, span), None, False)
        self._put_lane(index, updated)
        self._active = index
        self._lane_point = (index, len(_vertices_of(updated)) - 1)
        self._lane_span = None
        self._selected = None
        return True

    def finish_lane(self) -> bool:
        self.cancel_move()
        self._flash = None
        index = self._active if self._active is not None else self.unfinished_lane()
        if index is None or not 0 <= index < len(self.lanes):
            self._flash = _LANE_IDLE
            return False
        lane = self.lanes[index]
        if lane.finished:
            return False
        if not lane.spans:
            self._flash = _LANE_SHORT
            return False
        issue = self._spans_issue(lane.spans)
        if issue is not None:
            self._flash = issue
            return False
        self._put_lane(index, _Lane(lane.spans, None, True))
        self._hover = None
        self._active = index
        return True

    def remove_lane(self) -> bool:
        self.cancel_move()
        index = self._focus_lane()
        if index is None:
            return False
        self._flash = None
        lanes = self.lanes[:index] + self.lanes[index + 1 :]
        self._commit(_State(self.points, self.closed, lanes))
        self._active = min(index, len(self.lanes) - 1) if self.lanes else None
        self._lane_point = None
        self._lane_span = None
        return True

    def cancel_lane(self) -> None:
        """Drop the lane that is still being drawn. A finished lane only loses its selection."""
        self.cancel_move()
        self._flash = None
        self._hover = None
        index = self.unfinished_lane()
        if index is None:
            self._lane_point = None
            self._lane_span = None
            return
        self._active = index
        self.remove_lane()

    def select_lane_point(self, lane: int, index: int) -> None:
        if not self._vertex_exists(lane, index):
            self._lane_point = None
            return
        self._lane_point = (lane, index)
        self._lane_span = None
        self._selected = None
        self._active = lane

    def select_lane_span(self, lane: int, index: int) -> None:
        if not self._span_exists(lane, index):
            self._lane_span = None
            return
        self._lane_span = (lane, index)
        self._lane_point = None
        self._selected = None
        self._active = lane

    def clear_pick(self) -> None:
        self._selected = None
        self._lane_point = None
        self._lane_span = None

    def begin_lane_move(self, lane: int, index: int) -> None:
        if not self._vertex_exists(lane, index):
            return
        self._lane_move = (lane, index)
        self._lane_preview = None
        self._move_index = None
        self._preview = None
        self.select_lane_point(lane, index)

    def convert_to_line(self) -> bool:
        span = self._committed_selected_span()
        if span is None or self._lane_span is None:
            return False
        if span.kind == SPAN_LINE:
            return False
        start, end = span_start(span), span_end(span)
        return self._replace_selected_span(SlotSpan.line(start[0], start[1], end[0], end[1]))

    def convert_to_arc(self) -> bool:
        span = self._committed_selected_span()
        if span is None:
            return False
        if span.kind == SPAN_ARC:
            return False
        start, end = span_start(span), span_end(span)
        chord = _distance(start, end)
        built = arc_between(start, end, chord, right=True, long=False)
        if built is None:
            self._flash = _LANE_ARC
            return False
        return self._replace_selected_span(built)

    def set_arc(self, radius_mm: float, *, right: bool, long: bool) -> bool:
        span = self._committed_selected_span()
        if span is None or span.kind != SPAN_ARC:
            return False
        built = arc_between(span_start(span), span_end(span), radius_mm, right=right, long=long)
        if built is None:
            self._flash = _LANE_RADIUS if radius_mm < _chord(span) / 2.0 else _LANE_ARC
            return False
        return self._replace_selected_span(built)

    def preview_span_radius(self, radius_mm: float) -> bool:
        chosen = self._lane_span
        span = self._committed_selected_span()
        if chosen is None or span is None or span.kind != SPAN_ARC:
            return False
        right, long = _arc_flags(span)
        built = arc_between(span_start(span), span_end(span), radius_mm, right=right, long=long)
        if built is None:
            self._span_preview = None
            self._flash = _LANE_RADIUS
            return False
        self._flash = None
        self._span_preview = (chosen[0], chosen[1], built)
        return True

    def commit_span_radius(self, radius_mm: float) -> bool:
        self._span_preview = None
        span = self._committed_selected_span()
        if span is None or span.kind != SPAN_ARC:
            return False
        right, long = _arc_flags(span)
        return self.set_arc(radius_mm, right=right, long=long)

    def insert_point(self) -> bool:
        chosen = self._lane_span
        if chosen is None or not self.can_insert_point():
            return False
        lane, index = chosen
        span = self.lanes[lane].spans[index]
        midpoint = as_segment(span).point_at(0.5)
        start, end = span_start(span), span_end(span)
        left = SlotSpan.line(start[0], start[1], midpoint[0], midpoint[1])
        right = SlotSpan.line(midpoint[0], midpoint[1], end[0], end[1])
        spans = (
            *self.lanes[lane].spans[:index],
            left,
            right,
            *self.lanes[lane].spans[index + 1 :],
        )
        self._put_lane(lane, _Lane(spans, None, self.lanes[lane].finished))
        self._lane_point = (lane, index + 1)
        self._lane_span = None
        self._flash = None
        return True

    def remove_lane_point(self) -> bool:
        if not self.can_remove_lane_point() or self._lane_point is None:
            return False
        lane, index = self._lane_point
        spans = self.lanes[lane].spans
        start = span_start(spans[index - 1])
        end = span_end(spans[index])
        if _distance(start, end) <= _MIN_EDGE_MM:
            self._flash = _LANE_ZERO
            return False
        joined = SlotSpan.line(start[0], start[1], end[0], end[1])
        updated = (*spans[: index - 1], joined, *spans[index + 1 :])
        self._put_lane(lane, _Lane(updated, None, self.lanes[lane].finished))
        self._lane_point = None
        self._lane_span = (lane, index - 1)
        self._flash = None
        return True

    def pick(self, x_mm: float, y_mm: float, tolerance_mm: float) -> tuple[str, int, int] | None:
        """Nearest plate corner, lane vertex or lane segment inside the tolerance."""
        if not _finite_point(x_mm, y_mm) or tolerance_mm < 0:
            return None
        best: tuple[str, int, int] | None = None
        best_distance = tolerance_mm + 1.0
        for index, point in enumerate(self.display_points()):
            distance = _distance(point, (x_mm, y_mm))
            if distance <= tolerance_mm and distance < best_distance:
                best = ("plate", index, 0)
                best_distance = distance
        for lane, _item in enumerate(self.lanes):
            for index, point in enumerate(self._shown_vertices(lane)):
                distance = _distance(point, (x_mm, y_mm))
                if distance <= tolerance_mm and distance < best_distance:
                    best = ("point", lane, index)
                    best_distance = distance
        if best is not None:
            return best
        for lane, _item in enumerate(self.lanes):
            for index, span in enumerate(self._shown_spans(lane)):
                _t, distance = as_segment(span).closest(x_mm, y_mm)
                if distance <= tolerance_mm and distance < best_distance:
                    best = ("span", lane, index)
                    best_distance = distance
        return best

    def _lane_save_issue(self) -> str | None:
        if len(self.lanes) > MAX_LANE_COUNT:
            return _LANE_LIMIT
        for lane in self.lanes:
            if not lane.finished or lane.tail is not None or not lane.spans:
                return _LANE_OPEN if lane.spans or lane.tail is not None else _LANE_SHORT
            issue = self._spans_issue(lane.spans)
            if issue is not None:
                return issue
        return None

    def _spans_issue(self, spans: Sequence[SlotSpan]) -> str | None:
        if not spans:
            return _LANE_SHORT
        try:
            validate_path(GeometryPath(tuple(as_segment(span) for span in spans), "slot"))
        except ValidationError:
            return _LANE_GAP
        for span in spans:
            if span_length_mm(span) <= _MIN_EDGE_MM:
                return _LANE_ZERO
            if span.kind == SPAN_ARC and span.radius_mm + 1e-6 < _chord(span) / 2.0:
                return _LANE_RADIUS
        return None

    def _lanes_outside(self) -> bool:
        if not self.closed or len(self.points) < 3:
            return False
        for lane in self.lanes:
            for span in lane.spans:
                segment = as_segment(span)
                for step in range(5):
                    if not _inside(self.points, segment.point_at(step / 4.0)):
                        return True
        return False

    def _focus_lane(self) -> int | None:
        if self._lane_point is not None:
            return self._lane_point[0]
        if self._lane_span is not None:
            return self._lane_span[0]
        if self._active is not None and 0 <= self._active < len(self.lanes):
            return self._active
        return None

    def _put_lane(self, index: int, lane: _Lane) -> None:
        lanes = list(self.lanes)
        lanes[index] = lane
        self._commit(_State(self.points, self.closed, tuple(lanes)))

    def _replace_selected_span(self, span: SlotSpan) -> bool:
        chosen = self._lane_span
        if chosen is None:
            return False
        lane, index = chosen
        current = self.lanes[lane]
        spans = list(current.spans)
        spans[index] = span
        issue = self._spans_issue(spans)
        if issue is not None:
            self._flash = issue
            return False
        self._flash = None
        self._span_preview = None
        self._put_lane(lane, _Lane(tuple(spans), None, current.finished))
        self._lane_span = (lane, index)
        return True

    def _preview_lane_vertex(self, lane: int, index: int, x_mm: float, y_mm: float) -> None:
        if not _finite_point(x_mm, y_mm):
            self._flash = _COORDS
            self._lane_preview = None
            return
        updated = self._moved_lane(lane, index, float(x_mm), float(y_mm))
        if updated is None:
            self._lane_preview = None
            self._flash = self._move_issue(lane, index, float(x_mm), float(y_mm))
            return
        self._flash = None
        self._lane_preview = (lane, index, float(x_mm), float(y_mm))

    def _write_lane_vertex(self, lane: int, index: int, x_mm: float, y_mm: float) -> bool:
        if not _finite_point(x_mm, y_mm):
            self._flash = _COORDS
            return False
        updated = self._moved_lane(lane, index, float(x_mm), float(y_mm))
        if updated is None:
            self._flash = self._move_issue(lane, index, float(x_mm), float(y_mm))
            return False
        if updated == self.lanes[lane]:
            return False
        self._flash = None
        self._put_lane(lane, updated)
        self._lane_point = (lane, index)
        self._active = lane
        return True

    def _moved_lane(self, lane: int, index: int, x_mm: float, y_mm: float) -> _Lane | None:
        current = self.lanes[lane]
        vertices = list(_vertices_of(current))
        if not 0 <= index < len(vertices):
            return None
        point = (x_mm, y_mm)
        if vertices[index] == point:
            return current
        vertices[index] = point
        if not current.spans:
            return _Lane((), point, False)
        spans = list(current.spans)
        if index > 0:
            rebuilt = _retarget(spans[index - 1], vertices[index - 1], point)
            if rebuilt is None:
                return None
            spans[index - 1] = rebuilt
        if index < len(spans):
            rebuilt = _retarget(spans[index], point, vertices[index + 1])
            if rebuilt is None:
                return None
            spans[index] = rebuilt
        return _Lane(tuple(spans), None, current.finished)

    def _move_issue(self, lane: int, index: int, x_mm: float, y_mm: float) -> str:
        current = self.lanes[lane]
        vertices = list(_vertices_of(current))
        point = (x_mm, y_mm)
        if index > 0:
            previous = vertices[index - 1]
            if _retarget(current.spans[index - 1], previous, point) is None:
                return _failure(current.spans[index - 1], previous, point)
        if index < len(current.spans):
            following = vertices[index + 1]
            if _retarget(current.spans[index], point, following) is None:
                return _failure(current.spans[index], point, following)
        return _LANE_ARC

    def _shown_vertices(self, index: int) -> tuple[tuple[float, float], ...]:
        if not 0 <= index < len(self.lanes):
            return ()
        preview = self._lane_preview
        if preview is None or preview[0] != index:
            return _vertices_of(self.lanes[index])
        updated = self._moved_lane(preview[0], preview[1], preview[2], preview[3])
        if updated is None:
            return _vertices_of(self.lanes[index])
        return _vertices_of(updated)

    def _shown_spans(self, index: int) -> tuple[SlotSpan, ...]:
        if not 0 <= index < len(self.lanes):
            return ()
        spans = self.lanes[index].spans
        preview = self._lane_preview
        if preview is not None and preview[0] == index:
            updated = self._moved_lane(*preview)
            if updated is not None:
                spans = updated.spans
        override = self._span_preview
        if override is not None and override[0] == index and 0 <= override[1] < len(spans):
            items = list(spans)
            items[override[1]] = override[2]
            return tuple(items)
        return spans

    def _shown_lane_point(self) -> tuple[int, int] | None:
        point = self._lane_point
        if point is None or not 0 <= point[0] < len(self.lanes):
            return None
        if not 0 <= point[1] < len(self._shown_vertices(point[0])):
            return None
        return point

    def _shown_selected_span(self) -> SlotSpan | None:
        chosen = self._lane_span
        if chosen is None:
            return None
        spans = self._shown_spans(chosen[0])
        if not 0 <= chosen[1] < len(spans):
            return None
        return spans[chosen[1]]

    def _committed_selected_span(self) -> SlotSpan | None:
        chosen = self._lane_span
        if chosen is None or not self._span_exists(*chosen):
            return None
        return self.lanes[chosen[0]].spans[chosen[1]]

    def _vertex_exists(self, lane: int, index: int) -> bool:
        return 0 <= lane < len(self.lanes) and 0 <= index < len(_vertices_of(self.lanes[lane]))

    def _span_exists(self, lane: int, index: int) -> bool:
        return 0 <= lane < len(self.lanes) and 0 <= index < len(self.lanes[lane].spans)

    def sketch_lanes(self) -> tuple[tuple[SlotSpan, ...], ...]:
        """Grooves that painting and saving use. A dangling first point is omitted."""
        return tuple(self._shown_spans(index) for index in range(len(self.lanes)))
