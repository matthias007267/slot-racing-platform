"""Base class for small input dialogs."""

from __future__ import annotations

import logging

from PySide6.QtWidgets import QDialog, QDialogButtonBox, QFormLayout, QVBoxLayout, QWidget

from slot_racing.core.i18n import Translator
from slot_racing.uikit.errors import describe_error, is_expected
from slot_racing.uikit.theme import SPACE, set_role
from slot_racing.uikit.widgets import StatusLabel

logger = logging.getLogger(__name__)


class FormDialog(QDialog):
    """Form with OK/Cancel. A failing :meth:`submit` keeps the dialog open and shows the error."""

    def __init__(self, translator: Translator, title: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.translator = translator
        self.setWindowTitle(title)
        self.setModal(True)
        self.form = QFormLayout()
        self.message = StatusLabel("dialog-message")
        self._buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        self._buttons.accepted.connect(self.accept)
        self._buttons.rejected.connect(self.reject)
        ok_button = self._buttons.button(QDialogButtonBox.StandardButton.Ok)
        if ok_button is not None:
            ok_button.setDefault(True)
            set_role(ok_button, "primary")
        cancel_button = self._buttons.button(QDialogButtonBox.StandardButton.Cancel)
        if cancel_button is not None:
            set_role(cancel_button, "ghost")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(SPACE.md, SPACE.md, SPACE.md, SPACE.md)
        layout.setSpacing(SPACE.sm)
        layout.addLayout(self.form)
        layout.addWidget(self.message)
        layout.addWidget(self._buttons)

    def submit(self) -> None:
        """Validate and save. Raise :class:`ValidationError` for invalid input."""
        raise NotImplementedError

    def accept(self) -> None:
        try:
            self.submit()
        except Exception as error:
            if not is_expected(error):
                logger.exception("Saving from dialog %r failed", self.windowTitle())
            self.message.show_error(describe_error(self.translator, error))
            return
        super().accept()
