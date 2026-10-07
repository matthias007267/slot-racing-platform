"""Local diagnostics for the desktop application.

Import :func:`record` from anywhere in the core or a module. The application
calls :func:`install` once at startup.
"""

from slot_racing.core.diagnostics.bundle import suggested_bundle_name
from slot_racing.core.diagnostics.hooks import DialogGate, set_exception_presenter
from slot_racing.core.diagnostics.service import (
    LOG_BACKUPS,
    MAX_LOG_BYTES,
    Diagnostics,
    commit_id,
    current,
    install,
    mark_clean,
    note_fact,
    record,
    shutdown,
)

__all__ = [
    "LOG_BACKUPS",
    "MAX_LOG_BYTES",
    "Diagnostics",
    "DialogGate",
    "commit_id",
    "current",
    "install",
    "mark_clean",
    "note_fact",
    "record",
    "set_exception_presenter",
    "shutdown",
    "suggested_bundle_name",
]
