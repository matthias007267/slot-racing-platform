"""Alembic environment. Works with an injected connection (application) or a URL (CLI)."""

from __future__ import annotations

from alembic import context
from sqlalchemy import create_engine

from carrera.core.storage.base import Base
from carrera.core.storage.registry import import_plugin_models

import_plugin_models()
config = context.config
target_metadata = Base.metadata


def run_migrations() -> None:
    connection = config.attributes.get("connection")
    if connection is not None:
        _migrate(connection)
        return
    engine = create_engine(config.get_main_option("sqlalchemy.url") or "")
    with engine.connect() as new_connection:
        _migrate(new_connection)
        new_connection.commit()


def _migrate(connection: object) -> None:
    context.configure(
        connection=connection,  # type: ignore[arg-type]
        target_metadata=target_metadata,
        render_as_batch=True,
    )
    with context.begin_transaction():
        context.run_migrations()


run_migrations()
