"""Read the Carrera Evolution and Digital part files into library drafts.

The source UUID is the technical identity. The published article number stays
as printed. A piece key such as ``20509.2`` is a separate attribute. Shells
become placement templates and are not parts. Geometry is copied from the
file. Nothing is rounded onto the old 100 mm catalogue.
"""

from __future__ import annotations

import base64
import json
import math
import re
from collections.abc import Mapping
from dataclasses import dataclass
from functools import cache
from importlib.resources import files

from slot_racing.modules.track_planner.parts import (
    BORDER,
    BORDER_JOINT,
    CATALOG_SCALE,
    CURVE,
    LANE_CHANGE,
    PATH_CENTER,
    PATH_CONTOUR,
    PATH_DRAW,
    PATH_SLOT,
    PATH_START,
    PITLANE,
    SPECIAL,
    START_PIECE_KEY,
    STRAIGHT,
    SWITCH,
    TRACK,
    ConnectorSpec,
    PartSpec,
    SlotPath,
)
from slot_racing.modules.track_planner.svg_path import outline_points, parse_svg_path
from slot_racing.modules.track_planner.trace import trace

EVOLUTION_FILE = "carrera_evolution_20260921.planer.json"
DIGITAL_FILE = "carrera_digital_20260921.planer.json"
START_SOURCE = "888c36e0-3d78-482c-872c-7eee1259a90a"
HELPER_SOURCE = "54affb79-55c4-4b44-9212-6065b266d74f"
HELPER_PIECE_KEY = "20509.3"
_CURVES = frozenset({"20577", "20571", "20572", "20573", "20578"})
_SWITCHES = frozenset({"20517", "30343", "30345", "30347"})
_LANE_CHANGES = frozenset({"30362", "30363", "30364", "30365"})
_PITS = frozenset({"30341", "30356"})
_SPECIALS = frozenset({"30342", "30344", "30352", "30350/30351"})


@dataclass(frozen=True, slots=True)
class TemplateMemberDraft:
    source_id: str
    x_mm: float
    y_mm: float
    angle_deg: float


@dataclass(frozen=True, slots=True)
class TemplateDraft:
    source_id: str
    article_number: str
    name: str
    members: tuple[TemplateMemberDraft, ...]


@dataclass(frozen=True, slots=True)
class PackageDraft:
    article_number: str
    name: str
    contents: tuple[tuple[str, str, str], ...]
    """``(source id, name, official article)`` once each."""


@dataclass(frozen=True, slots=True)
class ImportNote:
    source_id: str
    article_number: str
    name: str
    detail: str


@dataclass(frozen=True, slots=True)
class LibraryDraft:
    parts: tuple[PartSpec, ...]
    templates: tuple[TemplateDraft, ...]
    packages: tuple[PackageDraft, ...]
    skipped: tuple[ImportNote, ...]
    notes: tuple[ImportNote, ...]


@cache
def load_carrera_library() -> LibraryDraft:
    """Both source files. A part that cannot be read is listed and left out.

    The files are bundled with the application, so the parsed draft is reused.
    """
    evolution = _part_table(_payload(EVOLUTION_FILE), EVOLUTION_FILE)
    digital = _part_table(_payload(DIGITAL_FILE), DIGITAL_FILE)
    raw = {**evolution, **digital}
    evolution_ids = set(evolution)
    digital_ids = set(digital)
    referenced = _referenced(raw)
    skipped: list[ImportNote] = []
    notes: list[ImportNote] = []
    shells: dict[str, dict[str, object]] = {}
    bodies: dict[str, dict[str, object]] = {}
    for source_id, part in raw.items():
        if _is_shell(part):
            shells[source_id] = part
            continue
        if not str(part.get("clip") or "").strip() and not part.get("slots"):
            skipped.append(
                ImportNote(source_id, str(part.get("partNumber") or ""), _name(part), "no geometry")
            )
            continue
        bodies[source_id] = part
    piece_keys = _piece_keys(bodies, shells)
    specs: list[PartSpec] = []
    for source_id, part in bodies.items():
        try:
            spec, drawing_notes = _part_spec(
                source_id,
                part,
                piece_key=piece_keys.get(source_id, ""),
                systems=_systems(source_id, evolution_ids, digital_ids, referenced),
            )
            specs.append(spec)
            notes.extend(
                ImportNote(source_id, spec.article_number, spec.name, detail)
                for detail in drawing_notes
            )
        except (ValueError, KeyError, TypeError) as error:
            skipped.append(
                ImportNote(
                    source_id,
                    str(part.get("partNumber") or ""),
                    _name(part),
                    str(error),
                )
            )
    known = {spec.source_id for spec in specs}
    imported = tuple(specs)
    templates: list[TemplateDraft] = []
    for source_id, part in shells.items():
        members, missing = _members(part, known)
        if missing or not members:
            skipped.append(
                ImportNote(source_id, str(part.get("partNumber") or ""), _name(part), "template")
            )
            continue
        templates.append(
            TemplateDraft(
                source_id=source_id,
                article_number=str(part.get("partNumber") or "").strip(),
                name=_name(part),
                members=members,
            )
        )
    notes.extend(_type_notes(imported))
    return LibraryDraft(
        parts=imported,
        templates=tuple(templates),
        packages=_packages(imported),
        skipped=tuple(skipped),
        notes=tuple(notes),
    )


def source_anchor(source_id: str) -> tuple[float, float, tuple[ConnectorSpec, ...]] | None:
    """Centre and joints of one catalogue body.

    ``None`` when that UUID is not a body in the bundled files. Catalogue reads
    use this in place of the stored joint rows, so an older database gains the
    edge joints without a rewrite of the part.
    """
    if not source_id:
        return None
    for spec in load_carrera_library().parts:
        if spec.source_id == source_id:
            return (spec.center_x_mm, spec.center_y_mm, spec.connectors)
    return None


def source_ids_hidden_from_collection() -> frozenset[str]:
    """Template shells and source bodies the catalogue does not offer on their own.

    ``listed`` is the source's own flag. A placement template is a separate
    entry, and an unlisted body exists only so that template can be placed.
    Neither is a stock row. The test is the source id, not the designation
    and not the selectable flag.
    """
    draft = load_carrera_library()
    hidden = {spec.source_id for spec in draft.parts if not spec.catalog_listed}
    hidden.update(template.source_id for template in draft.templates)
    return frozenset(hidden)


def _payload(filename: str) -> dict[str, object]:
    text = (files("slot_racing.modules.track_planner") / "data" / filename).read_text(
        encoding="utf-8"
    )
    document = json.loads(text)
    payload = document["payload"] if isinstance(document, dict) and "payload" in document else text
    if not isinstance(payload, str):
        raise ValueError(filename)
    padded = payload + "=" * (-len(payload) % 4)
    decoded = json.loads(base64.urlsafe_b64decode(padded))
    if not isinstance(decoded, dict) or not isinstance(decoded.get("parts"), dict):
        raise ValueError(filename)
    return decoded


def _part_table(document: Mapping[str, object], filename: str) -> dict[str, dict[str, object]]:
    """UUID to part object. A file whose parts are not objects is rejected."""
    raw = document.get("parts")
    if not isinstance(raw, dict):
        raise ValueError(filename)
    parts: dict[str, dict[str, object]] = {}
    for source_id, part in raw.items():
        if not isinstance(source_id, str) or not isinstance(part, dict):
            raise ValueError(filename)
        body: dict[str, object] = {}
        for key, value in part.items():
            if not isinstance(key, str):
                raise ValueError(filename)
            body[key] = value
        parts[source_id] = body
    return parts


def _referenced(parts: Mapping[str, dict[str, object]]) -> dict[str, set[str]]:
    found: dict[str, set[str]] = {source_id: set() for source_id in parts}
    for source_id, part in parts.items():
        for key in ("attachPoints", "placeholders"):
            rows = part.get(key) or []
            if not isinstance(rows, list):
                continue
            for row in rows:
                if isinstance(row, dict) and isinstance(row.get("partId"), str):
                    found.setdefault(source_id, set()).add(row["partId"])
    return found


def _systems(
    source_id: str,
    evolution_ids: set[str],
    digital_ids: set[str],
    referenced: Mapping[str, set[str]],
) -> tuple[str, ...]:
    systems: list[str] = []
    used_by_digital = any(
        source_id in refs for owner, refs in referenced.items() if owner in digital_ids
    )
    used_by_evolution = any(
        source_id in refs for owner, refs in referenced.items() if owner in evolution_ids
    )
    if source_id in evolution_ids or used_by_evolution:
        systems.append("evolution")
    if source_id in digital_ids or used_by_digital:
        systems.append("digital")
    return tuple(systems)


def _is_shell(part: dict[str, object]) -> bool:
    placeholders = part.get("placeholders") or []
    return bool(placeholders) and not part.get("slots") and not part.get("connectors")


def _name(part: Mapping[str, object]) -> str:
    names = part.get("names")
    if isinstance(names, dict):
        german = names.get("de")
        if isinstance(german, str) and german.strip():
            return german.strip()
        for value in names.values():
            if isinstance(value, str) and value.strip():
                return value.strip()
    return "Bauteil"


def _piece_keys(
    bodies: Mapping[str, dict[str, object]], shells: Mapping[str, dict[str, object]]
) -> dict[str, str]:
    grouped: dict[str, list[str]] = {}
    for source_id, part in bodies.items():
        article = str(part.get("partNumber") or "").strip()
        grouped.setdefault(article, []).append(source_id)
    keys: dict[str, str] = {START_SOURCE: START_PIECE_KEY, HELPER_SOURCE: HELPER_PIECE_KEY}
    for article, source_ids in grouped.items():
        if article == "20509":
            continue
        physical = [source_id for source_id in source_ids if source_id not in keys]
        if len(physical) < 2:
            continue
        ordered = sorted(physical, key=lambda source_id: _piece_order(source_id, bodies, shells))
        for index, source_id in enumerate(ordered, start=1):
            keys[source_id] = f"{article}.{index}"
    return keys


def _piece_order(
    source_id: str,
    bodies: Mapping[str, dict[str, object]],
    shells: Mapping[str, dict[str, object]],
) -> tuple[object, ...]:
    part = bodies[source_id]
    name = _name(part)
    if "1/2" in name:
        return (0, 1, source_id)
    if "2/2" in name:
        return (0, 2, source_id)
    if "Einfahrt" in name:
        return (0, 1, source_id)
    if "Ausfahrt" in name:
        return (0, 2, source_id)
    center = part.get("center") if isinstance(part.get("center"), dict) else {}
    center_x = float(center.get("x") or 0.0) if isinstance(center, dict) else 0.0
    for shell in shells.values():
        rows = shell.get("placeholders") or []
        if not isinstance(rows, list):
            continue
        for row in rows:
            if isinstance(row, dict) and row.get("partId") == source_id:
                return (1, float(row.get("x") or 0.0), float(row.get("y") or 0.0), source_id)
    return (2, center_x, source_id)


def _part_spec(
    source_id: str,
    part: dict[str, object],
    *,
    piece_key: str,
    systems: tuple[str, ...],
) -> tuple[PartSpec, tuple[str, ...]]:
    slots = _slot_paths(part.get("slots"))
    drawings, drawing_notes = _drawing_paths(part.get("drawings"))
    clip = parse_svg_path(str(part.get("clip") or ""))
    contours = tuple(SlotPath(subpath.spans, PATH_CONTOUR) for subpath in clip if subpath.spans)
    outline = outline_points(clip)
    if len(outline) < 3:
        outline = _bounds_box(slots)
    length, width = _span(outline)
    article = str(part.get("partNumber") or "").strip()
    category = _category(article, len(slots))
    connectors = _connectors(part, source_id=source_id, lane_count=len(slots))
    center_x, center_y = _center_xy(part)
    return PartSpec(
        article_number=article,
        scale=CATALOG_SCALE,
        name=_name(part),
        category=category,
        length_mm=length,
        width_mm=width,
        height_mm=None,
        radius_mm=None,
        angle_deg=None,
        lane_count=len(slots),
        connectors=connectors,
        outline=outline,
        slot_paths=slots + drawings,
        contours=contours,
        manufacturer="Carrera",
        description="",
        source_id=source_id,
        piece_key=piece_key,
        systems=systems,
        selectable=source_id != HELPER_SOURCE,
        catalog_listed=part.get("listed") is not False,
        center_x_mm=center_x,
        center_y_mm=center_y,
    ), drawing_notes


def _category(article: str, slot_count: int) -> str:
    if slot_count == 0:
        return BORDER
    if article in _CURVES:
        return CURVE
    if article in _SWITCHES:
        return SWITCH
    if article in _LANE_CHANGES:
        return LANE_CHANGE
    if article in _PITS:
        return PITLANE
    if article in _SPECIALS:
        return SPECIAL
    return STRAIGHT


def _slot_paths(raw: object) -> tuple[SlotPath, ...]:
    if not isinstance(raw, list):
        return ()
    paths: list[SlotPath] = []
    for index, slot in enumerate(raw, start=1):
        if not isinstance(slot, dict):
            continue
        parsed = parse_svg_path(str(slot.get("path") or ""))
        for subpath in parsed:
            if subpath.spans:
                paths.append(SlotPath(subpath.spans, PATH_SLOT, lane=index))
    return tuple(paths)


# Keys present on every drawing in the bundled Carrera files, plus the two fills.
_DRAWING_KEYS = frozenset(
    {
        "type",
        "path",
        "stroke-width",
        "stroke-color",
        "stroke-linecap",
        "fill-color",
        "fill-color-alt",
    }
)
_COLOR = re.compile(r"^#(?:[0-9a-f]{3,4}|[0-9a-f]{6}|[0-9a-f]{8})$")
_CAPS = frozenset({"butt", "round", "square"})
# Grey 4 mm strokes are the centre dashes. The 16 mm grey stroke is the slot rim.
_CENTER_STROKES = frozenset({("#e0e0e0", 4.0), ("#ebebeb", 4.0)})


def _drawing_paths(raw: object) -> tuple[tuple[SlotPath, ...], tuple[str, ...]]:
    """Every area and stroke. Physical grooves stay in ``slots``, not here.

    The file lists the front-most drawing first. ``z_order`` keeps that index so
    the painter can put the last entry at the back. ``fill-color`` is the plain
    roadway. ``fill-color-alt`` is the same surface with colour coding on.
    """
    if not isinstance(raw, list):
        return (), ("drawings are not a list",) if raw is not None else ()
    paths: list[SlotPath] = []
    notes: list[str] = []
    for index, drawing in enumerate(raw):
        if not isinstance(drawing, dict):
            notes.append(f"drawing {index} is not an object")
            trace("DRAWING_ATTRIBUTE", result="ignored", detail=notes[-1])
            continue
        for key in drawing:
            if key not in _DRAWING_KEYS:
                notes.append(f"unknown drawing attribute {key}")
                trace("DRAWING_ATTRIBUTE", result="kept", detail=key)
        kind, note = _drawing_kind(drawing)
        if note:
            notes.append(note)
            trace("DRAWING_ATTRIBUTE", result="kept", detail=note)
        try:
            parsed = parse_svg_path(str(drawing.get("path") or ""))
        except ValueError:
            notes.append(f"unsupported path command in drawing {index}")
            trace("DRAWING_ATTRIBUTE", result="unsupported", detail=notes[-1])
            continue
        if not parsed and str(drawing.get("path") or "").strip():
            notes.append(f"drawing {index} has no geometry")
            trace("DRAWING_ATTRIBUTE", result="empty", detail=notes[-1])
        extras = tuple(
            (str(key), _extra_text(value))
            for key, value in drawing.items()
            if str(key) not in _DRAWING_KEYS
        )
        stroke = _color_text(drawing.get("stroke-color"), notes)
        width = _width_text(drawing.get("stroke-width"), notes)
        linecap = _cap_text(drawing.get("stroke-linecap"), notes)
        fill = _color_text(drawing.get("fill-color"), notes)
        fill_alt = _color_text(drawing.get("fill-color-alt"), notes)
        closed = drawing.get("type") == "area"
        for subpath in parsed:
            if not subpath.spans:
                continue
            paths.append(
                SlotPath(
                    subpath.spans,
                    kind,
                    stroke=stroke,
                    width_mm=width,
                    linecap=linecap,
                    fill=fill,
                    fill_alt=fill_alt,
                    closed=closed or subpath.closed,
                    z_order=index,
                    extras=extras,
                )
            )
    return tuple(paths), tuple(dict.fromkeys(notes))


def _drawing_kind(drawing: Mapping[str, object]) -> tuple[str, str]:
    raw_type = drawing.get("type")
    if raw_type == "area":
        return PATH_DRAW, ""
    if raw_type != "path":
        return PATH_DRAW, f"unknown drawing type {raw_type}"
    color = str(drawing.get("stroke-color") or "").strip().lower()
    width = drawing.get("stroke-width")
    width_mm = (
        float(width) if isinstance(width, (int, float)) and not isinstance(width, bool) else None
    )
    if color in {"#ffffff", "#fff"}:
        return PATH_START, ""
    if width_mm is not None and (color, width_mm) in _CENTER_STROKES:
        return PATH_CENTER, ""
    return PATH_DRAW, ""


def _color_text(value: object, notes: list[str]) -> str | None:
    if not isinstance(value, str):
        return None
    text = value.strip().lower()
    if text in {"", "none", "transparent"}:
        return None
    if _COLOR.fullmatch(text) is None:
        notes.append(f"unrecognised colour {text}")
    return text


def _width_text(value: object, notes: list[str]) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        if value is not None:
            notes.append("unrecognised stroke width")
        return None
    number = float(value)
    if not math.isfinite(number):
        notes.append("unrecognised stroke width")
        return None
    return number


def _cap_text(value: object, notes: list[str]) -> str | None:
    if not isinstance(value, str) or not value.strip():
        return None
    text = value.strip().lower()
    if text not in _CAPS:
        notes.append(f"unknown line cap {text}")
    return text


def _extra_text(value: object) -> str:
    if isinstance(value, str):
        return value
    return json.dumps(value, ensure_ascii=False, default=str)


def _connectors(
    part: dict[str, object], *, source_id: str, lane_count: int
) -> tuple[ConnectorSpec, ...]:
    raw_edges = part.get("connectors")
    edges: list[object] = list(raw_edges) if isinstance(raw_edges, list) else []
    slots = part.get("slots") if isinstance(part.get("slots"), list) else []
    center_x, center_y = _center_xy(part)
    points = _slot_endpoints(slots)
    connectors: list[ConnectorSpec] = []
    edge_joints: list[ConnectorSpec] = []
    seen_edges: set[int] = set()
    attached = part.get("attachPoints")
    rows: list[object] = list(attached) if isinstance(attached, list) else []
    for index, row in enumerate(rows):
        if not isinstance(row, dict):
            continue
        indices = [
            int(item)
            for item in row.get("connectorIndices") or []
            if isinstance(item, int) and not isinstance(item, bool) and 0 <= item < len(edges)
        ]
        chosen = [edges[item] for item in indices]
        edge_points = _edge_points(chosen)
        if edge_points:
            mid_x = sum(point[0] for point in edge_points) / len(edge_points)
            mid_y = sum(point[1] for point in edge_points) / len(edge_points)
        else:
            mid_x = float(row.get("x") or 0.0)
            mid_y = float(row.get("y") or 0.0)
        types = tuple(
            str(edge.get("type")) for edge in chosen if isinstance(edge, dict) and edge.get("type")
        )
        auto = bool(row.get("autoConnect"))
        lanes = () if auto or lane_count < 1 else tuple(range(1, lane_count + 1))
        lane_points = () if auto else _lane_points(points, (mid_x, mid_y), lane_count)
        length = _segment_length(chosen[0]) if auto and len(indices) == 1 else None
        connectors.append(
            ConnectorSpec(
                name=f"j{index}",
                x_mm=mid_x,
                y_mm=mid_y,
                z_mm=0.0,
                direction_deg=_joint_direction(center_x, center_y, mid_x, mid_y, chosen),
                kind=BORDER_JOINT if auto else TRACK,
                lanes=lanes,
                target_source_id=str(row.get("partId") or ""),
                edge_types=types,
                exclusive=bool(types) or auto,
                has_neighbor=True,
                neighbor_x=float(row.get("x") or 0.0),
                neighbor_y=float(row.get("y") or 0.0),
                neighbor_angle=float(row.get("angle") or 0.0),
                lane_points=lane_points,
                edge_length_mm=length,
            )
        )
        if auto and len(indices) > 1:
            for item in indices:
                if item in seen_edges:
                    continue
                seen_edges.add(item)
                joint = _border_edge_joint(
                    edges[item],
                    item,
                    center_x,
                    center_y,
                    target_source_id=str(row.get("partId") or ""),
                )
                if joint is not None:
                    edge_joints.append(joint)
    if not rows:
        for item, edge in enumerate(edges):
            joint = _border_edge_joint(edge, item, center_x, center_y, target_source_id="")
            if joint is not None:
                edge_joints.append(joint)
    return tuple(connectors + edge_joints)


def _center_xy(part: Mapping[str, object]) -> tuple[float, float]:
    center = part.get("center")
    if not isinstance(center, dict):
        return (0.0, 0.0)
    return (float(center.get("x") or 0.0), float(center.get("y") or 0.0))


def _segment_length(edge: object) -> float | None:
    ends = _edge_ends(edge)
    if ends is None:
        return None
    start, end = ends
    length = math.hypot(end[0] - start[0], end[1] - start[1])
    if not math.isfinite(length) or length <= 0.0:
        return None
    return length


def _edge_ends(edge: object) -> tuple[tuple[float, float], tuple[float, float]] | None:
    if not isinstance(edge, dict):
        return None
    start = edge.get("p1")
    end = edge.get("p2")
    if not isinstance(start, dict) or not isinstance(end, dict):
        return None
    return (
        (float(start.get("x") or 0.0), float(start.get("y") or 0.0)),
        (float(end.get("x") or 0.0), float(end.get("y") or 0.0)),
    )


def _border_edge_joint(
    edge: object,
    index: int,
    center_x: float,
    center_y: float,
    *,
    target_source_id: str,
) -> ConnectorSpec | None:
    """One original connector edge, seated on its own midpoint."""
    ends = _edge_ends(edge)
    length = _segment_length(edge)
    if ends is None or length is None:
        return None
    start, end = ends
    mid_x = (start[0] + end[0]) / 2.0
    mid_y = (start[1] + end[1]) / 2.0
    kind = edge.get("type") if isinstance(edge, dict) else None
    return ConnectorSpec(
        name=f"e{index}",
        x_mm=mid_x,
        y_mm=mid_y,
        z_mm=0.0,
        direction_deg=_joint_direction(center_x, center_y, mid_x, mid_y, [edge]),
        kind=BORDER_JOINT,
        lanes=(),
        target_source_id=target_source_id,
        edge_types=(str(kind),) if kind else (),
        exclusive=False,
        has_neighbor=False,
        edge_length_mm=length,
    )


def _edge_points(edges: list[object]) -> list[tuple[float, float]]:
    points: list[tuple[float, float]] = []
    for edge in edges:
        if not isinstance(edge, dict):
            continue
        for key in ("p1", "p2"):
            point = edge.get(key)
            if isinstance(point, dict):
                points.append((float(point.get("x") or 0.0), float(point.get("y") or 0.0)))
    return points


def _slot_endpoints(slots: object) -> list[tuple[tuple[float, float], tuple[float, float]]]:
    found: list[tuple[tuple[float, float], tuple[float, float]]] = []
    if not isinstance(slots, list):
        return found
    for slot in slots:
        if not isinstance(slot, dict):
            continue
        start = slot.get("p1")
        end = slot.get("p2")
        if isinstance(start, dict) and isinstance(end, dict):
            found.append(
                (
                    (float(start.get("x") or 0.0), float(start.get("y") or 0.0)),
                    (float(end.get("x") or 0.0), float(end.get("y") or 0.0)),
                )
            )
    return found


def _lane_points(
    slots: list[tuple[tuple[float, float], tuple[float, float]]],
    joint: tuple[float, float],
    lane_count: int,
) -> tuple[tuple[float, float], ...]:
    if lane_count < 1 or not slots:
        return ()
    nearest = [min(pair, key=lambda point: _distance(point, joint)) for pair in slots[:lane_count]]
    ordered = sorted(nearest, key=lambda point: (point[1], point[0]))
    return tuple(ordered)


def _joint_direction(
    center_x: float,
    center_y: float,
    x_mm: float,
    y_mm: float,
    edges: list[object],
) -> float:
    """Outward heading of an end face.

    The source edge runs across the joint. Its normal that points away from the
    part centre is the direction a mating piece has to face. A missing edge
    falls back to the centre-to-joint vector.
    """
    away = math.degrees(math.atan2(y_mm - center_y, x_mm - center_x)) % 360.0
    edge = _edge_heading(edges)
    if edge is None:
        return away
    candidates = ((edge + 90.0) % 360.0, (edge - 90.0) % 360.0)
    return min(candidates, key=lambda heading: abs((heading - away + 180.0) % 360.0 - 180.0))


def _edge_heading(edges: list[object]) -> float | None:
    angles: list[float] = []
    for edge in edges:
        if not isinstance(edge, dict):
            continue
        start = edge.get("p1")
        end = edge.get("p2")
        if not isinstance(start, dict) or not isinstance(end, dict):
            continue
        dx = float(end.get("x") or 0.0) - float(start.get("x") or 0.0)
        dy = float(end.get("y") or 0.0) - float(start.get("y") or 0.0)
        if abs(dx) < 1e-9 and abs(dy) < 1e-9:
            continue
        angles.append(math.atan2(dy, dx))
    if not angles:
        return None
    base = angles[0]
    total_x = 0.0
    total_y = 0.0
    for angle in angles:
        if math.cos(angle - base) < 0.0:
            angle += math.pi
        total_x += math.cos(angle)
        total_y += math.sin(angle)
    return math.degrees(math.atan2(total_y, total_x)) % 360.0


def _distance(left: tuple[float, float], right: tuple[float, float]) -> float:
    return abs(left[0] - right[0]) + abs(left[1] - right[1])


def _members(
    part: dict[str, object], known: set[str]
) -> tuple[tuple[TemplateMemberDraft, ...], bool]:
    rows = part.get("placeholders") or []
    if not isinstance(rows, list):
        return (), True
    members: list[TemplateMemberDraft] = []
    missing = False
    for row in rows:
        if not isinstance(row, dict) or not isinstance(row.get("partId"), str):
            missing = True
            continue
        if row["partId"] not in known:
            missing = True
            continue
        members.append(
            TemplateMemberDraft(
                source_id=row["partId"],
                x_mm=float(row.get("x") or 0.0),
                y_mm=float(row.get("y") or 0.0),
                angle_deg=float(row.get("angle") or 0.0),
            )
        )
    return tuple(members), missing


def _packages(specs: tuple[PartSpec, ...]) -> tuple[PackageDraft, ...]:
    grouped: dict[str, list[PartSpec]] = {}
    for spec in specs:
        if not spec.selectable:
            continue
        grouped.setdefault(spec.article_number, []).append(spec)
    packages: list[PackageDraft] = []
    for article, parts in sorted(grouped.items()):
        separate = article == "20509"
        if separate or len(parts) == 1:
            for spec in parts:
                number = spec.piece_key or spec.article_number
                packages.append(
                    PackageDraft(
                        article_number=number,
                        name=spec.name,
                        contents=((spec.source_id, spec.name, spec.article_number),),
                    )
                )
            continue
        packages.append(
            PackageDraft(
                article_number=article,
                name=parts[0].name,
                contents=tuple((spec.source_id, spec.name, spec.article_number) for spec in parts),
            )
        )
    return tuple(packages)


def _type_notes(specs: tuple[PartSpec, ...]) -> tuple[ImportNote, ...]:
    by_source = {spec.source_id: spec for spec in specs}
    notes: list[ImportNote] = []
    for spec in specs:
        for connector in spec.connectors:
            if not connector.exclusive or not connector.edge_types:
                continue
            other = by_source.get(connector.target_source_id)
            if other is None:
                continue
            partner = next(
                (
                    item
                    for item in other.connectors
                    if item.exclusive and item.target_source_id == spec.source_id
                ),
                None,
            )
            if partner is None or not partner.edge_types:
                continue
            if partner.edge_types != connector.edge_types:
                notes.append(
                    ImportNote(
                        spec.source_id,
                        spec.article_number,
                        spec.name,
                        f"{connector.edge_types[0]} mates with {partner.edge_types[0]}",
                    )
                )
    return tuple(notes)


def _span(outline: tuple[tuple[float, float], ...]) -> tuple[float, float]:
    if not outline:
        return (0.0, 0.0)
    xs = [point[0] for point in outline]
    ys = [point[1] for point in outline]
    return (max(xs) - min(xs), max(ys) - min(ys))


def _bounds_box(paths: tuple[SlotPath, ...]) -> tuple[tuple[float, float], ...]:
    points: list[tuple[float, float]] = []
    for path in paths:
        for span in path.spans:
            points.append((span.x0, span.y0))
            points.append((span.x1, span.y1))
    if not points:
        return ((-1.0, -1.0), (1.0, -1.0), (1.0, 1.0), (-1.0, 1.0))
    min_x = min(point[0] for point in points)
    max_x = max(point[0] for point in points)
    min_y = min(point[1] for point in points)
    max_y = max(point[1] for point in points)
    return ((min_x, min_y), (max_x, min_y), (max_x, max_y), (min_x, max_y))
