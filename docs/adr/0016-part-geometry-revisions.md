# 0016 Part geometry revisions

## Context

A placed rail stores a foreign key to `track_part_definitions` plus its own position and rotation.
The plan JSON stores the same part id. If editing a definition overwrote that row, every saved
plan and every archived layout revision would change shape without being asked.

The library already identifies a part by its designation plus its article number. Scale is a
property. Two physical pieces may share one article number when their names differ (the curve-3
outer border and its end piece both use 20020563). A rename must not create a second catalogue
row, and seeding must not put the bundled geometry back over a definition the user changed.

## Decision

A part has a stable `lineage_id`. Each frozen geometry is one row with a `revision`. Exactly one
row of a lineage is `is_current`. The library, new placements, stock and package contents use
that row.

- A geometry change of a row that a plan or an archived layout still references inserts the next
  revision and leaves the referenced row as it was. Position and rotation are not moved, and
  neighbouring parts are not shifted.
- A geometry change of a row nothing references updates that row in place.
- A change of name, article number, scale, manufacturer or description updates the current row
  in place. It does not fork.
- `adopt_revision` points chosen tracks at the current row. It rewrites the current `plan`
  document and the instance rows. Rows named `archive-*` stay on the old part id.
- `origin` is `catalog`, `modified` or `custom`. Seeding inserts a missing catalogue identity and
  refreshes attachment geometry only on a current row whose origin is still `catalog`. A
  `catalog_key` keeps the bundled identity after a rename, so seeding does not insert a twin.
- Restoring a bundled part copies `standard_catalog()` onto the current revision, in place when
  that revision is unused and as a new revision when a plan still uses it.
- The unique index on name and article number applies only to current rows, so a frozen revision
  may keep the same designation. Article numbers are not unique by themselves.
- Stock corrections and package contents move to the new current row when a revision is forked.
  Plans do not.

The planner reads layout documents through the `track_layouts` table and does not import the
tracks module.

Later steps, not part of this decision's code: a shared path model for lines, arcs and cubic
Bézier curves, the parametric and graphical editors, and the versioned `.slotpart` exchange
file. Those stay on the same definition and the same connector kinds (`track`, `border`,
`support`). Rail joints and border attachments remain different compatibility rules.

## Consequences

- Existing databases gain the columns in migration `0020`. Existing rows become revision 1.
  Placed parts keep their id, so stored plans do not change shape.
- Downgrade points instances and package contents at the current row of each lineage and deletes
  the frozen revisions. Archived JSON is not rewritten, so a downgrade of a database that already
  forked is lossy for those archives.
- Usage counts in the open plan follow the current revision, so a frozen placement still consumes
  the stock of the part the library shows.
- Replacing a part that several current rows share an article number with must ask which row.
  The service lists every current match and does not pick one.
