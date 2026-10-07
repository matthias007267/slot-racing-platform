"""Edit package counts and the manual correction. Physical stock is derived."""

from __future__ import annotations

from collections.abc import Callable, Mapping

from PySide6.QtCore import QRegularExpression, Qt
from PySide6.QtGui import QRegularExpressionValidator, QShowEvent
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
from slot_racing.modules.track_planner.inventory import PhysicalStock
from slot_racing.modules.track_planner.packages import PackageContentView, PackageView
from slot_racing.modules.track_planner.parts import PartRecord
from slot_racing.modules.track_planner.service import TrackPlannerService
from slot_racing.modules.track_planner.trace import trace
from slot_racing.uikit.errors import describe_error
from slot_racing.uikit.theme import set_role, set_tone
from slot_racing.uikit.widgets import StatusLabel


class CollectionDialog(QDialog):
    """Packages first, then one row per physical definition. A change is stored immediately."""

    def __init__(
        self,
        translator: Translator,
        planner: TrackPlannerService,
        parent: QWidget | None = None,
        usage: Mapping[int, int] | None = None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("collection-dialog")
        self.setWindowTitle(translator.translate("planner.stock.manage"))
        self.setMinimumSize(640, 480)
        self._translator = translator
        self._planner = planner
        self._usage = dict(usage or {})
        self._rows: list[_StockRow] = []
        self._package_rows: list[_PackageRow] = []
        self._opened = False
        self.status = StatusLabel("collection-message")
        self.status.setParent(self)
        breakdown = {line.part_id: line for line in planner.stock_breakdown().values()}
        records = tuple(
            sorted(planner.list_parts(), key=lambda record: record.spec.name.casefold())
        )
        self._list = QWidget(self)
        self._list.setObjectName("collection-list")
        rows = QVBoxLayout(self._list)
        rows.setContentsMargins(0, 0, 0, 0)
        packages = planner.list_packages()
        if packages:
            heading = QLabel(translator.translate("planner.stock.packages"), self._list)
            heading.setObjectName("collection-packages")
            set_role(heading, "caption")
            rows.addWidget(heading)
            for package in packages:
                package_row = _PackageRow(translator, package, self._store_package, self._list)
                self._package_rows.append(package_row)
                rows.addWidget(package_row)
        parts_heading = QLabel(translator.translate("planner.stock.parts"), self._list)
        parts_heading.setObjectName("collection-parts")
        set_role(parts_heading, "caption")
        rows.addWidget(parts_heading)
        for record in records:
            line = breakdown.get(record.id)
            adjustment = 0 if line is None else line.adjustment
            part_row = _StockRow(
                translator,
                record,
                adjustment,
                self._usage.get(record.id, 0),
                line,
                self._store_adjustment,
                self._list,
            )
            self._rows.append(part_row)
            rows.addWidget(part_row)
        rows.addStretch(1)
        scroll = QScrollArea(self)
        scroll.setObjectName("collection-scroll")
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        scroll.setWidget(self._list)
        close = QPushButton(translator.translate("planner.stock.close"), self)
        close.setObjectName("collection-close")
        close.clicked.connect(self.accept)
        actions = QHBoxLayout()
        actions.addStretch(1)
        actions.addWidget(close)
        layout = QVBoxLayout(self)
        layout.addWidget(scroll, 1)
        layout.addWidget(self.status)
        layout.addLayout(actions)

    def showEvent(self, event: QShowEvent) -> None:  # noqa: N802
        if not self._opened:
            self._opened = True
            trace("COLLECTION_DIALOG_OPEN", result="opened")
        super().showEvent(event)

    def quantity(self, part_id: int) -> int:
        for row in self._rows:
            if row.part_id == part_id:
                return row.quantity()
        raise KeyError(part_id)

    def package_quantity(self, package_id: int) -> int:
        for row in self._package_rows:
            if row.package_id == package_id:
                return row.quantity()
        raise KeyError(package_id)

    def _store_adjustment(self, part_id: int, quantity: int) -> bool:
        try:
            self._planner.set_stock_adjustment(part_id, quantity)
        except Exception as error:
            self.status.show_error(describe_error(self._translator, error))
            return False
        self.status.show_info(self._translator.translate("planner.stock.saved"))
        self._refresh_labels()
        return True

    def _store_package(self, package_id: int, quantity: int) -> bool:
        try:
            self._planner.set_package_stock(package_id, quantity)
        except Exception as error:
            self.status.show_error(describe_error(self._translator, error))
            return False
        self.status.show_info(self._translator.translate("planner.stock.saved"))
        self._refresh_labels()
        return True

    def _refresh_labels(self) -> None:
        packages = {package.id: package for package in self._planner.list_packages()}
        breakdown = self._planner.stock_breakdown()
        for package_row in self._package_rows:
            package = packages.get(package_row.package_id)
            if package is not None:
                package_row.show_contained(package)
        for part_row in self._rows:
            line = breakdown.get(part_row.part_id)
            derived = 0 if line is None else line.derived
            physical = 0 if line is None else line.physical
            part_row.show_balance(derived, physical, self._usage.get(part_row.part_id, 0))


class _PackageRow(QWidget):
    def __init__(
        self,
        translator: Translator,
        package: PackageView,
        store: Callable[[int, int], bool],
        parent: QWidget,
    ) -> None:
        super().__init__(parent)
        self.package_id = package.id
        self._store = store
        self._quantity = package.quantity
        self._filling = False
        self._translator = translator
        self.setObjectName(f"collection-package-row-{package.id}")
        title = QLabel(f"{package.manufacturer} {package.article_number}", self)
        title.setObjectName("collection-package-article")
        title.setWordWrap(True)
        name = QLabel(package.name, self)
        name.setObjectName("collection-package-name")
        name.setWordWrap(True)
        caption = QLabel(translator.translate("planner.stock.package_qty"), self)
        self.minus = QPushButton("-", self)
        self.minus.setObjectName(f"collection-package-minus-{package.id}")
        set_role(self.minus, "ghost")
        self.minus.clicked.connect(self._decrease)
        self.field = QLineEdit(str(package.quantity), self)
        self.field.setObjectName(f"collection-package-quantity-{package.id}")
        self.field.setValidator(QRegularExpressionValidator(QRegularExpression(r"[0-9]*")))
        self.field.setFixedWidth(72)
        self.field.setAlignment(Qt.AlignmentFlag.AlignRight)
        self.field.editingFinished.connect(self._edited)
        self.plus = QPushButton("+", self)
        self.plus.setObjectName(f"collection-package-plus-{package.id}")
        set_role(self.plus, "ghost")
        self.plus.clicked.connect(self._increase)
        controls = QHBoxLayout()
        controls.setContentsMargins(0, 0, 0, 0)
        controls.addWidget(caption)
        controls.addWidget(self.minus)
        controls.addWidget(self.field)
        controls.addWidget(self.plus)
        controls.addStretch(1)
        self.recipe = QLabel(self)
        self.recipe.setObjectName(f"collection-package-recipe-{package.id}")
        self.recipe.setWordWrap(True)
        self.contained = QLabel(self)
        self.contained.setObjectName(f"collection-package-contained-{package.id}")
        self.contained.setWordWrap(True)
        self.show_contained(package)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 8, 4, 4)
        layout.addWidget(title)
        layout.addWidget(name)
        layout.addLayout(controls)
        layout.addWidget(self.recipe)
        layout.addWidget(self.contained)

    def quantity(self) -> int:
        return self._quantity

    def show_contained(self, package: PackageView) -> None:
        self.recipe.setText(
            self._translator.translate("planner.stock.per_pack")
            + ": "
            + _pieces(package.contents, multiplier=1)
        )
        self.contained.setText(
            self._translator.translate("planner.stock.contained")
            + ":\n"
            + _pieces(package.contents, multiplier=package.quantity)
        )

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
        if not self._store(self.package_id, quantity):
            self._show(self._quantity)
            return
        self._quantity = quantity
        self._show(quantity)

    def _show(self, quantity: int) -> None:
        self._filling = True
        self.field.setText(str(quantity))
        self._filling = False


class _StockRow(QWidget):
    def __init__(
        self,
        translator: Translator,
        record: PartRecord,
        adjustment: int,
        used: int,
        line: PhysicalStock | None,
        store: Callable[[int, int], bool],
        parent: QWidget,
    ) -> None:
        super().__init__(parent)
        self.part_id = record.id
        self._store = store
        self._quantity = adjustment
        self._filling = False
        self._translator = translator
        self.setObjectName(f"collection-row-{record.id}")
        name = QLabel(record.spec.name, self)
        name.setObjectName("collection-name")
        name.setWordWrap(True)
        article = QLabel(record.spec.article_number, self)
        article.setObjectName("collection-article")
        header = QHBoxLayout()
        header.setContentsMargins(0, 0, 0, 0)
        header.addWidget(name, 1)
        header.addWidget(article)
        caption = QLabel(translator.translate("planner.stock.adjustment"), self)
        self.minus = QPushButton("-", self)
        self.minus.setObjectName(f"collection-minus-{record.id}")
        set_role(self.minus, "ghost")
        self.minus.clicked.connect(self._decrease)
        self.field = QLineEdit(str(adjustment), self)
        self.field.setObjectName(f"collection-quantity-{record.id}")
        self.field.setValidator(QRegularExpressionValidator(QRegularExpression(r"-?[0-9]*")))
        self.field.setFixedWidth(72)
        self.field.setAlignment(Qt.AlignmentFlag.AlignRight)
        self.field.editingFinished.connect(self._edited)
        self.plus = QPushButton("+", self)
        self.plus.setObjectName(f"collection-plus-{record.id}")
        set_role(self.plus, "ghost")
        self.plus.clicked.connect(self._increase)
        controls = QHBoxLayout()
        controls.setContentsMargins(0, 0, 0, 0)
        controls.addWidget(caption)
        controls.addWidget(self.minus)
        controls.addWidget(self.field)
        controls.addWidget(self.plus)
        controls.addStretch(1)
        self.derived = QLabel(self)
        self.derived.setObjectName(f"collection-derived-{record.id}")
        self.derived.setWordWrap(True)
        self.physical = QLabel(self)
        self.physical.setObjectName(f"collection-physical-{record.id}")
        self.physical.setWordWrap(True)
        self.used = QLabel(self)
        self.used.setObjectName(f"collection-used-{record.id}")
        self.available = QLabel(self)
        self.available.setObjectName(f"collection-available-{record.id}")
        self.available.setWordWrap(True)
        figures = QHBoxLayout()
        figures.setContentsMargins(0, 0, 0, 0)
        figures.addWidget(self.physical)
        figures.addWidget(self.used)
        figures.addWidget(self.available, 1)
        produced = 0 if line is None else line.derived
        owned = 0 if line is None else line.physical
        self.show_balance(produced, owned, used)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 6, 4, 2)
        layout.addLayout(header)
        layout.addWidget(self.derived)
        layout.addLayout(controls)
        layout.addLayout(figures)
        self.setToolTip(translator.translate("planner.stock.adjustment"))

    def quantity(self) -> int:
        return self._quantity

    def show_balance(self, derived: int, physical: int, used: int) -> None:
        self.derived.setText(self._translator.format("planner.stock.derived_value", count=derived))
        self.physical.setText(
            self._translator.format("planner.stock.physical_value", count=physical)
        )
        self.used.setText(self._translator.format("planner.stock.used_value", count=used))
        available = physical - used
        if available < 0:
            self.available.setText(self._translator.format("planner.stock.over", count=-available))
            set_tone(self.available, "error")
        else:
            self.available.setText(
                self._translator.format("planner.stock.available_value", count=available)
            )
            set_tone(self.available, "muted")

    def _decrease(self) -> None:
        self._apply(self._quantity - 1)

    def _increase(self) -> None:
        self._apply(self._quantity + 1)

    def _edited(self) -> None:
        if self._filling:
            return
        text = self.field.text().strip()
        if text in {"", "-"} or not _signed_digits(text):
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


def _pieces(contents: tuple[PackageContentView, ...], *, multiplier: int) -> str:
    return "\n".join(f"{multiplier * line.quantity} \u00d7 {line.part_name}" for line in contents)


def _signed_digits(text: str) -> bool:
    body = text[1:] if text.startswith("-") else text
    return bool(body) and body.isdigit()
