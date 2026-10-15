"""The collection counts parts and packages, not placement templates or hidden pieces."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QLabel, QPushButton, QWidget
from pytestqt.qtbot import QtBot

from slot_racing.modules.track_planner.catalog_import import (
    HELPER_SOURCE,
    load_carrera_library,
    source_ids_hidden_from_collection,
)
from slot_racing.modules.track_planner.document import empty_plan, place_template
from slot_racing.modules.track_planner.models import TrackPartDefinition, TrackPartStock
from slot_racing.modules.track_planner.parts import CURVE, build_part
from slot_racing.modules.track_planner.ui.collection_dialog import CollectionDialog
from slot_racing.modules.track_planner.ui.page import PlannerPage
from tests.modules.conftest import Env
from tests.modules.test_track_parts import _planner, _select
from tests.modules.test_ui_management import open_page


def test_unlisted_bodies_and_template_shells_are_not_stock_rows() -> None:
    draft = load_carrera_library()
    hidden = source_ids_hidden_from_collection()
    unlisted = {spec.source_id for spec in draft.parts if not spec.catalog_listed}
    shells = {template.source_id for template in draft.templates}
    assert unlisted and shells
    assert unlisted <= hidden
    assert shells <= hidden
    assert HELPER_SOURCE in unlisted
    assert all(spec.catalog_listed or spec.source_id in hidden for spec in draft.parts)
    listed = [spec for spec in draft.parts if spec.catalog_listed and spec.selectable]
    assert listed
    assert all(spec.source_id not in hidden for spec in listed)


def test_the_collection_hides_template_pieces_and_keeps_parts_packages_and_stock(
    qtbot: QtBot, env: Env
) -> None:
    planner = _planner(env)
    hidden = source_ids_hidden_from_collection()
    parts = planner.list_parts()
    templates = planner.list_templates()
    assert len(templates) == 10
    half = next(record for record in parts if record.spec.piece_key == "30343.1")
    assert half.spec.source_id in hidden
    assert all(record.spec.piece_key != "20509.3" for record in parts)
    straight = next(
        record
        for record in parts
        if record.spec.article_number == "20509" and not record.spec.piece_key
    )
    assert straight.spec.source_id not in hidden
    own = planner.add_part(
        build_part(
            article_number="EB-NAME",
            scale="1:32",
            name="Engstelle links",
            category=CURVE,
            length_mm=None,
            width_mm=None,
            height_mm=None,
            radius_mm=400,
            angle_deg=30,
            lane_count=2,
        )
    )
    package = next(item for item in planner.list_packages() if item.article_number == "30343")
    planner.set_stock(half.id, 4)
    planner.set_stock(straight.id, 2)
    planner.set_stock(own.id, 1)
    planner.set_package_stock(package.id, 3)
    before_parts = {
        record.id: (record.spec.name, record.spec.source_id, record.origin)
        for record in planner.list_parts()
    }
    dialog = CollectionDialog(env.runtime.translator, planner)
    qtbot.addWidget(dialog)
    dialog.show()

    assert dialog.findChild(QWidget, f"collection-row-{half.id}") is None
    assert dialog.findChild(QWidget, f"collection-row-{straight.id}") is not None
    assert dialog.findChild(QWidget, f"collection-row-{own.id}") is not None
    assert _package_visible(dialog, package.id)
    assert dialog.quantity(straight.id) == 2
    assert dialog.quantity(own.id) == 1
    assert dialog.package_quantity(package.id) == 3
    assert not _row_text(dialog, "Weiche links")
    assert not _row_text(dialog, "Standardgerade 34,5cm (ohne Mittelstreifen)")

    dialog.search.setText("Weiche links 1/2")
    assert dialog.findChild(QWidget, f"collection-row-{half.id}") is None
    assert _package_visible(dialog, package.id)
    dialog.hide_sets.setChecked(True)
    assert not _package_visible(dialog, package.id)
    assert dialog.findChild(QWidget, f"collection-row-{half.id}") is None
    dialog.search.setText("Geraden")
    assert _visible(dialog, straight.id)
    assert not _visible(dialog, own.id)
    dialog.search.setText("Engstelle links")
    assert _visible(dialog, own.id)
    assert dialog.findChild(QWidget, f"collection-row-{half.id}") is None
    dialog.search.setText("")
    dialog.hide_sets.setChecked(False)
    plus = dialog.findChild(QPushButton, f"collection-plus-{straight.id}")
    assert isinstance(plus, QPushButton)
    plus.click()
    assert dialog.quantity(straight.id) == 3

    again = CollectionDialog(env.runtime.translator, planner)
    qtbot.addWidget(again)
    again.show()
    assert again.findChild(QWidget, f"collection-row-{half.id}") is None
    assert again.quantity(straight.id) == 3
    assert again.package_quantity(package.id) == 3
    assert again.quantity(own.id) == 1
    with env.runtime.database.session() as session:
        stock = session.get(TrackPartStock, half.id)
        assert stock is not None and stock.quantity == 4
        kept = session.get(TrackPartDefinition, half.id)
        assert kept is not None and kept.source_id == half.spec.source_id
    assert {
        record.id: (record.spec.name, record.spec.source_id, record.origin)
        for record in planner.list_parts()
    } == before_parts
    assert len(planner.list_templates()) == 10


def test_templates_stay_usable_outside_the_collection(qtbot: QtBot, env: Env) -> None:
    planner = _planner(env)
    template = next(item for item in planner.list_templates() if item.article_number == "30343")
    assert len(template.members) == 2
    track = env.track("Vorlage", lanes=2)
    placed = place_template(
        empty_plan(track.id),
        tuple(
            (member.part_id, member.x_mm, member.y_mm, member.angle_deg)
            for member in template.members
        ),
        10,
        20,
        0,
    )
    planner.save(placed)
    loaded = planner.load(track.id)
    assert {instance.part_id for instance in loaded.instances} == {
        member.part_id for member in template.members
    }

    window, page = open_page(qtbot, env, "track_planner")
    window.show()
    assert isinstance(page, PlannerPage)
    page.announce_stock_problem = lambda: None
    _select(page, track.id)
    assert _vorlage_rows(page)
    page.mode_collection.click()
    page.show_all_parts.click()
    assert _vorlage_rows(page) == []
    assert _library_article(page, "20509")
    page.mode_unlimited.click()
    rows = _vorlage_rows(page)
    assert rows
    page.library.setCurrentRow(rows[0])
    before = len(page.plan().instances)
    page._place_part()
    assert len(page.plan().instances) > before


def _visible(dialog: CollectionDialog, part_id: int) -> bool:
    row = dialog.findChild(QWidget, f"collection-row-{part_id}")
    assert isinstance(row, QWidget)
    return row.isVisible()


def _package_visible(dialog: CollectionDialog, package_id: int) -> bool:
    row = dialog.findChild(QWidget, f"collection-package-row-{package_id}")
    assert isinstance(row, QWidget)
    return row.isVisible()


def _row_text(dialog: CollectionDialog, text: str) -> bool:
    return any(label.text() == text and label.isVisible() for label in dialog.findChildren(QLabel))


def _vorlage_rows(page: PlannerPage) -> list[int]:
    rows: list[int] = []
    for index in range(page.library.count()):
        item = page.library.item(index)
        if item is None:
            continue
        value = item.data(Qt.ItemDataRole.UserRole)
        if isinstance(value, int) and value < 0:
            rows.append(index)
    return rows


def _library_article(page: PlannerPage, article: str) -> bool:
    for index in range(page.library.count()):
        item = page.library.item(index)
        card = None if item is None else page.library.itemWidget(item)
        label = None if card is None else card.findChild(QLabel, "planner-part-article")
        if label is not None and label.text() == article:
            return True
    return False
