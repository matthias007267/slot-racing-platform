"""Independent HUD panels. They display values the live view already has; they compute no race."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QResizeEvent
from PySide6.QtWidgets import (
    QFrame,
    QGridLayout,
    QHeaderView,
    QLabel,
    QProgressBar,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
)

from slot_racing.core.i18n import Translator
from slot_racing.uikit.theme import SPACE, set_role
from slot_racing.uikit.widgets import StatusLabel, make_table

_MIN_FONT = 12
_MAX_FONT = 72


class HudWidget(QFrame):
    """One movable panel. The stage positions it; the panel only lays out its own content."""

    def __init__(self, widget_id: str) -> None:
        super().__init__()
        self.widget_id = widget_id
        self.setObjectName(f"hud-panel-{widget_id}")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setMinimumSize(0, 0)
        set_role(self, "hud-panel")
        self.body = QVBoxLayout(self)
        self.body.setContentsMargins(SPACE.sm, SPACE.sm, SPACE.sm, SPACE.sm)
        self.body.setSpacing(SPACE.xs)

    def add_caption(self, text: str) -> QLabel:
        label = QLabel(text)
        set_role(label, "card-title")
        label.setWordWrap(True)
        label.setMinimumSize(0, 0)
        self.body.addWidget(label)
        return label


def fit_metric(label: QLabel, height: int) -> None:
    """Grow or shrink a number with the panel, and keep a readable minimum."""
    size = max(_MIN_FONT, min(_MAX_FONT, int(max(height, 0) * 0.42)))
    font = label.font()
    if font.pixelSize() == size:
        return
    font.setPixelSize(size)
    label.setFont(font)


class RaceHeaderWidget(HudWidget):
    def __init__(self, translator: Translator) -> None:
        super().__init__("race_header")
        tr = translator.translate
        self.name_label = QLabel(tr("race.live.title"))
        self.name_label.setObjectName("live-name")
        set_role(self.name_label, "section")
        self.name_label.setWordWrap(True)
        self.name_label.setMinimumSize(0, 0)
        self.track_label = _meta("live-track")
        self.provider_label = _meta("live-provider")
        self.header_status = _meta("hud-header-status")
        for label in (self.name_label, self.track_label, self.provider_label, self.header_status):
            self.body.addWidget(label)


class RaceClockWidget(HudWidget):
    def __init__(self, translator: Translator) -> None:
        super().__init__("race_clock")
        self.add_caption(translator.translate("race.live.time"))
        self.time_label = _metric("live-time")
        self.body.addWidget(self.time_label, 1)

    def resizeEvent(self, event: QResizeEvent) -> None:  # noqa: N802
        super().resizeEvent(event)
        fit_metric(self.time_label, self.height())


class LapProgressWidget(HudWidget):
    def __init__(self, translator: Translator) -> None:
        super().__init__("lap_progress")
        self.add_caption(translator.translate("hud.widget.lap_progress"))
        self.laps_label = _metric("live-laps")
        self.progress = QProgressBar()
        self.progress.setObjectName("live-progress")
        self.progress.setTextVisible(True)
        self.progress.setMinimumHeight(0)
        self.body.addWidget(self.laps_label, 1)
        self.body.addWidget(self.progress)

    def resizeEvent(self, event: QResizeEvent) -> None:  # noqa: N802
        super().resizeEvent(event)
        fit_metric(self.laps_label, self.height())


class LiveRankingWidget(HudWidget):
    def __init__(self, translator: Translator) -> None:
        super().__init__("live_ranking")
        tr = translator.translate
        self.add_caption(tr("hud.widget.live_ranking"))
        self.table = make_table(
            [
                tr("race.column.position"),
                tr("race.column.driver"),
                tr("race.column.vehicle"),
                tr("race.column.start_number"),
                tr("race.column.lane"),
                tr("race.column.laps_done"),
                tr("race.column.current_lap"),
                tr("race.column.last_lap"),
                tr("race.column.best_lap"),
                tr("race.column.total_time"),
                tr("race.column.progress"),
                tr("race.column.status"),
            ],
            "live-table",
        )
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        header.setStretchLastSection(True)
        self.table.setMinimumSize(0, 0)
        self.table.setHorizontalScrollMode(self.table.ScrollMode.ScrollPerPixel)
        self.body.addWidget(self.table, 1)


class DriverHighlightWidget(HudWidget):
    def __init__(self, translator: Translator) -> None:
        super().__init__("driver_highlight")
        tr = translator.translate
        self.detail_title = QLabel(tr("race.live.detail"))
        self.detail_title.setObjectName("live-detail-title")
        set_role(self.detail_title, "card-title")
        self.detail = QLabel(tr("race.live.detail_empty"))
        self.detail.setObjectName("live-detail")
        self.detail.setWordWrap(True)
        self.detail.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft)
        self.detail.setMinimumSize(0, 0)
        self.body.addWidget(self.detail_title)
        self.body.addWidget(self.detail, 1)


class LastLapWidget(HudWidget):
    def __init__(self, translator: Translator) -> None:
        super().__init__("last_lap")
        self.add_caption(translator.translate("race.column.last_lap"))
        self.value_label = _metric("hud-last-lap")
        self.value_label.setText("-")
        self.body.addWidget(self.value_label, 1)

    def resizeEvent(self, event: QResizeEvent) -> None:  # noqa: N802
        super().resizeEvent(event)
        fit_metric(self.value_label, self.height())


class BestLapWidget(HudWidget):
    def __init__(self, translator: Translator) -> None:
        super().__init__("best_lap")
        self.add_caption(translator.translate("race.column.best_lap"))
        self.value_label = _metric("hud-best-lap")
        self.value_label.setText("-")
        self.body.addWidget(self.value_label, 1)

    def resizeEvent(self, event: QResizeEvent) -> None:  # noqa: N802
        super().resizeEvent(event)
        fit_metric(self.value_label, self.height())


class RaceStatusWidget(HudWidget):
    def __init__(self, translator: Translator) -> None:
        super().__init__("race_status")
        self.add_caption(translator.translate("hud.widget.race_status"))
        self.status_label = _metric("live-status")
        self.body.addWidget(self.status_label, 1)

    def resizeEvent(self, event: QResizeEvent) -> None:  # noqa: N802
        super().resizeEvent(event)
        fit_metric(self.status_label, self.height())


class RaceMessageWidget(HudWidget):
    def __init__(self, translator: Translator) -> None:
        super().__init__("race_message")
        self.add_caption(translator.translate("hud.widget.race_message"))
        self.message_label = QLabel("-")
        self.message_label.setObjectName("hud-message")
        self.message_label.setWordWrap(True)
        self.message_label.setMinimumSize(0, 0)
        self.warning = StatusLabel("live-warning")
        self.body.addWidget(self.message_label)
        self.body.addWidget(self.warning)


class RaceControlsWidget(HudWidget):
    def __init__(self, translator: Translator) -> None:
        super().__init__("race_controls")
        tr = translator.translate
        self.pause_button = _button(tr("race.live.pause"), "live-pause", "secondary")
        self.stop_button = _button(tr("race.live.stop"), "live-stop", "danger")
        self.results_button = _button(tr("race.live.results"), "live-results", "primary")
        self.back_button = _button(tr("race.live.back"), "live-back", "ghost")
        grid = QGridLayout()
        grid.setSpacing(SPACE.sm)
        buttons = (
            self.pause_button,
            self.stop_button,
            self.results_button,
            self.back_button,
        )
        for index, button in enumerate(buttons):
            button.setMinimumWidth(0)
            button.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
            grid.addWidget(button, index // 2, index % 2)
        self.body.addLayout(grid)


def _meta(object_name: str) -> QLabel:
    label = QLabel()
    label.setObjectName(object_name)
    label.setWordWrap(True)
    label.setMinimumSize(0, 0)
    set_role(label, "caption")
    return label


def _metric(object_name: str) -> QLabel:
    label = QLabel("-")
    label.setObjectName(object_name)
    label.setAlignment(Qt.AlignmentFlag.AlignCenter)
    label.setMinimumSize(0, 0)
    label.setWordWrap(True)
    set_role(label, "telemetry-fit")
    return label


def _button(text: str, object_name: str, role: str) -> QPushButton:
    button = QPushButton(text)
    button.setObjectName(object_name)
    set_role(button, role)
    return button
