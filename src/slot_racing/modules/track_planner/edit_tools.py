"""Tool logic of the graphical part editor, in millimetres. No Qt in here.

The canvas turns mouse events into world positions and a pick tolerance. This controller
decides what a press, a drag or a release means for the current tool. Every change goes
through :class:`EditSession`, so undo, redo and the dirty check see it.

A change that cannot be built (a degenerate arc, a lane that does not exist, a join that
needs a gap bridged) leaves the part untouched and sets :attr:`message` to the error key.
"""

from __future__ import annotations

import math
from collections.abc import Callable
from dataclasses import dataclass

from slot_racing.core.errors import ValidationError
from slot_racing.modules.track_planner import editing as ed
from slot_racing.modules.track_planner.edit_session import (
    KIND_CONNECTOR,
    KIND_PATH,
    KIND_POINT,
    KIND_SEGMENT,
    EditSession,
    Snapped,
    Snapping,
    Target,
    hit_targets,
    next_target,
    snap_point,
)
from slot_racing.modules.track_planner.geometry import POSITION_GAP_MM
from slot_racing.modules.track_planner.parts import (
    SPAN_ARC,
    SPAN_BEZIER,
    SPAN_LINE,
    PartSpec,
    SlotSpan,
    as_segment,
)

TOOL_SELECT = "select"
TOOL_MOVE = "move"
TOOL_LINE = "line"
TOOL_ARC = "arc"
TOOL_BEZIER = "bezier"
TOOL_ADD_POINT = "add_point"
TOOL_REMOVE_POINT = "remove_point"
TOOL_SPLIT = "split"
TOOL_DELETE = "delete_segment"
TOOL_CONNECTOR = "connector"
TOOL_EDGE = "edge"
TOOL_CONTOUR = "contour"
TOOLS = (
    TOOL_SELECT,
    TOOL_MOVE,
    TOOL_LINE,
    TOOL_ARC,
    TOOL_BEZIER,
    TOOL_ADD_POINT,
    TOOL_REMOVE_POINT,
    TOOL_SPLIT,
    TOOL_DELETE,
    TOOL_CONNECTOR,
    TOOL_EDGE,
    TOOL_CONTOUR,
)
DRAW_TOOLS = (TOOL_LINE, TOOL_ARC, TOOL_BEZIER)

_DRAG_FRACTION = 0.3


@dataclass(slots=True)
class _Drag:
    base: PartSpec
    target: Target
    grab: tuple[float, float]
    offset: tuple[float, float]
    started: bool = False


class EditController:
    """What the pointer does, given the tool, the layer and the snapping aids."""

    def __init__(self, session: EditSession) -> None:
        self.session = session
        self.tool = TOOL_SELECT
        self.layer = ed.LAYER_GROOVE
        self.edge_slot = "left"
        self.snapping = Snapping()
        self.selection: Target | None = None
        self.message = ""
        self.pending: list[tuple[float, float]] = []
        self.chain: ed.PathRef | None = None
        self.hover: Snapped | None = None
        self.on_message: Callable[[str], None] | None = None
        self.on_selection: Callable[[], None] | None = None
        self._reverse_first = False
        self._drag: _Drag | None = None
        self._last_click: tuple[float, float] | None = None
        session.listeners.append(self._session_changed)

    # ------------------------------------------------------------------ tool state

    @property
    def spec(self) -> PartSpec:
        return self.session.spec

    @property
    def drawing(self) -> bool:
        return bool(self.pending) or self.chain is not None

    def set_tool(self, tool: str) -> None:
        if tool not in TOOLS:
            raise ValidationError("error.planner.geometry")
        self.cancel()
        self.tool = tool
        if tool == TOOL_CONTOUR:
            self.layer = ed.LAYER_CONTOUR
        elif tool == TOOL_EDGE:
            attached = self.spec.attachment is not None
            self.layer = ed.LAYER_ATTACH if attached else ed.LAYER_EDGE
        elif tool == TOOL_CONNECTOR:
            self.hover = None
        self._say("")

    def set_layer(self, layer: str) -> None:
        if layer not in ed.LAYERS:
            raise ValidationError("error.planner.geometry")
        if layer != self.layer:
            self.cancel()
        self.layer = layer

    def pick_layers(self) -> set[str]:
        if self.tool == TOOL_CONNECTOR:
            return set()
        if self.tool == TOOL_CONTOUR:
            return {ed.LAYER_CONTOUR}
        if self.tool == TOOL_EDGE:
            return {ed.LAYER_EDGE, ed.LAYER_ATTACH}
        return set(ed.LAYERS)

    def _picks_connectors(self) -> bool:
        return self.tool not in {TOOL_CONTOUR, TOOL_EDGE}

    # ------------------------------------------------------------------ selection

    def select(self, target: Target | None) -> None:
        self.selection = target
        if self.on_selection is not None:
            self.on_selection()

    def select_path(self, ref: ed.PathRef) -> None:
        self.select(Target(KIND_PATH, ref))

    def _normalized(self, target: Target | None) -> Target | None:
        if target is None:
            return None
        spec = self.spec
        if target.kind == KIND_CONNECTOR:
            return target if 0 <= target.connector < len(spec.connectors) else None
        if target.path is None:
            return None
        try:
            spans = ed.spans_of(spec, target.path)
        except ValidationError:
            return None
        if target.kind == KIND_PATH:
            return target
        if not 0 <= target.segment < len(spans):
            return Target(KIND_PATH, target.path)
        if target.kind == KIND_POINT and target.handle not in ed.handle_points(
            spans[target.segment]
        ):
            return Target(KIND_SEGMENT, target.path, target.segment)
        return target

    def _session_changed(self) -> None:
        fixed = self._normalized(self.selection)
        if fixed != self.selection:
            self.selection = fixed
            if self.on_selection is not None:
                self.on_selection()
        if self.chain is not None:
            try:
                ed.spans_of(self.spec, self.chain)
            except ValidationError:
                self.chain = None
                self.pending = []

    def selected_span(self) -> SlotSpan | None:
        target = self.selection
        if target is None or target.path is None or target.kind == KIND_PATH:
            return None
        try:
            return ed.spans_of(self.spec, target.path)[target.segment]
        except (ValidationError, IndexError):
            return None

    # ------------------------------------------------------------------ pointer

    def hits(self, x: float, y: float, tolerance: float, arrow_mm: float = 40.0) -> list[Target]:
        return hit_targets(
            self.spec,
            x,
            y,
            tolerance,
            layers=self.pick_layers(),
            connectors=self._picks_connectors(),
            arrow_mm=arrow_mm,
        )

    def press(self, x: float, y: float, tolerance: float, arrow_mm: float = 40.0) -> None:
        self._say("")
        tool = self.tool
        if tool in DRAW_TOOLS:
            self._draw_click(x, y, tolerance)
            return
        found = self.hits(x, y, tolerance, arrow_mm)
        if tool in {TOOL_SELECT, TOOL_MOVE, TOOL_CONNECTOR, TOOL_EDGE, TOOL_CONTOUR}:
            self._press_select(x, y, tolerance, found)
        elif tool == TOOL_ADD_POINT:
            self._add_point(x, y, tolerance)
        elif tool == TOOL_REMOVE_POINT:
            self._remove_point(found)
        elif tool == TOOL_SPLIT:
            self._split(x, y, found)
        elif tool == TOOL_DELETE:
            self._delete_segment(found)

    def move(
        self,
        x: float,
        y: float,
        tolerance: float,
        *,
        pressed: bool,
    ) -> None:
        if self._drag is not None and pressed:
            self._drag_to(x, y, tolerance)
            return
        if self.tool in DRAW_TOOLS or self.drawing:
            self.hover = self._snap(x, y, tolerance, self._anchor(), self._heading())
        else:
            self.hover = None

    def release(self, x: float, y: float, tolerance: float) -> None:
        drag = self._drag
        self._drag = None
        if drag is None:
            return
        if drag.started:
            self.session.commit()

    def cancel(self) -> None:
        """Escape: drop a drag or finish the stroke being drawn."""
        if self._drag is not None and self._drag.started:
            self.session.abort()
        self._drag = None
        self.pending = []
        self.chain = None
        self._reverse_first = False
        self.hover = None

    def finish_stroke(self) -> None:
        self.pending = []
        self.chain = None
        self._reverse_first = False

    # ------------------------------------------------------------------ select and drag

    def _press_select(self, x: float, y: float, tolerance: float, found: list[Target]) -> None:
        same_spot = (
            self._last_click is not None
            and math.hypot(x - self._last_click[0], y - self._last_click[1]) <= tolerance * 0.5
        )
        chosen = next_target(found, self.selection, cycle=same_spot)
        self._last_click = (x, y)
        self.select(chosen)
        self._drag = None
        if chosen is None:
            return
        movable = chosen.kind in {KIND_POINT, KIND_CONNECTOR}
        if self.tool == TOOL_MOVE and chosen.kind == KIND_SEGMENT:
            movable = True
        if (
            self.tool in {TOOL_SELECT, TOOL_MOVE, TOOL_CONNECTOR, TOOL_EDGE, TOOL_CONTOUR}
            and movable
        ):
            origin = self._anchor_of(chosen)
            offset = (x - origin[0], y - origin[1]) if chosen.kind == KIND_POINT else (0.0, 0.0)
            self._drag = _Drag(self.spec, chosen, (x, y), offset)

    def _anchor_of(self, target: Target) -> tuple[float, float]:
        spec = self.spec
        if target.kind == KIND_CONNECTOR:
            item = spec.connectors[target.connector]
            return (item.x_mm, item.y_mm)
        if target.path is None:
            return (0.0, 0.0)
        spans = ed.spans_of(spec, target.path)
        if target.kind == KIND_POINT:
            return ed.handle_points(spans[target.segment])[target.handle]
        return ed.span_start(spans[0])

    def _drag_to(self, x: float, y: float, tolerance: float) -> None:
        drag = self._drag
        if drag is None:
            return
        if not drag.started:
            if math.hypot(x - drag.grab[0], y - drag.grab[1]) < tolerance * _DRAG_FRACTION:
                return
            drag.started = True
        target = drag.target
        base = drag.base
        try:
            if target.kind == KIND_POINT and target.path is not None:
                start = self._anchor_in(base, target)
                snapped = self._snap(
                    x - drag.offset[0],
                    y - drag.offset[1],
                    tolerance,
                    None,
                    None,
                    skip=[start],
                    layers=None,
                )
                point = ed.PointRef(target.path, target.segment, target.handle)
                updated = ed.move_point(base, point, snapped.x, snapped.y)
            elif target.kind == KIND_CONNECTOR:
                updated = self._drag_connector(base, target, x, y, tolerance)
            elif target.path is not None:
                origin = ed.span_start(ed.spans_of(base, target.path)[0])
                snapped = self._snap(
                    origin[0] + x - drag.grab[0],
                    origin[1] + y - drag.grab[1],
                    tolerance,
                    None,
                    None,
                    skip=[origin],
                )
                updated = ed.translate_path(
                    base, target.path, snapped.x - origin[0], snapped.y - origin[1]
                )
            else:
                return
        except ValidationError as error:
            self._say(error.key)
            return
        self.hover = None
        self.session.live(updated)

    def _anchor_in(self, spec: PartSpec, target: Target) -> tuple[float, float]:
        assert target.path is not None
        return ed.handle_points(ed.spans_of(spec, target.path)[target.segment])[target.handle]

    def _drag_connector(
        self, base: PartSpec, target: Target, x: float, y: float, tolerance: float
    ) -> PartSpec:
        item = base.connectors[target.connector]
        if target.handle == "tip":
            heading = math.degrees(math.atan2(y - item.y_mm, x - item.x_mm))
            if self.snapping.angle and self.snapping.angle_step_deg > 0.0:
                step = self.snapping.angle_step_deg
                heading = round(heading / step) * step
            return ed.set_connector(base, target.connector, direction_deg=heading)
        snapped = self._snap(
            x,
            y,
            tolerance,
            None,
            None,
            skip=[(item.x_mm, item.y_mm)],
        )
        return ed.set_connector(base, target.connector, x_mm=snapped.x, y_mm=snapped.y)

    # ------------------------------------------------------------------ snapping

    def _snap(
        self,
        x: float,
        y: float,
        tolerance: float,
        anchor: tuple[float, float] | None,
        heading: float | None,
        *,
        skip: list[tuple[float, float]] | None = None,
        layers: set[str] | None = None,
    ) -> Snapped:
        return snap_point(
            self.spec,
            x,
            y,
            self.snapping,
            tolerance,
            anchor=anchor,
            heading_deg=heading,
            skip=skip or (),
            layers=layers,
        )

    def _chain_end(self) -> tuple[tuple[float, float], float] | None:
        """Where the open path being extended ends, and the heading it leaves in."""
        if self.chain is None:
            return None
        try:
            spans = ed.spans_of(self.spec, self.chain)
        except ValidationError:
            return None
        if self._reverse_first:
            return ed.span_start(spans[0]), (as_segment(spans[0]).tangent_deg(0.0) + 180.0) % 360.0
        return ed.span_end(spans[-1]), as_segment(spans[-1]).tangent_deg(1.0)

    def _anchor(self) -> tuple[float, float] | None:
        if self.pending:
            return self.pending[-1]
        end = self._chain_end()
        return None if end is None else end[0]

    def _heading(self) -> float | None:
        if self.pending:
            return None
        end = self._chain_end()
        return None if end is None else end[1]

    # ------------------------------------------------------------------ drawing

    def _draw_click(self, x: float, y: float, tolerance: float) -> None:
        point = self._snap(x, y, tolerance, self._anchor(), self._heading())
        spot = (point.x, point.y)
        if not self.pending and self.chain is None:
            self._begin_stroke(spot)
            return
        needed = 2 if self.tool == TOOL_ARC else 1
        if self.chain is not None and not self.pending:
            start = self._anchor()
            assert start is not None
            needed_more = needed
            via: list[tuple[float, float]] = []
        else:
            start = self.pending[0]
            via = self.pending[1:]
            needed_more = needed - len(via)
        if needed_more > 1:
            self.pending.append(spot)
            return
        try:
            span = self._build_span(start, via, spot)
        except ValidationError as error:
            self._say(error.key)
            return
        self._add_span(span)

    def _begin_stroke(self, spot: tuple[float, float]) -> None:
        self.pending = [spot]
        self._reverse_first = False
        found = self._extendable(spot)
        if found is None:
            return
        ref, at_start = found
        self.chain = ref
        self.pending = []
        self._reverse_first = at_start

    def _extendable(self, spot: tuple[float, float]) -> tuple[ed.PathRef, bool] | None:
        """An open path of the drawing layer that ends at ``spot``."""
        layer = self.layer
        for ref in ed.path_refs(self.spec):
            if ref.layer != layer:
                continue
            spans = ed.spans_of(self.spec, ref)
            if ed.is_closed(spans):
                continue
            first = ed.span_start(spans[0])
            last = ed.span_end(spans[-1])
            if math.hypot(last[0] - spot[0], last[1] - spot[1]) <= POSITION_GAP_MM:
                return ref, False
            if math.hypot(first[0] - spot[0], first[1] - spot[1]) <= POSITION_GAP_MM:
                return ref, True
        return None

    def _build_span(
        self,
        start: tuple[float, float],
        via: list[tuple[float, float]],
        end: tuple[float, float],
    ) -> SlotSpan:
        if math.hypot(end[0] - start[0], end[1] - start[1]) <= POSITION_GAP_MM:
            raise ValidationError("error.planner.geometry")
        if self.tool == TOOL_LINE:
            return SlotSpan.line(start[0], start[1], end[0], end[1])
        if self.tool == TOOL_ARC:
            arc = ed.arc_through(start, via[0], end)
            if arc is None:
                raise ValidationError("error.planner.editor.arc_points")
            return arc
        return ed.bezier_between(start, end, None if self.pending else self._heading())

    def _add_span(self, span: SlotSpan) -> None:
        spec = self.spec
        try:
            if self.chain is None:
                if self.layer == ed.LAYER_ATTACH and spec.attachment is not None:
                    existing = spec.attachment.edge
                    if existing:
                        raise ValidationError("error.planner.editor.attach_edge")
                updated, ref = ed.add_path(spec, self.layer, (span,), slot=self.edge_slot)
                index = 0
            else:
                ref = self.chain
                if self._reverse_first:
                    spec = ed.reverse_path(spec, ref)
                    self._reverse_first = False
                updated = ed.append_span(spec, ref, span)
                index = len(ed.spans_of(updated, ref)) - 1
        except ValidationError as error:
            self._say(error.key)
            self.pending = []
            return
        self.session.apply(updated)
        self.chain = ref
        self.pending = []
        self.select(Target(KIND_SEGMENT, ref, index))

    def rubber_band(self) -> tuple[SlotSpan, ...]:
        """The stroke as it would be created if the next click landed at the hover position."""
        hover = self.hover
        if hover is None or self.tool not in DRAW_TOOLS:
            return ()
        end = (hover.x, hover.y)
        if self.chain is not None and not self.pending:
            start = self._anchor()
            via: list[tuple[float, float]] = []
        elif self.pending:
            start = self.pending[0]
            via = self.pending[1:]
        else:
            return ()
        if start is None:
            return ()
        try:
            if self.tool == TOOL_ARC and not via:
                return (SlotSpan.line(start[0], start[1], end[0], end[1]),)
            return (self._build_span(start, via, end),)
        except ValidationError:
            return ()

    # ------------------------------------------------------------------ edit tools

    def _add_point(self, x: float, y: float, tolerance: float) -> None:
        found = [
            item
            for item in hit_targets(
                self.spec,
                x,
                y,
                tolerance,
                layers=self.pick_layers(),
                connectors=False,
            )
            if item.kind == KIND_SEGMENT and item.path is not None
        ]
        if not found:
            return
        target = found[0]
        path = target.path
        if path is None:
            return
        segment = target.segment
        if self.run(lambda spec: ed.split_at_point(spec, path, segment, x, y)):
            self.select(Target(KIND_POINT, path, segment, "end"))

    def _split(self, x: float, y: float, found: list[Target]) -> None:
        node = next(
            (
                item
                for item in found
                if item.kind == KIND_POINT
                and item.path is not None
                and item.handle in {"end", "p3"}
                and item.segment < len(ed.spans_of(self.spec, item.path)) - 1
            ),
            None,
        )
        if node is not None and node.path is not None:
            path, joint = node.path, node.segment
            self.run_with_ref(lambda spec: ed.cut_path(spec, path, joint))
            return
        line = next((i for i in found if i.kind == KIND_SEGMENT and i.path is not None), None)
        if line is not None and line.path is not None:
            path, segment = line.path, line.segment
            self.run(lambda spec: ed.split_at_point(spec, path, segment, x, y))

    def _remove_point(self, found: list[Target]) -> None:
        item = next((i for i in found if i.kind == KIND_POINT and i.path is not None), None)
        if item is None or item.path is None:
            return
        path, segment, handle = item.path, item.segment, item.handle
        count = len(ed.spans_of(self.spec, path))
        if handle in {"p1", "p2"}:
            self.run(lambda spec: ed.reset_handle(spec, path, segment, handle))
        elif handle in {"center", "mid"}:
            self._say("error.planner.editor.remove_point")
        elif handle in {"start", "p0"}:
            self.run(lambda spec: ed.remove_node(spec, path, 0))
        elif segment >= count - 1:
            self.run(lambda spec: ed.delete_span(spec, path, segment))
        else:
            self.run(lambda spec: ed.remove_node(spec, path, segment + 1))

    def _delete_segment(self, found: list[Target]) -> None:
        item = next(
            (i for i in found if i.kind in {KIND_SEGMENT, KIND_POINT} and i.path is not None),
            None,
        )
        if item is None or item.path is None:
            return
        path, segment = item.path, item.segment
        self.run(lambda spec: ed.delete_span(spec, path, segment))

    def delete_selection(self) -> None:
        """The Delete key: remove what is selected."""
        target = self.selection
        if target is None:
            return
        if target.kind == KIND_CONNECTOR:
            index = target.connector
            self.run(lambda spec: ed.remove_connector(spec, index))
            return
        if target.path is None:
            return
        path, segment = target.path, target.segment
        if target.kind == KIND_PATH:
            if path.layer == ed.LAYER_GROOVE:
                lane = self.spec.slot_paths[path.index].lane
                if lane:
                    self.run(lambda spec: ed.remove_lane(spec, lane))
                    return
            self.run(lambda spec: ed.remove_path(spec, path))
            return
        self.run(lambda spec: ed.delete_span(spec, path, segment))

    # ------------------------------------------------------------------ running changes

    def run(self, change: Callable[[PartSpec], PartSpec]) -> bool:
        """Build a new spec from the current one. An invalid change is reported, not applied."""
        self.session.commit()
        try:
            updated = change(self.spec)
        except ValidationError as error:
            self._say(error.key)
            return False
        return self.session.apply(updated)

    def run_with_ref(self, change: Callable[[PartSpec], tuple[PartSpec, ed.PathRef]]) -> bool:
        self.session.commit()
        try:
            updated, ref = change(self.spec)
        except ValidationError as error:
            self._say(error.key)
            return False
        applied = self.session.apply(updated)
        self.select_path(ref)
        return applied

    def _say(self, key: str) -> None:
        self.message = key
        if self.on_message is not None:
            self.on_message(key)


def span_kind_label(span: SlotSpan) -> str:
    if span.kind == SPAN_ARC:
        return "arc"
    if span.kind == SPAN_BEZIER:
        return "bezier"
    if span.kind == SPAN_LINE:
        return "line"
    return span.kind
