"""Element editor for one library part.

Saving is explicit. Opening a part and leaving it unchanged does not rewrite
its geometry. A draft may be incomplete. A usable part needs a closed plate,
valid paths and consistent lane joints.
"""

from __future__ import annotations

import logging
from dataclasses import replace

from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from slot_racing.core.domain import TrackId
from slot_racing.core.errors import ValidationError
from slot_racing.core.i18n import Translator
from slot_racing.modules.track_planner.graphic_session import ISSUE_CATEGORY
from slot_racing.modules.track_planner.parametric import with_metadata
from slot_racing.modules.track_planner.parts import (
    BORDER,
    CURVE,
    ORIGIN_CUSTOM,
    SCALES,
    SPECIAL,
    STRAIGHT,
    PartRecord,
    PartSpec,
    is_plate_draft,
)
from slot_racing.modules.track_planner.service import TrackPlannerService
from slot_racing.modules.track_planner.ui.graphic_editor import GraphicEditor
from slot_racing.uikit.dialog import FormDialog
from slot_racing.uikit.errors import describe_error, is_expected
from slot_racing.uikit.theme import set_role

logger = logging.getLogger(__name__)
_EDITOR_CATEGORIES = (STRAIGHT, CURVE, SPECIAL, BORDER)


class PartDialog(FormDialog):
    """Draw one part, or change only its designation."""

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
        self.setMinimumSize(1100, 760)
        self._planner = planner
        self._record = None if duplicate else record
        self._source = None if record is None else record.spec
        self._duplicate = duplicate
        self._corrupt = False
        self._block_save = False
        self._dismissed = False
        self._saved_ok = False
        self.created: PartSpec | None = None
        self.saved: PartRecord | None = None
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
        self.description.setFixedHeight(64)
        self.origin = QLabel(translate("planner.editor.origin.custom"))
        self.origin.setObjectName("part-origin")
        self.category = QComboBox()
        self.category.setObjectName("part-category")
        self.notice = QLabel()
        self.notice.setObjectName("part-editor-notice")
        self.notice.setWordWrap(True)
        self.notice.hide()
        self.graphic = GraphicEditor(translator, self)
        self.graphic.spec_provider = self._preview_spec
        self.preview = self.graphic.canvas
        meta = QWidget()
        meta.setObjectName("part-metadata")
        form = QFormLayout(meta)
        form.setContentsMargins(0, 0, 0, 0)
        heading = QLabel(translate("planner.editor.metadata"))
        set_role(heading, "section")
        form.addRow(heading)
        form.addRow(translate("planner.field.name"), self.name)
        form.addRow(translate("planner.field.article"), self.article)
        form.addRow(translate("planner.editor.manufacturer"), self.manufacturer)
        form.addRow(translate("planner.editor.description"), self.description)
        form.addRow(translate("planner.editor.origin"), self.origin)
        form.addRow(translate("planner.field.scale"), self.scale)
        form.addRow(translate("planner.field.category"), self.category)
        form.addRow(self.notice)
        self.graphic.side.insertWidget(0, meta)
        self._fill(record, duplicate)
        self._capture()
        self._install()

    def submit(self) -> None:
        self._store(as_draft=False)

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

    def _store(self, *, as_draft: bool) -> None:
        if self._corrupt or self._block_save:
            raise ValidationError("error.planner.geometry")
        spec = self._compiled(as_draft=as_draft)
        if self._record is None:
            self.saved = self._planner.add_part(spec)
        else:
            self.saved = self._planner.update_part(self._record.id, spec)
        self.created = self.saved.spec
        if self._record is not None:
            offer_adoption(self, self.translator, self._planner, self.saved)

    def _compiled(self, *, as_draft: bool) -> PartSpec:
        article, scale, name, manufacturer, description = self._texts()
        category = str(self.category.currentData() or STRAIGHT)
        session = self.graphic.session
        spec = with_metadata(
            session.compile(
                article_number=article or "Vorschau",
                scale=scale or "1:24",
                name=name or "Vorschau",
                manufacturer=manufacturer,
                description=description,
                category=category,
                as_draft=as_draft,
            ),
            article_number=article,
            scale=scale,
            name=name,
            manufacturer=manufacturer,
            description=description,
        )
        source = session.source
        promote = not as_draft and (
            source is None or session.edited or (source is not None and is_plate_draft(source))
        )
        if promote:
            if category == "draft":
                raise ValidationError(ISSUE_CATEGORY)
            issues = session.release_issues()
            self.graphic.show_issues(issues)
            if issues:
                raise ValidationError(issues[0][0])
        if self._duplicate:
            spec = replace(spec, source_id="", piece_key="", selectable=True)
        return spec

    def _preview_spec(self) -> PartSpec | None:
        if self._corrupt and self._source is not None:
            return self._source
        session = self.graphic.session
        if session.source is not None and not session.edited:
            return session.source
        scale = self.scale.currentData()
        category = self.category.currentData()
        try:
            return session.compile(
                article_number=self.article.text() or "Vorschau",
                scale="1:24" if scale is None else str(scale),
                name=self.name.text() or "Vorschau",
                manufacturer=self.manufacturer.text(),
                description=self.description.toPlainText(),
                category=STRAIGHT if category is None else str(category),
                as_draft=True,
            )
        except (ValidationError, ValueError):
            return session.source

    def _texts(self) -> tuple[str, str, str, str, str]:
        scale = self.scale.currentData()
        return (
            self.article.text(),
            "" if scale is None else str(scale),
            self.name.text(),
            self.manufacturer.text(),
            self.description.toPlainText(),
        )

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
            self.graphic.load(None)
            return
        self.article.setText(spec.article_number)
        self.name.setText(self._copy_name(spec.name) if duplicate else spec.name)
        self.manufacturer.setText(spec.manufacturer)
        self.description.setPlainText(spec.description)
        self._select(self.scale, spec.scale)
        self._select(self.category, spec.category)
        corrupt = record is not None and record.geometry_corrupt
        self._corrupt = corrupt
        self._block_save = duplicate and corrupt
        if corrupt:
            self.notice.setText(self.translator.translate("planner.editor.corrupt"))
            self.notice.show()
            self.graphic.set_editable(False)
        self.graphic.load(spec)

    def _install(self) -> None:
        layout = self.layout()
        assert isinstance(layout, QVBoxLayout)
        layout.insertWidget(0, self.graphic, 1)
        layout.removeItem(self.form)
        box = self.findChild(QDialogButtonBox)
        assert box is not None
        ok_button = box.button(QDialogButtonBox.StandardButton.Ok)
        cancel_button = box.button(QDialogButtonBox.StandardButton.Cancel)
        if ok_button is not None:
            ok_button.setText(self.translator.translate("planner.editor.save"))
            ok_button.setObjectName("part-editor-save")
            if self._block_save:
                ok_button.setEnabled(False)
        if cancel_button is not None:
            cancel_button.setText(self.translator.translate("planner.editor.cancel"))
            cancel_button.setObjectName("part-editor-cancel")
        draft = QPushButton(self.translator.translate("planner.editor.draft"))
        draft.setObjectName("part-editor-draft")
        draft.clicked.connect(self._save_draft)
        if self._block_save:
            draft.setEnabled(False)
        box.addButton(draft, QDialogButtonBox.ButtonRole.ActionRole)
        self._restore_button = QPushButton(self.translator.translate("planner.editor.restore"))
        self._restore_button.setObjectName("part-editor-restore")
        self._restore_button.clicked.connect(self._restore_catalog)
        box.addButton(self._restore_button, QDialogButtonBox.ButtonRole.ResetRole)
        # addButton shows the button. A new or custom part has nothing to restore.
        if self._record is None or self._record.origin == ORIGIN_CUSTOM:
            self._restore_button.hide()

    def _save_draft(self) -> None:
        try:
            self._store(as_draft=True)
        except Exception as error:
            if not is_expected(error):
                logger.exception("Saving draft %r failed", self.windowTitle())
            self.message.show_error(describe_error(self.translator, error))
            return
        self._saved_ok = True
        QDialog.accept(self)

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
            self.graphic.session.elements,
            self.graphic.session.pen_points,
        )

    def _copy_name(self, name: str) -> str:
        return self.translator.format("planner.editor.copy_name", name=name)[:120]

    @staticmethod
    def _select(box: QComboBox, value: object) -> None:
        index = box.findData(value)
        if index >= 0:
            box.setCurrentIndex(index)


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
