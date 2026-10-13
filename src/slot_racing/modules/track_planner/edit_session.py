"""Edit state of the graphical part editor. No Qt in here.

The state is one frozen :class:`PartSpec`. Every change builds a new spec through
:mod:`editing`; the session only keeps the history and the picking rules. A drag
replaces the spec without history (``live``) and writes one undo step when it ends
(``commit``). Nothing here touches the database.

Four snaps exist in the program and this module owns only the first two:

* editor grid snap (a mouse position rounds to the editor grid),
* control point snap inside one part (nodes, rail joints, optional tangent and angle),
* rail snap in the planner (``snap_pose``), and
* border snap in the planner (``attach_accessory``).

The last two live in the planner and are never called from here.
"""

from __future__ import annotations

import math
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field

from slot_racing.core.errors import ValidationError
from slot_racing.modules.track_planner import editing as ed
from slot_racing.modules.track_planner.geometry import (
    POSITION_GAP_MM,
    closest_on_segment,
)
from slot_racing.modules.track_planner.parts import PartSpec, as_segment

KIND_POINT = "point"
KIND_CONNECTOR = "connector"
KIND_SEGMENT = "segment"
KIND_PATH = "path"

HISTORY_LIMIT = 200
DEFAULT_GRID_MM = 10.0
DEFAULT_ANGLE_STEP_DEG = 15.0


@dataclass(frozen=True, slots=True)
class Target:
    """One thing the pointer can pick: a handle, a rail joint, a segment or a whole path."""

    kind: str
    path: ed.PathRef | None = None
    segment: int = 0
    handle: str = ""
    connector: int = -1
    distance_mm: float = 0.0

    def point(self) -> ed.PointRef | None:
        if self.kind != KIND_POINT or self.path is None:
            return None
        return ed.PointRef(self.path, self.segment, self.handle)

    def same_item(self, other: Target | None) -> bool:
        """Equal ignoring the distance, which changes with the pointer."""
        return (
            other is not None
            and self.kind == other.kind
            and self.path == other.path
            and self.segment == other.segment
            and self.handle == other.handle
            and self.connector == other.connector
        )


@dataclass(frozen=True, slots=True)
class Snapping:
    """Which aids are on. Each can be switched off on its own."""

    grid: bool = False
    grid_mm: float = DEFAULT_GRID_MM
    points: bool = True
    tangent: bool = False
    angle: bool = False
    angle_step_deg: float = DEFAULT_ANGLE_STEP_DEG


@dataclass(frozen=True, slots=True)
class Snapped:
    x: float
    y: float
    kind: str = ""


@dataclass(frozen=True, slots=True)
class LaneRow:
    """Length of one lane groove. ``length_mm`` is empty when no single length is certain."""

    lane: int
    length_mm: float | None


# --------------------------------------------------------------------------------------------
# Picking


def nodes_of(spec: PartSpec, layers: Iterable[str] | None = None) -> list[tuple[float, float]]:
    """Every node and rail joint a new point may snap to."""
    chosen = set(ed.LAYERS if layers is None else layers)
    found: list[tuple[float, float]] = []
    for ref in ed.path_refs(spec):
        if ref.layer not in chosen:
            continue
        spans = ed.spans_of(spec, ref)
        for span in spans:
            found.append(ed.span_start(span))
        found.append(ed.span_end(spans[-1]))
    for connector in spec.connectors:
        found.append((connector.x_mm, connector.y_mm))
    return found


def hit_targets(
    spec: PartSpec,
    x_mm: float,
    y_mm: float,
    tolerance_mm: float,
    *,
    layers: Iterable[str] | None = None,
    connectors: bool = True,
    arrow_mm: float = 40.0,
    segments: bool = True,
) -> list[Target]:
    """Everything within ``tolerance_mm``, best first: handles, joints, then segments.

    The tolerance is a pixel distance divided by the view scale, so it follows the zoom.
    Equal distances keep a stable order, which lets a repeated click cycle through overlaps.
    """
    chosen = set(ed.LAYERS if layers is None else layers)
    points: list[Target] = []
    lines: list[Target] = []
    for ref in ed.path_refs(spec):
        if ref.layer not in chosen:
            continue
        spans = ed.spans_of(spec, ref)
        previous_end: tuple[float, float] | None = None
        for index, span in enumerate(spans):
            for handle, (px, py) in ed.handle_points(span).items():
                if (
                    handle in ed.node_handles(span)[:1]
                    and previous_end is not None
                    and math.hypot(px - previous_end[0], py - previous_end[1]) <= POSITION_GAP_MM
                ):
                    continue
                distance = math.hypot(px - x_mm, py - y_mm)
                if distance <= tolerance_mm:
                    points.append(Target(KIND_POINT, ref, index, handle, distance_mm=distance))
            previous_end = ed.span_end(span)
            if not segments:
                continue
            try:
                _t, distance = closest_on_segment(as_segment(span), x_mm, y_mm)
            except ValidationError:
                continue
            if distance <= tolerance_mm:
                lines.append(Target(KIND_SEGMENT, ref, index, distance_mm=distance))
    joints: list[Target] = []
    if connectors:
        for index, connector in enumerate(spec.connectors):
            distance = math.hypot(connector.x_mm - x_mm, connector.y_mm - y_mm)
            if distance <= tolerance_mm:
                joints.append(Target(KIND_CONNECTOR, connector=index, distance_mm=distance))
            radians = math.radians(connector.direction_deg)
            tip = (
                connector.x_mm + math.cos(radians) * arrow_mm,
                connector.y_mm + math.sin(radians) * arrow_mm,
            )
            tip_distance = math.hypot(tip[0] - x_mm, tip[1] - y_mm)
            if tip_distance <= tolerance_mm:
                joints.append(
                    Target(KIND_CONNECTOR, connector=index, handle="tip", distance_mm=tip_distance)
                )
    return (
        sorted(points, key=lambda item: item.distance_mm)
        + sorted(joints, key=lambda item: item.distance_mm)
        + sorted(lines, key=lambda item: item.distance_mm)
    )


def next_target(found: list[Target], current: Target | None, *, cycle: bool) -> Target | None:
    """The best hit, or the one after ``current`` when the same spot is clicked again."""
    if not found:
        return None
    if cycle and current is not None:
        for index, item in enumerate(found):
            if item.same_item(current):
                return found[(index + 1) % len(found)]
    return found[0]


# --------------------------------------------------------------------------------------------
# Snapping


def snap_point(
    spec: PartSpec,
    x_mm: float,
    y_mm: float,
    snapping: Snapping,
    tolerance_mm: float,
    *,
    anchor: tuple[float, float] | None = None,
    heading_deg: float | None = None,
    skip: Iterable[tuple[float, float]] = (),
    layers: Iterable[str] | None = None,
) -> Snapped:
    """Round a mouse position by the enabled aids. Existing nodes win over everything else.

    ``skip`` lists positions that must not attract: the point being dragged sits on its own
    old node. ``anchor`` is the previous point when drawing, ``heading_deg`` the direction the
    previous segment leaves in.
    """
    if not (math.isfinite(x_mm) and math.isfinite(y_mm)):
        raise ValidationError("error.planner.geometry")
    if snapping.points:
        skipped = list(skip)
        best: tuple[float, tuple[float, float]] | None = None
        for node in nodes_of(spec, layers):
            if any(math.hypot(node[0] - s[0], node[1] - s[1]) <= POSITION_GAP_MM for s in skipped):
                continue
            distance = math.hypot(node[0] - x_mm, node[1] - y_mm)
            if distance <= tolerance_mm and (best is None or distance < best[0]):
                best = (distance, node)
        if best is not None:
            return Snapped(best[1][0], best[1][1], "point")
    px, py = x_mm, y_mm
    kind = ""
    if snapping.angle and anchor is not None and snapping.angle_step_deg > 0.0:
        dx = x_mm - anchor[0]
        dy = y_mm - anchor[1]
        radius = math.hypot(dx, dy)
        if radius > 1e-9:
            step = snapping.angle_step_deg
            angle = round(math.degrees(math.atan2(dy, dx)) / step) * step
            if snapping.grid and snapping.grid_mm > 0.0:
                radius = round(radius / snapping.grid_mm) * snapping.grid_mm
            px = anchor[0] + math.cos(math.radians(angle)) * radius
            py = anchor[1] + math.sin(math.radians(angle)) * radius
            return Snapped(px, py, "angle")
    elif snapping.tangent and anchor is not None and heading_deg is not None:
        radians = math.radians(heading_deg)
        ux, uy = math.cos(radians), math.sin(radians)
        dx = x_mm - anchor[0]
        dy = y_mm - anchor[1]
        along = dx * ux + dy * uy
        across = abs(-dx * uy + dy * ux)
        if across <= tolerance_mm and along > 0.0:
            return Snapped(anchor[0] + ux * along, anchor[1] + uy * along, "tangent")
    if snapping.grid and snapping.grid_mm > 0.0:
        step = snapping.grid_mm
        px = round(px / step) * step
        py = round(py / step) * step
        kind = "grid"
    return Snapped(px, py, kind)


# --------------------------------------------------------------------------------------------
# Lane lengths


def lane_rows(spec: PartSpec) -> tuple[LaneRow, ...]:
    """Length per lane. A lane with a gap, a branch or no clean through path shows none."""
    rows: list[LaneRow] = []
    grooves: dict[int, list[ed.PathRef]] = {}
    for ref in ed.groove_refs(spec):
        lane = spec.slot_paths[ref.index].lane
        if lane:
            grooves.setdefault(lane, []).append(ref)
    for lane in sorted(grooves):
        refs = grooves[lane]
        length: float | None = None
        if len(refs) == 1:
            try:
                path = ed.geometry_path(spec, refs[0])
                if all(joint.gap_mm <= POSITION_GAP_MM for joint in path.joints()):
                    length = path.length_mm()
            except ValidationError:
                length = None
        rows.append(LaneRow(lane, length))
    return tuple(rows)


def route_is_simple(spec: PartSpec) -> bool:
    """True when every lane is one groove between two rail joints. Nothing branches."""
    issues = {issue.key for issue in ed.validate_editing(spec)}
    blocking = {
        "error.planner.editor.branch",
        "error.planner.editor.lane_twice",
        "error.planner.editor.groove_end",
        "error.planner.editor.lane_missing",
        "error.planner.editor.gap",
    }
    return not (issues & blocking)


# --------------------------------------------------------------------------------------------
# Session


@dataclass(slots=True)
class EditSession:
    """The part being drawn, its starting state and the undo history."""

    base: PartSpec
    spec: PartSpec = field(init=False)
    _undo: list[PartSpec] = field(default_factory=list, init=False, repr=False)
    _redo: list[PartSpec] = field(default_factory=list, init=False, repr=False)
    _live_from: PartSpec | None = field(default=None, init=False, repr=False)
    _issue_cache: tuple[PartSpec, tuple[ed.EditIssue, ...]] | None = field(
        default=None, init=False, repr=False
    )
    listeners: list[Callable[[], None]] = field(default_factory=list, init=False, repr=False)

    def __post_init__(self) -> None:
        self.spec = self.base

    @property
    def changed(self) -> bool:
        return self.spec != self.base

    @property
    def is_live(self) -> bool:
        """True while a drag runs and its undo step is not written yet."""
        return self._live_from is not None

    @property
    def can_undo(self) -> bool:
        return bool(self._undo) or self._live_from is not None

    @property
    def can_redo(self) -> bool:
        return bool(self._redo) and self._live_from is None

    def result(self) -> PartSpec:
        """The part as it would be saved: measures, category and outline brought in line."""
        return ed.finalize(self.spec, self.base) if self.changed else self.spec

    def issues(self) -> tuple[ed.EditIssue, ...]:
        """Problems that block saving. Cached for the current spec."""
        current = self.spec
        if self._issue_cache is not None and self._issue_cache[0] is current:
            return self._issue_cache[1]
        try:
            found = ed.validate_editing(self.result())
        except ValidationError as error:
            found = (ed.EditIssue(error.key),)
        self._issue_cache = (current, found)
        return found

    def reset(self, base: PartSpec) -> None:
        self.base = base
        self.spec = base
        self._undo.clear()
        self._redo.clear()
        self._live_from = None
        self._issue_cache = None
        self._notify()

    def apply(self, spec: PartSpec) -> bool:
        """Make ``spec`` current as one undo step. ``False`` when nothing changed."""
        self._settle()
        if spec == self.spec:
            return False
        self._push(self.spec)
        self.spec = spec
        self._redo.clear()
        self._notify()
        return True

    def live(self, spec: PartSpec) -> None:
        """Show ``spec`` while a drag runs. No history entry until :meth:`commit`."""
        if self._live_from is None:
            self._live_from = self.spec
        self.spec = spec
        self._notify()

    def commit(self) -> bool:
        """End a drag. The state from before the drag becomes one undo step."""
        start = self._live_from
        self._live_from = None
        if start is None or start == self.spec:
            return False
        self._push(start)
        self._redo.clear()
        self._notify()
        return True

    def abort(self) -> None:
        """Drop a drag and go back to the state before it."""
        start = self._live_from
        self._live_from = None
        if start is not None:
            self.spec = start
            self._notify()

    def undo(self) -> bool:
        self._settle()
        if not self._undo:
            return False
        self._redo.append(self.spec)
        self.spec = self._undo.pop()
        self._notify()
        return True

    def redo(self) -> bool:
        self._settle()
        if not self._redo:
            return False
        self._push(self.spec)
        self.spec = self._redo.pop()
        self._notify()
        return True

    def _settle(self) -> None:
        if self._live_from is not None:
            self.commit()

    def _push(self, spec: PartSpec) -> None:
        self._undo.append(spec)
        if len(self._undo) > HISTORY_LIMIT:
            del self._undo[0]

    def _notify(self) -> None:
        for listener in list(self.listeners):
            listener()
