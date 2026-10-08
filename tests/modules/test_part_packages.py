"""Retail packages expand into physical pieces. Existing counts stay physical."""

from __future__ import annotations

from pathlib import Path

import pytest
from PySide6.QtWidgets import QLabel, QLineEdit, QScrollArea
from pytestqt.qtbot import QtBot
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError

from slot_racing.app.runtime import Runtime
from slot_racing.core.backup import create_backup, restore_backup
from slot_racing.core.config import AppConfig
from slot_racing.core.diagnostics import current, install
from slot_racing.core.domain import TrackId
from slot_racing.core.errors import ValidationError
from slot_racing.core.storage import Database
from slot_racing.modules.track_planner.document import (
    attach_accessory,
    empty_plan,
    place_instance,
    with_instances,
)
from slot_racing.modules.track_planner.inventory import (
    PackageContribution,
    analyze_inventory,
    derived_quantities,
    physical_quantity,
    physical_stocks,
)
from slot_racing.modules.track_planner.models import (
    TrackPartDefinition,
    TrackPartPackage,
    TrackPartPackageContent,
    TrackPartPackageStock,
    TrackPartStock,
    TrackPlanInstance,
)
from slot_racing.modules.track_planner.packages import (
    PackageContentSpec,
    PackageSpec,
    PackageView,
)
from slot_racing.modules.track_planner.parts import (
    END_PIECE_K3_NAME,
    OUTER_BORDER_K3_ARTICLE,
    OUTER_BORDER_K3_NAME,
    STANDARD_STRAIGHT_ARTICLE,
    STRAIGHT,
    PartInstance,
    PartRecord,
    build_part,
)
from slot_racing.modules.track_planner.service import TrackPlannerService
from slot_racing.modules.track_planner.ui.collection_dialog import CollectionDialog
from slot_racing.modules.tracks.service import TrackInput, TrackService
from tests.modules.conftest import Env
from tests.modules.test_part_inventory import (
    _open,
    _part,
    _place,
    _planner,
    _reload,
    _remaining,
)


def test_one_package_expands_each_content() -> None:
    contents = (
        PackageContribution(1, 10, 6),
        PackageContribution(1, 11, 2),
    )
    assert derived_quantities({1: 0}, contents) == {}
    assert derived_quantities({1: 1}, contents) == {10: 6, 11: 2}
    assert derived_quantities({1: 2}, contents) == {10: 12, 11: 4}


def test_one_part_can_come_from_two_packages() -> None:
    contents = (
        PackageContribution(1, 10, 6),
        PackageContribution(2, 10, 2),
    )
    assert derived_quantities({1: 1, 2: 2}, contents) == {10: 10}


def test_adjustment_adds_to_derived_stock_and_clamps_at_zero() -> None:
    assert physical_quantity(6, 2) == 8
    assert physical_quantity(6, -2) == 4
    assert physical_quantity(1, -5) == 0
    lines = physical_stocks({7: 1}, {7: -5})
    assert lines[7].physical == 0
    assert lines[7].adjustment == -5


def test_seeded_border_package_reuses_the_strip_and_adds_an_end_piece(env: Env) -> None:
    planner = _planner(env)
    packages = {package.article_number: package for package in planner.list_packages()}
    border = packages[OUTER_BORDER_K3_ARTICLE]
    assert border.manufacturer == "Carrera"
    assert border.name == OUTER_BORDER_K3_NAME
    assert border.quantity == 0
    by_name = {line.part_name: line for line in border.contents}
    assert by_name[OUTER_BORDER_K3_NAME].quantity == 6
    assert by_name[END_PIECE_K3_NAME].quantity == 2
    strip = _named(planner, OUTER_BORDER_K3_NAME)
    end = _named(planner, END_PIECE_K3_NAME)
    assert strip.spec.article_number == OUTER_BORDER_K3_ARTICLE
    assert end.spec.article_number == OUTER_BORDER_K3_ARTICLE
    assert strip.spec.attachment is not None
    assert end.spec.attachment is None
    assert strip.id != end.id
    assert by_name[OUTER_BORDER_K3_NAME].part_id == strip.id
    assert by_name[END_PIECE_K3_NAME].part_id == end.id
    straight = packages[STANDARD_STRAIGHT_ARTICLE]
    assert straight.contents[0].quantity == 1
    assert straight.contents[0].part_id == _part(planner, STANDARD_STRAIGHT_ARTICLE)
    formula = Path("src/slot_racing/modules/track_planner/inventory.py").read_text(encoding="utf-8")
    library = Path("src/slot_racing/modules/track_planner/library.py").read_text(encoding="utf-8")
    assert OUTER_BORDER_K3_ARTICLE not in formula
    assert OUTER_BORDER_K3_ARTICLE not in library


def test_package_stock_and_adjustment_make_the_physical_count(env: Env) -> None:
    planner = _planner(env)
    border = _package(planner, OUTER_BORDER_K3_ARTICLE)
    strip = _named(planner, OUTER_BORDER_K3_NAME).id
    end = _named(planner, END_PIECE_K3_NAME).id
    planner.set_package_stock(border.id, 2)
    assert planner.stock_breakdown()[strip].derived == 12
    assert planner.stock_breakdown()[end].derived == 4
    planner.set_stock_adjustment(strip, 2)
    owned = planner.stock_breakdown()[strip]
    assert (owned.derived, owned.adjustment, owned.physical) == (12, 2, 14)
    planner.set_stock_adjustment(strip, -2)
    assert planner.stock_breakdown()[strip].physical == 10
    with pytest.raises(ValidationError) as caught:
        planner.set_stock_adjustment(end, -5)
    assert caught.value.key == "error.planner.stock_adjustment"
    assert planner.stock_breakdown()[end].physical == 4


def test_existing_stock_quantity_stays_a_physical_count(env: Env) -> None:
    planner = _planner(env)
    part = _part(planner, STANDARD_STRAIGHT_ARTICLE)
    planner.set_stock(part, 7)
    box = _package(planner, STANDARD_STRAIGHT_ARTICLE)
    assert box.quantity == 0
    line = planner.stock_breakdown()[part]
    assert (line.derived, line.adjustment, line.physical) == (0, 7, 7)
    assert planner.stock_quantities()[part] == 7


def test_migration_keeps_a_stored_quantity_as_physical_stock() -> None:
    database = Database.in_memory()
    database.migrate("0015")
    with database.engine.begin() as connection:
        connection.exec_driver_sql("INSERT INTO tracks (name, lane_count) VALUES ('Alt', 2)")
        connection.exec_driver_sql(
            "INSERT INTO track_part_definitions "
            "(article_number, scale, name, category, lane_count, outline, suppressed) "
            "VALUES ('20020601', '1:24', 'Standardgerade', 'straight', 2, '[]', 0)"
        )
        part_id = connection.exec_driver_sql("SELECT id FROM track_part_definitions").scalar_one()
        track_id = connection.exec_driver_sql("SELECT id FROM tracks").scalar_one()
        connection.exec_driver_sql(
            f"INSERT INTO track_part_stock (part_id, quantity) VALUES ({int(part_id)}, 7)"
        )
        connection.exec_driver_sql(
            "INSERT INTO track_plan_instances "
            "(id, track_id, part_id, x_mm, y_mm, z_mm, rotation_x_deg, rotation_y_deg, "
            "rotation_z_deg) VALUES "
            f"('legacy-1', {int(track_id)}, {int(part_id)}, 10, 20, 0, 0, 0, 0)"
        )
    database.migrate()
    assert database.schema_revision() == "0017"
    with database.engine.connect() as connection:
        stored = connection.execute(text("SELECT quantity FROM track_part_stock")).scalar_one()
        packages = connection.execute(
            text("SELECT COUNT(*) FROM track_part_package_stock")
        ).scalar_one()
        instance = connection.execute(text("SELECT id FROM track_plan_instances")).scalar_one()
    assert stored == 7
    assert packages == 0
    assert instance == "legacy-1"
    planner = TrackPlannerService(database, TrackService(database))
    part = _part(planner, STANDARD_STRAIGHT_ARTICLE)
    assert planner.stock_quantities()[part] == 7
    assert _package(planner, STANDARD_STRAIGHT_ARTICLE).quantity == 0
    plan = planner.load(TrackId(int(track_id)))
    assert [item.id for item in plan.instances] == ["legacy-1"]
    with database.engine.begin() as connection:
        connection.exec_driver_sql("UPDATE track_part_stock SET quantity = -2")
    assert planner.stock_breakdown()[part].adjustment == -2
    assert planner.stock_quantities()[part] == 0
    database.dispose()


def test_planner_availability_follows_physical_stock(env: Env) -> None:
    planner = _planner(env)
    part = _part(planner, STANDARD_STRAIGHT_ARTICLE)
    box = _package(planner, STANDARD_STRAIGHT_ARTICLE)
    planner.set_package_stock(box.id, 12)
    instances = tuple(_copy(part, index) for index in range(9))
    covered = analyze_inventory(instances, planner.stock_quantities()).balance(part)
    assert (covered.owned, covered.used, covered.available) == (12, 9, 3)
    assert covered.excess_ids == ()
    over = analyze_inventory(
        instances + tuple(_copy(part, index) for index in range(9, 13)),
        planner.stock_quantities(),
    ).balance(part)
    assert (over.owned, over.used, over.available) == (12, 13, -1)
    assert len(over.excess_ids) == 1


def test_attached_parts_use_the_same_physical_stock(env: Env) -> None:
    planner = _planner(env)
    track = env.track("Rand", lanes=2)
    catalog = {record.id: record.spec for record in planner.list_parts()}
    curve = _part(planner, "20020573")
    outer = _named(planner, OUTER_BORDER_K3_NAME).id
    box = _package(planner, OUTER_BORDER_K3_ARTICLE)
    planner.set_package_stock(box.id, 1)
    plan = empty_plan(track.id)
    for index in range(5):
        plan = place_instance(plan, curve, catalog[curve], index * 2000, 0, 0, catalog)
        plan = attach_accessory(
            plan, plan.instances[-1].id, outer, catalog[outer], "outer", catalog
        )
    balance = analyze_inventory(plan.instances, planner.stock_quantities()).balance(outer)
    assert balance.owned == 6
    assert balance.used == 5
    assert balance.available == 1
    planner.save(plan)
    loaded = planner.load(track.id)
    assert sum(item.part_id == outer for item in loaded.instances) == 5


def test_collection_mode_uses_package_derived_stock(qtbot: QtBot, env: Env) -> None:
    page = _open(qtbot, env, "Sammlung")
    planner = _planner(env)
    box = _package(planner, STANDARD_STRAIGHT_ARTICLE)
    planner.set_package_stock(box.id, 4)
    _reload(page)
    page.mode_collection.click()
    label = _remaining(page, STANDARD_STRAIGHT_ARTICLE)
    assert label == "Noch 4"
    page.show_all_parts.click()
    _place(page, STANDARD_STRAIGHT_ARTICLE, x=0)
    assert _remaining(page, STANDARD_STRAIGHT_ARTICLE) == "Noch 3"


def test_reducing_package_stock_overbooks_without_deleting_the_plan(env: Env) -> None:
    planner = _planner(env)
    track = env.track("Abbau", lanes=2)
    part = planner.add_part(
        build_part(
            article_number="BOX-PART",
            scale="1:32",
            name="Lose Schiene",
            category=STRAIGHT,
            length_mm=200,
            width_mm=None,
            height_mm=None,
            radius_mm=None,
            angle_deg=None,
            lane_count=2,
        )
    )
    package = planner.add_package(
        PackageSpec(
            "Werkstatt",
            "BOX-6",
            "Sechserpack",
            (PackageContentSpec("Lose Schiene", "BOX-PART", 6),),
        )
    )
    planner.set_package_stock(package.id, 2)
    instances = tuple(_copy(part.id, index) for index in range(10))
    planner.save(with_instances(empty_plan(track.id), instances))
    balance = analyze_inventory(instances, planner.stock_quantities()).balance(part.id)
    assert (balance.owned, balance.available) == (12, 2)
    planner.set_package_stock(package.id, 1)
    loaded = planner.load(track.id)
    assert [item.id for item in loaded.instances] == [item.id for item in instances]
    over = analyze_inventory(loaded.instances, planner.stock_quantities()).balance(part.id)
    assert (over.owned, over.used, over.available) == (6, 10, -4)
    assert len(over.excess_ids) == 4


def test_backup_restores_packages_adjustments_and_plan_use(tmp_path: Path) -> None:
    folder = tmp_path
    runtime = Runtime.create(
        AppConfig(database_path=folder / "slot_racing.db"),
        config_path=folder / "config.json",
    )
    try:
        planner = runtime.services.get(TrackPlannerService)
        tracks = runtime.services.get(TrackService)
        track = tracks.create_track(TrackInput(name="Backup", lane_count=2))
        part = _part(planner, STANDARD_STRAIGHT_ARTICLE)
        box = _package(planner, STANDARD_STRAIGHT_ARTICLE)
        planner.set_package_stock(box.id, 2)
        planner.set_stock_adjustment(part, 3)
        catalog = {record.id: record.spec for record in planner.list_parts()}
        plan = place_instance(empty_plan(track.id), part, catalog[part], 0, 0, 0, catalog)
        planner.save(plan)
        before = planner.stock_breakdown()[part]
        archive = create_backup(runtime.database, runtime.config, folder / "backups")
        planner.set_package_stock(box.id, 0)
        planner.set_stock_adjustment(part, 0)
        restore_backup(
            archive,
            runtime.database,
            runtime.config,
            folder / "config.json",
            backup_directory=folder / "safety",
        )
        restored = TrackPlannerService(runtime.database, tracks)
        line = restored.stock_breakdown()[part]
        assert (line.derived, line.adjustment, line.physical) == (
            before.derived,
            before.adjustment,
            before.physical,
        )
        assert _package(restored, STANDARD_STRAIGHT_ARTICLE).quantity == 2
        assert [item.part_id for item in restored.load(track.id).instances] == [part]
    finally:
        runtime.shutdown()


def test_package_and_part_references_are_not_destroyed(env: Env) -> None:
    planner = _planner(env)
    part = planner.add_part(
        build_part(
            article_number="KEEP",
            scale="1:32",
            name="Behalten",
            category=STRAIGHT,
            length_mm=200,
            width_mm=None,
            height_mm=None,
            radius_mm=None,
            angle_deg=None,
            lane_count=2,
        )
    )
    package = planner.add_package(
        PackageSpec("Privat", "P-1", "Einzel", (PackageContentSpec("Behalten", "KEEP", 1),))
    )
    planner.set_package_stock(package.id, 2)
    planner.set_stock(part.id, 1)
    track = env.track("Referenz", lanes=2)
    planner.save(with_instances(empty_plan(track.id), (_copy(part.id, 0),)))
    with pytest.raises(ValidationError) as blocked:
        planner.delete_package(package.id)
    assert blocked.value.key == "error.planner.package_in_use"
    with pytest.raises(ValidationError) as part_blocked:
        planner.delete_part(part.id)
    assert part_blocked.value.key == "error.planner.part_in_use"
    database = env.runtime.database
    with pytest.raises(IntegrityError), database.session() as session:
        row = session.get(TrackPartDefinition, part.id)
        assert row is not None
        session.delete(row)
    with database.session() as session:
        assert session.get(TrackPartPackageContent, _content_id(database, package.id)) is not None
        assert session.get(TrackPartPackageStock, package.id) is not None
        assert session.scalar(select(TrackPlanInstance.id)) == "copy-0"
        assert session.get(TrackPartStock, part.id) is not None
    loose = planner.add_part(
        build_part(
            article_number="LOOSE",
            scale="1:32",
            name="Einzelteil",
            category=STRAIGHT,
            length_mm=200,
            width_mm=None,
            height_mm=None,
            radius_mm=None,
            angle_deg=None,
            lane_count=2,
        )
    )
    holding = planner.add_package(
        PackageSpec("Privat", "P-2", "Haelt", (PackageContentSpec("Einzelteil", "LOOSE", 1),))
    )
    with pytest.raises(ValidationError) as held:
        planner.delete_part(loose.id)
    assert held.value.key == "error.planner.part_in_package"
    with database.session() as session:
        assert session.get(TrackPartPackageContent, _content_id(database, holding.id)) is not None
    planner.set_package_stock(package.id, 0)
    empty = planner.add_package(
        PackageSpec("Privat", "P-0", "Leer", (PackageContentSpec("Behalten", "KEEP", 1),))
    )
    planner.delete_package(empty.id)
    with database.session() as session:
        assert session.get(TrackPartPackage, empty.id) is None
        assert session.get(TrackPartDefinition, part.id) is not None
        assert list(session.scalars(select(TrackPlanInstance)))
    bundled = _package(planner, STANDARD_STRAIGHT_ARTICLE)
    planner.delete_package(bundled.id)
    assert all(
        package.article_number != STANDARD_STRAIGHT_ARTICLE for package in planner.list_packages()
    )
    again = planner.list_packages()
    assert all(package.article_number != STANDARD_STRAIGHT_ARTICLE for package in again)


def test_stock_changes_are_breadcrumbs(env: Env, tmp_path: Path) -> None:
    service = install(tmp_path)
    try:
        planner = _planner(env)
        part = _part(planner, STANDARD_STRAIGHT_ARTICLE)
        box = _package(planner, STANDARD_STRAIGHT_ARTICLE)
        planner.set_package_stock(box.id, 2)
        planner.set_package_stock(box.id, 2)
        planner.set_stock_adjustment(part, 1)
        names = [item.event for item in service.breadcrumbs.snapshot()]
        assert names.count("PART_PACKAGE_STOCK_UPDATE") == 1
        assert names.count("PART_STOCK_ADJUSTMENT") == 1
        package_event = next(
            item
            for item in service.breadcrumbs.snapshot()
            if item.event == "PART_PACKAGE_STOCK_UPDATE"
        )
        fields = dict(package_event.fields)
        assert fields["package_id"] == str(box.id)
        assert fields["article"] == STANDARD_STRAIGHT_ARTICLE
        assert fields["quantity"] == "2"
        assert "Standardgerade" not in str(package_event.fields)
    finally:
        running = current()
        if running is not None:
            running.close()


def test_collection_dialog_edits_packages_and_adjustments(qtbot: QtBot, env: Env) -> None:
    page = _open(qtbot, env, "Dialog")
    planner = page._planner
    border = _package(planner, OUTER_BORDER_K3_ARTICLE)
    strip = _named(planner, OUTER_BORDER_K3_NAME).id
    end = _named(planner, END_PIECE_K3_NAME).id
    dialog = CollectionDialog(page._translator, planner, page, {strip: 9})
    qtbot.addWidget(dialog)
    dialog.show()
    dialog.resize(640, 520)
    qtbot.waitUntil(lambda: dialog.isVisible())
    scroll = dialog.findChild(QScrollArea, "collection-scroll")
    assert isinstance(scroll, QScrollArea)
    assert scroll.horizontalScrollBar().maximum() == 0
    _set_line(dialog, f"collection-package-quantity-{border.id}", "2")
    assert dialog.package_quantity(border.id) == 2
    contained = _label(dialog, f"collection-package-contained-{border.id}")
    assert "12 " in contained and OUTER_BORDER_K3_NAME in contained
    assert "4 " in contained and END_PIECE_K3_NAME in contained
    assert _label(dialog, f"collection-derived-{strip}").endswith("12")
    assert _label(dialog, f"collection-derived-{end}").endswith("4")
    assert _label(dialog, f"collection-physical-{strip}").endswith("12")
    assert _label(dialog, f"collection-used-{strip}").endswith("9")
    assert _label(dialog, f"collection-available-{strip}").endswith("3")
    _set_line(dialog, f"collection-quantity-{strip}", "2")
    assert _label(dialog, f"collection-physical-{strip}").endswith("14")
    assert _label(dialog, f"collection-available-{strip}").endswith("5")
    _set_line(dialog, f"collection-package-quantity-{border.id}", "1")
    assert planner.stock_breakdown()[strip].physical == 8
    assert "8" in _label(dialog, f"collection-physical-{strip}")
    dialog.close()
    dialog.deleteLater()

    def reopen(again: CollectionDialog) -> int:
        assert again.findChild(QLineEdit, f"collection-quantity-{strip}") is not None
        again.accept()
        return int(again.result())

    page.stock_dialog_runner = reopen
    page.manage_stock.click()
    page.manage_stock.click()
    assert page.library.count() > 0


def test_package_overbooking_uses_the_existing_warning(qtbot: QtBot, env: Env) -> None:
    page = _open(qtbot, env, "Ueber")
    page.announce_stock_problem = lambda: None
    planner = _planner(env)
    box = _package(planner, STANDARD_STRAIGHT_ARTICLE)
    planner.set_package_stock(box.id, 1)
    _reload(page)
    page.mode_collection.click()
    _place(page, STANDARD_STRAIGHT_ARTICLE, x=0)
    page.show_all_parts.click()
    _place(page, STANDARD_STRAIGHT_ARTICLE, x=2000)
    assert page.stock_warning.isVisible()
    assert _remaining(page, STANDARD_STRAIGHT_ARTICLE) == "Fehlt 1"


def test_a_legacy_plan_still_loads_edits_and_saves(env: Env) -> None:
    planner = _planner(env)
    track = env.track("Altplan", lanes=2)
    part = _part(planner, STANDARD_STRAIGHT_ARTICLE)
    catalog = {record.id: record.spec for record in planner.list_parts()}
    saved = planner.save(
        place_instance(empty_plan(track.id), part, catalog[part], 12, 34, 0, catalog)
    )
    _select_track = planner.load(track.id)
    assert _select_track.instances[0].x_mm == pytest.approx(saved.instances[0].x_mm)
    moved = with_instances(
        _select_track,
        (
            PartInstance(
                id=_select_track.instances[0].id,
                part_id=part,
                x_mm=80,
                y_mm=90,
            ),
        ),
    )
    planner.save(moved)
    loaded = planner.load(track.id)
    assert loaded.instances[0].x_mm == pytest.approx(80)
    assert loaded.instances[0].y_mm == pytest.approx(90)
    assert _package(planner, STANDARD_STRAIGHT_ARTICLE).quantity == 0


def _package(planner: TrackPlannerService, article: str) -> PackageView:
    for package in planner.list_packages():
        if package.article_number == article:
            return package
    raise AssertionError(article)


def _named(planner: TrackPlannerService, name: str) -> PartRecord:
    for record in planner.list_parts():
        if record.spec.name == name:
            return record
    raise AssertionError(name)


def _copy(part_id: int, index: int) -> PartInstance:
    return PartInstance(id=f"copy-{index}", part_id=part_id, x_mm=float(index * 1000), y_mm=0)


def _content_id(database: Database, package_id: int) -> int:
    with database.session() as session:
        found = session.scalar(
            select(TrackPartPackageContent.id).where(
                TrackPartPackageContent.package_id == package_id
            )
        )
    assert found is not None
    return int(found)


def _label(dialog: CollectionDialog, name: str) -> str:
    label = dialog.findChild(QLabel, name)
    assert isinstance(label, QLabel)
    return label.text()


def _set_line(dialog: CollectionDialog, name: str, value: str) -> None:
    field = dialog.findChild(QLineEdit, name)
    assert isinstance(field, QLineEdit)
    field.setText(value)
    field.editingFinished.emit()
