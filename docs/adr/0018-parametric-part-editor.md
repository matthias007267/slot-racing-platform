# 0018 Parametric part editor

## Context

The library already stores Carrera pieces, revisions (ADR 0016) and shared path geometry
(ADR 0017). Users need to create a straight, a curve or a simple border, edit a bundled
part, and duplicate one, then use the result in the planner. Freehand Bézier curves and
hand-drawn outlines are not edited here. A graphical editor for them is not implemented
(ADR 0019). This phase must not replace geometry it cannot edit, and it must not give a
saved plan a new shape unless that plan adopts it.

## Decision

The parametric editor rebuilds only a simple straight, a simple right- or left-hand arc, or
a border for one of those shapes. A catalogue straight or right-hand curve stays a compact
measure: empty slot paths and the existing connector helpers. A different pitch, individual
lane positions or a left-hand bend stores the grooves, the outline and the side edges.
A left-hand bend is a positive angle plus arcs whose sweep is negative, so the stored angle
never means “the other way”.

Switches, lane changes, pit entries, crossings, the curve end piece and any Bézier groove
are locked. The editor still shows that body with the plan painter and still saves the
designation, the article number and the texts. The save copies the stored spec and does not
call the simple builder. A broken `slot_paths` list is marked corrupt: a geometry change is
refused, a text change writes the original JSON back, and a duplicate of that row cannot be
saved. `None` and `[]` still mean the ordinary lanes.

Rails dock only through `track` joints. A border has no rail joint. It attaches only when
its `AttachmentProfile` matches the host: length and lane count for a straight, radius and
angle for a right-hand curve, or an equal border edge for a left-hand or otherwise custom
host. A border does not occupy a rail joint, so two rails can still meet beside it.

Saving uses the existing revision rules. A geometry change of a part that a plan already
uses inserts the next revision and leaves the placed pose alone. The editor then asks which
current plans should adopt it. The question states that existing tracks keep the previous
geometry until then. Archives are not rewritten. The same question is available from the
library as “Geometrie übernehmen”. A rename does not create a revision. Seeding still
refreshes only current rows whose origin is `catalog`, so a modified Carrera part and a
custom copy are left alone. Restoring the bundled definition is a confirmed action and goes
through the same revision guard.

No new table and no new migration. Identity remains the designation plus the article number.

## Consequences

A user can create a straight, a curve and a simple border, edit or duplicate a library part,
and place the result without restarting. Complex bodies stay intact. A custom track width on
a left-hand curve is part of that curve’s stored edge; a border fits it only when the edge
matches. `.slotpart` import and export stay out of this phase.

ADR 0019 records that the graphical editor was removed. A replacement is not implemented.
