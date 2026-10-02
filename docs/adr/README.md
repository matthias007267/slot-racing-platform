# Architecture Decision Records

| # | Decision |
|---|----------|
| [0001](0001-modular-monolith-with-plugins.md) | Modular monolith with plugins instead of services |
| [0002](0002-event-bus-and-immutable-events.md) | Synchronous in-process event bus with immutable events |
| [0003](0003-integer-nanosecond-time.md) | Integer nanoseconds and `perf_counter_ns` for time |
| [0004](0004-timing-source-abstraction.md) | `TimingSource` pushes standard events to a sink |
| [0005](0005-plugin-discovery-and-boundaries.md) | Entry-point discovery and import-linter enforced boundaries |
| [0006](0006-database-layout.md) | One SQLite database, one migration history, module-owned models |

New decisions get the next number. Format: Context, Decision, Consequences.
