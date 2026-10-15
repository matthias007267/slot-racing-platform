"""SVG path commands used by the Carrera part files.

Only ``M``, ``L``, ``A``, ``C`` and ``Z`` occur in those files. Coordinates stay
in millimetres. An arc becomes the centre form the planner already stores.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass

from slot_racing.modules.track_planner.parts import SPAN_ARC, SPAN_BEZIER, SPAN_LINE, SlotSpan

_TOKEN = re.compile(r"[MLACZmlacz]|[-+]?(?:\d+\.\d+|\d+|\.\d+)(?:[eE][-+]?\d+)?")


@dataclass(frozen=True, slots=True)
class ParsedPath:
    """One continuous subpath. A later ``M`` starts another."""

    spans: tuple[SlotSpan, ...]
    closed: bool


def parse_svg_path(text: str) -> tuple[ParsedPath, ...]:
    """Read one ``d`` attribute. Empty text has no subpaths."""
    tokens = _TOKEN.findall(text.replace(",", " "))
    if not tokens:
        return ()
    index = 0
    command = ""
    current = (0.0, 0.0)
    start = (0.0, 0.0)
    spans: list[SlotSpan] = []
    closed = False
    subpaths: list[ParsedPath] = []

    def take(count: int) -> tuple[float, ...]:
        nonlocal index
        values: list[float] = []
        for _ in range(count):
            if index >= len(tokens) or _command(tokens[index]):
                raise ValueError(text)
            values.append(float(tokens[index]))
            index += 1
        return tuple(values)

    def finish() -> None:
        nonlocal spans, closed
        if spans:
            subpaths.append(ParsedPath(tuple(spans), closed))
        spans = []
        closed = False

    while index < len(tokens):
        token = tokens[index]
        if _command(token):
            command = token
            index += 1
        elif not command:
            raise ValueError(text)
        absolute = command.isupper()
        kind = command.upper()
        if kind == "M":
            finish()
            x, y = take(2)
            current = _absolute((x, y), current, absolute=absolute)
            start = current
            command = "L" if absolute else "l"
        elif kind == "L":
            x, y = take(2)
            point = _absolute((x, y), current, absolute=absolute)
            if point != current:
                spans.append(SlotSpan.line(current[0], current[1], point[0], point[1]))
            current = point
        elif kind == "C":
            x1, y1, x2, y2, x, y = take(6)
            control_1 = _absolute((x1, y1), current, absolute=absolute)
            control_2 = _absolute((x2, y2), current, absolute=absolute)
            point = _absolute((x, y), current, absolute=absolute)
            spans.append(
                SlotSpan.bezier(
                    current[0],
                    current[1],
                    control_1[0],
                    control_1[1],
                    control_2[0],
                    control_2[1],
                    point[0],
                    point[1],
                )
            )
            current = point
        elif kind == "A":
            rx, ry, rotation, large, sweep, x, y = take(7)
            point = _absolute((x, y), current, absolute=absolute)
            spans.append(
                _arc_span(
                    current,
                    point,
                    rx,
                    ry,
                    rotation,
                    large >= 0.5,
                    sweep >= 0.5,
                )
            )
            current = point
        elif kind == "Z":
            if current != start and spans:
                spans.append(SlotSpan.line(current[0], current[1], start[0], start[1]))
            current = start
            closed = True
            finish()
        else:
            raise ValueError(text)
    finish()
    return tuple(subpaths)


def outline_points(
    subpaths: tuple[ParsedPath, ...], *, step_deg: float = 6.0
) -> tuple[tuple[float, float], ...]:
    """Polygon for the filled roadway. Arcs are sampled from the stored centre."""
    points: list[tuple[float, float]] = []
    for subpath in subpaths:
        for span in subpath.spans:
            if span.kind == SPAN_LINE:
                _append(points, (span.x0, span.y0))
                _append(points, (span.x1, span.y1))
            elif span.kind == SPAN_BEZIER:
                for step in range(9):
                    _append(points, _bezier_point(span, step / 8.0))
            elif span.kind == SPAN_ARC:
                pieces = max(1, math.ceil(abs(span.sweep_deg) / step_deg))
                for step in range(pieces + 1):
                    _append(points, _arc_point(span, step / pieces))
    if len(points) >= 2 and points[0] == points[-1]:
        points.pop()
    return tuple(points)


def _command(token: str) -> bool:
    return len(token) == 1 and token.isalpha()


def _absolute(
    point: tuple[float, float], current: tuple[float, float], *, absolute: bool
) -> tuple[float, float]:
    if absolute:
        return point
    return (current[0] + point[0], current[1] + point[1])


def _append(points: list[tuple[float, float]], point: tuple[float, float]) -> None:
    if points and _close(points[-1], point):
        return
    points.append(point)


def _close(left: tuple[float, float], right: tuple[float, float]) -> bool:
    return math.hypot(left[0] - right[0], left[1] - right[1]) <= 1e-6


def _arc_span(
    start: tuple[float, float],
    end: tuple[float, float],
    rx: float,
    ry: float,
    rotation_deg: float,
    large: bool,
    sweep: bool,
) -> SlotSpan:
    """Endpoint arc as a centre, radius and sweep. The file uses circular arcs."""
    radius = (abs(rx) + abs(ry)) / 2.0
    if radius <= 1e-9 or _close(start, end):
        return SlotSpan.line(start[0], start[1], end[0], end[1])
    center, start_deg, sweep_deg = _arc_center(start, end, radius, rotation_deg, large, sweep)
    return SlotSpan.arc(center[0], center[1], radius, start_deg, sweep_deg)


def _arc_center(
    start: tuple[float, float],
    end: tuple[float, float],
    radius: float,
    rotation_deg: float,
    large: bool,
    sweep: bool,
) -> tuple[tuple[float, float], float, float]:
    """W3C endpoint-to-centre conversion for one circular arc."""
    phi = math.radians(rotation_deg)
    cosine = math.cos(phi)
    sine = math.sin(phi)
    dx = (start[0] - end[0]) / 2.0
    dy = (start[1] - end[1]) / 2.0
    x1 = cosine * dx + sine * dy
    y1 = -sine * dx + cosine * dy
    radius_sq = radius * radius
    lam = (x1 * x1 + y1 * y1) / radius_sq
    if lam > 1.0:
        radius = math.sqrt(lam) * radius
        radius_sq = radius * radius
    sign = -1.0 if large == sweep else 1.0
    root = max(0.0, (radius_sq - x1 * x1 - y1 * y1) / (x1 * x1 + y1 * y1))
    scale = sign * math.sqrt(root)
    cx = scale * y1
    cy = scale * -x1
    mid_x = (start[0] + end[0]) / 2.0
    mid_y = (start[1] + end[1]) / 2.0
    center = (cosine * cx - sine * cy + mid_x, sine * cx + cosine * cy + mid_y)
    start_deg = math.degrees(math.atan2(y1 - cy, x1 - cx))
    end_deg = math.degrees(math.atan2(-y1 - cy, -x1 - cx))
    sweep_deg = (end_deg - start_deg) % 360.0
    if not sweep:
        sweep_deg -= 360.0
    if abs(sweep_deg) < 1e-9:
        sweep_deg = 360.0 if sweep else -360.0
    return center, start_deg % 360.0, sweep_deg


def _arc_point(span: SlotSpan, t: float) -> tuple[float, float]:
    angle = math.radians(span.start_deg + span.sweep_deg * t)
    return (
        span.x0 + span.radius_mm * math.cos(angle),
        span.y0 + span.radius_mm * math.sin(angle),
    )


def _bezier_point(span: SlotSpan, t: float) -> tuple[float, float]:
    u = 1.0 - t
    return (
        u**3 * span.x0 + 3 * u**2 * t * span.c1x + 3 * u * t**2 * span.c2x + t**3 * span.x1,
        u**3 * span.y0 + 3 * u**2 * t * span.c1y + 3 * u * t**2 * span.c2y + t**3 * span.y1,
    )
