"""Synchronous in-process event bus with per-handler error isolation."""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any, Protocol, TypeVar

from carrera.core.events.base import Event

logger = logging.getLogger(__name__)

E = TypeVar("E", bound=Event)
ErrorCallback = Callable[[Event, Callable[[Any], None], Exception], None]


class EventDispatcher(Protocol):
    """The part of the bus that modules use. Implemented by the bus and by scoped views."""

    def publish(self, event: Event) -> None: ...

    def subscribe(self, event_type: type[E], handler: Callable[[E], None]) -> Subscription: ...


class Subscription:
    """Handle returned by ``subscribe``. Call :meth:`cancel` to stop receiving events."""

    __slots__ = ("_active", "event_type", "handler")

    def __init__(self, event_type: type[Event], handler: Callable[[Any], None]) -> None:
        self.event_type = event_type
        self.handler = handler
        self._active = True

    @property
    def active(self) -> bool:
        return self._active

    def cancel(self) -> None:
        self._active = False


class EventBus:
    """Delivers events to handlers registered for the event type or one of its base classes.

    Delivery is synchronous and in subscription order. An exception raised by one handler is
    logged (and passed to ``on_error``) but never reaches the publisher or other handlers.
    """

    def __init__(self, on_error: ErrorCallback | None = None) -> None:
        self._subscriptions: list[Subscription] = []
        self._on_error = on_error

    def subscribe(self, event_type: type[E], handler: Callable[[E], None]) -> Subscription:
        subscription = Subscription(event_type, handler)
        self._subscriptions.append(subscription)
        return subscription

    def publish(self, event: Event) -> None:
        self._subscriptions = [s for s in self._subscriptions if s.active]
        for subscription in list(self._subscriptions):
            if not subscription.active or not isinstance(event, subscription.event_type):
                continue
            try:
                subscription.handler(event)
            except Exception as error:
                logger.exception(
                    "Event handler %r failed for %s", subscription.handler, type(event).__name__
                )
                if self._on_error is not None:
                    try:
                        self._on_error(event, subscription.handler, error)
                    except Exception:
                        logger.exception("Event bus error callback failed")
