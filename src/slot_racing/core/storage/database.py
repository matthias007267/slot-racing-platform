"""Engine and session handling for the application database."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from alembic import command
from alembic.config import Config
from sqlalchemy import Engine, create_engine, event
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

MIGRATIONS_DIR = Path(__file__).parent / "migrations"


class Database:
    """Owns the SQLAlchemy engine. Foreign keys are enforced for every SQLite connection."""

    def __init__(self, url: str) -> None:
        self.url = url
        self.engine: Engine
        self._session_factory: sessionmaker[Session]
        self._open(url)

    def _open(self, url: str) -> None:
        self.url = url
        parsed = make_url(url)
        kwargs: dict[str, Any] = {}
        if parsed.get_backend_name() == "sqlite" and parsed.database in (None, "", ":memory:"):
            kwargs = {"poolclass": StaticPool, "connect_args": {"check_same_thread": False}}
        self.engine = create_engine(url, **kwargs)
        if parsed.get_backend_name() == "sqlite":
            event.listen(self.engine, "connect", _enable_sqlite_foreign_keys)
        self._session_factory = sessionmaker(self.engine, expire_on_commit=False)

    def file_path(self) -> Path | None:
        """The SQLite file, when this database is stored in one."""
        parsed = make_url(self.url)
        if parsed.get_backend_name() != "sqlite":
            return None
        name = parsed.database
        if not name or name == ":memory:":
            return None
        return Path(name)

    def reopen(self) -> None:
        """Close every connection and open the same file again."""
        url = self.url
        self.dispose()
        self._open(url)

    @classmethod
    def from_path(cls, path: Path) -> Database:
        path.parent.mkdir(parents=True, exist_ok=True)
        return cls(f"sqlite:///{path.as_posix()}")

    @classmethod
    def in_memory(cls) -> Database:
        return cls("sqlite://")

    @contextmanager
    def session(self) -> Iterator[Session]:
        """Session that commits on success and rolls back on error."""
        session = self._session_factory()
        try:
            yield session
            session.commit()
        except BaseException:
            session.rollback()
            raise
        finally:
            session.close()

    def migrate(self, revision: str = "head") -> None:
        """Bring the schema to ``revision`` using the bundled Alembic migrations."""
        config = alembic_config()
        with self.engine.connect() as connection:
            config.attributes["connection"] = connection
            command.upgrade(config, revision)

    def dispose(self) -> None:
        self.engine.dispose()


def alembic_config() -> Config:
    config = Config()
    config.set_main_option("script_location", str(MIGRATIONS_DIR))
    return config


def _enable_sqlite_foreign_keys(dbapi_connection: Any, _record: Any) -> None:
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.close()
