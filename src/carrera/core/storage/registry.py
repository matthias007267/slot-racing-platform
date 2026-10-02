"""Makes sure the models of all installed plugins are registered in ``Base.metadata``."""

from __future__ import annotations

import importlib
import logging

from carrera.core.plugin.discovery import discover_plugins

logger = logging.getLogger(__name__)


def import_plugin_models() -> list[str]:
    """Import the model module declared in each plugin manifest. Returns the imported names."""
    imported: list[str] = []
    for plugin in discover_plugins().plugins:
        module_name = plugin.manifest.models_module
        if module_name is None:
            continue
        try:
            importlib.import_module(module_name)
            imported.append(module_name)
        except Exception:
            logger.exception("Could not import models of plugin %s", plugin.manifest.name)
    return imported
