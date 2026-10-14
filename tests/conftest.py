"""Session hooks shared by every test.

Offscreen Qt is required for headless runs. A per-test limit stops one blocked
dialog or worker from holding the Windows job for an hour: the limit names the
test, dumps every thread, and exits that worker.
"""

from __future__ import annotations

import faulthandler
import os
import threading
from collections.abc import Iterator

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

_DEFAULT_TIMEOUT_S = 120.0


def _timeout_seconds() -> float | None:
    raw = os.environ.get("SLOT_RACING_PYTEST_TIMEOUT", str(int(_DEFAULT_TIMEOUT_S)))
    if raw.strip().lower() in {"", "0", "off"}:
        return None
    return float(raw)


def _running_under_xdist_controller(item: pytest.Item) -> bool:
    config = item.config
    if not config.pluginmanager.hasplugin("xdist"):
        return False
    return not hasattr(config, "workerinput") and bool(getattr(config.option, "numprocesses", None))


def _original_stderr_fd(item: pytest.Item) -> int:
    """Fd of the real stderr, outside pytest's capture buffer."""
    manager = item.config.pluginmanager.getplugin("capturemanager")
    capturing = getattr(manager, "_global_capturing", None)
    saved = getattr(getattr(capturing, "err", None), "targetfd_save", None)
    if isinstance(saved, int) and saved >= 0:
        return saved
    return 2


def _expire(
    nodeid: str, generation: int, seconds: float, state: _TimeoutGate, stderr_fd: int
) -> None:
    with state.lock:
        if generation != state.generation:
            return
    message = (
        f"\nPYTEST TIMEOUT after {seconds:.0f}s in {nodeid}\n"
        "This worker exits so one blocked test cannot hold the rest of the run.\n"
    )
    stream = os.fdopen(os.dup(stderr_fd), "w", buffering=1, closefd=True)
    stream.write(message)
    faulthandler.dump_traceback(file=stream, all_threads=True)
    stream.flush()
    os._exit(3)


class _TimeoutGate:
    def __init__(self) -> None:
        self.generation = 0
        self.lock = threading.Lock()


_gate = _TimeoutGate()


@pytest.hookimpl(wrapper=True)
def pytest_runtest_protocol(item: pytest.Item) -> Iterator[None]:
    seconds = None if _running_under_xdist_controller(item) else _timeout_seconds()
    timer: threading.Timer | None = None
    generation = 0
    if seconds is not None:
        with _gate.lock:
            _gate.generation += 1
            generation = _gate.generation
        timer = threading.Timer(
            seconds,
            _expire,
            args=(item.nodeid, generation, seconds, _gate, _original_stderr_fd(item)),
        )
        timer.daemon = True
        timer.start()
    try:
        yield
    finally:
        if timer is not None:
            with _gate.lock:
                _gate.generation += 1
            timer.cancel()


@pytest.hookimpl(wrapper=True, tryfirst=True)
def pytest_runtest_teardown(item: pytest.Item) -> Iterator[None]:
    """Close dirty part editors before pytest-qt. A modal question would block the worker."""
    if "qtbot" in getattr(item, "fixturenames", ()):
        _dismiss_part_editors()
    yield


def _dismiss_part_editors() -> None:
    from PySide6.QtWidgets import QApplication
    from shiboken6 import Shiboken

    from slot_racing.modules.track_planner.ui.part_editor import PartDialog

    app = QApplication.instance()
    if not isinstance(app, QApplication):
        return
    for widget in app.topLevelWidgets():
        if isinstance(widget, PartDialog) and Shiboken.isValid(widget):
            widget._dismissed = True
