"""Track pieces: one definition, many placed instances.

A definition is an original part (system, article number, scale, measures, connectors, outline).
An instance is that part used once on a plan, with its own position and rotation. Editing an
instance never changes the definition.

Coordinates are millimetres. Positive y points down, matching the plan canvas. ``rotation_z_deg``
is clockwise. ``z`` and the other two rotations are stored so a later 3D view can use them; the
2D planner only applies x, y and rotation z.

Camera zones, sensors and timing points are not part of a piece.
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from slot_racing.core.domain.lanes import MAX_LANE_COUNT
from slot_racing.core.errors import ValidationError

SCALES = ("1:24", "1:32", "1:43")
# These systems have one scale. The part still stores that scale; it is not left empty.
IMPLIED_SCALE: dict[str, str] = {
    "Carrera Digital 132": "1:32",
    "Carrera Digital 124": "1:24",
    "Carrera Evolution": "1:32",
    "Carrera GO!!!": "1:43",
}

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


@dataclass(frozen=True, slots=True)
class PartSpec:
    """The original part. ``scale`` is always one of :data:`SCALES`, even for a known system."""

    system: str
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


@dataclass(frozen=True, slots=True)
class Pose:
    x_mm: float
    y_mm: float
    rotation_z_deg: float


def implied_scale(system: str) -> str | None:
    return IMPLIED_SCALE.get(system.strip())


def resolved_scale(system: str, scale: str | None) -> str:
    """Scale stored on the part. A known system still stores its own scale."""
    known = implied_scale(system)
    typed = _typed_scale(scale)
    if known is not None:
        if typed is not None and typed != known:
            raise ValidationError("error.planner.scale")
        return known
    if typed is None or typed not in SCALES:
        raise ValidationError("error.planner.scale")
    return typed


def stored_scale(system: str, scale: str | None) -> str:
    """The scale written on every part. It is never left empty."""
    return resolved_scale(system, scale)


def _typed_scale(scale: str | None) -> str | None:
    if scale is None:
        return None
    if not isinstance(scale, str):
        raise ValidationError("error.planner.scale")
    text = scale.strip()
    return text or None


def identity_key(system: str, article_number: str, scale: str | None) -> tuple[str, str, str]:
    """System, article number and the effective scale. No manufacturer."""
    system_name = system.strip()
    article = article_number.strip()
    if not system_name or not article:
        raise ValidationError("error.planner.part")
    return (system_name, article, resolved_scale(system_name, scale))


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


def standard_catalog() -> tuple[PartSpec, ...]:
    """Original Carrera Digital 132 pieces. The same definition can be placed any number of times.

    Lengths, radii and angles follow the Carrera catalogue: straights 345 / 115 / 86 mm,
    centre radii 300 / 500 / 700 / 900 mm. Packs that only repeat a piece are not a second part.
    """
    system = "Carrera Digital 132"
    scale = resolved_scale(system, None)
    specs: list[PartSpec] = []

    def add(spec: PartSpec) -> None:
        specs.append(spec)

    def straight(article: str, name: str, length: float, category: str = STRAIGHT) -> None:
        width = track_width(2)
        add(
            PartSpec(
                system,
                article,
                scale,
                name,
                category,
                length,
                width,
                None,
                None,
                0.0,
                2,
                straight_connectors(length, 2),
                rectangle(length, width),
            )
        )

    straight("20020601", "Standardgerade", 345.0)
    straight("20020611", "1/3-Gerade", 115.0)
    straight("20020612", "1/4-Gerade", 86.0)
    straight("20030343", "Spurwechsel links", 345.0, LANE_CHANGE)
    straight("20030345", "Spurwechsel rechts", 345.0, LANE_CHANGE)
    straight("20030347", "Doppelspurwechsel", 345.0, LANE_CHANGE)
    straight("20020517", "Weiche", 345.0, SWITCH)
    straight("20030350", "Engstelle links", 690.0, SPECIAL)
    straight("20030351", "Engstelle rechts", 690.0, SPECIAL)
    straight("20030373", "Schikane", 1035.0, SPECIAL)

    pit_width = track_width(1)
    add(
        PartSpec(
            system,
            "20030341",
            scale,
            "Pitlane-Gerade",
            PITLANE,
            345.0,
            pit_width,
            None,
            None,
            0.0,
            1,
            straight_connectors(345.0, 1),
            rectangle(345.0, pit_width),
        )
    )
    # Carrera sells the pit entry and exit inside kit 20030356, not as their own articles.
    # These two bodies keep that kit number with a suffix so each joint pattern is one part.
    add(pit_end(system, "20030356-E", "Pitlane-Einfahrt", pit_side=1.0))
    add(pit_end(system, "20030356-A", "Pitlane-Ausfahrt", pit_side=-1.0))

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
                system,
                article,
                scale,
                name,
                CURVE,
                None,
                width,
                None,
                radius,
                angle,
                2,
                curve_connectors(radius, angle, 2),
                arc_outline(radius, angle, width),
            )
        )
    add(
        PartSpec(
            system,
            "20020574",
            scale,
            "Steilkurve R1 30°",
            SPECIAL,
            None,
            track_width(2),
            None,
            300.0,
            30.0,
            2,
            curve_connectors(300.0, 30.0, 2),
            arc_outline(300.0, 30.0, track_width(2)),
        )
    )
    add(
        PartSpec(
            system,
            "20020587",
            scale,
            "Kreuzung",
            CROSSING,
            345.0,
            track_width(2),
            None,
            None,
            90.0,
            2,
            crossing_connectors(345.0, 2),
            rectangle(345.0, 345.0),
        )
    )
    border_width = 40.0
    add(
        PartSpec(
            system,
            "20020560",
            scale,
            "Randstreifen Standardgerade",
            BORDER,
            345.0,
            border_width,
            None,
            None,
            0.0,
            2,
            straight_connectors(345.0, 2, kind=BORDER_JOINT),
            rectangle(345.0, border_width),
        )
    )
    return tuple(specs)


def pit_end(system: str, article: str, name: str, *, pit_side: float) -> PartSpec:
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
        system,
        article,
        resolved_scale(system, None),
        name,
        PITLANE,
        length,
        width,
        None,
        None,
        0.0,
        2,
        connectors,
        rectangle(length, width),
    )


def build_part(
    *,
    system: str,
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
    system_name, article, _scale = identity_key(system, article_number, scale)
    title = name.strip()
    if not title or category not in CATEGORIES:
        raise ValidationError("error.planner.part")
    lanes = lanes_for(lane_count)
    width = track_width(len(lanes)) if width_mm is None else width_mm
    if category == CURVE or (category == SPECIAL and radius_mm is not None and angle_deg):
        if radius_mm is None or angle_deg is None or radius_mm <= 0 or angle_deg <= 0:
            raise ValidationError("error.planner.part")
        connectors = curve_connectors(radius_mm, angle_deg, len(lanes))
        outline = arc_outline(radius_mm, angle_deg, width)
        length = None
    elif category == CROSSING:
        if length_mm is None or length_mm <= 0:
            raise ValidationError("error.planner.part")
        connectors = crossing_connectors(length_mm, len(lanes))
        outline = rectangle(length_mm, length_mm)
        length = length_mm
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
    return PartSpec(
        system=system_name,
        article_number=article,
        scale=stored_scale(system_name, scale),
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
    )


def lookup_specs(records: Sequence[PartRecord]) -> Mapping[int, PartSpec]:
    return {record.id: record.spec for record in records}
