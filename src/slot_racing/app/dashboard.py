"""Dashboard built only from catalogs and the timing registry. Missing sources are omitted."""

from __future__ import annotations

from collections.abc import Callable

from PySide6.QtCore import QEventLoop, Qt, QTimer, QUrl
from PySide6.QtGui import QDesktopServices, QPixmap
from PySide6.QtWidgets import (
    QApplication,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLayout,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from slot_racing.app.qr_code import qr_image
from slot_racing.app.runtime import Runtime
from slot_racing.core.catalog import (
    DriverCatalog,
    RaceCatalog,
    RaceSummary,
    TrackCatalog,
    VehicleCatalog,
)
from slot_racing.core.clock import format_duration
from slot_racing.core.domain import RaceMode, RaceStatus
from slot_racing.core.live_display import LiveDisplay, LiveDisplayState, LiveDisplayStatus
from slot_racing.core.plugin import PluginState
from slot_racing.core.timing_registry import TimingProviderRegistry
from slot_racing.uikit.cards import Card, MetricCard
from slot_racing.uikit.providers import availability_text, provider_label
from slot_racing.uikit.theme import SPACE, configure_page, set_role, set_tone
from slot_racing.uikit.widgets import StatusPill, fill_table, make_table

OpenPage = Callable[[str, str | None], None]

_RACE_TONE = {
    RaceStatus.RUNNING: "ok",
    RaceStatus.FINISHED: "ok",
    RaceStatus.PAUSED: "warn",
    RaceStatus.ABORTED: "error",
    RaceStatus.READY: "info",
    RaceStatus.CREATED: "info",
}

_LIVE_TONE = {
    LiveDisplayState.READY: "ok",
    LiveDisplayState.STARTING: "warn",
    LiveDisplayState.STOPPING: "warn",
    LiveDisplayState.ERROR: "error",
    LiveDisplayState.NOT_STARTED: "info",
}

_LIVE_TEXT = {
    LiveDisplayState.NOT_STARTED: "dashboard.live.not_started",
    LiveDisplayState.STARTING: "dashboard.live.starting",
    LiveDisplayState.READY: "dashboard.live.ready",
    LiveDisplayState.ERROR: "dashboard.live.error",
    LiveDisplayState.STOPPING: "dashboard.live.stopping",
}

_QR_SIDE = 232

_ACTIONS: tuple[tuple[str, str | None, str, str, str], ...] = (
    ("races", "new_race", "dashboard.action.race", "primary", "dashboard-action-races"),
    ("drivers", None, "dashboard.action.drivers", "secondary", "dashboard-action-drivers"),
    ("vehicles", None, "dashboard.action.vehicles", "secondary", "dashboard-action-vehicles"),
    ("tracks", None, "dashboard.action.tracks", "secondary", "dashboard-action-tracks"),
    ("camera_setup", None, "dashboard.action.camera", "secondary", "dashboard-action-camera"),
)


class DashboardPage(QWidget):
    def __init__(self, runtime: Runtime, open_page: OpenPage) -> None:
        super().__init__()
        self._runtime = runtime
        self._open_page = open_page
        self._open_url: Callable[[QUrl], bool] = QDesktopServices.openUrl
        self._qr_url = ""
        self._qr_pixmap: QPixmap | None = None
        self._live_timer = QTimer(self)
        self._live_timer.setInterval(1000)
        self._live_timer.timeout.connect(self._refresh_live_status)
        self._host = QWidget()
        self._host.setObjectName("dashboard-body")
        self._host_layout = QVBoxLayout(self._host)
        configure_page(self._host_layout)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setWidget(self._host)
        layout = QVBoxLayout(self)
        configure_page(layout)
        layout.addWidget(scroll)
        self.refresh()

    def refresh(self) -> None:
        _clear(self._host_layout)
        self._host_layout.addLayout(self._metrics())
        self._host_layout.addLayout(self._race_row())
        live = self._live_card()
        if live is not None:
            self._host_layout.addWidget(live)
        lower = self._lower_row()
        if lower is not None:
            self._host_layout.addLayout(lower)
        self._host_layout.addWidget(self._modules_label())
        self._host_layout.addStretch(1)

    def showEvent(self, event: object) -> None:  # noqa: N802
        super().showEvent(event)  # type: ignore[arg-type]
        self.refresh()
        self._live_timer.start()

    def hideEvent(self, event: object) -> None:  # noqa: N802
        self._live_timer.stop()
        super().hideEvent(event)  # type: ignore[arg-type]

    def _metrics(self) -> QHBoxLayout:
        row = QHBoxLayout()
        row.setSpacing(SPACE.md)
        services = self._runtime.services
        translate = self._runtime.translator.translate
        cards: list[tuple[str, str, str, str]] = []
        drivers = services.find(DriverCatalog)
        vehicles = services.find(VehicleCatalog)
        tracks = services.find(TrackCatalog)
        races = services.find(RaceCatalog)
        if drivers is not None:
            cards.append(
                (
                    "dashboard.metric.drivers",
                    str(len(drivers.list_drivers())),
                    "dashboard.metric.drivers_caption",
                    "metric-drivers",
                )
            )
        if vehicles is not None:
            cards.append(
                (
                    "dashboard.metric.vehicles",
                    str(len(vehicles.list_vehicles())),
                    "dashboard.metric.vehicles_caption",
                    "metric-vehicles",
                )
            )
        if tracks is not None:
            cards.append(
                (
                    "dashboard.metric.tracks",
                    str(len(tracks.list_tracks())),
                    "dashboard.metric.tracks_caption",
                    "metric-tracks",
                )
            )
        if races is not None:
            cards.append(
                (
                    "dashboard.metric.races",
                    str(races.count_races()),
                    "dashboard.metric.races_caption",
                    "metric-races",
                )
            )
        for title_key, value, caption_key, object_name in cards:
            row.addWidget(
                MetricCard(translate(title_key), value, translate(caption_key), object_name), 1
            )
        if not cards:
            row.addStretch(1)
        return row

    def _race_row(self) -> QHBoxLayout:
        row = QHBoxLayout()
        row.setSpacing(SPACE.md)
        races = self._runtime.services.find(RaceCatalog)
        if races is None:
            return row
        active = races.active_summary()
        shown = active if active is not None else races.latest_summary()
        if shown is None:
            row.addWidget(self._empty_race_card(), 1)
        else:
            row.addWidget(self._status_card(shown, active is not None), 1)
        result = races.latest_result()
        if result is not None:
            row.addWidget(self._result_card(result), 1)
        return row

    def _empty_race_card(self) -> Card:
        translate = self._runtime.translator.translate
        card = Card(translate("dashboard.race.title"))
        label = QLabel(translate("dashboard.race.none"))
        set_role(label, "caption")
        card.body.addWidget(label)
        return card

    def _status_card(self, summary: RaceSummary, active: bool) -> Card:
        translate = self._runtime.translator.translate
        card = Card(translate("dashboard.race.title"))
        context = QLabel(translate("dashboard.race.active" if active else "dashboard.race.latest"))
        set_role(context, "caption")
        name = QLabel(summary.name)
        set_role(name, "telemetry")
        pill = StatusPill("dashboard-race-status")
        pill.set_status(
            translate(f"race.status.{summary.status.value}"),
            _RACE_TONE.get(summary.status, "info"),
        )
        meta_key = (
            "dashboard.race.meta_time_trial"
            if summary.mode is RaceMode.TIME_TRIAL
            else "dashboard.race.meta"
        )
        meta = QLabel(
            self._runtime.translator.format(
                meta_key,
                track=summary.track_name or "-",
                laps=summary.laps,
                provider=provider_label(self._runtime.translator, summary.timing_provider),
            )
        )
        set_role(meta, "caption")
        card.body.addWidget(context)
        card.body.addWidget(name)
        card.body.addWidget(pill)
        card.body.addWidget(meta)
        return card

    def _result_card(self, summary: RaceSummary) -> Card:
        translate = self._runtime.translator.translate
        card = Card(translate("dashboard.result.title"))
        name = QLabel(summary.name)
        set_role(name, "telemetry")
        card.body.addWidget(name)
        if not summary.standings:
            empty = QLabel(translate("dashboard.result.empty"))
            set_role(empty, "caption")
            card.body.addWidget(empty)
            return card
        table = make_table(
            [
                translate("dashboard.result.position"),
                translate("dashboard.result.driver"),
                translate("dashboard.result.time"),
                translate("dashboard.result.best"),
            ],
            "dashboard-results",
        )
        table.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        fill_table(
            table,
            [
                (
                    "-" if line.position is None else str(line.position),
                    line.driver_label,
                    format_duration(line.total_time_ns),
                    format_duration(line.best_lap_ns),
                )
                for line in summary.standings
            ],
        )
        header_height = max(table.horizontalHeader().sizeHint().height(), 28)
        row_height = table.verticalHeader().defaultSectionSize()
        table.setFixedHeight(header_height + table.rowCount() * row_height + 2)
        card.body.addWidget(table)
        return card

    def _lower_row(self) -> QHBoxLayout | None:
        timing = self._timing_card()
        actions = self._actions_card()
        if timing is None and actions is None:
            return None
        row = QHBoxLayout()
        row.setSpacing(SPACE.md)
        if timing is not None:
            row.addWidget(timing, 1)
        if actions is not None:
            row.addWidget(actions, 1)
        return row

    def _timing_card(self) -> Card | None:
        registry = self._runtime.services.find(TimingProviderRegistry)
        if registry is None:
            return None
        providers = registry.providers()
        if not providers:
            return None
        translate = self._runtime.translator.translate
        card = Card(translate("dashboard.timing.title"))
        for info in providers:
            line = QLabel(
                f"{provider_label(self._runtime.translator, info.provider_id)}  ·  "
                f"{availability_text(self._runtime.translator, info)}"
            )
            line.setObjectName(f"dashboard-provider-{info.provider_id}")
            set_role(line, "status")
            set_tone(line, "ok" if info.available else "error")
            card.body.addWidget(line)
        return card

    def _actions_card(self) -> Card | None:
        known = {item.id for item in self._runtime.contributions.navigation_items()}
        buttons = [spec for spec in _ACTIONS if spec[0] in known]
        if not buttons:
            return None
        translate = self._runtime.translator.translate
        card = Card(translate("dashboard.actions.title"))
        for entry_id, action, key, role, object_name in buttons:
            button = QPushButton(translate(key))
            button.setObjectName(object_name)
            set_role(button, role)
            button.clicked.connect(
                lambda _checked=False, chosen=entry_id, method=action: self._open_page(
                    chosen, method
                )
            )
            card.body.addWidget(button)
        return card

    def _live_card(self) -> Card | None:
        status = self._pull_live(ensure=True)
        if status is None:
            return None
        translate = self._runtime.translator.translate
        card = Card(translate("dashboard.live.title"))
        card.setObjectName("dashboard-live-card")
        top = QHBoxLayout()
        top.setSpacing(SPACE.md)
        qr = QLabel()
        qr.setObjectName("dashboard-live-qr")
        qr.setFixedSize(_QR_SIDE, _QR_SIDE)
        qr.setAlignment(Qt.AlignmentFlag.AlignCenter)
        qr.setStyleSheet("background: white; border-radius: 4px;")
        top.addWidget(qr, 0, Qt.AlignmentFlag.AlignTop)
        side = QVBoxLayout()
        side.setSpacing(SPACE.sm)
        pill = StatusPill("dashboard-live-status")
        detail = QLabel()
        detail.setObjectName("dashboard-live-detail")
        detail.setWordWrap(True)
        set_role(detail, "caption")
        clients = QLabel()
        clients.setObjectName("dashboard-live-clients")
        clients.setWordWrap(True)
        set_role(clients, "status")
        hint = QLabel(translate("dashboard.live.hint"))
        hint.setObjectName("dashboard-live-hint")
        hint.setWordWrap(True)
        set_role(hint, "caption")
        side.addWidget(pill)
        side.addWidget(detail)
        side.addWidget(clients)
        side.addWidget(hint)
        side.addStretch(1)
        top.addLayout(side, 1)
        url = QLabel()
        url.setObjectName("dashboard-live-url")
        url.setWordWrap(True)
        url.setMinimumWidth(0)
        url.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        set_role(url, "telemetry")
        open_button = QPushButton(translate("dashboard.live.open"))
        open_button.setObjectName("dashboard-live-open")
        set_role(open_button, "primary")
        open_button.clicked.connect(self._open_live_url)
        copy_button = QPushButton(translate("dashboard.live.copy"))
        copy_button.setObjectName("dashboard-live-copy")
        set_role(copy_button, "secondary")
        copy_button.clicked.connect(self._copy_live_url)
        start_button = QPushButton(translate("dashboard.live.start"))
        start_button.setObjectName("dashboard-live-start")
        set_role(start_button, "secondary")
        start_button.clicked.connect(self._start_live_display)
        card.body.addLayout(top)
        card.body.addWidget(url)
        card.body.addWidget(open_button)
        card.body.addWidget(copy_button)
        card.body.addWidget(start_button)
        self._apply_status(status, card)
        return card

    def _pull_live(self, *, ensure: bool) -> LiveDisplayStatus | None:
        display = self._runtime.services.find(LiveDisplay)
        if display is None:
            return None
        if ensure:
            return display.ensure()
        return display.status()

    def _refresh_live_status(self) -> None:
        status = self._pull_live(ensure=False)
        if status is not None:
            self._apply_status(status)

    def _apply_status(self, status: LiveDisplayStatus, root: QWidget | None = None) -> None:
        host = self if root is None else root
        pill = host.findChild(StatusPill, "dashboard-live-status")
        detail = host.findChild(QLabel, "dashboard-live-detail")
        clients = host.findChild(QLabel, "dashboard-live-clients")
        url = host.findChild(QLabel, "dashboard-live-url")
        qr = host.findChild(QLabel, "dashboard-live-qr")
        open_button = host.findChild(QPushButton, "dashboard-live-open")
        copy_button = host.findChild(QPushButton, "dashboard-live-copy")
        start_button = host.findChild(QPushButton, "dashboard-live-start")
        if (
            pill is None
            or detail is None
            or clients is None
            or url is None
            or qr is None
            or open_button is None
            or copy_button is None
            or start_button is None
        ):
            return
        translate = self._runtime.translator.translate
        text = translate(_LIVE_TEXT[status.state])
        if pill.text() != f"● {text}":
            pill.set_status(text, _LIVE_TONE[status.state])
        if detail.text() != status.detail:
            detail.setText(status.detail)
        detail.setVisible(bool(status.detail))
        client_text = ""
        if status.usable:
            if status.client_count == 0:
                client_text = translate("dashboard.live.clients_none")
            elif status.client_count == 1:
                client_text = translate("dashboard.live.clients_one")
            else:
                client_text = self._runtime.translator.format(
                    "dashboard.live.clients", count=status.client_count
                )
        if clients.text() != client_text:
            clients.setText(client_text)
        clients.setVisible(status.usable)
        shown = (
            _display_url(status.url) if status.usable else translate("dashboard.live.unavailable")
        )
        if url.text() != shown:
            url.setText(shown)
        url.setProperty("live-url", status.url if status.usable else "")
        open_button.setEnabled(status.usable)
        copy_button.setEnabled(status.usable)
        can_start = status.state in {LiveDisplayState.NOT_STARTED, LiveDisplayState.ERROR}
        start_button.setVisible(can_start)
        start_button.setEnabled(can_start)
        retry = status.state is LiveDisplayState.ERROR
        start_text = translate("dashboard.live.retry" if retry else "dashboard.live.start")
        if start_button.text() != start_text:
            start_button.setText(start_text)
        self._show_qr(qr, status.url if status.usable else "")

    def _show_qr(self, label: QLabel, url: str) -> None:
        if not url:
            self._qr_url = ""
            label.hide()
            label.clear()
            return
        if self._qr_url != url or self._qr_pixmap is None:
            self._qr_url = url
            self._qr_pixmap = QPixmap.fromImage(qr_image(url, side=_QR_SIDE))
        if label.property("qr-url") != url:
            label.setPixmap(self._qr_pixmap)
            label.setProperty("qr-url", url)
        label.show()

    def _live_url(self) -> str:
        url = self.findChild(QLabel, "dashboard-live-url")
        if url is None:
            return ""
        stored = url.property("live-url")
        return stored if isinstance(stored, str) else ""

    def _open_live_url(self) -> None:
        url = self._live_url()
        if url:
            self._open_url(QUrl(url))

    def _copy_live_url(self) -> None:
        url = self._live_url()
        clipboard = QApplication.clipboard()
        if url and clipboard is not None:
            clipboard.setText(url)

    def _start_live_display(self) -> None:
        display = self._runtime.services.find(LiveDisplay)
        pill = self.findChild(StatusPill, "dashboard-live-status")
        if display is None or pill is None:
            return
        pill.set_status(
            self._runtime.translator.translate("dashboard.live.starting"),
            _LIVE_TONE[LiveDisplayState.STARTING],
        )
        QApplication.processEvents(QEventLoop.ProcessEventsFlag.ExcludeUserInputEvents)
        self._apply_status(display.enable())

    def _modules_label(self) -> QLabel:
        translate = self._runtime.translator.translate
        enabled = [
            f"{status.title} ({status.version})"
            for status in self._runtime.plugins.statuses()
            if status.state is PluginState.ENABLED
        ]
        if enabled:
            text = (
                translate("dashboard.active_modules")
                + ":\n"
                + "\n".join(f"- {line}" for line in enabled)
            )
        else:
            text = translate("dashboard.no_modules")
        label = QLabel(text)
        label.setObjectName("dashboard-modules")
        label.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft)
        set_role(label, "caption")
        return label


def _display_url(url: str) -> str:
    """Show the address with break points. The copy action keeps the original."""
    step = 28
    return "\u200b".join(url[index : index + step] for index in range(0, len(url), step))


def _clear(layout: QLayout) -> None:
    while (item := layout.takeAt(0)) is not None:
        nested = item.layout()
        if nested is not None:
            _clear(nested)
        widget = item.widget()
        if widget is not None:
            widget.setParent(None)
            widget.deleteLater()
