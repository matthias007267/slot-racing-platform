# 0020 Element-oriented part editor

## Context

ADR 0018 builds simple straights, curves and borders from a form. ADR 0019 adds a second
tab that draws one closed line plate and up to four open lanes of lines and arcs. Bézier
curves, a start line and rail joints stay disabled there, and a part that is not that
simple shape cannot be edited graphically.

The editor people use should draw the body the planner places. A second global form that
generates length, width, radius, angle and lane pitch is the wrong tool for a free part.
Catalogue builders, stored parts and saved plans still depend on the parametric geometry
and on the revision rules in ADR 0016.

## Decision

`PartDialog` is one graphical workspace. The Parameter tab and the global fields for
length, width, height, radius, angle, lane pitch, lane positions and lane count are gone
from the dialog. Name, article number, manufacturer, description, origin, scale and
category stay, with catalogue restore, revisions and adoption unchanged.

The Qt-free session is `GraphicSession` in `graphic_session.py`. It owns elements, the
pen, selection, snap and one undo stack. An element is a floor plate, a driving lane, a
track connector or a start/finish stroke. A plate is one closed contour. A lane is an
open chain of `LineSegment`, `ArcSegment` and `CubicBezier` spans and carries a lane
number. A connector carries a point, an outward direction and the lane numbers it joins.
The start stroke is one path of kind `start`. Hover and the rubber band are not stored.

The Qt widget is `ui/graphic_editor.py`. It paints the millimetre grid, handles the
mouse and the keyboard, zooms at the pointer, pans, fits the drawing, and shows the
element list and the numeric fields for the selection. Clicks, handles and those fields
edit the same session. `paint_part` draws the compiled body, including the start stroke,
so the editor, the preview and the planner share one picture.

Saving an untouched open part rewrites only metadata through `with_metadata`. The stored
paths, connectors, attachment and border edges stay. An edited save writes the drawn
contours, slot paths, start path and track connectors. Radius and angle are cleared so a
catalogue arc figure does not replace the stored paths. An edited crossing is stored as
`special` for the same reason: the crossing figure ignores stored grooves. Non-track
connectors from the opened part are kept. Length and width become the bounding box of
the drawn endpoints. Centre paths and, once the body was edited, attachment and border
edges are not copied.

A draft is still `category="draft"`. The palette omits it and `place_instance` rejects
it. Promoting a draft or saving a new or edited part requires a closed plate, valid
paths, unique lane numbers, at least two track connectors, and a connector assignment
that names existing lanes and sits within reach of each named groove. The reach covers
a centreline joint listing every groove of a multi-lane part. Errors name the element
and the canvas marks it. Opening or cancelling writes nothing.

No table and no migration. `PATH_START` is accepted by the existing path JSON.
Connectors stay rows. `PlateSession` and the old plate widget remain for their tests;
the dialog does not use them. Catalogue construction stays in `parametric.py`.

## Consequences

A part can be drawn from lines, arcs and Bézier curves, given connectors and an optional
start line, saved as a draft or as a placeable part, opened again, and joined in the
planner. An existing catalogue part opened and saved without a geometry edit keeps its
body. A geometry edit of a used part still forks a revision.

Editing a border or a crossing graphically replaces its track connectors with the drawn
joints and drops border attachment. Text-only saves do not.
