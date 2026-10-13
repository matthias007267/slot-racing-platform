# 0019 Graphical part editor

## Context

The parametric editor (ADR 0018) builds simple straights, curves and borders. Everything
else stays locked there: lanes with a squeeze, a chicane, Bézier grooves and own border
shapes. Users need to draw those parts with the mouse on the same definition, the same
millimetre coordinates (+Y down, clockwise rotation, 0° along +x) and the same revision
rules (ADR 0016), without a second part format.

## Decision

`PartDialog` has two tabs on one dialog: the form and a drawing tab. Both read and write one
`PartSpec`. The drawing tab keeps a dialog-local undo history (`EditSession`). The planner's
page-level undo is not reused, because it stores whole plans and a part edit must not enter
it. A drag shows its result live and writes exactly one history step on release. No database
write happens before the save button.

The model is Qt free: `editing.py` (pure functions on frozen specs), `edit_session.py`
(history, hit tests, snapping), `edit_tools.py` (the twelve tools) and the existing
`geometry.py` from ADR 0017. Only `ui/part_canvas.py` and `ui/part_graphic.py` use Qt.

**Lossless switch.** The drawing tab starts from the part with its grooves written out. The
session counts as changed only when that differs. A saved part is the drawn spec when the
session changed, the stored spec when the part is locked, and the form result otherwise. A
form field that no longer applies to a drawn part is disabled and the form says so. Changing
only texts in the form keeps every control point. A drawn part that the form can express
exactly can be handed back with an explicit, confirmed button. That conversion rebuilds the
canonical form: grooves, dashed centre lines and the side edges. It is never done silently.
A drawn part the form cannot express becomes category `special`; its radius and length stop
claiming a standard shape.

**Parts that cannot be drawn.** A switch, lane change, crossing and pit part have branching
grooves, and the planner has no switching logic. They are shown read only with the reason.
Their texts stay editable and their geometry is never written. A damaged stored geometry is
shown read only and never replaced; a text change writes the original JSON back.

**Lanes and lengths.** Lanes are numbered grooves, at most four. A groove is a chain of
lines, arcs and cubic Béziers. Kinks are allowed and only reported. A gap between two
segments blocks saving and can be closed. A groove end must meet a rail joint plane within
1 mm and leave within 5° of the joint direction. A rail joint lists the lanes that pass it.
A lane length is shown only for a simple through route; a part whose routes branch is
refused instead of getting a wrong unique length.

**Contours.** Outer contours are closed chains of lines, arcs and Béziers. The first closed
contour is the outline. More contours are stored as holes and, as in ADR 0017, not cut out
of the fill; the editor says so. An open contour blocks saving, except a single open arc,
which is a stroke of a curve edge.

**Borders.** A standard border is converted to a custom border only after a confirmation.
A border has no rail joint. It fits a host only when its attachment edge equals a lateral
`BorderEdge` of that host (`attachment_fits`), so rotation, translation and the host's
name or ID play no role. A custom host receives side edges with one button. The edge can be
copied from the host, and a strip contour can be drawn beside it.

**Four separate snaps.** (1) the editor grid, (2) point, tangent and angle snaps inside a
part, (3) the rail snap in the planner and (4) the border snap in the planner. The first
two live in `edit_session.snap_point` and can each be switched off. The planner snaps are
never referenced by the editor modules; a test reads the import tree to keep it so.

Hit tests use a pixel distance divided by the view scale, so they follow the zoom. Handles
come first, then joints, then segments. A repeated click on the same spot cycles through
overlapping items.

**Persistence.** Saving goes through the existing revision model. A part that plans use
forks a new revision; old plans, archives and stock stay as they are; adoption stays an
explicit choice per track; neighbours are not adjusted. `PartDraft` gains `centerlines` so a
stored straight or curve with its dashed centre lines is still recognised as simple, and a
form save writes them back unchanged.

No new table and no migration.

## Consequences

The acceptance cases work through the dialog: an own squeeze, a chicane with Bézier control
points, an own border for a compatible part, and a graphically edited Carrera squeeze that
forks a revision.

Limits: switches and crossings cannot be drawn. Holes are stored but not cut out. A Bézier
strip beside an edge is sampled into lines. A Split on a groove makes a new lane; adding a
point splits a segment. Join pairs the nearest open ends. Moving a groove does not move its
dashed centre line, so such a part is no longer expressible in the form and stays a special
part. Closing a track, `.slotpart` exchange, 3D and physics are out of scope.
