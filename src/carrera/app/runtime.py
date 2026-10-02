"""Toolkit independent application runtime: wires core services and plugins together."""

from __future__ import annotations

import logging
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from carrera.app.translations import SHELL_TRANSLATIONS
from carrera.core.clock import Clock, MonotonicClock
from carrera.core.config import AppConfig, save_config
from carrera.core.events import EventBus
from carrera.core.i18n import Translator
from carrera.core.messages import CORE_TRANSLATIONS
from carrera.core.plugin import (
    ContributionRegistry,
    Plugin,
    PluginManager,
    ServiceRegistry,
    discover_plugins,
)
from carrera.core.storage import Database

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class Runtime:
    config: AppConfig
    config_path: Path | None
    bus: EventBus
    clock: Clock
    services: ServiceRegistry
    contributions: ContributionRegistry
    translator: Translator
    plugins: PluginManager
    database: Database

    @classmethod
    def create(
        cls,
        config: AppConfig,
        *,
        config_path: Path | None = None,
        database: Database | None = None,
        plugins: Sequence[Plugin] | None = None,
        clock: Clock | None = None,
    ) -> Runtime:
        """Build the runtime, migrate the database and enable all configured plugins.

        ``plugins=None`` discovers installed plugins through entry points.
        """
        bus = EventBus()
        clock = clock or MonotonicClock()
        services = ServiceRegistry()
        contributions = ContributionRegistry()
        translator = Translator(config.language)
        for catalog in (SHELL_TRANSLATIONS, CORE_TRANSLATIONS):
            for language, messages in catalog.items():
                translator.add_catalog(language, messages)

        database = database or Database.from_path(config.resolved_database_path())
        database.migrate()
        services.register(Database, database, owner="core")

        manager = PluginManager(
            bus=bus,
            services=services,
            contributions=contributions,
            translator=translator,
            clock=clock,
            config=config,
        )
        if plugins is None:
            discovery = discover_plugins()
            plugins = discovery.plugins
            for name, error in discovery.failures.items():
                manager.record_load_failure(name, error)
        for plugin in plugins:
            manager.register(plugin)

        manager.enable_many(
            name
            for name in manager.plugin_names()
            if config.is_plugin_enabled(name, manager.manifest_default_enabled(name))
        )
        return cls(
            config=config,
            config_path=config_path,
            bus=bus,
            clock=clock,
            services=services,
            contributions=contributions,
            translator=translator,
            plugins=manager,
            database=database,
        )

    def set_plugin_enabled(self, name: str, enabled: bool) -> None:
        """Enable or disable a plugin and remember the choice. Raises ``PluginError``."""
        if enabled:
            self.plugins.enable(name)
        else:
            self.plugins.disable(name)
        self.config.plugin_overrides[name] = enabled
        if self.config_path is not None:
            try:
                save_config(self.config, self.config_path)
            except OSError:
                logger.exception("Could not save configuration")

    def shutdown(self) -> None:
        self.plugins.shutdown()
        self.database.dispose()
