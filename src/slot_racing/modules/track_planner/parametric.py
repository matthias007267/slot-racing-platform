"""Parametric straights, curves and borders on the shared path model.

A catalogue straight or right-hand curve stays a compact measure: empty slot
paths and the existing connector helpers. A different pitch, individual lane
positions or a left-hand bend stores the grooves. Switches, crossings, pit
entries and Bézier bodies are left untouched.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, replace
from typing import Literal

from slot_racing.core.errors import ValidationError
from slot_racing.modules.track_planner.attachment import (
    HOST_CURVE_BANKED,
    HOST_CURVE_FLAT,
    HOST_CUSTOM,
    HOST_STRAIGHT,
)
from slot_racing.modules.track_planner.parts import (
    BORDER,
    CURVE,
    LANE_PITCH_MM,
    PATH_CONTOUR,
    SPECIAL,
    STRAIGHT,
    AttachmentProfile,
    BorderEdge,
    ConnectorSpec,
    PartSpec,
    SlotPath,
    SlotSpan,
    arc_outline,
    curve_connectors,
    identity_key,
    lanes_for,
    normalize_deg,
    rectangle,
    require_scale,
    straight_connectors,
    track_width,
)

DIRECTION_RIGHT: Literal["right"] = "right"
DIRECTION_LEFT: Literal["left"] = "left"
SIDE_BOTH = "both"
BODY_STRAIGHT: Literal["straight"] = "straight"
BODY_CURVE: Literal["curve"] = "curve"
BODY_BORDER: Literal["border"] = "border"
_Body = Literal["straight", "curve", "border"]
_SIMPLE = frozenset({STRAIGHT, CURVE, SPECIAL, BORDER})


@dataclass(frozen=True, slots=True)
class PartDraft:
    """Measures the parametric editor is allowed to rebuild."""

    category: str
    body: _Body
    length_mm: float | None
    radius_mm: float | None
    angle_deg: float | None
    direction: str
    lane_count: int
    pitch_mm: float
    width_mm: float | None
    offsets_mm: tuple[float, ...] | None
    border_side: str
    host_lanes: int
    # True when the stored part already has grooves, so a save writes them again.
    explicit_grooves: bool = False
    host_shape: str = ""


def editable_geometry(spec: PartSpec) -> bool:
    """False when saving the simple measures would drop curves the editor cannot show."""
    return draft_from_spec(spec) is not None


def draft_from_spec(spec: PartSpec) -> PartDraft | None:
    """Read a simple straight, curve or border. ``None`` means the body stays as stored."""
    if spec.attachment is not None:
        return _draft_border(spec)
    if _simple_straight(spec):
        offsets = _stored_offsets(spec)
        return PartDraft(
            category=spec.category,
            body=BODY_STRAIGHT,
            length_mm=spec.length_mm,
            radius_mm=None,
            angle_deg=0.0,
            direction=DIRECTION_RIGHT,
            lane_count=spec.lane_count,
            pitch_mm=_pitch_of(offsets, spec.lane_count),
            width_mm=spec.width_mm,
            offsets_mm=None if _even_pitch(offsets, spec.lane_count) else offsets,
            border_side=SIDE_BOTH,
            host_lanes=spec.lane_count,
            explicit_grooves=bool(spec.slot_paths),
        )
    curve = _simple_curve(spec)
    if curve is None:
        return None
    direction, offsets = curve
    return PartDraft(
        category=spec.category,
        body=BODY_CURVE,
        length_mm=None,
        radius_mm=spec.radius_mm,
        angle_deg=abs(float(spec.angle_deg or 0.0)),
        direction=direction,
        lane_count=spec.lane_count,
        pitch_mm=_pitch_of(offsets, spec.lane_count),
        width_mm=spec.width_mm,
        offsets_mm=None if _even_pitch(offsets, spec.lane_count) else offsets,
        border_side="outer",
        host_lanes=spec.lane_count,
        explicit_grooves=bool(spec.slot_paths),
    )


def spec_from_draft(
    draft: PartDraft,
    *,
    article_number: str,
    scale: str,
    name: str,
    manufacturer: str,
    description: str,
    height_mm: float | None,
    validate_identity: bool = True,
) -> PartSpec:
    """Build one part. Identity checks can be skipped for a live preview."""
    title, article, maker, text = _identity_texts(
        name,
        article_number,
        manufacturer,
        description,
        validate_identity=validate_identity,
    )
    if validate_identity:
        stored_scale = require_scale(scale)
    elif scale in {"1:24", "1:32", "1:43"}:
        stored_scale = scale
    else:
        stored_scale = "1:24"
    if draft.category not in _SIMPLE:
        raise ValidationError("error.planner.part")
    lanes_for(draft.lane_count)
    if draft.body == BODY_BORDER:
        spec = _border(draft)
    elif draft.body == BODY_CURVE:
        spec = _curve(draft)
    else:
        spec = _straight(draft)
    return PartSpec(
        article_number=article,
        scale=stored_scale,
        name=title,
        category=spec.category,
        length_mm=spec.length_mm,
        width_mm=spec.width_mm,
        height_mm=height_mm,
        radius_mm=spec.radius_mm,
        angle_deg=spec.angle_deg,
        lane_count=spec.lane_count,
        connectors=spec.connectors,
        outline=spec.outline,
        slot_paths=spec.slot_paths,
        attachment=spec.attachment,
        contours=spec.contours,
        border_edges=spec.border_edges,
        manufacturer=maker,
        description=text,
    )


def with_metadata(
    spec: PartSpec,
    *,
    article_number: str,
    scale: str,
    name: str,
    manufacturer: str,
    description: str,
) -> PartSpec:
    """New designation and text. Every curve, joint and edge stays on ``spec``."""
    title, article, maker, text = _identity_texts(
        name, article_number, manufacturer, description, validate_identity=True
    )
    return replace(
        spec,
        article_number=article,
        scale=require_scale(scale),
        name=title,
        manufacturer=maker,
        description=text,
    )


def _identity_texts(
    name: str,
    article_number: str,
    manufacturer: str,
    description: str,
    *,
    validate_identity: bool,
) -> tuple[str, str, str, str]:
    title = name.strip() if isinstance(name, str) else ""
    article = article_number.strip() if isinstance(article_number, str) else ""
    maker = manufacturer.strip() if isinstance(manufacturer, str) else ""
    text = description.strip() if isinstance(description, str) else ""
    if not validate_identity:
        return (title or "Vorschau", article or "VORSCHAU", maker, text)
    if not title or len(title) > 120:
        raise ValidationError("error.planner.editor.name")
    if not article or len(article) > 40:
        raise ValidationError("error.planner.editor.article")
    if len(maker) > 80:
        raise ValidationError("error.planner.editor.manufacturer")
    if len(text) > 500:
        raise ValidationError("error.planner.editor.description")
    identity_key(title, article)
    return title, article, maker, text


def _straight(draft: PartDraft) -> PartSpec:
    length = _positive(draft.length_mm, "error.planner.editor.length")
    width = _width(draft.width_mm, draft.lane_count)
    offsets = _offsets(draft)
    _finite_offsets(offsets)
    standard = _is_standard(draft, offsets) and not draft.explicit_grooves
    category = SPECIAL if draft.category == SPECIAL else STRAIGHT
    edges = () if standard and category == STRAIGHT else _straight_edges(length, width)
    return _shell(
        category=category,
        length_mm=length,
        width_mm=width,
        radius_mm=None,
        angle_deg=0.0,
        lane_count=draft.lane_count,
        connectors=straight_connectors(length, draft.lane_count),
        outline=rectangle(length, width),
        slot_paths=() if standard else _straight_paths(length, offsets),
        border_edges=() if standard else edges,
    )


def _curve(draft: PartDraft) -> PartSpec:
    radius = _positive(draft.radius_mm, "error.planner.editor.radius")
    angle = _positive(draft.angle_deg, "error.planner.editor.angle")
    if angle >= 360.0:
        raise ValidationError("error.planner.editor.angle")
    width = _width(draft.width_mm, draft.lane_count)
    if width >= radius * 2.0:
        raise ValidationError("error.planner.editor.width")
    offsets = _offsets(draft)
    _finite_offsets(offsets)
    left = draft.direction == DIRECTION_LEFT
    for index in range(draft.lane_count):
        if _groove_radius(radius, offsets[index], left) <= 0.0:
            raise ValidationError("error.planner.editor.pitch")
    standard = _is_standard(draft, offsets) and not left and not draft.explicit_grooves
    category = SPECIAL if draft.category == SPECIAL else CURVE
    connectors = curve_connectors(radius, angle, draft.lane_count)
    outline = arc_outline(radius, angle, width)
    if left:
        connectors = tuple(_mirror_connector(item) for item in connectors)
        outline = tuple((x_mm, -y_mm) for x_mm, y_mm in outline)
    paths: tuple[SlotPath, ...] = ()
    contours: tuple[SlotPath, ...] = ()
    edges: tuple[BorderEdge, ...] = ()
    if not standard:
        paths = _curve_paths(radius, angle, offsets, left)
        contours = _curve_contours(radius, angle, width, left)
        edges = _curve_edges(radius, angle, width, left)
    return _shell(
        category=category,
        length_mm=None,
        width_mm=width,
        radius_mm=radius,
        angle_deg=angle,
        lane_count=draft.lane_count,
        connectors=connectors,
        outline=outline,
        slot_paths=paths,
        contours=contours,
        border_edges=edges,
    )


def _border(draft: PartDraft) -> PartSpec:
    width = _positive(draft.width_mm, "error.planner.editor.width")
    lanes_for(draft.host_lanes)
    if border_host_is_curve(draft):
        return _curve_border(draft, width)
    length = _positive(draft.length_mm, "error.planner.editor.length")
    slots = _straight_slots(draft.border_side)
    return _shell(
        category=BORDER,
        length_mm=length,
        width_mm=width,
        radius_mm=None,
        angle_deg=0.0,
        lane_count=draft.host_lanes,
        connectors=(),
        outline=rectangle(length, width),
        attachment=AttachmentProfile(
            HOST_STRAIGHT,
            slots,
            host_length_mm=length,
            host_lanes=draft.host_lanes,
        ),
    )


def _curve_border(draft: PartDraft, width: float) -> PartSpec:
    radius = _positive(draft.radius_mm, "error.planner.editor.radius")
    angle = _positive(draft.angle_deg, "error.planner.editor.angle")
    if angle >= 360.0:
        raise ValidationError("error.planner.editor.angle")
    side = draft.border_side if draft.border_side in {"inner", "outer"} else "outer"
    left = draft.direction == DIRECTION_LEFT
    road = track_width(draft.host_lanes)
    if left:
        span = _edge_span(radius, road, angle, left, side)
        half = road / 2.0
        edge_radius = radius + half if side == "outer" else max(radius - half, width / 2.0)
        if side == "outer":
            center = edge_radius + width / 2.0
        else:
            center = max(edge_radius - width / 2.0, 0.1)
        outline = tuple((x_mm, -y_mm) for x_mm, y_mm in arc_outline(center, angle, width))
        profile = AttachmentProfile(HOST_CUSTOM, (side,), host_lanes=draft.host_lanes, edge=(span,))
    else:
        outline = arc_outline(_strip_center(radius, road, width, side), angle, width)
        host = (
            draft.host_shape
            if draft.host_shape in {HOST_CURVE_FLAT, HOST_CURVE_BANKED}
            else HOST_CURVE_FLAT
        )
        profile = AttachmentProfile(
            host,
            (side,),
            host_radius_mm=radius,
            host_angle_deg=angle,
            host_lanes=draft.host_lanes,
        )
    return _shell(
        category=BORDER,
        length_mm=None,
        width_mm=width,
        radius_mm=radius,
        angle_deg=angle,
        lane_count=draft.host_lanes,
        connectors=(),
        outline=outline,
        attachment=profile,
    )


def _shell(
    *,
    category: str,
    length_mm: float | None,
    width_mm: float,
    radius_mm: float | None,
    angle_deg: float,
    lane_count: int,
    connectors: tuple[ConnectorSpec, ...],
    outline: tuple[tuple[float, float], ...],
    slot_paths: tuple[SlotPath, ...] = (),
    attachment: AttachmentProfile | None = None,
    contours: tuple[SlotPath, ...] = (),
    border_edges: tuple[BorderEdge, ...] = (),
) -> PartSpec:
    return PartSpec(
        article_number="",
        scale="1:24",
        name="",
        category=category,
        length_mm=length_mm,
        width_mm=width_mm,
        height_mm=None,
        radius_mm=radius_mm,
        angle_deg=angle_deg,
        lane_count=lane_count,
        connectors=connectors,
        outline=outline,
        slot_paths=slot_paths,
        attachment=attachment,
        contours=contours,
        border_edges=border_edges,
    )


def _simple_straight(spec: PartSpec) -> bool:
    if spec.attachment is not None or spec.contours or spec.category not in {STRAIGHT, SPECIAL}:
        return False
    length = spec.length_mm
    if length is None or length <= 0.0 or spec.angle_deg not in (None, 0.0):
        return False
    if spec.radius_mm not in (None, 0.0):
        return False
    if spec.connectors != straight_connectors(length, spec.lane_count):
        return False
    if not _only_lines(spec.slot_paths) or not _edges_are_straight(spec):
        return False
    return _parallel(spec, length)


def _simple_curve(spec: PartSpec) -> tuple[str, tuple[float, ...]] | None:
    if spec.attachment is not None or spec.category not in {CURVE, SPECIAL}:
        return None
    if spec.length_mm is not None or spec.radius_mm is None or spec.angle_deg is None:
        return None
    if spec.radius_mm <= 0.0 or spec.angle_deg == 0.0:
        return None
    angle = abs(spec.angle_deg)
    if spec.slot_paths:
        return _stored_curve(spec, angle)
    if spec.contours or spec.border_edges:
        return None
    if spec.connectors != curve_connectors(spec.radius_mm, angle, spec.lane_count):
        return None
    return DIRECTION_RIGHT, _centered(spec.lane_count, LANE_PITCH_MM)


def _stored_curve(spec: PartSpec, angle: float) -> tuple[str, tuple[float, ...]] | None:
    sweeps = [span.sweep_deg for path in spec.slot_paths for span in path.spans]
    if not sweeps or any(path.kind != "slot" or len(path.spans) != 1 for path in spec.slot_paths):
        return None
    if any(span.kind != "arc" for path in spec.slot_paths for span in path.spans):
        return None
    left = sweeps[0] < 0.0
    if any((item < 0.0) != left or abs(abs(item) - angle) > 1e-6 for item in sweeps):
        return None
    radius = float(spec.radius_mm or 0.0)
    mirrored = tuple(
        _mirror_connector(item) for item in curve_connectors(radius, angle, spec.lane_count)
    )
    expected = mirrored if left else curve_connectors(radius, angle, spec.lane_count)
    if spec.connectors != expected:
        return None
    offsets = _radii_as_offsets(spec, left)
    if offsets is None:
        return None
    return (DIRECTION_LEFT if left else DIRECTION_RIGHT), offsets


def _draft_border(spec: PartSpec) -> PartDraft | None:
    profile = spec.attachment
    if profile is None or spec.connectors or spec.slot_paths or spec.contours:
        return None
    if profile.host_shape == HOST_STRAIGHT and profile.host_length_mm and not profile.edge:
        return _border_draft(
            spec,
            profile.host_length_mm,
            None,
            0.0,
            DIRECTION_RIGHT,
            _side_name(profile.slots, straight=True),
        )
    if profile.host_shape in {HOST_CURVE_FLAT, HOST_CURVE_BANKED} and not profile.edge:
        if profile.host_radius_mm is None or profile.host_angle_deg is None:
            return None
        return _border_draft(
            spec,
            None,
            profile.host_radius_mm,
            profile.host_angle_deg,
            DIRECTION_RIGHT,
            _side_name(profile.slots, straight=False),
        )
    if profile.host_shape == HOST_CUSTOM and len(profile.edge) == 1:
        span = profile.edge[0]
        if span.kind != "arc" or span.sweep_deg >= 0.0 or span.radius_mm <= 0.0:
            return None
        return _border_draft(
            spec,
            None,
            spec.radius_mm,
            abs(span.sweep_deg),
            DIRECTION_LEFT,
            profile.slots[0] if profile.slots else "outer",
        )
    return None


def _border_draft(
    spec: PartSpec,
    length: float | None,
    radius: float | None,
    angle: float,
    direction: str,
    side: str,
) -> PartDraft:
    profile = spec.attachment
    assert profile is not None
    return PartDraft(
        category=BORDER,
        body=BODY_BORDER,
        length_mm=length,
        radius_mm=radius,
        angle_deg=angle,
        direction=direction,
        lane_count=profile.host_lanes or spec.lane_count,
        pitch_mm=LANE_PITCH_MM,
        width_mm=spec.width_mm,
        offsets_mm=None,
        border_side=side,
        host_lanes=profile.host_lanes or spec.lane_count,
        host_shape=profile.host_shape,
    )


def _straight_paths(length: float, offsets: tuple[float, ...]) -> tuple[SlotPath, ...]:
    half = length / 2.0
    return tuple(
        SlotPath((SlotSpan.line(-half, y_mm, half, y_mm),), lane=index + 1)
        for index, y_mm in enumerate(offsets)
    )


def _straight_edges(length: float, width: float) -> tuple[BorderEdge, ...]:
    half = length / 2.0
    side = width / 2.0
    return (
        BorderEdge("left", (SlotSpan.line(-half, -side, half, -side),)),
        BorderEdge("right", (SlotSpan.line(-half, side, half, side),)),
    )


def _curve_paths(
    radius: float, angle: float, offsets: tuple[float, ...], left: bool
) -> tuple[SlotPath, ...]:
    return tuple(
        SlotPath(
            (
                SlotSpan.arc(
                    0.0, 0.0, _groove_radius(radius, offset, left), *_arc_angles(angle, left)
                ),
            ),
            lane=index + 1,
        )
        for index, offset in enumerate(offsets)
    )


def _curve_contours(radius: float, angle: float, width: float, left: bool) -> tuple[SlotPath, ...]:
    outer = radius + width / 2.0
    inner = max(radius - width / 2.0, 0.1)
    return (
        SlotPath((SlotSpan.arc(0.0, 0.0, outer, *_arc_angles(angle, left)),), PATH_CONTOUR),
        SlotPath((SlotSpan.arc(0.0, 0.0, inner, *_arc_angles(angle, left)),), PATH_CONTOUR),
    )


def _curve_edges(radius: float, angle: float, width: float, left: bool) -> tuple[BorderEdge, ...]:
    return (
        BorderEdge("outer", (_edge_span(radius, width, angle, left, "outer"),)),
        BorderEdge("inner", (_edge_span(radius, width, angle, left, "inner"),)),
    )


def _edge_span(radius: float, width: float, angle: float, left: bool, side: str) -> SlotSpan:
    half = width / 2.0
    edge = radius + half if side == "outer" else max(radius - half, 0.1)
    return SlotSpan.arc(0.0, 0.0, edge, *_arc_angles(angle, left))


def _arc_angles(angle: float, left: bool) -> tuple[float, float]:
    if left:
        return (angle / 2.0, -angle)
    return (-angle / 2.0, angle)


def _groove_radius(center: float, offset: float, left: bool) -> float:
    return center + offset if left else center - offset


def _strip_center(radius: float, road_width: float, strip_width: float, side: str) -> float:
    half = road_width / 2.0
    if side == "outer":
        return radius + half + strip_width / 2.0
    return max(radius - half - strip_width / 2.0, strip_width / 2.0)


def _mirror_connector(connector: ConnectorSpec) -> ConnectorSpec:
    return ConnectorSpec(
        connector.name,
        connector.x_mm,
        -connector.y_mm,
        connector.z_mm,
        normalize_deg(-connector.direction_deg),
        connector.kind,
        connector.lanes,
    )


def _offsets(draft: PartDraft) -> tuple[float, ...]:
    if draft.offsets_mm is not None:
        if len(draft.offsets_mm) != draft.lane_count:
            raise ValidationError("error.planner.editor.offsets")
        return draft.offsets_mm
    if draft.pitch_mm <= 0.0 or not math.isfinite(draft.pitch_mm):
        raise ValidationError("error.planner.editor.pitch")
    return _centered(draft.lane_count, draft.pitch_mm)


def _centered(count: int, pitch: float) -> tuple[float, ...]:
    return tuple((index - (count - 1) / 2.0) * pitch for index in range(count))


def _is_standard(draft: PartDraft, offsets: tuple[float, ...]) -> bool:
    return (
        draft.offsets_mm is None
        and _even_pitch(offsets, draft.lane_count)
        and (abs(_pitch_of(offsets, draft.lane_count) - LANE_PITCH_MM) <= 1e-6)
    )


def _even_pitch(offsets: tuple[float, ...], count: int) -> bool:
    if len(offsets) != count or count == 0:
        return False
    if count == 1:
        return abs(offsets[0]) <= 1e-6
    gaps = [offsets[index + 1] - offsets[index] for index in range(count - 1)]
    return all(abs(gap - gaps[0]) <= 1e-6 for gap in gaps)


def _pitch_of(offsets: tuple[float, ...], count: int) -> float:
    if count <= 1 or len(offsets) < 2:
        return LANE_PITCH_MM
    return offsets[1] - offsets[0]


def _width(width_mm: float | None, lane_count: int) -> float:
    if width_mm is None:
        return track_width(lane_count)
    if width_mm <= 0.0 or not math.isfinite(width_mm):
        raise ValidationError("error.planner.editor.width")
    return width_mm


def _positive(value: float | None, key: str) -> float:
    if value is None or isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValidationError(key)
    if not math.isfinite(value) or value <= 0.0:
        raise ValidationError(key)
    return float(value)


def _finite_offsets(offsets: tuple[float, ...]) -> None:
    if any(isinstance(item, bool) or not math.isfinite(item) for item in offsets):
        raise ValidationError("error.planner.editor.offsets")


def _stored_offsets(spec: PartSpec) -> tuple[float, ...]:
    if not spec.slot_paths:
        return _centered(spec.lane_count, LANE_PITCH_MM)
    ordered = sorted((path.lane or 0, path.spans[0].y0) for path in spec.slot_paths if path.spans)
    return tuple(y_mm for _lane, y_mm in ordered)


def _radii_as_offsets(spec: PartSpec, left: bool) -> tuple[float, ...] | None:
    center = float(spec.radius_mm or 0.0)
    ordered = sorted(spec.slot_paths, key=lambda path: path.lane or 0)
    if len(ordered) != spec.lane_count:
        return None
    offsets: list[float] = []
    for path in ordered:
        span = path.spans[0]
        if span.kind != "arc":
            return None
        offsets.append(span.radius_mm - center if left else center - span.radius_mm)
    return tuple(offsets)


def _only_lines(paths: tuple[SlotPath, ...]) -> bool:
    return all(len(path.spans) == 1 and path.spans[0].kind == "line" for path in paths)


def _parallel(spec: PartSpec, length: float) -> bool:
    if not spec.slot_paths:
        return True
    half = length / 2.0
    lanes = [path.lane for path in spec.slot_paths]
    if sorted(lane for lane in lanes if lane is not None) != list(range(1, spec.lane_count + 1)):
        return False
    for path in spec.slot_paths:
        span = path.spans[0]
        if abs(span.y0 - span.y1) > 1e-6:
            return False
        if abs(span.x0 + half) > 1e-6 or abs(span.x1 - half) > 1e-6:
            return False
    return True


def _edges_are_straight(spec: PartSpec) -> bool:
    if not spec.border_edges:
        return True
    return all(len(edge.spans) == 1 and edge.spans[0].kind == "line" for edge in spec.border_edges)


def _straight_slots(side: str) -> tuple[str, ...]:
    if side == "left":
        return ("left",)
    if side == "right":
        return ("right",)
    if side == SIDE_BOTH:
        return ("left", "right")
    raise ValidationError("error.planner.editor.side")


def _side_name(slots: tuple[str, ...], *, straight: bool) -> str:
    if straight and set(slots) == {"left", "right"}:
        return SIDE_BOTH
    if slots:
        return slots[0]
    return SIDE_BOTH if straight else "outer"


def border_host_is_curve(draft: PartDraft) -> bool:
    return draft.body == BODY_BORDER and draft.radius_mm is not None and draft.length_mm is None
