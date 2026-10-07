"""Structured track plan. Pieces and markers are data, not a picture.

A plan belongs to one track. The lane count stays on that track; the plan only records where
each piece sits on the grid and where later timing points can attach to a piece and a lane.
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from typing import Any
from uuid import uuid4

from slot_racing.core.domain import TrackId
from slot_racing.core.errors import ValidationError
from slot_racing.modules.track_planner.attachment import (
    ATTACHMENT_SLOTS,
    attachment_fits,
    follow_hosts,
    slot_taken,
)
from slot_racing.modules.track_planner.parts import (
    DEFAULT_GRID_MM,
    DEFAULT_SNAP_MM,
    EXTEND_DIRECTIONS,
    PartInstance,
    PartSpec,
    Pose,
    connector_occupied,
    connectors_compatible,
    extend_article,
    extend_pose,
    find_catalog_part,
    is_start_finish_part,
    join_pose,
    rotate_xy,
    snap_pose,
    snap_selection,
)

_POSITION_LIMIT_MM = 100_000.0

PLAN_VERSION = 1

STRAIGHT_H = "straight_h"
STRAIGHT_V = "straight_v"
CURVE_90 = "curve_90"
PIECE_TYPES = frozenset({STRAIGHT_H, STRAIGHT_V, CURVE_90})

START_FINISH = "start_finish"
SENSOR = "sensor"
CAMERA = "camera"
TIMING_POINT = "timing_point"
SECTOR = "sector"
MARKER_KINDS = frozenset({START_FINISH, SENSOR, CAMERA, TIMING_POINT, SECTOR})

CLOCKWISE = "clockwise"
COUNTERCLOCKWISE = "counterclockwise"
DIRECTIONS = frozenset({CLOCKWISE, COUNTERCLOCKWISE})

GRID_LIMIT = 64
_ID_LIMIT = 40

# Footprint in grid cells. The same footprint is used for every lane count; lanes are drawn
# inside it so a 2-lane plan never reserves space for a fourth lane.
_SPANS = {
    STRAIGHT_H: (4, 2),
    STRAIGHT_V: (2, 4),
    CURVE_90: (4, 4),
}


@dataclass(frozen=True, slots=True)
class Piece:
    id: str
    piece_type: str
    x: int
    y: int
    rotation: int


@dataclass(frozen=True, slots=True)
class Marker:
    """A point that can later name a sensor, a camera, a sector or the start/finish line.

    ``piece_id`` ties the point to a piece. ``lane`` is 1-based and empty when the point covers
    every lane, which is how the start/finish line is stored.
    """

    id: str
    kind: str
    x: int
    y: int
    piece_id: str | None = None
    lane: int | None = None


@dataclass(frozen=True, slots=True)
class TrackPlan:
    track_id: TrackId
    version: int
    direction: str
    pieces: tuple[Piece, ...]
    markers: tuple[Marker, ...]
    grid_enabled: bool = False
    grid_mm: float = DEFAULT_GRID_MM
    snap_mm: float = DEFAULT_SNAP_MM
    instances: tuple[PartInstance, ...] = ()

    def start_finish(self) -> Marker | None:
        found = [marker for marker in self.markers if marker.kind == START_FINISH]
        return found[0] if found else None


def new_id() -> str:
    return uuid4().hex[:12]


def empty_plan(track_id: TrackId) -> TrackPlan:
    return TrackPlan(
        track_id=track_id,
        version=PLAN_VERSION,
        direction=CLOCKWISE,
        pieces=(),
        markers=(),
    )


def span(piece_type: str) -> tuple[int, int]:
    """Width and height of a piece in grid cells."""
    try:
        return _SPANS[piece_type]
    except KeyError as error:
        raise ValidationError("error.planner.piece") from error


def lane_centers(lane_count: int, span_px: float) -> tuple[float, ...]:
    """Where each lane is drawn across ``span_px``. The count is the track's, never a fixed 4."""
    if lane_count < 1:
        return ()
    step = span_px / (lane_count + 1)
    return tuple(step * (index + 1) for index in range(lane_count))


def travel_vector(piece_type: str, rotation: int, direction: str) -> tuple[int, int]:
    """Driving direction as a grid step. Positive y points downward. Reversing the plan flips it."""
    if piece_type == STRAIGHT_H:
        vector = (1, 0)
    elif piece_type == STRAIGHT_V:
        vector = (0, 1)
    elif piece_type == CURVE_90:
        vector = {0: (1, 1), 90: (-1, 1), 180: (-1, -1), 270: (1, -1)}[rotation]
    else:
        raise ValidationError("error.planner.piece")
    if direction == COUNTERCLOCKWISE:
        return (-vector[0], -vector[1])
    if direction != CLOCKWISE:
        raise ValidationError("error.planner.direction")
    return vector


def add_piece(plan: TrackPlan, piece_type: str, x: int, y: int, rotation: int = 0) -> TrackPlan:
    piece = Piece(id=new_id(), piece_type=piece_type, x=x, y=y, rotation=rotation)
    _check_piece(piece)
    return _replace(plan, pieces=(*plan.pieces, piece))


def move_piece(plan: TrackPlan, piece_id: str, x: int, y: int) -> TrackPlan:
    pieces = tuple(
        _checked(Piece(piece.id, piece.piece_type, x, y, piece.rotation))
        if piece.id == piece_id
        else piece
        for piece in plan.pieces
    )
    if not any(piece.id == piece_id for piece in plan.pieces):
        raise ValidationError("error.planner.piece")
    return _replace(plan, pieces=pieces)


def rotate_piece(plan: TrackPlan, piece_id: str, rotation: int) -> TrackPlan:
    pieces = tuple(
        _checked(Piece(piece.id, piece.piece_type, piece.x, piece.y, rotation))
        if piece.id == piece_id
        else piece
        for piece in plan.pieces
    )
    if not any(piece.id == piece_id for piece in plan.pieces):
        raise ValidationError("error.planner.piece")
    return _replace(plan, pieces=pieces)


def remove_piece(plan: TrackPlan, piece_id: str) -> TrackPlan:
    pieces = tuple(piece for piece in plan.pieces if piece.id != piece_id)
    if len(pieces) == len(plan.pieces):
        raise ValidationError("error.planner.piece")
    markers = tuple(
        Marker(marker.id, marker.kind, marker.x, marker.y, None, marker.lane)
        if marker.piece_id == piece_id
        else marker
        for marker in plan.markers
    )
    return _replace(plan, pieces=pieces, markers=markers)


def place_start_finish(plan: TrackPlan, x: int, y: int, piece_id: str | None = None) -> TrackPlan:
    _check_cell(x, y)
    if piece_id is not None and piece_id not in {piece.id for piece in plan.pieces}:
        raise ValidationError("error.planner.piece")
    current = plan.start_finish()
    marker = Marker(
        id=current.id if current is not None else new_id(),
        kind=START_FINISH,
        x=x,
        y=y,
        piece_id=piece_id,
        lane=None,
    )
    others = tuple(item for item in plan.markers if item.kind != START_FINISH)
    return _replace(plan, markers=(*others, marker))


def move_marker(plan: TrackPlan, marker_id: str, x: int, y: int) -> TrackPlan:
    if not any(marker.id == marker_id for marker in plan.markers):
        raise ValidationError("error.planner.piece")
    markers = tuple(
        Marker(marker.id, marker.kind, x, y, marker.piece_id, marker.lane)
        if marker.id == marker_id
        else marker
        for marker in plan.markers
    )
    return _replace(plan, markers=markers)


def remove_marker(plan: TrackPlan, marker_id: str) -> TrackPlan:
    markers = tuple(marker for marker in plan.markers if marker.id != marker_id)
    if len(markers) == len(plan.markers):
        raise ValidationError("error.planner.piece")
    return _replace(plan, markers=markers)


def set_direction(plan: TrackPlan, direction: str) -> TrackPlan:
    if direction not in DIRECTIONS:
        raise ValidationError("error.planner.direction")
    return _replace(plan, direction=direction)


def reset_plan(plan: TrackPlan) -> TrackPlan:
    return empty_plan(plan.track_id)


def next_origin(plan: TrackPlan, piece_type: str) -> tuple[int, int]:
    """First free grid origin for a new piece, scanning from the top left."""
    width, height = span(piece_type)
    occupied = [(piece.x, piece.y, *span(piece.piece_type)) for piece in plan.pieces]
    for y in range(0, GRID_LIMIT - height + 1, 2):
        for x in range(0, GRID_LIMIT - width + 1, 2):
            if not any(_overlaps(x, y, width, height, *box) for box in occupied):
                return x, y
    return 0, 0


def to_document(plan: TrackPlan) -> dict[str, Any]:
    checked = _validate(plan)
    return {
        "version": checked.version,
        "direction": checked.direction,
        "pieces": [
            {
                "id": piece.id,
                "type": piece.piece_type,
                "x": piece.x,
                "y": piece.y,
                "rotation": piece.rotation,
            }
            for piece in checked.pieces
        ],
        "markers": [
            {
                "id": marker.id,
                "kind": marker.kind,
                "x": marker.x,
                "y": marker.y,
                "piece_id": marker.piece_id,
                "lane": marker.lane,
            }
            for marker in checked.markers
        ],
        "grid_enabled": checked.grid_enabled,
        "grid_mm": checked.grid_mm,
        "snap_mm": checked.snap_mm,
        "instances": [_instance_document(instance) for instance in checked.instances],
    }


def parse_plan(track_id: TrackId, payload: object) -> TrackPlan:
    if not isinstance(payload, dict):
        raise ValidationError("error.planner.invalid")
    version = payload.get("version")
    direction = payload.get("direction")
    raw_pieces = payload.get("pieces")
    raw_markers = payload.get("markers")
    if version != PLAN_VERSION or direction not in DIRECTIONS:
        raise ValidationError("error.planner.invalid")
    if not isinstance(raw_pieces, list) or not isinstance(raw_markers, list):
        raise ValidationError("error.planner.invalid")
    pieces = tuple(_parse_piece(item) for item in raw_pieces)
    markers = tuple(_parse_marker(item) for item in raw_markers)
    raw_instances = payload.get("instances", [])
    if not isinstance(raw_instances, list):
        raise ValidationError("error.planner.invalid")
    return _validate(
        TrackPlan(
            track_id=track_id,
            version=PLAN_VERSION,
            direction=str(direction),
            pieces=pieces,
            markers=markers,
            grid_enabled=_flag(payload.get("grid_enabled", False)),
            grid_mm=_millimetre(payload.get("grid_mm", DEFAULT_GRID_MM)),
            snap_mm=_millimetre(payload.get("snap_mm", DEFAULT_SNAP_MM)),
            instances=tuple(_parse_instance(item) for item in raw_instances),
        )
    )


def validate_lanes(plan: TrackPlan, lane_count: int) -> None:
    """Reject a marker lane the track does not have. The track's own lane count is not written."""
    for marker in plan.markers:
        if marker.lane is None:
            continue
        if lane_count < 1 or not 1 <= marker.lane <= lane_count:
            raise ValidationError("error.planner.lane")


def add_instance(plan: TrackPlan, instance: PartInstance) -> TrackPlan:
    _check_instance(instance)
    return _replace(plan, instances=(*plan.instances, instance))


def instances_center(instances: Sequence[PartInstance]) -> tuple[float, float]:
    """Temporary centre of a selection. It is not stored on the plan."""
    count = len(instances)
    if count == 0:
        return (0.0, 0.0)
    return (
        sum(instance.x_mm for instance in instances) / count,
        sum(instance.y_mm for instance in instances) / count,
    )


def rotate_instances_around(
    plan: TrackPlan,
    instance_ids: Sequence[str],
    center_x: float,
    center_y: float,
    delta_deg: float,
) -> TrackPlan:
    """Turn every listed instance around one shared point. Definitions stay untouched."""
    wanted = {instance_id for instance_id in instance_ids if _movable(plan, instance_id)}
    if not wanted or delta_deg % 360 == 0:
        return plan
    updated: list[PartInstance] = []
    for instance in plan.instances:
        if instance.id not in wanted:
            updated.append(instance)
            continue
        dx, dy = rotate_xy(instance.x_mm - center_x, instance.y_mm - center_y, delta_deg)
        updated.append(
            replace(
                instance,
                x_mm=center_x + dx,
                y_mm=center_y + dy,
                rotation_z_deg=(instance.rotation_z_deg + delta_deg) % 360.0,
            )
        )
    return _replace(plan, instances=tuple(updated))


def duplicate_instances(
    plan: TrackPlan, instance_ids: Sequence[str], dx_mm: float, dy_mm: float
) -> tuple[TrackPlan, tuple[str, ...]]:
    """New instances of the same definitions, shifted so the copy is not on the original.

    Copying a rail also copies the parts attached to it. The copies point at the
    new rails. An attached part is not pasted onto the original rail.
    """
    by_id = {instance.id: instance for instance in plan.instances}
    wanted = {instance_id for instance_id in instance_ids if instance_id in by_id}
    for instance_id in list(wanted):
        host_id = by_id[instance_id].host_id
        if host_id is not None and host_id in by_id:
            wanted.add(host_id)
    for instance in plan.instances:
        if instance.host_id in wanted:
            wanted.add(instance.id)
    if not wanted:
        return plan, ()
    id_map = {instance_id: new_id() for instance_id in wanted}
    created: list[str] = []
    extra: list[PartInstance] = []
    group_map: dict[str, str] = {}
    for instance in plan.instances:
        if instance.id not in wanted:
            continue
        group_id = None if instance.host_id is not None else instance.group_id
        if group_id is not None and group_id not in group_map:
            group_map[group_id] = new_id()
        host_id = None if instance.host_id is None else id_map.get(instance.host_id)
        if instance.host_id is not None and host_id is None:
            continue
        identifier = id_map[instance.id]
        extra.append(
            replace(
                instance,
                id=identifier,
                x_mm=instance.x_mm + dx_mm,
                y_mm=instance.y_mm + dy_mm,
                start_straight=False,
                group_id=None if group_id is None else group_map[group_id],
                host_id=host_id,
            )
        )
        created.append(identifier)
    if not extra:
        return plan, ()
    return _replace(plan, instances=(*plan.instances, *extra)), tuple(created)


def move_instance(plan: TrackPlan, instance_id: str, x_mm: float, y_mm: float) -> TrackPlan:
    return _update_instance(
        plan,
        instance_id,
        lambda instance: replace(instance, x_mm=x_mm, y_mm=y_mm),
    )


def rotate_instance(plan: TrackPlan, instance_id: str, rotation_z_deg: float) -> TrackPlan:
    return _update_instance(
        plan,
        instance_id,
        lambda instance: replace(instance, rotation_z_deg=rotation_z_deg),
    )


def remove_instance(plan: TrackPlan, instance_id: str) -> TrackPlan:
    return remove_instances(plan, (instance_id,))


def remove_instances(plan: TrackPlan, instance_ids: Sequence[str]) -> TrackPlan:
    """Remove the listed instances. A removed rail takes its attached parts with it."""
    requested = set(instance_ids)
    if not any(instance.id in requested for instance in plan.instances):
        raise ValidationError("error.planner.piece")
    drop = set(requested)
    growing = True
    while growing:
        growing = False
        for instance in plan.instances:
            if instance.host_id in drop and instance.id not in drop:
                drop.add(instance.id)
                growing = True
    kept = tuple(instance for instance in plan.instances if instance.id not in drop)
    return _replace(plan, instances=kept)


def set_start_straight(
    plan: TrackPlan, instance_id: str, enabled: bool, catalog: dict[int, PartSpec]
) -> TrackPlan:
    """Mark the connecting rail as start and finish. Any previous mark is cleared.

    A standard straight cannot be turned into the connecting rail, and its stock
    is not involved. The flag stays a property of this one instance.
    """
    current = _require_instance(plan, instance_id)
    spec = catalog.get(current.part_id)
    if enabled and (spec is None or not is_start_finish_part(spec)):
        raise ValidationError("error.planner.start_straight")
    instances: list[PartInstance] = []
    for instance in plan.instances:
        if instance.id == instance_id:
            instances.append(replace(instance, start_straight=enabled))
        elif enabled and instance.start_straight:
            instances.append(replace(instance, start_straight=False))
        else:
            instances.append(instance)
    return _replace(plan, instances=tuple(instances))


def toggle_group(plan: TrackPlan, instance_ids: Sequence[str]) -> TrackPlan:
    """Group the selection, or dissolve it when the selection already is that group."""
    wanted = [
        instance_id
        for instance_id in instance_ids
        if _has_instance(plan, instance_id) and _require_instance(plan, instance_id).host_id is None
    ]
    if len(wanted) < 2:
        return plan
    selected = [instance for instance in plan.instances if instance.id in set(wanted)]
    group_ids = {instance.group_id for instance in selected}
    if len(group_ids) == 1 and None not in group_ids:
        group_id = next(iter(group_ids))
        members = {instance.id for instance in plan.instances if instance.group_id == group_id}
        if members == set(wanted):
            cleared = tuple(
                replace(instance, group_id=None) if instance.group_id == group_id else instance
                for instance in plan.instances
            )
            return _replace(plan, instances=cleared)
    fresh = new_id()
    chosen = set(wanted)
    grouped = tuple(
        replace(instance, group_id=fresh) if instance.id in chosen else instance
        for instance in plan.instances
    )
    return _replace(plan, instances=grouped)


def dock_copy(
    plan: TrackPlan, instance_id: str, connector_index: int, catalog: dict[int, PartSpec]
) -> TrackPlan:
    """Place another instance of the same definition on one free joint."""
    current = _require_instance(plan, instance_id)
    spec = catalog.get(current.part_id)
    if spec is None or not 0 <= connector_index < len(spec.connectors):
        raise ValidationError("error.planner.part")
    target = spec.connectors[connector_index]
    placed = _placed(plan, catalog)
    if connector_occupied(current, target, placed):
        raise ValidationError("error.planner.part")
    best: tuple[float, Pose] | None = None
    for source in spec.connectors:
        if not connectors_compatible(source, target):
            continue
        pose = join_pose(current, target, source)
        if _pose_distance(pose, current) < 1.0:
            continue
        turn = _rotation_delta(pose.rotation_z_deg, current.rotation_z_deg)
        if best is None or turn < best[0]:
            best = (turn, pose)
    if best is None:
        raise ValidationError("error.planner.part")
    pose = best[1]
    return place_instance(
        plan, current.part_id, spec, pose.x_mm, pose.y_mm, pose.rotation_z_deg, catalog
    )


def extend_from_connector(
    plan: TrackPlan,
    instance_id: str,
    connector_index: int,
    direction: str,
    catalog: dict[int, PartSpec],
) -> TrackPlan:
    """Append the standard part that continues ``direction`` from one free joint.

    The result is one ordinary instance, snapped with the same rules as a library drop.
    The caller records that plan as a single undo step. A joint that cannot take the part
    leaves the plan untouched because this function raises instead of returning a new plan.
    """
    current = _require_instance(plan, instance_id)
    current_spec = catalog.get(current.part_id)
    if (
        current_spec is None
        or direction not in EXTEND_DIRECTIONS
        or not 0 <= connector_index < len(current_spec.connectors)
    ):
        raise ValidationError("error.planner.extend")
    target = current_spec.connectors[connector_index]
    if connector_occupied(current, target, _placed(plan, catalog)):
        raise ValidationError("error.planner.extend")
    found = find_catalog_part(catalog, extend_article(direction))
    if found is None:
        raise ValidationError("error.planner.extend")
    part_id, spec = found
    pose = extend_pose(current, target, spec, direction)
    if pose is None:
        raise ValidationError("error.planner.extend")
    updated = place_instance(
        plan, part_id, spec, pose.x_mm, pose.y_mm, pose.rotation_z_deg, catalog
    )
    created = updated.instances[-1]
    if not connector_occupied(current, target, ((created, spec),)):
        raise ValidationError("error.planner.extend")
    return updated


def clone_plan(plan: TrackPlan, track_id: TrackId) -> TrackPlan:
    """A new plan with new instance ids and the same definitions, poses and markers."""
    id_map = {instance.id: new_id() for instance in plan.instances}
    group_map: dict[str, str] = {}
    instances: list[PartInstance] = []
    for instance in plan.instances:
        group_id = None if instance.host_id is not None else instance.group_id
        if group_id is not None and group_id not in group_map:
            group_map[group_id] = new_id()
        host_id = None if instance.host_id is None else id_map.get(instance.host_id)
        if instance.host_id is not None and host_id is None:
            continue
        instances.append(
            replace(
                instance,
                id=id_map[instance.id],
                group_id=None if group_id is None else group_map[group_id],
                host_id=host_id,
            )
        )
    return _validate(
        TrackPlan(
            track_id=track_id,
            version=plan.version,
            direction=plan.direction,
            pieces=plan.pieces,
            markers=plan.markers,
            grid_enabled=plan.grid_enabled,
            grid_mm=plan.grid_mm,
            snap_mm=plan.snap_mm,
            instances=tuple(instances),
        )
    )


def _has_instance(plan: TrackPlan, instance_id: str) -> bool:
    return any(instance.id == instance_id for instance in plan.instances)


def _rotation_delta(left: float, right: float) -> float:
    delta = abs(left - right) % 360.0
    return min(delta, 360.0 - delta)


def _pose_distance(pose: Pose, instance: PartInstance) -> float:
    return math.hypot(pose.x_mm - instance.x_mm, pose.y_mm - instance.y_mm)


def set_plan_grid(plan: TrackPlan, *, enabled: bool, grid_mm: float, snap_mm: float) -> TrackPlan:
    return _replace(plan, grid_enabled=enabled, grid_mm=grid_mm, snap_mm=snap_mm)


def with_instances(plan: TrackPlan, instances: tuple[PartInstance, ...]) -> TrackPlan:
    return _replace(plan, instances=instances)


def place_instance(
    plan: TrackPlan,
    part_id: int,
    spec: PartSpec,
    x_mm: float,
    y_mm: float,
    rotation_z_deg: float,
    catalog: dict[int, PartSpec],
) -> TrackPlan:
    """Append one instance. A nearby compatible joint wins over the grid."""
    if spec.attachment is not None:
        raise ValidationError("error.planner.part")
    placed = _placed(plan, catalog)
    pose = snap_pose(
        spec,
        Pose(x_mm, y_mm, rotation_z_deg),
        placed,
        snap_mm=plan.snap_mm,
        grid_mm=plan.grid_mm if plan.grid_enabled else None,
    )
    instance = PartInstance(
        id=new_id(),
        part_id=part_id,
        x_mm=pose.x_mm,
        y_mm=pose.y_mm,
        rotation_z_deg=pose.rotation_z_deg,
    )
    plan = add_instance(plan, instance)
    if not is_start_finish_part(spec):
        return plan
    known = dict(catalog)
    known[part_id] = spec
    return set_start_straight(plan, instance.id, True, known)


def attach_accessory(
    plan: TrackPlan,
    host_id: str,
    part_id: int,
    spec: PartSpec,
    slot: str,
    catalog: dict[int, PartSpec],
) -> TrackPlan:
    """Bind one accessory to one rail. The pose is the rail's pose."""
    if spec.attachment is None or slot not in ATTACHMENT_SLOTS:
        raise ValidationError("error.planner.part")
    host = _require_instance(plan, host_id)
    if host.host_id is not None:
        raise ValidationError("error.planner.part")
    host_spec = catalog.get(host.part_id)
    if host_spec is None or not attachment_fits(spec, host_spec, slot):
        raise ValidationError("error.planner.part")
    if slot_taken(plan.instances, host_id, slot):
        raise ValidationError("error.planner.part")
    return add_instance(
        plan,
        PartInstance(
            id=new_id(),
            part_id=part_id,
            x_mm=host.x_mm,
            y_mm=host.y_mm,
            z_mm=host.z_mm,
            rotation_x_deg=host.rotation_x_deg,
            rotation_y_deg=host.rotation_y_deg,
            rotation_z_deg=host.rotation_z_deg,
            host_id=host.id,
            attachment_slot=slot,
        ),
    )


def reposition_instance(
    plan: TrackPlan,
    instance_id: str,
    x_mm: float,
    y_mm: float,
    catalog: dict[int, PartSpec],
) -> TrackPlan:
    """Move one part. It is a selection of a single member and uses the same snap."""
    return reposition_selection(plan, {instance_id: (x_mm, y_mm)}, instance_id, catalog)


def reposition_selection(
    plan: TrackPlan,
    proposed_xy: Mapping[str, tuple[float, float]],
    anchor_id: str,
    catalog: dict[int, PartSpec],
) -> TrackPlan:
    """Move every listed part by one shared snap. Rotations stay coupled to that snap.

    ``proposed_xy`` is the drag result. Connections are the joints that already meet on
    ``plan``, before this drag. The anchor only decides which part falls onto the grid when
    no free joint is in range.
    """
    hosts_only = {
        instance_id: point
        for instance_id, point in proposed_xy.items()
        if _movable(plan, instance_id)
    }
    if not hosts_only:
        return plan
    if anchor_id not in hosts_only:
        anchor_id = next(iter(hosts_only))
    proposed_xy = hosts_only
    topology = _placed(plan, catalog)
    by_id = {instance.id: (instance, spec) for instance, spec in topology}
    proposed: list[tuple[PartInstance, PartSpec]] = []
    for instance_id, (x_mm, y_mm) in proposed_xy.items():
        found = by_id.get(instance_id)
        if found is None:
            raise ValidationError("error.planner.part")
        instance, spec = found
        proposed.append((replace(instance, x_mm=x_mm, y_mm=y_mm), spec))
    selected = set(proposed_xy)
    stationary = tuple(pair for pair in topology if pair[0].id not in selected)
    poses = snap_selection(
        tuple(proposed),
        stationary,
        anchor_id=anchor_id,
        snap_mm=plan.snap_mm,
        grid_mm=plan.grid_mm if plan.grid_enabled else None,
        source_blockers=topology,
        target_blockers=topology,
    )
    updated = tuple(
        replace(
            instance,
            x_mm=poses[instance.id].x_mm,
            y_mm=poses[instance.id].y_mm,
            rotation_z_deg=poses[instance.id].rotation_z_deg,
        )
        if instance.id in poses
        else instance
        for instance in plan.instances
    )
    return _replace(plan, instances=updated)


def _placed(
    plan: TrackPlan, catalog: dict[int, PartSpec]
) -> tuple[tuple[PartInstance, PartSpec], ...]:
    placed: list[tuple[PartInstance, PartSpec]] = []
    for instance in plan.instances:
        if instance.host_id is not None:
            continue
        spec = catalog.get(instance.part_id)
        if spec is not None:
            placed.append((instance, spec))
    return tuple(placed)


def _movable(plan: TrackPlan, instance_id: str) -> bool:
    for instance in plan.instances:
        if instance.id == instance_id:
            return instance.host_id is None
    return False


def _require_instance(plan: TrackPlan, instance_id: str) -> PartInstance:
    for instance in plan.instances:
        if instance.id == instance_id:
            return instance
    raise ValidationError("error.planner.piece")


def _update_instance(plan: TrackPlan, instance_id: str, build: Any) -> TrackPlan:
    if not any(instance.id == instance_id for instance in plan.instances):
        raise ValidationError("error.planner.piece")
    instances = tuple(
        build(instance) if instance.id == instance_id else instance for instance in plan.instances
    )
    return _replace(plan, instances=instances)


def _instance_document(instance: PartInstance) -> dict[str, Any]:
    document = {
        "id": instance.id,
        "part_id": instance.part_id,
        "x": instance.x_mm,
        "y": instance.y_mm,
        "z": instance.z_mm,
        "rotation_x": instance.rotation_x_deg,
        "rotation_y": instance.rotation_y_deg,
        "rotation_z": instance.rotation_z_deg,
        "start_straight": instance.start_straight,
        "group_id": instance.group_id,
    }
    if instance.host_id is not None:
        document["host_id"] = instance.host_id
        document["attachment_slot"] = instance.attachment_slot
    return document


def _parse_instance(payload: object) -> PartInstance:
    if not isinstance(payload, dict):
        raise ValidationError("error.planner.invalid")
    part_id = payload.get("part_id")
    if isinstance(part_id, bool) or not isinstance(part_id, int) or part_id < 1:
        raise ValidationError("error.planner.invalid")
    group_id = payload.get("group_id")
    return PartInstance(
        id=_identity(payload.get("id")),
        part_id=part_id,
        x_mm=_millimetre(payload.get("x")),
        y_mm=_millimetre(payload.get("y")),
        z_mm=_millimetre(payload.get("z", 0)),
        rotation_x_deg=_angle(payload.get("rotation_x", 0)),
        rotation_y_deg=_angle(payload.get("rotation_y", 0)),
        rotation_z_deg=_angle(payload.get("rotation_z", 0)),
        start_straight=_optional_flag(payload.get("start_straight", False)),
        group_id=None if group_id is None else _identity(group_id),
        host_id=_optional_identity(payload.get("host_id")),
        attachment_slot=_optional_identity(payload.get("attachment_slot")),
    )


def _check_instance(instance: PartInstance) -> None:
    if not isinstance(instance, PartInstance):
        raise ValidationError("error.planner.part")
    _identity(instance.id)
    if isinstance(instance.part_id, bool) or not isinstance(instance.part_id, int):
        raise ValidationError("error.planner.part")
    if instance.part_id < 1:
        raise ValidationError("error.planner.part")
    for value in (instance.x_mm, instance.y_mm, instance.z_mm):
        _millimetre(value)
        if abs(value) > _POSITION_LIMIT_MM:
            raise ValidationError("error.planner.position")
    for value in (instance.rotation_x_deg, instance.rotation_y_deg, instance.rotation_z_deg):
        _angle(value)
    if not isinstance(instance.start_straight, bool):
        raise ValidationError("error.planner.part")
    if instance.group_id is not None:
        _identity(instance.group_id)
    host_id = instance.host_id
    slot = instance.attachment_slot
    if (host_id is None) != (slot is None):
        raise ValidationError("error.planner.part")
    if host_id is not None:
        _identity(host_id)
        if host_id == instance.id or slot not in ATTACHMENT_SLOTS:
            raise ValidationError("error.planner.part")


def _millimetre(value: object) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValidationError("error.planner.invalid")
    number = float(value)
    if number != number or number in (float("inf"), float("-inf")):
        raise ValidationError("error.planner.invalid")
    return number


def _angle(value: object) -> float:
    return _millimetre(value) % 360.0


def _flag(value: object) -> bool:
    if not isinstance(value, bool):
        raise ValidationError("error.planner.invalid")
    return value


def _optional_flag(value: object) -> bool:
    if value is None:
        return False
    return _flag(value)


def _parse_piece(payload: object) -> Piece:
    if not isinstance(payload, dict):
        raise ValidationError("error.planner.invalid")
    return _checked(
        Piece(
            id=_identity(payload.get("id")),
            piece_type=_text(payload.get("type")),
            x=_cell(payload.get("x")),
            y=_cell(payload.get("y")),
            rotation=_cell(payload.get("rotation")),
        )
    )


def _parse_marker(payload: object) -> Marker:
    if not isinstance(payload, dict):
        raise ValidationError("error.planner.invalid")
    piece_id = payload.get("piece_id")
    lane = payload.get("lane")
    return Marker(
        id=_identity(payload.get("id")),
        kind=_text(payload.get("kind")),
        x=_cell(payload.get("x")),
        y=_cell(payload.get("y")),
        piece_id=None if piece_id is None else _identity(piece_id),
        lane=None if lane is None else _cell(lane),
    )


def _validate(plan: TrackPlan) -> TrackPlan:
    if plan.version != PLAN_VERSION or plan.direction not in DIRECTIONS:
        raise ValidationError("error.planner.invalid")
    seen: set[str] = set()
    for piece in plan.pieces:
        if piece.id in seen:
            raise ValidationError("error.planner.invalid")
        seen.add(piece.id)
        _check_piece(piece)
    piece_ids = {piece.id for piece in plan.pieces}
    instance_ids: set[str] = set()
    for instance in plan.instances:
        if instance.id in instance_ids or instance.id in seen:
            raise ValidationError("error.planner.invalid")
        instance_ids.add(instance.id)
        _check_instance(instance)
    if not isinstance(plan.grid_enabled, bool) or plan.grid_mm <= 0 or plan.snap_mm < 0:
        raise ValidationError("error.planner.invalid")
    instances = _dissolve_small_groups(follow_hosts(plan.instances))
    if sum(instance.start_straight for instance in instances) > 1:
        raise ValidationError("error.planner.invalid")
    marker_ids: set[str] = set()
    starts = 0
    for marker in plan.markers:
        if marker.id in marker_ids or marker.kind not in MARKER_KINDS:
            raise ValidationError("error.planner.invalid")
        marker_ids.add(marker.id)
        _check_cell(marker.x, marker.y)
        if marker.piece_id is not None and marker.piece_id not in piece_ids:
            raise ValidationError("error.planner.invalid")
        if marker.kind == START_FINISH:
            starts += 1
            if marker.lane is not None:
                raise ValidationError("error.planner.invalid")
        elif marker.lane is not None and marker.lane < 1:
            raise ValidationError("error.planner.lane")
    if starts > 1:
        raise ValidationError("error.planner.invalid")
    if instances == plan.instances:
        return plan
    return TrackPlan(
        track_id=plan.track_id,
        version=plan.version,
        direction=plan.direction,
        pieces=plan.pieces,
        markers=plan.markers,
        grid_enabled=plan.grid_enabled,
        grid_mm=plan.grid_mm,
        snap_mm=plan.snap_mm,
        instances=instances,
    )


def _dissolve_small_groups(instances: tuple[PartInstance, ...]) -> tuple[PartInstance, ...]:
    counts: dict[str, int] = {}
    for instance in instances:
        if instance.group_id is not None:
            counts[instance.group_id] = counts.get(instance.group_id, 0) + 1
    if all(count >= 2 for count in counts.values()):
        return instances
    return tuple(
        replace(instance, group_id=None)
        if instance.group_id is not None and counts[instance.group_id] < 2
        else instance
        for instance in instances
    )


def _check_piece(piece: Piece) -> None:
    if piece.piece_type not in PIECE_TYPES:
        raise ValidationError("error.planner.piece")
    if piece.piece_type == CURVE_90:
        if piece.rotation not in {0, 90, 180, 270}:
            raise ValidationError("error.planner.piece")
    elif piece.rotation != 0:
        raise ValidationError("error.planner.piece")
    width, height = span(piece.piece_type)
    if piece.x < 0 or piece.y < 0 or piece.x + width > GRID_LIMIT or piece.y + height > GRID_LIMIT:
        raise ValidationError("error.planner.position")


def _checked(piece: Piece) -> Piece:
    _check_piece(piece)
    return piece


def _check_cell(x: int, y: int) -> None:
    numbers = not isinstance(x, bool) and not isinstance(y, bool)
    if not numbers or not isinstance(x, int) or not isinstance(y, int):
        raise ValidationError("error.planner.position")
    if x < 0 or y < 0 or x >= GRID_LIMIT or y >= GRID_LIMIT:
        raise ValidationError("error.planner.position")


def _cell(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValidationError("error.planner.invalid")
    return value


def _optional_identity(value: object) -> str | None:
    if value is None:
        return None
    return _identity(value)


def _identity(value: object) -> str:
    if not isinstance(value, str):
        raise ValidationError("error.planner.invalid")
    text = value.strip()
    if not text or len(text) > _ID_LIMIT:
        raise ValidationError("error.planner.invalid")
    return text


def _text(value: object) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValidationError("error.planner.invalid")
    return value.strip()


def _replace(
    plan: TrackPlan,
    *,
    direction: str | None = None,
    pieces: tuple[Piece, ...] | None = None,
    markers: tuple[Marker, ...] | None = None,
    grid_enabled: bool | None = None,
    grid_mm: float | None = None,
    snap_mm: float | None = None,
    instances: tuple[PartInstance, ...] | None = None,
) -> TrackPlan:
    return _validate(
        TrackPlan(
            track_id=plan.track_id,
            version=plan.version,
            direction=plan.direction if direction is None else direction,
            pieces=plan.pieces if pieces is None else pieces,
            markers=plan.markers if markers is None else markers,
            grid_enabled=plan.grid_enabled if grid_enabled is None else grid_enabled,
            grid_mm=plan.grid_mm if grid_mm is None else grid_mm,
            snap_mm=plan.snap_mm if snap_mm is None else snap_mm,
            instances=plan.instances if instances is None else instances,
        )
    )


def _overlaps(
    x: int, y: int, width: int, height: int, other_x: int, other_y: int, other_w: int, other_h: int
) -> bool:
    separated_x = x >= other_x + other_w or other_x >= x + width
    separated_y = y >= other_y + other_h or other_y >= y + height
    return not separated_x and not separated_y
