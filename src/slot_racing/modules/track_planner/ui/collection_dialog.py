"""Edit package counts and the manual correction. Physical stock is derived."""

from __future__ import annotations

from collections.abc import Callable, Mapping

from PySide6.QtCore import QRegularExpression, Qt
from PySide6.QtGui import QRegularExpressionValidator, QShowEvent
from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from slot_racing.core.i18n import Translator
from slot_racing.modules.track_planner.inventory import PhysicalStock
from slot_racing.modules.track_planner.library import CatalogReconcileReport
from slot_racing.modules.track_planner.packages import PackageContentView, PackageView
from slot_racing.modules.track_planner.parts import PartRecord
from slot_racing.modules.track_planner.service import TrackPlannerService
from slot_racing.modules.track_planner.trace import trace
from slot_racing.modules.track_planner.ui.dialog_lifetime import destroy_dialog, destroy_widget
from slot_racing.modules.track_planner.ui.library_dialog import PartDialog
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
        self._package_heading: QLabel | None = None
        self._parts_heading: QLabel | None = None
        self._opened = False
        self.status = StatusLabel("collection-message")
        self.status.setParent(self)
        self.search = QLineEdit(self)
        self.search.setObjectName("collection-search")
        self.search.setPlaceholderText(translator.translate("planner.stock.search"))
        self.search.textChanged.connect(self._apply_filter)
        self.hide_sets = QCheckBox(translator.translate("planner.stock.hide_sets"), self)
        self.hide_sets.setObjectName("collection-hide-sets")
        self.hide_sets.toggled.connect(self._apply_filter)
        self._list = QWidget(self)
        self._list.setObjectName("collection-list")
        self._rows_layout = QVBoxLayout(self._list)
        self._rows_layout.setContentsMargins(0, 0, 0, 0)
        self._fill()
        scroll = QScrollArea(self)
        scroll.setObjectName("collection-scroll")
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        scroll.setWidget(self._list)
        reset = QPushButton(translator.translate("planner.stock.reset"), self)
        reset.setObjectName("collection-reset")
        reset.clicked.connect(self._reset_library)
        close = QPushButton(translator.translate("planner.stock.close"), self)
        close.setObjectName("collection-close")
        close.clicked.connect(self.accept)
        actions = QHBoxLayout()
        actions.addWidget(reset)
        actions.addStretch(1)
        actions.addWidget(close)
        layout = QVBoxLayout(self)
        layout.addWidget(self.search)
        layout.addWidget(self.hide_sets)
        layout.addWidget(scroll, 1)
        layout.addWidget(self.status)
        layout.addLayout(actions)

    def showEvent(self, event: QShowEvent) -> None:  # noqa: N802
        if not self._opened:
            self._opened = True
            trace("COLLECTION_DIALOG_OPEN", result="opened")
        super().showEvent(event)

    def _fill(self) -> None:
        self._clear_rows()
        breakdown = {line.part_id: line for line in self._planner.stock_breakdown().values()}
        records = tuple(
            sorted(self._planner.list_parts(), key=lambda record: record.spec.name.casefold())
        )
        packages = self._planner.list_packages()
        if packages:
            heading = QLabel(self._translator.translate("planner.stock.packages"), self._list)
            heading.setObjectName("collection-packages")
            set_role(heading, "caption")
            self._package_heading = heading
            self._rows_layout.addWidget(heading)
            for package in packages:
                package_row = _PackageRow(
                    self._translator, package, self._store_package, self._list
                )
                self._package_rows.append(package_row)
                self._rows_layout.addWidget(package_row)
        parts_heading = QLabel(self._translator.translate("planner.stock.parts"), self._list)
        parts_heading.setObjectName("collection-parts")
        set_role(parts_heading, "caption")
        self._parts_heading = parts_heading
        self._rows_layout.addWidget(parts_heading)
        for record in records:
            line = breakdown.get(record.id)
            adjustment = 0 if line is None else line.adjustment
            part_row = _StockRow(
                self._translator,
                record,
                adjustment,
                self._usage.get(record.id, 0),
                line,
                self._store_adjustment,
                self._edit_part,
                self._list,
            )
            self._rows.append(part_row)
            self._rows_layout.addWidget(part_row)
        self._rows_layout.addStretch(1)
        self._apply_filter()

    def _clear_rows(self) -> None:
        while self._rows_layout.count():
            item = self._rows_layout.takeAt(0)
            widget = None if item is None else item.widget()
            if widget is not None:
                destroy_widget(widget)
        self._rows = []
        self._package_rows = []
        self._package_heading = None
        self._parts_heading = None

    def _apply_filter(self) -> None:
        query = self.search.text()
        hide_sets = self.hide_sets.isChecked()
        packages_visible = 0
        for row in self._package_rows:
            visible = not hide_sets and collection_query_matches(
                query, name=row.entry_name, article=row.entry_article, category_label=""
            )
            row.setVisible(visible)
            packages_visible += int(visible)
        if self._package_heading is not None:
            self._package_heading.setVisible(packages_visible > 0)
        parts_visible = 0
        for part_row in self._rows:
            visible = collection_query_matches(
                query,
                name=part_row.entry_name,
                article=part_row.entry_article,
                category_label=part_row.category_label,
            )
            part_row.setVisible(visible)
            parts_visible += int(visible)
        if self._parts_heading is not None:
            self._parts_heading.setVisible(parts_visible > 0)

    def _reset_library(self) -> None:
        report = self._planner.preview_library_reset()
        if not self._confirm_reset(report):
            return
        self._planner.reset_library()
        self._fill()
        self.status.show_info(self._translator.translate("planner.stock.reset.done"))

    def _confirm_reset(self, report: CatalogReconcileReport) -> bool:
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Icon.Question)
        box.setObjectName("collection-reset-dialog")
        box.setWindowTitle(self._translator.translate("planner.stock.reset.title"))
        box.setText(_reset_message(self._translator, report))
        accept = box.addButton(
            self._translator.translate("planner.stock.reset.accept"),
            QMessageBox.ButtonRole.AcceptRole,
        )
        cancel = box.addButton(
            self._translator.translate("planner.stock.reset.cancel"),
            QMessageBox.ButtonRole.RejectRole,
        )
        accept.setObjectName("collection-reset-accept")
        cancel.setObjectName("collection-reset-cancel")
        box.setDefaultButton(cancel)
        box.exec()
        return box.clickedButton() is accept

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

    def _edit_part(self, part_id: int) -> None:
        record = next((item for item in self._planner.list_parts() if item.id == part_id), None)
        if record is None:
            return
        dialog = PartDialog(self._translator, self._planner, record, parent=self)
        saved = None
        try:
            accepted = bool(dialog.exec())
            saved = dialog.saved
        finally:
            destroy_dialog(dialog)
        if not accepted or saved is None:
            return
        for row in self._rows:
            if row.part_id == part_id:
                row.show_saved(saved)
        self._apply_filter()
        self.status.show_info(self._translator.translate("planner.library.updated"))

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
        self.entry_kind = "package"
        self.entry_name = package.name
        self.entry_article = package.article_number
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
        edit: Callable[[int], None],
        parent: QWidget,
    ) -> None:
        super().__init__(parent)
        self.part_id = record.id
        self.entry_kind = "part"
        self.entry_name = record.spec.name
        self.entry_article = record.spec.article_number
        self.category_label = translator.translate(f"planner.category.{record.spec.category}")
        self._store = store
        self._edit = edit
        self._quantity = adjustment
        self._filling = False
        self._translator = translator
        self.setObjectName(f"collection-row-{record.id}")
        self.name = QLabel(record.spec.name, self)
        self.name.setObjectName("collection-name")
        self.name.setWordWrap(True)
        self.article = QLabel(record.spec.article_number, self)
        self.article.setObjectName("collection-article")
        header = QHBoxLayout()
        header.setContentsMargins(0, 0, 0, 0)
        header.addWidget(self.name, 1)
        header.addWidget(self.article)
        self.edit = QPushButton(translator.translate("planner.stock.edit"), self)
        self.edit.setObjectName(f"collection-edit-{record.id}")
        set_role(self.edit, "ghost")
        self.edit.clicked.connect(self._request_edit)
        header.addWidget(self.edit)
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

    def show_saved(self, record: PartRecord) -> None:
        self.part_id = record.id
        self.entry_name = record.spec.name
        self.entry_article = record.spec.article_number
        self.category_label = self._translator.translate(f"planner.category.{record.spec.category}")
        self.name.setText(record.spec.name)
        self.article.setText(record.spec.article_number)

    def _request_edit(self) -> None:
        self._edit(self.part_id)

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


def collection_query_matches(query: str, *, name: str, article: str, category_label: str) -> bool:
    """Case-insensitive match on designation, article and the readable category.

    ``Geraden`` matches the category ``Gerade``, and ``Kurven`` matches ``Kurve``,
    including a steep curve whose designation does not contain that word.
    """
    needle = query.strip().casefold()
    if not needle:
        return True
    if needle in name.casefold() or needle in article.casefold():
        return True
    label = category_label.strip().casefold()
    if not label:
        return False
    if needle in label:
        return True
    return needle.startswith(label) and needle[len(label) :] in {"n", "en", "e", "s"}


def _reset_message(translator: Translator, report: CatalogReconcileReport) -> str:
    if report.is_empty():
        return translator.translate("planner.stock.reset.none")
    lines = [translator.translate("planner.stock.reset.intro")]
    groups = (
        ("planner.stock.reset.added", report.added),
        ("planner.stock.reset.updated", report.updated),
        ("planner.stock.reset.removed", report.removed),
        ("planner.stock.reset.retired", report.retired),
    )
    for key, entries in groups:
        if not entries:
            continue
        lines.append(translator.format(key, count=len(entries)))
        for entry in entries:
            stock = "" if entry.stock is None else f" ({entry.stock})"
            lines.append(f"• {entry.article_number} {entry.name}{stock}")
    lines.append(translator.translate("planner.stock.reset.keep"))
    return "\n".join(lines)


def _pieces(contents: tuple[PackageContentView, ...], *, multiplier: int) -> str:
    return "\n".join(f"{multiplier * line.quantity} \u00d7 {line.part_name}" for line in contents)


def _signed_digits(text: str) -> bool:
    body = text[1:] if text.startswith("-") else text
    return bool(body) and body.isdigit()
