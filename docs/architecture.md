# Architecture

Carrera Racing Platform is a modular desktop application ("building-block system"): a small,
stable core plus optional feature modules that are plugins. Decisions are recorded in
[`docs/adr`](adr/README.md).

```text
                 ┌──────────────────────────── carrera.app ───────────────────────────┐
                 │  Runtime (wiring)   MainWindow (PySide6)   Dashboard / Settings    │
                 └───────────────┬────────────────────────────────────────────────────┘
                                 │ discovers via entry points (never imports modules)
 ┌───────────────────────────────▼───────────────────────────────────────────────────┐
 │ carrera.modules   drivers_vehicles  tracks  races  timing  statistics  track_planner│
 │ (plugins)         timing_camera*  timing_sensor*  audio_animation   (* off by default)│
 └───────────────────────────────┬───────────────────────────────────────────────────┘
                                 │ import only
 ┌───────────────────────────────▼───────────────────────────────────────────────────┐
 │ carrera.core   events · plugin · timing · config · domain · storage · clock · i18n │
 └───────────────────────────────────────────────────────────────────────────────────┘
```

## Core (`carrera.core`)

Qt-free and hardware-free. It must not import `carrera.app`, `carrera.modules`, PySide6, OpenCV
or GPIO libraries (enforced by import-linter).

| Package / module | Responsibility |
|---|---|
| `clock` | `Clock` protocol, `MonotonicClock` (`perf_counter_ns`), `ManualClock` |
| `events` | Immutable typed events and `EventBus` |
| `plugin` | Manifest, lifecycle, services, UI contributions, discovery, manager |
| `timing` | `TimingSource`, `ManuallyTriggerable`, `TimingSourceFactory`, `TimingSessionSpec`, `TimingSetupService` |
| `catalog` | `DriverCatalog`, `VehicleCatalog`, `TrackCatalog` and their read-only info types |
| `errors` | `ValidationError(key, **params)` for translatable user errors |
| `domain` | IDs, `Participant`, `ParticipantResult`, `RaceStatus`, `TimingLayout`, `TimingPosition`, `TimingSensor`, `TimingSetup` |
| `messages` | German texts for the core's `ValidationError` keys (registered by the runtime) |
| `config` | `AppConfig` (pydantic), JSON load/save, per-user paths |
| `storage` | SQLAlchemy base, `Database`, Alembic migrations, core tables |
| `i18n` | Key based `Translator` (German first), `format` fills placeholders |

## Modules (`carrera.modules`)

Every module is a package with a `plugin.py` (a `Plugin` subclass registered as entry point in
`pyproject.toml`) and optionally `models.py` and domain code. Modules never import each other.

| Module | State |
|---|---|
| `drivers_vehicles` | Models, `DriverService`, `VehicleService` (implement the catalogs), driver and vehicle pages |
| `tracks` | Models (`Track`, `TrackLayout`), `TrackService` (implements `TrackCatalog`), track page and the **timing configuration** (editor, wizard, test mode). Optionally uses `timing` |
| `races` | Models, **race engine**, `RaceService`, `RaceRecorder`, `RaceController`/`RaceRunner`, race pages (list, 6-step flow, live view, results). Requires `drivers_vehicles` and `tracks`, optionally `timing` |
| `timing` | Models, **`TimingSetupManager`** (stores a track's timing setup), **`SimulationTimingProvider`** and its `TimingSourceFactory` |
| `statistics`, `track_planner` | Placeholder plugin with navigation entry |
| `timing_camera`, `timing_sensor`, `audio_animation` | Placeholder plugin only (camera/sensor off by default) |

### Shared UI helpers (`carrera.uikit`)

Small Qt helpers used by the pages of several modules: `EntityPage` (list with add / edit /
(de)activate / delete), `FormDialog`, table helpers, `describe_error` and the common German texts.
It depends on the core only; the core, the engine and the timing modules never import it
(import-linter).

## Master data and race flow

```text
tracks ──TrackCatalog──┐
drivers_vehicles ──Driver/VehicleCatalog──▶ races (RaceService: configuration, rules, results)

RaceController.start_race ─ validates ─▶ TimingSourceFactory.create_source ─▶ TimingSource
TimingSource ─SensorTriggered─▶ EventBus ─▶ RaceEngine ─Sector/Lap/Race events─▶ EventBus
EventBus ─▶ RaceRecorder ─▶ RaceService (database)          EventBus ─▶ RaceRunner (live standings)
LiveRaceView / ResultsView ─ read only ─▶ RaceRunner.snapshot() / RaceService.get_results()
```

- `races` never imports `drivers_vehicles` or `tracks`; it looks them up through the catalog
  interfaces in `carrera.core.catalog`, which those modules register as services.
- Participant rules live in `RaceService.add_participant`: driver and vehicle exist and are
  active, the lane exists on the track and is free, driver and vehicle are used once, and the
  number of participants never exceeds the track's lane count. A race is editable while
  `CREATED`/`READY`; adding the first participant makes it `READY`.
- `RaceController.start_race` allows one running race at a time, re-validates the race, picks a
  `TimingSourceFactory` (`AppConfig.timing_source`, otherwise the first by name), builds a fresh
  source and `RaceEngine` and starts it. The simulation is host driven: the live view calls
  `RaceRunner.tick()` from a Qt timer, which polls the source.
- `RaceRecorder` subscribes to the race events and stores lifecycle changes, laps with their
  sector times and the final standings (position, laps, total and best lap time, finish status).
  Storage problems are logged and shown as a warning; they never interrupt the race.
- The UI only displays. Live standings come from the engine (`RaceRunner.snapshot()`), stored
  results are ordered by the position the engine determined. Last lap and average lap are
  computed by `RaceService`, not by the UI.
- Races still marked running or paused at startup (crash) are set to `ABORTED`.
- Errors shown to the user are `ValidationError`s with translation keys; unexpected errors are
  logged and shown on the page. Pages, dialogs and the live view catch errors per action so a
  single failure never closes the application.

## Event system

Events are frozen dataclasses with `timestamp_ns: int`. Every `*_ns` field is validated as a
non-negative integer. Event types:

- Timing: `SensorTriggered(source_id, sensor_id, position_id, lane)`
- Race: `RaceStarting`, `RaceStarted`, `RacePaused`, `RaceResumed`, `RaceFinished`
- Laps/sectors: `LapStarted`, `LapCompleted`, `SectorCompleted`
- Result: `WinnerDetermined`
- Plugins: `PluginEnabled`, `PluginDisabled`

`EventBus.subscribe(EventType, handler)` returns a `Subscription`. Delivery is synchronous, in
subscription order; subscribing to `Event` receives everything. Handler exceptions are logged and
isolated. Plugins use `context.events`, a scoped view whose subscriptions are cancelled when the
plugin is disabled.

## Plugin system

A plugin declares a `PluginManifest` (name, version, title, `requires`, `optional`,
`enabled_by_default`, `models_module`) and optional German/other `translations`. Its lifecycle is
`activate(context)` / `deactivate()`.

`PluginContext` is the only handle a plugin gets:

- `events` – publish and (scoped) subscribe
- `register_service(interface, impl, name)` / `find_service(s)` / `get_service`
- `add_navigation(NavigationItem(id, title_key, order, page_factory))`
- `clock`, `config`, `translator`

Everything a plugin registers is removed automatically when it is disabled.

`PluginManager`:

- `enable_many(names)` activates in dependency order (`requires` and `optional`), never raises;
  failures mark the plugin `FAILED` (with the error), roll back its registrations, and mark
  plugins that require it as failed too.
- `enable(name)` requires the required plugins to be enabled and raises otherwise.
- `disable(name)` first disables all plugins that require it.
- `optional` dependencies only influence order; a plugin must tolerate their absence.
  Optional UI that depends on another plugin can listen to `PluginEnabled`/`PluginDisabled`.
- Discovery uses the entry-point group `carrera.plugins`; plugins that cannot be imported are
  reported as failed.

## Timing abstraction

```text
SimulationTimingProvider ─┐
CameraTimingProvider*    ─┼─ TimingSource.start(sink) ─▶ SensorTriggered ─▶ EventBus ─▶ RaceEngine
RaspberryPiTimingProvider*┤
CarreraTimingProvider*   ─┘                                            (* future modules)
```

`TimingSource` knows nothing about its origin. Every source delivers the same event,
`SensorTriggered(timestamp_ns, source_id, sensor_id, position_id, lane)`.

### Track timing layout

A track can have a **timing layout**: an ordered list of *logical positions*.

- `TimingPosition(id, type, order, name)`: `type` is `START_FINISH` or `SECTOR`. The `id` is
  stable, the `order` is the place along the lap (start/finish is 1), `name` is an optional
  display name. Display names such as "Sektor 2" are derived from type and order by the UI; no
  code decides by strings what a position means.
- `TimingLayout(positions)` validates itself: at least one position, exactly one `START_FINISH`
  and it is first, unique ids, orders `1..n`. The number of sectors is not limited (0 to many).
  Sector `k` ends at the k-th position of the lap sequence; the last sector ends at
  `START_FINISH` and completes the lap.
- `TimingSensor(id, position_id, name, hardware_id, active)` is a *device* that reports one
  position. `hardware_id` is an opaque string for providers (for example a GPIO pin); it is not
  the logical position and the engine never sees it.
- `TimingSetup(layout, sensors)` binds exactly one sensor to every position and validates unique
  sensor ids and unique hardware ids. `ensure_usable()` rejects inactive sensors: a setup with an
  inactive sensor can be stored but cannot be used as timing source.
- All of this is in `carrera.core.domain`, so every provider and the engine use the same rules.
  Invalid configurations raise `ValidationError`; the UI shows the translated message.

### Providers and engine

```text
Race ─ track ─▶ TimingSetupService.get_setup(track)   (fallback: default_timing_setup())
                         │
                TimingSessionSpec(setup, lanes, laps, race_id, track_id)
                         │
              TimingSourceFactory.create_source(spec) ─▶ TimingSource
                         │  SensorTriggered(sensor_id, position_id, lane)
                         ▼
                      EventBus ─▶ RaceEngine(RaceConfig(layout = setup.layout))
```

- The *provider* knows sensors (and, for real devices, hardware ids) and translates raw signals
  into `SensorTriggered` with the `position_id` of the sensor's position.
- The *engine* knows only the layout (positions). It compares `event.position_id` with the next
  expected position of a participant. It never imports a provider or reads hardware ids
  (import-linter).
- The simulation has no built-in layout. `SimulationTimingProvider(clock, setup, lanes, laps)`
  passes the positions of the given setup in driving order; `SimulationTimingFactory` takes the
  setup from `TimingSessionSpec`.
- `TimingSetupService` (core interface, implemented and registered by the `timing` module) stores
  and loads the setup per track. The races module asks for it lazily; tracks without a stored
  setup use `default_timing_setup()` (`start_finish`, `sector_1`, `sector_2`) so existing tracks and
  races keep working. A stored setup with an inactive sensor stops the race start with a clear
  message.

### Attaching camera and Raspberry Pi later

A camera or Raspberry Pi module implements `TimingSource` and a `TimingSourceFactory` and
registers the factory as a service, exactly like the simulation. In `create_source(spec)` it reads
`spec.setup`: each active `TimingSensor` has a `hardware_id` that tells the provider which pin,
camera zone or device channel belongs to that sensor, and `position_id` is what it must put into
`SensorTriggered`. Nothing in the engine, the track UI or the database schema changes. The
timing test mode works with any source that implements `ManuallyTriggerable`; real hardware
sources are not simulated there.

### Timing configuration UI (`tracks` module)

The track page has a *Timing-Konfiguration* button. It opens, inside the same navigation page,

- the **configuration view**: a table of positions with sensor id, name, hardware id and active
  flag; add, remove, reorder, edit and activate/deactivate; save and reset to the default;
- the **wizard** in six steps: track, positions (start/finish is automatic), sensors, order,
  test, save;
- the **test mode**: *Simulation auslösen* creates one simulated `SensorTriggered` through a
  simulation source built from the current layout and shows a table (time, sensor, position),
  event count, last position and sensor, time and the detected order. Test events go to a private
  sink, never to the application bus, and are not stored.

The UI only edits a `TimingDraft` and shows what the `TimingTestSession` reports. Validation lives
in the core domain, persistence in `TimingSetupManager`, and no race logic (laps, positions,
sector times) is computed in the UI. `timing_editor` and `timing_test` are Qt-free
(import-linter).

Host driven hooks on `TimingSource` (`poll`, `pause`, `resume`) default to no-ops, so sources with
their own threads or callbacks are unaffected.

## Race engine (`carrera.modules.races.engine`)

`RaceEngine(config, bus, clock, timing_sources)` with `RaceConfig(race_id, laps, participants,
layout)`.

- `start()` → `RaceStarting`, `RaceStarted`, `LapStarted` per participant; starts the sources.
- Subscribes to `SensorTriggered`; each participant expects the next sensor of the lap sequence.
  Unexpected sensors, unknown lanes and events while paused are ignored.
- Publishes `SectorCompleted`, `LapCompleted`, `LapStarted`.
- A participant finishes after `laps` laps; the first finisher triggers `WinnerDetermined`;
  when all have finished `RaceFinished` is published and sources are stopped.
- `pause()` / `resume()` exclude paused time from race time. `stop()` ends the race early,
  ranking by laps and then time (`aborted=True`).
- `elapsed_ns()` is the race time without pauses. `poll_sources()` lets host driven sources
  deliver events; `pause()`/`resume()` are forwarded to the sources.
- A timing source that fails to start, poll, pause or resume is recorded in `source_errors`; the
  race continues.
- It imports no UI, camera or GPIO code (enforced by import-linter).

Standing start: lap 1 begins at `RaceStarted`.

## Database

SQLAlchemy 2 typed models on SQLite, migrated with Alembic. `Database` enables SQLite foreign
keys, offers `session()` (commit/rollback), `migrate()` and `in_memory()` for tests.

| Owner | Tables |
|---|---|
| core | `plugins`, `settings` |
| `drivers_vehicles` | `drivers` (unique `start_number`), `vehicles` (optional `driver_id`) |
| `tracks` | `tracks`, `track_layouts` |
| `races` | `races`, `race_participants`, `laps`, `sectors` |
| `timing` | `timing_configurations` (one per track), `timing_positions`, `timing_sensors` |

All models share one metadata and one migration history. Create migrations with
`uv run alembic revision --autogenerate -m "message"` (see `alembic.ini`). A test fails if the
migrations and models drift apart. Migration `0002` (drivers, vehicles, tracks, race results)
keeps existing data (for example `nickname` becomes `display_name`) and can be downgraded;
migration `0003` adds `timing_positions` and extends `timing_sensors` (name, position, hardware id,
active flag) without removing columns or rows; the
Alembic environment switches SQLite foreign keys off while tables are rebuilt and verifies them
afterwards. Foreign keys from races to tracks, drivers and vehicles are `RESTRICT`, so used master
data can be deactivated but not deleted.

## UI shell (`carrera.app`)

- `Runtime.create(config)` builds bus, services, contributions, translator and plugin manager,
  migrates the database and enables the configured plugins. It is Qt-free and testable.
- `MainWindow` shows a navigation list and a page stack. The navigation consists of the built-in
  *Dashboard* and *Einstellungen* plus the `NavigationItem`s of enabled plugins, ordered by
  `order`. It is rebuilt when contributions change. Pages are created lazily; a module without a
  `page_factory` gets a placeholder page, and a failing factory yields an error page.
- *Einstellungen* lists the modules with checkboxes to enable/disable them at runtime; the
  choice is saved in the config file.
- Navigation order used by modules: Dashboard (shell), Fahrer 10, Fahrzeuge 20, Strecken 30,
  Rennen 40, Zeitmessung 50, Statistiken 60, Streckenplaner 70, Einstellungen (shell).

## Dependency rules

| Rule | Enforced by |
|---|---|
| `core` ↛ `app`, `modules`, PySide6, cv2, gpiozero, RPi | import-linter |
| modules ↛ other modules | import-linter (independence) |
| modules ↛ `app` | import-linter |
| `app` ↛ `modules` (plugins are discovered) | import-linter |
| race engine, service, recorder, runner, types, models ↛ PySide6, `uikit`, cv2, gpiozero, RPi | import-linter |
| `modules.races.ui` ↛ engine, recorder (no race logic in the UI) | import-linter |
| `modules.timing` ↛ PySide6, `uikit` | import-linter |
| `core` ↛ `uikit`; `uikit` ↛ `app`, `modules` | import-linter |
| race engine ↛ timing modules, `app`, `uikit` (engine knows only the core) | import-linter |
| `tracks.timing_editor`, `tracks.timing_test` ↛ PySide6, `uikit`, cv2, gpiozero, RPi | import-linter |
| Adding a module: add entry point and add it to the independence contract | review |

## Deviations from the proposed layout

- `LICENSE` is a placeholder stating that no license has been chosen yet.
- `pi_agent/` is only a README for now and not a Python package.
- Extra root files: `alembic.ini` (developer CLI) and `.python-version`.
