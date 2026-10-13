"""Parametric part editor. Saving is explicit; cancel drops the draft."""

from __future__ import annotations

import logging
import math
from typing import Literal

from PySide6.QtCore import QPointF, Qt
from PySide6.QtGui import QColor, QPainter, QPen, QPolygonF
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from slot_racing.core.domain import TrackId
from slot_racing.core.domain.lanes import MAX_LANE_COUNT
from slot_racing.core.errors import ValidationError
from slot_racing.core.i18n import Translator
from slot_racing.modules.track_planner.parametric import (
    BODY_BORDER,
    BODY_CURVE,
    BODY_STRAIGHT,
    DIRECTION_LEFT,
    DIRECTION_RIGHT,
    SIDE_BOTH,
    PartDraft,
    draft_from_spec,
    spec_from_draft,
    with_metadata,
)
from slot_racing.modules.track_planner.parts import (
    BORDER,
    CURVE,
    LANE_PITCH_MM,
    ORIGIN_CUSTOM,
    SCALES,
    SPECIAL,
    STRAIGHT,
    TRACK,
    PartRecord,
    PartSpec,
)
from slot_racing.modules.track_planner.service import TrackPlannerService
from slot_racing.modules.track_planner.ui.track_paint import paint_part
from slot_racing.uikit.dialog import FormDialog
from slot_racing.uikit.errors import describe_error, is_expected
from slot_racing.uikit.theme import COLORS, set_role

logger = logging.getLogger(__name__)
_EDITOR_CATEGORIES = (STRAIGHT, CURVE, SPECIAL, BORDER)


class PartDialog(FormDialog):
    """Create, edit or duplicate one part. Geometry the form cannot express is kept."""

    def __init__(
        self,
        translator: Translator,
        planner: TrackPlannerService,
        record: PartRecord | None = None,
        parent: QWidget | None = None,
        *,
        duplicate: bool = False,
    ) -> None:
        if duplicate:
            title = translator.translate("planner.library.duplicate")
        elif record is not None:
            title = translator.translate("planner.library.edit")
        else:
            title = translator.translate("planner.library.add")
        super().__init__(translator, title, parent)
        self.setObjectName("part-editor")
        self.setMinimumSize(920, 680)
        self._planner = planner
        self._record = None if duplicate else record
        self._source = None if record is None else record.spec
        self._duplicate = duplicate
        self._explicit = False
        self._host_shape = ""
        self._locked = False
        self._corrupt = False
        self._block_save = False
        self._dismissed = False
        self._saved_ok = False
        self.created: PartSpec | None = None
        self.saved: PartRecord | None = None
        self._last_preview: PartSpec | None = None
        translate = translator.translate
        self.article = QLineEdit()
        self.article.setObjectName("part-article")
        self.scale = QComboBox()
        self.scale.setObjectName("part-scale")
        for value in SCALES:
            self.scale.addItem(value, value)
        self.name = QLineEdit()
        self.name.setObjectName("part-name")
        self.manufacturer = QLineEdit()
        self.manufacturer.setObjectName("part-manufacturer")
        self.description = QPlainTextEdit()
        self.description.setObjectName("part-description")
        self.description.setFixedHeight(72)
        self.origin = QLabel(translate("planner.editor.origin.custom"))
        self.origin.setObjectName("part-origin")
        self.category = QComboBox()
        self.category.setObjectName("part-category")
        self.shape = QComboBox()
        self.shape.setObjectName("part-shape")
        self.shape.addItem(translate("planner.category.straight"), BODY_STRAIGHT)
        self.shape.addItem(translate("planner.category.curve"), BODY_CURVE)
        self.host = QComboBox()
        self.host.setObjectName("part-host")
        self.host.addItem(translate("planner.category.straight"), BODY_STRAIGHT)
        self.host.addItem(translate("planner.category.curve"), BODY_CURVE)
        self.length = _measure("part-length")
        self.width_mm = _measure("part-width")
        self.height_mm = _measure("part-height")
        self.radius = _measure("part-radius")
        self.angle = _measure("part-angle")
        self.pitch = _measure("part-pitch")
        self.pitch.setSpecialValueText("")
        self.pitch.setValue(LANE_PITCH_MM)
        self.offsets = QLineEdit()
        self.offsets.setObjectName("part-offsets")
        self.offsets.setPlaceholderText(translate("planner.editor.offsets.hint"))
        self.direction = QComboBox()
        self.direction.setObjectName("part-direction")
        self.direction.addItem(translate("planner.editor.direction.right"), DIRECTION_RIGHT)
        self.direction.addItem(translate("planner.editor.direction.left"), DIRECTION_LEFT)
        self.border_side = QComboBox()
        self.border_side.setObjectName("part-side")
        self.lanes = QSpinBox()
        self.lanes.setObjectName("part-lanes")
        self.lanes.setRange(1, MAX_LANE_COUNT)
        self.lanes.setValue(2)
        self.notice = QLabel()
        self.notice.setObjectName("part-editor-notice")
        self.notice.setWordWrap(True)
        self.notice.hide()
        self.preview = EditorPreview()
        self._fields = QFormLayout()
        self._build_form(translate)
        self._fill(record, duplicate)
        self._capture()
        self._wire()
        self._refresh_preview()

    def submit(self) -> None:
        if self._block_save:
            raise ValidationError("error.planner.geometry")
        spec = self._spec_to_save()
        if self._record is None:
            self.saved = self._planner.add_part(spec)
        else:
            self.saved = self._planner.update_part(self._record.id, spec)
        self.created = self.saved.spec
        if self._record is not None:
            offer_adoption(self, self.translator, self._planner, self.saved)

    def accept(self) -> None:
        try:
            self.submit()
        except Exception as error:
            if not is_expected(error):
                logger.exception("Saving part %r failed", self.windowTitle())
            self.message.show_error(describe_error(self.translator, error))
            return
        self._saved_ok = True
        QDialog.accept(self)

    def reject(self) -> None:
        if not self._allow_close():
            return
        super().reject()

    def closeEvent(self, event: object) -> None:  # noqa: N802
        if self._saved_ok or self._allow_close():
            super().closeEvent(event)  # type: ignore[arg-type]
            return
        event.ignore()  # type: ignore[attr-defined]

    def _spec_to_save(self) -> PartSpec:
        article, scale, name, manufacturer, description = self._texts()
        if self._locked:
            if self._source is None:
                raise ValidationError("error.planner.part")
            return with_metadata(
                self._source,
                article_number=article,
                scale=scale,
                name=name,
                manufacturer=manufacturer,
                description=description,
            )
        return spec_from_draft(
            self._draft(),
            height_mm=_optional(self.height_mm),
            article_number=article,
            scale=scale,
            name=name,
            manufacturer=manufacturer,
            description=description,
        )

    def _texts(self) -> tuple[str, str, str, str, str]:
        scale = self.scale.currentData()
        return (
            self.article.text(),
            "" if scale is None else str(scale),
            self.name.text(),
            self.manufacturer.text(),
            self.description.toPlainText(),
        )

    def _draft(self) -> PartDraft:
        category = str(self.category.currentData())
        body = self._body()
        width = None if self.width_mm.value() <= 0 else float(self.width_mm.value())
        if body == BODY_BORDER and (width is None or width <= 0):
            width = 40.0
        return PartDraft(
            category=category,
            body=body,
            length_mm=_optional(self.length),
            radius_mm=_optional(self.radius),
            angle_deg=_optional(self.angle),
            direction=str(self.direction.currentData()),
            lane_count=int(self.lanes.value()),
            pitch_mm=float(self.pitch.value()) if self.pitch.value() > 0 else LANE_PITCH_MM,
            width_mm=width,
            offsets_mm=_parse_offsets(self.offsets.text()),
            border_side=str(self.border_side.currentData()),
            host_lanes=int(self.lanes.value()),
            explicit_grooves=self._explicit,
            host_shape=self._host_shape,
        )

    def _body(self) -> Literal["straight", "curve", "border"]:
        category = str(self.category.currentData())
        shape = str(self.shape.currentData())
        if category == BORDER or (category == SPECIAL and shape == BODY_BORDER):
            return BODY_BORDER
        if category == CURVE or (category == SPECIAL and shape == BODY_CURVE):
            return BODY_CURVE
        return BODY_STRAIGHT

    def _fill(self, record: PartRecord | None, duplicate: bool) -> None:
        origin = ORIGIN_CUSTOM if record is None or duplicate else record.origin
        self.origin.setText(self.translator.translate(f"planner.editor.origin.{origin}"))
        categories = list(_EDITOR_CATEGORIES)
        spec = None if record is None else record.spec
        if spec is not None and spec.category not in categories:
            categories.append(spec.category)
        for category in categories:
            self.category.addItem(
                self.translator.translate(f"planner.category.{category}"), category
            )
        if spec is None:
            self.length.setValue(345.0)
            self._sync_fields()
            return
        self.article.setText(spec.article_number)
        self.name.setText(self._copy_name(spec.name) if duplicate else spec.name)
        self.manufacturer.setText(spec.manufacturer)
        self.description.setPlainText(spec.description)
        self._select(self.scale, spec.scale)
        self._select(self.category, spec.category)
        self.height_mm.setValue(0.0 if spec.height_mm is None else spec.height_mm)
        self.lanes.setValue(spec.lane_count)
        corrupt = record is not None and record.geometry_corrupt
        draft = None if corrupt else draft_from_spec(spec)
        self._corrupt = corrupt
        self._block_save = duplicate and corrupt
        self._locked = draft is None or corrupt
        if draft is None:
            self.length.setValue(0.0 if spec.length_mm is None else spec.length_mm)
            self.width_mm.setValue(0.0 if spec.width_mm is None else spec.width_mm)
            self.radius.setValue(0.0 if spec.radius_mm is None else spec.radius_mm)
            self.angle.setValue(0.0 if spec.angle_deg is None else spec.angle_deg)
        else:
            self._explicit = draft.explicit_grooves
            self._host_shape = draft.host_shape
            self._apply_draft(draft)
        if corrupt:
            self.notice.setText(self.translator.translate("planner.editor.corrupt"))
        elif self._locked:
            self.notice.setText(self.translator.translate("planner.editor.locked"))
        self.notice.setVisible(self._locked)
        self._sync_fields()
        if self._block_save:
            save = self.findChild(QPushButton, "part-editor-save")
            if save is not None:
                save.setEnabled(False)
        if record is not None and not duplicate and record.origin != ORIGIN_CUSTOM:
            self._restore_button.show()

    def _apply_draft(self, draft: PartDraft) -> None:
        self._select(self.shape, draft.body if draft.body != BODY_BORDER else BODY_STRAIGHT)
        self._select(self.host, BODY_CURVE if draft.radius_mm else BODY_STRAIGHT)
        self.length.setValue(0.0 if draft.length_mm is None else draft.length_mm)
        self.radius.setValue(0.0 if draft.radius_mm is None else draft.radius_mm)
        self.angle.setValue(0.0 if draft.angle_deg is None else draft.angle_deg)
        self.pitch.setValue(draft.pitch_mm)
        self.width_mm.setValue(0.0 if draft.width_mm is None else draft.width_mm)
        self.lanes.setValue(draft.lane_count)
        self._select(self.direction, draft.direction)
        if draft.offsets_mm:
            self.offsets.setText(", ".join(_trim(value) for value in draft.offsets_mm))
        self._fill_sides()
        self._select(self.border_side, draft.border_side)

    def _sync_fields(self) -> None:
        body = self._body()
        border = body == BODY_BORDER
        curve = body == BODY_CURVE or (border and str(self.host.currentData()) == BODY_CURVE)
        straight = not curve
        enabled = not self._locked
        self.category.setEnabled(enabled)
        special = str(self.category.currentData()) == SPECIAL
        self.shape.setEnabled(enabled)
        self.host.setEnabled(enabled)
        self._fields.setRowVisible(self.shape, special)
        self._fields.setRowVisible(self.host, border)
        self._fields.setRowVisible(self.border_side, border)
        self._fields.setRowVisible(self.direction, curve)
        self._fields.setRowVisible(self.pitch, not border)
        self._fields.setRowVisible(self.offsets, not border)
        self.length.setEnabled(enabled and straight)
        self.radius.setEnabled(enabled and curve)
        self.angle.setEnabled(enabled and curve)
        self.direction.setEnabled(enabled and curve)
        self.pitch.setEnabled(enabled and not border)
        self.offsets.setEnabled(enabled and not border)
        self.lanes.setEnabled(enabled)
        self.width_mm.setEnabled(enabled)
        self.height_mm.setEnabled(enabled)
        self.border_side.setEnabled(enabled and border)
        self._fill_sides()

    def _fill_sides(self) -> None:
        current = self.border_side.currentData()
        self.border_side.blockSignals(True)
        self.border_side.clear()
        translate = self.translator.translate
        curve = self._body() == BODY_BORDER and str(self.host.currentData()) == BODY_CURVE
        choices: tuple[tuple[str, str], ...]
        if curve:
            choices = (
                ("planner.editor.side.outer", "outer"),
                ("planner.editor.side.inner", "inner"),
            )
        else:
            choices = (
                ("planner.editor.side.both", SIDE_BOTH),
                ("planner.editor.side.left", "left"),
                ("planner.editor.side.right", "right"),
            )
        for key, value in choices:
            self.border_side.addItem(translate(key), value)
        self._select(self.border_side, current if isinstance(current, str) else choices[0][1])
        self.border_side.blockSignals(False)

    def _refresh_preview(self) -> None:
        spec = self._preview_spec()
        if spec is not None:
            self._last_preview = spec
        self.preview.set_spec(self._last_preview, literal=self._corrupt)

    def _preview_spec(self) -> PartSpec | None:
        if self._locked and self._source is not None:
            return self._source
        article, scale, name, manufacturer, description = self._texts()
        try:
            return spec_from_draft(
                self._draft(),
                height_mm=_optional(self.height_mm),
                validate_identity=False,
                article_number=article,
                scale=scale,
                name=name,
                manufacturer=manufacturer,
                description=description,
            )
        except ValidationError:
            return None

    def _wire(self) -> None:
        self.category.currentIndexChanged.connect(self._changed)
        self.shape.currentIndexChanged.connect(self._changed)
        self.host.currentIndexChanged.connect(self._changed)
        self.direction.currentIndexChanged.connect(self._refresh_preview)
        self.border_side.currentIndexChanged.connect(self._refresh_preview)
        self.scale.currentIndexChanged.connect(self._refresh_preview)
        spins = (self.length, self.width_mm, self.height_mm, self.radius, self.angle, self.pitch)
        for spin in spins:
            spin.valueChanged.connect(self._refresh_preview)
        self.lanes.valueChanged.connect(self._refresh_preview)
        self.offsets.textChanged.connect(self._refresh_preview)
        self.name.textChanged.connect(self._refresh_preview)
        self.article.textChanged.connect(self._refresh_preview)

    def _changed(self) -> None:
        body = self._body()
        curve = body == BODY_CURVE or (
            body == BODY_BORDER and str(self.host.currentData()) == BODY_CURVE
        )
        if curve:
            if self.radius.value() <= 0:
                self.radius.setValue(300.0)
            if self.angle.value() <= 0:
                self.angle.setValue(60.0)
        elif self.length.value() <= 0:
            self.length.setValue(345.0)
        self._sync_fields()
        self._refresh_preview()

    def _build_form(self, translate: object) -> None:
        assert callable(translate)
        form = self._fields
        form.addRow(translate("planner.field.name"), self.name)
        form.addRow(translate("planner.field.article"), self.article)
        form.addRow(translate("planner.editor.manufacturer"), self.manufacturer)
        form.addRow(translate("planner.editor.description"), self.description)
        form.addRow(translate("planner.editor.origin"), self.origin)
        form.addRow(translate("planner.field.scale"), self.scale)
        form.addRow(translate("planner.field.category"), self.category)
        form.addRow(translate("planner.editor.shape"), self.shape)
        form.addRow(translate("planner.editor.host"), self.host)
        form.addRow(translate("planner.field.length"), self.length)
        form.addRow(translate("planner.field.width"), self.width_mm)
        form.addRow(translate("planner.field.height"), self.height_mm)
        form.addRow(translate("planner.field.radius"), self.radius)
        form.addRow(translate("planner.field.angle"), self.angle)
        form.addRow(translate("planner.editor.direction"), self.direction)
        form.addRow(translate("planner.editor.pitch"), self.pitch)
        form.addRow(translate("planner.editor.offsets"), self.offsets)
        form.addRow(translate("planner.field.lanes"), self.lanes)
        form.addRow(translate("planner.editor.side"), self.border_side)
        host = QWidget()
        host.setLayout(form)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(host)
        scroll.setMinimumWidth(420)
        row = QWidget()
        columns = QHBoxLayout(row)
        columns.addWidget(scroll, 1)
        side = QVBoxLayout()
        caption = QLabel(translate("planner.editor.preview"))
        set_role(caption, "section")
        side.addWidget(caption)
        side.addWidget(self.preview, 1)
        side.addWidget(self.notice)
        columns.addLayout(side, 1)
        layout = self.layout()
        assert isinstance(layout, QVBoxLayout)
        layout.insertWidget(0, row)
        layout.removeItem(self.form)
        self._style_buttons(translate)

    def _style_buttons(self, translate: object) -> None:
        assert callable(translate)
        box = self.findChild(QDialogButtonBox)
        assert box is not None
        ok_button = box.button(QDialogButtonBox.StandardButton.Ok)
        cancel_button = box.button(QDialogButtonBox.StandardButton.Cancel)
        if ok_button is not None:
            ok_button.setText(translate("planner.editor.save"))
            ok_button.setObjectName("part-editor-save")
        if cancel_button is not None:
            cancel_button.setText(translate("planner.editor.cancel"))
            cancel_button.setObjectName("part-editor-cancel")
        self._restore_button = QPushButton(translate("planner.editor.restore"))
        self._restore_button.setObjectName("part-editor-restore")
        self._restore_button.hide()
        self._restore_button.clicked.connect(self._restore_catalog)
        box.addButton(self._restore_button, QDialogButtonBox.ButtonRole.ResetRole)

    def _restore_catalog(self) -> None:
        if self._record is None:
            return
        answer = QMessageBox.question(
            self,
            self.translator.translate("planner.editor.restore"),
            self.translator.translate("planner.editor.restore_confirm"),
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        try:
            self.saved = self._planner.restore_catalog(self._record.id)
        except Exception as error:
            self.message.show_error(describe_error(self.translator, error))
            return
        self.created = self.saved.spec
        offer_adoption(self, self.translator, self._planner, self.saved)
        self._saved_ok = True
        QDialog.accept(self)

    def _allow_close(self) -> bool:
        if self._saved_ok or self._dismissed or not self._dirty():
            self._dismissed = True
            return True
        answer = QMessageBox.question(
            self,
            self.translator.translate("planner.editor.cancel"),
            self.translator.translate("planner.editor.discard"),
        )
        allowed = answer == QMessageBox.StandardButton.Yes
        if allowed:
            self._dismissed = True
        return allowed

    def _dirty(self) -> bool:
        return hasattr(self, "_baseline") and self._snapshot() != self._baseline

    def _capture(self) -> None:
        self._baseline = self._snapshot()

    def _snapshot(self) -> tuple[object, ...]:
        return (
            self.name.text(),
            self.article.text(),
            self.manufacturer.text(),
            self.description.toPlainText(),
            self.scale.currentData(),
            self.category.currentData(),
            self.shape.currentData(),
            self.host.currentData(),
            self.length.value(),
            self.width_mm.value(),
            self.height_mm.value(),
            self.radius.value(),
            self.angle.value(),
            self.pitch.value(),
            self.offsets.text(),
            self.direction.currentData(),
            self.border_side.currentData(),
            self.lanes.value(),
        )

    def _copy_name(self, name: str) -> str:
        text = self.translator.format("planner.editor.copy_name", name=name)
        return text[:120]

    @staticmethod
    def _select(box: QComboBox, value: object) -> None:
        index = box.findData(value)
        if index >= 0:
            box.setCurrentIndex(index)


class EditorPreview(QWidget):
    """The same painter the plan uses, plus joint marks."""

    def __init__(self) -> None:
        super().__init__()
        self.setObjectName("part-editor-preview")
        self.setMinimumSize(300, 300)
        self._spec: PartSpec | None = None
        self._literal = False

    def set_spec(self, spec: PartSpec | None, *, literal: bool = False) -> None:
        self._spec = spec
        self._literal = literal
        self.update()

    def paintEvent(self, _event: object) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor(COLORS.surface))
        spec = self._spec
        if spec is None:
            painter.end()
            return
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        _fit(painter, spec, self.width(), self.height())
        _grid(painter)
        if self._literal:
            _outline(painter, spec)
        else:
            paint_part(
                painter,
                spec,
                color_coding=False,
                selected=False,
                start_straight=False,
                attachment_slot=_slot(spec),
            )
        _marks(painter, spec)
        painter.end()


def offer_adoption(
    parent: QWidget,
    translator: Translator,
    planner: TrackPlannerService,
    record: PartRecord,
) -> int:
    """Ask which current plans should pick up the new geometry. Archives stay put."""
    tracks = planner.tracks_on_previous_geometry(record.id)
    if not tracks:
        return 0
    dialog = QDialog(parent)
    dialog.setObjectName("part-adopt")
    dialog.setWindowTitle(translator.translate("planner.library.adopt"))
    dialog.setMinimumWidth(460)
    kept = QLabel(translator.translate("planner.editor.kept"))
    kept.setObjectName("part-adopt-kept")
    kept.setWordWrap(True)
    current = QLabel(translator.format("planner.editor.current", revision=record.revision))
    current.setObjectName("part-adopt-current")
    hint = QLabel(translator.translate("planner.editor.adopt_hint"))
    hint.setWordWrap(True)
    boxes: list[tuple[TrackId, QPushButton]] = []
    choice_host = QWidget()
    choices = QVBoxLayout(choice_host)
    for track_id, name in tracks:
        button = QPushButton(name)
        button.setObjectName(f"part-adopt-track-{int(track_id)}")
        button.setCheckable(True)
        choices.addWidget(button)
        boxes.append((track_id, button))
    buttons = QDialogButtonBox()
    adopt = buttons.addButton(
        translator.translate("planner.editor.adopt"), QDialogButtonBox.ButtonRole.AcceptRole
    )
    later = buttons.addButton(
        translator.translate("planner.editor.later"), QDialogButtonBox.ButtonRole.RejectRole
    )
    adopt.setObjectName("part-adopt-confirm")
    later.setObjectName("part-adopt-later")
    set_role(adopt, "primary")
    buttons.accepted.connect(dialog.accept)
    buttons.rejected.connect(dialog.reject)
    layout = QVBoxLayout(dialog)
    layout.addWidget(kept)
    layout.addWidget(current)
    layout.addWidget(hint)
    layout.addWidget(choice_host)
    layout.addWidget(buttons)
    if dialog.exec() != QDialog.DialogCode.Accepted:
        return 0
    chosen = [track_id for track_id, button in boxes if button.isChecked()]
    if not chosen:
        return 0
    return planner.adopt_revision(record.id, chosen)


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


def _parse_offsets(text: str) -> tuple[float, ...] | None:
    raw = text.replace(";", ",").strip()
    if not raw:
        return None
    values: list[float] = []
    for piece in raw.split(","):
        item = piece.strip()
        if not item:
            continue
        try:
            value = float(item)
        except ValueError as error:
            raise ValidationError("error.planner.editor.offsets") from error
        if not math.isfinite(value):
            raise ValidationError("error.planner.editor.offsets")
        values.append(value)
    if not values:
        return None
    return tuple(values)


def _trim(value: float) -> str:
    return f"{value:.1f}".rstrip("0").rstrip(".")


def _slot(spec: PartSpec) -> str | None:
    profile = spec.attachment
    if profile is None or not profile.slots:
        return None
    return profile.slots[0]


def _fit(painter: QPainter, spec: PartSpec, width: int, height: int) -> None:
    points = list(spec.outline)
    for connector in spec.connectors:
        points.append((connector.x_mm, connector.y_mm))
    if not points:
        points = [(-20.0, -10.0), (20.0, 10.0)]
    xs = [point[0] for point in points]
    ys = [point[1] for point in points]
    span_x = max(max(xs) - min(xs), 1.0)
    span_y = max(max(ys) - min(ys), 1.0)
    margin = 28.0
    scale = min((width - 2 * margin) / span_x, (height - 2 * margin) / span_y)
    center_x = (min(xs) + max(xs)) / 2.0
    center_y = (min(ys) + max(ys)) / 2.0
    painter.translate(width / 2.0, height / 2.0)
    painter.scale(scale, scale)
    painter.translate(-center_x, -center_y)


def _outline(painter: QPainter, spec: PartSpec) -> None:
    polygon = QPolygonF([QPointF(x_mm, y_mm) for x_mm, y_mm in spec.outline])
    if polygon.size() < 2:
        return
    pen = QPen(QColor(COLORS.text))
    pen.setCosmetic(True)
    pen.setWidthF(1.5)
    painter.setPen(pen)
    painter.setBrush(Qt.BrushStyle.NoBrush)
    painter.drawPolygon(polygon)


def _grid(painter: QPainter) -> None:
    pen = QPen(QColor(COLORS.border))
    pen.setCosmetic(True)
    pen.setWidthF(1.0)
    painter.setPen(pen)
    step = 100.0
    reach = 4000.0
    cursor = -reach
    while cursor <= reach:
        painter.drawLine(int(cursor), int(-reach), int(cursor), int(reach))
        painter.drawLine(int(-reach), int(cursor), int(reach), int(cursor))
        cursor += step


def _marks(painter: QPainter, spec: PartSpec) -> None:
    for connector in spec.connectors:
        color = COLORS.accent if connector.kind == TRACK else COLORS.warning
        pen = QPen(QColor(color))
        pen.setCosmetic(True)
        pen.setWidthF(2.0)
        painter.setPen(pen)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawEllipse(QPointF(connector.x_mm, connector.y_mm), 6.0, 6.0)
        radians = math.radians(connector.direction_deg)
        painter.drawLine(
            QPointF(connector.x_mm, connector.y_mm),
            QPointF(
                connector.x_mm + math.cos(radians) * 22.0,
                connector.y_mm + math.sin(radians) * 22.0,
            ),
        )
    profile = spec.attachment
    pen = QPen(QColor(COLORS.info))
    pen.setCosmetic(True)
    pen.setWidthF(2.0)
    painter.setPen(pen)
    for edge in spec.border_edges:
        for span in edge.spans:
            if span.kind == "line":
                painter.drawLine(QPointF(span.x0, span.y0), QPointF(span.x1, span.y1))
    if profile is not None:
        for span in profile.edge:
            if span.kind == "line":
                painter.drawLine(QPointF(span.x0, span.y0), QPointF(span.x1, span.y1))
