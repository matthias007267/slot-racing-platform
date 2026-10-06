"""Lane lengths of a connected plan, from the slot geometry.

The painter is not involved. A straight groove is the distance between its
ends, and an arc is radius times its sweep in radians. Those pieces are summed
along one groove as it crosses from part to part.

A part is measured only when every driving groove runs from one connector lane
to another and each lane is a single continuation. Spur changes, switches and
crossings fail that test: several routes leave the same lane, and no route is
invented for them. A run that contains such a part reports no length.

A closed run is walked once. A loose part is its own run and is not added to
another.
"""

from __future__ import annotations

import math
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from slot_racing.modules.track_planner.figure import track_figure
from slot_racing.modules.track_planner.parts import (
    SPAN_ARC,
    ConnectorSpec,
    PartInstance,
    PartSpec,
    SlotSpan,
    connectors_compatible,
    curve_lane_radius,
    lane_offset_mm,
    normalize_deg,
    span_point,
    world_xy,
)

# The side panel stays quiet until a run is at least this long.
MIN_LENGTH_PARTS = 4
_BIND_MM = 1.0
_JOIN_MM = 1.0

Port = tuple[str, int, int]
_LocalPort = tuple[int, int]


@dataclass(frozen=True, slots=True)
class LaneLength:
    """One groove. ``length_mm`` is the full sum, not a rounded display value."""

    lane: int
    length_mm: float


@dataclass(frozen=True, slots=True)
class TrackRun:
    """One connected group of parts. ``lanes`` is empty when the route is not unique."""

    part_count: int
    lanes: tuple[LaneLength, ...]


def span_length_mm(span: SlotSpan) -> float:
    """Length of one line or circular arc. The sweep sign does not matter."""
    if span.kind == SPAN_ARC:
        return abs(span.radius_mm * math.radians(span.sweep_deg))
    return math.hypot(span.x1 - span.x0, span.y1 - span.y0)


def path_length_mm(spans: Sequence[SlotSpan]) -> float:
    return sum(span_length_mm(span) for span in spans)


def format_length_m(length_mm: float) -> str:
    """Metres with two decimals and a decimal comma. Rounding happens only here."""
    text = f"{length_mm / 1000.0:.2f}".replace(".", ",")
    return f"{text} m"


def connected_runs(
    instances: Sequence[PartInstance],
    catalog: Mapping[int, PartSpec],
) -> tuple[TrackRun, ...]:
    """Every connected group, in plan order. Short groups are included."""
    by_id = {instance.id: instance for instance in instances}
    neighbors: dict[str, set[str]] = {instance.id: set() for instance in instances}
    ordered = list(instances)
    for index, left in enumerate(ordered):
        left_spec = catalog.get(left.part_id)
        if left_spec is None:
            continue
        for right in ordered[index + 1 :]:
            right_spec = catalog.get(right.part_id)
            if right_spec is None:
                continue
            if _parts_join(left, left_spec, right, right_spec):
                neighbors[left.id].add(right.id)
                neighbors[right.id].add(left.id)
    seen: set[str] = set()
    runs: list[TrackRun] = []
    for instance in ordered:
        if instance.id in seen:
            continue
        stack = [instance.id]
        seen.add(instance.id)
        members: list[PartInstance] = []
        while stack:
            current = stack.pop()
            members.append(by_id[current])
            for other in neighbors[current]:
                if other not in seen:
                    seen.add(other)
                    stack.append(other)
        members.sort(key=lambda item: ordered.index(item))
        runs.append(TrackRun(len(members), _run_lengths(members, catalog)))
    return tuple(runs)


def display_lane_lengths(
    instances: Sequence[PartInstance],
    catalog: Mapping[int, PartSpec],
) -> tuple[LaneLength, ...]:
    """Lanes of every run long enough to show. Shorter runs are omitted."""
    rows: list[LaneLength] = []
    for run in connected_runs(instances, catalog):
        if run.part_count >= MIN_LENGTH_PARTS:
            rows.extend(run.lanes)
    return tuple(rows)


def _run_lengths(
    members: Sequence[PartInstance], catalog: Mapping[int, PartSpec]
) -> tuple[LaneLength, ...]:
    routed: dict[str, tuple[tuple[_LocalPort, _LocalPort, float], ...]] = {}
    for instance in members:
        spec = catalog.get(instance.part_id)
        if spec is None:
            return ()
        edges = _part_edges(spec)
        if edges is None:
            return ()
        routed[instance.id] = edges
    adjacency: dict[Port, list[tuple[Port, float]]] = {}

    def link(left: Port, right: Port, length: float) -> None:
        adjacency.setdefault(left, []).append((right, length))
        adjacency.setdefault(right, []).append((left, length))

    for instance in members:
        for start, end, length in routed[instance.id]:
            link(
                (instance.id, start[0], start[1]),
                (instance.id, end[0], end[1]),
                length,
            )
    for index, left in enumerate(members):
        left_spec = catalog[left.part_id]
        for right in members[index + 1 :]:
            right_spec = catalog[right.part_id]
            for left_index, left_joint in enumerate(left_spec.connectors):
                for right_index, right_joint in enumerate(right_spec.connectors):
                    if not _joints_meet(left, left_joint, right, right_joint):
                        continue
                    for lane in left_joint.lanes:
                        link(
                            (left.id, left_index, lane),
                            (right.id, right_index, lane),
                            0.0,
                        )
    if any(len(links) > 2 for links in adjacency.values()):
        return ()
    try:
        return _walk(adjacency)
    except _WalkError:
        return ()


def _part_edges(
    spec: PartSpec,
) -> tuple[tuple[_LocalPort, _LocalPort, float], ...] | None:
    """Connector-to-connector grooves, or ``None`` when a groove branches or stops early."""
    edges: list[tuple[_LocalPort, _LocalPort, float]] = []
    used: list[_LocalPort] = []
    for path in track_figure(spec).slots:
        if not path.spans:
            return None
        start = _bind(spec, span_point(path.spans[0], 0.0))
        end = _bind(spec, span_point(path.spans[-1], 1.0))
        if start is None or end is None or start == end:
            return None
        if start in used or end in used:
            return None
        used.extend((start, end))
        edges.append((start, end, path_length_mm(path.spans)))
    counts = Counter(lane for _connector, lane in used)
    if any(count > 2 for count in counts.values()):
        return None
    return tuple(edges)


def _bind(spec: PartSpec, point: tuple[float, float]) -> _LocalPort | None:
    best: tuple[float, _LocalPort] | None = None
    for index, connector in enumerate(spec.connectors):
        for lane in connector.lanes:
            anchor = _lane_anchor(spec, connector, lane)
            distance = math.hypot(point[0] - anchor[0], point[1] - anchor[1])
            if distance <= _BIND_MM and (best is None or distance < best[0]):
                best = (distance, (index, lane))
    if best is None:
        return None
    return best[1]


def _lane_anchor(spec: PartSpec, connector: ConnectorSpec, lane: int) -> tuple[float, float]:
    """Where ``lane`` meets ``connector`` in the part's own coordinates."""
    index = connector.lanes.index(lane)
    count = len(connector.lanes)
    offset = lane_offset_mm(index, count)
    folded = abs(connector.direction_deg) % 180.0
    if folded > 90.0:
        folded = 180.0 - folded
    if folded <= 20.0:
        return (connector.x_mm, connector.y_mm + offset)
    if folded >= 70.0:
        return (connector.x_mm + offset, connector.y_mm)
    radius = spec.radius_mm
    if radius is not None and radius > 0.0:
        groove = curve_lane_radius(radius, index, spec.lane_count)
        centre = math.hypot(connector.x_mm, connector.y_mm)
        if centre > 1.0:
            scale = groove / centre
            return (connector.x_mm * scale, connector.y_mm * scale)
    return (connector.x_mm, connector.y_mm + offset)


def _parts_join(
    left: PartInstance, left_spec: PartSpec, right: PartInstance, right_spec: PartSpec
) -> bool:
    for left_joint in left_spec.connectors:
        for right_joint in right_spec.connectors:
            if _joints_meet(left, left_joint, right, right_joint):
                return True
    return False


def _joints_meet(
    left: PartInstance,
    left_joint: ConnectorSpec,
    right: PartInstance,
    right_joint: ConnectorSpec,
) -> bool:
    if left.id == right.id or not connectors_compatible(left_joint, right_joint):
        return False
    origin = world_xy(left, left_joint.x_mm, left_joint.y_mm)
    other = world_xy(right, right_joint.x_mm, right_joint.y_mm)
    if math.hypot(origin[0] - other[0], origin[1] - other[1]) > _JOIN_MM:
        return False
    heading = normalize_deg(left_joint.direction_deg + left.rotation_z_deg)
    other_heading = normalize_deg(right_joint.direction_deg + right.rotation_z_deg)
    turn = abs(((heading - other_heading) % 360.0) - 180.0)
    return turn <= 20.0


class _WalkError(Exception):
    """The groove graph is not a single path or loop."""


def _walk(adjacency: dict[Port, list[tuple[Port, float]]]) -> tuple[LaneLength, ...]:
    seen: set[frozenset[Port]] = set()
    courses: list[LaneLength] = []

    def consume(start: Port) -> None:
        current = start
        previous: Port | None = None
        length = 0.0
        lanes = {start[2]}
        for _step in range(len(adjacency) + 1):
            chosen: tuple[Port, float] | None = None
            for neighbor, edge_length in adjacency.get(current, []):
                if neighbor == previous:
                    continue
                identity = frozenset((current, neighbor))
                if identity in seen:
                    continue
                chosen = (neighbor, edge_length)
                seen.add(identity)
                break
            if chosen is None:
                break
            neighbor, edge_length = chosen
            length += edge_length
            lanes.add(neighbor[2])
            previous, current = current, neighbor
            if current == start:
                break
        else:
            raise _WalkError
        if length > 0.0:
            lane = start[2] if len(lanes) == 1 else min(lanes)
            courses.append(LaneLength(lane, length))

    for port, links in adjacency.items():
        if len(links) != 1:
            continue
        identity = frozenset((port, links[0][0]))
        if identity not in seen:
            consume(port)
    for port, links in adjacency.items():
        for neighbor, _length in links:
            if frozenset((port, neighbor)) not in seen:
                consume(port)
                break
    courses.sort(key=lambda item: (item.lane, item.length_mm))
    return tuple(courses)
