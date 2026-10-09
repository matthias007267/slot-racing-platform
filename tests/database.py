"""A migrated in-memory database for tests.

Each call returns a private copy. Tests do not share rows, and the copy is not
built by running every Alembic revision again.
"""

from __future__ import annotations

import threading

from slot_racing.core.storage import Database
from slot_racing.core.storage.database import head_revision

_template: Database | None = None
_lock = threading.Lock()


def migrated_database() -> Database:
    """A new in-memory database already at the current schema head."""
    template = _migrated_template()
    fresh = Database.in_memory()
    _copy_sqlite(template, fresh)
    return fresh


def _migrated_template() -> Database:
    global _template
    with _lock:
        if _template is None:
            database = Database.in_memory()
            database.migrate()
            if database.schema_revision() != head_revision():
                raise RuntimeError("test database template did not reach head")
            _template = database
        return _template


def _copy_sqlite(source: Database, destination: Database) -> None:
    src = source.engine.raw_connection()
    dst = destination.engine.raw_connection()
    try:
        source_conn = src.driver_connection
        dest_conn = dst.driver_connection
        if source_conn is None or dest_conn is None:
            raise RuntimeError("SQLite connection is closed")
        source_conn.backup(dest_conn)
    finally:
        src.close()
        dst.close()
