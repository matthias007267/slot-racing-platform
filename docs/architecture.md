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
| `timing` | `TimingSource`, `TimingSourceFactory`, `TimingSessionSpec` |
| `catalog` | `DriverCatalog`, `VehicleCatalog`, `TrackCatalog` and their read-only info types |
| `errors` | `ValidationError(key, **params)` for translatable user errors |
| `domain` | IDs, `Participant`, `ParticipantResult`, `RaceStatus`, `TimingLayout` |
| `config` | `AppConfig` (pydantic), JSON load/save, per-user paths |
| `storage` | SQLAlchemy base, `Database`, Alembic migrations, core tables |
| `i18n` | Key based `Translator` (German first), `format` fills placeholders |

## Modules (`carrera.modules`)

Every module is a package with a `plugin.py` (a `Plugin` subclass registered as entry point in
`pyproject.toml`) and optionally `models.py` and domain code. Modules never import each other.

| Module | State |
|---|---|
| `drivers_vehicles` | Models, `DriverService`, `VehicleService` (implement the catalogs), driver and vehicle pages |
| `tracks` | Models (`Track`, `TrackLayout`), `TrackService` (implements `TrackCatalog`), track page |
| `races` | Models, **race engine**, `RaceService`, `RaceRecorder`, `RaceController`/`RaceRunner`, race pages (list, 6-step flow, live view, results). Requires `drivers_vehicles` and `tracks`, optionally `timing` |
| `timing` | Models, **`SimulationTimingProvider`** and its `TimingSourceFactory` |
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

- Timing: `SensorTriggered(source_id, sensor_id, lane)`
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

`TimingSource` knows nothing about its origin. `TimingLayout` describes the logical sequence of
timing points: `START_FINISH`, `SECTOR_1`, … `SECTOR_n`. Sector `k` ends at the k-th point of the
lap sequence; the last sector ends at `START_FINISH` and completes the lap. The future
configuration wizard (drive over each sensor in order) produces such a layout.

`SimulationTimingProvider(clock, layout, lanes, laps)` simulates several lanes with individual
lap times (per lap), start delays and evenly spaced sensors. With a `ManualClock` it is advanced
with `advance_to`, `advance_by` or `run_to_end` and moves the clock. With any clock the host calls
`poll()`, which delivers the events that are due; `pause()`/`resume()` shift the remaining
schedule. `SimulationTimingFactory` registers the simulation as a `TimingSourceFactory` service
(`name="simulation"`) and creates one source per race.

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
| `timing` | `timing_configurations`, `timing_sensors` |

All models share one metadata and one migration history. Create migrations with
`uv run alembic revision --autogenerate -m "message"` (see `alembic.ini`). A test fails if the
migrations and models drift apart. Migration `0002` (drivers, vehicles, tracks, race results)
keeps existing data (for example `nickname` becomes `display_name`) and can be downgraded; the
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
| Adding a module: add entry point and add it to the independence contract | review |

## Deviations from the proposed layout

- `LICENSE` is a placeholder stating that no license has been chosen yet.
- `pi_agent/` is only a README for now and not a Python package.
- Extra root files: `alembic.ini` (developer CLI) and `.python-version`.
