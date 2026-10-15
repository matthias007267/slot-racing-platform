"""Qt-free element editor for one part.

Coordinates are millimetres. A click commits geometry; the hover preview does
not. Undo restores elements, not the mouse position. The compiled
:class:`PartSpec` is the same body the plan paints.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass, replace
from typing import TypedDict, Unpack

from slot_racing.core.errors import ValidationError
from slot_racing.modules.track_planner.editing import (
    arc_through,
    is_closed,
    outline_from_spans,
    rotate_span,
    span_end,
    span_length_mm,
    span_of,
    span_start,
    translate_span,
)
from slot_racing.modules.track_planner.geometry import (
    POSITION_GAP_MM,
    CubicBezier,
    GeometryPath,
    validate_path,
)
from slot_racing.modules.track_planner.parametric import with_metadata
from slot_racing.modules.track_planner.parts import (
    LANE_PITCH_MM,
    PATH_CONTOUR,
    PATH_SLOT,
    PATH_START,
    SPAN_ARC,
    SPAN_BEZIER,
    SPAN_LINE,
    TRACK,
    ConnectorSpec,
    PartSpec,
    SlotPath,
    SlotSpan,
    as_segment,
    driving_paths,
)

TOOL_SELECT = "select"
TOOL_LINE = "line"
TOOL_ARC = "arc"
TOOL_BEZIER = "bezier"
TOOL_PLATE = "plate"
TOOL_LANE = "lane"
TOOL_JOINT = "joint"
TOOL_START = "start"
TOOLS = (
    TOOL_SELECT,
    TOOL_LINE,
    TOOL_ARC,
    TOOL_BEZIER,
    TOOL_PLATE,
    TOOL_LANE,
    TOOL_JOINT,
    TOOL_START,
)

KIND_PLATE = "plate"
KIND_LANE = "lane"
KIND_JOINT = "connector"
KIND_START = "start"

ISSUE_PLATE = "planner.editor.issue.plate"
ISSUE_GEOMETRY = "planner.editor.issue.geometry"
ISSUE_LANE = "planner.editor.issue.lane"
ISSUE_JOINT = "planner.editor.issue.joint"
ISSUE_ENDS = "planner.editor.issue.ends"
ISSUE_CATEGORY = "planner.editor.issue.category"
_HISTORY = 200


@dataclass(frozen=True, slots=True)
class EditorElement:
    """One drawn object. ``id`` stays when the geometry moves."""

    id: str
    kind: str
    spans: tuple[SlotSpan, ...] = ()
    lane: int | None = None
    x: float = 0.0
    y: float = 0.0
    direction_deg: float = 0.0
    lanes: tuple[int, ...] = ()
    visible: bool = True
    locked: bool = False
    closed: bool = False
    finished: bool = False
    name: str = ""


class _ElementPatch(TypedDict, total=False):
    """Fields ``_replace`` may write on one element."""

    spans: tuple[SlotSpan, ...]
    x: float
    y: float
    direction_deg: float
    lanes: tuple[int, ...]
    visible: bool
    locked: bool


@dataclass(frozen=True, slots=True)
class _Pen:
    """Clicks of the span that is not stored yet."""

    tool: str
    target: str | None
    points: tuple[tuple[float, float], ...]


def _distance(left: tuple[float, float], right: tuple[float, float]) -> float:
    return math.hypot(right[0] - left[0], right[1] - left[1])


def _finite(x_mm: float, y_mm: float) -> bool:
    return (
        not isinstance(x_mm, bool)
        and not isinstance(y_mm, bool)
        and isinstance(x_mm, (int, float))
        and isinstance(y_mm, (int, float))
        and math.isfinite(x_mm)
        and math.isfinite(y_mm)
    )


class GraphicSession:
    """Elements, selection, snap and one undo stack."""

    def __init__(self) -> None:
        self.tool = TOOL_SELECT
        self.grid_mm = 10.0
        self.snap_grid = True
        self.snap_points = True
        self.elements: tuple[EditorElement, ...] = ()
        self.selected: frozenset[str] = frozenset()
        self.active_span: tuple[str, int] | None = None
        self.active_handle: str | None = None
        self.hover: tuple[float, float] | None = None
        self.snap_target: tuple[float, float] | None = None
        self.marks: frozenset[str] = frozenset()
        self._seq = 0
        self._undo: list[tuple[EditorElement, ...]] = []
        self._redo: list[tuple[EditorElement, ...]] = []
        self._pen: _Pen | None = None
        self._drag_from: tuple[float, float] | None = None
        self._drag_base: tuple[EditorElement, ...] | None = None
        self._drag_handle: tuple[str, int, str] | None = None
        self._source: PartSpec | None = None
        self._baseline: tuple[EditorElement, ...] = ()
        self._path_kind = KIND_LANE
        self._edited = False

    @property
    def source(self) -> PartSpec | None:
        return self._source

    @property
    def pen_points(self) -> tuple[tuple[float, float], ...]:
        return () if self._pen is None else self._pen.points

    @property
    def edited(self) -> bool:
        return self._edited

    def load(self, spec: PartSpec | None) -> None:
        """Show ``spec`` without marking it edited. ``None`` starts empty."""
        self._source = spec
        self._edited = False
        self._undo.clear()
        self._redo.clear()
        self._pen = None
        self.selected = frozenset()
        self.active_span = None
        self.active_handle = None
        self.marks = frozenset()
        self.elements = () if spec is None else _elements_from_spec(spec)
        self._baseline = self.elements
        self._seq = _sequence_of(self.elements)
        self._path_kind = KIND_LANE
        self._edited = False

    def set_tool(self, tool: str) -> None:
        if tool not in TOOLS:
            raise ValueError(tool)
        if tool == TOOL_PLATE:
            self._finish_kind(KIND_LANE)
            self._path_kind = KIND_PLATE
        elif tool in {TOOL_LANE, TOOL_LINE}:
            self._path_kind = KIND_LANE
        elif tool in {TOOL_SELECT, TOOL_JOINT, TOOL_START}:
            self._finish_kind(KIND_LANE)
        self.tool = tool
        self._pen = None
        self.hover = None

    def cancel_action(self) -> None:
        """Drop the span that is still being drawn. A drag returns to its start."""
        if self._drag_base is not None:
            self.elements = self._drag_base
        self._pen = None
        self.hover = None
        self.snap_target = None
        self._drag_from = None
        self._drag_base = None
        self._drag_handle = None

    def click(
        self, x_mm: float, y_mm: float, *, shift: bool = False, tolerance_mm: float = 6.0
    ) -> None:
        point = self.snap(x_mm, y_mm, tolerance_mm)
        if self.tool == TOOL_SELECT:
            self._select_click(point, shift=shift, tolerance_mm=tolerance_mm)
            return
        if self.tool == TOOL_JOINT:
            self._place_joint(point)
            return
        if self.tool == TOOL_START:
            self._click_start(point)
            return
        if self.tool in {TOOL_PLATE, TOOL_LANE, TOOL_LINE}:
            self._click_line(point, tolerance_mm)
            return
        if self.tool == TOOL_ARC:
            self._click_arc(point)
            return
        self._click_bezier(point)

    def move(self, x_mm: float, y_mm: float, *, tolerance_mm: float = 6.0) -> None:
        point = self.snap(x_mm, y_mm, tolerance_mm)
        self.hover = point
        if self._drag_from is None or self._drag_base is None:
            return
        if self._drag_handle is not None:
            self.elements = _moved_handle(self._drag_base, self._drag_handle, point)
            return
        dx = point[0] - self._drag_from[0]
        dy = point[1] - self._drag_from[1]
        self.elements = _moved_elements(self._drag_base, self.selected, dx, dy)

    def release(self) -> None:
        if self._drag_base is None:
            return
        base = self._drag_base
        self._drag_from = None
        self._drag_base = None
        self._drag_handle = None
        self._commit(base)

    def snap(self, x_mm: float, y_mm: float, tolerance_mm: float) -> tuple[float, float]:
        if not _finite(x_mm, y_mm):
            self.snap_target = None
            return (x_mm, y_mm)
        if self.snap_points:
            best: tuple[float, float] | None = None
            best_distance = tolerance_mm
            for anchor in self._anchors():
                distance = _distance((x_mm, y_mm), anchor)
                if distance <= best_distance:
                    best = anchor
                    best_distance = distance
            if best is not None:
                self.snap_target = best
                return best
        if not self.snap_grid or self.grid_mm <= 0:
            self.snap_target = None
            return (float(x_mm), float(y_mm))
        step = self.grid_mm
        snapped = (round(x_mm / step) * step, round(y_mm / step) * step)
        self.snap_target = snapped
        return snapped

    def delete_selection(self) -> None:
        previous = self.elements
        removed = {
            item.id for item in self.elements if item.id in self.selected and not item.locked
        }
        if not removed:
            return
        self.elements = tuple(item for item in self.elements if item.id not in removed)
        self.selected = frozenset(item for item in self.selected if item not in removed)
        self.active_span = None
        self.active_handle = None
        self._commit(previous)

    def duplicate_selection(self) -> None:
        previous = self.elements
        copies: list[EditorElement] = []
        chosen: list[str] = []
        for item in self.elements:
            if item.id not in self.selected or item.locked or item.kind == KIND_PLATE:
                continue
            copy = self._copy(item, 20.0, 20.0)
            copies.append(copy)
            chosen.append(copy.id)
        if not copies:
            return
        self.elements = (*self.elements, *copies)
        self.selected = frozenset(chosen)
        self._commit(previous)

    def rotate_selection(self, degrees: float) -> None:
        self._transform_selection(lambda item, origin: _rotate_element(item, degrees, origin))

    def mirror_selection(self, axis: str) -> None:
        self._transform_selection(lambda item, origin: _mirror_element(item, axis, origin))

    def set_visible(self, element_id: str, visible: bool) -> None:
        self._replace(element_id, visible=visible)

    def set_locked(self, element_id: str, locked: bool) -> None:
        self._replace(element_id, locked=locked)

    def select_ids(self, ids: frozenset[str]) -> None:
        self.selected = frozenset(item.id for item in self.elements if item.id in ids)
        self.active_span = None
        self.active_handle = None

    def undo(self) -> None:
        if not self._undo:
            return
        self._redo.append(self.elements)
        self.elements = self._undo.pop()
        self._edited = self.elements != self._baseline
        self.selected = frozenset()
        self._pen = None

    def redo(self) -> None:
        if not self._redo:
            return
        self._undo.append(self.elements)
        self.elements = self._redo.pop()
        self._edited = self.elements != self._baseline

    def can_undo(self) -> bool:
        return bool(self._undo)

    def can_redo(self) -> bool:
        return bool(self._redo)

    def element(self, element_id: str) -> EditorElement | None:
        return next((item for item in self.elements if item.id == element_id), None)

    def plate(self) -> EditorElement | None:
        return next((item for item in self.elements if item.kind == KIND_PLATE), None)

    def lanes(self) -> tuple[EditorElement, ...]:
        return tuple(item for item in self.elements if item.kind == KIND_LANE)

    def metrics(self) -> dict[str, float | str | None]:
        """Numbers for the selected handle or the single selected element."""
        point = self._active_point()
        span = self._active_span()
        element = self._single()
        length = None if span is None else span_length_mm(span)
        radius = span.radius_mm if span is not None and span.kind == SPAN_ARC else None
        sweep = span.sweep_deg if span is not None and span.kind == SPAN_ARC else None
        direction = (
            element.direction_deg if element is not None and element.kind == KIND_JOINT else None
        )
        lanes = ""
        if element is not None and element.kind == KIND_JOINT:
            lanes = ", ".join(str(lane) for lane in element.lanes)
        return {
            "x": None if point is None else point[0],
            "y": None if point is None else point[1],
            "length": length,
            "radius": radius,
            "sweep": sweep,
            "direction": direction,
            "lanes": lanes,
        }

    def set_metric(self, name: str, value: float | str) -> None:
        previous = self.elements
        if name in {"x", "y"} and isinstance(value, (int, float)):
            self._set_point(name, float(value))
        elif name == "length" and isinstance(value, (int, float)):
            self._set_length(float(value))
        elif name == "radius" and isinstance(value, (int, float)):
            self._set_radius(float(value))
        elif name == "sweep" and isinstance(value, (int, float)):
            self._set_sweep(float(value))
        elif name == "direction" and isinstance(value, (int, float)):
            element = self._single()
            if element is not None and element.kind == KIND_JOINT:
                self._replace(element.id, direction_deg=float(value) % 360.0)
        elif name == "lanes" and isinstance(value, str):
            element = self._single()
            if element is not None and element.kind == KIND_JOINT:
                self._replace(element.id, lanes=_parse_lanes(value))
        else:
            return
        self._commit(previous)

    def finish_path(self) -> None:
        """End the open lane so the next click starts another one."""
        previous = self.elements
        changed = False
        elements: list[EditorElement] = []
        for item in self.elements:
            if item.kind == KIND_LANE and not item.finished and item.id in self._open_targets():
                elements.append(replace(item, finished=True))
                changed = True
            else:
                elements.append(item)
        self._pen = None
        if changed:
            self.elements = tuple(elements)
            self._commit(previous)

    def release_issues(self) -> tuple[tuple[str, str], ...]:
        """Problems that keep this drawing from becoming a placeable part."""
        found: list[tuple[str, str]] = []
        plate = self.plate()
        if plate is None or not plate.closed or len(plate.spans) < 3:
            found.append((ISSUE_PLATE, "" if plate is None else plate.id))
        elif _path_broken(plate.spans):
            found.append((ISSUE_GEOMETRY, plate.id))
        lane_numbers: list[int] = []
        for lane in self.lanes():
            if not lane.spans:
                continue
            if lane.lane is None or lane.lane in lane_numbers:
                found.append((ISSUE_LANE, lane.id))
            else:
                lane_numbers.append(lane.lane)
            if _path_broken(lane.spans):
                found.append((ISSUE_GEOMETRY, lane.id))
        if lane_numbers and sum(1 for item in self.elements if item.kind == KIND_JOINT) < 2:
            found.append((ISSUE_ENDS, ""))
        claimed: dict[int, list[str]] = {number: [] for number in lane_numbers}
        for joint in self.elements:
            if joint.kind != KIND_JOINT:
                continue
            if not joint.lanes or len(joint.lanes) != len(set(joint.lanes)):
                found.append((ISSUE_JOINT, joint.id))
                continue
            if not _joint_meets_lanes(joint, self.lanes()):
                found.append((ISSUE_JOINT, joint.id))
            for number in joint.lanes:
                if number not in claimed:
                    found.append((ISSUE_JOINT, joint.id))
                else:
                    claimed[number].append(joint.id)
        for number, joints in claimed.items():
            if len(joints) < 1:
                lane = next(item for item in self.lanes() if item.lane == number)
                found.append((ISSUE_LANE, lane.id))
        self.marks = frozenset(element_id for _key, element_id in found if element_id)
        return tuple(found)

    def compile(
        self,
        *,
        article_number: str,
        scale: str,
        name: str,
        manufacturer: str,
        description: str,
        category: str,
        as_draft: bool,
    ) -> PartSpec:
        """The stored body. An untouched open keeps the original geometry."""
        if self._source is not None and not self._edited:
            kept = with_metadata(
                self._source,
                article_number=article_number,
                scale=scale,
                name=name,
                manufacturer=manufacturer,
                description=description,
            )
            if as_draft:
                return replace(kept, category="draft")
            if category and category != kept.category:
                return replace(kept, category=category)
            return kept
        plate = self.plate()
        lanes = tuple(
            SlotPath(item.spans, PATH_SLOT, item.lane)
            for item in self.lanes()
            if item.spans and item.lane is not None
        )
        starts = tuple(
            SlotPath(item.spans, PATH_START)
            for item in self.elements
            if item.kind == KIND_START and item.spans
        )
        contours = (
            () if plate is None or not plate.spans else (SlotPath(plate.spans, PATH_CONTOUR),)
        )
        outline = outline_from_spans(plate.spans) if plate is not None and plate.spans else ()
        if not outline and self._source is not None and not self._edited:
            outline = self._source.outline
        preserved = tuple(
            connector
            for connector in (self._source.connectors if self._source is not None else ())
            if connector.kind != TRACK
        )
        connectors = (
            tuple(
                ConnectorSpec(
                    item.name or item.id,
                    item.x,
                    item.y,
                    0.0,
                    item.direction_deg,
                    TRACK,
                    item.lanes,
                )
                for item in self.elements
                if item.kind == KIND_JOINT
            )
            + preserved
        )
        lane_count = max((item.lane or 0 for item in self.lanes()), default=1)
        bounds = _bounds(self.elements)
        length = None if bounds is None else bounds[2] - bounds[0]
        width = None if bounds is None else bounds[3] - bounds[1]
        height = None if self._source is None else self._source.height_mm
        # A crossing figure ignores stored grooves. An edited body is a special so the
        # plan paints the same paths the editor stored.
        body_category = "draft" if as_draft else ("special" if category == "crossing" else category)
        spec = PartSpec(
            article_number=article_number,
            scale=scale,
            name=name,
            category=body_category,
            length_mm=length,
            width_mm=width,
            height_mm=height,
            radius_mm=None,
            angle_deg=None,
            lane_count=max(1, lane_count),
            connectors=connectors,
            outline=outline,
            slot_paths=lanes + starts,
            contours=contours,
            manufacturer=manufacturer,
            description=description,
            attachment=None
            if self._edited
            else (None if self._source is None else self._source.attachment),
            border_edges=() if self._edited or self._source is None else self._source.border_edges,
        )
        if not as_draft:
            validate_path_group(spec)
        return spec

    def _select_click(
        self, point: tuple[float, float], *, shift: bool, tolerance_mm: float
    ) -> None:
        handle = self._hit_handle(point, tolerance_mm)
        if handle is not None:
            element_id, index, name = handle
            self.selected = frozenset({element_id})
            self.active_span = (element_id, index)
            self.active_handle = name
            self._drag_from = point
            self._drag_base = self.elements
            self._drag_handle = handle
            return
        hit = self._hit_element(point, tolerance_mm)
        self._drag_handle = None
        if hit is None:
            if not shift:
                self.selected = frozenset()
                self.active_span = None
                self.active_handle = None
            return
        if shift:
            self.selected = self.selected - {hit} if hit in self.selected else self.selected | {hit}
        elif hit not in self.selected:
            self.selected = frozenset({hit})
        self.active_span = None
        self.active_handle = None
        element = self.element(hit)
        if element is not None and not element.locked:
            self._drag_from = point
            self._drag_base = self.elements

    def _click_line(self, point: tuple[float, float], tolerance_mm: float) -> None:
        kind = KIND_PLATE if self.tool == TOOL_PLATE else KIND_LANE
        self._path_kind = kind
        target = self._open_existing(kind)
        if target is not None and target.kind == KIND_PLATE and target.closed:
            return
        if (
            target is not None
            and target.kind == KIND_PLATE
            and len(target.spans) >= 3
            and _distance(point, span_start(target.spans[0])) <= tolerance_mm
        ):
            self._close_plate(target.id, point)
            return
        anchor = self._anchor(target)
        if anchor is None:
            self._pen = _Pen(self.tool, None if target is None else target.id, (point,))
            if target is not None:
                self.selected = frozenset({target.id})
            return
        if _distance(anchor, point) <= POSITION_GAP_MM:
            return
        span = SlotSpan.line(anchor[0], anchor[1], point[0], point[1])
        created = self._append_span(None if target is None else target.id, span, kind)
        self._pen = _Pen(self.tool, created, (point,))

    def _close_plate(self, element_id: str, point: tuple[float, float]) -> None:
        element = self.element(element_id)
        if element is None or not element.spans:
            return
        previous = self.elements
        end = span_end(element.spans[-1])
        start = span_start(element.spans[0])
        spans = element.spans
        if _distance(end, start) > POSITION_GAP_MM:
            spans = (*spans, SlotSpan.line(end[0], end[1], start[0], start[1]))
        self.elements = tuple(
            replace(item, spans=spans, closed=True, finished=True)
            if item.id == element_id
            else item
            for item in self.elements
        )
        self._pen = None
        del point
        self._commit(previous)

    def _draw_kind(self) -> str:
        """Arcs and curves follow the open plate. A closed plate sends them to a lane."""
        if self._path_kind == KIND_PLATE:
            plate = self.plate()
            if plate is not None and not plate.closed:
                return KIND_PLATE
        return KIND_LANE

    def _click_arc(self, point: tuple[float, float]) -> None:
        kind = self._draw_kind()
        target = self._open_existing(kind)
        if target is not None and target.kind == KIND_PLATE and target.closed:
            return
        if self._pen is None or self._pen.tool != TOOL_ARC:
            anchor = None if target is None or not target.spans else span_end(target.spans[-1])
            if anchor is None:
                self._pen = _Pen(TOOL_ARC, None, (point,))
                return
            if _distance(anchor, point) <= POSITION_GAP_MM:
                self._pen = _Pen(TOOL_ARC, target.id if target is not None else None, (anchor,))
                return
            self._pen = _Pen(TOOL_ARC, None if target is None else target.id, (anchor, point))
            return
        points = self._pen.points
        if len(points) == 1:
            if _distance(points[0], point) <= POSITION_GAP_MM:
                return
            self._pen = _Pen(TOOL_ARC, self._pen.target, (points[0], point))
            return
        span = arc_through(points[0], point, points[1])
        if span is None:
            return
        created = self._append_span(self._pen.target, span, kind)
        self._pen = _Pen(TOOL_ARC, created, (points[1],))

    def _click_bezier(self, point: tuple[float, float]) -> None:
        kind = self._draw_kind()
        target = self._open_existing(kind)
        if target is not None and target.kind == KIND_PLATE and target.closed:
            return
        if self._pen is None or self._pen.tool != TOOL_BEZIER:
            anchor = None if target is None or not target.spans else span_end(target.spans[-1])
            if anchor is None:
                self._pen = _Pen(TOOL_BEZIER, None, (point,))
                return
            if _distance(anchor, point) <= POSITION_GAP_MM:
                self._pen = _Pen(TOOL_BEZIER, target.id if target is not None else None, (anchor,))
                return
            self._pen = _Pen(TOOL_BEZIER, None if target is None else target.id, (anchor, point))
            return
        points = self._pen.points
        if len(points) == 1:
            if _distance(points[0], point) <= POSITION_GAP_MM:
                return
            self._pen = _Pen(TOOL_BEZIER, self._pen.target, (points[0], point))
            return
        span = _bezier_bulge(points[0], points[1], point)
        created = self._append_span(self._pen.target, span, kind)
        self._pen = _Pen(TOOL_BEZIER, created, (points[1],))

    def _click_start(self, point: tuple[float, float]) -> None:
        if self._pen is None or self._pen.tool != TOOL_START:
            self._pen = _Pen(TOOL_START, None, (point,))
            return
        start = self._pen.points[0]
        if _distance(start, point) <= POSITION_GAP_MM:
            return
        previous = self.elements
        element = EditorElement(
            self._next("start"),
            KIND_START,
            spans=(SlotSpan.line(start[0], start[1], point[0], point[1]),),
            finished=True,
            name="Start/Ziel",
        )
        self.elements = (
            *(item for item in self.elements if item.kind != KIND_START),
            element,
        )
        self.selected = frozenset({element.id})
        self._pen = None
        self._commit(previous)

    def _place_joint(self, point: tuple[float, float]) -> None:
        previous = self.elements
        lanes, direction = _lanes_for_joint(self.lanes(), point)
        name = _next_joint_name(self.elements)
        element = EditorElement(
            self._next("joint"),
            KIND_JOINT,
            x=point[0],
            y=point[1],
            direction_deg=direction,
            lanes=lanes,
            finished=True,
            name=name,
        )
        self.elements = (*self.elements, element)
        self.selected = frozenset({element.id})
        self._commit(previous)

    def _open_existing(self, kind: str) -> EditorElement | None:
        if kind == KIND_PLATE:
            return self.plate()
        return next((item for item in self.lanes() if not item.finished), None)

    def _anchor(self, target: EditorElement | None) -> tuple[float, float] | None:
        if self._pen is not None and self._pen.tool == self.tool and self._pen.points:
            same = target is None or self._pen.target in {None, target.id}
            if same:
                return self._pen.points[-1]
        if target is None or not target.spans:
            return None
        return span_end(target.spans[-1])

    def _append_span(self, target_id: str | None, span: SlotSpan, kind: str) -> str:
        previous = self.elements
        target = None if target_id is None else self.element(target_id)
        if target is None or (target.kind == KIND_PLATE and target.closed):
            target = self._create(kind)
        current = self.element(target.id)
        assert current is not None
        self.elements = tuple(
            replace(item, spans=(*current.spans, span), finished=False)
            if item.id == current.id
            else item
            for item in self.elements
        )
        self.selected = frozenset({current.id})
        self._commit(previous)
        return current.id

    def _create(self, kind: str) -> EditorElement:
        if kind == KIND_PLATE:
            plate = self.plate()
            if plate is not None and not plate.closed:
                return plate
            element = EditorElement(self._next("plate"), KIND_PLATE, name="Bodenplatte")
        else:
            number = 1 + max((item.lane or 0 for item in self.lanes()), default=0)
            element = EditorElement(
                self._next("lane"), KIND_LANE, lane=number, name=f"Spur {number}"
            )
        self.elements = (*self.elements, element)
        return element

    def _finish_kind(self, kind: str) -> None:
        previous = self.elements
        changed = False
        elements: list[EditorElement] = []
        for item in self.elements:
            if item.kind == kind and item.spans and not item.finished and not item.closed:
                elements.append(replace(item, finished=True))
                changed = True
            else:
                elements.append(item)
        if changed:
            self.elements = tuple(elements)
            self._commit(previous)

    def preview_span(self) -> SlotSpan | None:
        """The rubber band. It is not stored until the next click."""
        if self._pen is None or self.hover is None or not self._pen.points:
            return None
        points = self._pen.points
        end = self.hover
        if self.tool in {TOOL_LINE, TOOL_PLATE, TOOL_LANE, TOOL_START}:
            if _distance(points[-1], end) <= POSITION_GAP_MM:
                return None
            return SlotSpan.line(points[-1][0], points[-1][1], end[0], end[1])
        if self.tool == TOOL_ARC and len(points) == 1:
            return SlotSpan.line(points[0][0], points[0][1], end[0], end[1])
        if self.tool == TOOL_ARC and len(points) >= 2:
            return arc_through(points[0], end, points[1])
        if self.tool == TOOL_BEZIER and len(points) == 1:
            return SlotSpan.line(points[0][0], points[0][1], end[0], end[1])
        if self.tool == TOOL_BEZIER and len(points) >= 2:
            return _bezier_bulge(points[0], points[1], end)
        return None

    def _open_targets(self) -> frozenset[str]:
        return frozenset(item.id for item in self.lanes() if not item.finished)

    def _commit(self, previous: tuple[EditorElement, ...]) -> None:
        if previous == self.elements:
            return
        self._undo.append(previous)
        if len(self._undo) > _HISTORY:
            del self._undo[0]
        self._redo.clear()
        self._edited = self.elements != self._baseline

    def _replace(self, element_id: str, **changes: Unpack[_ElementPatch]) -> None:
        self.elements = tuple(
            replace(item, **changes) if item.id == element_id else item for item in self.elements
        )

    def _next(self, prefix: str) -> str:
        self._seq += 1
        return f"{prefix}-{self._seq}"

    def _copy(self, item: EditorElement, dx: float, dy: float) -> EditorElement:
        spans = tuple(translate_span(span, dx, dy) for span in item.spans)
        lane = item.lane
        if item.kind == KIND_LANE:
            lane = 1 + max((other.lane or 0 for other in self.lanes()), default=0)
        return replace(
            item,
            id=self._next(item.kind),
            spans=spans,
            lane=lane,
            x=item.x + dx,
            y=item.y + dy,
            name=_copy_name(item, lane),
        )

    def _transform_selection(self, transform: object) -> None:
        assert callable(transform)
        chosen = [item for item in self.elements if item.id in self.selected and not item.locked]
        if not chosen:
            return
        origin = _centroid(chosen)
        previous = self.elements
        mapped = {item.id: transform(item, origin) for item in chosen}
        self.elements = tuple(mapped.get(item.id, item) for item in self.elements)
        self._commit(previous)

    def _anchors(self) -> tuple[tuple[float, float], ...]:
        found: list[tuple[float, float]] = []
        for item in self.elements:
            if not item.visible:
                continue
            if item.kind == KIND_JOINT:
                found.append((item.x, item.y))
            for span in item.spans:
                found.append(span_start(span))
                found.append(span_end(span))
        return tuple(found)

    def _hit_handle(
        self, point: tuple[float, float], tolerance_mm: float
    ) -> tuple[str, int, str] | None:
        best: tuple[str, int, str] | None = None
        best_distance = tolerance_mm
        for item in self.elements:
            if not item.visible or item.locked or (item.id not in self.selected and self.selected):
                continue
            if self.selected and item.id not in self.selected:
                continue
            for index, span in enumerate(item.spans):
                for name, anchor in _handles(span):
                    distance = _distance(point, anchor)
                    if distance <= best_distance:
                        best = (item.id, index, name)
                        best_distance = distance
            if item.kind == KIND_JOINT:
                distance = _distance(point, (item.x, item.y))
                if distance <= best_distance:
                    best = (item.id, 0, "joint")
                    best_distance = distance
        return best

    def _hit_element(self, point: tuple[float, float], tolerance_mm: float) -> str | None:
        best: str | None = None
        best_distance = tolerance_mm
        for item in reversed(self.elements):
            if not item.visible:
                continue
            if item.kind == KIND_JOINT:
                distance = _distance(point, (item.x, item.y))
            else:
                distance = _path_distance(item.spans, point)
            if distance <= best_distance:
                best = item.id
                best_distance = distance
        return best

    def _active_span(self) -> SlotSpan | None:
        if self.active_span is None:
            element = self._single()
            if element is None or len(element.spans) != 1:
                return None
            return element.spans[0]
        element_id, index = self.active_span
        element = self.element(element_id)
        if element is None or not 0 <= index < len(element.spans):
            return None
        return element.spans[index]

    def _active_point(self) -> tuple[float, float] | None:
        if self.active_handle == "joint":
            element = self._single()
            if element is None:
                return None
            return (element.x, element.y)
        span = self._active_span()
        if span is None or self.active_handle is None:
            element = self._single()
            if element is None:
                return None
            if element.kind == KIND_JOINT:
                return (element.x, element.y)
            if element.spans:
                return span_start(element.spans[0])
            return None
        handles = dict(_handles(span))
        return handles.get(self.active_handle)

    def _single(self) -> EditorElement | None:
        if len(self.selected) != 1:
            return None
        return self.element(next(iter(self.selected)))

    def _set_point(self, axis: str, value: float) -> None:
        current = self._active_point()
        if current is None:
            return
        point = (value, current[1]) if axis == "x" else (current[0], value)
        if self.active_handle == "joint" or (
            self.active_handle is None
            and (element := self._single()) is not None
            and element.kind == KIND_JOINT
        ):
            element = self._single()
            if element is None:
                return
            self._replace(element.id, x=point[0], y=point[1])
            return
        if self.active_span is None or self.active_handle is None:
            element = self._single()
            if element is None or current is None:
                return
            self.elements = _moved_elements(
                self.elements, frozenset({element.id}), point[0] - current[0], point[1] - current[1]
            )
            return
        element_id, index, handle = (*self.active_span, self.active_handle)
        self.elements = _moved_handle(self.elements, (element_id, index, handle), point)

    def _set_length(self, length: float) -> None:
        span = self._active_span()
        element = self._single()
        if span is None or span.kind != SPAN_LINE or element is None or length <= 0:
            return
        if self.active_span is None:
            element_id, index = element.id, 0 if len(element.spans) == 1 else -1
            if index < 0:
                return
        else:
            element_id, index = self.active_span
        start = span_start(span)
        end = span_end(span)
        current = _distance(start, end)
        if current <= POSITION_GAP_MM:
            return
        scale = length / current
        point = (start[0] + (end[0] - start[0]) * scale, start[1] + (end[1] - start[1]) * scale)
        self.elements = _moved_handle(self.elements, (element_id, index, "end"), point)

    def _set_radius(self, radius: float) -> None:
        span = self._active_span()
        if span is None or span.kind != SPAN_ARC or radius <= 0 or self.active_span is None:
            return
        element_id, index = self.active_span
        updated = SlotSpan.arc(span.x0, span.y0, radius, span.start_deg, span.sweep_deg)
        self._put_span(element_id, index, updated)

    def _set_sweep(self, sweep: float) -> None:
        span = self._active_span()
        if span is None or span.kind != SPAN_ARC or self.active_span is None:
            return
        element_id, index = self.active_span
        updated = SlotSpan.arc(span.x0, span.y0, span.radius_mm, span.start_deg, sweep)
        self._put_span(element_id, index, updated)

    def _put_span(self, element_id: str, index: int, span: SlotSpan) -> None:
        element = self.element(element_id)
        if element is None or not 0 <= index < len(element.spans):
            return
        spans = list(element.spans)
        spans[index] = span
        self._replace(element_id, spans=tuple(spans))


def validate_path_group(spec: PartSpec) -> None:
    """Stored curves must meet. A start stroke is checked as a path, not as a lane."""
    for path in spec.slot_paths:
        role = "slot" if path.kind == PATH_START else None
        geometry = (
            driving_geometry(path)
            if role is None
            else GeometryPath(tuple(as_segment(span) for span in path.spans), "slot")
        )
        validate_path(geometry)
    for path in spec.contours:
        validate_path(GeometryPath(tuple(as_segment(span) for span in path.spans), "contour"))


def driving_geometry(path: SlotPath) -> GeometryPath:
    from slot_racing.modules.track_planner.parts import geometry_of

    return geometry_of(path)


def _elements_from_spec(spec: PartSpec) -> tuple[EditorElement, ...]:
    elements: list[EditorElement] = []
    if spec.contours:
        spans = spec.contours[0].spans
        elements.append(
            EditorElement(
                "plate-1",
                KIND_PLATE,
                spans=spans,
                closed=is_closed(spans),
                finished=is_closed(spans),
                name="Bodenplatte",
            )
        )
    elif len(spec.outline) >= 3:
        spans = tuple(
            SlotSpan.line(a[0], a[1], b[0], b[1])
            for a, b in zip(spec.outline, (*spec.outline[1:], spec.outline[0]), strict=False)
        )
        elements.append(
            EditorElement(
                "plate-1", KIND_PLATE, spans=spans, closed=True, finished=True, name="Bodenplatte"
            )
        )
    paths = tuple(path for path in spec.slot_paths if path.kind == PATH_SLOT)
    if not paths:
        paths = tuple(
            SlotPath(tuple(span_of(segment) for segment in path.segments), PATH_SLOT, path.lane)
            for path in driving_paths(spec)
        )
    for index, path in enumerate(paths):
        number = path.lane or index + 1
        elements.append(
            EditorElement(
                f"lane-{number}",
                KIND_LANE,
                spans=path.spans,
                lane=number,
                finished=True,
                name=f"Spur {number}",
            )
        )
    for path in spec.slot_paths:
        if path.kind == PATH_START and path.spans:
            elements.append(
                EditorElement(
                    "start-1", KIND_START, spans=path.spans, finished=True, name="Start/Ziel"
                )
            )
    for connector in spec.connectors:
        if connector.kind != TRACK:
            continue
        elements.append(
            EditorElement(
                f"joint-{connector.name}",
                KIND_JOINT,
                x=connector.x_mm,
                y=connector.y_mm,
                direction_deg=connector.direction_deg,
                lanes=connector.lanes,
                finished=True,
                name=connector.name,
            )
        )
    return tuple(elements)


def _sequence_of(elements: tuple[EditorElement, ...]) -> int:
    highest = 0
    for item in elements:
        suffix = item.id.rsplit("-", 1)[-1]
        if suffix.isdigit():
            highest = max(highest, int(suffix))
    return highest


def _handles(span: SlotSpan) -> tuple[tuple[str, tuple[float, float]], ...]:
    segment = as_segment(span)
    if isinstance(segment, CubicBezier):
        names = ("p0", "p1", "p2", "p3")
        return tuple(zip(names, segment.control_points(), strict=True))
    found = [("start", segment.start()), ("end", segment.end())]
    if span.kind == SPAN_ARC:
        found.append(("mid", segment.point_at(0.5)))
    return tuple(found)


def _moved_handle(
    elements: tuple[EditorElement, ...],
    handle: tuple[str, int, str],
    point: tuple[float, float],
) -> tuple[EditorElement, ...]:
    element_id, index, name = handle
    updated: list[EditorElement] = []
    for item in elements:
        if item.id != element_id:
            updated.append(item)
            continue
        if name == "joint":
            updated.append(replace(item, x=point[0], y=point[1]))
            continue
        if not 0 <= index < len(item.spans):
            updated.append(item)
            continue
        spans = list(item.spans)
        spans[index] = _move_span_handle(spans[index], name, point)
        updated.append(replace(item, spans=tuple(spans)))
    return tuple(updated)


def _move_span_handle(span: SlotSpan, handle: str, point: tuple[float, float]) -> SlotSpan:
    from slot_racing.modules.track_planner.editing import move_handle

    if handle == "mid" and span.kind == SPAN_ARC:
        arc = arc_through(span_start(span), point, span_end(span))
        return span if arc is None else arc
    try:
        return move_handle(span, handle, point[0], point[1])
    except ValidationError:
        return span


def _moved_elements(
    elements: tuple[EditorElement, ...],
    selected: frozenset[str],
    dx: float,
    dy: float,
) -> tuple[EditorElement, ...]:
    if dx == 0.0 and dy == 0.0:
        return elements
    moved: list[EditorElement] = []
    for item in elements:
        if item.id not in selected or item.locked:
            moved.append(item)
            continue
        spans = tuple(translate_span(span, dx, dy) for span in item.spans)
        moved.append(replace(item, spans=spans, x=item.x + dx, y=item.y + dy))
    return tuple(moved)


def _rotate_element(
    item: EditorElement, degrees: float, origin: tuple[float, float]
) -> EditorElement:
    spans = tuple(rotate_span(span, degrees, origin) for span in item.spans)
    turned = _rotate_point((item.x, item.y), degrees, origin)
    return replace(
        item,
        spans=spans,
        x=turned[0],
        y=turned[1],
        direction_deg=(item.direction_deg + degrees) % 360.0,
    )


def _mirror_element(item: EditorElement, axis: str, origin: tuple[float, float]) -> EditorElement:
    spans = tuple(_mirror_span(span, axis, origin) for span in item.spans)
    point = _mirror_point((item.x, item.y), axis, origin)
    direction = item.direction_deg
    if item.kind == KIND_JOINT:
        direction = (180.0 - direction) % 360.0 if axis == "x" else (-direction) % 360.0
    return replace(item, spans=spans, x=point[0], y=point[1], direction_deg=direction)


def _mirror_span(span: SlotSpan, axis: str, origin: tuple[float, float]) -> SlotSpan:
    if span.kind == SPAN_BEZIER:
        points = (
            (span.x0, span.y0),
            (span.c1x, span.c1y),
            (span.c2x, span.c2y),
            (span.x1, span.y1),
        )
        mirrored = tuple(_mirror_point(point, axis, origin) for point in points)
        return SlotSpan.bezier(*(value for point in mirrored for value in point))
    if span.kind == SPAN_ARC:
        start = _mirror_point(span_start(span), axis, origin)
        mid = _mirror_point(as_segment(span).point_at(0.5), axis, origin)
        end = _mirror_point(span_end(span), axis, origin)
        arc = arc_through(start, mid, end)
        return SlotSpan.line(start[0], start[1], end[0], end[1]) if arc is None else arc
    start = _mirror_point(span_start(span), axis, origin)
    end = _mirror_point(span_end(span), axis, origin)
    return SlotSpan.line(start[0], start[1], end[0], end[1])


def _mirror_point(
    point: tuple[float, float], axis: str, origin: tuple[float, float]
) -> tuple[float, float]:
    if axis == "y":
        return (point[0], origin[1] - (point[1] - origin[1]))
    return (origin[0] - (point[0] - origin[0]), point[1])


def _rotate_point(
    point: tuple[float, float], degrees: float, origin: tuple[float, float]
) -> tuple[float, float]:
    radians = math.radians(degrees)
    cosine = math.cos(radians)
    sine = math.sin(radians)
    dx = point[0] - origin[0]
    dy = point[1] - origin[1]
    return (origin[0] + dx * cosine - dy * sine, origin[1] + dx * sine + dy * cosine)


def _centroid(elements: Sequence[EditorElement]) -> tuple[float, float]:
    points: list[tuple[float, float]] = []
    for item in elements:
        if item.kind == KIND_JOINT:
            points.append((item.x, item.y))
        for span in item.spans:
            points.append(span_start(span))
            points.append(span_end(span))
    if not points:
        return (0.0, 0.0)
    return (
        sum(point[0] for point in points) / len(points),
        sum(point[1] for point in points) / len(points),
    )


def _bounds(elements: Sequence[EditorElement]) -> tuple[float, float, float, float] | None:
    points: list[tuple[float, float]] = []
    for item in elements:
        for span in item.spans:
            points.append(span_start(span))
            points.append(span_end(span))
    if not points:
        return None
    xs = [point[0] for point in points]
    ys = [point[1] for point in points]
    return (min(xs), min(ys), max(xs), max(ys))


def _path_distance(spans: tuple[SlotSpan, ...], point: tuple[float, float]) -> float:
    if not spans:
        return math.inf
    return min(as_segment(span).closest(point[0], point[1])[1] for span in spans)


def _path_broken(spans: tuple[SlotSpan, ...]) -> bool:
    if not spans:
        return True
    try:
        validate_path(GeometryPath(tuple(as_segment(span) for span in spans), "slot"))
    except ValidationError:
        return True
    return False


def _joint_reach(lane_total: int) -> float:
    """A centre-line joint may stand between the grooves it lists."""
    return 20.0 + max(0, lane_total - 1) * LANE_PITCH_MM / 2.0


def _joint_meets_lanes(joint: EditorElement, lanes: tuple[EditorElement, ...]) -> bool:
    reach = _joint_reach(len(joint.lanes))
    for number in joint.lanes:
        lane = next((item for item in lanes if item.lane == number and item.spans), None)
        if lane is None:
            return False
        ends = (span_start(lane.spans[0]), span_end(lane.spans[-1]))
        if not any(_distance((joint.x, joint.y), end) <= reach for end in ends):
            return False
    return True


def _lanes_for_joint(
    lanes: tuple[EditorElement, ...], point: tuple[float, float]
) -> tuple[tuple[int, ...], float]:
    records: list[tuple[int, float, float, str]] = []
    for lane in lanes:
        if not lane.spans or lane.lane is None:
            continue
        start = span_start(lane.spans[0])
        end = span_end(lane.spans[-1])
        start_distance = _distance(point, start)
        end_distance = _distance(point, end)
        if start_distance <= end_distance:
            direction = (as_segment(lane.spans[0]).tangent_deg(0.0) + 180.0) % 360.0
            records.append((lane.lane, start_distance, direction, "start"))
        else:
            direction = as_segment(lane.spans[-1]).tangent_deg(1.0) % 360.0
            records.append((lane.lane, end_distance, direction, "end"))
    if not records:
        return (), 0.0
    nearest = min(records, key=lambda item: item[1])
    side = nearest[3]
    sided = [item for item in records if item[3] == side]
    reach = _joint_reach(len(sided))
    chosen = [item for item in sided if item[1] <= reach]
    if not chosen and nearest[1] <= 40.0:
        chosen = [nearest]
    numbers = tuple(item[0] for item in sorted(chosen, key=lambda item: item[0]))
    return numbers, nearest[2]


def _next_joint_name(elements: Sequence[EditorElement]) -> str:
    used = {item.name for item in elements if item.kind == KIND_JOINT}
    for name in ("a", "b", "c", "d", "e", "f", "g", "h"):
        if name not in used:
            return name
    return f"j{len(used) + 1}"


def _parse_lanes(text: str) -> tuple[int, ...]:
    found: list[int] = []
    for piece in text.replace(";", ",").split(","):
        item = piece.strip()
        if not item:
            continue
        value = int(item)
        if value < 1:
            raise ValidationError("error.planner.geometry")
        found.append(value)
    return tuple(found)


def _copy_name(item: EditorElement, lane: int | None) -> str:
    if item.kind == KIND_LANE and lane is not None:
        return f"Spur {lane}"
    return item.name


def _bezier_bulge(
    start: tuple[float, float], end: tuple[float, float], via: tuple[float, float]
) -> SlotSpan:
    dx = end[0] - start[0]
    dy = end[1] - start[1]
    chord = math.hypot(dx, dy) or 1.0
    px, py = -dy / chord, dx / chord
    bulge = (via[0] - (start[0] + end[0]) / 2.0) * px + (via[1] - (start[1] + end[1]) / 2.0) * py
    first = (start[0] + dx / 3.0 + px * bulge, start[1] + dy / 3.0 + py * bulge)
    second = (start[0] + 2.0 * dx / 3.0 + px * bulge, start[1] + 2.0 * dy / 3.0 + py * bulge)
    return SlotSpan.bezier(
        start[0], start[1], first[0], first[1], second[0], second[1], end[0], end[1]
    )
