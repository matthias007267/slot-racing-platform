"""Qt-free floor-plate session for the graphical part editor.

One part has one closed line contour. The session stores that contour, the
selected corner and an undo stack. Hover and an in-progress drag are not
geometry. Nothing here imports Qt.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass
from itertools import pairwise

from slot_racing.core.errors import ValidationError
from slot_racing.modules.track_planner.editing import (
    contour_from_polygon,
    is_closed,
    outline_from_spans,
)
from slot_racing.modules.track_planner.geometry import POSITION_GAP_MM
from slot_racing.modules.track_planner.parametric import with_metadata
from slot_racing.modules.track_planner.parts import (
    DRAFT,
    PATH_CONTOUR,
    SPAN_LINE,
    PartSpec,
    SlotPath,
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


@dataclass(frozen=True, slots=True)
class _State:
    points: tuple[tuple[float, float], ...]
    closed: bool


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


def plate_part(points: Sequence[tuple[float, float]]) -> PartSpec:
    """One closed line plate. Identity fields are placeholders until :func:`stored_plate`."""
    issues = plate_issues(points, closed=True)
    if issues:
        raise ValidationError(issues[0])
    spans = contour_from_polygon(points)
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
        # The column is required. A draft has no driving lane; painters must not invent one.
        lane_count=1,
        connectors=(),
        outline=outline_from_spans(spans),
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


def plate_corners(spec: PartSpec) -> tuple[tuple[float, float], ...] | None:
    """Corners of a pure line plate. ``None`` means this body must not be point-edited."""
    if not is_plate_draft(spec) or spec.connectors or spec.slot_paths or spec.border_edges:
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


class PlateSession:
    """One floor plate: corners, selection, hover and undo."""

    def __init__(
        self,
        points: Sequence[tuple[float, float]] = (),
        *,
        closed: bool = False,
    ) -> None:
        self._state = _State(tuple((float(x), float(y)) for x, y in points), closed)
        self._undo: list[_State] = []
        self._redo: list[_State] = []
        self._selected: int | None = None
        self._hover: tuple[float, float] | None = None
        self._preview: tuple[int, float, float] | None = None
        self._move_index: int | None = None
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

    def geometry_token(self) -> tuple[object, ...]:
        return (self._state.points, self._state.closed)

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
        """Why the committed contour must not be written. ``None`` means it may."""
        if not self.closed:
            return _SHORT if len(self.points) < 3 else _OPEN
        issues = plate_issues(self.points, closed=True)
        return issues[0] if issues else None

    def set_hover(self, point: tuple[float, float] | None) -> None:
        if point is not None and not _finite_point(point[0], point[1]):
            point = None
        self._hover = None if point is None else (float(point[0]), float(point[1]))

    def select(self, index: int | None) -> None:
        if index is not None and not 0 <= index < len(self.display_points()):
            index = None
        self._selected = index

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
        self._commit(_State((*self.points, point), False))
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
        self._commit(_State(self.points, True))
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
        self._commit(_State((), False))

    def cancel_open(self) -> None:
        """Drop an unfinished chain. A closed plate only loses its selection."""
        self.cancel_move()
        self._flash = None
        self._hover = None
        if self.closed or not self.points:
            self._selected = None
            return
        self._selected = None
        self._commit(_State((), False))

    def reject_second(self) -> None:
        self._flash = _EXISTS

    def begin_move(self, index: int) -> None:
        if not 0 <= index < len(self.points):
            return
        self._move_index = index
        self._preview = None
        self._selected = index

    def preview_move(self, x_mm: float, y_mm: float) -> None:
        if self._move_index is None:
            return
        if not _finite_point(x_mm, y_mm):
            self._flash = _COORDS
            return
        self._preview = (self._move_index, float(x_mm), float(y_mm))

    def preview_selected(self, x_mm: float, y_mm: float) -> None:
        if self._selected is None or not _finite_point(x_mm, y_mm):
            return
        self._preview = (self._selected, float(x_mm), float(y_mm))

    def commit_move(self) -> None:
        preview = self._preview
        self._preview = None
        self._move_index = None
        if preview is None:
            return
        index, x_mm, y_mm = preview
        self._write_point(index, x_mm, y_mm)

    def commit_selected(self, x_mm: float, y_mm: float) -> bool:
        self._preview = None
        if self._selected is None:
            return False
        return self._write_point(self._selected, x_mm, y_mm)

    def cancel_move(self) -> None:
        self._preview = None
        self._move_index = None

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
        self._commit(_State(tuple(items), self.closed))
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
