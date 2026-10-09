"""Large, wrapped briefing blocks for the race assistant and the heat change."""

from __future__ import annotations

from PySide6.QtWidgets import QFrame, QGridLayout, QLabel, QSizePolicy, QVBoxLayout, QWidget

from slot_racing.core.domain import RaceMode
from slot_racing.core.i18n import Translator
from slot_racing.modules.races.hud import (
    TEXT_DRIVER,
    TEXT_HEADER,
    TEXT_PLACE,
    TEXT_ROW,
    TEXT_TIME,
    ViewStyle,
)
from slot_racing.modules.races.types import HeatBriefing, HeatInfo, RaceInfo
from slot_racing.uikit.theme import (
    FONT_CAPTION,
    FONT_HEAT,
    FONT_HINT,
    FONT_LANE,
    FONT_VALUE,
    SPACE,
    set_role,
)


def apply_text_size(label: QLabel, base: int, width: int, *, bold: bool, scale: int = 100) -> None:
    """Grow with the window, and stay large enough to read on a narrow one.

    The size is a theme step. A widget stylesheet keeps it, because the application
    sheet otherwise replaces a font that was only set on the label.
    ``scale`` is a percent. 100 keeps the size this board already used.
    """
    factor = min(1.3, max(0.85, width / 960))
    pixels = min(96, max(16, round(base * factor * scale / 100)))
    font = label.font()
    font.setPixelSize(pixels)
    font.setBold(bold)
    label.setFont(font)
    weight = 700 if bold else 500
    label.setStyleSheet(f"font-size: {pixels}px; font-weight: {weight};")


def _wrapped(text: str, role: str) -> QLabel:
    label = QLabel(text)
    label.setWordWrap(True)
    label.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
    set_role(label, role)
    return label


class SeatCard(QFrame):
    """One lane and the driver assigned to it."""

    def __init__(self, lane_text: str, driver: str) -> None:
        super().__init__()
        set_role(self, "card")
        self.lane_label = _wrapped(lane_text, "wizard-caption")
        self.driver_label = _wrapped(driver, "lane-name")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(SPACE.md, SPACE.md, SPACE.md, SPACE.md)
        layout.setSpacing(SPACE.xs)
        layout.addWidget(self.lane_label)
        layout.addWidget(self.driver_label)

    def fit(self, width: int, *, lane_scale: int = 100, driver_scale: int = 100) -> None:
        apply_text_size(self.lane_label, FONT_CAPTION, width, bold=True, scale=lane_scale)
        apply_text_size(self.driver_label, FONT_LANE, width, bold=True, scale=driver_scale)


class BriefingBoard(QWidget):
    """Current heat, lane assignments and lane changes. Buttons stay with the host."""

    def __init__(self) -> None:
        super().__init__()
        self.setObjectName("heat-briefing")
        self._view = ViewStyle()
        self.title = _wrapped("", "heat-title")
        self.title.setObjectName("heat-briefing-title")
        self.changes_caption = _wrapped("", "wizard-caption")
        self.changes_caption.setObjectName("heat-changes-caption")
        self.hint = _wrapped("", "wizard-caption")
        self.hint.setObjectName("heat-briefing-hint")
        self._seat_host = QWidget()
        self._seats = QGridLayout(self._seat_host)
        self._seats.setContentsMargins(0, 0, 0, 0)
        self._seats.setSpacing(SPACE.md)
        self._change_host = QWidget()
        self._changes = QVBoxLayout(self._change_host)
        self._changes.setContentsMargins(0, 0, 0, 0)
        self._changes.setSpacing(SPACE.sm)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(SPACE.md)
        layout.addWidget(self.title)
        layout.addWidget(self._seat_host)
        layout.addWidget(self.changes_caption)
        layout.addWidget(self._change_host)
        layout.addWidget(self.hint)
        layout.addStretch(1)

    def set_view_style(self, style: ViewStyle) -> None:
        self._view = style
        self.fit(self.width())

    def show_message(self, text: str) -> None:
        """A single large line when there is no heat to brief."""
        self.title.setText(text)
        self.hint.clear()
        self.changes_caption.hide()
        self._change_host.hide()
        _clear(self._seats)
        self.fit(self.width())

    def show_briefing(self, briefing: HeatBriefing, translator: Translator) -> None:
        fmt = translator.format
        free = translator.translate("race.time_trial.free")
        self.title.setText(fmt("race.heat.title", sequence=briefing.sequence))
        self.hint.setText(translator.translate("race.heat.ready_hint"))
        _clear(self._seats)
        columns = 2 if len(briefing.seats) > 1 else 1
        for index, seat in enumerate(briefing.seats):
            name = seat.driver_label or free
            card = SeatCard(f"{translator.translate('race.live.lane')} {seat.lane}", name)
            card.lane_label.setObjectName(f"heat-seat-lane-{seat.lane}")
            card.driver_label.setObjectName(f"heat-seat-driver-{seat.lane}")
            card.driver_label.setText(name)
            self._seats.addWidget(card, index // columns, index % columns)
        for column in range(columns):
            self._seats.setColumnStretch(column, 1)
        changes = [change for change in briefing.changes if change.from_lane is not None]
        _clear(self._changes)
        self.changes_caption.setText(translator.translate("race.heat.rotation") if changes else "")
        self.changes_caption.setVisible(bool(changes))
        self._change_host.setVisible(bool(changes))
        for change in changes:
            line = _wrapped(
                fmt(
                    "race.heat.change",
                    driver=change.driver_label,
                    from_lane=change.from_lane,
                    to_lane=change.to_lane,
                ),
                "lane-name",
            )
            line.setObjectName("heat-change")
            self._changes.addWidget(line)
        self.fit(self.width())

    def resizeEvent(self, event: object) -> None:  # noqa: N802
        super().resizeEvent(event)  # type: ignore[arg-type]
        self.fit(self.width())

    def fit(self, width: int) -> None:
        place = _percent(self._view, TEXT_PLACE)
        header = _percent(self._view, TEXT_HEADER)
        driver = _percent(self._view, TEXT_DRIVER)
        time = _percent(self._view, TEXT_TIME)
        apply_text_size(self.title, FONT_HEAT, width, bold=True, scale=place)
        apply_text_size(self.changes_caption, FONT_CAPTION, width, bold=True, scale=header)
        apply_text_size(self.hint, FONT_HINT, width, bold=False, scale=time)
        for index in range(self._seats.count()):
            item = self._seats.itemAt(index)
            widget = None if item is None else item.widget()
            if isinstance(widget, SeatCard):
                widget.fit(width, lane_scale=header, driver_scale=driver)
        for index in range(self._changes.count()):
            item = self._changes.itemAt(index)
            widget = None if item is None else item.widget()
            if isinstance(widget, QLabel):
                apply_text_size(widget, FONT_VALUE, width, bold=True, scale=driver)


class OverviewBoard(QWidget):
    """Step 5: race facts, drivers and every heat, in blocks instead of one paragraph."""

    def __init__(self) -> None:
        super().__init__()
        self.setObjectName("race-overview-board")
        self._view = ViewStyle()
        self._layout = QVBoxLayout(self)
        self._layout.setContentsMargins(0, 0, 0, 0)
        self._layout.setSpacing(SPACE.md)

    def set_view_style(self, style: ViewStyle) -> None:
        self._view = style
        self.fit(self.width())

    def show_race(
        self,
        race: RaceInfo,
        heats: tuple[HeatInfo, ...],
        translator: Translator,
        provider: str,
    ) -> None:
        fmt = translator.format
        _clear(self._layout)
        mode = (
            fmt("race.overview.time_trial_duration", minutes=race.duration_minutes or 0)
            if race.mode is RaceMode.TIME_TRIAL
            else fmt("race.overview.laps", laps=race.laps)
        )
        facts = (
            fmt("race.overview.race", name=race.name),
            fmt("race.overview.track", track=race.track_name, lanes=race.lane_count),
            mode,
            fmt("race.overview.provider", provider=provider),
        )
        for text in facts:
            self._layout.addWidget(_wrapped(text, "wizard-value"))
        self._layout.addWidget(
            _wrapped(translator.translate("race.overview.participants"), "wizard-caption")
        )
        for participant in race.participants:
            self._layout.addWidget(
                _wrapped(
                    fmt(
                        "race.overview.participant",
                        driver=participant.driver_label,
                        vehicle=participant.vehicle_label,
                    ),
                    "lane-name",
                )
            )
        if heats:
            self._layout.addWidget(
                _wrapped(translator.translate("race.overview.heats"), "wizard-caption")
            )
            names = {person.id: person.driver_label for person in race.participants}
            for heat in heats:
                self._layout.addWidget(
                    _wrapped(fmt("race.heat.title", sequence=heat.sequence), "heat-title")
                )
                host = QWidget()
                grid = QGridLayout(host)
                grid.setContentsMargins(0, 0, 0, 0)
                grid.setSpacing(SPACE.md)
                columns = 2 if len(heat.seats) > 1 else 1
                for index, (participant_id, lane) in enumerate(heat.seats):
                    card = SeatCard(
                        f"{translator.translate('race.live.lane')} {lane}",
                        names.get(participant_id, ""),
                    )
                    grid.addWidget(card, index // columns, index % columns)
                self._layout.addWidget(host)
        self._layout.addStretch(1)
        self.fit(self.width())

    def resizeEvent(self, event: object) -> None:  # noqa: N802
        super().resizeEvent(event)  # type: ignore[arg-type]
        self.fit(self.width())

    def fit(self, width: int) -> None:
        place = _percent(self._view, TEXT_PLACE)
        header = _percent(self._view, TEXT_HEADER)
        driver = _percent(self._view, TEXT_DRIVER)
        row = _percent(self._view, TEXT_ROW)
        for label in self.findChildren(QLabel):
            role = label.property("role")
            if role == "heat-title":
                apply_text_size(label, FONT_HEAT, width, bold=True, scale=place)
            elif role == "lane-name":
                apply_text_size(label, FONT_LANE, width, bold=True, scale=driver)
            elif role == "wizard-value":
                apply_text_size(label, FONT_VALUE, width, bold=True, scale=row)
            else:
                apply_text_size(label, FONT_CAPTION, width, bold=True, scale=header)
        for card in self.findChildren(SeatCard):
            card.fit(width, lane_scale=header, driver_scale=driver)


def _percent(style: ViewStyle, role: str) -> int:
    return round(style.font_scale * style.text_scale(role) / 100)


def _clear(layout: QGridLayout | QVBoxLayout) -> None:
    while layout.count():
        item = layout.takeAt(0)
        if item is None:
            continue
        widget = item.widget()
        if widget is not None:
            widget.hide()
            widget.deleteLater()
            widget.setParent(None)
        child = item.layout()
        if child is not None:
            _clear(child)  # type: ignore[arg-type]
