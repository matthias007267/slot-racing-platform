# 0017 Shared path geometry

## Context

The part library already stores a Carrera piece as measures: length, radius, angle, an outline
polygon and two rail joints. Curves and lane changes are drawn from those numbers. The
parametric editor needs lines, circular arcs and cubic Bézier curves,
including a part whose lanes do not stay a constant pitch apart and whose outline is not the
catalogue rectangle.

That geometry has to stay free of Qt, survive save and load as the original control points, and
ride on the revision rows from ADR 0016. A used part must not change shape in a saved plan.
Catalogue pieces must not grow a stored polyline for every groove.

## Decision

`geometry.py` is the only place that evaluates a path. A path is an ordered tuple of frozen
segments:

- A line is its two endpoints. Its length is the Euclidean distance.
- An arc is a centre, a radius, a start angle and a signed sweep. Positive sweep runs toward +y.
  Its length is radius times the absolute sweep in radians. Catalogue radii and angles are not
  redefined; a curve with empty `slot_paths` is still derived from those measures.
- A cubic Bézier curve is the four control points `P0`, `P1`, `P2` and `P3`. Position is
  `B(t) = (1-t)³P0 + 3(1-t)²t P1 + 3(1-t)t² P2 + t³ P3`. The length is the integral of `|B'(t)|`
  by adaptive Simpson's rule. Recursion stops when the coarse and refined estimates of one
  interval differ by at most 0.01 mm, or after 16 splits. The result is cached by the eight
  control coordinates. A changed curve is a new cache key.

Coordinates stay millimetres, with +y down and clockwise rotation, matching the plan.

A joint reports position continuity and tangent continuity separately. A gap larger than 0.05 mm
inside one part is rejected. A tangent turn larger than 1° is a kink and is allowed. This check
does not decide whether a whole layout is a closed lap.

`driving_paths` returns stored slot paths when a part has them. Otherwise it derives the ordinary
straight, the catalogue curve, or the existing lane-change grooves. Seeding does not write those
derived paths back onto catalogue rows.

A part may also store contour paths and side `BorderEdge` rows. Several contours are allowed.
The filled roadway and the hit test stay the outline polygon; a second contour is not subtracted
as a hole. An accessory may carry the same spans on `AttachmentProfile.edge`. `host_shape`
`"custom"` fits only when that edge equals the host's border edge for the chosen slot. The
comparison is the span tuple, not a database id. A catalogue strip still matches by host family,
length or radius, and angle, and it still fits a straight that also has an extra custom edge.

Rail joints stay kind `track`. `rails_can_join` requires that kind on both ends and the same lane
tuple. Kind `border` and kind `support` never snap as a rail, and a border does not occupy a rail
joint. A custom border does not remove the left and right slots of an ordinary straight.

The painter turns each span into `QPainterPath` operations. A Bézier span is `cubicTo` of the
stored control points, not a sampled polyline. Centre-line dashes of a Bézier curve are chords
computed once inside the cached figure. The figure cache key is the frozen definition, so a
geometry edit is a different object and drops the old picture.

Persistence uses the existing `slot_paths` JSON column. One list holds grooves (`slot`,
`center`), contours (`contour`) and border edges (`border`, with a slot name). A Bézier object
is `kind: "bezier"` plus `x0,y0` … `x3,y3` for `P0` … `P3`. Line and arc objects from before this
format still load. A broken entry clears the list, which is how an older row asks for the
ordinary derived lanes. No Qt object and no database id is stored in that JSON. No new table and
no new migration.

`same_geometry` treats contours, border edges and the attachment edge as geometry. Editing a
Bézier control point or a side edge of a part that a plan still references forks a revision, as
ADR 0016 already does for length and radius. A rename does not drop stored paths when the
incoming spec has no explicit geometry of its own.

Invalid coordinates, a non-positive radius, a zero-length segment, a missing Bézier point, a
disconnected path, a lane that no `track` joint lists, and a custom border without an edge raise
`error.planner.geometry`. A finite but unusual shape is kept.

## Consequences

- Catalogue plans and lane lengths of ordinary straights and curves stay on the old measures.
  A switch, a crossing or a lane change still has no single lane length.
- The parametric editor is ADR 0018 and calls this model. ADR 0019 records that the graphical
  editor was removed; a replacement is not implemented. The path types stay for stored parts
  and for that later editor. `.slotpart` import and export are not built here.
- Hole-shaped outlines are stored as extra contours and are not cut out of the fill.
- Downgrade is unchanged: the JSON column already existed. A database that has stored Bézier
  spans will load those spans as ordinary derived lanes if this code is removed, because an
  unknown span clears the list.
