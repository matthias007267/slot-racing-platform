"""Central handlers for exceptions that nobody else caught."""

from __future__ import annotations

import logging
import sys
import threading
import time
import uuid
from collections.abc import Callable
from types import TracebackType

logger = logging.getLogger(__name__)

Presenter = Callable[[str], None]
_presenter: Presenter | None = None
_handling = threading.local()


class DialogGate:
    """Allows one crash dialog at a time, then a quiet period.

    Further exceptions are still logged. They do not open another dialog.
    """

    def __init__(self, cooldown_s: float = 2.0) -> None:
        self._cooldown_s = cooldown_s
        self._lock = threading.Lock()
        self._open = False
        self._last = 0.0

    def allow(self) -> bool:
        with self._lock:
            if self._open:
                return False
            now = _now()
            if now - self._last < self._cooldown_s:
                return False
            self._open = True
            self._last = now
            return True

    def release(self) -> None:
        with self._lock:
            self._open = False
            self._last = _now()


def set_exception_presenter(presenter: Presenter | None) -> None:
    """Register the UI that may show a crash id. ``None`` only logs."""
    global _presenter
    _presenter = presenter


def exception_presenter() -> Presenter | None:
    return _presenter


def new_crash_id() -> str:
    return uuid.uuid4().hex[:8]


def log_uncaught(
    exc_type: type[BaseException],
    exc: BaseException,
    tb: TracebackType | None,
    *,
    thread_name: str,
    session_id: str,
) -> str:
    """Write one uncaught exception and return its id. Re-entry is ignored."""
    if getattr(_handling, "on", False):
        return ""
    _handling.on = True
    try:
        crash_id = new_crash_id()
        logger.critical(
            "UNCAUGHT crash_id=%s thread=%s session=%s type=%s message=%s",
            crash_id,
            thread_name,
            session_id,
            getattr(exc_type, "__name__", type(exc_type).__name__),
            exc,
            exc_info=(exc_type, exc, tb),
        )
        return crash_id
    finally:
        _handling.on = False


def install_hooks(
    *,
    session_id: str,
    on_crash: Callable[[str, str], None],
    gate: DialogGate,
) -> tuple[Callable[..., object], Callable[..., object]]:
    """Install process and thread hooks. Returns the previous hooks."""
    previous_sys = sys.excepthook
    previous_thread = threading.excepthook

    def sys_hook(
        exc_type: type[BaseException],
        exc: BaseException,
        tb: TracebackType | None,
    ) -> None:
        _deliver(
            exc_type,
            exc,
            tb,
            thread_name=threading.current_thread().name,
            session_id=session_id,
            on_crash=on_crash,
            gate=gate,
        )
        previous_sys(exc_type, exc, tb)

    def thread_hook(args: threading.ExceptHookArgs) -> None:
        name = args.thread.name if args.thread is not None else "unknown"
        _deliver(
            args.exc_type or BaseException,
            args.exc_value,
            args.exc_traceback,
            thread_name=name,
            session_id=session_id,
            on_crash=on_crash,
            gate=gate,
        )
        previous_thread(args)

    sys.excepthook = sys_hook
    threading.excepthook = thread_hook
    return previous_sys, previous_thread


def restore_hooks(
    previous_sys: Callable[..., object],
    previous_thread: Callable[..., object],
) -> None:
    sys.excepthook = previous_sys
    threading.excepthook = previous_thread


def _deliver(
    exc_type: type[BaseException],
    exc: BaseException | None,
    tb: TracebackType | None,
    *,
    thread_name: str,
    session_id: str,
    on_crash: Callable[[str, str], None],
    gate: DialogGate,
) -> None:
    if exc is None:
        exc = exc_type()
    crash_id = log_uncaught(exc_type, exc, tb, thread_name=thread_name, session_id=session_id)
    if not crash_id:
        return
    try:
        on_crash(crash_id, thread_name)
    except Exception:
        logger.error("recording an uncaught exception failed crash_id=%s", crash_id)
    presenter = _presenter
    if presenter is None or threading.current_thread() is not threading.main_thread():
        return
    if not gate.allow():
        return
    try:
        presenter(crash_id)
    except Exception:
        logger.error("crash dialog failed crash_id=%s", crash_id)
    finally:
        gate.release()


def _now() -> float:
    return time.monotonic()
