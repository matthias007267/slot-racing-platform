# 0003 Integer nanoseconds and `perf_counter_ns` for time

## Context
Lap and sector times need exact arithmetic and a clock that does not jump.

## Decision
All times are `int` nanoseconds. Fields ending in `_ns` are validated as non-negative ints (no
floats, no bools). Timestamps come from a `Clock`: `MonotonicClock` uses `time.perf_counter_ns`
(monotonic and high resolution on Windows, unlike `time.monotonic` there); `ManualClock` drives
simulation and tests. Wall-clock time is only used for persistence metadata.

## Consequences
- No rounding errors when summing laps; SQLite stores them as `BIGINT`.
- Timestamps are only comparable within one process run. Timing sources on other machines (Pi)
  must be converted to the desktop timebase when received.
