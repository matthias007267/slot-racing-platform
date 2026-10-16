"""Centre, centre rotation and short label are stored on the part itself.

A copy has no source id. Reloading it must not ask the bundled files for
these values, and it must not invent a centre the row never had.
"""

from __future__ import annotations

from dataclasses import replace

from PySide6.QtCore import Qt
from PySide6.QtGui import QImage, QPainter
from PySide6.QtWidgets import QDialog
from pytestqt.qtbot import QtBot
from sqlalchemy import text
from sqlalchemy.engine import Connection

from slot_racing.core.domain import TrackId
from slot_racing.core.storage.database import Database
from slot_racing.modules.track_planner.catalog_import import load_carrera_library
from slot_racing.modules.track_planner.document import (
    add_instance,
    complete_auto_connections,
    empty_plan,
    place_instance,
    place_template,
)
from slot_racing.modules.track_planner.graphic_session import GraphicSession
from slot_racing.modules.track_planner.library import PartLibrary
from slot_racing.modules.track_planner.models import TrackPartDefinition
from slot_racing.modules.track_planner.parts import (
    ACTIVE_CURVE_SOURCE,
    ACTIVE_STRAIGHT_SOURCE,
    ORIGIN_CATALOG,
    ORIGIN_CUSTOM,
    ORIGIN_MODIFIED,
    PartInstance,
    PartRecord,
    PartSpec,
    Pose,
    build_part,
    join_pose,
    origin_from_midpoint,
    snap_pose,
)
from slot_racing.modules.track_planner.service import TrackPlannerService
from slot_racing.modules.track_planner.ui.part_editor import PartDialog
from slot_racing.modules.track_planner.ui.track_paint import paint_part
from tests.modules.conftest import Env
from tests.modules.test_track_parts import _planner

_CURVE = ACTIVE_CURVE_SOURCE
_STRAIGHT = ACTIVE_STRAIGHT_SOURCE
_BORDER = "572355ed-e23f-4ab4-949b-ba507dab1da9"


def test_import_stores_centre_rotation_and_short_label_from_the_file(env: Env) -> None:
    planner = _planner(env)
    curve = _record(planner, _CURVE)
    straight = _record(planner, _STRAIGHT)
    file_curve = _file(_CURVE)
    file_straight = _file(_STRAIGHT)
    assert file_curve.center_y_mm == -297.0
    assert file_curve.center_angle_deg == 0.0
    assert file_curve.short_label == "K1/60"
    assert file_straight.center_x_mm == 0.0
    assert file_straight.center_y_mm == 0.0
    assert file_straight.short_label == "1/1"
    assert curve.spec.center_x_mm == file_curve.center_x_mm
    assert curve.spec.center_y_mm == file_curve.center_y_mm
    assert curve.spec.center_angle_deg == file_curve.center_angle_deg
    assert curve.spec.short_label == file_curve.short_label
    with env.runtime.database.session() as session:
        row = session.get(TrackPartDefinition, curve.id)
        assert row is not None
        assert row.center_x_mm == file_curve.center_x_mm
        assert row.center_y_mm == file_curve.center_y_mm
        assert row.center_angle_deg == file_curve.center_angle_deg
        assert row.short_label == "K1/60"
        straight_row = session.get(TrackPartDefinition, straight.id)
        assert straight_row is not None
        assert straight_row.center_x_mm == 0.0
        assert straight_row.center_y_mm == 0.0
        assert straight_row.short_label == "1/1"


def test_saved_centre_survives_closing_and_opening_the_library(env: Env) -> None:
    planner = _planner(env)
    curve = _record(planner, _CURVE)
    fresh = PartLibrary(env.runtime.database)
    loaded = fresh.require(curve.id)
    assert loaded.spec.center_x_mm == curve.spec.center_x_mm
    assert loaded.spec.center_y_mm == curve.spec.center_y_mm
    assert loaded.spec.center_angle_deg == curve.spec.center_angle_deg
    assert loaded.spec.short_label == curve.spec.short_label
    assert loaded.spec.source_id == _CURVE


def test_a_copy_without_source_id_keeps_the_straight_centre_and_label(env: Env) -> None:
    planner = _planner(env)
    straight = _record(planner, _STRAIGHT)
    copied = _copy(planner, straight, "Kopie Gerade", "EB-COPY-1")
    assert copied.spec.source_id == ""
    assert copied.origin == ORIGIN_CUSTOM
    assert copied.spec.center_x_mm == 0.0
    assert copied.spec.center_y_mm == 0.0
    assert copied.spec.center_angle_deg == 0.0
    assert copied.spec.short_label == "1/1"
    loaded = PartLibrary(env.runtime.database).require(copied.id)
    assert loaded.spec.source_id == ""
    assert loaded.spec.center_x_mm == 0.0
    assert loaded.spec.center_y_mm == 0.0
    assert loaded.spec.short_label == "1/1"
    assert _anchor(loaded.spec) == _anchor(straight.spec)


def test_a_copied_k1_keeps_its_nontrivial_centre(env: Env) -> None:
    planner = _planner(env)
    curve = _record(planner, _CURVE)
    copied = _copy(planner, curve, "Kopie K1", "EB-K1")
    loaded = PartLibrary(env.runtime.database).require(copied.id)
    assert loaded.spec.source_id == ""
    assert loaded.spec.center_x_mm == curve.spec.center_x_mm
    assert loaded.spec.center_y_mm == -297.0
    assert loaded.spec.center_angle_deg == 0.0
    assert loaded.spec.short_label == "K1/60"
    assert loaded.spec.center_y_mm == _file(_CURVE).center_y_mm
    once = origin_from_midpoint(100.0, 50.0, 15.0, loaded.spec.center_x_mm, loaded.spec.center_y_mm)
    twice = origin_from_midpoint(
        once[0], once[1], 15.0, loaded.spec.center_x_mm, loaded.spec.center_y_mm
    )
    file_once = origin_from_midpoint(
        100.0, 50.0, 15.0, _file(_CURVE).center_x_mm, _file(_CURVE).center_y_mm
    )
    assert once == file_once
    assert twice != once


def test_a_custom_part_stores_its_own_centre_without_a_source_file(env: Env) -> None:
    planner = _planner(env)
    spec = replace(
        build_part(
            article_number="EB-OWN",
            scale="1:24",
            name="Eigenes Teil",
            category="straight",
            length_mm=180.0,
            width_mm=None,
            height_mm=None,
            radius_mm=None,
            angle_deg=None,
            lane_count=2,
        ),
        center_x_mm=12.5,
        center_y_mm=-40.25,
        center_angle_deg=33.0,
        short_label="Eigen",
    )
    created = planner.add_part(spec)
    loaded = PartLibrary(env.runtime.database).require(created.id)
    assert loaded.spec.source_id == ""
    assert loaded.origin == ORIGIN_CUSTOM
    assert loaded.spec.center_x_mm == 12.5
    assert loaded.spec.center_y_mm == -40.25
    assert loaded.spec.center_angle_deg == 33.0
    assert loaded.spec.short_label == "Eigen"
    planner.library.ensure_seed()
    again = planner.library.require(created.id)
    assert again.spec.center_x_mm == 12.5
    assert again.spec.center_angle_deg == 33.0
    assert again.spec.short_label == "Eigen"


def test_a_revision_keeps_the_centre_of_the_frozen_body(env: Env) -> None:
    planner = _planner(env)
    curve = _record(planner, _CURVE)
    track = env.track("Revision", lanes=2)
    planner.save(
        place_instance(empty_plan(track.id), curve.id, curve.spec, 8.0, 9.0, 12.0, _one(curve))
    )
    revised = planner.update_part(curve.id, replace(curve.spec, length_mm=400.0))
    assert revised.id != curve.id
    assert revised.revision == 2
    assert revised.origin == ORIGIN_MODIFIED
    assert revised.spec.center_y_mm == curve.spec.center_y_mm
    assert revised.spec.short_label == "K1/60"
    frozen = planner.library.require(curve.id)
    assert frozen.is_current is False
    assert frozen.revision == 1
    assert frozen.spec.center_x_mm == curve.spec.center_x_mm
    assert frozen.spec.center_y_mm == curve.spec.center_y_mm
    assert frozen.spec.center_angle_deg == curve.spec.center_angle_deg
    assert frozen.spec.short_label == "K1/60"
    loaded = planner.load(track.id)
    assert loaded.instances[0].part_id == curve.id
    assert (loaded.instances[0].x_mm, loaded.instances[0].y_mm) == (8.0, 9.0)
    assert loaded.instances[0].rotation_z_deg == 12.0


def test_older_rows_keep_a_documented_fallback_when_the_source_is_gone() -> None:
    database = Database.in_memory()
    database.migrate("0024")
    curve = _file(_CURVE)
    with database.engine.begin() as connection:
        connection.exec_driver_sql("INSERT INTO tracks (name, lane_count) VALUES ('Alt', 2)")
        track_id = connection.exec_driver_sql("SELECT id FROM tracks").scalar_one()
        _insert_legacy(
            connection,
            article="20571",
            name="Kurve K1",
            source_id=_CURVE,
            lineage="a" * 32,
            revision=1,
            current=0,
        )
        current_id = _insert_legacy(
            connection,
            article="20571",
            name="Kurve K1",
            source_id=_CURVE,
            lineage="a" * 32,
            revision=2,
            current=1,
        )
        copy_id = _insert_legacy(
            connection,
            article="EB-ALT",
            name="Alte Kopie",
            source_id="",
            lineage="b" * 32,
            revision=1,
            current=1,
        )
        connection.exec_driver_sql(
            "INSERT INTO track_part_stock (part_id, quantity) VALUES "
            f"({int(current_id)}, 4), ({int(copy_id)}, 2)"
        )
        connection.exec_driver_sql(
            "INSERT INTO track_plan_instances "
            "(id, track_id, part_id, x_mm, y_mm, z_mm, rotation_x_deg, rotation_y_deg, "
            "rotation_z_deg, is_start_straight, sort_order, host_dx, host_dy, host_da) "
            f"VALUES ('legacy-copy', {int(track_id)}, {int(copy_id)}, 12, 34, 0, 0, 0, 56, "
            "0, 0, 0, 0, 0)"
        )
    database.migrate()
    with database.engine.connect() as connection:
        current = connection.execute(
            text(
                "SELECT center_x_mm, center_y_mm, center_angle_deg, short_label, "
                "revision, is_current FROM track_part_definitions WHERE id = :part_id"
            ),
            {"part_id": current_id},
        ).one()
        frozen = connection.execute(
            text(
                "SELECT center_y_mm, short_label, revision, is_current "
                "FROM track_part_definitions WHERE lineage_id = :lineage AND revision = 1"
            ),
            {"lineage": "a" * 32},
        ).one()
        copied = connection.execute(
            text(
                "SELECT center_x_mm, center_y_mm, center_angle_deg, short_label "
                "FROM track_part_definitions WHERE id = :part_id"
            ),
            {"part_id": copy_id},
        ).one()
        pose = connection.execute(
            text("SELECT x_mm, y_mm, rotation_z_deg FROM track_plan_instances")
        ).one()
        stock = {
            int(part_id): int(quantity)
            for part_id, quantity in connection.execute(
                text("SELECT part_id, quantity FROM track_part_stock")
            )
        }
    assert current.center_x_mm == curve.center_x_mm
    assert current.center_y_mm == curve.center_y_mm
    assert current.center_angle_deg == 0.0
    assert current.short_label == "K1/60"
    assert current.revision == 2
    assert current.is_current == 1
    assert frozen.center_y_mm == curve.center_y_mm
    assert frozen.short_label == "K1/60"
    assert frozen.revision == 1
    assert frozen.is_current == 0
    assert (copied.center_x_mm, copied.center_y_mm, copied.center_angle_deg) == (0.0, 0.0, 0.0)
    assert copied.short_label == ""
    assert (pose.x_mm, pose.y_mm, pose.rotation_z_deg) == (12.0, 34.0, 56.0)
    assert stock == {current_id: 4, copy_id: 2}
    loaded = PartLibrary(database).require(copy_id)
    assert loaded.spec.center_x_mm == 0.0
    assert loaded.spec.center_y_mm == 0.0
    assert loaded.spec.short_label == ""
    database.dispose()


def test_a_stored_centre_wins_over_the_reference_file(env: Env) -> None:
    planner = _planner(env)
    curve = _record(planner, _CURVE)
    updated = planner.update_part(
        curve.id,
        replace(curve.spec, center_y_mm=-100.0, center_angle_deg=15.0, short_label="ZZ"),
    )
    assert updated.origin == ORIGIN_CATALOG
    assert updated.id == curve.id
    planner.library.ensure_seed()
    loaded = PartLibrary(env.runtime.database).require(curve.id)
    assert loaded.spec.center_y_mm == -100.0
    assert loaded.spec.center_angle_deg == 15.0
    assert loaded.spec.short_label == "ZZ"
    assert loaded.spec.center_y_mm != _file(_CURVE).center_y_mm
    assert loaded.spec.source_id == _CURVE


def test_a_reloaded_part_paints_the_same_pixels(qapp: object, env: Env) -> None:
    del qapp
    planner = _planner(env)
    curve = _record(planner, _CURVE)
    copied = _copy(planner, curve, "Kopie Bild", "EB-PX")
    loaded = PartLibrary(env.runtime.database).require(copied.id)
    assert _pixels(_paint(curve.spec)) == _pixels(_paint(_file(_CURVE)))
    assert _pixels(_paint(loaded.spec)) == _pixels(_paint(curve.spec))


def test_snap_positions_stay_on_the_stored_centre(env: Env) -> None:
    planner = _planner(env)
    straight = _record(planner, _STRAIGHT)
    curve = _record(planner, _CURVE)
    border = _record(planner, _BORDER)
    digital = _digital(planner)
    copied = _copy(planner, curve, "Kopie Snap", "EB-SNAP")
    loaded = PartLibrary(env.runtime.database).require(copied.id)
    assert _anchor(straight.spec) == (0.0, 0.0)
    assert origin_from_midpoint(10.0, 20.0, 30.0, 0.0, 0.0) == (10.0, 20.0)
    assert _snap(straight.spec, curve.spec) == _snap(_file(_STRAIGHT), _file(_CURVE))
    assert _snap(straight.spec, loaded.spec) == _snap(straight.spec, curve.spec)
    assert _anchor(border.spec) == _anchor(_file(_BORDER))
    assert border.spec.short_label == ""
    assert digital.spec.short_label == _file(digital.spec.source_id).short_label
    assert digital.spec.center_x_mm == _file(digital.spec.source_id).center_x_mm
    assert any(connector.exclusive for connector in digital.spec.connectors)
    host = PartInstance("rail", straight.id, 0.0, 0.0)
    joint = next(connector for connector in straight.spec.connectors if connector.kind == "track")
    moving = next(connector for connector in loaded.spec.connectors if connector.kind == "track")
    seated = join_pose(host, joint, moving)
    file_moving = next(
        connector for connector in _file(_CURVE).connectors if connector.kind == "track"
    )
    assert seated == join_pose(host, joint, file_moving)


def test_a_saved_plan_does_not_move_when_the_centre_is_stored(env: Env) -> None:
    planner = _planner(env)
    curve = _record(planner, _CURVE)
    straight = _record(planner, _STRAIGHT)
    track = env.track("Lage", lanes=2)
    catalog = {curve.id: curve.spec, straight.id: straight.spec}
    plan = place_instance(empty_plan(track.id), straight.id, straight.spec, 0.0, 0.0, 0.0, catalog)
    plan = place_instance(plan, curve.id, curve.spec, 345.0, -12.5, 60.0, catalog)
    planner.save(plan)
    before = [(item.part_id, item.x_mm, item.y_mm, item.rotation_z_deg) for item in plan.instances]
    planner.library.ensure_seed()
    loaded = planner.load(track.id)
    assert [
        (item.part_id, item.x_mm, item.y_mm, item.rotation_z_deg) for item in loaded.instances
    ] == before


def test_stock_is_unchanged_by_storing_the_centre(env: Env) -> None:
    planner = _planner(env)
    curve = _record(planner, _CURVE)
    planner.set_stock(curve.id, 6)
    copied = _copy(planner, curve, "Kopie Bestand", "EB-STK")
    owned = planner.stock_quantities()
    assert owned[curve.id] == 6
    assert copied.id not in owned
    planner.library.ensure_seed()
    assert planner.stock_quantities()[curve.id] == 6
    assert planner.library.require(curve.id).spec.short_label == "K1/60"


def test_autoconnect_uses_the_stored_centre_of_a_copy(env: Env) -> None:
    planner = _planner(env)
    straight = _record(planner, _STRAIGHT)
    curve = _record(planner, _CURVE)
    copied_straight = _copy(planner, straight, "Kopie AC", "EB-AC")
    copied_curve = _copy(planner, curve, "Kopie ACK", "EB-ACK")
    catalog = {record.id: record.spec for record in planner.library.list_active()}
    original = _companions(straight, catalog)
    copied = _companions(copied_straight, catalog)
    assert original
    assert [(source, x_mm, y_mm, angle) for source, x_mm, y_mm, angle in copied] == [
        (source, x_mm, y_mm, angle) for source, x_mm, y_mm, angle in original
    ]
    curve_original = _companions(curve, catalog)
    curve_copy = _companions(copied_curve, catalog)
    assert curve_copy == curve_original
    assert curve_copy


def test_template_placement_uses_the_stored_centre_once(env: Env) -> None:
    planner = _planner(env)
    planner.list_parts()
    template = next(
        item
        for item in planner.list_templates()
        if any(
            _anchor(planner.library.require(member.part_id).spec) != (0.0, 0.0)
            for member in item.members
        )
    )
    members = tuple(
        (member.part_id, member.x_mm, member.y_mm, member.angle_deg) for member in template.members
    )
    stored = {
        member.part_id: _anchor(planner.library.require(member.part_id).spec)
        for member in template.members
    }
    files = {
        member.part_id: _anchor(_file(planner.library.require(member.part_id).spec.source_id))
        for member in template.members
    }
    plan = empty_plan(TrackId(1))
    from_store = place_template(plan, members, 10.0, 20.0, 0.0, stored)
    from_file = place_template(plan, members, 10.0, 20.0, 0.0, files)
    zero = {part_id: (0.0, 0.0) for part_id in stored}
    without_centre = place_template(plan, members, 10.0, 20.0, 0.0, zero)
    stored_poses = _poses(from_store.instances)
    assert stored_poses == _poses(from_file.instances)
    assert stored_poses != _poses(without_centre.instances)


def test_the_editor_round_trips_a_centre_it_does_not_edit(qtbot: QtBot, env: Env) -> None:
    planner = _planner(env)
    curve = _record(planner, _CURVE)
    dialog = PartDialog(env.runtime.translator, planner, curve, duplicate=True)
    qtbot.addWidget(dialog)
    dialog.article.setText("EB-ED")
    dialog.accept()
    assert dialog.result() == QDialog.DialogCode.Accepted
    saved = dialog.saved
    assert saved is not None
    assert saved.spec.source_id == ""
    assert saved.spec.center_x_mm == curve.spec.center_x_mm
    assert saved.spec.center_y_mm == -297.0
    assert saved.spec.short_label == "K1/60"
    opened = PartDialog(env.runtime.translator, planner, planner.library.require(saved.id))
    qtbot.addWidget(opened)
    source = opened.graphic.session.source
    assert source is not None
    assert source.center_y_mm == -297.0
    assert source.short_label == "K1/60"
    opened.description.setPlainText("Hinweis")
    opened.accept()
    kept = PartLibrary(env.runtime.database).require(saved.id)
    assert kept.spec.description == "Hinweis"
    assert kept.spec.center_y_mm == -297.0
    assert kept.spec.center_angle_deg == 0.0
    assert kept.spec.short_label == "K1/60"
    assert kept.spec.source_id == ""
    session = GraphicSession()
    session.load(kept.spec)
    session._edited = True
    rebuilt = session.compile(
        article_number=kept.spec.article_number,
        scale=kept.spec.scale,
        name=kept.spec.name,
        manufacturer=kept.spec.manufacturer,
        description=kept.spec.description,
        category=kept.spec.category,
        as_draft=True,
    )
    assert rebuilt.center_x_mm == kept.spec.center_x_mm
    assert rebuilt.center_y_mm == -297.0
    assert rebuilt.center_angle_deg == 0.0
    assert rebuilt.short_label == "K1/60"
    untouched = GraphicSession()
    untouched.load(curve.spec)
    compiled = untouched.compile(
        article_number=curve.spec.article_number,
        scale=curve.spec.scale,
        name=curve.spec.name,
        manufacturer=curve.spec.manufacturer,
        description=curve.spec.description,
        category=curve.spec.category,
        as_draft=False,
    )
    assert compiled.center_y_mm == curve.spec.center_y_mm
    assert compiled.short_label == "K1/60"


def _file(source_id: str) -> PartSpec:
    return next(spec for spec in load_carrera_library().parts if spec.source_id == source_id)


def _record(planner: TrackPlannerService, source_id: str) -> PartRecord:
    planner.list_parts()
    return next(
        record for record in planner.library.list_active() if record.spec.source_id == source_id
    )


def _copy(planner: TrackPlannerService, record: PartRecord, name: str, article: str) -> PartRecord:
    return planner.add_part(
        replace(
            record.spec,
            name=name,
            article_number=article,
            source_id="",
            piece_key="",
        )
    )


def _anchor(spec: PartSpec) -> tuple[float, float]:
    return (spec.center_x_mm, spec.center_y_mm)


def _one(record: PartRecord) -> dict[int, PartSpec]:
    return {record.id: record.spec}


def _digital(planner: TrackPlannerService) -> PartRecord:
    planner.list_parts()
    return next(
        record
        for record in planner.library.list_active()
        if record.spec.article_number == "30343"
        and record.spec.short_label == "WL"
        and "digital" in record.spec.systems
    )


def _snap(target: PartSpec, moving: PartSpec) -> tuple[float, float, float]:
    host = PartInstance("placed", 1, 0.0, 0.0)
    pose = snap_pose(
        moving,
        Pose(400.0, 0.0, 0.0),
        ((host, target),),
        snap_mm=200.0,
        grid_mm=None,
    )
    return (pose.x_mm, pose.y_mm, pose.rotation_z_deg)


def _poses(instances: tuple[PartInstance, ...]) -> list[tuple[int, float, float, float]]:
    return [(item.part_id, item.x_mm, item.y_mm, item.rotation_z_deg) for item in instances]


def _companions(
    record: PartRecord, catalog: dict[int, PartSpec]
) -> list[tuple[str, float, float, float]]:
    host = PartInstance("host", record.id, 0.0, 0.0)
    seated = complete_auto_connections(
        add_instance(empty_plan(TrackId(1)), host), catalog, ("host",)
    )
    return sorted(
        (catalog[item.part_id].source_id, item.x_mm, item.y_mm, item.rotation_z_deg)
        for item in seated.instances
        if item.id != "host"
    )


def _paint(spec: PartSpec) -> QImage:
    image = QImage(240, 240, QImage.Format.Format_ARGB32_Premultiplied)
    image.fill(Qt.GlobalColor.white)
    painter = QPainter(image)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, False)
    painter.translate(120.0, 120.0)
    painter.scale(0.15, 0.15)
    paint_part(
        painter,
        spec,
        color_coding=False,
        selected=False,
        start_straight=False,
        show_short_label=True,
    )
    painter.end()
    return image


def _pixels(image: QImage) -> tuple[int, ...]:
    return tuple(
        image.pixel(x_px, y_px)
        for y_px in range(0, image.height(), 4)
        for x_px in range(0, image.width(), 4)
    )


def _insert_legacy(
    connection: Connection,
    *,
    article: str,
    name: str,
    source_id: str,
    lineage: str,
    revision: int,
    current: int,
) -> int:
    connection.execute(
        text(
            "INSERT INTO track_part_definitions "
            "(article_number, scale, name, category, lane_count, outline, source_id, "
            "lineage_id, revision, is_current, origin) VALUES "
            "(:article, '1:24', :name, 'curve', 2, '[]', :source_id, "
            ":lineage, :revision, :current, 'catalog')"
        ),
        {
            "article": article,
            "name": name,
            "source_id": source_id,
            "lineage": lineage,
            "revision": revision,
            "current": current,
        },
    )
    found = connection.execute(
        text("SELECT id FROM track_part_definitions ORDER BY id DESC LIMIT 1")
    ).scalar_one()
    return int(found)
