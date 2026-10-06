"""Edit how many of each library part the user owns. Definitions stay in the library."""

from __future__ import annotations

from collections.abc import Callable

from PySide6.QtCore import QRegularExpression, Qt
from PySide6.QtGui import QRegularExpressionValidator
from PySide6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from slot_racing.core.i18n import Translator
from slot_racing.modules.track_planner.parts import PartRecord
from slot_racing.modules.track_planner.service import TrackPlannerService
from slot_racing.uikit.errors import describe_error
from slot_racing.uikit.theme import set_role
from slot_racing.uikit.widgets import StatusLabel


class CollectionDialog(QDialog):
    """One row per definition. A change is stored immediately."""

    def __init__(
        self,
        translator: Translator,
        planner: TrackPlannerService,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("collection-dialog")
        self.setWindowTitle(translator.translate("planner.stock.manage"))
        self.setMinimumSize(640, 480)
        self._translator = translator
        self._planner = planner
        self._rows: list[_StockRow] = []
        self.status = StatusLabel("collection-message")
        stock = planner.stock_quantities()
        records = tuple(
            sorted(planner.list_parts(), key=lambda record: record.spec.name.casefold())
        )
        self._list = QWidget()
        self._list.setObjectName("collection-list")
        rows = QVBoxLayout(self._list)
        rows.setContentsMargins(0, 0, 0, 0)
        for record in records:
            row = _StockRow(translator, record, stock.get(record.id, 0), self._store)
            self._rows.append(row)
            rows.addWidget(row)
        rows.addStretch(1)
        scroll = QScrollArea()
        scroll.setObjectName("collection-scroll")
        scroll.setWidgetResizable(True)
        scroll.setWidget(self._list)
        close = QPushButton(translator.translate("planner.stock.close"))
        close.setObjectName("collection-close")
        close.clicked.connect(self.accept)
        actions = QHBoxLayout()
        actions.addStretch(1)
        actions.addWidget(close)
        layout = QVBoxLayout(self)
        layout.addWidget(scroll, 1)
        layout.addWidget(self.status)
        layout.addLayout(actions)

    def quantity(self, part_id: int) -> int:
        for row in self._rows:
            if row.part_id == part_id:
                return row.quantity()
        raise KeyError(part_id)

    def _store(self, part_id: int, quantity: int) -> bool:
        try:
            self._planner.set_stock(part_id, quantity)
        except Exception as error:
            self.status.show_error(describe_error(self._translator, error))
            return False
        self.status.show_info(self._translator.translate("planner.stock.saved"))
        return True


class _StockRow(QWidget):
    def __init__(
        self,
        translator: Translator,
        record: PartRecord,
        quantity: int,
        store: Callable[[int, int], bool],
    ) -> None:
        super().__init__()
        self.part_id = record.id
        self._store = store
        self._quantity = quantity
        self._filling = False
        self.setObjectName(f"collection-row-{record.id}")
        name = QLabel(record.spec.name)
        name.setObjectName("collection-name")
        article = QLabel(record.spec.article_number)
        article.setObjectName("collection-article")
        self.minus = QPushButton("-")
        self.minus.setObjectName(f"collection-minus-{record.id}")
        set_role(self.minus, "ghost")
        self.minus.clicked.connect(self._decrease)
        self.field = QLineEdit(str(quantity))
        self.field.setObjectName(f"collection-quantity-{record.id}")
        self.field.setValidator(QRegularExpressionValidator(QRegularExpression(r"[0-9]*")))
        self.field.setFixedWidth(96)
        self.field.setAlignment(Qt.AlignmentFlag.AlignRight)
        self.field.editingFinished.connect(self._edited)
        self.plus = QPushButton("+")
        self.plus.setObjectName(f"collection-plus-{record.id}")
        set_role(self.plus, "ghost")
        self.plus.clicked.connect(self._increase)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(4, 2, 4, 2)
        layout.addWidget(name, 1)
        layout.addWidget(article)
        layout.addWidget(self.minus)
        layout.addWidget(self.field)
        layout.addWidget(self.plus)
        self.setToolTip(translator.translate("planner.stock.manage"))

    def quantity(self) -> int:
        return self._quantity

    def _decrease(self) -> None:
        self._apply(max(self._quantity - 1, 0))

    def _increase(self) -> None:
        self._apply(self._quantity + 1)

    def _edited(self) -> None:
        if self._filling:
            return
        text = self.field.text().strip()
        if not text.isdigit():
            self._show(self._quantity)
            return
        self._apply(int(text))

    def _apply(self, quantity: int) -> None:
        if quantity == self._quantity:
            self._show(quantity)
            return
        if not self._store(self.part_id, quantity):
            self._show(self._quantity)
            return
        self._quantity = quantity
        self._show(quantity)

    def _show(self, quantity: int) -> None:
        self._filling = True
        self.field.setText(str(quantity))
        self._filling = False
