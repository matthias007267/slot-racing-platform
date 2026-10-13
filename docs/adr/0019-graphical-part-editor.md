# 0019 Graphical part editor withdrawn

## Context

The parametric editor (ADR 0018) builds simple straights, curves and borders. Complex bodies
stay locked there: a squeeze, a chicane, a switch, a lane change, a crossing, a pit part,
Bézier grooves and a freehand special. A graphical editor was added on the same `PartSpec`,
with a drawing tab and twelve mouse tools.

Manual use on Windows showed serious interaction problems and a repeated
`IndexError: tuple index out of range` at `smooth = report[joint][1] <= 1.0` when a joint
handle was selected. The geometry, the storage and the revision model were not the cause.

## Decision

The graphical surface is removed completely. `PartDialog` is only the parametric form. There
is no drawing tab. A new element-oriented point-and-click editor is planned and is **not
implemented**.

Removed with the surface:

- `ui/part_graphic.py` and `ui/part_canvas.py`
- `edit_tools.py` and `edit_session.py` (the twelve tools, hit tests, editor snaps, dialog undo)
- `graphic_translations.py`
- the tools themselves: select, move, line, arc, Bézier, add point, remove point, split
  segment, delete segment, rail joint, border edge and contour

Kept, because later drawing and the planner still need them:

- `geometry.py`: line segments, circular arcs, cubic Bézier curves and composite paths
- grooves, outer contours, border edges, rail joints, border attachments and lane assignment
- transforms, length, painting and planner docking
- `parts.py`, `attachment.py`, `figure.py`, `track_paint.py`, `lane_length.py`, `document.py`
- the parametric editor
- the revision model from ADR 0016 (`lineage_id`, `revision`, `catalog_key`, provenance,
  current and historical rows, explicit adoption). Saved plans and archives are not rewritten
- `editing.py`, the Qt-free operations on a frozen spec. Nothing in the application imports
  it until a new editor exists

No new geometry format. No new table. No migration.

**Complex parts.** Opening a part does not write. A save that does not change geometry copies
the stored spec and only replaces designation, article number, manufacturer and description.
Geometry fields are disabled when the form cannot express the body, and the notice says that
graphical editing is not available. A damaged `slot_paths` list stays read only; a duplicate
of that row cannot be saved. The form never rebuilds a switch, a crossing or a Bézier groove
into a simple straight.

Rail joints and border edges stay separate compatibility rules. The planner does not close a
track and does not move neighbours.

## Consequences

Simple straights, curves and borders can still be created, edited, duplicated and placed.
Complex catalogue and custom parts still load, paint and sit in a plan, and their stored
control points stay byte-for-byte the same when only texts change.

What is temporarily unavailable: drawing or reshaping a part with the mouse, the twelve
tools, the drawing-tab property bar, and editor-local undo. Those are not hidden; their
implementation is gone. The replacement editor does not exist yet.
