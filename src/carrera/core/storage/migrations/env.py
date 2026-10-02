"""Alembic environment. Works with an injected connection (application) or a URL (CLI)."""

from __future__ import annotations

from alembic import context
from sqlalchemy import Connection, create_engine

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


def _migrate(connection: Connection) -> None:
    is_sqlite = connection.dialect.name == "sqlite"
    if is_sqlite:
        _set_sqlite_foreign_keys(connection, enabled=False)
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        render_as_batch=True,
    )
    with context.begin_transaction():
        context.run_migrations()
    connection.commit()
    if is_sqlite:
        # Batch migrations recreate tables. With foreign keys enforced, dropping the old table
        # would cascade-delete or reject rows of tables that reference it.
        violations = connection.exec_driver_sql("PRAGMA foreign_key_check").fetchall()
        _set_sqlite_foreign_keys(connection, enabled=True)
        if violations:
            raise RuntimeError(f"migration left foreign key violations: {violations}")


def _set_sqlite_foreign_keys(connection: Connection, *, enabled: bool) -> None:
    connection.exec_driver_sql(f"PRAGMA foreign_keys={'ON' if enabled else 'OFF'}")
    current = connection.exec_driver_sql("PRAGMA foreign_keys").scalar()
    if bool(current) != enabled:
        raise RuntimeError("cannot change SQLite foreign key enforcement inside a transaction")


run_migrations()


run_migrations()
