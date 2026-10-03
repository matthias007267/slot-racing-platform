"""Central dark racing theme.

Widgets take their colors from these tokens through the application palette and stylesheet.
Dynamic properties ``role`` and ``tone`` select a variant. Pages do not invent their own colors.
"""

from __future__ import annotations

import tempfile
from dataclasses import dataclass
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QPainter, QPalette, QPen, QPixmap
from PySide6.QtWidgets import QApplication, QLayout, QWidget


@dataclass(frozen=True, slots=True)
class Color:
    background: str = "#121417"
    surface: str = "#1A1E24"
    elevated: str = "#222831"
    border: str = "#2E3540"
    text: str = "#E8EDF2"
    text_secondary: str = "#A8B0BA"
    text_muted: str = "#8B949E"
    accent: str = "#2FBF71"
    accent_hover: str = "#3DDC97"
    accent_pressed: str = "#24945A"
    accent_text: str = "#0E1412"
    warning: str = "#E2B340"
    error: str = "#E15B5B"
    info: str = "#7EB6D9"
    selection: str = "#1E3A2C"
    row_hover: str = "#242A33"
    danger_hover: str = "#3A2224"


@dataclass(frozen=True, slots=True)
class Space:
    xs: int = 4
    sm: int = 8
    md: int = 16
    lg: int = 24
    xl: int = 32


COLORS = Color()
SPACE = Space()

_RADIUS = 6
_BUTTON_HEIGHT = 32


def configure_page(layout: QLayout) -> None:
    """Outer page padding lives on the shell. Nested pages only keep a consistent gap."""
    layout.setContentsMargins(0, 0, 0, 0)
    layout.setSpacing(SPACE.md)


def polish(widget: QWidget) -> None:
    style = widget.style()
    style.unpolish(widget)
    style.polish(widget)
    widget.update()


def set_role(widget: QWidget, role: str) -> None:
    widget.setProperty("role", role)
    polish(widget)


def set_tone(widget: QWidget, tone: str) -> None:
    widget.setProperty("tone", tone)
    polish(widget)


def apply_theme(app: QApplication) -> None:
    """Fusion plus palette plus stylesheet. Safe to call more than once."""
    app.setStyle("Fusion")
    app.setPalette(_palette())
    app.setStyleSheet(stylesheet(_checkmark()))


def _palette() -> QPalette:
    color = QColor
    palette = QPalette()
    palette.setColor(QPalette.ColorRole.Window, color(COLORS.background))
    palette.setColor(QPalette.ColorRole.WindowText, color(COLORS.text))
    palette.setColor(QPalette.ColorRole.Base, color(COLORS.surface))
    palette.setColor(QPalette.ColorRole.AlternateBase, color(COLORS.elevated))
    palette.setColor(QPalette.ColorRole.Text, color(COLORS.text))
    palette.setColor(QPalette.ColorRole.Button, color(COLORS.elevated))
    palette.setColor(QPalette.ColorRole.ButtonText, color(COLORS.text))
    palette.setColor(QPalette.ColorRole.Highlight, color(COLORS.selection))
    palette.setColor(QPalette.ColorRole.HighlightedText, color(COLORS.text))
    palette.setColor(QPalette.ColorRole.ToolTipBase, color(COLORS.elevated))
    palette.setColor(QPalette.ColorRole.ToolTipText, color(COLORS.text))
    palette.setColor(QPalette.ColorRole.PlaceholderText, color(COLORS.text_muted))
    palette.setColor(QPalette.ColorRole.Mid, color(COLORS.border))
    palette.setColor(QPalette.ColorRole.Light, color(COLORS.elevated))
    palette.setColor(QPalette.ColorRole.Dark, color(COLORS.background))
    muted = color(COLORS.text_muted)
    for role in (
        QPalette.ColorRole.WindowText,
        QPalette.ColorRole.Text,
        QPalette.ColorRole.ButtonText,
    ):
        palette.setColor(QPalette.ColorGroup.Disabled, role, muted)
    return palette


def _checkmark() -> str:
    """A small check icon so a checked box is not only a green square."""
    path = Path(tempfile.gettempdir()) / "slot-racing-checkbox.png"
    pixmap = QPixmap(16, 16)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    pen = QPen(QColor(COLORS.accent_text))
    pen.setWidthF(1.8)
    pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
    painter.setPen(pen)
    painter.drawLine(3, 8, 6, 11)
    painter.drawLine(6, 11, 12, 4)
    painter.end()
    pixmap.save(str(path))
    return path.as_posix()


def stylesheet(checkmark: str = "") -> str:
    c = COLORS
    radius = _RADIUS
    height = _BUTTON_HEIGHT
    return f"""
    QMainWindow, QDialog, QMessageBox {{
        background: {c.background};
        color: {c.text};
    }}
    QWidget#shell-content, QWidget#dashboard-body, QStackedWidget#page-host {{
        background: {c.background};
    }}
    QWidget#sidebar {{
        background: {c.surface};
        border-right: 1px solid {c.border};
    }}
    QWidget#nav-list, QScrollArea#nav-scroll {{
        background: {c.surface};
        border: none;
    }}
    QLabel {{
        background: transparent;
        color: {c.text};
    }}
    QLabel#brand {{
        color: {c.text};
        font-size: 13px;
        font-weight: 700;
        padding: 4px 8px 12px 8px;
    }}
    QFrame#sidebar-divider {{
        color: {c.border};
        background: {c.border};
        max-height: 1px;
    }}
    QLabel[role="page-title"] {{
        font-size: 20px;
        font-weight: 600;
    }}
    QLabel[role="section"] {{
        color: {c.text_secondary};
        font-size: 13px;
        font-weight: 600;
    }}
    QLabel[role="card-title"] {{
        color: {c.text_muted};
        font-size: 11px;
        font-weight: 600;
    }}
    QLabel[role="metric"], QLabel[role="telemetry"] {{
        color: {c.text};
        font-weight: 600;
        font-family: "DejaVu Sans Mono", "Liberation Mono", monospace;
    }}
    QLabel[role="metric"] {{
        font-size: 28px;
    }}
    QLabel[role="telemetry"] {{
        font-size: 18px;
    }}
    QLabel[role="telemetry-fit"] {{
        font-weight: 600;
        font-family: "DejaVu Sans Mono", "Liberation Mono", monospace;
    }}
    QLabel[role="caption"] {{
        color: {c.text_secondary};
        font-size: 12px;
    }}
    QLabel[tone="ok"] {{ color: {c.accent}; }}
    QLabel[tone="warn"] {{ color: {c.warning}; }}
    QLabel[tone="error"] {{ color: {c.error}; }}
    QLabel[tone="info"] {{ color: {c.info}; }}
    QLabel[tone="muted"] {{ color: {c.text_muted}; }}
    QLabel[role="status"] {{
        font-size: 12px;
        font-weight: 600;
    }}
    QFrame[role="card"] {{
        background: {c.surface};
        border: 1px solid {c.border};
        border-radius: 8px;
    }}
    QFrame[role="hud-panel"] {{
        background: {c.surface};
        border: 1px solid {c.border};
        border-radius: 8px;
    }}
    QFrame[role="hud-panel"][tone="ok"] {{
        border: 1px solid {c.accent};
    }}
    QFrame[role="hud-panel"][tone="warn"] {{
        border: 1px solid {c.warning};
    }}
    QWidget#hud-preview, QWidget#hud-stage {{
        background: {c.background};
        border: 1px solid {c.border};
        border-radius: 8px;
    }}
    QFrame[role="hud-box"] {{
        background: {c.surface};
        border: 1px dashed {c.border};
        border-radius: 6px;
    }}
    QFrame[role="hud-box"][active="true"] {{
        background: {c.selection};
        border: 1px solid {c.accent};
    }}
    QFrame[role="hud-handle"] {{
        background: {c.accent};
        border: none;
        border-radius: 2px;
    }}
    QScrollArea#settings-scroll, QWidget#settings-body {{
        background: transparent;
        border: none;
    }}
    QProgressBar {{
        background: {c.elevated};
        border: 1px solid {c.border};
        border-radius: 4px;
        color: {c.text};
        text-align: center;
        min-height: 14px;
    }}
    QProgressBar::chunk {{
        background: {c.accent};
        border-radius: 3px;
    }}
    QPushButton {{
        background: {c.elevated};
        color: {c.text};
        border: 1px solid {c.border};
        border-radius: {radius}px;
        min-height: {height}px;
        padding: 4px 14px;
        font-size: 13px;
    }}
    QPushButton:hover {{
        background: {c.row_hover};
    }}
    QPushButton:disabled {{
        color: {c.text_muted};
        background: {c.surface};
        border-color: {c.border};
    }}
    QPushButton[compact="true"] {{
        min-height: 0px;
        padding: 2px 6px;
        font-size: 12px;
    }}
    QPushButton[compact="true"][fit="small"] {{
        padding: 0px 2px;
        font-size: 10px;
    }}
    QPushButton:default, QPushButton[role="primary"] {{
        background: {c.accent};
        color: {c.accent_text};
        border: 1px solid {c.accent};
        font-weight: 600;
    }}
    QPushButton:default:hover, QPushButton[role="primary"]:hover {{
        background: {c.accent_hover};
        border-color: {c.accent_hover};
    }}
    QPushButton:default:pressed, QPushButton[role="primary"]:pressed {{
        background: {c.accent_pressed};
        border-color: {c.accent_pressed};
    }}
    QPushButton[role="primary"]:disabled {{
        background: {c.elevated};
        color: {c.text_muted};
        border: 1px solid {c.border};
    }}
    QPushButton[role="danger"] {{
        background: transparent;
        color: {c.error};
        border: 1px solid {c.error};
    }}
    QPushButton[role="danger"]:hover {{
        background: {c.danger_hover};
    }}
    QPushButton[role="ghost"] {{
        background: transparent;
        color: {c.text_secondary};
        border: 1px solid transparent;
    }}
    QPushButton[role="ghost"]:hover {{
        background: {c.elevated};
        color: {c.text};
    }}
    QPushButton[role="nav"] {{
        background: transparent;
        color: {c.text_secondary};
        border: none;
        border-radius: {radius}px;
        text-align: left;
        padding: 6px 10px;
        min-height: 36px;
        font-weight: 500;
    }}
    QPushButton[role="nav"]:hover {{
        background: {c.elevated};
        color: {c.text};
    }}
    QPushButton[role="nav"][active="true"] {{
        background: {c.selection};
        color: {c.accent_hover};
        font-weight: 600;
    }}
    QLineEdit, QComboBox, QSpinBox, QDoubleSpinBox, QPlainTextEdit, QTextEdit {{
        background: {c.elevated};
        color: {c.text};
        border: 1px solid {c.border};
        border-radius: {radius}px;
        padding: 4px 8px;
        min-height: 28px;
        selection-background-color: {c.selection};
        selection-color: {c.text};
    }}
    QLineEdit:focus, QComboBox:focus, QSpinBox:focus, QDoubleSpinBox:focus,
    QPlainTextEdit:focus, QTextEdit:focus, QListWidget:focus {{
        border: 1px solid {c.accent};
    }}
    QComboBox::drop-down {{
        border: none;
        width: 22px;
    }}
    QComboBox::down-arrow {{
        image: none;
        width: 0px;
        height: 0px;
        border-left: 4px solid transparent;
        border-right: 4px solid transparent;
        border-top: 5px solid {c.text_secondary};
    }}
    QComboBox QAbstractItemView {{
        background: {c.elevated};
        color: {c.text};
        border: 1px solid {c.border};
        selection-background-color: {c.selection};
        selection-color: {c.text};
        outline: none;
    }}
    QCheckBox {{
        spacing: 8px;
        color: {c.text};
    }}
    QCheckBox::indicator {{
        width: 16px;
        height: 16px;
        border: 1px solid {c.border};
        border-radius: 3px;
        background: {c.elevated};
    }}
    QCheckBox::indicator:checked {{
        background: {c.accent};
        border-color: {c.accent};
        image: url({checkmark});
    }}
    QCheckBox::indicator:disabled {{
        background: {c.surface};
        border-color: {c.border};
    }}
    QTableWidget {{
        background: {c.surface};
        alternate-background-color: {c.elevated};
        color: {c.text};
        gridline-color: transparent;
        border: 1px solid {c.border};
        border-radius: {radius}px;
        selection-background-color: {c.selection};
        selection-color: {c.text};
        outline: none;
    }}
    QTableWidget::item {{
        padding: 4px 8px;
        border-bottom: 1px solid {c.border};
    }}
    QTableWidget::item:selected {{
        background: {c.selection};
        color: {c.text};
    }}
    QHeaderView::section {{
        background: {c.surface};
        color: {c.text_muted};
        border: none;
        border-bottom: 1px solid {c.border};
        padding: 8px;
        font-size: 11px;
        font-weight: 600;
    }}
    QTableCornerButton::section {{
        background: {c.surface};
        border: none;
    }}
    QListWidget {{
        background: {c.surface};
        color: {c.text};
        border: 1px solid {c.border};
        border-radius: {radius}px;
        outline: none;
    }}
    QListWidget::item {{
        padding: 6px 8px;
        border-bottom: 1px solid {c.border};
    }}
    QListWidget::item:selected {{
        background: {c.selection};
        color: {c.text};
    }}
    QListWidget::item:hover {{
        background: {c.row_hover};
    }}
    QScrollArea {{
        background: transparent;
        border: none;
    }}
    QScrollBar:vertical {{
        background: transparent;
        width: 10px;
        margin: 0;
    }}
    QScrollBar::handle:vertical {{
        background: {c.border};
        border-radius: 4px;
        min-height: 24px;
    }}
    QScrollBar:horizontal {{
        background: transparent;
        height: 10px;
        margin: 0;
    }}
    QScrollBar::handle:horizontal {{
        background: {c.border};
        border-radius: 4px;
        min-width: 24px;
    }}
    QScrollBar::add-line, QScrollBar::sub-line {{
        height: 0;
        width: 0;
    }}
    QToolTip, QMenu {{
        background: {c.elevated};
        color: {c.text};
        border: 1px solid {c.border};
    }}
    QMenu::item:selected {{
        background: {c.selection};
    }}
    """
