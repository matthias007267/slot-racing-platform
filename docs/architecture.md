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
| `timing` | `TimingSource` interface |
| `domain` | IDs, `Participant`, `ParticipantResult`, `RaceStatus`, `TimingLayout` |
| `config` | `AppConfig` (pydantic), JSON load/save, per-user paths |
| `storage` | SQLAlchemy base, `Database`, Alembic migrations, core tables |
| `i18n` | Key based `Translator` (German first) |

## Modules (`carrera.modules`)

Every module is a package with a `plugin.py` (a `Plugin` subclass registered as entry point in
`pyproject.toml`) and optionally `models.py` and domain code. Modules never import each other.

| Module | State in this phase |
|---|---|
| `drivers_vehicles` | Models (`Driver`, `Vehicle`), navigation entries |
| `tracks` | Models (`Track`, `TrackLayout`), navigation entry |
| `races` | Models (`Race`, `RaceParticipant`, `Lap`, `Sector`), **race engine**, navigation entry |
| `timing` | Models (`TimingConfiguration`, `TimingSensor`), **`SimulationTimingProvider`**, navigation entry |
| `statistics`, `track_planner` | Placeholder plugin with navigation entry |
| `timing_camera`, `timing_sensor`, `audio_animation` | Placeholder plugin only (camera/sensor off by default) |

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
- `clock`, `config`

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
lap times (per lap), start delays and evenly spaced sensors. It runs on virtual time: it is
advanced with `advance_to`, `advance_by` or `run_to_end` and moves the shared `ManualClock`.

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
- A timing source that fails to start is recorded in `source_errors`; the race continues.
- It imports no UI, camera or GPIO code (enforced by import-linter).

Standing start: lap 1 begins at `RaceStarted`.

## Database

SQLAlchemy 2 typed models on SQLite, migrated with Alembic. `Database` enables SQLite foreign
keys, offers `session()` (commit/rollback), `migrate()` and `in_memory()` for tests.

| Owner | Tables |
|---|---|
| core | `plugins`, `settings` |
| `drivers_vehicles` | `drivers`, `vehicles` |
| `tracks` | `tracks`, `track_layouts` |
| `races` | `races`, `race_participants`, `laps`, `sectors` |
| `timing` | `timing_configurations`, `timing_sensors` |

All models share one metadata and one migration history. Create migrations with
`uv run alembic revision --autogenerate -m "message"` (see `alembic.ini`). A test fails if the
migrations and models drift apart.

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
| `modules.races` ↛ PySide6, cv2, gpiozero, RPi | import-linter |
| Adding a module: add entry point and add it to the independence contract | review |

## Deviations from the proposed layout

- `LICENSE` is a placeholder stating that no license has been chosen yet.
- `pi_agent/` is only a README for now and not a Python package.
- Extra root files: `alembic.ini` (developer CLI) and `.python-version`.
