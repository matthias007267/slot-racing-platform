# 0019 Graphical floor-plate editor

## Context

The parametric editor (ADR 0018) builds simple straights, curves and borders. Complex bodies
stay locked there: a squeeze, a chicane, a switch, a lane change, a crossing, a pit part,
Bézier grooves and a freehand special.

A first graphical editor was added on the same `PartSpec`, with a drawing tab and twelve
mouse tools. Manual use on Windows showed serious interaction problems and a repeated
`IndexError: tuple index out of range` at `smooth = report[joint][1] <= 1.0` when a joint
handle was selected. The geometry, the storage and the revision model were not the cause.
That surface was removed: `ui/part_graphic.py`, `ui/part_canvas.py`, `edit_tools.py`,
`edit_session.py`, `graphic_translations.py`, and the twelve tools.

The geometry those tools used is still the model a later editor must call.

## Decision

`PartDialog` has two tabs. **Parameter** is the parametric editor and is unchanged.
**Grafisch** is a point-and-click editor for one floor plate and its driving lanes. Switching tabs does not
rewrite geometry and does not create a revision.

The Qt-free session is `PlateSession` in `plate_session.py`. It owns the contour, the lane
paths, the active element, the active lane, the selected corner, point or segment, the hover
preview and one undo stack. Hover and an in-progress drag are not geometry. Undo and redo
restore the plate and the lanes together. There is no second session and no tool-controller
framework.

The Qt widget is `ui/plate_editor.py`. It paints the millimetre grid, handles the mouse and
the keyboard, zooms and pans, and shows the property fields. A click asks the session what
should change. A closed plate is painted with `paint_part`. An open chain is a local
preview stroke and is not a stored path.

A plate is a closed line contour built with `contour_from_polygon` and `outline_from_spans`.
One part has one plate. A second plate is refused until the user replaces the first; replace
is one undo step. Closing needs at least three distinct corners, no zero-length edge, a
usable area and no proper self-intersection. Invalid input is reported and is not written.

**Drafts are not drivable parts.** A plate is stored as a normal `PartSpec` with
`category="draft"`, one closed line contour and an outline. Up to four driving lanes are
stored as `slot_paths` of kind `slot`, numbered from 1. A lane is an open chain of
`LineSegment` and `ArcSegment` values. There are no connectors, no border edges and no
centre lines. `draft` is not a `build_part` category. `driving_paths` and the figure do not
invent lanes when none are stored. The planner palette omits the draft, and `place_instance`
rejects it. The library still lists it, so it can be opened again. Complete-part validation
is unchanged. No table and no migration: the category column and the path JSON already exist.

A lane is drawn by clicks. Each new connection is a straight segment. The mouse preview is
not stored. Finishing a lane does not add a segment back to the start. A finished straight
segment can become one circular arc through `arc_between`: start, end, radius, a right or
left turn, and the short or long arc. The short arc is at most a half turn. The radius must
be at least half the chord. A smaller radius is refused and the previous span stays. Moving
an endpoint keeps that radius and that variant; if they no longer fit, the move is refused
and the span stays an arc. Converting the arc back to a line keeps the endpoints. A point
can be inserted into a straight segment or an interior point between two straight segments
can be removed. Those two actions stay disabled on an arc. The shown distance of a selected
point to another lane or to the plate edge is a point-to-path distance, not a constant pitch.
Lane length is the sum of the existing segment lengths.

A pure line plate, with or without these line and arc lanes, opens back into the same
points without rounding the stored coordinates. Any other body — Bézier curves, centre
lines, joints, borders, or lanes that are not a continuous line/arc chain — is shown with
`paint_part` and the point tools are disabled. Nothing simplifies that body into lines or
arcs. A lane that leaves the plate is reported and the part remains a draft.

What this phase does not draw: rail joints, border joints, border pieces, a centre line, a
start/finish line, automatic lane pitch, a constant-offset copy of a lane, track closure,
`.slotpart` import or export.

## Consequences

A new part can be given one closed floor plate and up to four open lanes of lines and arcs,
edited by click or by millimetre fields, saved, and opened again with the same geometry.
Carrera parts, parametric editing, revisions and saved plans stay on the previous rules. A
draft does not appear in the planner palette and cannot be placed.

The next editor phase can add rail joints on this same session. The disabled entries still
name the centre line, the start/finish line, border pieces and the two joint kinds. Those
entries do nothing.
