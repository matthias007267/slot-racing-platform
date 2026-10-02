"""Minimal, Qt-free translation lookup. Source keys are English, the first catalog is German."""

from __future__ import annotations

from collections.abc import Mapping

DEFAULT_LANGUAGE = "de"


class Translator:
    """Looks up keys in the current language, then the default language, then the key itself."""

    def __init__(self, language: str = DEFAULT_LANGUAGE) -> None:
        self.language = language
        self._catalogs: dict[str, dict[str, str]] = {}

    def add_catalog(self, language: str, messages: Mapping[str, str]) -> None:
        self._catalogs.setdefault(language, {}).update(messages)

    def translate(self, key: str, default: str | None = None) -> str:
        for language in (self.language, DEFAULT_LANGUAGE):
            message = self._catalogs.get(language, {}).get(key)
            if message is not None:
                return message
        return default if default is not None else key
