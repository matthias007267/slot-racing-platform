"""Asks who is ready for the next heat, and who cannot start or has to leave the race."""

from __future__ import annotations

from PySide6.QtCore import Signal
from PySide6.QtWidgets import QComboBox, QLabel, QPushButton, QVBoxLayout, QWidget

from slot_racing.core.i18n import Translator
from slot_racing.modules.races.types import HeatBriefing
from slot_racing.uikit.theme import SPACE, set_role


class HeatGate(QWidget):
    start_requested = Signal()
    postpone_requested = Signal(int)
    disqualify_requested = Signal(int)

    def __init__(self, translator: Translator) -> None:
        super().__init__()
        self.translator = translator
        self.setObjectName("heat-gate")
        tr = translator.translate
        self.title_label = QLabel()
        self.title_label.setObjectName("heat-title")
        self.body_label = QLabel()
        self.body_label.setObjectName("heat-body")
        self.body_label.setWordWrap(True)
        self.driver_combo = QComboBox()
        self.driver_combo.setObjectName("heat-driver")
        self.postpone_button = QPushButton(tr("race.heat.postpone"))
        self.postpone_button.setObjectName("heat-postpone")
        self.disqualify_button = QPushButton(tr("race.heat.disqualify"))
        self.disqualify_button.setObjectName("heat-disqualify")
        self.start_button = QPushButton(tr("race.heat.start"))
        self.start_button.setObjectName("heat-start")
        set_role(self.start_button, "primary")
        set_role(self.postpone_button, "secondary")
        set_role(self.disqualify_button, "danger")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(SPACE.sm)
        layout.addWidget(self.title_label)
        layout.addWidget(self.body_label)
        layout.addWidget(self.driver_combo)
        layout.addWidget(self.postpone_button)
        layout.addWidget(self.disqualify_button)
        layout.addWidget(self.start_button)
        self.postpone_button.clicked.connect(self._postpone)
        self.disqualify_button.clicked.connect(self._disqualify)
        self.start_button.clicked.connect(self.start_requested.emit)
        self.hide()

    def show_briefing(self, briefing: HeatBriefing) -> None:
        fmt = self.translator.format
        tr = self.translator.translate
        free = tr("race.time_trial.free")
        lines = [fmt("race.heat.next", sequence=briefing.sequence), ""]
        for seat in briefing.seats:
            name = seat.driver_label or free
            lines.append(fmt("race.heat.seat", lane=seat.lane, driver=name))
        changes = [change for change in briefing.changes if change.from_lane is not None]
        if changes:
            lines.extend(["", tr("race.heat.rotation")])
            for change in changes:
                lines.append(
                    fmt(
                        "race.heat.change",
                        driver=change.driver_label,
                        from_lane=change.from_lane,
                        to_lane=change.to_lane,
                    )
                )
        lines.extend(["", tr("race.heat.ready_hint")])
        self.title_label.setText(fmt("race.heat.title", sequence=briefing.sequence))
        self.body_label.setText("\n".join(lines))
        chosen = self.driver_combo.currentData()
        self.driver_combo.clear()
        for participant_id, label in briefing.drivers:
            self.driver_combo.addItem(label, participant_id)
        if chosen is not None:
            index = self.driver_combo.findData(chosen)
            if index >= 0:
                self.driver_combo.setCurrentIndex(index)
        self.show()

    def _postpone(self) -> None:
        participant_id = self.driver_combo.currentData()
        if participant_id is not None:
            self.postpone_requested.emit(int(participant_id))

    def _disqualify(self) -> None:
        participant_id = self.driver_combo.currentData()
        if participant_id is not None:
            self.disqualify_requested.emit(int(participant_id))
