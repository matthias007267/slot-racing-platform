"""Picture shown for the running program.

``resources/icons`` holds the picture. The window and the taskbar use it while
the program is open. The Windows build converts the same file into the icon of the
program file and of the installer (``packaging/windows``).
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path

from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QApplication, QWidget

logger = logging.getLogger(__name__)

APPLICATION_ICON_NAME = "Rennwagen auf glänzender Slotcar-Strecke.png"
# Windows groups a Python process under python.exe unless the process has its
# own identity. The taskbar then shows the Python icon instead of this picture.
WINDOWS_APP_ID = "slot-racing-platform"


def application_icon_path() -> Path | None:
    """The picture in ``resources/icons``, found from this checkout."""
    for parent in Path(__file__).resolve().parents:
        candidate = parent / "resources" / "icons" / APPLICATION_ICON_NAME
        if candidate.is_file():
            return candidate
    return None


def application_icon() -> QIcon:
    path = application_icon_path()
    if path is None:
        logger.warning("Application icon is missing: resources/icons/%s", APPLICATION_ICON_NAME)
        return QIcon()
    return QIcon(str(path))


def install_application_icon(target: QApplication | QWidget) -> None:
    """Use the picture as the icon of the application or one window."""
    icon = application_icon()
    if icon.isNull():
        return
    target.setWindowIcon(icon)


def use_windows_taskbar_identity() -> None:
    """Give the Windows process its own taskbar identity before any window opens."""
    if sys.platform != "win32":
        return
    import ctypes

    shell32 = ctypes.windll.shell32
    shell32.SetCurrentProcessExplicitAppUserModelID(WINDOWS_APP_ID)
