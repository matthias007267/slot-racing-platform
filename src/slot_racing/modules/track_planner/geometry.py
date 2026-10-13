"""Shared path geometry for rails, grooves and outlines.

Coordinates are millimetres. ``0°`` points along +x and positive angles run
toward +y, the same convention as the plan. Rotation is clockwise in that
y-down frame. Nothing in this module imports Qt.

A cubic Bézier curve is stored as its four control points. The length helper
integrates ``|B'(t)|`` with adaptive Simpson's rule to an absolute tolerance of
0.01 mm and caches that result. Drawing uses the control points themselves.

Format version 1 is the span objects already stored on a part. Line and arc
objects stay as they were. A Bézier span adds ``kind: "bezier"`` and the four
points. Several contour paths are allowed; the filled outline polygon stays a
separate hit shape and is not a hole subtraction.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass
from functools import lru_cache
from typing import Literal

from slot_racing.core.errors import ValidationError

GEOMETRY_VERSION = 1
# A joint closer than this is position-continuous. A larger gap is a break.
POSITION_GAP_MM = 0.05
# Tangents within this angle are treated as smooth. A larger turn is a kink.
TANGENT_KINK_DEG = 1.0
# Adaptive Simpson stops when one interval is within this absolute error.
BEZIER_LENGTH_TOLERANCE_MM = 0.01
_BEZIER_DEPTH = 16
_ROLE = Literal["slot", "center", "contour", "border"]


def rotate_xy(x_mm: float, y_mm: float, rotation_z_deg: float) -> tuple[float, float]:
    """Clockwise rotation in the plan's y-down coordinates."""
    radians = math.radians(rotation_z_deg)
    cosine = math.cos(radians)
    sine = math.sin(radians)
    return (x_mm * cosine - y_mm * sine, x_mm * sine + y_mm * cosine)


def _finite(value: object) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValidationError("error.planner.geometry")
    return float(value)


def _tangent_deg(dx: float, dy: float) -> float:
    if dx == 0.0 and dy == 0.0:
        return 0.0
    return math.degrees(math.atan2(dy, dx)) % 360.0


def _unit(value: float) -> float:
    fraction = _finite(value)
    if not 0.0 < fraction < 1.0:
        raise ValidationError("error.planner.geometry")
    return fraction


def _lerp(left: tuple[float, float], right: tuple[float, float], t: float) -> tuple[float, float]:
    return (left[0] + (right[0] - left[0]) * t, left[1] + (right[1] - left[1]) * t)


def _turn_deg(start_deg: float, end_deg: float) -> float:
    return abs((end_deg - start_deg + 180.0) % 360.0 - 180.0)


@dataclass(frozen=True, slots=True)
class Bounds:
    """Axis-aligned box in millimetres."""

    min_x: float
    min_y: float
    max_x: float
    max_y: float

    def union(self, other: Bounds) -> Bounds:
        return Bounds(
            min(self.min_x, other.min_x),
            min(self.min_y, other.min_y),
            max(self.max_x, other.max_x),
            max(self.max_y, other.max_y),
        )


@dataclass(frozen=True, slots=True)
class LineSegment:
    """Straight segment from ``(x0, y0)`` to ``(x1, y1)``."""

    x0: float
    y0: float
    x1: float
    y1: float

    def __post_init__(self) -> None:
        object.__setattr__(self, "x0", _finite(self.x0))
        object.__setattr__(self, "y0", _finite(self.y0))
        object.__setattr__(self, "x1", _finite(self.x1))
        object.__setattr__(self, "y1", _finite(self.y1))

    def start(self) -> tuple[float, float]:
        return (self.x0, self.y0)

    def end(self) -> tuple[float, float]:
        return (self.x1, self.y1)

    def length_mm(self) -> float:
        return math.hypot(self.x1 - self.x0, self.y1 - self.y0)

    def point_at(self, t: float) -> tuple[float, float]:
        fraction = _finite(t)
        return (
            self.x0 + (self.x1 - self.x0) * fraction,
            self.y0 + (self.y1 - self.y0) * fraction,
        )

    def tangent_deg(self, _t: float = 0.0) -> float:
        return _tangent_deg(self.x1 - self.x0, self.y1 - self.y0)

    def translated(self, dx: float, dy: float) -> LineSegment:
        shift_x = _finite(dx)
        shift_y = _finite(dy)
        return LineSegment(
            self.x0 + shift_x, self.y0 + shift_y, self.x1 + shift_x, self.y1 + shift_y
        )

    def rotated(
        self, rotation_z_deg: float, origin: tuple[float, float] = (0.0, 0.0)
    ) -> LineSegment:
        start = _rotated_pair(self.start(), rotation_z_deg, origin)
        end = _rotated_pair(self.end(), rotation_z_deg, origin)
        return LineSegment(start[0], start[1], end[0], end[1])

    def bounds(self) -> Bounds:
        return Bounds(
            min(self.x0, self.x1),
            min(self.y0, self.y1),
            max(self.x0, self.x1),
            max(self.y0, self.y1),
        )

    def split_at(self, t: float) -> tuple[LineSegment, LineSegment]:
        fraction = _unit(t)
        x, y = self.point_at(fraction)
        return (LineSegment(self.x0, self.y0, x, y), LineSegment(x, y, self.x1, self.y1))

    def closest(self, x_mm: float, y_mm: float) -> tuple[float, float]:
        """``(t, distance)`` of the nearest point on the segment."""
        dx = self.x1 - self.x0
        dy = self.y1 - self.y0
        square = dx * dx + dy * dy
        if square <= 0.0:
            return (0.0, math.hypot(x_mm - self.x0, y_mm - self.y0))
        t = min(max(((x_mm - self.x0) * dx + (y_mm - self.y0) * dy) / square, 0.0), 1.0)
        px, py = self.point_at(t)
        return (t, math.hypot(x_mm - px, y_mm - py))


@dataclass(frozen=True, slots=True)
class ArcSegment:
    """Circular arc. Positive ``sweep_deg`` runs toward +y."""

    cx: float
    cy: float
    radius_mm: float
    start_deg: float
    sweep_deg: float

    def __post_init__(self) -> None:
        object.__setattr__(self, "cx", _finite(self.cx))
        object.__setattr__(self, "cy", _finite(self.cy))
        radius = _finite(self.radius_mm)
        if radius <= 0.0:
            raise ValidationError("error.planner.geometry")
        object.__setattr__(self, "radius_mm", radius)
        object.__setattr__(self, "start_deg", _finite(self.start_deg))
        object.__setattr__(self, "sweep_deg", _finite(self.sweep_deg))

    def start(self) -> tuple[float, float]:
        return self.point_at(0.0)

    def end(self) -> tuple[float, float]:
        return self.point_at(1.0)

    def length_mm(self) -> float:
        return abs(self.radius_mm * math.radians(self.sweep_deg))

    def point_at(self, t: float) -> tuple[float, float]:
        angle = math.radians(self.start_deg + self.sweep_deg * _finite(t))
        return (
            self.cx + self.radius_mm * math.cos(angle),
            self.cy + self.radius_mm * math.sin(angle),
        )

    def tangent_deg(self, t: float = 0.0) -> float:
        theta = self.start_deg + self.sweep_deg * _finite(t)
        turn = 90.0 if self.sweep_deg >= 0.0 else -90.0
        return (theta + turn) % 360.0

    def translated(self, dx: float, dy: float) -> ArcSegment:
        return ArcSegment(
            self.cx + _finite(dx),
            self.cy + _finite(dy),
            self.radius_mm,
            self.start_deg,
            self.sweep_deg,
        )

    def rotated(
        self, rotation_z_deg: float, origin: tuple[float, float] = (0.0, 0.0)
    ) -> ArcSegment:
        center = _rotated_pair((self.cx, self.cy), rotation_z_deg, origin)
        return ArcSegment(
            center[0],
            center[1],
            self.radius_mm,
            self.start_deg + _finite(rotation_z_deg),
            self.sweep_deg,
        )

    def bounds(self) -> Bounds:
        points = [self.start(), self.end()]
        start = self.start_deg
        sweep = self.sweep_deg
        if sweep != 0.0:
            for cardinal in (0.0, 90.0, 180.0, 270.0):
                if _angle_on_sweep(start, sweep, cardinal):
                    points.append(self.point_at((cardinal - start) / sweep))
        xs = [point[0] for point in points]
        ys = [point[1] for point in points]
        return Bounds(min(xs), min(ys), max(xs), max(ys))

    def split_at(self, t: float) -> tuple[ArcSegment, ArcSegment]:
        fraction = _unit(t)
        first = self.sweep_deg * fraction
        return (
            ArcSegment(self.cx, self.cy, self.radius_mm, self.start_deg, first),
            ArcSegment(
                self.cx, self.cy, self.radius_mm, self.start_deg + first, self.sweep_deg - first
            ),
        )

    def closest(self, x_mm: float, y_mm: float) -> tuple[float, float]:
        """``(t, distance)`` of the nearest point on the arc, ends included."""
        dx = x_mm - self.cx
        dy = y_mm - self.cy
        if self.sweep_deg == 0.0:
            return (0.0, math.hypot(x_mm - self.start()[0], y_mm - self.start()[1]))
        angle = math.degrees(math.atan2(dy, dx)) if (dx or dy) else self.start_deg
        sweep = self.sweep_deg
        delta = (angle - self.start_deg) % 360.0
        if sweep < 0.0:
            delta = delta - 360.0 if delta > 0.0 else 0.0
        inside = 0.0 <= delta <= sweep if sweep > 0.0 else sweep <= delta <= 0.0
        if inside:
            t = delta / sweep
            return (t, abs(math.hypot(dx, dy) - self.radius_mm))
        ends = ((0.0, self.start()), (1.0, self.end()))
        return min(
            ((end_t, math.hypot(x_mm - p[0], y_mm - p[1])) for end_t, p in ends),
            key=lambda item: item[1],
        )


@dataclass(frozen=True, slots=True)
class CubicBezier:
    """Cubic Bézier ``B(t)`` for ``0 ≤ t ≤ 1``. The control points are the stored form."""

    p0x: float
    p0y: float
    p1x: float
    p1y: float
    p2x: float
    p2y: float
    p3x: float
    p3y: float

    def __post_init__(self) -> None:
        for name in ("p0x", "p0y", "p1x", "p1y", "p2x", "p2y", "p3x", "p3y"):
            object.__setattr__(self, name, _finite(getattr(self, name)))

    def control_points(
        self,
    ) -> tuple[
        tuple[float, float],
        tuple[float, float],
        tuple[float, float],
        tuple[float, float],
    ]:
        return (
            (self.p0x, self.p0y),
            (self.p1x, self.p1y),
            (self.p2x, self.p2y),
            (self.p3x, self.p3y),
        )

    def start(self) -> tuple[float, float]:
        return (self.p0x, self.p0y)

    def end(self) -> tuple[float, float]:
        return (self.p3x, self.p3y)

    def point_at(self, t: float) -> tuple[float, float]:
        fraction = _finite(t)
        return _bezier_point(self._coords(), fraction)

    def tangent_deg(self, t: float = 0.0) -> float:
        dx, dy = _bezier_derivative(self._coords(), _finite(t))
        return _tangent_deg(dx, dy)

    def length_mm(self) -> float:
        return _bezier_length(self._coords())

    def translated(self, dx: float, dy: float) -> CubicBezier:
        shift_x = _finite(dx)
        shift_y = _finite(dy)
        return CubicBezier(
            self.p0x + shift_x,
            self.p0y + shift_y,
            self.p1x + shift_x,
            self.p1y + shift_y,
            self.p2x + shift_x,
            self.p2y + shift_y,
            self.p3x + shift_x,
            self.p3y + shift_y,
        )

    def rotated(
        self, rotation_z_deg: float, origin: tuple[float, float] = (0.0, 0.0)
    ) -> CubicBezier:
        points = tuple(
            _rotated_pair(point, rotation_z_deg, origin) for point in self.control_points()
        )
        return CubicBezier(
            points[0][0],
            points[0][1],
            points[1][0],
            points[1][1],
            points[2][0],
            points[2][1],
            points[3][0],
            points[3][1],
        )

    def bounds(self) -> Bounds:
        xs = [self.p0x, self.p3x]
        ys = [self.p0y, self.p3y]
        for root in _bezier_critical_times(self.p0x, self.p1x, self.p2x, self.p3x):
            xs.append(_bezier_point(self._coords(), root)[0])
        for root in _bezier_critical_times(self.p0y, self.p1y, self.p2y, self.p3y):
            ys.append(_bezier_point(self._coords(), root)[1])
        return Bounds(min(xs), min(ys), max(xs), max(ys))

    def split_at(self, t: float) -> tuple[CubicBezier, CubicBezier]:
        """De Casteljau split. Both halves together draw the original curve."""
        fraction = _unit(t)
        p0, p1, p2, p3 = self.control_points()
        q0 = _lerp(p0, p1, fraction)
        q1 = _lerp(p1, p2, fraction)
        q2 = _lerp(p2, p3, fraction)
        r0 = _lerp(q0, q1, fraction)
        r1 = _lerp(q1, q2, fraction)
        mid = _lerp(r0, r1, fraction)
        return (
            CubicBezier(*p0, *q0, *r0, *mid),
            CubicBezier(*mid, *r1, *q2, *p3),
        )

    def closest(self, x_mm: float, y_mm: float) -> tuple[float, float]:
        """``(t, distance)`` of the nearest point, found by sampling and refining."""
        best_t = 0.0
        best = math.inf
        steps = 48
        for index in range(steps + 1):
            t = index / steps
            px, py = self.point_at(t)
            distance = math.hypot(x_mm - px, y_mm - py)
            if distance < best:
                best, best_t = distance, t
        low = max(best_t - 1.0 / steps, 0.0)
        high = min(best_t + 1.0 / steps, 1.0)
        for _ in range(24):
            left = low + (high - low) / 3.0
            right = high - (high - low) / 3.0
            lx, ly = self.point_at(left)
            rx, ry = self.point_at(right)
            if math.hypot(x_mm - lx, y_mm - ly) <= math.hypot(x_mm - rx, y_mm - ry):
                high = right
            else:
                low = left
        t = (low + high) / 2.0
        px, py = self.point_at(t)
        return (t, math.hypot(x_mm - px, y_mm - py))

    def _coords(self) -> tuple[float, float, float, float, float, float, float, float]:
        return (self.p0x, self.p0y, self.p1x, self.p1y, self.p2x, self.p2y, self.p3x, self.p3y)


Segment = LineSegment | ArcSegment | CubicBezier


@dataclass(frozen=True, slots=True)
class SegmentJoint:
    """Continuity between segment ``index`` and the next one."""

    index: int
    gap_mm: float
    tangent_delta_deg: float

    @property
    def position_continuous(self) -> bool:
        return self.gap_mm <= POSITION_GAP_MM

    @property
    def tangent_continuous(self) -> bool:
        return self.tangent_delta_deg <= TANGENT_KINK_DEG


@dataclass(frozen=True, slots=True)
class GeometryPath:
    """Ordered segments of one groove, contour or border edge."""

    segments: tuple[Segment, ...]
    role: _ROLE = "slot"
    lane: int | None = None
    slot: str | None = None

    def start(self) -> tuple[float, float]:
        self._require_segments()
        return self.segments[0].start()

    def end(self) -> tuple[float, float]:
        self._require_segments()
        return self.segments[-1].end()

    def length_mm(self) -> float:
        return sum(segment.length_mm() for segment in self.segments)

    def tangent_at_start_deg(self) -> float:
        self._require_segments()
        return self.segments[0].tangent_deg(0.0)

    def tangent_at_end_deg(self) -> float:
        self._require_segments()
        return self.segments[-1].tangent_deg(1.0)

    def bounds(self) -> Bounds:
        self._require_segments()
        box = self.segments[0].bounds()
        for segment in self.segments[1:]:
            box = box.union(segment.bounds())
        return box

    def joints(self) -> tuple[SegmentJoint, ...]:
        reports: list[SegmentJoint] = []
        for index, (left, right) in enumerate(zip(self.segments, self.segments[1:], strict=False)):
            end = left.end()
            start = right.start()
            reports.append(
                SegmentJoint(
                    index,
                    math.hypot(start[0] - end[0], start[1] - end[1]),
                    _turn_deg(left.tangent_deg(1.0), right.tangent_deg(0.0)),
                )
            )
        return tuple(reports)

    def translated(self, dx: float, dy: float) -> GeometryPath:
        return GeometryPath(
            tuple(segment.translated(dx, dy) for segment in self.segments),
            self.role,
            self.lane,
            self.slot,
        )

    def rotated(
        self, rotation_z_deg: float, origin: tuple[float, float] = (0.0, 0.0)
    ) -> GeometryPath:
        return GeometryPath(
            tuple(segment.rotated(rotation_z_deg, origin) for segment in self.segments),
            self.role,
            self.lane,
            self.slot,
        )

    def _require_segments(self) -> None:
        if not self.segments:
            raise ValidationError("error.planner.geometry")


def validate_segment(segment: Segment) -> None:
    """Reject a segment that has no extent. Unusual but finite shapes stay allowed."""
    if isinstance(segment, ArcSegment):
        if segment.sweep_deg == 0.0:
            raise ValidationError("error.planner.geometry")
        return
    if segment.length_mm() <= 1e-9:
        raise ValidationError("error.planner.geometry")


def validate_path(path: GeometryPath) -> None:
    """Require ordered segments that meet. A kink is allowed. A gap is not."""
    if path.role not in {"slot", "center", "contour", "border"}:
        raise ValidationError("error.planner.geometry")
    if not path.segments:
        raise ValidationError("error.planner.geometry")
    for segment in path.segments:
        validate_segment(segment)
    for joint in path.joints():
        if joint.gap_mm > POSITION_GAP_MM:
            raise ValidationError("error.planner.geometry")


def _rotated_pair(
    point: tuple[float, float], rotation_z_deg: float, origin: tuple[float, float]
) -> tuple[float, float]:
    shifted = (point[0] - origin[0], point[1] - origin[1])
    turned = rotate_xy(shifted[0], shifted[1], rotation_z_deg)
    return (turned[0] + origin[0], turned[1] + origin[1])


def _angle_on_sweep(start_deg: float, sweep_deg: float, cardinal: float) -> bool:
    if sweep_deg == 0.0:
        return False
    delta = (cardinal - start_deg) % 360.0
    if sweep_deg < 0.0:
        delta = (delta - 360.0) % 360.0 - 360.0
        return sweep_deg <= delta <= 0.0
    return 0.0 <= delta <= sweep_deg


def _bezier_point(
    coords: tuple[float, float, float, float, float, float, float, float], t: float
) -> tuple[float, float]:
    p0x, p0y, p1x, p1y, p2x, p2y, p3x, p3y = coords
    u = 1.0 - t
    weight0 = u * u * u
    weight1 = 3.0 * u * u * t
    weight2 = 3.0 * u * t * t
    weight3 = t * t * t
    return (
        weight0 * p0x + weight1 * p1x + weight2 * p2x + weight3 * p3x,
        weight0 * p0y + weight1 * p1y + weight2 * p2y + weight3 * p3y,
    )


def _bezier_derivative(
    coords: tuple[float, float, float, float, float, float, float, float], t: float
) -> tuple[float, float]:
    p0x, p0y, p1x, p1y, p2x, p2y, p3x, p3y = coords
    u = 1.0 - t
    # B'(t) = 3(1-t)^2 (P1-P0) + 6(1-t)t (P2-P1) + 3 t^2 (P3-P2)
    dx = 3.0 * u * u * (p1x - p0x) + 6.0 * u * t * (p2x - p1x) + 3.0 * t * t * (p3x - p2x)
    dy = 3.0 * u * u * (p1y - p0y) + 6.0 * u * t * (p2y - p1y) + 3.0 * t * t * (p3y - p2y)
    return (dx, dy)


def _bezier_critical_times(p0: float, p1: float, p2: float, p3: float) -> tuple[float, ...]:
    """Roots of one axis of ``B'(t)`` inside ``(0, 1)``."""
    a = p1 - p0
    b = p2 - p1
    c = p3 - p2
    quadratic = a - 2.0 * b + c
    linear = 2.0 * (b - a)
    constant = a
    roots: list[float] = []
    if abs(quadratic) < 1e-12:
        if abs(linear) >= 1e-12:
            root = -constant / linear
            if 0.0 < root < 1.0:
                roots.append(root)
        return tuple(roots)
    discriminant = linear * linear - 4.0 * quadratic * constant
    if discriminant < 0.0:
        return ()
    scale = math.sqrt(discriminant)
    for sign in (-1.0, 1.0):
        root = (-linear + sign * scale) / (2.0 * quadratic)
        if 0.0 < root < 1.0:
            roots.append(root)
    return tuple(roots)


@lru_cache(maxsize=1024)
def _bezier_length(
    coords: tuple[float, float, float, float, float, float, float, float],
) -> float:
    """Length of ``|B'(t)|`` within :data:`BEZIER_LENGTH_TOLERANCE_MM`.

    The cache key is the control points. A changed curve is a new key, so a
    repaint of an unchanged part does not integrate again.
    """
    return _simpson(coords, 0.0, 1.0, BEZIER_LENGTH_TOLERANCE_MM, _BEZIER_DEPTH)


def _speed(
    coords: tuple[float, float, float, float, float, float, float, float], t: float
) -> float:
    dx, dy = _bezier_derivative(coords, t)
    return math.hypot(dx, dy)


def _simpson(
    coords: tuple[float, float, float, float, float, float, float, float],
    left: float,
    right: float,
    tolerance: float,
    depth: int,
) -> float:
    mid = (left + right) / 2.0
    whole = (
        (right - left)
        / 6.0
        * (_speed(coords, left) + 4.0 * _speed(coords, mid) + _speed(coords, right))
    )
    left_mid = (left + mid) / 2.0
    right_mid = (mid + right) / 2.0
    left_half = (
        (mid - left)
        / 6.0
        * (_speed(coords, left) + 4.0 * _speed(coords, left_mid) + _speed(coords, mid))
    )
    right_half = (
        (right - mid)
        / 6.0
        * (_speed(coords, mid) + 4.0 * _speed(coords, right_mid) + _speed(coords, right))
    )
    if depth <= 0 or abs(left_half + right_half - whole) <= tolerance:
        return left_half + right_half
    return _simpson(coords, left, mid, tolerance / 2.0, depth - 1) + _simpson(
        coords, mid, right, tolerance / 2.0, depth - 1
    )


def split_segment(segment: Segment, t: float) -> tuple[Segment, Segment]:
    """Split at ``0 < t < 1``. Both parts follow the original curve."""
    return segment.split_at(t)


def closest_on_segment(segment: Segment, x_mm: float, y_mm: float) -> tuple[float, float]:
    """``(t, distance)`` from a point to a segment, used for hit tests."""
    return segment.closest(x_mm, y_mm)


def path_length_mm(segments: Sequence[Segment]) -> float:
    return sum(segment.length_mm() for segment in segments)
