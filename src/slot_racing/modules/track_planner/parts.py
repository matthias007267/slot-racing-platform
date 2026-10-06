"""Track pieces: one definition, many placed instances.

A definition is one part: designation, article number, scale, category, measures, connectors
and outline. Its identity is the designation plus the article number. Scale and category are
properties. An instance is that part used once on a plan, with its own position and rotation.
Editing an instance never changes the definition.

Coordinates are millimetres. Positive y points down, matching the plan canvas. ``rotation_z_deg``
is clockwise. ``z`` and the other two rotations are stored so a later 3D view can use them; the
2D planner only applies x, y and rotation z.

Camera zones, sensors and timing points are not part of a piece.
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from itertools import pairwise

from slot_racing.core.domain.lanes import MAX_LANE_COUNT
from slot_racing.core.errors import ValidationError

SCALES = ("1:24", "1:32", "1:43")
# The seeded Carrera rails are stored at 1:24. The scale is a property, not an identity.
CATALOG_SCALE = "1:24"

STRAIGHT = "straight"
CURVE = "curve"
LANE_CHANGE = "lane_change"
CROSSING = "crossing"
SWITCH = "switch"
PITLANE = "pitlane"
SPECIAL = "special"
BORDER = "border"
SUPPORT = "support"
CATEGORIES = (
    STRAIGHT,
    CURVE,
    LANE_CHANGE,
    CROSSING,
    SWITCH,
    PITLANE,
    SPECIAL,
    BORDER,
    SUPPORT,
)

TRACK = "track"
BORDER_JOINT = "border"
SUPPORT_JOINT = "support"
CONNECTOR_KINDS = frozenset({TRACK, BORDER_JOINT, SUPPORT_JOINT})

# Carrera Digital 132 / Evolution: 200 mm for two lanes, 100 mm between lane centres.
LANE_PITCH_MM = 100.0
DEFAULT_GRID_MM = 10.0
DEFAULT_SNAP_MM = 25.0
_POSITION_LIMIT_MM = 100_000.0

# Continue-build uses catalogue articles, never the displayed designation.
STANDARD_STRAIGHT_ARTICLE = "20020601"
STANDARD_CURVE_ARTICLE = "20020571"
EXTEND_STRAIGHT = "straight"
EXTEND_LEFT = "left"
EXTEND_RIGHT = "right"
EXTEND_DIRECTIONS = (EXTEND_LEFT, EXTEND_STRAIGHT, EXTEND_RIGHT)
# Fan around the connector's outward heading. 45° puts a connector that faces
# up on the screen at ↖ ↑ ↗. Positive is clockwise, which is a right turn.
ARROW_FAN_DEG = 45.0
_EXTEND_TURN_DEG = 1.0


@dataclass(frozen=True, slots=True)
class ConnectorSpec:
    """One joint in the part's own coordinates."""

    name: str
    x_mm: float
    y_mm: float
    z_mm: float
    direction_deg: float
    kind: str
    lanes: tuple[int, ...]


# A slot path is geometry, not a paint special case. ``line`` uses the two endpoints.
# ``arc`` uses the centre ``(x0, y0)``, ``radius_mm`` and the angles. 0° is +x and
# positive angles run toward +y, the same convention as the curve connectors.
SPAN_LINE = "line"
SPAN_ARC = "arc"
PATH_SLOT = "slot"
PATH_CENTER = "center"


@dataclass(frozen=True, slots=True)
class SlotSpan:
    """One straight or circular piece of a groove, in local millimetres."""

    kind: str
    x0: float
    y0: float
    x1: float
    y1: float
    radius_mm: float = 0.0
    start_deg: float = 0.0
    sweep_deg: float = 0.0

    @staticmethod
    def line(x0: float, y0: float, x1: float, y1: float) -> SlotSpan:
        return SlotSpan(SPAN_LINE, x0, y0, x1, y1)

    @staticmethod
    def arc(cx: float, cy: float, radius_mm: float, start_deg: float, sweep_deg: float) -> SlotSpan:
        return SlotSpan(SPAN_ARC, cx, cy, 0.0, 0.0, radius_mm, start_deg, sweep_deg)


@dataclass(frozen=True, slots=True)
class SlotPath:
    """One continuous groove. Several spans join end to end.

    ``kind`` is ``slot`` for a driving groove and ``center`` for the dashed line
    between two lanes. Complex parts store these on the definition. A part with
    no paths gets the ordinary lanes derived from its measures.
    """

    spans: tuple[SlotSpan, ...]
    kind: str = PATH_SLOT


@dataclass(frozen=True, slots=True)
class PartSpec:
    """One part. ``scale`` is one of :data:`SCALES` and is not part of the identity."""

    article_number: str
    scale: str
    name: str
    category: str
    length_mm: float | None
    width_mm: float | None
    height_mm: float | None
    radius_mm: float | None
    angle_deg: float | None
    lane_count: int
    connectors: tuple[ConnectorSpec, ...]
    outline: tuple[tuple[float, float], ...]
    # Empty means "derive the ordinary lanes". Diverging parts store their grooves here.
    slot_paths: tuple[SlotPath, ...] = ()


@dataclass(frozen=True, slots=True)
class PartRecord:
    """A :class:`PartSpec` stored in the library."""

    id: int
    spec: PartSpec


@dataclass(frozen=True, slots=True)
class PartInstance:
    """One use of a library part on a plan. The definition is referenced, not copied."""

    id: str
    part_id: int
    x_mm: float
    y_mm: float
    z_mm: float = 0.0
    rotation_x_deg: float = 0.0
    rotation_y_deg: float = 0.0
    rotation_z_deg: float = 0.0
    start_straight: bool = False
    group_id: str | None = None


@dataclass(frozen=True, slots=True)
class Pose:
    x_mm: float
    y_mm: float
    rotation_z_deg: float


def require_scale(scale: str | None) -> str:
    """One of :data:`SCALES`. Missing and unknown values are rejected."""
    if not isinstance(scale, str):
        raise ValidationError("error.planner.scale")
    text = scale.strip()
    if text not in SCALES:
        raise ValidationError("error.planner.scale")
    return text


def normalize_name(name: str) -> str:
    """Identity form of a designation: trimmed, case folded. The stored text keeps its case."""
    if not isinstance(name, str):
        raise ValidationError("error.planner.part")
    return name.strip().casefold()


def normalize_article(article_number: str) -> str:
    """Identity form of an article number: trimmed. Leading zeros stay."""
    if not isinstance(article_number, str):
        raise ValidationError("error.planner.part")
    return article_number.strip()


def identity_key(name: str, article_number: str) -> tuple[str, str]:
    """Designation plus article number. Scale, category and manufacturer are not included."""
    title = normalize_name(name)
    article = normalize_article(article_number)
    if not title or not article:
        raise ValidationError("error.planner.part")
    return (title, article)


def normalize_deg(angle: float) -> float:
    if isinstance(angle, bool) or not isinstance(angle, (int, float)) or not math.isfinite(angle):
        raise ValidationError("error.planner.part")
    return float(angle) % 360.0


def rotate_xy(x_mm: float, y_mm: float, rotation_z_deg: float) -> tuple[float, float]:
    """Clockwise rotation in the plan's y-down coordinates."""
    radians = math.radians(rotation_z_deg)
    cosine = math.cos(radians)
    sine = math.sin(radians)
    return (x_mm * cosine - y_mm * sine, x_mm * sine + y_mm * cosine)


def world_xy(instance: PartInstance, x_mm: float, y_mm: float) -> tuple[float, float]:
    rotated_x, rotated_y = rotate_xy(x_mm, y_mm, instance.rotation_z_deg)
    return (instance.x_mm + rotated_x, instance.y_mm + rotated_y)


def lanes_for(lane_count: int) -> tuple[int, ...]:
    if isinstance(lane_count, bool) or not isinstance(lane_count, int):
        raise ValidationError("error.planner.lane")
    if not 1 <= lane_count <= MAX_LANE_COUNT:
        raise ValidationError("error.planner.lane")
    return tuple(range(1, lane_count + 1))


def track_width(lane_count: int) -> float:
    return len(lanes_for(lane_count)) * LANE_PITCH_MM


def rectangle(length_mm: float, width_mm: float) -> tuple[tuple[float, float], ...]:
    half_length = length_mm / 2
    half_width = width_mm / 2
    return (
        (-half_length, -half_width),
        (half_length, -half_width),
        (half_length, half_width),
        (-half_length, half_width),
    )


def arc_outline(
    radius_mm: float, angle_deg: float, width_mm: float, steps: int = 8
) -> tuple[tuple[float, float], ...]:
    """A top-view ring segment. The circle centre is the local origin."""
    half = math.radians(angle_deg / 2)
    outer = radius_mm + width_mm / 2
    inner = max(radius_mm - width_mm / 2, 0.0)
    points: list[tuple[float, float]] = []
    for index in range(steps + 1):
        theta = -half + (2 * half) * index / steps
        points.append((outer * math.cos(theta), outer * math.sin(theta)))
    for index in range(steps + 1):
        theta = half - (2 * half) * index / steps
        points.append((inner * math.cos(theta), inner * math.sin(theta)))
    return tuple(points)


def straight_connectors(
    length_mm: float, lane_count: int, *, kind: str = TRACK
) -> tuple[ConnectorSpec, ...]:
    lanes = lanes_for(lane_count)
    half = length_mm / 2
    return (
        ConnectorSpec("a", -half, 0.0, 0.0, 180.0, kind, lanes),
        ConnectorSpec("b", half, 0.0, 0.0, 0.0, kind, lanes),
    )


def curve_connectors(
    radius_mm: float, angle_deg: float, lane_count: int
) -> tuple[ConnectorSpec, ...]:
    lanes = lanes_for(lane_count)
    half = math.radians(angle_deg / 2)
    start = (radius_mm * math.cos(-half), radius_mm * math.sin(-half))
    end = (radius_mm * math.cos(half), radius_mm * math.sin(half))
    start_dir = normalize_deg(math.degrees(-half) - 90.0)
    end_dir = normalize_deg(math.degrees(half) + 90.0)
    return (
        ConnectorSpec("a", start[0], start[1], 0.0, start_dir, TRACK, lanes),
        ConnectorSpec("b", end[0], end[1], 0.0, end_dir, TRACK, lanes),
    )


def crossing_connectors(length_mm: float, lane_count: int) -> tuple[ConnectorSpec, ...]:
    lanes = lanes_for(lane_count)
    half = length_mm / 2
    return (
        ConnectorSpec("west", -half, 0.0, 0.0, 180.0, TRACK, lanes),
        ConnectorSpec("east", half, 0.0, 0.0, 0.0, TRACK, lanes),
        ConnectorSpec("north", 0.0, -half, 0.0, 270.0, TRACK, lanes),
        ConnectorSpec("south", 0.0, half, 0.0, 90.0, TRACK, lanes),
    )


def connectors_compatible(left: ConnectorSpec, right: ConnectorSpec) -> bool:
    """Same joint kind and the same lane order. A reversed lane list does not fit."""
    return left.kind == right.kind and left.lanes == right.lanes and left.kind in CONNECTOR_KINDS


def join_pose(
    target: PartInstance, target_joint: ConnectorSpec, source_joint: ConnectorSpec
) -> Pose:
    """Pose that seats ``source_joint`` on ``target_joint``. The math is the snap join."""
    target_point = world_xy(target, target_joint.x_mm, target_joint.y_mm)
    target_dir = normalize_deg(target_joint.direction_deg + target.rotation_z_deg)
    rotation = normalize_deg(target_dir + 180.0 - source_joint.direction_deg)
    rotated = rotate_xy(source_joint.x_mm, source_joint.y_mm, rotation)
    return Pose(target_point[0] - rotated[0], target_point[1] - rotated[1], rotation)


def connector_occupied(
    instance: PartInstance,
    connector: ConnectorSpec,
    placed: Sequence[tuple[PartInstance, PartSpec]],
    *,
    tolerance_mm: float = 1.0,
) -> bool:
    """True when another compatible joint already meets this one."""
    point = world_xy(instance, connector.x_mm, connector.y_mm)
    direction = normalize_deg(connector.direction_deg + instance.rotation_z_deg)
    for other, spec in placed:
        if other.id == instance.id:
            continue
        for candidate in spec.connectors:
            if not connectors_compatible(connector, candidate):
                continue
            other_point = world_xy(other, candidate.x_mm, candidate.y_mm)
            distance = math.hypot(point[0] - other_point[0], point[1] - other_point[1])
            if distance > tolerance_mm:
                continue
            other_dir = normalize_deg(candidate.direction_deg + other.rotation_z_deg)
            turn = abs(((direction - other_dir) % 360.0) - 180.0)
            if turn <= 20.0:
                return True
    return False


def signed_delta_deg(start_deg: float, end_deg: float) -> float:
    """Clockwise degrees from ``start_deg`` to ``end_deg``, in the range -180..180."""
    return (normalize_deg(end_deg) - normalize_deg(start_deg) + 180.0) % 360.0 - 180.0


def extend_article(direction: str) -> str:
    """Catalogue article a continue-build direction inserts. Left and right share one curve."""
    if direction == EXTEND_STRAIGHT:
        return STANDARD_STRAIGHT_ARTICLE
    if direction in {EXTEND_LEFT, EXTEND_RIGHT}:
        return STANDARD_CURVE_ARTICLE
    raise ValidationError("error.planner.part")


def find_catalog_part(
    catalog: Mapping[int, PartSpec], article_number: str
) -> tuple[int, PartSpec] | None:
    """The lowest id whose article number matches. The designation is not consulted."""
    wanted = normalize_article(article_number)
    matches = [
        (part_id, spec)
        for part_id, spec in catalog.items()
        if normalize_article(spec.article_number) == wanted
    ]
    if not matches:
        return None
    matches.sort(key=lambda item: item[0])
    return matches[0]


def standard_extend_parts(catalog: Mapping[int, PartSpec]) -> dict[str, tuple[int, PartSpec]]:
    """Straight continues with the standard straight; both turns use the standard curve."""
    chosen: dict[str, tuple[int, PartSpec]] = {}
    straight = find_catalog_part(catalog, STANDARD_STRAIGHT_ARTICLE)
    curve = find_catalog_part(catalog, STANDARD_CURVE_ARTICLE)
    if straight is not None:
        chosen[EXTEND_STRAIGHT] = straight
    if curve is not None:
        chosen[EXTEND_LEFT] = curve
        chosen[EXTEND_RIGHT] = curve
    return chosen


def arrow_heading_deg(outward_deg: float, direction: str) -> float:
    """Screen heading of one continue arrow. It is the connector heading plus the local fan."""
    if direction == EXTEND_STRAIGHT:
        return normalize_deg(outward_deg)
    if direction == EXTEND_LEFT:
        return normalize_deg(outward_deg - ARROW_FAN_DEG)
    if direction == EXTEND_RIGHT:
        return normalize_deg(outward_deg + ARROW_FAN_DEG)
    raise ValidationError("error.planner.part")


def continuation_delta_deg(
    target: PartInstance,
    target_joint: ConnectorSpec,
    spec: PartSpec,
    source_joint: ConnectorSpec,
) -> float | None:
    """Signed turn at ``target_joint`` when ``spec`` is seated on ``source_joint``.

    Zero continues straight. Negative is a left turn and positive is a right turn, both
    relative to the joint's outward heading. ``None`` means the joint cannot take this part.
    """
    if not connectors_compatible(source_joint, target_joint):
        return None
    others = [joint for joint in spec.connectors if joint.name != source_joint.name]
    if len(others) != 1:
        return None
    pose = join_pose(target, target_joint, source_joint)
    if math.hypot(pose.x_mm - target.x_mm, pose.y_mm - target.y_mm) < 1.0:
        return None
    outward = normalize_deg(target_joint.direction_deg + target.rotation_z_deg)
    leaving = normalize_deg(others[0].direction_deg + pose.rotation_z_deg)
    return signed_delta_deg(outward, leaving)


def _direction_matches(delta: float, direction: str) -> bool:
    if direction == EXTEND_STRAIGHT:
        return abs(delta) <= _EXTEND_TURN_DEG
    if direction == EXTEND_LEFT:
        return delta < -_EXTEND_TURN_DEG
    if direction == EXTEND_RIGHT:
        return delta > _EXTEND_TURN_DEG
    return False


def extend_pose(
    target: PartInstance,
    target_joint: ConnectorSpec,
    spec: PartSpec,
    direction: str,
) -> Pose | None:
    """Pose that seats ``spec`` on ``target_joint`` so the free end leaves in ``direction``."""
    best: tuple[tuple[float, int], Pose] | None = None
    for index, source in enumerate(spec.connectors):
        delta = continuation_delta_deg(target, target_joint, spec, source)
        if delta is None or not _direction_matches(delta, direction):
            continue
        pose = join_pose(target, target_joint, source)
        rank = (abs(delta), index)
        if best is None or rank < best[0]:
            best = (rank, pose)
    if best is None:
        return None
    return best[1]


def offered_extend_directions(
    target: PartInstance,
    target_joint: ConnectorSpec,
    straight: PartSpec | None,
    curve: PartSpec | None,
) -> tuple[str, ...]:
    """Directions for which a standard part can actually be joined to this free joint."""
    found: list[str] = []
    for direction, spec in (
        (EXTEND_LEFT, curve),
        (EXTEND_STRAIGHT, straight),
        (EXTEND_RIGHT, curve),
    ):
        if spec is None:
            continue
        if extend_pose(target, target_joint, spec, direction) is not None:
            found.append(direction)
    return tuple(found)


def placed_continuation_delta_deg(
    target: PartInstance,
    target_joint: ConnectorSpec,
    created: PartInstance,
    created_spec: PartSpec,
) -> float | None:
    """Signed turn from ``target_joint`` to the free end of a part that is already seated."""
    if not connector_occupied(target, target_joint, ((created, created_spec),)):
        return None
    point = world_xy(target, target_joint.x_mm, target_joint.y_mm)
    free: ConnectorSpec | None = None
    for source in created_spec.connectors:
        other = world_xy(created, source.x_mm, source.y_mm)
        if math.hypot(point[0] - other[0], point[1] - other[1]) <= 1.0:
            continue
        if free is not None:
            return None
        free = source
    if free is None:
        return None
    outward = normalize_deg(target_joint.direction_deg + target.rotation_z_deg)
    leaving = normalize_deg(free.direction_deg + created.rotation_z_deg)
    return signed_delta_deg(outward, leaving)


def can_dock(
    candidate: PartSpec,
    target: PartInstance,
    target_spec: PartSpec,
    placed: Sequence[tuple[PartInstance, PartSpec]],
) -> bool:
    """Whether ``candidate`` can be joined to a free compatible joint of ``target``."""
    for connector in target_spec.connectors:
        if connector_occupied(target, connector, placed):
            continue
        for source in candidate.connectors:
            if not connectors_compatible(source, connector):
                continue
            pose = join_pose(target, connector, source)
            if math.hypot(pose.x_mm - target.x_mm, pose.y_mm - target.y_mm) >= 1.0:
                return True
    return False


def quantize(value: float, step_mm: float) -> float:
    if step_mm <= 0:
        return value
    return round(value / step_mm) * step_mm


def apply_grid(pose: Pose, grid_mm: float | None) -> Pose:
    """Leave the pose alone when the grid is off."""
    if grid_mm is None:
        return pose
    return Pose(quantize(pose.x_mm, grid_mm), quantize(pose.y_mm, grid_mm), pose.rotation_z_deg)


def snap_pose(
    moving: PartSpec,
    proposed: Pose,
    placed: Sequence[tuple[PartInstance, PartSpec]],
    *,
    snap_mm: float,
    grid_mm: float | None,
) -> Pose:
    """Move onto the nearest compatible joint, or onto the grid when nothing is close.

    Distance is measured between the joint that would meet and the joint already on the plan.
    Equal distances prefer the earlier instance, then the earlier joint, so the result does not
    jump between candidates.
    """
    if snap_mm < 0:
        raise ValidationError("error.planner.part")
    best: tuple[tuple[float, str, int, int], Pose] | None = None
    proposed_rotation = normalize_deg(proposed.rotation_z_deg)
    proposed_instance = PartInstance(
        "moving", 0, proposed.x_mm, proposed.y_mm, rotation_z_deg=proposed_rotation
    )
    for target_instance, target_part in placed:
        for target_index, target in enumerate(target_part.connectors):
            target_point = world_xy(target_instance, target.x_mm, target.y_mm)
            target_dir = normalize_deg(target.direction_deg + target_instance.rotation_z_deg)
            for source_index, source in enumerate(moving.connectors):
                if not connectors_compatible(source, target):
                    continue
                rotation = normalize_deg(target_dir + 180.0 - source.direction_deg)
                rotated = rotate_xy(source.x_mm, source.y_mm, rotation)
                origin = (target_point[0] - rotated[0], target_point[1] - rotated[1])
                current = world_xy(proposed_instance, source.x_mm, source.y_mm)
                distance = math.hypot(current[0] - target_point[0], current[1] - target_point[1])
                if distance > snap_mm:
                    continue
                key = (distance, target_instance.id, target_index, source_index)
                pose = Pose(origin[0], origin[1], rotation)
                if best is None or key < best[0]:
                    best = (key, pose)
    if best is not None:
        return best[1]
    return apply_grid(
        Pose(proposed.x_mm, proposed.y_mm, proposed_rotation),
        grid_mm,
    )


def lane_world_point(
    instance: PartInstance, connector: ConnectorSpec, lane: int
) -> tuple[float, float]:
    """Where one lane crosses a joint. Lane 1 stays on the part's negative local y."""
    if lane not in connector.lanes:
        raise ValidationError("error.planner.lane")
    index = connector.lanes.index(lane)
    offset = (index - (len(connector.lanes) - 1) / 2) * LANE_PITCH_MM
    return world_xy(instance, connector.x_mm, connector.y_mm + offset)


def lane_offset_mm(index: int, lane_count: int) -> float:
    """Local y of one lane on a straight joint. Lane 1 is the negative side."""
    return (index - (lane_count - 1) / 2.0) * LANE_PITCH_MM


def curve_lane_radius(center_mm: float, index: int, lane_count: int) -> float:
    """Groove radius on a catalogue curve.

    The arc bends to the right as it runs from joint a to joint b, so lane 1
    (the left lane of a straight) is the outer groove. That is what lines up
    with a straight after the parts are joined.
    """
    return center_mm - lane_offset_mm(index, lane_count)


def span_point(span: SlotSpan, t: float) -> tuple[float, float]:
    """Point at fraction ``t`` along one span. ``t`` is 0 at the start and 1 at the end."""
    if span.kind == SPAN_ARC:
        angle = math.radians(span.start_deg + span.sweep_deg * t)
        return (
            span.x0 + span.radius_mm * math.cos(angle),
            span.y0 + span.radius_mm * math.sin(angle),
        )
    return (span.x0 + (span.x1 - span.x0) * t, span.y0 + (span.y1 - span.y0) * t)


def s_bend(x0: float, y0: float, x1: float, y1: float) -> tuple[SlotSpan, ...]:
    """Two arcs from ``(x0, y0)`` to ``(x1, y1)``, tangent to +x at both ends."""
    run = x1 - x0
    rise = y1 - y0
    if run <= 1.0 or abs(rise) < 1.0:
        return (SlotSpan.line(x0, y0, x1, y1),)
    dx = run / 2.0
    dy = rise / 2.0
    alpha = 2.0 * math.atan2(abs(dy), dx)
    radius = abs(dy) / (1.0 - math.cos(alpha))
    sign = 1.0 if dy >= 0.0 else -1.0
    start1 = -sign * 90.0
    sweep1 = sign * math.degrees(alpha)
    mid_x = x0 + dx
    mid_y = y0 + dy
    center2_y = y1 - sign * radius
    start2 = math.degrees(math.atan2(mid_y - center2_y, mid_x - x1))
    end_angle = sign * 90.0
    sweep2 = (end_angle - start2 + 180.0) % 360.0 - 180.0
    if sweep1 > 0.0 and sweep2 > 0.0:
        sweep2 -= 360.0
    if sweep1 < 0.0 and sweep2 < 0.0:
        sweep2 += 360.0
    return (
        SlotSpan.arc(x0, y0 + sign * radius, radius, start1, sweep1),
        SlotSpan.arc(x1, center2_y, radius, start2, sweep2),
    )


def _through_lanes(length: float, lane_count: int) -> tuple[SlotPath, ...]:
    half = length / 2.0
    paths: list[SlotPath] = []
    for index in range(lane_count):
        y = lane_offset_mm(index, lane_count)
        paths.append(SlotPath((SlotSpan.line(-half, y, half, y),)))
    return tuple(paths)


def _center_line(length: float) -> SlotPath:
    half = length / 2.0
    return SlotPath((SlotSpan.line(-half, 0.0, half, 0.0),), PATH_CENTER)


def lane_change_paths(length: float, side: str) -> tuple[SlotPath, ...]:
    """Both through grooves plus the branch that changes lane.

    ``left`` moves from lane 2 to lane 1, ``right`` the other way, ``both`` crosses.
    """
    half = length / 2.0
    margin = length * 0.22
    x0 = -half + margin
    x1 = half - margin
    paths: list[SlotPath] = list(_through_lanes(length, 2))
    if side in {"left", "both"}:
        paths.append(SlotPath(s_bend(x0, LANE_PITCH_MM / 2.0, x1, -LANE_PITCH_MM / 2.0)))
    if side in {"right", "both"}:
        paths.append(SlotPath(s_bend(x0, -LANE_PITCH_MM / 2.0, x1, LANE_PITCH_MM / 2.0)))
    paths.append(_center_line(length))
    return tuple(paths)


def lateral_bow_paths(length: float, shift: float) -> tuple[SlotPath, ...]:
    """Both lanes and the centre bow sideways by ``shift`` and return."""
    half = length / 2.0
    paths: list[SlotPath] = []
    for index in range(2):
        y = lane_offset_mm(index, 2)
        spans = s_bend(-half, y, 0.0, y + shift) + s_bend(0.0, y + shift, half, y)
        paths.append(SlotPath(spans))
    center = s_bend(-half, 0.0, 0.0, shift) + s_bend(0.0, shift, half, 0.0)
    paths.append(SlotPath(center, PATH_CENTER))
    return tuple(paths)


def chicane_paths(length: float) -> tuple[SlotPath, ...]:
    """Both lanes weave to one side and then the other. The centre follows."""
    half = length / 2.0
    shift = LANE_PITCH_MM * 0.36
    stations = (-half, -half / 3.0, half / 3.0, half)
    offsets = (0.0, shift, -shift, 0.0)
    bases = (lane_offset_mm(0, 2), lane_offset_mm(1, 2), 0.0)
    kinds = (PATH_SLOT, PATH_SLOT, PATH_CENTER)
    paths: list[SlotPath] = []
    for base, kind in zip(bases, kinds, strict=True):
        spans: list[SlotSpan] = []
        points = tuple(zip(stations, offsets, strict=True))
        for (x_a, off_a), (x_b, off_b) in pairwise(points):
            spans.extend(s_bend(x_a, base + off_a, x_b, base + off_b))
        paths.append(SlotPath(tuple(spans), kind))
    return tuple(paths)


def encode_slot_paths(paths: tuple[SlotPath, ...]) -> list[dict[str, object]]:
    encoded: list[dict[str, object]] = []
    for path in paths:
        spans: list[dict[str, object]] = []
        for span in path.spans:
            if span.kind == SPAN_ARC:
                spans.append(
                    {
                        "kind": SPAN_ARC,
                        "cx": span.x0,
                        "cy": span.y0,
                        "radius": span.radius_mm,
                        "start_deg": span.start_deg,
                        "sweep_deg": span.sweep_deg,
                    }
                )
            else:
                spans.append(
                    {
                        "kind": SPAN_LINE,
                        "x0": span.x0,
                        "y0": span.y0,
                        "x1": span.x1,
                        "y1": span.y1,
                    }
                )
        encoded.append({"kind": path.kind, "spans": spans})
    return encoded


def decode_slot_paths(payload: object) -> tuple[SlotPath, ...]:
    """Read stored grooves. A missing or broken value means the ordinary lanes."""
    if not isinstance(payload, list):
        return ()
    paths: list[SlotPath] = []
    for entry in payload:
        if not isinstance(entry, dict):
            return ()
        raw_spans = entry.get("spans")
        if not isinstance(raw_spans, list):
            return ()
        spans: list[SlotSpan] = []
        for raw in raw_spans:
            span = _decode_span(raw)
            if span is None:
                return ()
            spans.append(span)
        kind = entry.get("kind", PATH_SLOT)
        if kind not in {PATH_SLOT, PATH_CENTER} or not spans:
            return ()
        paths.append(SlotPath(tuple(spans), str(kind)))
    return tuple(paths)


def _decode_span(raw: object) -> SlotSpan | None:
    if not isinstance(raw, dict):
        return None
    kind = raw.get("kind")
    try:
        if kind == SPAN_ARC:
            return SlotSpan.arc(
                float(raw["cx"]),
                float(raw["cy"]),
                float(raw["radius"]),
                float(raw["start_deg"]),
                float(raw["sweep_deg"]),
            )
        if kind == SPAN_LINE:
            return SlotSpan.line(
                float(raw["x0"]), float(raw["y0"]), float(raw["x1"]), float(raw["y1"])
            )
    except (KeyError, TypeError, ValueError):
        return None
    return None


def standard_catalog() -> tuple[PartSpec, ...]:
    """Carrera rails. Evolution and Digital 132 share these pieces, so each one is stored once.

    Identity is the designation plus the article number. The stored scale is 1:24, the scale
    of these rails, even when 1:32 cars run on them. Lengths, radii and angles follow the
    catalogue: straights 345 / 115 / 86 mm, centre radii 300 / 500 / 700 / 900 mm.
    """
    scale = CATALOG_SCALE
    specs: list[PartSpec] = []

    def add(spec: PartSpec) -> None:
        specs.append(spec)

    def straight(
        article: str,
        name: str,
        length: float,
        category: str = STRAIGHT,
        slot_paths: tuple[SlotPath, ...] = (),
    ) -> None:
        width = track_width(2)
        add(
            PartSpec(
                article_number=article,
                scale=scale,
                name=name,
                category=category,
                length_mm=length,
                width_mm=width,
                height_mm=None,
                radius_mm=None,
                angle_deg=0.0,
                lane_count=2,
                connectors=straight_connectors(length, 2),
                outline=rectangle(length, width),
                slot_paths=slot_paths,
            )
        )

    straight("20020601", "Standardgerade", 345.0)
    straight("20020611", "1/3-Gerade", 115.0)
    straight("20020612", "1/4-Gerade", 86.0)
    straight("20030343", "Spurwechsel links", 345.0, LANE_CHANGE, lane_change_paths(345.0, "left"))
    straight(
        "20030345", "Spurwechsel rechts", 345.0, LANE_CHANGE, lane_change_paths(345.0, "right")
    )
    straight("20030347", "Doppelspurwechsel", 345.0, LANE_CHANGE, lane_change_paths(345.0, "both"))
    straight("20020517", "Weiche", 345.0, SWITCH, lane_change_paths(345.0, "left"))
    straight("20030350", "Engstelle links", 690.0, SPECIAL, lateral_bow_paths(690.0, -32.0))
    straight("20030351", "Engstelle rechts", 690.0, SPECIAL, lateral_bow_paths(690.0, 32.0))
    straight("20030373", "Schikane", 1035.0, SPECIAL, chicane_paths(1035.0))

    pit_width = track_width(1)
    add(
        PartSpec(
            article_number="20030341",
            scale=scale,
            name="Pitlane-Gerade",
            category=PITLANE,
            length_mm=345.0,
            width_mm=pit_width,
            height_mm=None,
            radius_mm=None,
            angle_deg=0.0,
            lane_count=1,
            connectors=straight_connectors(345.0, 1),
            outline=rectangle(345.0, pit_width),
        )
    )
    # Entry and exit are two different bodies. The article number keeps a suffix so each
    # joint pattern stays one part.
    add(pit_end("20030356-E", "Pitlane-Einfahrt", pit_side=1.0))
    add(pit_end("20030356-A", "Pitlane-Ausfahrt", pit_side=-1.0))

    for article, name, radius, angle in (
        ("20020577", "Kurve R1 30°", 300.0, 30.0),
        ("20020571", "Kurve R1 60°", 300.0, 60.0),
        ("20020572", "Kurve R2 30°", 500.0, 30.0),
        ("20020573", "Kurve R3 30°", 700.0, 30.0),
        ("20020578", "Kurve R4 15°", 900.0, 15.0),
    ):
        width = track_width(2)
        add(
            PartSpec(
                article_number=article,
                scale=scale,
                name=name,
                category=CURVE,
                length_mm=None,
                width_mm=width,
                height_mm=None,
                radius_mm=radius,
                angle_deg=angle,
                lane_count=2,
                connectors=curve_connectors(radius, angle, 2),
                outline=arc_outline(radius, angle, width),
            )
        )
    width = track_width(2)
    add(
        PartSpec(
            article_number="20020574",
            scale=scale,
            name="Steilkurve R1 30°",
            category=SPECIAL,
            length_mm=None,
            width_mm=width,
            height_mm=None,
            radius_mm=300.0,
            angle_deg=30.0,
            lane_count=2,
            connectors=curve_connectors(300.0, 30.0, 2),
            outline=arc_outline(300.0, 30.0, width),
        )
    )
    add(
        PartSpec(
            article_number="20020587",
            scale=scale,
            name="Kreuzung",
            category=CROSSING,
            length_mm=345.0,
            width_mm=width,
            height_mm=None,
            radius_mm=None,
            angle_deg=90.0,
            lane_count=2,
            connectors=crossing_connectors(345.0, 2),
            outline=rectangle(345.0, 345.0),
        )
    )
    border_width = 40.0
    add(
        PartSpec(
            article_number="20020560",
            scale=scale,
            name="Randstreifen Standardgerade",
            category=BORDER,
            length_mm=345.0,
            width_mm=border_width,
            height_mm=None,
            radius_mm=None,
            angle_deg=0.0,
            lane_count=2,
            connectors=straight_connectors(345.0, 2, kind=BORDER_JOINT),
            outline=rectangle(345.0, border_width),
        )
    )
    return tuple(specs)


def pit_end(article: str, name: str, *, pit_side: float) -> PartSpec:
    """Main line plus the pit spur. The spur is one lane and only fits a pit straight."""
    length = 345.0
    width = track_width(2) + track_width(1)
    main = lanes_for(2)
    pit = lanes_for(1)
    half = length / 2
    side = pit_side * (track_width(2) / 2 + track_width(1) / 2)
    connectors = (
        ConnectorSpec("main-a", -half, 0.0, 0.0, 180.0, TRACK, main),
        ConnectorSpec("main-b", half, 0.0, 0.0, 0.0, TRACK, main),
        ConnectorSpec("pit", 0.0, side, 0.0, 90.0 if pit_side > 0 else 270.0, TRACK, pit),
    )
    return PartSpec(
        article_number=article,
        scale=CATALOG_SCALE,
        name=name,
        category=PITLANE,
        length_mm=length,
        width_mm=width,
        height_mm=None,
        radius_mm=None,
        angle_deg=0.0,
        lane_count=2,
        connectors=connectors,
        outline=rectangle(length, width),
    )


def build_part(
    *,
    article_number: str,
    scale: str | None,
    name: str,
    category: str,
    length_mm: float | None,
    width_mm: float | None,
    height_mm: float | None,
    radius_mm: float | None,
    angle_deg: float | None,
    lane_count: int,
) -> PartSpec:
    """A part entered in the library. Joints follow the category and the measures."""
    if not isinstance(name, str):
        raise ValidationError("error.planner.part")
    title = name.strip()
    article = normalize_article(article_number)
    identity_key(title, article)
    stored_scale = require_scale(scale)
    if category not in CATEGORIES:
        raise ValidationError("error.planner.part")
    lanes = lanes_for(lane_count)
    width = track_width(len(lanes)) if width_mm is None else width_mm
    if category == CURVE or (category == SPECIAL and radius_mm is not None and angle_deg):
        if radius_mm is None or angle_deg is None or radius_mm <= 0 or angle_deg <= 0:
            raise ValidationError("error.planner.part")
        connectors = curve_connectors(radius_mm, angle_deg, len(lanes))
        outline = arc_outline(radius_mm, angle_deg, width)
        length = None
        paths: tuple[SlotPath, ...] = ()
    elif category == CROSSING:
        if length_mm is None or length_mm <= 0:
            raise ValidationError("error.planner.part")
        connectors = crossing_connectors(length_mm, len(lanes))
        outline = rectangle(length_mm, length_mm)
        length = length_mm
        paths = ()
    else:
        if length_mm is None or length_mm <= 0:
            raise ValidationError("error.planner.part")
        if category == BORDER:
            kind = BORDER_JOINT
        elif category == SUPPORT:
            kind = SUPPORT_JOINT
        else:
            kind = TRACK
        connectors = straight_connectors(length_mm, len(lanes), kind=kind)
        outline = rectangle(length_mm, width)
        length = length_mm
        if category == SWITCH:
            paths = lane_change_paths(length_mm, "left")
        elif category == LANE_CHANGE:
            paths = lane_change_paths(length_mm, "both")
        else:
            paths = ()
    return PartSpec(
        article_number=article,
        scale=stored_scale,
        name=title,
        category=category,
        length_mm=length,
        width_mm=width,
        height_mm=height_mm,
        radius_mm=radius_mm,
        angle_deg=0.0 if angle_deg is None and category == STRAIGHT else angle_deg,
        lane_count=len(lanes),
        connectors=connectors,
        outline=outline,
        slot_paths=paths,
    )


def lookup_specs(records: Sequence[PartRecord]) -> Mapping[int, PartSpec]:
    return {record.id: record.spec for record in records}
