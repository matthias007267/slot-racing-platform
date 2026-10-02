"""Plugin lifecycle management with failure isolation."""

from __future__ import annotations

import logging
from collections.abc import Iterable
from dataclasses import dataclass
from enum import StrEnum

from slot_racing.core.clock import Clock
from slot_racing.core.config import AppConfig
from slot_racing.core.events import EventDispatcher, PluginDisabled, PluginEnabled
from slot_racing.core.i18n import Translator
from slot_racing.core.plugin.context import PluginContext
from slot_racing.core.plugin.contributions import ContributionRegistry
from slot_racing.core.plugin.errors import PluginActivationError, PluginDependencyError, PluginError
from slot_racing.core.plugin.plugin import Plugin
from slot_racing.core.plugin.services import ServiceRegistry

logger = logging.getLogger(__name__)


class PluginState(StrEnum):
    REGISTERED = "registered"
    """Known but not activated yet."""
    ENABLED = "enabled"
    DISABLED = "disabled"
    FAILED = "failed"


@dataclass(frozen=True, slots=True)
class PluginStatus:
    name: str
    version: str
    title: str
    state: PluginState
    error: str | None = None


@dataclass(slots=True)
class _Record:
    plugin: Plugin
    state: PluginState = PluginState.REGISTERED
    error: str | None = None
    context: PluginContext | None = None


class PluginManager:
    """Registers plugins and activates/deactivates them.

    Activation errors never propagate out of :meth:`enable_many`; the offending plugin is marked
    ``FAILED`` and everything it registered is rolled back. Disabling a plugin first disables
    all plugins that require it.
    """

    def __init__(
        self,
        *,
        bus: EventDispatcher,
        services: ServiceRegistry,
        contributions: ContributionRegistry,
        translator: Translator,
        clock: Clock,
        config: AppConfig,
    ) -> None:
        self._bus = bus
        self._services = services
        self._contributions = contributions
        self._translator = translator
        self._clock = clock
        self._config = config
        self._records: dict[str, _Record] = {}
        self._load_failures: dict[str, str] = {}
        self._activation_order: list[str] = []

    def register(self, plugin: Plugin) -> None:
        name = plugin.manifest.name
        if name in self._records:
            raise PluginError(f"plugin {name!r} is already registered")
        self._records[name] = _Record(plugin)
        for language, messages in plugin.translations.items():
            self._translator.add_catalog(language, messages)

    def record_load_failure(self, name: str, error: str) -> None:
        """Remember a plugin that could not even be imported so the UI can report it."""
        self._load_failures[name] = error

    def plugin_names(self) -> list[str]:
        return sorted(self._records)

    def is_enabled(self, name: str) -> bool:
        record = self._records.get(name)
        return record is not None and record.state is PluginState.ENABLED

    def statuses(self) -> list[PluginStatus]:
        statuses = [
            PluginStatus(
                name=name,
                version=record.plugin.manifest.version,
                title=self._translator.translate(
                    f"plugin.{name}.title", record.plugin.manifest.title
                ),
                state=record.state,
                error=record.error,
            )
            for name, record in sorted(self._records.items())
        ]
        statuses.extend(
            PluginStatus(name, "", name, PluginState.FAILED, error)
            for name, error in sorted(self._load_failures.items())
        )
        return statuses

    def manifest_default_enabled(self, name: str) -> bool:
        return self._record(name).plugin.manifest.enabled_by_default

    def enable(self, name: str) -> None:
        """Activate a plugin whose required plugins are already enabled."""
        record = self._record(name)
        if record.state is PluginState.ENABLED:
            return
        for required in record.plugin.manifest.requires:
            required_record = self._records.get(required)
            if required_record is None or required_record.state is not PluginState.ENABLED:
                raise PluginDependencyError(f"{name} requires {required}, which is not enabled")

        context = PluginContext(
            name,
            bus=self._bus,
            services=self._services,
            contributions=self._contributions,
            clock=self._clock,
            config=self._config,
            translator=self._translator,
        )
        try:
            record.plugin.activate(context)
        except Exception as error:
            logger.exception("Plugin %s failed to activate", name)
            self._safe_deactivate(record.plugin)
            context.release()
            self._mark_failed(record, f"{type(error).__name__}: {error}")
            raise PluginActivationError(f"plugin {name} failed to activate: {error}") from error

        record.context = context
        record.state = PluginState.ENABLED
        record.error = None
        self._activation_order.append(name)
        self._bus.publish(
            PluginEnabled(
                timestamp_ns=self._clock.now_ns(),
                plugin_name=name,
                plugin_version=record.plugin.manifest.version,
            )
        )

    def enable_many(self, names: Iterable[str]) -> None:
        """Activate plugins in dependency order. Failures are recorded, never raised."""
        wanted = [name for name in names if name in self._records]
        ordered, cyclic = self._dependency_order(wanted)
        for name in cyclic:
            self._mark_failed(self._records[name], "circular plugin dependency")
        for name in ordered:
            try:
                self.enable(name)
            except PluginActivationError:
                pass
            except PluginError as error:
                self._mark_failed(self._records[name], str(error))

    def disable(self, name: str) -> None:
        """Deactivate a plugin, after deactivating every plugin that requires it."""
        record = self._record(name)
        if record.state is not PluginState.ENABLED:
            if record.state is PluginState.FAILED:
                record.state = PluginState.DISABLED
                record.error = None
            return
        for other_name, other in list(self._records.items()):
            if other.state is PluginState.ENABLED and name in other.plugin.manifest.requires:
                self.disable(other_name)

        self._safe_deactivate(record.plugin)
        if record.context is not None:
            record.context.release()
            record.context = None
        record.state = PluginState.DISABLED
        if name in self._activation_order:
            self._activation_order.remove(name)
        self._bus.publish(PluginDisabled(timestamp_ns=self._clock.now_ns(), plugin_name=name))

    def shutdown(self) -> None:
        for name in reversed(list(self._activation_order)):
            self.disable(name)

    def _record(self, name: str) -> _Record:
        try:
            return self._records[name]
        except KeyError:
            raise PluginError(f"unknown plugin {name!r}") from None

    @staticmethod
    def _mark_failed(record: _Record, message: str) -> None:
        record.state = PluginState.FAILED
        record.error = message

    @staticmethod
    def _safe_deactivate(plugin: Plugin) -> None:
        try:
            plugin.deactivate()
        except Exception:
            logger.exception("Plugin %s failed to deactivate cleanly", plugin.manifest.name)

    def _dependency_order(self, names: list[str]) -> tuple[list[str], list[str]]:
        """Topologically sort ``names`` (required and optional dependencies first).

        Returns ``(ordered, cyclic)``."""
        selected = set(names)
        pending = {
            name: {
                dep
                for dep in (
                    *self._records[name].plugin.manifest.requires,
                    *self._records[name].plugin.manifest.optional,
                )
                if dep in selected
            }
            for name in selected
        }
        ordered: list[str] = []
        while pending:
            ready = sorted(name for name, deps in pending.items() if not deps)
            if not ready:
                return ordered, sorted(pending)
            for name in ready:
                ordered.append(name)
                del pending[name]
            for deps in pending.values():
                deps.difference_update(ready)
        return ordered, []
