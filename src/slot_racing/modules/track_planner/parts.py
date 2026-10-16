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
from dataclasses import dataclass, replace
from itertools import pairwise
from typing import Literal

from slot_racing.core.domain.lanes import MAX_LANE_COUNT
from slot_racing.core.errors import ValidationError
from slot_racing.modules.track_planner.geometry import (
    ArcSegment,
    CubicBezier,
    GeometryPath,
    LineSegment,
    Segment,
    validate_path,
)
from slot_racing.modules.track_planner.geometry import (
    rotate_xy as rotate_point,
)

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
# A floor-plate sketch. It is stored geometry, not a rail the planner may place.
DRAFT = "draft"
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
# Defaults for a plan that has never stored its own grid or dock distance.
# The dock radius stays 200 mm. A tighter search would reject docks that already
# land on the right joint. Occupancy stays at 1 mm and 20° of opposing heading.
DEFAULT_GRID_MM = 100.0
DEFAULT_SNAP_MM = 200.0
# Untyped source edges whose lengths differ by more than this do not mate.
EDGE_LENGTH_TOLERANCE_MM = 0.1
# The current (since 2021) electronic connecting rail. Not the older plug piece 20515/20520.
START_FINISH_ARTICLE = "20020518"
# Analog bottleneck, two rails of 345 mm. Not the digital pieces 20030350 / 20030351.
BOTTLENECK_ARTICLE = "20020516"
BOTTLENECK_LENGTH_MM = 690.0
# Carrera does not publish the inner slot spacing. Each groove stays on its lane
# and, at the joint of the two rails, sits a quarter of the lane pitch off the
# centreline. The drawn slots and the lane length use that continuation.
BOTTLENECK_INNER_OFFSET_MM = LANE_PITCH_MM / 4.0
_POSITION_LIMIT_MM = 100_000.0

# Continue-build uses catalogue articles, never the displayed designation.
STANDARD_STRAIGHT_ARTICLE = "20020601"
STANDARD_STRAIGHT_NAME = "Standardgerade"
STANDARD_CURVE_ARTICLE = "20020571"
# One sales article, two physical pieces. The package owns this number.
OUTER_BORDER_K3_ARTICLE = "20020563"
OUTER_BORDER_K3_NAME = "Außenrandstreifen Kurve 3/30°"
END_PIECE_K3_NAME = "Endstück Kurve 3/30°"
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
    """One joint in the part's own coordinates.

    ``target_source_id`` is the part an ``attachPoint`` names. A typed joint is
    ``exclusive``: it mates with that part, not with every joint that shares a
    type string. ``has_neighbor`` stores that part's centre. The origin is
    ``centre - rotate(center, angle)``. ``lane_points`` are the groove ends at
    this joint. Empty keeps the 100 mm pitch.
    """

    name: str
    x_mm: float
    y_mm: float
    z_mm: float
    direction_deg: float
    kind: str
    lanes: tuple[int, ...]
    target_source_id: str = ""
    edge_types: tuple[str, ...] = ()
    exclusive: bool = False
    has_neighbor: bool = False
    # Attach-point coordinates. They name the neighbour's centre, not its origin.
    neighbor_x: float = 0.0
    neighbor_y: float = 0.0
    neighbor_angle: float = 0.0
    lane_points: tuple[tuple[float, float], ...] = ()
    # Set on a source edge. Untyped edges mate within :data:`EDGE_LENGTH_TOLERANCE_MM`.
    edge_length_mm: float | None = None


# A slot path is geometry, not a paint special case. ``line`` uses the two endpoints.
# ``arc`` uses the centre ``(x0, y0)``, ``radius_mm`` and the angles. 0° is +x and
# positive angles run toward +y, the same convention as the curve connectors.
SPAN_LINE = "line"
SPAN_ARC = "arc"
SPAN_BEZIER = "bezier"
PATH_SLOT = "slot"
PATH_CENTER = "center"
PATH_CONTOUR = "contour"
PATH_BORDER = "border"
PATH_START = "start"
# A source drawing that is neither a driving groove nor a centre or start mark.
PATH_DRAW = "draw"
BORDER_SLOTS = frozenset({"left", "right", "inner", "outer"})
_STYLE_KEYS = frozenset(
    {
        "kind",
        "spans",
        "lane",
        "slot",
        "stroke",
        "width",
        "cap",
        "fill",
        "fill_alt",
        "dashed",
        "closed",
        "z",
        "extras",
    }
)


@dataclass(frozen=True, slots=True)
class SlotSpan:
    """One piece of a groove, in local millimetres.

    ``line`` uses the two endpoints. ``arc`` uses the centre ``(x0, y0)``,
    ``radius_mm`` and the angles. ``bezier`` stores ``(x0, y0)`` as P0,
    ``(c1x, c1y)`` as P1, ``(c2x, c2y)`` as P2 and ``(x1, y1)`` as P3.
    """

    kind: str
    x0: float
    y0: float
    x1: float
    y1: float
    radius_mm: float = 0.0
    start_deg: float = 0.0
    sweep_deg: float = 0.0
    c1x: float = 0.0
    c1y: float = 0.0
    c2x: float = 0.0
    c2y: float = 0.0

    @staticmethod
    def line(x0: float, y0: float, x1: float, y1: float) -> SlotSpan:
        return SlotSpan(SPAN_LINE, x0, y0, x1, y1)

    @staticmethod
    def arc(cx: float, cy: float, radius_mm: float, start_deg: float, sweep_deg: float) -> SlotSpan:
        return SlotSpan(SPAN_ARC, cx, cy, 0.0, 0.0, radius_mm, start_deg, sweep_deg)

    @staticmethod
    def bezier(
        x0: float,
        y0: float,
        x1: float,
        y1: float,
        x2: float,
        y2: float,
        x3: float,
        y3: float,
    ) -> SlotSpan:
        """P0, P1, P2, P3. The stored span keeps those four points."""
        return SlotSpan(SPAN_BEZIER, x0, y0, x3, y3, 0.0, 0.0, 0.0, x1, y1, x2, y2)


@dataclass(frozen=True, slots=True)
class SlotPath:
    """One continuous groove or drawing. Several spans join end to end.

    ``kind`` is ``slot`` for a driving groove, ``center`` for a centre mark and
    ``draw`` for a source fill or stroke that is not a groove. A part with no
    paths gets the ordinary lanes derived from its measures. Paint fields stay
    empty on a groove, so a drawing cannot move a rail joint.
    """

    spans: tuple[SlotSpan, ...]
    kind: str = PATH_SLOT
    # Set when this groove is one numbered lane. Empty means "in list order".
    lane: int | None = None
    # Set on a border edge. A groove leaves this empty.
    slot: str | None = None
    stroke: str | None = None
    width_mm: float | None = None
    linecap: str | None = None
    fill: str | None = None
    fill_alt: str | None = None
    dashed: bool = False
    closed: bool = False
    # Higher source indexes sit further back. A negative value is a user mark on top.
    z_order: int = 0
    # Drawing keys the importer does not interpret. They stay in the JSON.
    extras: tuple[tuple[str, str], ...] = ()


@dataclass(frozen=True, slots=True)
class AttachmentProfile:
    """Where one accessory may sit. The numbers describe the host, not a world side.

    A retail box is not a field here. How many strips a box holds lives on the
    package, and stock counts this physical definition.
    """

    host_shape: str
    slots: tuple[str, ...]
    host_length_mm: float | None = None
    host_radius_mm: float | None = None
    host_angle_deg: float | None = None
    host_lanes: int | None = None
    # Mating edge in the host's local frame. Empty for a catalogue strip.
    edge: tuple[SlotSpan, ...] = ()


@dataclass(frozen=True, slots=True)
class BorderEdge:
    """One side a border may occupy. The spans are not a rail joint."""

    slot: str
    spans: tuple[SlotSpan, ...]


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
    # Set only when this part rides on another instance instead of joining the track.
    attachment: AttachmentProfile | None = None
    # Exact outline curves. Empty keeps the polygon in ``outline``.
    contours: tuple[SlotPath, ...] = ()
    # Side edges a border may use. They are not rail joints.
    border_edges: tuple[BorderEdge, ...] = ()
    manufacturer: str = ""
    description: str = ""
    # Stable id from the source file. Empty for a part drawn in this application.
    source_id: str = ""
    # Internal piece key such as 20509.2. Empty when the article has one body.
    piece_key: str = ""
    # evolution, digital, or both. Empty on a part that is not from that library.
    systems: tuple[str, ...] = ()
    # Hidden helpers stay in the library and out of the part picker.
    selectable: bool = True
    # False when the source catalogue does not offer this body on its own.
    # Those pieces belong to a placement template. A part drawn here stays listed.
    catalog_listed: bool = True
    # Source ``center``. Attach points and template placeholders name this point.
    center_x_mm: float = 0.0
    center_y_mm: float = 0.0


# A catalogue row, a catalogue row the user changed, or a part they created.
ORIGIN_CATALOG = "catalog"
ORIGIN_MODIFIED = "modified"
ORIGIN_CUSTOM = "custom"
PART_ORIGINS = frozenset({ORIGIN_CATALOG, ORIGIN_MODIFIED, ORIGIN_CUSTOM})


def is_plate_draft(spec: PartSpec) -> bool:
    """True when this row is a floor-plate sketch and must not be placed on a plan."""
    return spec.category == DRAFT


@dataclass(frozen=True, slots=True)
class PartRecord:
    """A :class:`PartSpec` stored in the library.

    ``lineage_id`` stays when the name or the geometry changes. ``revision`` is one
    frozen geometry. Plans keep the revision they were built with.
    """

    id: int
    spec: PartSpec
    lineage_id: str = ""
    revision: int = 1
    origin: str = ORIGIN_CUSTOM
    is_current: bool = True
    # The JSON could not be read. Saving must not replace it with derived lanes.
    geometry_corrupt: bool = False


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
    # Set together. The pose is copied from that instance; the slot is relative to it.
    host_id: str | None = None
    attachment_slot: str | None = None
    # Local pose relative to the host. Used when the border has its own outline.
    host_dx: float = 0.0
    host_dy: float = 0.0
    host_da: float = 0.0


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
    return title, article


def catalog_storage_key(name: str, article_number: str) -> str:
    """Stable key of a bundled catalogue part. A rename does not change it."""
    title, article = identity_key(name, article_number)
    return f"{title}\t{article}"


def same_geometry(left: PartSpec, right: PartSpec) -> bool:
    """True when the two specs describe the same body, joints and grooves.

    Name, article number, scale, manufacturer and description are not geometry.
    """
    return (
        left.category == right.category
        and left.length_mm == right.length_mm
        and left.width_mm == right.width_mm
        and left.height_mm == right.height_mm
        and left.radius_mm == right.radius_mm
        and left.angle_deg == right.angle_deg
        and left.lane_count == right.lane_count
        and left.connectors == right.connectors
        and left.outline == right.outline
        and left.slot_paths == right.slot_paths
        and left.attachment == right.attachment
        and left.contours == right.contours
        and left.border_edges == right.border_edges
    )


def merge_preserved_geometry(stored: PartSpec, incoming: PartSpec) -> PartSpec:
    """Keep stored grooves and joints when the incoming spec has no explicit geometry.

    A form that only repeats the measures must not wipe a switch or a Bézier groove.
    An edit that sends slot paths, contours or border edges is stored as given.
    """
    if _has_explicit_geometry(incoming) or not _has_explicit_geometry(stored):
        return incoming
    if (
        stored.category,
        stored.length_mm,
        stored.width_mm,
        stored.radius_mm,
        stored.angle_deg,
        stored.lane_count,
    ) != (
        incoming.category,
        incoming.length_mm,
        incoming.width_mm,
        incoming.radius_mm,
        incoming.angle_deg,
        incoming.lane_count,
    ):
        return incoming
    return replace(
        incoming,
        connectors=stored.connectors,
        outline=stored.outline,
        slot_paths=stored.slot_paths,
        attachment=stored.attachment,
        contours=stored.contours,
        border_edges=stored.border_edges,
    )


def _has_explicit_geometry(spec: PartSpec) -> bool:
    edge = spec.attachment.edge if spec.attachment is not None else ()
    return bool(spec.slot_paths or spec.contours or spec.border_edges or edge)


def normalize_deg(angle: float) -> float:
    if isinstance(angle, bool) or not isinstance(angle, (int, float)) or not math.isfinite(angle):
        raise ValidationError("error.planner.part")
    return float(angle) % 360.0


def rotate_xy(x_mm: float, y_mm: float, rotation_z_deg: float) -> tuple[float, float]:
    """Clockwise rotation in the plan's y-down coordinates."""
    return rotate_point(x_mm, y_mm, rotation_z_deg)


def origin_from_midpoint(
    mid_x: float,
    mid_y: float,
    angle_deg: float,
    center_x: float,
    center_y: float,
) -> tuple[float, float]:
    """Origin of a part whose centre sits at ``(mid_x, mid_y)``.

    Source attach points and template placeholders name the centre. The plan
    stores the geometric origin. ``angle_deg`` is the part's own rotation in
    degrees. A centre at the origin leaves the midpoint unchanged.
    """
    rotated_x, rotated_y = rotate_xy(center_x, center_y, angle_deg)
    return (mid_x - rotated_x, mid_y - rotated_y)


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


def connectors_compatible(
    left: ConnectorSpec,
    right: ConnectorSpec,
    *,
    left_source: str = "",
    right_source: str = "",
) -> bool:
    """Whether these two joints may meet.

    Two exclusive joints still mate only when an attach point names the other
    part. Equal type strings are not enough, so a digital switch keeps the
    pairing it already had. A source edge mates by length and, when either
    edge is typed, by that type. Untyped rails still share a lane list.
    """
    if left.kind != right.kind or left.kind not in CONNECTOR_KINDS:
        return False
    if left.exclusive and right.exclusive:
        if not left_source or not right_source:
            return False
        return left.target_source_id == right_source or right.target_source_id == left_source
    if left.edge_length_mm is not None or right.edge_length_mm is not None:
        return _edge_pair(left, right, left_source=left_source, right_source=right_source)
    if left.exclusive or right.exclusive:
        return False
    return left.lanes == right.lanes


def _edge_pair(
    left: ConnectorSpec,
    right: ConnectorSpec,
    *,
    left_source: str,
    right_source: str,
) -> bool:
    """Two original connector edges. Untyped lengths may differ by 0.1 mm."""
    if left.edge_length_mm is None or right.edge_length_mm is None:
        return False
    if abs(left.edge_length_mm - right.edge_length_mm) > EDGE_LENGTH_TOLERANCE_MM:
        return False
    if not _edge_types_agree(left.edge_types, right.edge_types):
        return False
    left_names_other = not left.target_source_id or left.target_source_id == right_source
    right_names_other = not right.target_source_id or right.target_source_id == left_source
    return left_names_other and right_names_other


def _edge_types_agree(left: tuple[str, ...], right: tuple[str, ...]) -> bool:
    if not left and not right:
        return True
    if not left or not right:
        return False
    return left == right


def rails_can_join(left: ConnectorSpec, right: ConnectorSpec) -> bool:
    """Two rail ends. A border edge or a support joint is never a rail target."""
    return left.kind == TRACK and connectors_compatible(left, right)


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
    source_id: str = "",
) -> bool:
    """True when another compatible joint already meets this one."""
    point = world_xy(instance, connector.x_mm, connector.y_mm)
    direction = normalize_deg(connector.direction_deg + instance.rotation_z_deg)
    for other, spec in placed:
        if other.id == instance.id:
            continue
        for candidate in spec.connectors:
            if not connectors_compatible(
                connector,
                candidate,
                left_source=source_id,
                right_source=spec.source_id,
            ):
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


def connector_is_free(
    instance: PartInstance,
    connector: ConnectorSpec,
    placed: Sequence[tuple[PartInstance, PartSpec]],
    *,
    tolerance_mm: float = 1.0,
    source_id: str = "",
) -> bool:
    """True when this joint is not already met by another compatible joint.

    Snap candidates and continue-build arrows both use this. A joint that is occupied for
    one of them is occupied for the other.
    """
    return not connector_occupied(
        instance, connector, placed, tolerance_mm=tolerance_mm, source_id=source_id
    )


def free_connector_indexes(
    instance: PartInstance,
    spec: PartSpec,
    placed: Sequence[tuple[PartInstance, PartSpec]],
) -> tuple[int, ...]:
    """Indexes of joints that may start a snap or a continue-build arrow."""
    return tuple(
        index
        for index, connector in enumerate(spec.connectors)
        if connector_is_free(instance, connector, placed, source_id=spec.source_id)
    )


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
        if normalize_article(spec.article_number) == wanted and not spec.piece_key
    ]
    if not matches:
        matches = [
            (part_id, spec)
            for part_id, spec in catalog.items()
            if normalize_article(spec.article_number) == wanted
        ]
    if not matches:
        return None
    matches.sort(key=lambda item: item[0])
    return matches[0]


ACTIVE_STRAIGHT_SOURCE = "4187668d-ee71-440a-9bb2-6e400e017416"
ACTIVE_CURVE_SOURCE = "cc253612-a0cb-4b17-b940-18460c337acb"


def find_source_part(
    catalog: Mapping[int, PartSpec], source_id: str
) -> tuple[int, PartSpec] | None:
    """The current part with this source UUID."""
    matches = [(part_id, spec) for part_id, spec in catalog.items() if spec.source_id == source_id]
    if not matches:
        return None
    matches.sort(key=lambda item: item[0])
    return matches[0]


def standard_extend_parts(catalog: Mapping[int, PartSpec]) -> dict[str, tuple[int, PartSpec]]:
    """Straight continues with the standard straight; both turns use the standard curve."""
    chosen: dict[str, tuple[int, PartSpec]] = {}
    straight = find_source_part(catalog, ACTIVE_STRAIGHT_SOURCE) or find_catalog_part(
        catalog, STANDARD_STRAIGHT_ARTICLE
    )
    curve = find_source_part(catalog, ACTIVE_CURVE_SOURCE) or find_catalog_part(
        catalog, STANDARD_CURVE_ARTICLE
    )
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


def _turn_ends(spec: PartSpec) -> tuple[ConnectorSpec, ...]:
    """The two rail ends that define a continuation.

    Border joints stay on the part but are not a second driving end. A part with
    exactly two connectors is unchanged.
    """
    rails = tuple(joint for joint in spec.connectors if joint.kind == TRACK and not joint.exclusive)
    if len(rails) == 2:
        return rails
    return spec.connectors


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
    ends = _turn_ends(spec)
    others = [joint for joint in ends if joint.name != source_joint.name]
    if source_joint.name not in {joint.name for joint in ends} or len(others) != 1:
        return None
    pose = join_pose(target, target_joint, source_joint)
    # A second arc of the same radius keeps the circle centre and only changes rotation.
    # Reject the join only when it would drop the part onto the one already there.
    same_place = math.hypot(pose.x_mm - target.x_mm, pose.y_mm - target.y_mm) < 1.0
    same_turn = (
        abs(signed_delta_deg(target.rotation_z_deg, pose.rotation_z_deg)) <= _EXTEND_TURN_DEG
    )
    if same_place and same_turn:
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
    """Pose that seats ``spec`` on ``target_joint`` so the free end leaves in ``direction``.

    An attach point that names this part wins. Its stored pose is the source
    continuation. A generic rail join is only the fallback.
    """
    named = _named_extend_pose(target, target_joint, spec, direction)
    if named is not None:
        return named
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
    seated = False
    for source in _turn_ends(created_spec):
        other = world_xy(created, source.x_mm, source.y_mm)
        if math.hypot(point[0] - other[0], point[1] - other[1]) <= 1.0:
            seated = True
            continue
        if free is not None:
            return None
        free = source
    if free is None or not seated:
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
        if connector_occupied(target, connector, placed, source_id=target_spec.source_id):
            continue
        for source in candidate.connectors:
            if not connectors_compatible(
                source,
                connector,
                left_source=candidate.source_id,
                right_source=target_spec.source_id,
            ):
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
    """Dock one new part onto the nearest free joint, or onto the grid.

    The part is not on the plan yet, so every one of its joints is free. A joint on the plan
    is a target only when it is free as well. One selected part uses :func:`snap_selection`
    with the same rule; this entry keeps the placement of a part that has no previous pose.
    """
    if snap_mm < 0:
        raise ValidationError("error.planner.part")
    rotation = normalize_deg(proposed.rotation_z_deg)
    instance = PartInstance("moving", 0, proposed.x_mm, proposed.y_mm, rotation_z_deg=rotation)
    poses = snap_selection(
        ((instance, moving),),
        placed,
        anchor_id=instance.id,
        snap_mm=snap_mm,
        grid_mm=grid_mm,
        source_blockers=(),
        target_blockers=placed,
    )
    return poses[instance.id]


def _named_extend_pose(
    target: PartInstance,
    target_joint: ConnectorSpec,
    spec: PartSpec,
    direction: str,
) -> Pose | None:
    if (
        not target_joint.has_neighbor
        or not spec.source_id
        or target_joint.target_source_id != spec.source_id
    ):
        return None
    pose = neighbor_pose(
        target,
        target_joint,
        center_x_mm=spec.center_x_mm,
        center_y_mm=spec.center_y_mm,
    )
    created = PartInstance("named", 0, pose.x_mm, pose.y_mm, rotation_z_deg=pose.rotation_z_deg)
    delta = placed_continuation_delta_deg(target, target_joint, created, spec)
    if delta is None or not _direction_matches(delta, direction):
        return None
    return pose


def neighbor_pose(
    host: PartInstance,
    connector: ConnectorSpec,
    *,
    center_x_mm: float = 0.0,
    center_y_mm: float = 0.0,
) -> Pose:
    """World origin of the part an attach point names.

    The stored neighbour coordinates are that part's centre. ``center_x_mm``
    and ``center_y_mm`` are the neighbour's own centre offset. Both are zero
    for a part whose geometric origin is its centre.
    """
    local_x, local_y = origin_from_midpoint(
        connector.neighbor_x,
        connector.neighbor_y,
        connector.neighbor_angle,
        center_x_mm,
        center_y_mm,
    )
    x_mm, y_mm = world_xy(host, local_x, local_y)
    return Pose(x_mm, y_mm, normalize_deg(host.rotation_z_deg + connector.neighbor_angle))


def _named_neighbor_pose(
    proposed: Sequence[tuple[PartInstance, PartSpec]],
    stationary: Sequence[tuple[PartInstance, PartSpec]],
    snap_mm: float,
    blockers: Sequence[tuple[PartInstance, PartSpec]],
) -> dict[str, Pose] | None:
    """Seat a part on the attach point that names it, when the drop is already close."""
    best: tuple[tuple[float, float], PartInstance, Pose] | None = None
    for moving, spec in proposed:
        if not spec.source_id:
            continue
        for host, host_spec in stationary:
            if host.id == moving.id:
                continue
            for connector in host_spec.connectors:
                if not connector.has_neighbor or connector.target_source_id != spec.source_id:
                    continue
                if connector_occupied(host, connector, blockers, source_id=host_spec.source_id):
                    continue
                seated = neighbor_pose(
                    host,
                    connector,
                    center_x_mm=spec.center_x_mm,
                    center_y_mm=spec.center_y_mm,
                )
                distance = math.hypot(moving.x_mm - seated.x_mm, moving.y_mm - seated.y_mm)
                if distance > snap_mm:
                    continue
                turn = abs(signed_delta_deg(moving.rotation_z_deg, seated.rotation_z_deg))
                key = (distance, turn)
                if best is None or key < best[0]:
                    best = (key, moving, seated)
    if best is None:
        return None
    _key, moving, seated = best
    delta = signed_delta_deg(moving.rotation_z_deg, seated.rotation_z_deg)
    poses: dict[str, Pose] = {}
    for instance, _spec in proposed:
        if instance.id == moving.id:
            poses[instance.id] = seated
            continue
        dx, dy = rotate_xy(instance.x_mm - moving.x_mm, instance.y_mm - moving.y_mm, delta)
        poses[instance.id] = Pose(
            seated.x_mm + dx,
            seated.y_mm + dy,
            normalize_deg(instance.rotation_z_deg + delta),
        )
    return poses


def snap_selection(
    proposed: Sequence[tuple[PartInstance, PartSpec]],
    stationary: Sequence[tuple[PartInstance, PartSpec]],
    *,
    anchor_id: str,
    snap_mm: float,
    grid_mm: float | None,
    source_blockers: Sequence[tuple[PartInstance, PartSpec]],
    target_blockers: Sequence[tuple[PartInstance, PartSpec]],
) -> dict[str, Pose]:
    """Dock a selection as one rigid body onto the nearest pair of free joints.

    ``proposed`` carries the dragged poses. Occupancy is read from ``source_blockers`` and
    ``target_blockers``, which are the poses from before this drag. A joint that was already
    connected stays out of this snap, including a joint that meets another selected part or a
    part that is not selected. The winning joint is the anchor: the whole selection rotates
    around it, then shifts so the anchor lands on the target. Without a free pair in range,
    the anchor falls onto the grid and the others keep their offset.
    """
    if snap_mm < 0 or not proposed or not any(instance.id == anchor_id for instance, _ in proposed):
        raise ValidationError("error.planner.part")
    named = _named_neighbor_pose(proposed, stationary, snap_mm, target_blockers)
    if named is not None:
        return named
    original = {instance.id: instance for instance, _spec in source_blockers}
    best: tuple[tuple[float, str, int, int, str], _SnapJoin] | None = None
    for moving, spec in proposed:
        prior = original.get(moving.id, moving)
        blockers = source_blockers if moving.id in original else ()
        for source_index in free_connector_indexes(prior, spec, blockers):
            source = spec.connectors[source_index]
            current = world_xy(moving, source.x_mm, source.y_mm)
            for target_instance, target_spec in stationary:
                if target_instance.id == moving.id:
                    continue
                indexes = free_connector_indexes(target_instance, target_spec, target_blockers)
                for target_index in indexes:
                    target = target_spec.connectors[target_index]
                    if not connectors_compatible(
                        source,
                        target,
                        left_source=spec.source_id,
                        right_source=target_spec.source_id,
                    ):
                        continue
                    target_point = world_xy(target_instance, target.x_mm, target.y_mm)
                    distance = math.hypot(
                        current[0] - target_point[0], current[1] - target_point[1]
                    )
                    if distance > snap_mm:
                        continue
                    key = (distance, target_instance.id, target_index, source_index, moving.id)
                    join = _SnapJoin(moving, source, target_instance, target)
                    if best is None or key < best[0]:
                        best = (key, join)
    if best is None:
        return _grid_selection(proposed, anchor_id, grid_mm)
    join = best[1]
    seated = join_pose(join.target, join.target_joint, join.source_joint)
    delta = signed_delta_deg(join.moving.rotation_z_deg, seated.rotation_z_deg)
    anchor = world_xy(join.target, join.target_joint.x_mm, join.target_joint.y_mm)
    return _rotate_selection(proposed, join.moving, join.source_joint, anchor, delta)


@dataclass(frozen=True, slots=True)
class _SnapJoin:
    moving: PartInstance
    source_joint: ConnectorSpec
    target: PartInstance
    target_joint: ConnectorSpec


def _rotate_selection(
    proposed: Sequence[tuple[PartInstance, PartSpec]],
    owner: PartInstance,
    source_joint: ConnectorSpec,
    target_point: tuple[float, float],
    delta_deg: float,
) -> dict[str, Pose]:
    """One rotation about the moving joint, then one shift onto the target joint."""
    pivot = world_xy(owner, source_joint.x_mm, source_joint.y_mm)
    poses: dict[str, Pose] = {}
    for instance, _spec in proposed:
        dx, dy = rotate_xy(instance.x_mm - pivot[0], instance.y_mm - pivot[1], delta_deg)
        poses[instance.id] = Pose(
            target_point[0] + dx,
            target_point[1] + dy,
            normalize_deg(instance.rotation_z_deg + delta_deg),
        )
    return poses


def _grid_selection(
    proposed: Sequence[tuple[PartInstance, PartSpec]],
    anchor_id: str,
    grid_mm: float | None,
) -> dict[str, Pose]:
    anchor = next(instance for instance, _spec in proposed if instance.id == anchor_id)
    gridded = apply_grid(
        Pose(anchor.x_mm, anchor.y_mm, normalize_deg(anchor.rotation_z_deg)),
        grid_mm,
    )
    dx = gridded.x_mm - anchor.x_mm
    dy = gridded.y_mm - anchor.y_mm
    return {
        instance.id: Pose(
            instance.x_mm + dx,
            instance.y_mm + dy,
            normalize_deg(instance.rotation_z_deg),
        )
        for instance, _spec in proposed
    }


def lane_world_point(
    instance: PartInstance, connector: ConnectorSpec, lane: int
) -> tuple[float, float]:
    """Where one lane crosses a joint. Lane 1 stays on the part's negative local y."""
    if lane not in connector.lanes:
        raise ValidationError("error.planner.lane")
    index = connector.lanes.index(lane)
    if connector.lane_points:
        point = connector.lane_points[index]
        return world_xy(instance, point[0], point[1])
    offset = (index - (len(connector.lanes) - 1) / 2) * LANE_PITCH_MM
    return world_xy(instance, connector.x_mm, connector.y_mm + offset)


def lane_offset_mm(index: int, lane_count: int) -> float:
    """Local y of one lane on a straight joint. Lane 1 is the negative side."""
    return (index - (lane_count - 1) / 2.0) * LANE_PITCH_MM


def curve_bends_left(spec: PartSpec) -> bool:
    """True when a stored groove arc sweeps toward negative y. Catalogue curves do not.

    Paint and centre marks are not grooves, so their arcs do not flip the joint.
    """
    return any(
        span.kind == SPAN_ARC and span.sweep_deg < 0.0
        for path in spec.slot_paths
        if path.kind == PATH_SLOT
        for span in path.spans
    )


def curve_lane_radius(center_mm: float, index: int, lane_count: int) -> float:
    """Groove radius on a catalogue curve.

    The arc bends to the right as it runs from joint a to joint b, so lane 1
    (the left lane of a straight) is the outer groove. That is what lines up
    with a straight after the parts are joined.
    """
    return center_mm - lane_offset_mm(index, lane_count)


def span_point(span: SlotSpan, t: float) -> tuple[float, float]:
    """Point at fraction ``t`` along one span. ``t`` is 0 at the start and 1 at the end."""
    return as_segment(span).point_at(t)


def as_segment(span: SlotSpan) -> Segment:
    """The shared segment for a stored span. Line and arc keep their old parameters."""
    if span.kind == SPAN_ARC:
        return ArcSegment(span.x0, span.y0, span.radius_mm, span.start_deg, span.sweep_deg)
    if span.kind == SPAN_BEZIER:
        return CubicBezier(
            span.x0, span.y0, span.c1x, span.c1y, span.c2x, span.c2y, span.x1, span.y1
        )
    return LineSegment(span.x0, span.y0, span.x1, span.y1)


def geometry_of(path: SlotPath) -> GeometryPath:
    """One stored path as a :class:`GeometryPath`. The role follows ``path.kind``."""
    role: Literal["slot", "center", "contour", "border"]
    if path.kind == PATH_CENTER:
        role = "center"
    elif path.kind in {PATH_CONTOUR, PATH_DRAW} or path.closed:
        role = "contour"
    elif path.kind == PATH_BORDER:
        role = "border"
    else:
        role = "slot"
    return GeometryPath(
        tuple(as_segment(span) for span in path.spans),
        role,
        path.lane,
        path.slot,
    )


def driving_paths(spec: PartSpec) -> tuple[GeometryPath, ...]:
    """Driving grooves. Stored paths win. Otherwise the measures stay compact and are derived."""
    stored = [path for path in spec.slot_paths if path.kind == PATH_SLOT]
    if stored:
        paths: list[GeometryPath] = []
        for index, path in enumerate(stored):
            geometry = geometry_of(path)
            lane = path.lane if path.lane is not None else index + 1
            paths.append(GeometryPath(geometry.segments, "slot", lane, path.slot))
        return tuple(paths)
    if spec.category == DRAFT:
        return ()
    if _parametric_curve(spec):
        return _curve_paths(spec)
    length = spec.length_mm
    if spec.category == SWITCH and length:
        return _numbered(lane_change_paths(length, "left"))
    if spec.category == LANE_CHANGE and length:
        return _numbered(lane_change_paths(length, "both"))
    if length:
        return _numbered(_through_lanes(length, spec.lane_count))
    return ()


def _numbered(paths: tuple[SlotPath, ...]) -> tuple[GeometryPath, ...]:
    numbered: list[GeometryPath] = []
    lane = 1
    for path in paths:
        if path.kind != PATH_SLOT:
            continue
        numbered.append(GeometryPath(tuple(as_segment(span) for span in path.spans), "slot", lane))
        lane += 1
    return tuple(numbered)


def _parametric_curve(spec: PartSpec) -> bool:
    return (
        spec.category in {CURVE, SPECIAL}
        and spec.radius_mm is not None
        and spec.angle_deg not in (None, 0.0)
        and spec.radius_mm > 0.0
        and spec.length_mm is None
    )


def _curve_paths(spec: PartSpec) -> tuple[GeometryPath, ...]:
    radius = float(spec.radius_mm or 0.0)
    angle = float(spec.angle_deg or 0.0)
    start = -angle / 2.0
    return tuple(
        GeometryPath(
            (
                ArcSegment(
                    0.0,
                    0.0,
                    curve_lane_radius(radius, index, spec.lane_count),
                    start,
                    angle,
                ),
            ),
            "slot",
            index + 1,
        )
        for index in range(spec.lane_count)
    )


def validate_part_geometry(spec: PartSpec) -> None:
    """Check one part's own curves, lanes and border edges. This is not a lap-closure test."""
    for path in spec.slot_paths:
        if path.kind == PATH_DRAW:
            continue
        validate_path(geometry_of(path))
    for path in spec.contours:
        contour = geometry_of(path)
        validate_path(GeometryPath(contour.segments, "contour", contour.lane, contour.slot))
    for edge in spec.border_edges:
        _validate_border_edge(edge.slot, edge.spans)
    profile = spec.attachment
    if profile is not None and profile.host_shape == "custom" and not profile.edge:
        raise ValidationError("error.planner.geometry")
    if profile is not None and profile.edge:
        for slot in profile.slots:
            _validate_border_edge(slot, profile.edge)
    track_lanes: set[int] = set()
    for connector in spec.connectors:
        for value in (connector.x_mm, connector.y_mm, connector.z_mm, connector.direction_deg):
            if (
                isinstance(value, bool)
                or not isinstance(value, (int, float))
                or not math.isfinite(value)
            ):
                raise ValidationError("error.planner.geometry")
        if connector.kind not in CONNECTOR_KINDS:
            raise ValidationError("error.planner.geometry")
        for lane in connector.lanes:
            if isinstance(lane, bool) or not isinstance(lane, int) or lane < 1:
                raise ValidationError("error.planner.geometry")
            if lane > MAX_LANE_COUNT:
                raise ValidationError("error.planner.geometry")
            if connector.kind == TRACK:
                track_lanes.add(lane)
    if not track_lanes:
        return
    for path in spec.slot_paths:
        if path.kind == PATH_SLOT and path.lane is not None and path.lane not in track_lanes:
            raise ValidationError("error.planner.geometry")


def _validate_border_edge(slot: str, spans: tuple[SlotSpan, ...]) -> None:
    if slot not in BORDER_SLOTS or not spans:
        raise ValidationError("error.planner.geometry")
    validate_path(GeometryPath(tuple(as_segment(span) for span in spans), "border", None, slot))


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


def bottleneck_paths(length: float) -> tuple[SlotPath, ...]:
    """Two grooves that narrow toward the centre joint and open again.

    Lane 1 stays lane 1 and lane 2 stays lane 2. The ends meet the ordinary
    straight anchors, so the part snaps like any other two-lane rail.
    """
    half = length / 2.0
    inner = BOTTLENECK_INNER_OFFSET_MM
    paths: list[SlotPath] = []
    for index in range(2):
        y = lane_offset_mm(index, 2)
        narrowed = inner if y >= 0.0 else -inner
        spans = s_bend(-half, y, 0.0, narrowed) + s_bend(0.0, narrowed, half, y)
        paths.append(SlotPath(spans))
    paths.append(_center_line(length))
    return tuple(paths)


START_PIECE_KEY = "20509.2"


def is_start_finish_part(spec: PartSpec) -> bool:
    """The start straight is the only start and finish piece."""
    return spec.piece_key == START_PIECE_KEY


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
    return [_encode_path(path) for path in paths]


def encode_geometry(spec: PartSpec) -> list[dict[str, object]]:
    """Grooves, contours and border edges in one JSON list. Catalogue rows omit the extras."""
    encoded = encode_slot_paths(spec.slot_paths)
    encoded.extend(_encode_path(SlotPath(path.spans, PATH_CONTOUR)) for path in spec.contours)
    profile = spec.attachment
    if profile is None:
        for edge in spec.border_edges:
            encoded.append(
                {"kind": PATH_BORDER, "slot": edge.slot, "spans": _encode_spans(edge.spans)}
            )
    elif profile.edge:
        for slot in profile.slots:
            encoded.append(
                {"kind": PATH_BORDER, "slot": slot, "spans": _encode_spans(profile.edge)}
            )
    return encoded


def geometry_payload_corrupt(payload: object) -> bool:
    """True when stored JSON is present but cannot be read as grooves, contours or edges.

    ``None`` and an empty list still mean the ordinary lanes. A broken custom
    entry must not be treated as that empty list.
    """
    if payload is None or payload == []:
        return False
    if not isinstance(payload, list):
        return True
    slots, contours, edges = decode_geometry(payload)
    return not slots and not contours and not edges


def decode_slot_paths(payload: object) -> tuple[SlotPath, ...]:
    """Read stored grooves. A missing or broken value means the ordinary lanes."""
    slots, _contours, _edges = decode_geometry(payload)
    return slots


def decode_geometry(
    payload: object,
) -> tuple[tuple[SlotPath, ...], tuple[SlotPath, ...], tuple[BorderEdge, ...]]:
    """Split a stored list into grooves, contours and border edges.

    A broken entry clears the whole value, which is how an older row asks for
    the ordinary lanes. Line and arc objects from before Bézier curves still load.
    """
    if not isinstance(payload, list):
        return (), (), ()
    slots: list[SlotPath] = []
    contours: list[SlotPath] = []
    edges: list[BorderEdge] = []
    for entry in payload:
        if not isinstance(entry, dict):
            return (), (), ()
        raw_spans = entry.get("spans")
        if not isinstance(raw_spans, list):
            return (), (), ()
        spans: list[SlotSpan] = []
        for raw in raw_spans:
            span = _decode_span(raw)
            if span is None:
                return (), (), ()
            spans.append(span)
        if not spans:
            return (), (), ()
        kind = entry.get("kind", PATH_SLOT)
        lane = entry.get("lane")
        slot = entry.get("slot")
        lane_no = lane if isinstance(lane, int) and not isinstance(lane, bool) else None
        slot_name = slot if isinstance(slot, str) else None
        style = _decode_style(entry)
        if kind in {PATH_SLOT, PATH_CENTER, PATH_START, PATH_DRAW}:
            slots.append(SlotPath(tuple(spans), str(kind), lane_no, slot_name, *style))
        elif kind == PATH_CONTOUR:
            contours.append(SlotPath(tuple(spans), PATH_CONTOUR, None, None, *style))
        elif kind == PATH_BORDER and slot_name in BORDER_SLOTS:
            edges.append(BorderEdge(slot_name, tuple(spans)))
        else:
            return (), (), ()
    return tuple(slots), tuple(contours), tuple(edges)


def _encode_path(path: SlotPath) -> dict[str, object]:
    encoded: dict[str, object] = {"kind": path.kind, "spans": _encode_spans(path.spans)}
    if path.lane is not None:
        encoded["lane"] = path.lane
    if path.slot is not None:
        encoded["slot"] = path.slot
    if path.stroke is not None:
        encoded["stroke"] = path.stroke
    if path.width_mm is not None:
        encoded["width"] = path.width_mm
    if path.linecap is not None:
        encoded["cap"] = path.linecap
    if path.fill is not None:
        encoded["fill"] = path.fill
    if path.fill_alt is not None:
        encoded["fill_alt"] = path.fill_alt
    if path.dashed:
        encoded["dashed"] = True
    if path.closed:
        encoded["closed"] = True
    if path.z_order:
        encoded["z"] = path.z_order
    if path.extras:
        encoded["extras"] = [[key, value] for key, value in path.extras]
    return encoded


def _encode_spans(spans: tuple[SlotSpan, ...]) -> list[dict[str, object]]:
    return [_encode_span(span) for span in spans]


def _encode_span(span: SlotSpan) -> dict[str, object]:
    if span.kind == SPAN_ARC:
        return {
            "kind": SPAN_ARC,
            "cx": span.x0,
            "cy": span.y0,
            "radius": span.radius_mm,
            "start_deg": span.start_deg,
            "sweep_deg": span.sweep_deg,
        }
    if span.kind == SPAN_BEZIER:
        return {
            "kind": SPAN_BEZIER,
            "x0": span.x0,
            "y0": span.y0,
            "x1": span.c1x,
            "y1": span.c1y,
            "x2": span.c2x,
            "y2": span.c2y,
            "x3": span.x1,
            "y3": span.y1,
        }
    return {
        "kind": SPAN_LINE,
        "x0": span.x0,
        "y0": span.y0,
        "x1": span.x1,
        "y1": span.y1,
    }


def _decode_style(
    entry: Mapping[str, object],
) -> tuple[
    str | None,
    float | None,
    str | None,
    str | None,
    str | None,
    bool,
    bool,
    int,
    tuple[tuple[str, str], ...],
]:
    """Paint fields. A row written before drawings were stored leaves them empty."""
    extras = _decode_extras(entry.get("extras"))
    unknown = [(key, _style_text(value)) for key, value in entry.items() if key not in _STYLE_KEYS]
    z_raw = entry.get("z", 0)
    z_order = z_raw if isinstance(z_raw, int) and not isinstance(z_raw, bool) else 0
    return (
        _optional_text(entry.get("stroke")),
        _optional_float(entry.get("width")) if "width" in entry else None,
        _optional_text(entry.get("cap")),
        _optional_text(entry.get("fill")),
        _optional_text(entry.get("fill_alt")),
        entry.get("dashed") is True,
        entry.get("closed") is True,
        z_order,
        extras + tuple(unknown),
    )


def _decode_extras(raw: object) -> tuple[tuple[str, str], ...]:
    if not isinstance(raw, list):
        return ()
    found: list[tuple[str, str]] = []
    for item in raw:
        if (
            isinstance(item, list)
            and len(item) == 2
            and isinstance(item[0], str)
            and isinstance(item[1], str)
        ):
            found.append((item[0], item[1]))
    return tuple(found)


def _optional_text(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    text = value.strip()
    return text or None


def _optional_float(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    number = float(value)
    if not math.isfinite(number):
        return None
    return number


def _style_text(value: object) -> str:
    if isinstance(value, str):
        return value
    return str(value)


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
        if kind == SPAN_BEZIER:
            return SlotSpan.bezier(
                float(raw["x0"]),
                float(raw["y0"]),
                float(raw["x1"]),
                float(raw["y1"]),
                float(raw["x2"]),
                float(raw["y2"]),
                float(raw["x3"]),
                float(raw["y3"]),
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

    straight(STANDARD_STRAIGHT_ARTICLE, STANDARD_STRAIGHT_NAME, 345.0)
    # 20020518 since 2021: electronic connecting rail, 345 mm, plus a separate
    # standard straight in the package. Only the connecting rail is this part.
    straight(START_FINISH_ARTICLE, "Anschlussgerade", 345.0)
    straight("20020611", "1/3-Gerade", 115.0)
    straight("20020612", "1/4-Gerade", 86.0)
    straight("20030343", "Spurwechsel links", 345.0, LANE_CHANGE, lane_change_paths(345.0, "left"))
    straight(
        "20030345", "Spurwechsel rechts", 345.0, LANE_CHANGE, lane_change_paths(345.0, "right")
    )
    straight("20030347", "Doppelspurwechsel", 345.0, LANE_CHANGE, lane_change_paths(345.0, "both"))
    straight("20020517", "Weiche", 345.0, SWITCH, lane_change_paths(345.0, "left"))
    straight(
        BOTTLENECK_ARTICLE,
        "Engstelle",
        BOTTLENECK_LENGTH_MM,
        SPECIAL,
        bottleneck_paths(BOTTLENECK_LENGTH_MM),
    )
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
    # Plan radii match the flat curve of the same number (R2/R3/R4). The outer
    # diameters follow: R2 about 1.2 m, R4 about 2.0 m. Banking is not stored.
    for article, name, radius, angle in (
        ("20020575", "Steilkurve R2 30°", 500.0, 30.0),
        ("20020576", "Steilkurve R3 30°", 700.0, 30.0),
        ("20020579", "Steilkurve R4 15°", 900.0, 15.0),
    ):
        add(
            PartSpec(
                article_number=article,
                scale=scale,
                name=name,
                category=SPECIAL,
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
    # One mountable piece per definition. A retail box that holds several of
    # them is a package, not a second meaning of this row.
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
            connectors=(),
            outline=rectangle(345.0, border_width),
            attachment=AttachmentProfile(
                "straight",
                ("left", "right"),
                host_length_mm=345.0,
                host_lanes=2,
            ),
        )
    )
    # Centre of the strip: road radius ± half the two-lane width ± half the strip.
    outer_radius = 700.0 + 100.0 + border_width / 2.0
    inner_radius = 700.0 - 100.0 - border_width / 2.0
    add(
        PartSpec(
            article_number=OUTER_BORDER_K3_ARTICLE,
            scale=scale,
            name=OUTER_BORDER_K3_NAME,
            category=BORDER,
            length_mm=None,
            width_mm=border_width,
            height_mm=None,
            radius_mm=700.0,
            angle_deg=30.0,
            lane_count=2,
            connectors=(),
            outline=arc_outline(outer_radius, 30.0, border_width),
            attachment=AttachmentProfile(
                "curve_flat",
                ("outer",),
                host_radius_mm=700.0,
                host_angle_deg=30.0,
                host_lanes=2,
            ),
        )
    )
    # Same sales article as the strip. The name keeps the two pieces distinct.
    # The outline is only a stand-in until a later catalogue gives it a shape.
    add(
        PartSpec(
            article_number=OUTER_BORDER_K3_ARTICLE,
            scale=scale,
            name=END_PIECE_K3_NAME,
            category=BORDER,
            length_mm=40.0,
            width_mm=border_width,
            height_mm=None,
            radius_mm=None,
            angle_deg=0.0,
            lane_count=2,
            connectors=(),
            outline=rectangle(40.0, border_width),
        )
    )
    add(
        PartSpec(
            article_number="20020592",
            scale=scale,
            name="Innenrandstreifen Kurve 3/30°",
            category=BORDER,
            length_mm=None,
            width_mm=border_width,
            height_mm=None,
            radius_mm=700.0,
            angle_deg=30.0,
            lane_count=2,
            connectors=(),
            outline=arc_outline(inner_radius, 30.0, border_width),
            attachment=AttachmentProfile(
                "curve_flat",
                ("inner",),
                host_radius_mm=700.0,
                host_angle_deg=30.0,
                host_lanes=2,
            ),
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
