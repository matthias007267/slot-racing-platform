"""Errors that are meant to be shown to the user."""

from __future__ import annotations


class ValidationError(ValueError):
    """Invalid input or a violated business rule. It is a ``ValueError`` so domain objects can
    reject invalid construction with it.

    ``key`` is a translation key and ``params`` are its format arguments, so the message can be
    shown in any language by the UI (``Translator.format``).
    """

    def __init__(self, key: str, **params: object) -> None:
        super().__init__(key)
        self.key = key
        self.params = params


class TimingProviderError(ValidationError):
    """A timing provider cannot be used. Translatable like any user facing error, so the UI shows
    it next to the race configuration instead of failing the application."""


class ProviderUnavailable(TimingProviderError):  # noqa: N818
    """The provider is registered but cannot time a race right now (not connected, not
    implemented, ...). Raised before the race starts."""


class ProviderConfigurationError(TimingProviderError):
    """The provider is available but does not support the requested session or setup."""
