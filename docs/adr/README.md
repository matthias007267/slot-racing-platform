# Architecture Decision Records

| # | Decision |
|---|----------|
| [0001](0001-modular-monolith-with-plugins.md) | Modular monolith with plugins instead of services |
| [0002](0002-event-bus-and-immutable-events.md) | Synchronous in-process event bus with immutable events |
| [0003](0003-integer-nanosecond-time.md) | Integer nanoseconds and `perf_counter_ns` for time |
| [0004](0004-timing-source-abstraction.md) | `TimingSource` pushes standard events to a sink |
| [0005](0005-plugin-discovery-and-boundaries.md) | Entry-point discovery and import-linter enforced boundaries |
| [0006](0006-database-layout.md) | One SQLite database, one migration history, module-owned models |
| [0007](0007-catalogs-timing-factory-and-race-flow.md) | Core catalogs, timing factory and the event driven race flow |
| [0008](0008-track-timing-layout.md) | Track timing layout: logical positions, sensors and setups |
| [0009](0009-timing-provider-infrastructure.md) | Timing providers: ids, registry, capabilities, availability and errors |
| [0010](0010-vendor-neutral-naming.md) | Manufacturer-neutral naming: package, command, plugin group, data locations |
| [0011](0011-camera-configuration.md) | Camera configuration is global and not tied to a track |
| [0012](0012-camera-setup-ui.md) | Camera setup page edits that global document visually |
| [0013](0013-configurable-race-hud.md) | Race HUD layout is a versioned settings document |

New decisions get the next number. Format: Context, Decision, Consequences.
