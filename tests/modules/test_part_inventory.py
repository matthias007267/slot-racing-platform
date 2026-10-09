"""Personal stock, collection mode, excess contours and deleting a track."""

from __future__ import annotations

import math
from collections.abc import Callable
from pathlib import Path

import pytest
from PySide6.QtCore import QPoint, Qt, QTimer
from PySide6.QtGui import QColor, QImage
from PySide6.QtWidgets import QApplication, QLabel, QLineEdit, QMessageBox
from pytestqt.qtbot import QtBot
from sqlalchemy import select

from slot_racing.app.runtime import Runtime
from slot_racing.core.backup import create_backup, restore_backup
from slot_racing.core.config import AppConfig, load_config
from slot_racing.core.config.models import TRACK_PLANNER_BUILD_COLLECTION
from slot_racing.core.domain import TrackId
from slot_racing.core.errors import ValidationError
from slot_racing.modules.track_planner.document import (
    duplicate_instances,
    empty_plan,
    place_instance,
)
from slot_racing.modules.track_planner.figure import track_figure
from slot_racing.modules.track_planner.inventory import analyze_inventory, require_quantity
from slot_racing.modules.track_planner.models import TrackPartStock, TrackPlanInstance
from slot_racing.modules.track_planner.parts import (
    EXTEND_STRAIGHT,
    STANDARD_CURVE_ARTICLE,
    STANDARD_STRAIGHT_ARTICLE,
    STRAIGHT,
    PartInstance,
    PartSpec,
    build_part,
)
from slot_racing.modules.track_planner.service import TrackPlannerService
from slot_racing.modules.track_planner.ui.canvas import ExtendArrow, InstanceItem
from slot_racing.modules.track_planner.ui.collection_dialog import CollectionDialog
from slot_racing.modules.track_planner.ui.page import PlannerPage
from slot_racing.modules.track_planner.ui.track_paint import _drawn
from slot_racing.modules.tracks.models import TrackLayout
from slot_racing.uikit.theme import COLORS
from tests.database import migrated_database
from tests.modules.conftest import Env
from tests.modules.test_track_extend_arrows import _paint_items
from tests.modules.test_track_parts import _library_row, _select
from tests.modules.test_track_planner_extend import _arrows
from tests.modules.test_ui_management import open_page

STRAIGHT_ARTICLE = STANDARD_STRAIGHT_ARTICLE
CURVE_ARTICLE = STANDARD_CURVE_ARTICLE
R2_ARTICLE = "20020572"


def test_quantity_rejects_negatives_and_non_integers() -> None:
    assert require_quantity(0) == 0
    assert require_quantity(10**12) == 10**12
    for value in (-1, True, False, 1.5, "4"):
        with pytest.raises(ValidationError) as caught:
            require_quantity(value)
        assert caught.value.key == "error.planner.stock"


def test_only_the_latest_instances_are_excess() -> None:
    part = 7
    instances = tuple(_placed(f"id-{index}", part, index) for index in range(5))
    # Dictionary order and id order would disagree with creation order here.
    owned = {part: 3}
    report = analyze_inventory(instances, owned)
    line = report.balance(part)
    assert (line.owned, line.used, line.available) == (3, 5, -2)
    assert line.excess_ids == ("id-3", "id-4")
    assert report.excess_ids() == frozenset({"id-3", "id-4"})
    assert report.over_capacity()
    covered = analyze_inventory(instances, {part: 5})
    assert covered.balance(part).available == 0
    assert covered.excess_ids() == frozenset()
    assert not covered.over_capacity()
    removed = analyze_inventory(instances[:4], owned)
    assert removed.excess_ids() == frozenset({"id-3"})
    cleared = analyze_inventory(instances[:3], owned)
    assert cleared.excess_ids() == frozenset()


def test_missing_stock_counts_as_zero_and_lanes_do_not_multiply() -> None:
    first = _placed("a", 1, 0)
    second = _placed("b", 1, 1)
    other = _placed("c", 2, 2)
    report = analyze_inventory((first, second, other), {2: 4, 9: 1})
    assert report.balance(1) == report.balance(1)
    assert (report.balance(1).owned, report.balance(1).used, report.balance(1).available) == (
        0,
        2,
        -2,
    )
    assert report.balance(1).excess_ids == ("a", "b")
    assert (report.balance(2).owned, report.balance(2).used, report.balance(2).available) == (
        4,
        1,
        3,
    )
    assert report.balance(9).used == 0
    assert report.balance(9).available == 1
    # Six copies of a wide part are six parts, not six times its lane count.
    wide = tuple(_placed(f"w{index}", 4, index) for index in range(6))
    wide_report = analyze_inventory(wide, {4: 6})
    assert wide_report.balance(4).used == 6
    assert wide_report.excess_ids() == frozenset()


def test_duplicating_a_group_appends_the_new_copies_as_excess() -> None:
    plan = empty_plan(TrackId(1))
    catalog = {1: _spec()}
    plan = place_instance(plan, 1, catalog[1], 0, 0, 0, catalog)
    plan = place_instance(plan, 1, catalog[1], 2000, 0, 0, catalog)
    original = tuple(instance.id for instance in plan.instances)
    plan, created = duplicate_instances(plan, list(original), 0, 2000)
    report = analyze_inventory(plan.instances, {1: 2})
    assert report.balance(1).used == 4
    assert report.balance(1).excess_ids == created
    moved = tuple(
        _placed(instance.id, instance.part_id, index, group="g" if index < 2 else None)
        for index, instance in enumerate(plan.instances)
    )
    assert analyze_inventory(moved, {1: 2}).balance(1).used == 4


def test_stock_is_stored_changed_and_reloaded(env: Env) -> None:
    planner = _planner(env)
    part = _part(planner, STRAIGHT_ARTICLE)
    planner.set_stock(part, 12)
    planner.set_stock(part, 4)
    planner.set_stock(part, 0)
    assert planner.stock_quantities()[part] == 0
    planner.set_stock(part, 10**12)
    assert planner.stock_quantities()[part] == 10**12
    with pytest.raises(ValidationError):
        planner.set_stock(part, -1)
    assert planner.stock_quantities()[part] == 10**12
    again = TrackPlannerService(env.runtime.database, env.tracks)
    assert again.stock_quantities()[part] == 10**12
    custom = planner.add_part(
        build_part(
            article_number="EB-STOCK",
            scale="1:32",
            name="Eigenes Teil",
            category=STRAIGHT,
            length_mm=200,
            width_mm=None,
            height_mm=None,
            radius_mm=None,
            angle_deg=None,
            lane_count=2,
        )
    )
    planner.set_stock(custom.id, 3)
    fresh = TrackPlannerService(env.runtime.database, env.tracks)
    assert fresh.stock_quantities()[custom.id] == 3


def test_save_and_load_keep_the_same_excess_instances(env: Env) -> None:
    planner = _planner(env)
    track = env.track("Oval", lanes=2)
    part = _part(planner, STRAIGHT_ARTICLE)
    planner.set_stock(part, 2)
    plan = planner.load(track.id)
    spec = _record_spec(planner, part)
    catalog = {part: spec}
    for index in range(4):
        plan = place_instance(plan, part, spec, index * 2000.0, 0.0, 0.0, catalog)
    created = tuple(instance.id for instance in plan.instances)
    planner.save(plan)
    loaded = planner.load(track.id)
    assert tuple(instance.id for instance in loaded.instances) == created
    report = analyze_inventory(loaded.instances, planner.stock_quantities())
    assert report.balance(part).excess_ids == created[2:]


def test_deleting_a_track_removes_only_that_plan(env: Env) -> None:
    planner = _planner(env)
    kept = env.track("Bleibt", lanes=2)
    doomed = env.track("Weg", lanes=4)
    part = _part(planner, STRAIGHT_ARTICLE)
    planner.set_stock(part, 8)
    plan = place_instance(
        planner.load(doomed.id),
        part,
        _record_spec(planner, part),
        0,
        0,
        0,
        {part: _record_spec(planner, part)},
    )
    spec = _record_spec(planner, part)
    plan = place_instance(plan, part, spec, 2000, 0, 0, {part: spec})
    planner.save(plan)
    other = place_instance(
        planner.load(kept.id),
        part,
        _record_spec(planner, part),
        0,
        0,
        0,
        {part: _record_spec(planner, part)},
    )
    planner.save(other)
    definitions = {record.id for record in planner.list_parts()}
    planner.delete_track(doomed.id)
    assert env.tracks.get_track(doomed.id) is None
    assert env.tracks.get_track(kept.id) is not None
    assert planner.load(kept.id).instances
    assert planner.stock_quantities()[part] == 8
    assert {record.id for record in planner.list_parts()} == definitions
    with env.runtime.database.session() as session:
        instances = session.scalars(
            select(TrackPlanInstance).where(TrackPlanInstance.track_id == int(doomed.id))
        )
        layouts = session.scalars(select(TrackLayout).where(TrackLayout.track_id == int(doomed.id)))
        assert list(instances) == []
        assert list(layouts) == []
        assert session.get(TrackPartStock, part) is not None


def test_a_track_used_by_a_race_or_time_trial_is_kept(env: Env) -> None:
    planner = _planner(env)
    track = env.track("Historie", lanes=2)
    race = env.races.create_race("Finale", track.id, 8)
    with pytest.raises(ValidationError) as caught:
        planner.delete_track(track.id)
    assert caught.value.key == "error.track.in_use"
    assert env.tracks.get_track(track.id) is not None
    assert env.races.get_race(race.id) is not None
    trial_track = env.track("Zeitfahren", lanes=2)
    trial = env.races.create_time_trial("Training", trial_track.id)
    with pytest.raises(ValidationError) as trial_error:
        planner.delete_track(trial_track.id)
    assert trial_error.value.key == "error.track.in_use"
    assert env.races.get_race(trial.id) is not None
    assert env.tracks.get_track(trial_track.id) is not None


def test_backup_restores_personal_stock(tmp_path: Path) -> None:
    folder = tmp_path
    config = AppConfig(
        database_path=folder / "slot_racing.db",
        backup_directory=folder / "backups",
        track_planner_build_mode=TRACK_PLANNER_BUILD_COLLECTION,
    )
    runtime = Runtime.create(config, config_path=folder / "config.json")
    try:
        from slot_racing.modules.tracks.service import TrackService

        planner = runtime.services.get(TrackPlannerService)
        tracks = runtime.services.get(TrackService)
        part = _part(planner, STRAIGHT_ARTICLE)
        planner.set_stock(part, 15)
        archive = create_backup(runtime.database, runtime.config, folder / "backups")
        planner.set_stock(part, 1)
        restore_backup(
            archive,
            runtime.database,
            runtime.config,
            folder / "config.json",
            backup_directory=folder / "safety",
        )
        restored = TrackPlannerService(runtime.database, tracks)
        assert restored.stock_quantities()[part] == 15
        assert runtime.config.track_planner_build_mode == TRACK_PLANNER_BUILD_COLLECTION
    finally:
        runtime.shutdown()


def test_unlimited_lists_every_part_and_collection_lists_what_is_left(
    qtbot: QtBot, env: Env
) -> None:
    page = _open(qtbot, env, "Bibliothek")
    planner = _planner(env)
    straight = _part(planner, STRAIGHT_ARTICLE)
    curve = _part(planner, CURVE_ARTICLE)
    planner.set_stock(straight, 4)
    planner.set_stock(curve, 2)
    planner.set_stock(_part(planner, R2_ARTICLE), 0)
    _reload(page)
    assert page.mode_unlimited.isChecked()
    assert not page.show_all_parts.isVisible()
    assert _library_row(page, STRAIGHT_ARTICLE) >= 0
    assert _library_row(page, R2_ARTICLE) >= 0
    assert _remaining(page, STRAIGHT_ARTICLE) is None
    page.mode_collection.click()
    assert page.mode_collection.isChecked()
    assert page.show_all_parts.isVisible()
    assert _remaining(page, STRAIGHT_ARTICLE) == "Noch 4"
    assert _remaining(page, CURVE_ARTICLE) == "Noch 2"
    with pytest.raises(AssertionError):
        _library_row(page, R2_ARTICLE)
    page.show_all_parts.click()
    assert _library_row(page, R2_ARTICLE) >= 0
    assert _remaining(page, R2_ARTICLE) == "Noch 0"


def test_placing_and_removing_updates_the_library_and_the_warning(qtbot: QtBot, env: Env) -> None:
    page = _open(qtbot, env, "Verbrauch")
    notices: list[str] = []
    page.announce_stock_problem = lambda: notices.append("shown")
    planner = _planner(env)
    straight = _part(planner, STRAIGHT_ARTICLE)
    planner.set_stock(straight, 4)
    _reload(page)
    page.mode_collection.click()
    for index in range(4):
        _place(page, STRAIGHT_ARTICLE, x=index * 2000.0)
    assert notices == []
    with pytest.raises(AssertionError):
        _library_row(page, STRAIGHT_ARTICLE)
    page.show_all_parts.click()
    fifth = _place(page, STRAIGHT_ARTICLE, x=8000)
    assert notices == ["shown"]
    assert _remaining(page, STRAIGHT_ARTICLE) == "Fehlt 1"
    excess = [item for item in _instances(page) if item._shortage]
    assert [item.item_id for item in excess] == [fifth.id]
    assert page.stock_warning.isVisible()
    _place(page, STRAIGHT_ARTICLE, x=10000)
    assert notices == ["shown"]
    page.delete_selected()
    assert page.stock_warning.isVisible()
    # The newest remaining copy is still the fifth, so one extra is left.
    assert len([item for item in _instances(page) if item._shortage]) == 1
    page.undo()
    assert len(page.plan().instances) == 6
    assert page.stock_warning.isVisible()
    assert notices == ["shown"]
    while page.plan().instances and page.stock_warning.isVisible():
        _select_instance(page, page.plan().instances[-1].id)
        page.delete_selected()
    assert len(page.plan().instances) == 4
    assert not page.stock_warning.isVisible()
    assert _remaining(page, STRAIGHT_ARTICLE) == "Noch 0"
    page.undo()
    assert page.stock_warning.isVisible()
    assert notices == ["shown", "shown"]


def test_the_notice_closes_and_building_continues(qtbot: QtBot, env: Env) -> None:
    page = _open(qtbot, env, "Hinweis")
    planner = _planner(env)
    straight = _part(planner, STRAIGHT_ARTICLE)
    planner.set_stock(straight, 1)
    _reload(page)
    page.mode_collection.click()
    _place(page, STRAIGHT_ARTICLE, x=0)
    _close_notice_soon()
    page.show_all_parts.click()
    _place(page, STRAIGHT_ARTICLE, x=2000)
    assert page.stock_warning.isVisible()
    assert QApplication.activeModalWidget() is None
    _place(page, STRAIGHT_ARTICLE, x=4000)
    assert len(page.plan().instances) == 3
    assert page.stock_warning.isVisible()


def test_changing_stock_clears_and_restores_the_warning(qtbot: QtBot, env: Env) -> None:
    page = _open(qtbot, env, "Bestand")
    notices: list[str] = []
    page.announce_stock_problem = lambda: notices.append("shown")
    planner = _planner(env)
    straight = _part(planner, STRAIGHT_ARTICLE)
    planner.set_stock(straight, 1)
    _reload(page)
    page.mode_collection.click()
    _place(page, STRAIGHT_ARTICLE, x=0)
    page.show_all_parts.click()
    _place(page, STRAIGHT_ARTICLE, x=2000)
    assert notices == ["shown"]
    assert page.stock_warning.isVisible()

    def raise_stock(dialog: CollectionDialog) -> int:
        field = dialog.findChild(QLineEdit, f"collection-quantity-{straight}")
        assert isinstance(field, QLineEdit)
        field.setText("2")
        field.editingFinished.emit()
        dialog.accept()
        return int(dialog.result())

    page.stock_dialog_runner = raise_stock
    page.manage_stock.click()
    assert not page.stock_warning.isVisible()
    assert all(not item._shortage for item in _instances(page))

    def lower_stock(dialog: CollectionDialog) -> int:
        field = dialog.findChild(QLineEdit, f"collection-quantity-{straight}")
        assert isinstance(field, QLineEdit)
        field.setText("1")
        field.editingFinished.emit()
        dialog.accept()
        return int(dialog.result())

    page.stock_dialog_runner = lower_stock
    page.manage_stock.click()
    assert page.stock_warning.isVisible()
    assert notices == ["shown", "shown"]
    assert sum(item._shortage for item in _instances(page)) == 1


def test_undo_redo_copy_and_extend_follow_the_stock(qtbot: QtBot, env: Env) -> None:
    page = _open(qtbot, env, "Aenderungen")
    page.announce_stock_problem = lambda: None
    planner = _planner(env)
    straight = _part(planner, STRAIGHT_ARTICLE)
    planner.set_stock(straight, 1)
    _reload(page)
    page.mode_collection.click()
    first = _place(page, STRAIGHT_ARTICLE, x=0)
    with pytest.raises(AssertionError):
        _library_row(page, STRAIGHT_ARTICLE)
    page.undo()
    assert page.plan().instances == ()
    assert _remaining(page, STRAIGHT_ARTICLE) == "Noch 1"
    page.redo()
    assert page.plan().instances[0].id == first.id
    with pytest.raises(AssertionError):
        _library_row(page, STRAIGHT_ARTICLE)
    _select_instance(page, first.id)
    page.copy_selection()
    page.paste_selection()
    assert len(page.plan().instances) == 2
    assert [item.item_id for item in _instances(page) if item._shortage] == [
        page.plan().instances[-1].id
    ]
    _select_instance(page, first.id)
    arrow = next(item for item in _arrows(page) if item.direction == EXTEND_STRAIGHT)
    _click(qtbot, page, arrow)
    assert len(page.plan().instances) == 3
    assert page.plan().instances[-1].part_id == straight
    assert page.plan().instances[-1].id in _excess_ids(page)
    before = len(page.plan().instances)
    _select_instance(page, first.id)
    page.canvas.scene().clearSelection()
    for item in _instances(page):
        item.setSelected(True)
    page.copy_selection()
    page.paste_selection()
    assert len(page.plan().instances) == before + before
    assert _planner(env).stock_quantities()[straight] == 1


def test_switching_mode_checks_the_plan_immediately(qtbot: QtBot, env: Env) -> None:
    page = _open(qtbot, env, "Modus")
    notices: list[str] = []
    page.announce_stock_problem = lambda: notices.append("shown")
    planner = _planner(env)
    straight = _part(planner, STRAIGHT_ARTICLE)
    planner.set_stock(straight, 0)
    _reload(page)
    _place(page, STRAIGHT_ARTICLE, x=0)
    assert not page.stock_warning.isVisible()
    assert all(not item._shortage for item in _instances(page))
    page.mode_collection.click()
    assert notices == ["shown"]
    assert page.stock_warning.isVisible()
    assert _instances(page)[0]._shortage
    page.mode_unlimited.click()
    assert not page.stock_warning.isVisible()
    assert all(not item._shortage for item in _instances(page))
    assert _library_row(page, R2_ARTICLE) >= 0
    page.mode_collection.click()
    assert notices == ["shown"]
    assert env.runtime.config.track_planner_build_mode == TRACK_PLANNER_BUILD_COLLECTION


def test_the_build_mode_is_a_saved_planner_preference(qtbot: QtBot, tmp_path: Path) -> None:
    from slot_racing.core.catalog import TrackCatalog

    path = tmp_path / "config.json"
    runtime = Runtime.create(AppConfig(), config_path=path, database=migrated_database())
    try:
        page = PlannerPage(
            runtime.translator,
            runtime.services.get(TrackCatalog),
            runtime.services.get(TrackPlannerService),
            runtime.config,
            runtime.config_path,
        )
        qtbot.addWidget(page)
        assert page.mode_unlimited.isChecked()
        page.mode_collection.click()
        assert load_config(path).track_planner_build_mode == TRACK_PLANNER_BUILD_COLLECTION
        page.mode_unlimited.click()
        assert load_config(path).track_planner_build_mode == "unlimited"
    finally:
        runtime.shutdown()


def test_selection_and_shortage_contours_can_show_together(qtbot: QtBot, env: Env) -> None:
    page = _open(qtbot, env, "Kontur")
    page.announce_stock_problem = lambda: None
    planner = _planner(env)
    curve = _part(planner, CURVE_ARTICLE)
    planner.set_stock(curve, 0)
    _reload(page)
    page.mode_collection.click()
    page.show_all_parts.click()
    _place(page, CURVE_ARTICLE)
    item = _instances(page)[0]
    assert item._shortage
    assert item.isSelected()
    path = _drawn(track_figure(item.spec)).roadway
    assert any(
        path.elementAt(index).type == path.ElementType.CurveToElement
        for index in range(path.elementCount())
    )
    image = _paint_items([item], 480, 480, 40, 240)
    arc = (400.0 * math.cos(math.radians(25)), 400.0 * math.sin(math.radians(25)))
    assert _color_near(image, 40 + arc[0], 240 + arc[1], _is_error) or _color_near(
        image, 40 + arc[0], 240 + arc[1], _is_accent
    )
    assert _color_near(image, 440, 240, _is_accent)
    assert _color_near(image, 440, 240, _is_error)
    assert not _color_near(image, 440, 360, _is_error)
    assert not _color_near(image, 340, 40, _is_error)
    item.setSelected(False)
    plain = _paint_items([item], 480, 480, 40, 240)
    assert _color_near(plain, 440, 240, _is_error)
    assert not _color_near(plain, 440, 240, _is_accent)
    assert not _color_near(plain, 440, 360, _is_error)


def test_delete_track_asks_and_leaves_the_collection_in_place(qtbot: QtBot, env: Env) -> None:
    kept = env.track("Bleibt", lanes=2)
    page = _open(qtbot, env, "Loeschen")
    doomed = page.plan().track_id
    planner = _planner(env)
    part = _part(planner, STRAIGHT_ARTICLE)
    planner.set_stock(part, 6)
    _place(page, STRAIGHT_ARTICLE, x=0)
    page.save()
    definitions = {record.id for record in planner.list_parts()}
    assert page.delete_track_button.text() == "Strecke löschen"
    seen: list[str] = []

    def refuse(text: str) -> bool:
        seen.append(text)
        return False

    page.confirm_delete = refuse
    page.delete_current_track()
    assert "Loeschen" in seen[0]
    assert env.tracks.get_track(doomed) is not None
    page.confirm_delete = lambda _text: True
    page.delete_current_track()
    assert env.tracks.get_track(doomed) is None
    assert env.tracks.get_track(kept.id) is not None
    assert planner.stock_quantities()[part] == 6
    assert {record.id for record in planner.list_parts()} == definitions
    race_track = env.track("Rennen", lanes=2)
    race = env.races.create_race("Lauf", race_track.id, 5)
    page._refresh_tracks()
    _select(page, race_track.id)
    page.delete_current_track()
    assert env.tracks.get_track(race_track.id) is not None
    assert env.races.get_race(race.id) is not None
    assert "nicht gelöscht" in page.status.text()


def _planner(env: Env) -> TrackPlannerService:
    return env.runtime.services.get(TrackPlannerService)


def _part(planner: TrackPlannerService, article: str) -> int:
    for record in planner.list_parts():
        if record.spec.article_number == article:
            return record.id
    raise AssertionError(article)


def _record_spec(planner: TrackPlannerService, part_id: int) -> PartSpec:
    for record in planner.list_parts():
        if record.id == part_id:
            return record.spec
    raise AssertionError(part_id)


def _placed(instance_id: str, part_id: int, index: int, group: str | None = None) -> PartInstance:
    return PartInstance(
        id=instance_id,
        part_id=part_id,
        x_mm=float(index * 1000),
        y_mm=0.0,
        group_id=group,
    )


def _spec() -> PartSpec:
    return build_part(
        article_number="EB-MOVE",
        scale="1:32",
        name="Schiebegerade",
        category=STRAIGHT,
        length_mm=200,
        width_mm=None,
        height_mm=None,
        radius_mm=None,
        angle_deg=None,
        lane_count=2,
    )


def _open(qtbot: QtBot, env: Env, name: str) -> PlannerPage:
    track = env.track(name, lanes=2)
    window, page = open_page(qtbot, env, "track_planner")
    window.show()
    assert isinstance(page, PlannerPage)
    _select(page, track.id)
    return page


def _reload(page: PlannerPage) -> None:
    page._load_parts()
    page._draw()


def _place(page: PlannerPage, article: str, *, x: float = 0.0, y: float = 0.0) -> PartInstance:
    if not page.show_all_parts.isChecked() and page.mode_collection.isChecked():
        try:
            row = _library_row(page, article)
        except AssertionError:
            page.show_all_parts.click()
            row = _library_row(page, article)
    else:
        row = _library_row(page, article)
    page.library.setCurrentRow(row)
    page.place_part.click()
    page.x_mm.setValue(x)
    page.y_mm.setValue(y)
    return page.plan().instances[-1]


def _remaining(page: PlannerPage, article: str) -> str | None:
    row = _library_row(page, article)
    item = page.library.item(row)
    card = None if item is None else page.library.itemWidget(item)
    label = None if card is None else card.findChild(QLabel, "planner-part-remaining")
    if label is None:
        return None
    return label.text()


def _library_has(page: PlannerPage, article: str) -> bool:
    try:
        _library_row(page, article)
    except AssertionError:
        return False
    return True


def _instances(page: PlannerPage) -> list[InstanceItem]:
    return [item for item in page.canvas.scene().items() if isinstance(item, InstanceItem)]


def _excess_ids(page: PlannerPage) -> set[str]:
    return {item.item_id for item in _instances(page) if item._shortage}


def _select_instance(page: PlannerPage, instance_id: str) -> None:
    page.canvas.scene().clearSelection()
    for item in _instances(page):
        item.setSelected(item.item_id == instance_id)


def _click(qtbot: QtBot, page: PlannerPage, arrow: ExtendArrow) -> None:
    origin = page.canvas.mapFromScene(arrow.scenePos())
    point = origin + QPoint(round(arrow.offset.x()), round(arrow.offset.y()))
    qtbot.mouseClick(page.canvas.viewport(), Qt.MouseButton.LeftButton, pos=point)  # type: ignore[no-untyped-call]


def _close_notice_soon() -> None:
    def close() -> None:
        for widget in QApplication.topLevelWidgets():
            if isinstance(widget, QMessageBox) and widget.objectName() == "planner-stock-notice":
                widget.accept()

    QTimer.singleShot(0, close)


def _is_accent(color: QColor) -> bool:
    accent = QColor(COLORS.accent)
    return abs(color.green() - accent.green()) <= 40 and color.green() > color.red() + 40


def _is_error(color: QColor) -> bool:
    error = QColor(COLORS.error)
    return abs(color.red() - error.red()) <= 50 and color.red() > color.green() + 40


def _color_near(image: QImage, x: float, y: float, match: Callable[[QColor], bool]) -> bool:
    cx = round(x)
    cy = round(y)
    for dx in range(-3, 4):
        for dy in range(-3, 4):
            px = cx + dx
            py = cy + dy
            if (
                0 <= px < image.width()
                and 0 <= py < image.height()
                and match(image.pixelColor(px, py))
            ):
                return True
    return False
