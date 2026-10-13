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
**Grafisch** is a new point-and-click editor for one floor plate. Switching tabs does not
rewrite geometry and does not create a revision.

The Qt-free session is `PlateSession` in `plate_session.py`. It owns the contour, the mode
(`select` or `draw`), the corners, the selected corner, the hover preview, the dirty flag
and a stack of committed contours. Hover and an in-progress drag are not geometry. Undo and
redo restore a previous contour. There is no tool-controller framework.

The Qt widget is `ui/plate_editor.py`. It paints the millimetre grid, handles the mouse and
the keyboard, zooms and pans, and shows the property fields. A click asks the session what
should change. A closed plate is painted with `paint_part`. An open chain is a local
preview stroke and is not a stored path.

A plate is a closed line contour built with `contour_from_polygon` and `outline_from_spans`.
One part has one plate. A second plate is refused until the user replaces the first; replace
is one undo step. Closing needs at least three distinct corners, no zero-length edge, a
usable area and no proper self-intersection. Invalid input is reported and is not written.

**Drafts are not drivable parts.** A plate is stored as a normal `PartSpec` with
`category="draft"`, one contour, an outline, no connectors, no grooves and no border edges.
`draft` is not a `build_part` category. `driving_paths` and the figure do not invent lanes
for it. The planner palette omits it, and `place_instance` rejects it. The library still
lists it, so it can be opened again. Complete-part validation is unchanged. No table and no
migration: the category column and the contour JSON already exist.

A pure line plate can be opened back into corners without rounding the stored coordinates.
Any other body — arcs, Bézier curves, lanes, joints, borders — is shown with `paint_part`
and the point tools are disabled. Nothing simplifies that body into a polygon.

What this phase does not draw: lanes, new arcs, Bézier curves, rail joints, border joints,
border pieces, a centre line, a start/finish line, automatic lane pitch, track closure,
`.slotpart` import or export.

## Consequences

A new part can be given one closed floor plate, edited by click or by millimetre fields,
saved, and opened again with the same corners. Carrera parts, parametric editing, revisions
and saved plans stay on the previous rules. A plate does not appear in the planner palette
and cannot be placed.

The next editor phase can add lanes and arcs beside this plate. The session already
separates element choice from the canvas, and disabled entries name the later elements.
Those entries do nothing.
