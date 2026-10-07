"""Application entry point."""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence

from PySide6.QtCore import QEventLoop, QTimer
from PySide6.QtWidgets import QApplication

from slot_racing import __version__
from slot_racing.app.diagnostics_ui import install_qt_messages, remember_qt_versions
from slot_racing.app.main_window import MainWindow
from slot_racing.app.runtime import Runtime
from slot_racing.core.config import app_data_dir, default_config_path, load_config
from slot_racing.core.diagnostics import install, mark_clean, note_fact, shutdown
from slot_racing.core.plugin import PluginState
from slot_racing.uikit.theme import apply_theme


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="slot-racing", description="Slot-Racing Platform")
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    parser.add_argument(
        "--smoke-test",
        action="store_true",
        help="start the application, show the window briefly and exit (for CI)",
    )
    parser.add_argument("--debug", action="store_true", help="enable debug logging")
    args, qt_args = parser.parse_known_args(argv if argv is not None else sys.argv[1:])

    install(app_data_dir(), debug=args.debug, smoke_test=args.smoke_test)
    config_path = default_config_path()
    runtime: Runtime | None = None
    completed = False
    try:
        runtime = Runtime.create(load_config(config_path), config_path=config_path)
        _remember_runtime(runtime)
        existing = QApplication.instance()
        app = (
            existing
            if isinstance(existing, QApplication)
            else QApplication([sys.argv[0], *qt_args])
        )
        install_qt_messages()
        remember_qt_versions()
        apply_theme(app)
        window = MainWindow(runtime)
        window.show()
        if args.smoke_test:
            # Quit only this loop. QApplication.quit() sets a thread flag that
            # makes every later QEventLoop return immediately, so a smoke test
            # inside the shared test application would disable timers afterwards.
            loop = QEventLoop()
            QTimer.singleShot(500, loop.quit)
            loop.exec()
            window.close()
            completed = True
            return 0
        code = app.exec()
        completed = True
        return code
    finally:
        try:
            if runtime is not None:
                runtime.shutdown()
        finally:
            if completed:
                mark_clean()
            shutdown()


def _remember_runtime(runtime: Runtime) -> None:
    enabled = [
        status.name for status in runtime.plugins.statuses() if status.state is PluginState.ENABLED
    ]
    note_fact("schema_revision", runtime.database.schema_revision())
    note_fact("plugins", ", ".join(enabled))
    note_fact("timing_source", runtime.config.timing_source or "default")


if __name__ == "__main__":
    sys.exit(main())
