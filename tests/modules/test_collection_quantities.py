"""Zero every personal quantity without touching the library or saved tracks."""

from __future__ import annotations

import pytest
from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication, QLineEdit, QMessageBox, QPushButton, QWidget
from pytestqt.qtbot import QtBot
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from slot_racing.core.storage.base import Base
from slot_racing.modules.track_planner.document import empty_plan, place_instance
from slot_racing.modules.track_planner.models import (
    TrackPartDefinition,
    TrackPartPackage,
    TrackPartPackageContent,
    TrackPartPackageStock,
    TrackPartStock,
    TrackPlanInstance,
)
from slot_racing.modules.track_planner.packages import PackageContentSpec, PackageSpec
from slot_racing.modules.track_planner.parts import CURVE, build_part
from slot_racing.modules.track_planner.ui.collection_dialog import CollectionDialog
from tests.modules.conftest import Env
from tests.modules.test_library_reset import _mark_supplied
from tests.modules.test_part_inventory import _excess_ids, _open, _place, _reload, _remaining
from tests.modules.test_track_parts import _planner, _straight_spec

_CONFIRM = "Alle Bestandsmengen deiner Bauteilesammlung auf 0 zurücksetzen?"


def test_quantity_reset_clears_visible_hidden_and_retired_stock(env: Env) -> None:
    planner = _planner(env)
    straight = next(
        record
        for record in planner.list_parts()
        if record.spec.article_number == "20509" and not record.spec.piece_key
    )
    package = next(item for item in planner.list_packages() if item.article_number == "20509")
    custom = planner.add_part(_straight_spec("EB-EIGEN", lanes=2))
    owned = planner.add_package(
        PackageSpec(
            "Werkstatt",
            "BOX-Q",
            "Eigene Kiste",
            (PackageContentSpec(custom.spec.name, custom.spec.article_number, 2),),
        )
    )
    retired = planner.add_part(_straight_spec("ALT-BESTAND", lanes=2))
    planner.set_stock(straight.id, 7)
    planner.set_stock(custom.id, 5)
    planner.set_stock(retired.id, 4)
    planner.set_package_stock(package.id, 2)
    planner.set_package_stock(owned.id, 3)
    with env.runtime.database.session() as session:
        negative = session.get(TrackPartStock, custom.id)
        assert negative is not None
        negative.quantity = -2
        hidden_box = session.get(TrackPartPackage, owned.id)
        assert hidden_box is not None
        hidden_box.suppressed = True
    _mark_supplied(env, retired.id, source_id="retired-stock", catalog_key="retired-stock")
    planner.library.ensure_seed()
    track = env.track("Bleibt", lanes=2)
    plan = place_instance(
        empty_plan(track.id), custom.id, custom.spec, 12, 8, 0, {custom.id: custom.spec}
    )
    planner.save(plan)
    with env.runtime.database.session() as session:
        renamed = session.get(TrackPartDefinition, straight.id)
        assert renamed is not None
        renamed.name = "Umbenannt"
    planner.reset_library()
    assert planner.library.require(straight.id).spec.name != "Umbenannt"
    assert planner.stock_breakdown()[straight.id].adjustment == 7
    assert next(item.quantity for item in planner.list_packages() if item.id == package.id) == 2
    definitions = _definitions(env)
    contents = _contents(env)
    instances = _instances(env)
    packages = _packages(env)
    counts = _counts(env)
    changed = planner.reset_collection_quantities()
    assert changed >= 4
    assert planner.reset_collection_quantities() == 0
    assert _definitions(env) == definitions
    assert _contents(env) == contents
    assert _instances(env) == instances
    assert _packages(env) == packages
    assert _counts(env) == counts
    with env.runtime.database.session() as session:
        for part_id in (straight.id, custom.id, retired.id):
            stock = session.get(TrackPartStock, part_id)
            assert stock is not None and stock.quantity == 0
        for package_id in (package.id, owned.id):
            boxes = session.get(TrackPartPackageStock, package_id)
            assert boxes is not None and boxes.quantity == 0
        retired_row = session.get(TrackPartDefinition, retired.id)
        assert retired_row is not None and retired_row.legacy and not retired_row.selectable
    assert planner.library.require(straight.id).spec.name != "Umbenannt"
    assert planner.load(track.id).instances[0].part_id == custom.id
    assert all(record.id != retired.id for record in planner.list_parts())
    assert all(item.id != owned.id for item in planner.list_packages())


def test_a_failed_quantity_reset_writes_nothing(env: Env, monkeypatch: pytest.MonkeyPatch) -> None:
    planner = _planner(env)
    part = planner.add_part(_straight_spec("EB-ATOM", lanes=2))
    planner.set_stock(part.id, 6)
    package = next(item for item in planner.list_packages())
    planner.set_package_stock(package.id, 2)
    real = Session.execute

    def execute(self: Session, statement: object, *args: object, **kwargs: object) -> object:
        compiled = str(statement)
        if "track_part_package_stock" in compiled and compiled.upper().startswith("UPDATE"):
            raise RuntimeError("stop")
        return real(self, statement, *args, **kwargs)  # type: ignore[call-overload]

    monkeypatch.setattr(Session, "execute", execute)
    with pytest.raises(RuntimeError, match="stop"):
        planner.reset_collection_quantities()
    with env.runtime.database.session() as session:
        stock = session.get(TrackPartStock, part.id)
        assert stock is not None and stock.quantity == 6
        boxes = session.get(TrackPartPackageStock, package.id)
        assert boxes is not None and boxes.quantity == 2


def test_filters_do_not_limit_the_reset_and_cancel_keeps_quantities(qtbot: QtBot, env: Env) -> None:
    planner = _planner(env)
    curve = planner.add_part(
        build_part(
            article_number="EB-CURVE",
            scale="1:32",
            name="Bogen",
            category=CURVE,
            length_mm=None,
            width_mm=None,
            height_mm=None,
            radius_mm=400,
            angle_deg=30,
            lane_count=2,
        )
    )
    straight = planner.add_part(_straight_spec("EB-STR", lanes=2))
    retired = planner.add_part(_straight_spec("ALT-DIALOG", lanes=2))
    package = next(item for item in planner.list_packages() if item.article_number == "20509")
    planner.set_stock(curve.id, 6)
    planner.set_stock(straight.id, 5)
    planner.set_stock(retired.id, 4)
    planner.set_package_stock(package.id, 3)
    _mark_supplied(env, retired.id, source_id="retired-dialog", catalog_key="retired-dialog")
    planner.library.ensure_seed()
    dialog = CollectionDialog(env.runtime.translator, planner)
    qtbot.addWidget(dialog)
    dialog.show()
    dialog.search.setText("Kurven")
    dialog.hide_sets.setChecked(True)
    assert _visible(dialog, curve.id)
    assert not _visible(dialog, straight.id)
    assert not _package_visible(dialog, package.id)
    assert dialog.findChild(QWidget, f"collection-row-{retired.id}") is None
    curve_field = _field(dialog, f"collection-quantity-{curve.id}")
    curve_field.setText("11")
    straight_field = _field(dialog, f"collection-quantity-{straight.id}")
    package_field = _field(dialog, f"collection-package-quantity-{package.id}")

    _click_reset(qtbot, dialog, accept=False)
    assert curve_field.text() == "11"
    assert dialog.quantity(curve.id) == 6
    assert planner.stock_breakdown()[curve.id].adjustment == 6
    assert planner.stock_breakdown()[straight.id].adjustment == 5
    assert next(item.quantity for item in planner.list_packages() if item.id == package.id) == 3
    with env.runtime.database.session() as session:
        retired_stock = session.get(TrackPartStock, retired.id)
        assert retired_stock is not None and retired_stock.quantity == 4

    calls: list[int] = []
    original = planner.reset_collection_quantities

    def counted() -> int:
        changed = original()
        calls.append(changed)
        return changed

    planner.reset_collection_quantities = counted  # type: ignore[method-assign]
    _click_reset(qtbot, dialog, accept=True)
    assert calls == [calls[0]] and calls[0] >= 3
    assert curve_field.text() == "0"
    assert straight_field.text() == "0"
    assert package_field.text() == "0"
    assert dialog.quantity(curve.id) == 0
    assert dialog.quantity(straight.id) == 0
    assert dialog.package_quantity(package.id) == 0
    curve_field.editingFinished.emit()
    straight_field.editingFinished.emit()
    package_field.editingFinished.emit()
    assert curve_field.text() == "0"
    assert planner.stock_breakdown()[curve.id].adjustment == 0
    assert planner.stock_breakdown()[straight.id].adjustment == 0
    assert next(item.quantity for item in planner.list_packages() if item.id == package.id) == 0
    with env.runtime.database.session() as session:
        retired_stock = session.get(TrackPartStock, retired.id)
        assert retired_stock is not None and retired_stock.quantity == 0
        definition = session.get(TrackPartDefinition, curve.id)
        assert definition is not None and definition.name == "Bogen"
    assert "0 gesetzt" in dialog.status.text()

    again = CollectionDialog(env.runtime.translator, planner)
    qtbot.addWidget(again)
    again.show()
    assert again.quantity(curve.id) == 0
    assert again.quantity(straight.id) == 0
    assert again.package_quantity(package.id) == 0
    _click_reset(qtbot, again, accept=True)
    assert calls == [calls[0], 0]
    assert planner.stock_breakdown()[curve.id].adjustment == 0


def test_resetting_quantities_refreshes_library_notes_and_the_plan(qtbot: QtBot, env: Env) -> None:
    page = _open(qtbot, env, "Anzeige")
    page.announce_stock_problem = lambda: None
    planner = page._planner
    custom = planner.add_part(_straight_spec("EB-NOTE", lanes=2))
    planner.set_stock(custom.id, 3)
    _reload(page)
    page.mode_collection.click()
    page.show_all_parts.click()
    assert _remaining(page, "EB-NOTE") == "Noch 3"
    placed = _place(page, "EB-NOTE", x=0)
    assert _remaining(page, "EB-NOTE") == "Noch 2"
    assert not page.stock_warning.isVisible()

    def reset(dialog: CollectionDialog) -> int:
        dialog.search.setText("nichts-davon")
        dialog.hide_sets.setChecked(True)
        field = _field(dialog, f"collection-quantity-{custom.id}")
        field.setText("9")
        _click_reset(qtbot, dialog, accept=True)
        assert field.text() == "0"
        field.editingFinished.emit()
        assert dialog.quantity(custom.id) == 0
        dialog.accept()
        return int(dialog.result())

    page.stock_dialog_runner = reset
    page.manage_stock.click()
    assert planner.stock_breakdown()[custom.id].adjustment == 0
    assert _remaining(page, "EB-NOTE") == "Fehlt 1"
    assert page.stock_warning.isVisible()
    assert placed.id in _excess_ids(page)
    assert page.plan().instances[0].part_id == custom.id


def _click_reset(qtbot: QtBot, dialog: CollectionDialog, *, accept: bool) -> None:
    def answer() -> None:
        box = QApplication.activeModalWidget()
        assert isinstance(box, QMessageBox)
        assert box.text() == _CONFIRM
        assert box.defaultButton() is not None
        assert box.defaultButton().objectName() == "collection-reset-stock-cancel"
        name = "collection-reset-stock-accept" if accept else "collection-reset-stock-cancel"
        button = box.findChild(QPushButton, name)
        assert isinstance(button, QPushButton)
        button.click()

    QTimer.singleShot(0, answer)
    button = dialog.findChild(QPushButton, "collection-reset-stock")
    assert isinstance(button, QPushButton)
    assert button.text() == "Meine Bauteilesammlung zurücksetzen"
    button.click()
    qtbot.waitUntil(lambda: QApplication.activeModalWidget() is None)


def _field(dialog: CollectionDialog, object_name: str) -> QLineEdit:
    field = dialog.findChild(QLineEdit, object_name)
    assert isinstance(field, QLineEdit)
    return field


def _visible(dialog: CollectionDialog, part_id: int) -> bool:
    row = dialog.findChild(QWidget, f"collection-row-{part_id}")
    assert isinstance(row, QWidget)
    return row.isVisible()


def _package_visible(dialog: CollectionDialog, package_id: int) -> bool:
    row = dialog.findChild(QWidget, f"collection-package-row-{package_id}")
    assert isinstance(row, QWidget)
    return row.isVisible()


def _definitions(env: Env) -> tuple[tuple[object, ...], ...]:
    with env.runtime.database.session() as session:
        rows = session.scalars(select(TrackPartDefinition).order_by(TrackPartDefinition.id))
        return tuple(
            (
                row.id,
                row.name,
                row.article_number,
                row.piece_key,
                row.origin,
                row.legacy,
                row.selectable,
                row.source_id,
                row.catalog_key,
                row.suppressed,
            )
            for row in rows
        )


def _contents(env: Env) -> tuple[tuple[int, int, int], ...]:
    with env.runtime.database.session() as session:
        rows = session.scalars(select(TrackPartPackageContent).order_by(TrackPartPackageContent.id))
        return tuple((row.package_id, row.part_id, row.quantity) for row in rows)


def _instances(env: Env) -> tuple[tuple[str, int, int, float, float], ...]:
    with env.runtime.database.session() as session:
        rows = session.scalars(select(TrackPlanInstance).order_by(TrackPlanInstance.id))
        return tuple((row.id, row.track_id, row.part_id, row.x_mm, row.y_mm) for row in rows)


def _packages(env: Env) -> tuple[tuple[int, str, str, bool, bool], ...]:
    with env.runtime.database.session() as session:
        rows = session.scalars(select(TrackPartPackage).order_by(TrackPartPackage.id))
        return tuple(
            (row.id, row.name, row.article_number, row.suppressed, row.bundled) for row in rows
        )


def _counts(env: Env) -> dict[str, int]:
    with env.runtime.database.session() as session:
        return {
            name: int(session.scalar(select(func.count()).select_from(table)) or 0)
            for name, table in Base.metadata.tables.items()
        }
