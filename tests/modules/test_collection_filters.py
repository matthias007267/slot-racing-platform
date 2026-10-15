"""Search and the set filter in the collection leave stored quantities alone."""

from __future__ import annotations

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication, QLabel, QMessageBox, QPushButton, QWidget
from pytestqt.qtbot import QtBot

from slot_racing.modules.track_planner.models import TrackPartDefinition, TrackPartStock
from slot_racing.modules.track_planner.parts import CURVE, STRAIGHT, build_part
from slot_racing.modules.track_planner.ui.collection_dialog import (
    CollectionDialog,
    collection_query_matches,
)
from tests.modules.conftest import Env
from tests.modules.test_track_parts import _planner, _straight_spec


def test_category_plurals_match_without_the_word_in_the_name() -> None:
    assert collection_query_matches("Kurven", name="Bogen", article="EB-1", category_label="Kurve")
    assert collection_query_matches(
        "kurve", name="Steilwand", article="EB-2", category_label="Kurve"
    )
    assert collection_query_matches(
        "Geraden", name="Schiene", article="EB-3", category_label="Gerade"
    )
    assert collection_query_matches(
        "  gErAdE ", name="Schiene", article="EB-3", category_label="Gerade"
    )
    assert collection_query_matches("20571", name="Bogen", article="20571", category_label="Kurve")
    assert not collection_query_matches(
        "Kurven", name="Schiene", article="EB-3", category_label="Gerade"
    )
    assert collection_query_matches("  ", name="Schiene", article="EB-3", category_label="Gerade")


def test_search_and_hidden_sets_keep_every_stock_row(qtbot: QtBot, env: Env) -> None:
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
    with env.runtime.database.session() as session:
        row = session.get(TrackPartDefinition, straight.id)
        assert row is not None
        row.name = "Schiene"
        row.category = STRAIGHT
    steep = next(record for record in planner.list_parts() if record.spec.article_number == "20577")
    standard = next(
        record
        for record in planner.list_parts()
        if record.spec.article_number == "20509" and not record.spec.piece_key
    )
    package = next(item for item in planner.list_packages() if item.article_number == "20509")
    planner.set_stock(curve.id, 6)
    planner.set_stock(straight.id, 5)
    planner.set_stock(steep.id, 1)
    planner.set_package_stock(package.id, 4)
    dialog = CollectionDialog(env.runtime.translator, planner)
    qtbot.addWidget(dialog)
    dialog.show()

    dialog.search.setText("Kurven")
    assert _visible(dialog, curve.id)
    assert _visible(dialog, steep.id)
    assert not _visible(dialog, straight.id)
    dialog.search.setText("Geraden")
    assert _visible(dialog, straight.id)
    assert _visible(dialog, standard.id)
    assert not _visible(dialog, curve.id)
    assert not _visible(dialog, steep.id)
    dialog.search.setText("eb-curve")
    assert _visible(dialog, curve.id)
    assert not _visible(dialog, straight.id)
    dialog.search.setText("20571")
    assert any(
        _visible(dialog, record.id)
        for record in planner.list_parts()
        if record.spec.article_number == "20571"
    )
    dialog.search.setText("")
    assert _visible(dialog, curve.id)
    assert _visible(dialog, straight.id)

    dialog.hide_sets.setChecked(True)
    dialog.search.setText("20509")
    assert not _package_visible(dialog, package.id)
    assert _visible(dialog, standard.id)
    dialog.search.setText("Geraden")
    assert not _visible(dialog, curve.id)
    plus = dialog.findChild(QPushButton, f"collection-plus-{straight.id}")
    assert isinstance(plus, QPushButton)
    plus.click()
    assert dialog.quantity(curve.id) == 6
    assert dialog.quantity(straight.id) == 6
    assert planner.stock_breakdown()[curve.id].adjustment == 6
    assert planner.stock_breakdown()[straight.id].adjustment == 6
    assert next(item.quantity for item in planner.list_packages() if item.id == package.id) == 4
    dialog.search.setText("")
    dialog.hide_sets.setChecked(False)
    assert _visible(dialog, curve.id)
    assert _package_visible(dialog, package.id)
    assert dialog.package_quantity(package.id) == 4
    with env.runtime.database.session() as session:
        hidden = session.get(TrackPartStock, curve.id)
        assert hidden is not None and hidden.quantity == 6


def test_reset_confirmation_names_the_obsolete_part_and_refresh_hides_it(
    qtbot: QtBot, env: Env
) -> None:
    planner = _planner(env)
    planner.list_parts()
    dialog = CollectionDialog(env.runtime.translator, planner)
    qtbot.addWidget(dialog)
    dialog.show()
    obsolete = planner.add_part(_straight_spec("ALT-UI", lanes=2))
    with env.runtime.database.session() as session:
        row = session.get(TrackPartDefinition, obsolete.id)
        assert row is not None
        row.origin = "catalog"
        row.catalog_key = "archived-ui"
        row.source_id = ""
    seen: list[str] = []

    def accept() -> None:
        box = QApplication.activeModalWidget()
        assert isinstance(box, QMessageBox)
        seen.append(box.text())
        button = box.findChild(QPushButton, "collection-reset-accept")
        assert isinstance(button, QPushButton)
        button.click()

    QTimer.singleShot(0, accept)
    reset = dialog.findChild(QPushButton, "collection-reset")
    assert isinstance(reset, QPushButton)
    reset.click()
    assert seen and "ALT-UI" in seen[0]
    assert "Entfernt" in seen[0]
    assert dialog.findChild(QWidget, f"collection-row-{obsolete.id}") is None
    assert all(record.spec.article_number != "ALT-UI" for record in planner.list_parts())
    heading = dialog.findChild(QLabel, "collection-parts")
    assert isinstance(heading, QLabel)
    assert heading.isVisible()


def test_cancelling_the_reset_keeps_the_obsolete_part(qtbot: QtBot, env: Env) -> None:
    planner = _planner(env)
    planner.list_parts()
    dialog = CollectionDialog(env.runtime.translator, planner)
    qtbot.addWidget(dialog)
    obsolete = planner.add_part(_straight_spec("ALT-NEIN", lanes=2))
    with env.runtime.database.session() as session:
        row = session.get(TrackPartDefinition, obsolete.id)
        assert row is not None
        row.origin = "catalog"
        row.catalog_key = "archived-no"
        row.source_id = ""

    def cancel() -> None:
        box = QApplication.activeModalWidget()
        assert isinstance(box, QMessageBox)
        button = box.findChild(QPushButton, "collection-reset-cancel")
        assert isinstance(button, QPushButton)
        button.click()

    QTimer.singleShot(0, cancel)
    reset = dialog.findChild(QPushButton, "collection-reset")
    assert isinstance(reset, QPushButton)
    reset.click()
    with env.runtime.database.session() as session:
        row = session.get(TrackPartDefinition, obsolete.id)
        assert row is not None and row.legacy is False and row.selectable is True


def _visible(dialog: CollectionDialog, part_id: int) -> bool:
    row = dialog.findChild(QWidget, f"collection-row-{part_id}")
    assert isinstance(row, QWidget)
    return row.isVisible()


def _package_visible(dialog: CollectionDialog, package_id: int) -> bool:
    row = dialog.findChild(QWidget, f"collection-package-row-{package_id}")
    assert isinstance(row, QWidget)
    return row.isVisible()
