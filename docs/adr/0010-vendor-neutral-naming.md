# ADR 0010: Manufacturer-neutral naming

## Context
The project started under a name that referred to one slot-car manufacturer. The platform is a
general slot-racing application; it is not affiliated with any manufacturer and must not suggest
otherwise. Core, race engine, track model and timing abstraction never depended on a manufacturer.

## Decision
- Project name: **Slot-Racing Platform** (`slot-racing-platform`).
- Python package `slot_racing` (was the old brand name), console command `slot-racing`,
  plugin entry-point group `slot_racing.plugins`, environment variable `SLOT_RACING_HOME`,
  data directory `SlotRacingPlatform`, database file `slot_racing.db`.
- Module names were already neutral (`drivers_vehicles`, `tracks`, `races`, `timing`,
  `timing_camera`, `timing_sensor`, ...) and are unchanged. Plugin ids are unchanged, so stored
  plugin settings keep working.
- Manufacturer hardware is an optional, separate timing provider (`TimingSourceFactory` with its
  own provider id). No such provider exists yet and none is created as a placeholder.
- No database change: no table, column or migration name contained the old brand. The migration
  history is untouched.

## Compatibility
Existing installations keep working without any data being moved or deleted:
- `CARRERA_HOME` is still honored when `SLOT_RACING_HOME` is not set.
- An existing `CarreraRacingPlatform` data directory is used when no `SlotRacingPlatform`
  directory exists.
- An existing `carrera.db` is used when no `slot_racing.db` exists in the data directory.
- A `database_path` stored explicitly in `config.json` is unaffected.

These legacy names are the only remaining occurrences of the old brand in the code base
(`core/config/paths.py`, its tests, the README note and this ADR).

## Consequences
- Imports, entry points and the CLI command changed; third-party plugins must register in the
  `slot_racing.plugins` group.
- The GitHub repository still carries its old name until it is renamed manually in the
  repository settings (it cannot be done from the code base); the `slot-racing-platform` name in
  `README.md`, `pyproject.toml` and `LICENSE` already follows the new name, so the clone URL in
  the README must be updated after that rename.
