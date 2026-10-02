"""Errors that are meant to be shown to the user."""

from __future__ import annotations


class ValidationError(Exception):
    """Invalid input or a violated business rule.

    ``key`` is a translation key and ``params`` are its format arguments, so the message can be
    shown in any language by the UI (``Translator.format``).
    """

    def __init__(self, key: str, **params: object) -> None:
        super().__init__(key)
        self.key = key
        self.params = params
