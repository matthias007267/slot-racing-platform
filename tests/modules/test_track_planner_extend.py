"""Save-as, start straight, grid, rotation handle, docking, filters, library and groups."""

from __future__ import annotations

import math
from pathlib import Path

import pytest
from PySide6.QtCore import QPoint, QRectF, Qt
from PySide6.QtGui import QImage, QPainter
from PySide6.QtWidgets import QDialog, QLabel, QWidget
from pytestqt.qtbot import QtBot

from slot_racing.app.runtime import Runtime
from slot_racing.core.backup import create_backup, restore_backup
from slot_racing.core.config import AppConfig
from slot_racing.core.domain import TrackId
from slot_racing.core.errors import ValidationError
from slot_racing.modules.track_planner.document import (
    empty_plan,
    place_instance,
    set_start_straight,
    toggle_group,
)
from slot_racing.modules.track_planner.parts import (
    BORDER,
    CURVE,
    STRAIGHT,
    ConnectorSpec,
    PartInstance,
    PartSpec,
    build_part,
    world_xy,
)
from slot_racing.modules.track_planner.service import TrackPlannerService
from slot_racing.modules.track_planner.ui.canvas import MM, InstanceItem, PlusItem, RotationHandle
from slot_racing.modules.track_planner.ui.library_dialog import PartDialog
from slot_racing.modules.track_planner.ui.library_manager import LibraryManager
from slot_racing.modules.track_planner.ui.page import PlannerPage
from slot_racing.modules.tracks.service import TrackInput, TrackService
from tests.modules.conftest import Env
from tests.modules.test_track_parts import _library_row, _planner, _select
from tests.modules.test_track_planner_ui import _instances, _key
from tests.modules.test_ui_management import open_page


def test_the_open_plan_can_be_stored_as_a_new_track(qtbot: QtBot, env: Env) -> None:
    track = env.track("Original", lanes=3)
    window, page = open_page(qtbot, env, "track_planner")
    window.show()
    assert isinstance(page, PlannerPage)
    _select(page, track.id)
    assert page.delete_button.isHidden()
    assert page.place_part.isHidden()
    page.add_start.click()
    page.library.setCurrentRow(_library_row(page, "20020601"))
    page.place_part.click()
    page.x_mm.setValue(40)
    page.y_mm.setValue(15)
    page.rotation_free.setValue(12)
    page.start_straight.setChecked(True)
    page.save()
    page.x_mm.setValue(80)
    original = page.plan()
    before = _planner(env).list_parts()
    page._ask_track_name = lambda: "Kopie"  # type: ignore[method-assign]
    page.save_as_button.click()
    assert page.plan().track_id == original.track_id
    assert page.plan().instances == original.instances
    assert page.plan().markers == original.markers
    assert page.track_combo.currentData() == track.id
    assert _planner(env).list_parts() == before
    stored = _planner(env).load(track.id)
    assert stored.instances[0].x_mm == pytest.approx(40)
    assert stored.instances[0].start_straight
    copy_id = page.track_combo.findText("Kopie")
    assert copy_id >= 0
    copied_track = page.track_combo.itemData(copy_id)
    assert copied_track != track.id
    copied = _planner(env).load(TrackId(int(copied_track)))
    assert copied.track_id != original.track_id
    assert len(copied.instances) == 1
    assert copied.instances[0].id != original.instances[0].id
    assert copied.instances[0].part_id == original.instances[0].part_id
    assert copied.instances[0].x_mm == pytest.approx(original.instances[0].x_mm)
    assert copied.instances[0].y_mm == pytest.approx(original.instances[0].y_mm)
    assert copied.instances[0].rotation_z_deg == pytest.approx(12)
    assert copied.instances[0].start_straight
    assert copied.start_finish() is not None
    assert copied.markers[0].x == original.markers[0].x
    info = env.tracks.get_track(copied.track_id)
    assert info is not None and info.lane_count == 3 and info.name == "Kopie"


def test_only_one_straight_can_be_the_start_straight(qtbot: QtBot, env: Env) -> None:
    track = env.track("Start", lanes=2)
    window, page = open_page(qtbot, env, "track_planner")
    window.show()
    assert isinstance(page, PlannerPage)
    _select(page, track.id)
    page.grid.setChecked(False)
    assert not page.jump_button.isEnabled()
    page.jump_to_start()
    assert "Startgerade" in page.status.text()
    assert page.plan().instances == ()

    page.library.setCurrentRow(_library_row(page, "20020572"))
    page.place_part.click()
    curve = page.plan().instances[0]
    assert page._parts[curve.part_id].category == CURVE
    assert not page.start_straight.isEnabled()
    page.start_straight.setChecked(True)
    assert not page.plan().instances[0].start_straight
    with pytest.raises(ValidationError) as caught:
        set_start_straight(page.plan(), curve.id, True, page._parts)
    assert caught.value.key == "error.planner.start_straight"
    page.delete_selected()

    page.library.setCurrentRow(_library_row(page, "20020601"))
    page.place_part.click()
    page.x_mm.setValue(0)
    page.y_mm.setValue(0)
    page.start_straight.setChecked(True)
    page.place_part.click()
    page.x_mm.setValue(800)
    page.y_mm.setValue(0)
    assert sum(instance.start_straight for instance in page.plan().instances) == 1
    second = page.plan().instances[1]
    _select_ids(page, {second.id})
    assert page.start_straight.isEnabled()
    page.start_straight.setChecked(True)
    flags = {instance.id: instance.start_straight for instance in page.plan().instances}
    assert flags[second.id]
    assert not flags[page.plan().instances[0].id]
    assert page.jump_button.isEnabled()

    start = next(instance for instance in page.plan().instances if instance.start_straight)
    kept = (start.x_mm, start.y_mm)
    page.canvas.pan_by(420, -260)
    page.jump_button.click()
    center = page.canvas.mapToScene(page.canvas.viewport().rect().center())
    assert abs(center.x() - start.x_mm * MM) < 4
    assert abs(center.y() - start.y_mm * MM) < 4
    assert (start.x_mm, start.y_mm) == kept

    _select_ids(page, {start.id})
    _key(qtbot, page, Qt.Key.Key_C, Qt.KeyboardModifier.ControlModifier)
    _key(qtbot, page, Qt.Key.Key_V, Qt.KeyboardModifier.ControlModifier)
    assert sum(instance.start_straight for instance in page.plan().instances) == 1
    clone_id = page.canvas.selected_ids()[0]
    clone = next(instance for instance in page.plan().instances if instance.id == clone_id)
    assert clone.id != start.id
    assert not clone.start_straight
    _select_ids(page, {start.id})
    _key(qtbot, page, Qt.Key.Key_Delete)
    assert not any(instance.start_straight for instance in page.plan().instances)
    assert not page.jump_button.isEnabled()


def test_the_grid_is_off_until_it_is_switched_on(qtbot: QtBot, env: Env) -> None:
    track = env.track("Raster", lanes=2)
    window, page = open_page(qtbot, env, "track_planner")
    window.show()
    assert isinstance(page, PlannerPage)
    _select(page, track.id)
    assert not page.grid.isChecked()
    assert not page.canvas._scene.grid_enabled
    page.library.setCurrentRow(0)
    page.place_part.click()
    stored = (page.plan().instances[0].x_mm, page.plan().instances[0].y_mm)
    plain = _pixels(page)
    page.grid.setChecked(True)
    assert page.canvas._scene.grid_enabled
    assert page.plan().grid_enabled
    assert (page.plan().instances[0].x_mm, page.plan().instances[0].y_mm) == stored
    lined = _pixels(page)
    assert lined != plain
    page.grid.setChecked(False)
    assert not page.canvas._scene.grid_enabled
    assert _pixels(page) != lined
    assert (page.plan().instances[0].x_mm, page.plan().instances[0].y_mm) == stored


def test_a_part_rotates_only_from_the_handle(qtbot: QtBot, env: Env) -> None:
    track = env.track("Drehen", lanes=2)
    window, page = open_page(qtbot, env, "track_planner")
    window.show()
    assert isinstance(page, PlannerPage)
    _select(page, track.id)
    page.grid.setChecked(False)
    page.library.setCurrentRow(_library_row(page, "20020612"))
    page.place_part.click()
    page.x_mm.setValue(0)
    page.y_mm.setValue(0)
    item = _instances(page)[0]
    center = page.canvas.mapFromScene(item.sceneBoundingRect().center())
    hit = page.canvas.itemAt(center)
    assert isinstance(hit, InstanceItem)
    assert not isinstance(hit, RotationHandle)
    rotation = page.plan().instances[0].rotation_z_deg
    origin = page.plan().instances[0].x_mm
    viewport = page.canvas.viewport()
    qtbot.mousePress(viewport, Qt.MouseButton.LeftButton, pos=center)  # type: ignore[no-untyped-call]
    qtbot.mouseMove(viewport, pos=center + QPoint(40, 0))  # type: ignore[no-untyped-call]
    qtbot.mouseRelease(viewport, Qt.MouseButton.LeftButton, pos=center + QPoint(40, 0))  # type: ignore[no-untyped-call]
    assert page.plan().instances[0].rotation_z_deg == pytest.approx(rotation)
    assert page.plan().instances[0].x_mm != pytest.approx(origin)
    knob = page.canvas.mapFromScene(page.canvas.rotation_handle.pos()) + QPoint(0, -36)
    qtbot.mousePress(viewport, Qt.MouseButton.LeftButton, pos=knob)  # type: ignore[no-untyped-call]
    assert page.canvas.rotation_handle.dragging
    qtbot.mouseMove(viewport, pos=knob + QPoint(30, 20))  # type: ignore[no-untyped-call]
    qtbot.mouseRelease(viewport, Qt.MouseButton.LeftButton, pos=knob + QPoint(30, 20))  # type: ignore[no-untyped-call]
    assert page.plan().instances[0].rotation_z_deg != pytest.approx(rotation)


def test_a_plus_docks_another_copy_and_undo_removes_it(qtbot: QtBot, env: Env) -> None:
    track = env.track("Plus", lanes=2)
    window, page = open_page(qtbot, env, "track_planner")
    window.show()
    assert isinstance(page, PlannerPage)
    _select(page, track.id)
    page.grid.setChecked(False)
    page.library.setCurrentRow(_library_row(page, "20020601"))
    page.place_part.click()
    page.x_mm.setValue(0)
    page.y_mm.setValue(0)
    pluses = _pluses(page)
    assert len(pluses) == 2
    original = page.plan().instances[0]
    spec = page._parts[original.part_id]
    target = pluses[0]
    point = page.canvas.mapFromScene(target.scenePos()) + QPoint(
        int(target.offset.x()), int(target.offset.y())
    )
    qtbot.mouseClick(page.canvas.viewport(), Qt.MouseButton.LeftButton, pos=point)  # type: ignore[no-untyped-call]
    assert len(page.plan().instances) == 2
    created = page.plan().instances[1]
    assert created.part_id == original.part_id
    assert created.id != original.id
    assert page.canvas.selected_ids() == [created.id]
    assert _joints_meet(original, created, spec.connectors)
    _select_ids(page, {original.id})
    assert len(_pluses(page)) == 1
    page.undo()
    assert [instance.id for instance in page.plan().instances] == [original.id]


def test_library_filters_follow_scale_compatibility_and_reset(qtbot: QtBot, env: Env) -> None:
    planner = _planner(env)
    planner.add_part(
        _custom(planner, "EB-F43", "1:43", "Filtergerade", STRAIGHT, length=200, lanes=2)
    )
    planner.add_part(
        _custom(planner, "EB-F1", "1:32", "Schmale Gerade", STRAIGHT, length=200, lanes=1)
    )
    track = env.track("Filter", lanes=2)
    window, page = open_page(qtbot, env, "track_planner")
    window.show()
    assert isinstance(page, PlannerPage)
    _select(page, track.id)
    assert not page.compatible.isEnabled()
    full = page.library.count()
    page.scale_filter.setCurrentIndex(page.scale_filter.findData("1:43"))
    assert _scales(page) == ["1:43"]
    page.scale_filter.setCurrentIndex(page.scale_filter.findData("1:32"))
    assert "1:43" not in _scales(page)
    assert "1:32" in _scales(page)
    page.library.setCurrentRow(_library_row(page, "20020601"))
    page.place_part.click()
    assert page.compatible.isEnabled()
    page.compatible.setChecked(True)
    articles = _articles(page)
    assert _contains(articles, "20020601")
    assert not _contains(articles, "20020560")
    assert not _contains(articles, "EB-F1")
    assert not _contains(articles, "EB-F43")
    page.canvas.scene().clearSelection()
    assert not page.compatible.isEnabled()
    assert _contains(_articles(page), "20020560")
    page.reset_filter.click()
    assert page.scale_filter.currentData() is None
    assert not page.compatible.isChecked()
    assert page.library.count() == full


def test_the_library_manager_edits_definitions_and_keeps_used_ones(
    qtbot: QtBot, env: Env, monkeypatch: pytest.MonkeyPatch
) -> None:
    planner = _planner(env)
    created = planner.add_part(
        _custom(planner, "EB-EDIT", "1:32", "A" * 80, STRAIGHT, length=220, lanes=2)
    )
    track = env.track("Bibliothek", lanes=2)
    window, page = open_page(qtbot, env, "track_planner")
    window.show()
    assert isinstance(page, PlannerPage)
    _select(page, track.id)
    page.library.setCurrentRow(_library_row(page, "EB-EDIT"))
    page.place_part.click()
    page.save()
    placed_id = page.plan().instances[0].id
    manager = LibraryManager(
        env.runtime.translator, planner, {instance.part_id for instance in page.plan().instances}
    )
    qtbot.addWidget(manager)
    manager.resize(640, 520)
    manager.show()
    row = next(item for item in manager._rows if item.part_id == created.id)
    row.resize(480, 72)
    layout = row.layout()
    assert layout is not None
    layout.activate()
    preview = row.findChild(QWidget, "library-manager-preview")
    name = row.findChild(QLabel, "library-manager-name")
    scale = row.findChild(QLabel, "library-manager-scale")
    assert preview is not None and name is not None and scale is not None
    names = {label.objectName() for label in row.findChildren(QLabel)}
    assert names == {"library-manager-name", "library-manager-scale"}
    preview_right = preview.mapTo(row, preview.rect().bottomRight()).x()
    name_left = name.mapTo(row, name.rect().topLeft()).x()
    name_bottom = name.mapTo(row, name.rect().bottomLeft()).y()
    scale_top = scale.mapTo(row, scale.rect().topLeft()).y()
    assert preview_right <= name_left
    assert name_bottom <= scale_top
    assert scale.text() == "1:32"
    assert name.text() == "A" * 80
    manager.search.setText("EB-EDIT")
    assert row.isVisible()
    assert any(not item.isVisible() for item in manager._rows)

    def _rename(dialog: PartDialog) -> int:
        dialog.name.setText("Geändert")
        dialog.accept()
        return int(QDialog.DialogCode.Accepted)

    monkeypatch.setattr(PartDialog, "exec", _rename)
    manager.select_part(created.id)
    manager.edit_button.click()
    assert planner.library.require(created.id).spec.name == "Geändert"
    assert page.plan().instances[0].id == placed_id
    assert page.plan().instances[0].part_id == created.id
    manager.delete_button.click()
    assert "verwendet" in manager.status.text()
    assert planner.library.require(created.id).spec.name == "Geändert"
    assert len(page.plan().instances) == 1

    unused = planner.add_part(
        _custom(planner, "EB-FREE", "1:24", "Frei", STRAIGHT, length=180, lanes=2)
    )
    manager.reload()
    manager.select_part(unused.id)
    manager.delete_button.click()
    with pytest.raises(ValidationError):
        planner.library.require(unused.id)
    assert len(page.plan().instances) == 1


def test_ctrl_g_groups_members_for_selection_move_rotation_copy_and_undo(
    qtbot: QtBot, env: Env
) -> None:
    track = env.track("Gruppe", lanes=2)
    window, page = open_page(qtbot, env, "track_planner")
    window.show()
    assert isinstance(page, PlannerPage)
    _select(page, track.id)
    page.grid.setChecked(False)
    page.library.setCurrentRow(_library_row(page, "20020611"))
    page.place_part.click()
    page.x_mm.setValue(0)
    page.y_mm.setValue(0)
    page.place_part.click()
    page.x_mm.setValue(400)
    page.y_mm.setValue(0)
    both = {instance.id for instance in page.plan().instances}
    _select_ids(page, both)
    history = len(page._history)
    _select_ids(page, {next(iter(both))})
    _key(qtbot, page, Qt.Key.Key_G, Qt.KeyboardModifier.ControlModifier)
    assert len(page._history) == history
    _select_ids(page, both)
    _key(qtbot, page, Qt.Key.Key_G, Qt.KeyboardModifier.ControlModifier)
    group_id = page.plan().instances[0].group_id
    assert group_id is not None
    assert page.plan().instances[1].group_id == group_id
    page.canvas.scene().clearSelection()
    click = page.canvas.mapFromScene(_instances(page)[0].sceneBoundingRect().center())
    qtbot.mouseClick(page.canvas.viewport(), Qt.MouseButton.LeftButton, pos=click)  # type: ignore[no-untyped-call]
    assert set(page.canvas.selected_ids()) == both

    before = [(instance.x_mm, instance.y_mm) for instance in page.plan().instances]
    item = next(entry for entry in _instances(page) if entry.item_id == page.plan().instances[0].id)
    origin = page.canvas.mapFromScene(item.sceneBoundingRect().center())
    viewport = page.canvas.viewport()
    qtbot.mousePress(viewport, Qt.MouseButton.LeftButton, pos=origin)  # type: ignore[no-untyped-call]
    qtbot.mouseMove(viewport, pos=origin + QPoint(50, 0))  # type: ignore[no-untyped-call]
    qtbot.mouseRelease(viewport, Qt.MouseButton.LeftButton, pos=origin + QPoint(50, 0))  # type: ignore[no-untyped-call]
    moved = [(instance.x_mm, instance.y_mm) for instance in page.plan().instances]
    assert moved[0][0] != pytest.approx(before[0][0])
    assert moved[0][0] - before[0][0] == pytest.approx(moved[1][0] - before[1][0])
    assert moved[0][1] - before[0][1] == pytest.approx(moved[1][1] - before[1][1])
    assert page.plan().instances[0].group_id == group_id

    selected = tuple(page.plan().instances)
    center = (
        (selected[0].x_mm + selected[1].x_mm) / 2,
        (selected[0].y_mm + selected[1].y_mm) / 2,
    )
    page._rotated([instance.id for instance in selected], center[0], center[1], 35)
    turned = page.plan().instances
    assert turned[0].rotation_z_deg == pytest.approx(35)
    assert turned[1].rotation_z_deg == pytest.approx(35)
    assert turned[0].group_id == group_id and turned[1].group_id == group_id
    assert math.isclose(_span(selected[0], center), _span(turned[0], center), abs_tol=0.05)
    page.undo()
    page.save()
    page.discard()
    restored = page.plan().instances
    assert [instance.group_id for instance in restored] == [group_id, group_id]
    restored_ids = {instance.id for instance in restored}
    _select_ids(page, restored_ids)
    _key(qtbot, page, Qt.Key.Key_C, Qt.KeyboardModifier.ControlModifier)
    _key(qtbot, page, Qt.Key.Key_V, Qt.KeyboardModifier.ControlModifier)
    clones = [instance for instance in page.plan().instances if instance.id not in restored_ids]
    assert len(clones) == 2
    assert clones[0].group_id is not None
    assert clones[0].group_id == clones[1].group_id
    assert clones[0].group_id != group_id
    kept = {instance.group_id for instance in page.plan().instances if instance.id in restored_ids}
    assert kept == {group_id}
    page.undo()
    _select_ids(page, restored_ids)
    _key(qtbot, page, Qt.Key.Key_G, Qt.KeyboardModifier.ControlModifier)
    assert all(instance.group_id is None for instance in page.plan().instances)
    page.undo()
    assert page.plan().instances[0].group_id == group_id
    _select_ids(page, {page.plan().instances[0].id})
    page.delete_selected()
    assert len(page.plan().instances) == 1
    assert page.plan().instances[0].group_id is None


def test_backup_restores_groups_and_the_start_straight(tmp_path: Path) -> None:
    source_dir = tmp_path / "source"
    source_dir.mkdir()
    source = Runtime.create(
        AppConfig(database_path=source_dir / "slot_racing.db", language="de"),
        config_path=source_dir / "config.json",
    )
    try:
        tracks = source.services.get(TrackService)
        planner = source.services.get(TrackPlannerService)
        track = tracks.create_track(TrackInput(name="Gruppe", lane_count=2))
        part = next(
            record
            for record in planner.list_parts()
            if record.spec.article_number == "20020601"
            and record.spec.system == "Carrera Digital 132"
        )
        catalog = {part.id: part.spec}
        plan = place_instance(empty_plan(track.id), part.id, part.spec, 0, 0, 0, catalog)
        plan = place_instance(plan, part.id, part.spec, 500, 0, 0, catalog)
        plan = toggle_group(plan, [instance.id for instance in plan.instances])
        plan = set_start_straight(plan, plan.instances[0].id, True, catalog)
        planner.save(plan)
        archive = create_backup(source.database, source.config, tmp_path / "backups")
        track_id = int(track.id)
        group_id = plan.instances[0].group_id
    finally:
        source.shutdown()

    other_dir = tmp_path / "other"
    other_dir.mkdir()
    runtime = Runtime.create(
        AppConfig(database_path=other_dir / "slot_racing.db", language="de"),
        config_path=other_dir / "config.json",
    )
    try:
        restore_backup(
            archive,
            runtime.database,
            runtime.config,
            runtime.config_path,
            backup_directory=other_dir / "backups",
        )
        planner = runtime.services.get(TrackPlannerService)
        loaded = planner.load(TrackId(track_id))
        assert {instance.group_id for instance in loaded.instances} == {group_id}
        starts = [instance for instance in loaded.instances if instance.start_straight]
        assert len(starts) == 1
        assert starts[0].x_mm == pytest.approx(0)
        assert loaded.instances[0].part_id == loaded.instances[1].part_id
    finally:
        runtime.shutdown()


def test_carrera_evolution_parts_are_seeded_beside_digital(env: Env) -> None:
    planner = _planner(env)
    evolution = [
        record for record in planner.list_parts() if record.spec.system == "Carrera Evolution"
    ]
    by_article = {record.spec.article_number: record.spec for record in evolution}
    assert set(by_article) >= {
        "20020601",
        "20020611",
        "20020612",
        "20020577",
        "20020571",
        "20020572",
        "20020573",
        "20020578",
        "20020587",
        "20020560",
    }
    assert all(spec.scale == "1:32" for spec in by_article.values())
    assert by_article["20020601"].category == STRAIGHT
    assert by_article["20020601"].length_mm == pytest.approx(345)
    assert by_article["20020571"].category == CURVE
    assert by_article["20020571"].radius_mm == pytest.approx(300)
    assert by_article["20020571"].angle_deg == pytest.approx(60)
    assert by_article["20020560"].category == BORDER
    digital = next(
        record
        for record in planner.list_parts()
        if record.spec.article_number == "20020601" and record.spec.system == "Carrera Digital 132"
    )
    same_article = next(
        record.id for record in evolution if record.spec.article_number == "20020601"
    )
    assert digital.id != same_article
    assert "manufacturer" not in digital.spec.__dataclass_fields__


def _select_ids(page: PlannerPage, ids: set[str]) -> None:
    page.canvas.scene().clearSelection()
    for item in _instances(page):
        item.setSelected(item.item_id in ids)


def _pluses(page: PlannerPage) -> list[PlusItem]:
    return [item for item in page.canvas.scene().items() if isinstance(item, PlusItem)]


def _joints_meet(
    left: PartInstance, right: PartInstance, connectors: tuple[ConnectorSpec, ...]
) -> bool:
    points_left = [world_xy(left, connector.x_mm, connector.y_mm) for connector in connectors]
    points_right = [world_xy(right, connector.x_mm, connector.y_mm) for connector in connectors]
    for start in points_left:
        for end in points_right:
            if math.hypot(start[0] - end[0], start[1] - end[1]) < 1:
                return True
    return False


def _pixels(page: PlannerPage) -> list[int]:
    image = QImage(64, 64, QImage.Format.Format_RGB32)
    painter = QPainter(image)
    page.canvas.scene().render(painter, QRectF(0, 0, 64, 64), QRectF(0, 0, 64, 64))
    painter.end()
    return [image.pixel(x, y) for x in range(0, 64, 4) for y in range(0, 64, 4)]


def _scales(page: PlannerPage) -> list[str]:
    found: list[str] = []
    for row in range(page.library.count()):
        item = page.library.item(row)
        card = None if item is None else page.library.itemWidget(item)
        label = None if card is None else card.findChild(QLabel, "planner-part-scale")
        if label is not None:
            found.append(label.text())
    return found


def _articles(page: PlannerPage) -> list[str]:
    found: list[str] = []
    for row in range(page.library.count()):
        item = page.library.item(row)
        if item is not None:
            found.append(item.text())
    return found


def _contains(articles: list[str], article: str) -> bool:
    return any(article in text for text in articles)


def _custom(
    planner: TrackPlannerService,
    article: str,
    scale: str,
    name: str,
    category: str,
    *,
    length: float,
    lanes: int,
) -> PartSpec:
    del planner
    spec = build_part(
        system="Eigenbau",
        article_number=article,
        scale=scale,
        name=name,
        category=category,
        length_mm=length,
        width_mm=None,
        height_mm=None,
        radius_mm=None,
        angle_deg=None,
        lane_count=lanes,
    )
    return spec


def _span(instance: PartInstance, center: tuple[float, float]) -> float:
    return math.hypot(instance.x_mm - center[0], instance.y_mm - center[1])
