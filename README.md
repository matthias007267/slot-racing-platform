# Carrera Racing Platform

Modular desktop application for managing and running Carrera slot-car races.

The long-term scope covers drivers and vehicles, tracks and layout planning, races, lap and
sector times, camera and Raspberry Pi based timing, statistics, audio and race presentation.
The software is a building-block system: a small core plus optional modules (plugins) that can be
developed, enabled, disabled, replaced or removed independently. Modules never import each other;
they communicate through standardized events and core interfaces.

## Status

Implemented: core (events, plugin system, config, domain types, storage), timing abstraction with
a simulator, a hardware independent race engine, database with migrations and a PySide6 shell with
dynamic navigation. Usable in the application:

- **Fahrer, Fahrzeuge, Strecken:** list, create, edit, deactivate and delete with validation
  (unique driver start numbers, lane count, driver assignment for vehicles).
- **Rennen:** six step setup (name, track, mode with laps, participants with driver, vehicle and
  lane, overview, start), live view with positions and lap times, and stored results that can be
  reopened later. Timing is simulated (`SimulationTimingProvider`).
- **Timing-Konfiguration (Strecken):** each track can have a timing layout of logical positions
  (start/finish plus any number of sectors) with a sensor assigned to every position. Editor,
  six step wizard and a test mode with simulated events; the simulation and later timing
  providers use the stored layout. Tracks without a configuration use a default layout.

**Not** implemented yet: camera detection, Raspberry Pi/GPIO, Carrera hardware, track planner,
audio/animations, statistics, time limited races.

See [docs/architecture.md](docs/architecture.md) and the [ADRs](docs/adr/README.md).

## Requirements

- Python 3.12 or newer (uv installs it for you if missing)
- [uv](https://docs.astral.sh/uv/)
- Windows is the primary target. Linux and macOS work for development.
- Linux only: Qt needs a few system libraries, for example on Ubuntu
  `sudo apt install libegl1 libgl1 libxkbcommon0 libdbus-1-3 libfontconfig1 libglib2.0-0`

## Installation

```bash
git clone https://github.com/matthias007267/carrera-racing-platform.git
cd carrera-racing-platform
uv sync
```

`uv sync` installs the project in editable mode, which is also required for module discovery
(entry points).

### Cursor Cloud / Web environment

The repository ships `.cursor/environment.json`, which Cursor uses with priority over any
dashboard-managed environment. Its `install` command runs `.cursor/install.sh` on a fresh Ubuntu
VM (during environment builds, or on agent boot when no build exists). The script is idempotent
and does exactly what the manual installation above needs, nothing more:

1. installs the Qt system libraries via `apt` if they are missing,
2. installs the pinned `uv` version (`UV_VERSION`, default `0.12.22`) and links it into
   `/usr/local/bin`, because install/start run in non-interactive login shells that do not load
   `~/.bashrc`,
3. runs `uv sync --locked`, so the environment matches `uv.lock` exactly (it fails if the lock
   file is out of date; refresh it with `uv lock`).

No `start` command is configured because the application has no background services. Run the
script locally with `bash .cursor/install.sh`. The GitHub Actions workflow does not use it; it
installs uv with `astral-sh/setup-uv` and runs `uv sync --locked` itself.

## Run

```bash
uv run carrera                   # start the desktop application
uv run python -m carrera.app     # equivalent
uv run carrera --smoke-test      # start, show the window briefly, exit (headless friendly)
```

Data location: `%APPDATA%\CarreraRacingPlatform` on Windows, `~/.local/share/CarreraRacingPlatform`
elsewhere. Set `CARRERA_HOME` to use another directory. It contains `config.json` and `carrera.db`.
The database schema is migrated automatically on startup.

Modules can be switched on and off under *Einstellungen*. The camera and sensor timing modules
are off by default.

## Tests

```bash
uv run pytest
```

Tests need no hardware. Qt tests run headless (`QT_QPA_PLATFORM=offscreen` is set in
`tests/conftest.py`).

## Development commands

| Task | Command |
|---|---|
| Install / update dependencies | `uv sync` |
| Tests | `uv run pytest` |
| Lint | `uv run ruff check .` |
| Format | `uv run ruff format .` |
| Type check | `uv run mypy src tests` |
| Architecture rules | `uv run lint-imports` |
| New DB migration | `uv run alembic revision --autogenerate -m "message"` |

Run everything CI runs:

```bash
uv run ruff check . && uv run ruff format --check . && uv run mypy src tests \
  && uv run lint-imports && uv run pytest
```

## Repository layout

```text
src/carrera/core/      events, plugin system, timing interface, config, domain, storage
src/carrera/app/       desktop shell (PySide6) and runtime wiring
src/carrera/uikit/     small Qt helpers shared by module pages (depends on the core only)
src/carrera/modules/   feature modules (plugins): drivers_vehicles, tracks, races, timing,
                       timing_camera, timing_sensor, track_planner, audio_animation, statistics
pi_agent/              future Raspberry Pi agent (placeholder)
tests/                 unit and integration tests, no hardware needed
docs/                  architecture documentation and ADRs
```

## Adding a module

1. Create `src/carrera/modules/<name>/` with a `plugin.py` containing a `Plugin` subclass.
2. Register it in `pyproject.toml` under `[project.entry-points."carrera.plugins"]`.
3. Add it to the independence contract in `[tool.importlinter]`.
4. Run `uv sync`, then the checks above.

## License

No license has been chosen yet (see `LICENSE`).
