"""Parts that ride on one rail instead of joining the track.

An accessory is its own plan instance. Its pose is the parent's pose, and its
outline is drawn in that same local frame, offset to one side. It has no track
joint. A retail box that contains several strips is not expanded here: stock
still counts one definition, and the user records how many strips they own.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import replace

from slot_racing.modules.track_planner.parts import (
    CURVE,
    SPECIAL,
    STRAIGHT,
    AttachmentProfile,
    PartInstance,
    PartSpec,
    arc_outline,
    track_width,
)

HOST_STRAIGHT = "straight"
HOST_CURVE_FLAT = "curve_flat"
HOST_CURVE_BANKED = "curve_banked"
HOST_SHAPES = frozenset({HOST_STRAIGHT, HOST_CURVE_FLAT, HOST_CURVE_BANKED})

SLOT_LEFT = "left"
SLOT_RIGHT = "right"
SLOT_INNER = "inner"
SLOT_OUTER = "outer"
STRAIGHT_SLOTS = (SLOT_LEFT, SLOT_RIGHT)
CURVE_SLOTS = (SLOT_INNER, SLOT_OUTER)
ATTACHMENT_SLOTS = frozenset({*STRAIGHT_SLOTS, *CURVE_SLOTS})

# Visual width of a reference border strip. It is not a second roadway.
STRIP_WIDTH_MM = 40.0
_MEASURE_MM = 0.5


def host_shape(spec: PartSpec) -> str | None:
    """Which attachment family a rail belongs to. An accessory is not a host."""
    if spec.attachment is not None:
        return None
    if spec.category == STRAIGHT and spec.length_mm:
        return HOST_STRAIGHT
    if spec.category == CURVE and spec.radius_mm and spec.angle_deg:
        return HOST_CURVE_FLAT
    if spec.category == SPECIAL and spec.length_mm is None and spec.radius_mm and spec.angle_deg:
        return HOST_CURVE_BANKED
    return None


def slots_for_host(spec: PartSpec) -> tuple[str, ...]:
    shape = host_shape(spec)
    if shape == HOST_STRAIGHT:
        return STRAIGHT_SLOTS
    if shape in {HOST_CURVE_FLAT, HOST_CURVE_BANKED}:
        return CURVE_SLOTS
    return ()


def attachment_fits(accessory: PartSpec, host: PartSpec, slot: str) -> bool:
    """True when this definition may occupy ``slot`` on this exact host shape."""
    profile = accessory.attachment
    if profile is None or slot not in profile.slots or slot not in slots_for_host(host):
        return False
    if profile.host_lanes is not None and host.lane_count != profile.host_lanes:
        return False
    if host_shape(host) != profile.host_shape:
        return False
    if profile.host_shape == HOST_STRAIGHT:
        return _close(host.length_mm, profile.host_length_mm)
    return _close(host.radius_mm, profile.host_radius_mm) and _close(
        host.angle_deg, profile.host_angle_deg
    )


def slot_taken(instances: Sequence[PartInstance], host_id: str, slot: str) -> bool:
    return any(
        instance.host_id == host_id and instance.attachment_slot == slot for instance in instances
    )


def placed_outline(spec: PartSpec, slot: str | None) -> tuple[tuple[float, float], ...]:
    """Local outline. A slotted strip sits beside the host; the library card stays centred."""
    profile = spec.attachment
    if profile is None or slot not in profile.slots:
        return spec.outline
    return _strip_outline(profile, slot)


def follow_hosts(instances: tuple[PartInstance, ...]) -> tuple[PartInstance, ...]:
    """Copy each parent's pose onto its accessories and drop an accessory with no parent.

    The copy is absolute, so repeated moves do not accumulate a second offset.
    """
    by_id = {instance.id: instance for instance in instances}
    changed = False
    kept: list[PartInstance] = []
    for instance in instances:
        if instance.host_id is None:
            kept.append(instance)
            continue
        host = by_id.get(instance.host_id)
        if host is None or host.host_id is not None or host.id == instance.id:
            changed = True
            continue
        synced = _pose_of(instance, host)
        if synced != instance:
            changed = True
        kept.append(synced)
    if not changed:
        return instances
    return tuple(kept)


def _pose_of(instance: PartInstance, host: PartInstance) -> PartInstance:
    return replace(
        instance,
        x_mm=host.x_mm,
        y_mm=host.y_mm,
        z_mm=host.z_mm,
        rotation_x_deg=host.rotation_x_deg,
        rotation_y_deg=host.rotation_y_deg,
        rotation_z_deg=host.rotation_z_deg,
        start_straight=False,
        group_id=None,
    )


def _strip_outline(profile: AttachmentProfile, slot: str) -> tuple[tuple[float, float], ...]:
    if profile.host_shape == HOST_STRAIGHT:
        length = profile.host_length_mm or 0.0
        return _shifted_rectangle(length, STRIP_WIDTH_MM, _straight_offset(slot))
    radius = profile.host_radius_mm or 0.0
    angle = profile.host_angle_deg or 0.0
    return arc_outline(_curve_radius(radius, slot), angle, STRIP_WIDTH_MM)


def _straight_offset(slot: str) -> float:
    outside = track_width(2) / 2.0 + STRIP_WIDTH_MM / 2.0
    if slot == SLOT_LEFT:
        return -outside
    return outside


def _curve_radius(road_radius: float, slot: str) -> float:
    half = track_width(2) / 2.0
    if slot == SLOT_OUTER:
        return road_radius + half + STRIP_WIDTH_MM / 2.0
    return max(road_radius - half - STRIP_WIDTH_MM / 2.0, STRIP_WIDTH_MM / 2.0)


def _shifted_rectangle(
    length_mm: float, width_mm: float, y_mm: float
) -> tuple[tuple[float, float], ...]:
    half_length = length_mm / 2.0
    half_width = width_mm / 2.0
    return (
        (-half_length, y_mm - half_width),
        (half_length, y_mm - half_width),
        (half_length, y_mm + half_width),
        (-half_length, y_mm + half_width),
    )


def _close(left: float | None, right: float | None) -> bool:
    if left is None or right is None:
        return False
    return abs(left - right) <= _MEASURE_MM


def radii(points: Sequence[tuple[float, float]]) -> tuple[float, ...]:
    """Distance of each outline point from the local origin. Tests use this."""
    return tuple(math.hypot(x_mm, y_mm) for x_mm, y_mm in points)
