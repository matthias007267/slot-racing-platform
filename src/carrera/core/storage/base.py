"""Declarative base shared by the core and all plugin-owned models."""

from __future__ import annotations

from sqlalchemy import MetaData
from sqlalchemy.orm import DeclarativeBase

NAMING_CONVENTION = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_N_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    """All tables live in one metadata so there is a single migration history.

    Tables of different plugins may reference each other by table name in foreign keys, but
    their Python model modules never import each other.
    """

    metadata = MetaData(naming_convention=NAMING_CONVENTION)
