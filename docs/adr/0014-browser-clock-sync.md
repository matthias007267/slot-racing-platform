# 0014 Browser clock estimate for red start lamps

## Context

Browsers on the local network should light the red start lamps together. Waiting
for one WebSocket message per lamp shows each lamp only after that message
arrives, so phones and other PCs drift apart by the network delay.

The race clock is the PC's monotonic `perf_counter_ns` (ADR 0003). A browser's
`Date.now()` jumps with the wall clock and is not usable. `performance.now()`
is monotonic for the life of the tab, but it is not the PC clock.

`planned_go_ns` is the moment the PC intends to start. It is not permission
for a browser to show GO. A blocked GUI thread can reach that timestamp before
`RaceEngine.start` runs. GO stays a separate server message, `start_committed`,
sent only after the engine has stamped the race.

JSON numbers are exact in JavaScript only up to `2**53 - 1`. A PC that has
been up for about 104 days has monotonic nanoseconds beyond that, so those
timestamps must not travel as JSON numbers.

## Decision

Broadcast protocol 3. The server thread answers each `time_probe` with a
`time_sample` immediately. The four timestamps are browser send `t1`, PC
receive `t2`, PC send `t3`, and browser receive `t4`.

```text
RTT = (t4 - t1) - (t3 - t2)
offset = ((t2 - t1) + (t3 - t4)) / 2
```

`offset` estimates host time as `performance.now()` scaled to nanoseconds plus
`offset`. Several samples are kept. Low-RTT samples are preferred and the
offset is their median. The uncertainty is that median RTT, halved, widened
when the chosen offsets disagree. It is an estimate for a symmetric path. It
is not a hard bound on asymmetric delay or on time spent outside those four
timestamps.

Eight probes are sent when a browser connects, four more when a start sequence
becomes active, and two about every 30 seconds (eight when the estimate is
poor). A random delay of up to five seconds keeps ten browsers from probing
on the same tick. The server accepts about ten probes a second per browser
and ignores the rest. A bad probe does not close the socket.

Three fresh samples and an uncertainty up to 8 ms are good. Up to 20 ms is
usable. Wider, thinner, or older than 45 seconds is poor and the lamps follow
`start_phase` again. A good or usable result stays usable up to 25 ms so one
noisy sample does not flip the mode. An offset jump larger than 50 ms is kept
only when the selected samples agree within 20 ms.

Host nanoseconds on the wire are decimal strings. The browser probe time is
decimal microseconds, which stays inside a safe JavaScript integer, and is
scaled to nanoseconds before the formulas. `BigInt` does the protocol math.
Painting uses the difference to the next lamp.

When the estimate is good or usable, the browser shows the red phase of the
current schedule at the estimated PC time, and never an earlier phase than it
already showed or than `start_phase` confirmed. At `planned_go_ns` the last
red phase stays on. GO is still only `start_committed` for that `sequence_id`.

The lamp timer is armed for the next red boundary, or for the planned GO time
so the hold can start. It is capped at 60 seconds and then reads the clock
again. A boundary that is already due uses one animation frame. There is no
standing animation loop and no `setInterval`. A late timer skips to the phase
the clock says now.

Hiding the page, losing the socket, revoking the schedule, or seeing the
browser clock jump backward drops the estimate. After a hide, new probes run
before local lamps resume. Nothing is replayed. The diagnostic page shows the
estimate and labels it as an estimate. It does not show the session token.

The race engine does not wait for probes, browsers, or this estimate.

## Consequences

- Red lamps on several browsers can change with the PC clock instead of with
  each network message. The match is only as good as the estimate.
- This is not a real-time guarantee. Wireless delay can be asymmetric, and a
  phone can pause timers in the background. The 8 ms and 20 ms steps are
  starting points for later measurement, not a claim about every device.
- GO cannot move earlier than the engine start, even when the estimate is
  exact and `planned_go_ns` is already past.
- Poor estimates keep the Phase 2C-1 server-driven red lamps.
