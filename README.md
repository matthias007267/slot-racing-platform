# Slot-Racing Platform

Slot-Racing Platform is a modular desktop application for managing slot-racing tracks, drivers,
vehicles, races and timing systems.

The platform is manufacturer-independent. It is an independent project and is not affiliated with,
endorsed by or supported by any slot-car manufacturer. Manufacturer-specific hardware can be added
later as optional, separate timing providers; the core, race engine, track model and timing
abstraction do not depend on any manufacturer.

The long-term scope covers drivers and vehicles, tracks and layout planning, races, lap and
sector times, camera and Raspberry Pi based timing, statistics, audio and race presentation.
The software is a building-block system: a small core plus optional modules (plugins) that can be
developed, enabled, disabled, replaced or removed independently. Modules never import each other;
they communicate through standardized events and core interfaces.

## Status

Implemented: core (events, plugin system, config, domain types, storage), timing abstraction with
a simulator, a hardware independent race engine, database with migrations and a dark PySide6
shell with a sidebar and dynamic navigation. Usable in the application:

- **Fahrer, Fahrzeuge, Strecken:** list, create, edit, deactivate and delete with validation
  (unique driver start numbers, lane count, driver assignment for vehicles).
- **Rennen:** six step setup (name, track, mode with laps, participants with driver, vehicle and
  lane, overview, start), a live view of the engine standings (position, laps, lap times, the
  selected driver) with pause, resume and abort, and stored results that can be reopened later.
  The live view can return to the race list without stopping the race. Timing comes from a
  selectable timing provider. The simulation is built in.
  Camera timing is an optional module, off by default, and a camera race uses the saved camera
  configuration. Further providers register themselves and then appear in the selection.
- **Timing-Konfiguration (Strecken):** each track can have a timing layout of logical positions
  (start/finish plus any number of sectors) with a sensor assigned to every position. Editor,
  six step wizard and a test mode with simulated events; the simulation and the camera provider
  use the stored layout. Tracks without a configuration use a default layout.
- **Kamera-Timing:** one global camera and its detection zones, edited on the live picture.
  Saving that document is what the next camera race uses.

**Not** implemented yet: automatic vehicle detection, Raspberry Pi/GPIO, manufacturer-specific
hardware, track planner, audio/animations, statistics, time limited races.

See [docs/architecture.md](docs/architecture.md) and the [ADRs](docs/adr/README.md).

## Requirements

- Python 3.12 or newer (uv installs it for you if missing)
- [uv](https://docs.astral.sh/uv/)
- Windows is the primary target. Linux and macOS work for development.
- Linux only: Qt needs a few system libraries, for example on Ubuntu
  `sudo apt install libegl1 libgl1 libxkbcommon0 libdbus-1-3 libfontconfig1 libglib2.0-0`

## Installation

```bash
git clone https://github.com/matthias007267/slot-racing-platform.git
cd slot-racing-platform
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
uv run slot-racing                   # start the desktop application
uv run python -m slot_racing.app     # equivalent
uv run slot-racing --smoke-test      # start, show the window briefly, exit (headless friendly)
```

On Windows, double-click `Start.bat` in the project folder. A desktop shortcut to that file
works as well: the script switches to the project directory and runs `uv run slot-racing`.
The project environment does not have to be activated by hand. If `uv` is not available but
`.venv` already exists, the script uses that environment's Python with the same entry point
(`python -m slot_racing.app`). When the start fails, the window stays open and shows the error.

Data location: `%APPDATA%\SlotRacingPlatform` on Windows, `~/.local/share/SlotRacingPlatform`
elsewhere. Set `SLOT_RACING_HOME` to use another directory. It contains `config.json` and
`slot_racing.db`. Installations created before the project was renamed keep working: an existing
`CarreraRacingPlatform` directory, a `carrera.db` file and the `CARRERA_HOME` variable are still
recognized and never moved or deleted (see [ADR 0010](docs/adr/0010-vendor-neutral-naming.md)).
The database schema is migrated automatically on startup.

Modules can be switched on and off under *Einstellungen*. The camera and sensor timing modules
are off by default.

## Tests

```bash
uv run pytest
```

Tests need no hardware. Qt tests run headless (`QT_QPA_PLATFORM=offscreen` is set in
`tests/conftest.py`). The camera hardware test is not part of this run.

## Kamera-Hardware-Test

`uv run pytest` und die CI brauchen keine Kamera und führen den Hardware-Test nicht aus.
Ohne Kamera ist auch der optionale Lauf kein Fehler: der Test wird übersprungen.

```bash
uv run pytest -m camera_hardware
```

Manuell, mit einem Fahrzeug auf der Bahn:

1. Kamera anschließen
2. Kamera-Timing öffnen
3. Kamera auswählen
4. Livebild prüfen
5. Detection-Zonen konfigurieren
6. Konfiguration speichern
7. Rennen starten
8. Fahrzeug durch die Zonen fahren
9. Rundenzeiten prüfen

Die gespeicherte Auflösung ist ein Wunsch an die Kamera. Solange nichts anderes gespeichert
ist, gilt 640×480 bei 30 Bildern pro Sekunde. Der Treiber darf eine andere Bildgröße liefern.
Die Zonen sind Anteile dieses Bildes und müssen im Kamerabild liegen. Ein Fahrzeug wird nur
erkannt, wenn es durch die gezeichnete Zone fährt.

Stelle die Kamera so auf, dass jede befahrene Spur ihre Zone im Bild durchquert, möglichst
mit wenig Gegenlicht und ohne dass die Zone von der Bahnkante oder einem Schatten dauerhaft
gefüllt ist. Es ist kein bestimmtes Kameramodell erforderlich.

Der automatische Hardware-Test vergleicht das Livebild mit einem schwarzen Referenzbild.
Die Kamera muss dafür eine beleuchtete Szene sehen, sonst entsteht keine Durchfahrt.

## Development commands

| Task | Command |
|---|---|
| Install / update dependencies | `uv sync` |
| Tests | `uv run pytest` |
| Camera hardware test (optional, not in CI) | `uv run pytest -m camera_hardware` |
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
src/slot_racing/core/      events, plugin system, timing interface, config, domain, storage
src/slot_racing/app/       desktop shell (PySide6) and runtime wiring
src/slot_racing/uikit/     small Qt helpers shared by module pages (depends on the core only)
src/slot_racing/modules/   feature modules (plugins): drivers_vehicles, tracks, races, timing,
                       timing_camera, timing_sensor, track_planner, audio_animation, statistics
pi_agent/              future Raspberry Pi agent (placeholder)
tests/                 unit and integration tests; a real camera is only the opt-in hardware test
docs/                  architecture documentation and ADRs
```

## Adding a module

1. Create `src/slot_racing/modules/<name>/` with a `plugin.py` containing a `Plugin` subclass.
2. Register it in `pyproject.toml` under `[project.entry-points."slot_racing.plugins"]`.
3. Add it to the independence contract in `[tool.importlinter]`.
4. Run `uv sync`, then the checks above.

## License

No license has been chosen yet (see `LICENSE`).
