"""Parametric part editor: create, edit, duplicate, snap and keep saved plans."""

from __future__ import annotations

from dataclasses import replace
from typing import Literal

import pytest
from PySide6.QtCore import QTimer
from PySide6.QtWidgets import (
    QApplication,
    QDialog,
    QLabel,
    QMessageBox,
    QPushButton,
    QWidget,
)
from pytestqt.qtbot import QtBot
from sqlalchemy import select

from slot_racing.core.errors import ValidationError
from slot_racing.modules.track_planner.attachment import attachment_fits
from slot_racing.modules.track_planner.document import (
    attach_accessory,
    empty_plan,
    move_instance,
    place_instance,
)
from slot_racing.modules.track_planner.geometry import LineSegment, path_length_mm
from slot_racing.modules.track_planner.models import TrackPartDefinition
from slot_racing.modules.track_planner.packages import PackageContentSpec, PackageSpec
from slot_racing.modules.track_planner.parametric import (
    BODY_BORDER,
    BODY_CURVE,
    BODY_STRAIGHT,
    DIRECTION_LEFT,
    DIRECTION_RIGHT,
    SIDE_BOTH,
    PartDraft,
    draft_from_spec,
    spec_from_draft,
    with_metadata,
)
from slot_racing.modules.track_planner.parts import (
    BORDER,
    CURVE,
    ORIGIN_CATALOG,
    ORIGIN_CUSTOM,
    ORIGIN_MODIFIED,
    STANDARD_CURVE_ARTICLE,
    STANDARD_STRAIGHT_ARTICLE,
    STRAIGHT,
    TRACK,
    PartInstance,
    PartRecord,
    Pose,
    SlotPath,
    SlotSpan,
    build_part,
    driving_paths,
    join_pose,
    rails_can_join,
    same_geometry,
    snap_pose,
    standard_catalog,
    straight_connectors,
)
from slot_racing.modules.track_planner.service import TrackPlannerService
from slot_racing.modules.track_planner.ui.collection_dialog import CollectionDialog
from slot_racing.modules.track_planner.ui.library_dialog import PartDialog
from slot_racing.modules.track_planner.ui.library_manager import LibraryManager
from slot_racing.modules.track_planner.ui.page import PlannerPage
from slot_racing.modules.track_planner.ui.part_editor import offer_adoption
from slot_racing.modules.tracks.models import TrackLayout
from tests.modules.conftest import Env
from tests.modules.test_part_revisions import _article, _catalog
from tests.modules.test_track_parts import _library_row, _planner, _select
from tests.modules.test_ui_management import open_page


def test_an_editable_catalogue_part_roundtrips_without_a_new_body() -> None:
    for spec in standard_catalog():
        draft = draft_from_spec(spec)
        if draft is None:
            kept = with_metadata(
                spec,
                article_number=spec.article_number,
                scale=spec.scale,
                name=spec.name,
                manufacturer=spec.manufacturer,
                description=spec.description,
            )
            assert same_geometry(kept, spec)
            continue
        rebuilt = spec_from_draft(
            draft,
            article_number=spec.article_number,
            scale=spec.scale,
            name=spec.name,
            manufacturer=spec.manufacturer,
            description=spec.description,
            height_mm=spec.height_mm,
        )
        assert same_geometry(rebuilt, spec), spec.name


def test_a_new_straight_curve_and_border_save_reload_and_dock(env: Env) -> None:
    planner = _planner(env)
    straight = _save_draft(
        planner,
        _draft(length_mm=220.0),
        article="EB-G",
        name="Eigene Gerade",
        manufacturer="Werkstatt",
        description="Zwei Spuren",
    )
    assert straight.origin == ORIGIN_CUSTOM
    assert straight.spec.length_mm == pytest.approx(220)
    assert straight.spec.connectors == straight_connectors(220.0, 2)
    assert len(straight.spec.connectors) == 2
    assert all(joint.kind == TRACK for joint in straight.spec.connectors)
    assert straight.spec.slot_paths == ()
    loaded = planner.library.require(straight.id)
    assert loaded.spec.name == "Eigene Gerade"
    assert path_length_mm(driving_paths(loaded.spec)[0].segments) == pytest.approx(220)

    curve = _save_draft(
        planner,
        _draft(
            category=CURVE,
            body=BODY_CURVE,
            length_mm=None,
            radius_mm=300.0,
            angle_deg=60.0,
        ),
        article="EB-K",
        name="Eigene Kurve",
    )
    assert curve.spec.radius_mm == pytest.approx(300)
    assert curve.spec.angle_deg == pytest.approx(60)
    assert curve.spec.slot_paths == ()

    border = _save_draft(
        planner,
        _draft(
            category=BORDER,
            body=BODY_BORDER,
            length_mm=220.0,
            width_mm=40.0,
            border_side=SIDE_BOTH,
        ),
        article="EB-R",
        name="Eigener Rand",
    )
    assert border.spec.attachment is not None
    assert border.spec.connectors == ()
    assert attachment_fits(border.spec, straight.spec, "left")
    assert attachment_fits(border.spec, straight.spec, "right")
    assert not attachment_fits(border.spec, straight.spec, "outer")
    assert not attachment_fits(border.spec, curve.spec, "left")

    track = env.track("Eigenbau", lanes=2)
    catalog = {straight.id: straight.spec, curve.id: curve.spec, border.id: border.spec}
    plan = place_instance(empty_plan(track.id), straight.id, straight.spec, 0, 0, 0, catalog)
    host = plan.instances[0].id
    plan = attach_accessory(plan, host, border.id, border.spec, "right", catalog)
    seated = join_pose(plan.instances[0], straight.spec.connectors[1], curve.spec.connectors[0])
    plan = place_instance(
        plan, curve.id, curve.spec, seated.x_mm + 8, seated.y_mm, seated.rotation_z_deg, catalog
    )
    snapped = next(item for item in plan.instances if item.part_id == curve.id)
    assert snapped.x_mm == pytest.approx(seated.x_mm)
    assert snapped.rotation_z_deg == pytest.approx(seated.rotation_z_deg)
    planner.save(plan)
    again = planner.load(track.id)
    by_part = {item.part_id: item for item in again.instances}
    assert set(by_part) == {straight.id, border.id, curve.id}
    assert by_part[border.id].attachment_slot == "right"
    assert by_part[border.id].host_id == host
    assert by_part[curve.id].x_mm == pytest.approx(seated.x_mm)


def test_pitch_offsets_and_a_left_curve_stay_on_the_shared_path(env: Env) -> None:
    planner = _planner(env)
    pitched = _save_draft(
        planner,
        _draft(length_mm=180.0, pitch_mm=80.0),
        article="EB-P",
        name="Enger Abstand",
    )
    grooves = driving_paths(pitched.spec)
    first, second = grooves[0].segments[0], grooves[1].segments[0]
    assert isinstance(first, LineSegment)
    assert isinstance(second, LineSegment)
    assert first.y0 == pytest.approx(-40)
    assert second.y0 == pytest.approx(40)
    shifted = _save_draft(
        planner,
        _draft(length_mm=180.0, offsets_mm=(-30.0, 50.0)),
        article="EB-O",
        name="Versetzte Spuren",
    )
    lanes = driving_paths(shifted.spec)
    low, high = lanes[0].segments[0], lanes[1].segments[0]
    assert isinstance(low, LineSegment)
    assert isinstance(high, LineSegment)
    assert low.y0 == pytest.approx(-30)
    assert high.y0 == pytest.approx(50)

    left = _save_draft(
        planner,
        _draft(
            category=CURVE,
            body=BODY_CURVE,
            length_mm=None,
            radius_mm=300.0,
            angle_deg=60.0,
            direction=DIRECTION_LEFT,
        ),
        article="EB-L",
        name="Linkskurve",
    )
    strip = _save_draft(
        planner,
        _draft(
            category=BORDER,
            body=BODY_BORDER,
            length_mm=None,
            radius_mm=300.0,
            angle_deg=60.0,
            direction=DIRECTION_LEFT,
            width_mm=40.0,
            border_side="outer",
        ),
        article="EB-LA",
        name="Linkskurvenrand",
    )
    assert attachment_fits(strip.spec, left.spec, "outer")
    assert not attachment_fits(strip.spec, left.spec, "inner")
    straight = _save_draft(planner, _draft(length_mm=200.0), article="EB-S", name="Dockgerade")
    target = PartInstance("rail", straight.id, 0, 0)
    pose = join_pose(target, straight.spec.connectors[1], left.spec.connectors[0])
    snapped = snap_pose(
        left.spec,
        Pose(pose.x_mm + 4, pose.y_mm, pose.rotation_z_deg),
        ((target, straight.spec),),
        snap_mm=30,
        grid_mm=None,
    )
    assert snapped.x_mm == pytest.approx(pose.x_mm)
    assert snapped.y_mm == pytest.approx(pose.y_mm)
    near = snap_pose(
        straight.spec,
        Pose(12, 0, 0),
        ((PartInstance("strip", strip.id, 0, 40), strip.spec),),
        snap_mm=500,
        grid_mm=None,
    )
    assert near.x_mm == pytest.approx(12)
    border_joint = build_part(
        article_number="EB-BJ",
        scale="1:24",
        name="Rand mit Stirn",
        category=BORDER,
        length_mm=200,
        width_mm=None,
        height_mm=None,
        radius_mm=None,
        angle_deg=None,
        lane_count=2,
    )
    assert not rails_can_join(straight.spec.connectors[0], border_joint.connectors[0])


def test_invalid_measures_are_rejected_and_a_broken_path_is_not_replaced(env: Env) -> None:
    with pytest.raises(ValidationError) as empty_name:
        spec_from_draft(
            _draft(),
            article_number="EB",
            scale="1:24",
            name="  ",
            manufacturer="",
            description="",
            height_mm=None,
        )
    assert empty_name.value.key == "error.planner.editor.name"
    with pytest.raises(ValidationError) as empty_article:
        spec_from_draft(
            _draft(),
            article_number="  ",
            scale="1:24",
            name="Name",
            manufacturer="",
            description="",
            height_mm=None,
        )
    assert empty_article.value.key == "error.planner.editor.article"
    with pytest.raises(ValidationError) as bad_length:
        spec_from_draft(
            _draft(length_mm=0),
            article_number="EB",
            scale="1:24",
            name="Name",
            manufacturer="",
            description="",
            height_mm=None,
        )
    assert bad_length.value.key == "error.planner.editor.length"
    with pytest.raises(ValidationError) as bad_angle:
        spec_from_draft(
            _draft(category=CURVE, body=BODY_CURVE, length_mm=None, radius_mm=300, angle_deg=400),
            article_number="EB",
            scale="1:24",
            name="Name",
            manufacturer="",
            description="",
            height_mm=None,
        )
    assert bad_angle.value.key == "error.planner.editor.angle"
    with pytest.raises(ValidationError) as bad_offsets:
        spec_from_draft(
            _draft(offsets_mm=(float("nan"), 40.0)),
            article_number="EB",
            scale="1:24",
            name="Name",
            manufacturer="",
            description="",
            height_mm=None,
        )
    assert bad_offsets.value.key == "error.planner.editor.offsets"

    planner = _planner(env)
    created = _save_draft(planner, _draft(length_mm=200), article="EB-BAD", name="Beschädigt")
    with env.runtime.database.session() as session:
        row = session.get(TrackPartDefinition, created.id)
        assert row is not None
        row.slot_paths = [{"kind": "slot", "spans": "broken"}]
    reloaded = planner.library.require(created.id)
    assert reloaded.geometry_corrupt is True
    with pytest.raises(ValidationError) as refused:
        planner.update_part(created.id, replace(reloaded.spec, length_mm=250))
    assert refused.value.key == "error.planner.geometry"
    renamed = planner.update_part(created.id, replace(reloaded.spec, name="Text bleibt"))
    assert renamed.spec.name == "Text bleibt"
    assert renamed.spec.length_mm == pytest.approx(200)
    with env.runtime.database.session() as session:
        stored = session.get(TrackPartDefinition, created.id)
        assert stored is not None
        assert stored.slot_paths == [{"kind": "slot", "spans": "broken"}]


def test_metadata_and_measures_edit_a_carrera_part_without_reseeding(env: Env) -> None:
    planner = _planner(env)
    straight = _article(planner, STANDARD_STRAIGHT_ARTICLE)
    curve = _article(planner, STANDARD_CURVE_ARTICLE)
    renamed = planner.update_part(
        straight.id,
        replace(straight.spec, name="Umbenannt", manufacturer="Carrera", description="Notiz"),
    )
    assert renamed.id == straight.id
    assert renamed.origin == ORIGIN_CATALOG
    assert renamed.revision == 1
    changed = planner.update_part(
        straight.id, replace(planner.library.require(straight.id).spec, length_mm=400)
    )
    assert changed.id == straight.id
    assert changed.origin == ORIGIN_MODIFIED
    assert changed.spec.length_mm == pytest.approx(400)
    curved = planner.update_part(curve.id, replace(curve.spec, radius_mm=320, angle_deg=45))
    assert curved.spec.radius_mm == pytest.approx(320)
    assert curved.spec.angle_deg == pytest.approx(45)
    planner.library.ensure_seed()
    assert planner.library.require(straight.id).spec.length_mm == pytest.approx(400)
    assert planner.library.require(curve.id).spec.radius_mm == pytest.approx(320)
    original = next(
        spec for spec in standard_catalog() if spec.article_number == STANDARD_CURVE_ARTICLE
    )
    assert original.radius_mm == pytest.approx(300)
    assert original.angle_deg == pytest.approx(60)


def test_a_locked_body_and_a_bezier_groove_survive_a_rename(env: Env) -> None:
    planner = _planner(env)
    switch = _article(planner, "20020517")
    assert draft_from_spec(switch.spec) is None
    renamed = planner.update_part(
        switch.id, replace(switch.spec, name="Weiche bleibt", description="Hinweis")
    )
    assert same_geometry(renamed.spec, switch.spec)
    assert renamed.origin == ORIGIN_CATALOG
    custom = planner.add_part(
        replace(
            switch.spec,
            article_number="EB-BZ",
            name="Bézier",
            slot_paths=(SlotPath((SlotSpan.bezier(0, 0, 40, 10, 80, -10, 120, 0),), lane=1),),
            connectors=straight_connectors(120.0, 2),
            length_mm=120.0,
        )
    )
    assert draft_from_spec(custom.spec) is None
    kept = planner.update_part(custom.id, replace(custom.spec, description="unverändert"))
    assert kept.spec.slot_paths == custom.spec.slot_paths
    assert kept.lineage_id == custom.lineage_id


def test_a_duplicate_is_a_new_lineage_and_later_edits_do_not_revise_the_original(env: Env) -> None:
    planner = _planner(env)
    original = _article(planner, STANDARD_STRAIGHT_ARTICLE)
    copied = planner.add_part(
        replace(
            original.spec,
            name="Kopie der Geraden",
            article_number=original.spec.article_number,
        )
    )
    assert copied.lineage_id != original.lineage_id
    assert copied.origin == ORIGIN_CUSTOM
    assert same_geometry(copied.spec, original.spec)
    with env.runtime.database.session() as session:
        row = session.get(TrackPartDefinition, copied.id)
        assert row is not None and row.catalog_key is None
    revised = planner.update_part(copied.id, replace(copied.spec, length_mm=500))
    assert revised.lineage_id == copied.lineage_id
    assert planner.library.require(original.id).spec.length_mm == pytest.approx(345)
    assert planner.library.require(original.id).origin == ORIGIN_CATALOG
    assert revised.id == copied.id


def test_a_border_does_not_take_a_rail_joint_or_block_one(env: Env) -> None:
    planner = _planner(env)
    rail = _save_draft(planner, _draft(length_mm=345.0), article="EB-RAIL", name="Schiene")
    other = _save_draft(planner, _draft(length_mm=345.0), article="EB-RAIL2", name="Zweite Schiene")
    border = _save_draft(
        planner,
        _draft(category=BORDER, body=BODY_BORDER, length_mm=345.0, width_mm=40.0),
        article="EB-STRIP",
        name="Streifen",
    )
    host = PartInstance("host", rail.id, 0, 0)
    strip = PartInstance("strip", border.id, 0, 120)
    pose = join_pose(host, rail.spec.connectors[1], other.spec.connectors[0])
    snapped = snap_pose(
        other.spec,
        Pose(pose.x_mm + 6, pose.y_mm + 4, pose.rotation_z_deg),
        ((host, rail.spec), (strip, border.spec)),
        snap_mm=40,
        grid_mm=None,
    )
    assert snapped.x_mm == pytest.approx(pose.x_mm)
    assert snapped.rotation_z_deg == pytest.approx(pose.rotation_z_deg)
    idle = snap_pose(
        border.spec,
        Pose(pose.x_mm, pose.y_mm, 0),
        ((host, rail.spec),),
        snap_mm=500,
        grid_mm=None,
    )
    assert idle.x_mm == pytest.approx(pose.x_mm)
    assert not attachment_fits(border.spec, rail.spec, "east")


def test_editing_a_used_part_forks_and_adoption_is_explicit(qtbot: QtBot, env: Env) -> None:
    planner = _planner(env)
    straight = _article(planner, STANDARD_STRAIGHT_ARTICLE)
    first = env.track("Eins", lanes=2)
    second = env.track("Zwei", lanes=2)
    catalog = _catalog(straight)
    planner.set_stock(straight.id, 4)
    planner.add_package(
        PackageSpec(
            "Carrera",
            "PK-EDIT",
            "Eigene Kiste",
            (PackageContentSpec(straight.spec.name, straight.spec.article_number, 2),),
        )
    )
    planner.save(
        place_instance(empty_plan(first.id), straight.id, straight.spec, 10, 20, 5, catalog)
    )
    planner.save(
        place_instance(empty_plan(second.id), straight.id, straight.spec, 30, 40, 0, catalog)
    )
    moved = planner.load(first.id)
    planner.save(move_instance(moved, moved.instances[0].id, 12, 20))
    revised = planner.update_part(straight.id, replace(straight.spec, length_mm=410))
    assert revised.revision == 2
    assert planner.load(first.id).instances[0].part_id == straight.id
    assert planner.load(second.id).instances[0].part_id == straight.id
    assert planner.stock_quantities()[revised.id] == 4
    package = next(item for item in planner.list_packages() if item.article_number == "PK-EDIT")
    assert package.contents[0].part_id == revised.id
    fresh = place_instance(
        empty_plan(first.id),
        revised.id,
        revised.spec,
        0,
        0,
        0,
        {revised.id: revised.spec},
    )
    assert fresh.instances[0].part_id == revised.id
    parent = QWidget()
    qtbot.addWidget(parent)
    parent.show()

    def confirm() -> None:
        dialog = QApplication.activeModalWidget()
        assert isinstance(dialog, QDialog)
        assert dialog.objectName() == "part-adopt"
        kept = dialog.findChild(QLabel, "part-adopt-kept")
        assert kept is not None
        assert kept.text() == "Bestehende Strecken behalten zunächst die bisherige Geometrie."
        current = dialog.findChild(QLabel, "part-adopt-current")
        assert current is not None and "2" in current.text()
        choice = dialog.findChild(QPushButton, f"part-adopt-track-{int(first.id)}")
        assert isinstance(choice, QPushButton)
        choice.setChecked(True)
        confirm_button = dialog.findChild(QPushButton, "part-adopt-confirm")
        assert isinstance(confirm_button, QPushButton)
        confirm_button.click()

    QTimer.singleShot(0, confirm)
    assert offer_adoption(parent, env.runtime.translator, planner, revised) == 1
    adopted = planner.load(first.id)
    assert adopted.instances[0].part_id == revised.id
    pose = adopted.instances[0]
    assert (pose.x_mm, pose.y_mm, pose.rotation_z_deg) == (
        12,
        20,
        5,
    )
    assert planner.load(second.id).instances[0].part_id == straight.id
    with env.runtime.database.session() as session:
        archives = session.scalars(select(TrackLayout).where(TrackLayout.name != "plan")).all()
    assert archives
    assert all(
        instance["part_id"] == straight.id
        for layout in archives
        for instance in layout.data["instances"]
    )
    parent.close()


def test_the_editor_saves_cancels_and_refreshes_the_library(qtbot: QtBot, env: Env) -> None:
    planner = _planner(env)
    track = env.track("Dialog", lanes=2)
    window, page = open_page(qtbot, env, "track_planner")
    window.show()
    assert isinstance(page, PlannerPage)
    _select(page, track.id)
    page.library.setCurrentRow(-1)
    page.edit_part.click()
    assert "Bibliothek" in page.status.text()
    page.duplicate_part.click()
    assert "Bibliothek" in page.status.text()

    def create() -> None:
        dialog = QApplication.activeModalWidget()
        assert isinstance(dialog, PartDialog)
        dialog.article.setText("EB-UI")
        dialog.name.setText("Dialogteil")
        dialog.manufacturer.setText("Eigen")
        dialog.description.setPlainText("Notiz")
        dialog.length.setValue(210)
        assert dialog.preview._spec is not None
        before = dialog.preview._spec.length_mm
        dialog.length.setValue(260)
        dialog.preview.repaint()
        assert dialog.preview._spec is not None
        assert dialog.preview._spec.length_mm == pytest.approx(260)
        assert before != dialog.preview._spec.length_mm
        dialog.length.setValue(0)
        dialog.preview.repaint()
        assert dialog.preview._spec.length_mm == pytest.approx(260)
        save = dialog.findChild(QPushButton, "part-editor-save")
        assert isinstance(save, QPushButton)
        dialog.length.setValue(210)
        save.click()

    QTimer.singleShot(0, create)
    page.add_part.click()
    row = _library_row(page, "EB-UI")
    page.library.setCurrentRow(row)
    page.place_part.click()
    assert page.plan().instances[0].part_id == _part_id(planner, "EB-UI")
    page.undo_button.click()
    assert page.plan().instances == ()
    page.redo_button.click()
    assert len(page.plan().instances) == 1

    def rename() -> None:
        dialog = QApplication.activeModalWidget()
        assert isinstance(dialog, PartDialog)
        assert dialog.length.value() == pytest.approx(210)
        dialog.name.setText("Dialogteil neu")
        button = dialog.findChild(QPushButton, "part-editor-save")
        assert isinstance(button, QPushButton)
        button.click()

    page.library.setCurrentRow(_library_row(page, "EB-UI"))
    QTimer.singleShot(0, rename)
    page.edit_part.click()
    assert planner.library.require(_part_id(planner, "EB-UI")).spec.name == "Dialogteil neu"

    def cancel() -> None:
        dialog = QApplication.activeModalWidget()
        assert isinstance(dialog, PartDialog)
        dialog.name.setText("Nicht speichern")

        def refuse() -> None:
            box = QApplication.activeModalWidget()
            assert isinstance(box, QMessageBox)
            no = box.button(QMessageBox.StandardButton.No)
            assert no is not None
            no.click()

        def allow() -> None:
            box = QApplication.activeModalWidget()
            assert isinstance(box, QMessageBox)
            yes = box.button(QMessageBox.StandardButton.Yes)
            assert yes is not None
            yes.click()

        QTimer.singleShot(0, refuse)
        dialog.reject()
        assert dialog.isVisible()
        QTimer.singleShot(0, allow)
        dialog.reject()

    QTimer.singleShot(0, cancel)
    page.edit_part.click()
    assert planner.library.require(_part_id(planner, "EB-UI")).spec.name == "Dialogteil neu"


def test_the_editor_reports_validation_and_keeps_a_locked_preview(qtbot: QtBot, env: Env) -> None:
    planner = _planner(env)
    planner.list_parts()
    dialog = PartDialog(env.runtime.translator, planner)
    qtbot.addWidget(dialog)
    dialog.show()
    dialog.article.setText("EB-VAL")
    dialog.accept()
    assert dialog.result() != QDialog.DialogCode.Accepted
    assert "Bezeichnung" in dialog.message.text()
    dialog.name.setText("Gültig")
    dialog.article.setText("")
    dialog.accept()
    assert "Artikelnummer" in dialog.message.text()
    dialog.article.setText(STANDARD_STRAIGHT_ARTICLE)
    dialog.name.setText("Standardgerade")
    dialog.accept()
    assert dialog.result() != QDialog.DialogCode.Accepted
    assert "existiert bereits" in dialog.message.text()
    dialog.length.setValue(0)
    dialog.name.setText("Kurz")
    dialog.article.setText("EB-VAL")
    dialog.accept()
    assert "Länge" in dialog.message.text()

    def discard() -> None:
        box = QApplication.activeModalWidget()
        assert isinstance(box, QMessageBox)
        yes = box.button(QMessageBox.StandardButton.Yes)
        assert yes is not None
        yes.click()

    QTimer.singleShot(0, discard)
    dialog.close()

    switch = _article(planner, "20020517")
    editor = PartDialog(env.runtime.translator, planner, switch)
    qtbot.addWidget(editor)
    editor.show()
    assert not editor.length.isEnabled()
    assert editor.notice.isVisible()
    assert "nicht verändert" in editor.notice.text()
    editor.preview.repaint()
    assert editor.preview._spec is not None
    assert editor.preview._spec.slot_paths == switch.spec.slot_paths
    editor.description.setPlainText("Nur Text")
    editor.accept()
    stored = planner.library.require(switch.id)
    assert stored.spec.description == "Nur Text"
    assert same_geometry(stored.spec, switch.spec)


def test_library_manager_creates_duplicates_and_collection_edits(qtbot: QtBot, env: Env) -> None:
    planner = _planner(env)
    source = _save_draft(planner, _draft(length_mm=180), article="EB-SRC", name="Quelle")
    manager = LibraryManager(env.runtime.translator, planner)
    qtbot.addWidget(manager)
    manager.show()

    def duplicate() -> None:
        dialog = QApplication.activeModalWidget()
        assert isinstance(dialog, PartDialog)
        assert dialog.name.text().startswith("Kopie von")
        assert dialog.origin.text() == "Eigenbau"
        dialog.article.setText("EB-COPY")
        dialog.name.setText("Unabhängige Kopie")
        save = dialog.findChild(QPushButton, "part-editor-save")
        assert isinstance(save, QPushButton)
        save.click()

    manager.select_part(source.id)
    QTimer.singleShot(0, duplicate)
    manager.duplicate_button.click()
    copied = _part_id(planner, "EB-COPY")
    assert planner.library.require(copied).lineage_id != source.lineage_id
    assert planner.library.require(source.id).spec.name == "Quelle"
    manager.adopt_button.click()
    assert "ältere Geometrie" in manager.status.text()

    track = env.track("Sammlung", lanes=2)
    window, page = open_page(qtbot, env, "track_planner")
    window.show()
    assert isinstance(page, PlannerPage)
    _select(page, track.id)
    shown: list[str] = []

    def edit_from_collection() -> None:
        collection = QApplication.activeModalWidget()
        assert isinstance(collection, CollectionDialog)

        def save_name() -> None:
            editor = QApplication.activeModalWidget()
            assert isinstance(editor, PartDialog)
            editor.name.setText("Aus der Sammlung")
            button = editor.findChild(QPushButton, "part-editor-save")
            assert isinstance(button, QPushButton)
            button.click()

            def after_editor() -> None:
                row = collection.findChild(QWidget, f"collection-row-{source.id}")
                label = None if row is None else row.findChild(QLabel, "collection-name")
                shown.append("" if label is None else label.text())
                close = collection.findChild(QPushButton, "collection-close")
                assert isinstance(close, QPushButton)
                close.click()

            QTimer.singleShot(0, after_editor)

        QTimer.singleShot(0, save_name)
        edit = collection.findChild(QPushButton, f"collection-edit-{source.id}")
        assert isinstance(edit, QPushButton)
        edit.click()

    QTimer.singleShot(0, edit_from_collection)
    page.manage_stock.click()
    assert shown == ["Aus der Sammlung"]
    assert planner.library.require(source.id).spec.name == "Aus der Sammlung"


def test_a_corrupt_duplicate_cannot_be_saved(qtbot: QtBot, env: Env) -> None:
    planner = _planner(env)
    created = _save_draft(planner, _draft(length_mm=200), article="EB-CR", name="Kaputt")
    with env.runtime.database.session() as session:
        row = session.get(TrackPartDefinition, created.id)
        assert row is not None
        row.slot_paths = [{"kind": "slot"}]
    record = planner.library.require(created.id)
    dialog = PartDialog(env.runtime.translator, planner, record, duplicate=True)
    qtbot.addWidget(dialog)
    dialog.show()
    save = dialog.findChild(QPushButton, "part-editor-save")
    assert isinstance(save, QPushButton)
    assert not save.isEnabled()
    dialog.preview.repaint()
    before = len(planner.list_parts())
    dialog.accept()
    assert dialog.result() != QDialog.DialogCode.Accepted
    assert len(planner.list_parts()) == before
    with env.runtime.database.session() as session:
        stored = session.get(TrackPartDefinition, created.id)
        assert stored is not None
        assert stored.slot_paths == [{"kind": "slot"}]


def _draft(
    *,
    category: str = STRAIGHT,
    body: Literal["straight", "curve", "border"] = BODY_STRAIGHT,
    length_mm: float | None = 345.0,
    radius_mm: float | None = None,
    angle_deg: float | None = 0.0,
    direction: str = DIRECTION_RIGHT,
    lane_count: int = 2,
    pitch_mm: float = 100.0,
    width_mm: float | None = None,
    offsets_mm: tuple[float, ...] | None = None,
    border_side: str = SIDE_BOTH,
    host_lanes: int = 2,
) -> PartDraft:
    return PartDraft(
        category=category,
        body=body,
        length_mm=length_mm,
        radius_mm=radius_mm,
        angle_deg=angle_deg,
        direction=direction,
        lane_count=lane_count,
        pitch_mm=pitch_mm,
        width_mm=width_mm,
        offsets_mm=offsets_mm,
        border_side=border_side,
        host_lanes=host_lanes,
    )


def _save_draft(
    planner: TrackPlannerService,
    draft: PartDraft,
    *,
    article: str,
    name: str,
    manufacturer: str = "",
    description: str = "",
) -> PartRecord:
    spec = spec_from_draft(
        draft,
        article_number=article,
        scale="1:24",
        name=name,
        manufacturer=manufacturer,
        description=description,
        height_mm=None,
    )
    return planner.add_part(spec)


def _part_id(planner: TrackPlannerService, article: str) -> int:
    return next(
        record.id for record in planner.list_parts() if record.spec.article_number == article
    )
