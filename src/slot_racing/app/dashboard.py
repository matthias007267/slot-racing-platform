"""Dashboard built only from catalogs and the timing registry. Missing sources are omitted."""

from __future__ import annotations

from collections.abc import Callable

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QLayout,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

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
        lower = self._lower_row()
        if lower is not None:
            self._host_layout.addLayout(lower)
        self._host_layout.addWidget(self._modules_label())
        self._host_layout.addStretch(1)

    def showEvent(self, event: object) -> None:  # noqa: N802
        super().showEvent(event)  # type: ignore[arg-type]
        self.refresh()

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


def _clear(layout: QLayout) -> None:
    while (item := layout.takeAt(0)) is not None:
        nested = item.layout()
        if nested is not None:
            _clear(nested)
        widget = item.widget()
        if widget is not None:
            widget.setParent(None)
            widget.deleteLater()
