# 0006 One SQLite database, one migration history, module-owned models

## Context
Persistence must be modular, but a desktop application with a single SQLite file does not need a
database per module.

## Decision
- SQLAlchemy 2 (typed `Mapped` models), SQLite, Alembic.
- All models share one `Base`/`MetaData` (with a naming convention) from `carrera.core.storage`.
- Each plugin owns its models and declares them in `PluginManifest.models_module`. The Alembic
  environment imports the models of all installed plugins.
- One linear migration history in `carrera/core/storage/migrations`, applied automatically at
  startup (`Database.migrate`). SQLite foreign keys are always enabled.
- Tables may reference other modules' tables by name in foreign keys; the Python model modules
  never import each other.

## Consequences
- Disabling a module never touches the schema. Physically removing a module that owns referenced
  tables needs a migration.
- A test verifies that the migrations match the models.
- Persistence of race results is done by `RaceRecorder` (see ADR 0007).
