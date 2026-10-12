# 0015 Measure start-light sync without a new control channel

## Context

Phase 2C-2 lets a browser estimate the PC clock and light red lamps locally.
GO is still only `start_committed` after `RaceEngine.start`. What was missing
was a way to see, after a real start, how far the browsers were from the PC
and from each other.

A browser must not gain a way to steer the race in order to report those
numbers. The quality steps from ADR 0014 (8 ms, 20 ms, 25 ms hold, 45 s) were
chosen before any track measurement. Changing them from a simulation would
dress a model up as a result.

## Decision

Optional diagnostic fields ride on the existing phase and commit documents:
`observed_ns` and `lateness_ns` when the PC publishes a red phase,
`published_ns` when it queues `start_committed`, and `skipped_steps` when the
GUI jumped ahead. They are not consulted by the start reducer. A phase with
those fields still cannot authorize GO.

Each browser keeps at most 400 events in memory. The diagnostic page shows
the summary and can download JSON or CSV. The HUD does the same only when the
address contains `messung=1`, so a normal race view does not grow a panel.
The file contains an anonymous client id and no session token. It describes
that browser only.

Several files are compared later, on the PC, by `scripts/compare_start_measurements.py`.
There is no live message for a browser to upload its log. An optional video
helper, `scripts/optical_start_frames.py`, is not imported by the race.

The automated network series uses fixed delays. It checks the current rules.
It does not change them. A negative GO estimate is stored and labelled. It is
not treated as proof that the light was visible early.

## Consequences

- A tester can export one browser's start log without a network service.
- The PC lamp time in that log is the publish time, not a photograph of the
  monitor. A frame callback is not the lit pixel either.
- Cross-browser spread exists only for rows that carry an estimated PC time.
  Rows without one are skipped, not filled in.
- The 8 / 20 / 25 / 45 ms and second limits stay as in ADR 0014 until a set of
  device logs supports a change.
