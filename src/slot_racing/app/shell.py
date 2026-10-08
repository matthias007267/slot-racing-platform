"""Sidebar and header. Pages do not build their own navigation."""

from __future__ import annotations

from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from slot_racing.app.runtime import Runtime
from slot_racing.core.timing_registry import TimingProviderRegistry
from slot_racing.uikit.icons import nav_icon
from slot_racing.uikit.providers import provider_label
from slot_racing.uikit.theme import NAVIGATION_WIDTH, SPACE, set_role
from slot_racing.uikit.widgets import StatusPill


class Sidebar(QWidget):
    """Vertical navigation. ``footer_id`` stays pinned; the id order itself does not change."""

    selected = Signal(str)

    def __init__(self, brand: str) -> None:
        super().__init__()
        self.setObjectName("sidebar")
        self.setFixedWidth(NAVIGATION_WIDTH)
        self._buttons: dict[str, QPushButton] = {}
        self._order: list[str] = []
        self._current: str | None = None
        self._quit_button: QPushButton | None = None

        self._nav_layout = QVBoxLayout()
        self._nav_layout.setContentsMargins(0, 0, 0, 0)
        self._nav_layout.setSpacing(SPACE.xs)
        nav_list = QWidget()
        nav_list.setObjectName("nav-list")
        nav_list.setLayout(self._nav_layout)
        scroll = QScrollArea()
        scroll.setObjectName("nav-scroll")
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setWidget(nav_list)

        self._divider = QFrame()
        self._divider.setObjectName("sidebar-divider")
        self._divider.setFrameShape(QFrame.Shape.HLine)
        self._footer_layout = QVBoxLayout()
        self._footer_layout.setContentsMargins(0, 0, 0, 0)
        self._footer_layout.setSpacing(SPACE.xs)

        brand_label = QLabel(brand)
        brand_label.setObjectName("brand")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(SPACE.sm, SPACE.md, SPACE.sm, SPACE.md)
        layout.setSpacing(SPACE.sm)
        layout.addWidget(brand_label)
        layout.addWidget(scroll, 1)
        layout.addWidget(self._divider)
        layout.addLayout(self._footer_layout)

    def set_quit_button(self, button: QPushButton) -> None:
        """Pin a quit button above the footer entry. Navigation rebuilds keep it."""
        self._quit_button = button
        button.setParent(self)

    def set_entries(self, entries: list[tuple[str, str]], *, footer_id: str) -> None:
        self._clear(self._nav_layout)
        self._clear(self._footer_layout)
        self._buttons.clear()
        self._order = []
        self._current = None
        if self._quit_button is not None:
            self._footer_layout.addWidget(self._quit_button)
        for entry_id, title in entries:
            button = self._make_button(entry_id, title)
            self._buttons[entry_id] = button
            self._order.append(entry_id)
            target = self._footer_layout if entry_id == footer_id else self._nav_layout
            target.addWidget(button)
        self._nav_layout.addStretch(1)
        self._divider.setVisible(footer_id in self._buttons)

    def ids(self) -> list[str]:
        return list(self._order)

    def titles(self) -> list[str]:
        return [self._buttons[entry_id].text() for entry_id in self._order]

    def current_id(self) -> str | None:
        return self._current

    def select(self, entry_id: str) -> None:
        if entry_id not in self._buttons:
            return
        self._current = entry_id
        for key, button in self._buttons.items():
            active = key == entry_id
            button.setProperty("active", "true" if active else "false")
            button.setIcon(nav_icon(key, active=active))
            set_role(button, "nav")
        self.selected.emit(entry_id)

    def _make_button(self, entry_id: str, title: str) -> QPushButton:
        button = QPushButton(title)
        button.setObjectName(f"nav-{entry_id}")
        button.setIcon(nav_icon(entry_id, active=False))
        button.setIconSize(QSize(16, 16))
        button.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        button.setProperty("active", "false")
        set_role(button, "nav")
        button.clicked.connect(lambda _checked=False, chosen=entry_id: self.select(chosen))
        return button

    def _clear(self, layout: QVBoxLayout) -> None:
        while (item := layout.takeAt(0)) is not None:
            widget = item.widget()
            if widget is None or widget is self._quit_button:
                continue
            widget.setParent(None)
            widget.deleteLater()


class ShellHeader(QWidget):
    def __init__(self) -> None:
        super().__init__()
        self.setObjectName("shell-header")
        self._title = QLabel()
        self._title.setObjectName("shell-title")
        set_role(self._title, "page-title")
        self._status = StatusPill("shell-status")
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(SPACE.md)
        layout.addWidget(self._title, 1)
        layout.addWidget(
            self._status,
            0,
            Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
        )

    def set_title(self, title: str) -> None:
        self._title.setText(title)

    def set_timing(self, text: str, tone: str) -> None:
        self._status.set_status(text, tone)


def timing_state(runtime: Runtime) -> tuple[str, str]:
    """Label and tone for the timing providers that are actually registered."""
    translate = runtime.translator.translate
    registry = runtime.services.find(TimingProviderRegistry)
    if registry is None:
        return translate("shell.timing.none"), "muted"
    providers = registry.providers()
    if not providers:
        return translate("shell.timing.none"), "muted"
    available = [info for info in providers if info.available]
    if not available:
        return translate("shell.timing.offline"), "error"
    preferred = registry.default_provider_id(runtime.config.timing_source)
    provider_id = preferred if preferred is not None else available[0].provider_id
    return provider_label(runtime.translator, provider_id), "ok"
