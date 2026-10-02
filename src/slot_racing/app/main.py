"""Application entry point."""

from __future__ import annotations

import argparse
import logging
import sys
from collections.abc import Sequence

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication

from slot_racing import __version__
from slot_racing.app.main_window import MainWindow
from slot_racing.app.runtime import Runtime
from slot_racing.core.config import default_config_path, load_config


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

    logging.basicConfig(
        level=logging.DEBUG if args.debug else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    config_path = default_config_path()
    runtime = Runtime.create(load_config(config_path), config_path=config_path)
    try:
        existing = QApplication.instance()
        app = (
            existing
            if isinstance(existing, QApplication)
            else QApplication([sys.argv[0], *qt_args])
        )
        window = MainWindow(runtime)
        window.show()
        if args.smoke_test:
            QTimer.singleShot(500, app.quit)
        return app.exec()
    finally:
        runtime.shutdown()


if __name__ == "__main__":
    sys.exit(main())
