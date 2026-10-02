"""Turns exceptions into messages for the user."""

from __future__ import annotations

from sqlalchemy.exc import SQLAlchemyError

from carrera.core.errors import ValidationError
from carrera.core.i18n import Translator


def describe_error(translator: Translator, error: BaseException) -> str:
    """User facing text for ``error``.

    Validation errors carry their own translation key. Database errors get a generic message;
    anything else is reported with its type so it can be diagnosed. The caller is responsible
    for logging unexpected errors.
    """
    if isinstance(error, ValidationError):
        return translator.format(error.key, **error.params)
    if isinstance(error, SQLAlchemyError):
        return translator.translate("error.database")
    return translator.format("error.unexpected", detail=f"{type(error).__name__}: {error}")


def is_expected(error: BaseException) -> bool:
    """Expected errors are user mistakes and are not logged as failures."""
    return isinstance(error, ValidationError)
