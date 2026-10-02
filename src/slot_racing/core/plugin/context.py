"""The only handle a plugin gets to the application."""

from __future__ import annotations

from collections.abc import Callable
from typing import TypeVar

from slot_racing.core.clock import Clock
from slot_racing.core.config import AppConfig
from slot_racing.core.events import Event, EventDispatcher, Subscription
from slot_racing.core.i18n import Translator
from slot_racing.core.plugin.contributions import ContributionRegistry, NavigationItem
from slot_racing.core.plugin.services import ServiceRegistry
from slot_racing.core.timing import TimingSourceFactory

T = TypeVar("T")
E = TypeVar("E", bound=Event)


class ScopedEventBus:
    """Event bus view that remembers subscriptions so they can be removed with the plugin."""

    def __init__(self, bus: EventDispatcher) -> None:
        self._bus = bus
        self._subscriptions: list[Subscription] = []

    def publish(self, event: Event) -> None:
        self._bus.publish(event)

    def subscribe(self, event_type: type[E], handler: Callable[[E], None]) -> Subscription:
        subscription = self._bus.subscribe(event_type, handler)
        self._subscriptions.append(subscription)
        return subscription

    def release(self) -> None:
        for subscription in self._subscriptions:
            subscription.cancel()
        self._subscriptions.clear()


class PluginContext:
    """Per-plugin facade over the event bus, service registry and contribution registry."""

    def __init__(
        self,
        plugin_name: str,
        *,
        bus: EventDispatcher,
        services: ServiceRegistry,
        contributions: ContributionRegistry,
        clock: Clock,
        config: AppConfig,
        translator: Translator,
    ) -> None:
        self.plugin_name = plugin_name
        self.events = ScopedEventBus(bus)
        self.clock = clock
        self.config = config
        self.translator = translator
        self._services = services
        self._contributions = contributions

    def register_service(
        self, interface: type[T], implementation: T, name: str | None = None
    ) -> None:
        self._services.register(interface, implementation, owner=self.plugin_name, name=name)

    def register_timing_provider(self, factory: TimingSourceFactory) -> None:
        """Offer a timing provider. Its ``provider_id`` must be unique; the registration is
        removed when this plugin is disabled."""
        self._services.register(
            TimingSourceFactory, factory, owner=self.plugin_name, name=factory.provider_id
        )

    def get_service(self, interface: type[T], name: str | None = None) -> T:
        return self._services.get(interface, name)

    def find_service(self, interface: type[T], name: str | None = None) -> T | None:
        return self._services.find(interface, name)

    def find_services(self, interface: type[T]) -> list[T]:
        return self._services.find_all(interface)

    def add_navigation(self, item: NavigationItem) -> None:
        self._contributions.add_navigation(self.plugin_name, item)

    def release(self) -> None:
        """Remove everything this plugin registered. Called by the plugin manager."""
        self.events.release()
        self._services.remove_owner(self.plugin_name)
        self._contributions.remove_owner(self.plugin_name)
