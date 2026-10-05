"""Enter one original part into the library."""

from __future__ import annotations

from PySide6.QtWidgets import QComboBox, QDoubleSpinBox, QLineEdit, QSpinBox

from slot_racing.core.domain.lanes import MAX_LANE_COUNT
from slot_racing.core.i18n import Translator
from slot_racing.modules.track_planner.parts import (
    CATEGORIES,
    IMPLIED_SCALE,
    SCALES,
    PartSpec,
    build_part,
)
from slot_racing.modules.track_planner.service import TrackPlannerService
from slot_racing.uikit.dialog import FormDialog


class PartDialog(FormDialog):
    def __init__(self, translator: Translator, planner: TrackPlannerService) -> None:
        super().__init__(translator, translator.translate("planner.library.add"))
        self._planner = planner
        self.created: PartSpec | None = None
        self.system = QComboBox()
        self.system.setObjectName("part-system")
        self.system.setEditable(True)
        for name in (*IMPLIED_SCALE, "Eigenbau"):
            self.system.addItem(name)
        self.system.currentTextChanged.connect(self._sync_scale)
        self.article = QLineEdit()
        self.article.setObjectName("part-article")
        self.scale = QComboBox()
        self.scale.setObjectName("part-scale")
        for value in SCALES:
            self.scale.addItem(value, value)
        self.name = QLineEdit()
        self.name.setObjectName("part-name")
        self.category = QComboBox()
        self.category.setObjectName("part-category")
        for category in CATEGORIES:
            self.category.addItem(translator.translate(f"planner.category.{category}"), category)
        self.length = _measure("part-length")
        self.width_mm = _measure("part-width")
        self.height_mm = _measure("part-height")
        self.radius = _measure("part-radius")
        self.angle = _measure("part-angle")
        self.lanes = QSpinBox()
        self.lanes.setObjectName("part-lanes")
        self.lanes.setRange(1, MAX_LANE_COUNT)
        self.lanes.setValue(2)
        form = self.form
        translate = translator.translate
        form.addRow(translate("planner.field.system"), self.system)
        form.addRow(translate("planner.field.article"), self.article)
        form.addRow(translate("planner.field.scale"), self.scale)
        form.addRow(translate("planner.field.name"), self.name)
        form.addRow(translate("planner.field.category"), self.category)
        form.addRow(translate("planner.field.length"), self.length)
        form.addRow(translate("planner.field.width"), self.width_mm)
        form.addRow(translate("planner.field.height"), self.height_mm)
        form.addRow(translate("planner.field.radius"), self.radius)
        form.addRow(translate("planner.field.angle"), self.angle)
        form.addRow(translate("planner.field.lanes"), self.lanes)
        self._sync_scale(self.system.currentText())

    def submit(self) -> None:
        scale = self.scale.currentData()
        spec = build_part(
            system=self.system.currentText(),
            article_number=self.article.text(),
            scale=None if scale is None else str(scale),
            name=self.name.text(),
            category=str(self.category.currentData()),
            length_mm=_optional(self.length),
            width_mm=_optional(self.width_mm),
            height_mm=_optional(self.height_mm),
            radius_mm=_optional(self.radius),
            angle_deg=_optional(self.angle),
            lane_count=self.lanes.value(),
        )
        self._planner.add_part(spec)
        self.created = spec

    def _sync_scale(self, system: str) -> None:
        known = IMPLIED_SCALE.get(system.strip())
        self.scale.setEnabled(known is None)
        if known is not None:
            index = self.scale.findData(known)
            if index >= 0:
                self.scale.setCurrentIndex(index)


def _measure(object_name: str) -> QDoubleSpinBox:
    spin = QDoubleSpinBox()
    spin.setObjectName(object_name)
    spin.setRange(0, 100_000)
    spin.setDecimals(1)
    spin.setSpecialValueText("—")
    return spin


def _optional(spin: QDoubleSpinBox) -> float | None:
    if spin.value() <= 0:
        return None
    return float(spin.value())
