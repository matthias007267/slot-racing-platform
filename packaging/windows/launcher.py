"""Entry point of the frozen Windows program.

A windowed PyInstaller program has no console, so ``sys.stdout`` and ``sys.stderr`` are ``None``.
Libraries that write to them would fail, so they are replaced by a null device first.
"""

from __future__ import annotations

import multiprocessing
import os
import sys
from pathlib import Path


def _ensure_streams() -> None:
    if sys.stdout is None:
        sys.stdout = open(os.devnull, "w", encoding="utf-8")  # noqa: SIM115
    if sys.stderr is None:
        sys.stderr = open(os.devnull, "w", encoding="utf-8")  # noqa: SIM115


def _remember_build_commit() -> None:
    root = Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent))
    marker = root / "BUILD_COMMIT"
    try:
        commit = marker.read_text(encoding="ascii").strip()
    except OSError:
        return
    if commit:
        os.environ.setdefault("SLOT_RACING_COMMIT", commit)


def run() -> int:
    multiprocessing.freeze_support()
    _ensure_streams()
    _remember_build_commit()
    from slot_racing.app.main import main

    return main()


if __name__ == "__main__":
    sys.exit(run())
