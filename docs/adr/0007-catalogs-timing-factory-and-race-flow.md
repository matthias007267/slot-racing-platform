# 0007 Core catalogs, timing factory and the event driven race flow

## Context
The first complete race flow needs drivers, vehicles and tracks in the race module, a way to
create a timing source per race, and persistent results. The existing rules still apply: modules
do not import each other, the engine is free of UI and hardware, timing does not know the UI, and
everything is connected through core interfaces and events. The task text did not decide how these
pieces are connected, so the smallest additions to the existing architecture were chosen.

## Decision
- **Catalogs in the core.** `DriverCatalog`, `VehicleCatalog` and `TrackCatalog` (read-only
  interfaces plus frozen info classes) live in `slot_racing.core.catalog`. The owning modules
  implement and register them; `races` depends only on the interfaces. `races` declares
  `requires=("drivers_vehicles", "tracks")` and `optional=("timing",)`.
- **Timing factory.** `TimingSourceFactory` and `TimingSessionSpec` are added to
  `slot_racing.core.timing`. A timing module registers a factory as a service; the race controller
  asks for a fresh source per race. `AppConfig.timing_source` can select a factory by name.
- **Host driven sources.** `TimingSource` gets no-op `poll()`, `pause()` and `resume()` hooks. The
  simulation uses them to run on a real clock without threads; the engine forwards pause/resume.
  The live view drives `poll()` from a Qt timer through `RaceRunner.tick()`.
- **Event driven persistence.** `RaceRecorder` subscribes to the standard race events and stores
  laps, sectors, lifecycle and standings. The engine does not know it exists.
- **Statuses.** `RaceStatus` gains `READY` and `ABORTED`. The engine still uses only
  `CREATED/RUNNING/PAUSED/FINISHED`; race management sets `READY` (configured) and `ABORTED`
  (stopped early or interrupted).
- **Shared UI helpers.** A small `slot_racing.uikit` package holds the list page, dialog and table
  helpers used by several modules. It depends on the core only and is forbidden in the core, the
  engine and the timing modules.
- **Errors.** User facing rule violations are `core.errors.ValidationError(key, **params)`; the UI
  translates them. Everything else is logged and shown generically on the page.
- **Layout.** Until tracks describe their own sensors, races use the fixed timing layout
  `start_finish, sector_1, sector_2`.
- **Deleting.** Foreign keys from races are `RESTRICT`; master data used in a race is
  deactivated instead of deleted.

## Consequences
- Adding a real timing source later means registering another `TimingSourceFactory`; races and UI
  stay unchanged.
- One race can run at a time (controller restriction), which matches a single physical track.
- The default layout and the maximum of six lanes are placeholders for the later track layout.
- Reducing a track's lane count does not check existing races (tracks does not know races).
- Existing tests were touched in two places only: the shell placeholder test used the `races`
  page, which is now real (it uses `statistics`), and engine/simulation tests were extended.
