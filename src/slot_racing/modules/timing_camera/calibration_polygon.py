"""Search polygon for one calibration run.

Points are fractions of the camera frame, so the same outline survives a
different preview size or capture resolution. The polygon is only the area
the calibration may search. A race keeps using the rectangular zone and does
not mask the picture with this outline.
"""

from __future__ import annotations

from dataclasses import dataclass

from slot_racing.modules.timing_camera.configuration import NormalizedRoi

MIN_POINTS = 3
MAX_POINTS = 24
_AREA_EPSILON = 1e-8


@dataclass(frozen=True, slots=True)
class CalibrationPolygon:
    """A closed outline. Fewer than three points, or a crossing outline, is not usable."""

    points: tuple[tuple[float, float], ...]

    def __post_init__(self) -> None:
        if not isinstance(self.points, tuple):
            raise TypeError("points must be a tuple")
        for point in self.points:
            if (
                not isinstance(point, tuple)
                or len(point) != 2
                or isinstance(point[0], bool)
                or isinstance(point[1], bool)
                or not isinstance(point[0], (int, float))
                or not isinstance(point[1], (float, int))
            ):
                raise TypeError("a point must be a pair of numbers")

    @property
    def problem(self) -> str | None:
        """A stable code when the outline cannot be searched, otherwise ``None``."""
        count = len(self.points)
        if count < MIN_POINTS:
            return "too_few_points"
        if count > MAX_POINTS:
            return "too_many_points"
        for x, y in self.points:
            if x < 0 or y < 0 or x > 1 or y > 1:
                return "outside_frame"
        if len(set(self.points)) != count:
            return "duplicate_point"
        # A bowtie can cancel to zero area. The crossing is the reason it is rejected.
        if _self_intersects(self.points):
            return "self_intersection"
        if abs(_area(self.points)) <= _AREA_EPSILON:
            return "empty"
        return None

    @property
    def is_valid(self) -> bool:
        return self.problem is None

    def moved(self, index: int, x: float, y: float) -> CalibrationPolygon:
        """A copy with one point in a new place. The result may be invalid."""
        if index < 0 or index >= len(self.points):
            raise IndexError("point index is outside the polygon")
        points = list(self.points)
        points[index] = (float(x), float(y))
        return CalibrationPolygon(tuple(points))

    def without(self, index: int) -> CalibrationPolygon:
        """A copy that no longer contains one point."""
        if index < 0 or index >= len(self.points):
            raise IndexError("point index is outside the polygon")
        points = list(self.points)
        del points[index]
        return CalibrationPolygon(tuple(points))

    def added(self, x: float, y: float) -> CalibrationPolygon:
        """A copy with one more point. The technical limit is :data:`MAX_POINTS`."""
        if len(self.points) >= MAX_POINTS:
            raise ValueError("too_many_points")
        return CalibrationPolygon((*self.points, (float(x), float(y))))


def contains_point(polygon: CalibrationPolygon, x: float, y: float) -> bool:
    """Whether one normalized point lies inside or on the outline."""
    if not polygon.is_valid:
        return False
    if x < 0 or y < 0 or x > 1 or y > 1:
        return False
    return _contains(polygon.points, (x, y))


def rectangle_inside_polygon(
    polygon: CalibrationPolygon,
    roi: NormalizedRoi,
    *,
    margin: float = 0.0,
) -> bool:
    """Whether ``roi``, grown by ``margin`` on every side, lies inside the outline.

    Touching the outline is allowed. A rectangle that leaves a concave outline,
    or that would cross into a neighbouring lane beyond the polygon, is not.
    ``margin`` is a fraction of the frame, the same unit as the polygon.
    """
    if not polygon.is_valid:
        return False
    if margin < 0:
        raise ValueError("margin must not be negative")
    left = roi.x - margin
    top = roi.y - margin
    right = roi.x + roi.width + margin
    bottom = roi.y + roi.height + margin
    if left < 0 or top < 0 or right > 1 + 1e-9 or bottom > 1 + 1e-9:
        return False
    corners = ((left, top), (right, top), (right, bottom), (left, bottom))
    if any(not _contains(polygon.points, corner) for corner in corners):
        return False
    edges = (
        (corners[0], corners[1]),
        (corners[1], corners[2]),
        (corners[2], corners[3]),
        (corners[3], corners[0]),
    )
    ring = _ring(polygon.points)
    for start, end in edges:
        for index in range(len(ring) - 1):
            if _proper_intersect(start, end, ring[index], ring[index + 1]):
                return False
    return True


def rois_overlap(left: NormalizedRoi, right: NormalizedRoi) -> bool:
    """Shared area. Edges that only touch do not count as an overlap."""
    return not (
        left.x + left.width <= right.x
        or right.x + right.width <= left.x
        or left.y + left.height <= right.y
        or right.y + right.height <= left.y
    )


def _area(points: tuple[tuple[float, float], ...]) -> float:
    total = 0.0
    for index, (x_value, y_value) in enumerate(points):
        next_x, next_y = points[(index + 1) % len(points)]
        total += x_value * next_y - next_x * y_value
    return total / 2


def _self_intersects(points: tuple[tuple[float, float], ...]) -> bool:
    ring = _ring(points)
    count = len(points)
    for index in range(count):
        for other in range(index + 1, count):
            if other == index or (other + 1) % count == index or (index + 1) % count == other:
                continue
            if _proper_intersect(ring[index], ring[index + 1], ring[other], ring[other + 1]):
                return True
    return False


def _ring(points: tuple[tuple[float, float], ...]) -> tuple[tuple[float, float], ...]:
    return (*points, points[0])


def _contains(points: tuple[tuple[float, float], ...], point: tuple[float, float]) -> bool:
    """Even-odd test. A point on the boundary counts as inside."""
    x_value, y_value = point
    for index in range(len(points)):
        start = points[index]
        end = points[(index + 1) % len(points)]
        if _on_segment(start, end, point):
            return True
    inside = False
    for index in range(len(points)):
        x1, y1 = points[index]
        x2, y2 = points[(index + 1) % len(points)]
        if (y1 > y_value) == (y2 > y_value):
            continue
        cross = (x2 - x1) * (y_value - y1) / (y2 - y1) + x1
        if x_value < cross:
            inside = not inside
    return inside


def _on_segment(
    start: tuple[float, float], end: tuple[float, float], point: tuple[float, float]
) -> bool:
    cross = _orient(start, end, point)
    if abs(cross) > 1e-9:
        return False
    return (
        min(start[0], end[0]) - 1e-9 <= point[0] <= max(start[0], end[0]) + 1e-9
        and min(start[1], end[1]) - 1e-9 <= point[1] <= max(start[1], end[1]) + 1e-9
    )


def _proper_intersect(
    a_start: tuple[float, float],
    a_end: tuple[float, float],
    b_start: tuple[float, float],
    b_end: tuple[float, float],
) -> bool:
    first = _orient(a_start, a_end, b_start)
    second = _orient(a_start, a_end, b_end)
    third = _orient(b_start, b_end, a_start)
    fourth = _orient(b_start, b_end, a_end)
    if first == 0 or second == 0 or third == 0 or fourth == 0:
        return False
    return (first > 0) != (second > 0) and (third > 0) != (fourth > 0)


def _orient(
    start: tuple[float, float], end: tuple[float, float], point: tuple[float, float]
) -> float:
    return (end[0] - start[0]) * (point[1] - start[1]) - (end[1] - start[1]) * (point[0] - start[0])
