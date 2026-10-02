"""Lookup and preflight checks for timing providers.

Providers register a :class:`~slot_racing.core.timing.TimingSourceFactory` through their plugin
context; the registry is a view over those registrations, so disabling a plugin removes its
provider automatically. The race module resolves ``provider_id`` to a factory only when a race
starts. Nothing here names a concrete provider.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Sequence
from dataclasses import dataclass

from slot_racing.core.errors import (
    ProviderConfigurationError,
    ProviderUnavailable,
    TimingProviderError,
)
from slot_racing.core.timing import (
    ProviderAvailability,
    ProviderCapabilities,
    TimingSessionSpec,
    TimingSource,
    TimingSourceFactory,
)

logger = logging.getLogger(__name__)

DEFAULT_TIMING_PROVIDER = "simulation"
"""Provider id given to races stored before races knew their provider (database default)."""


@dataclass(frozen=True, slots=True)
class TimingProviderInfo:
    """Read-only description of a registered provider, for lists and selections."""

    provider_id: str
    capabilities: ProviderCapabilities
    availability: ProviderAvailability

    @property
    def available(self) -> bool:
        return self.availability.available


class TimingProviderRegistry:
    def __init__(self, factories: Callable[[], Sequence[TimingSourceFactory]]) -> None:
        self._factories = factories

    def provider_ids(self) -> list[str]:
        return sorted(self._by_id())

    def is_registered(self, provider_id: str) -> bool:
        return provider_id in self._by_id()

    def factory(self, provider_id: str) -> TimingSourceFactory:
        """The factory for ``provider_id``. Raises :class:`ProviderUnavailable` if unknown."""
        factories = self._by_id()
        factory = factories.get(provider_id)
        if factory is None:
            if not factories:
                raise ProviderUnavailable("error.timing_provider.none_registered")
            raise ProviderUnavailable("error.timing_provider.unknown", provider=provider_id)
        return factory

    def info(self, provider_id: str) -> TimingProviderInfo:
        return self._describe(self.factory(provider_id))

    def providers(self) -> list[TimingProviderInfo]:
        """All registered providers ordered by id, each with its current availability."""
        factories = self._by_id()
        return [self._describe(factories[provider_id]) for provider_id in sorted(factories)]

    def default_provider_id(self, preferred: str | None = None) -> str | None:
        """``preferred`` if it is registered and available, otherwise the first available
        provider by id, otherwise ``None``."""
        available = [info.provider_id for info in self.providers() if info.available]
        if preferred is not None and preferred in available:
            return preferred
        return available[0] if available else None

    def check(
        self,
        provider_id: str,
        *,
        lane_count: int | None = None,
        spec: TimingSessionSpec | None = None,
    ) -> TimingSourceFactory:
        """Preflight: is the provider registered, available and able to time the session?

        Returns the factory. Raises :class:`TimingProviderError` (translatable) otherwise.
        """
        factory = self.factory(provider_id)
        availability = self._availability(factory)
        if not availability.available:
            raise ProviderUnavailable(
                availability.reason_key or "error.timing_provider.unavailable",
                **{"provider": provider_id, **availability.reason_params},
            )
        lanes = len(spec.lanes) if spec is not None else lane_count
        if lanes is not None and lanes > 1 and not factory.capabilities.supports_multiple_lanes:
            raise ProviderConfigurationError(
                "error.timing_provider.single_lane", provider=provider_id
            )
        if spec is not None:
            try:
                factory.validate(spec)
            except TimingProviderError:
                raise
            except Exception as error:
                logger.exception("Timing provider %s failed to validate a session", provider_id)
                raise ProviderConfigurationError(
                    "error.timing_provider.failed",
                    provider=provider_id,
                    detail=f"{type(error).__name__}: {error}",
                ) from error
        return factory

    def create_source(self, provider_id: str, spec: TimingSessionSpec) -> TimingSource:
        """Check the provider for ``spec`` and build its source. Raises
        :class:`TimingProviderError`; unexpected errors of the factory are wrapped, never leaked
        as arbitrary exceptions."""
        factory = self.check(provider_id, spec=spec)
        try:
            return factory.create_source(spec)
        except TimingProviderError:
            raise
        except Exception as error:
            logger.exception("Timing provider %s failed to create a source", provider_id)
            raise ProviderUnavailable(
                "error.timing_provider.failed",
                provider=provider_id,
                detail=f"{type(error).__name__}: {error}",
            ) from error

    def _by_id(self) -> dict[str, TimingSourceFactory]:
        result: dict[str, TimingSourceFactory] = {}
        for factory in self._factories():
            provider_id = factory.provider_id
            if not provider_id.strip():
                raise ProviderConfigurationError("error.timing_provider.id_blank")
            if provider_id in result:
                raise ProviderConfigurationError(
                    "error.timing_provider.duplicate", provider=provider_id
                )
            result[provider_id] = factory
        return result

    def _describe(self, factory: TimingSourceFactory) -> TimingProviderInfo:
        return TimingProviderInfo(
            factory.provider_id, factory.capabilities, self._availability(factory)
        )

    @staticmethod
    def _availability(factory: TimingSourceFactory) -> ProviderAvailability:
        try:
            return factory.availability()
        except Exception as error:
            logger.exception(
                "Timing provider %s failed its availability check", factory.provider_id
            )
            return ProviderAvailability.unavailable(
                "error.timing_provider.failed",
                provider=factory.provider_id,
                detail=f"{type(error).__name__}: {error}",
            )
