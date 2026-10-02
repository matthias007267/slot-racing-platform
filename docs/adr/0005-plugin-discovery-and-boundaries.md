# 0005 Entry-point discovery and import-linter enforced boundaries

## Context
Module independence must survive years of development and must be checked automatically.

## Decision
- Plugins are registered in `pyproject.toml` under the entry-point group `slot_racing.plugins`. The
  app shell discovers them with `importlib.metadata` and never imports a module directly.
- A plugin that fails to import or activate is marked `FAILED`; everything else keeps running.
- `import-linter` contracts (run in CI) enforce:
  - `slot_racing.core` does not import `app`, `modules`, PySide6, OpenCV or GPIO libraries;
  - modules are independent of each other and of the app shell;
  - the app shell does not import modules;
  - the race engine does not import UI or hardware libraries.
- Plugin enablement is configured with explicit overrides; the manifest provides the default
  (`timing_camera` and `timing_sensor` are off by default).

## Consequences
- A third-party or removed module needs no change in the app or other modules.
- After adding a module, its entry point and its line in the independence contract must be added.
- The package must be installed (`uv sync`) for discovery to see the entry points.
