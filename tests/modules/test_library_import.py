"""The Carrera source library replaces the bundled catalogue."""

from __future__ import annotations

from dataclasses import replace

import pytest
from sqlalchemy import func, select

from slot_racing.modules.track_planner.catalog_import import HELPER_SOURCE, load_carrera_library
from slot_racing.modules.track_planner.document import empty_plan, place_instance, to_document
from slot_racing.modules.track_planner.graphic_session import GraphicSession
from slot_racing.modules.track_planner.models import (
    TrackPartDefinition,
    TrackPartPackage,
    TrackPartStock,
    TrackPlanInstance,
)
from slot_racing.modules.track_planner.parts import (
    BORDER_JOINT,
    EXTEND_LEFT,
    EXTEND_RIGHT,
    EXTEND_STRAIGHT,
    ORIGIN_CATALOG,
    PATH_SLOT,
    START_PIECE_KEY,
    TRACK,
    ConnectorSpec,
    PartInstance,
    Pose,
    connectors_compatible,
    extend_pose,
    is_start_finish_part,
    join_pose,
    lane_world_point,
    placed_continuation_delta_deg,
    snap_pose,
)
from slot_racing.modules.track_planner.service import TrackPlannerService
from tests.database import migrated_database
from tests.modules.conftest import Env
from tests.modules.test_track_parts import _planner, _straight_spec


def test_a_fresh_library_is_empty_until_the_source_files_are_imported() -> None:
    library_db = migrated_database()
    planner = TrackPlannerService(library_db, _tracks(library_db))
    assert planner.library.list_active() == ()
    assert planner.library.retire_bundled_catalog() == 0
    assert planner.library.list_active() == ()


def test_retiring_the_old_catalogue_keeps_plans_and_stock(env: Env) -> None:
    planner = _planner(env)
    assert planner.library.list_active() == ()
    old = planner.library.add_part(_straight_spec("ALT-1", lanes=2, scale="1:24"))
    with env.runtime.database.session() as session:
        row = session.get(TrackPartDefinition, old.id)
        assert row is not None
        row.origin = ORIGIN_CATALOG
        row.catalog_key = "bundled-straight"
        row.source_id = ""
        session.add(TrackPartStock(part_id=old.id, quantity=4))
    track = env.track("Archiv", lanes=2)
    plan = place_instance(empty_plan(track.id), old.id, old.spec, 12, 8, 0, {old.id: old.spec})
    planner.save(plan)
    retired = planner.library.retire_bundled_catalog()
    assert retired == 1
    assert planner.library.list_active() == ()
    with env.runtime.database.session() as session:
        row = session.get(TrackPartDefinition, old.id)
        assert row is not None and row.legacy and not row.selectable
        stock = session.get(TrackPartStock, old.id)
        assert stock is not None and stock.quantity == 4
        assert session.get(TrackPlanInstance, plan.instances[0].id) is not None
    loaded = planner.load(track.id)
    assert loaded.instances[0].part_id == old.id
    draft = planner.library.import_carrera_library()
    assert draft.skipped == ()
    active = planner.library.list_active()
    assert len(active) == 51
    assert all(record.id != old.id for record in active)
    assert all(record.spec.piece_key != "20509.3" for record in active)
    assert planner.load(track.id).instances[0].part_id == old.id
    kept = planner.records_for([old.id])
    assert kept[0].spec.article_number == "ALT-1"
    with env.runtime.database.session() as session:
        assert session.get(TrackPartStock, old.id).quantity == 4
    again = planner.library.import_carrera_library()
    assert len(again.parts) == len(draft.parts)
    assert len(planner.library.list_active()) == 51


def test_both_source_files_import_without_duplicates(env: Env) -> None:
    planner = _planner(env)
    draft = load_carrera_library()
    assert len(draft.parts) == 52
    assert sum(spec.selectable for spec in draft.parts) == 51
    assert len(draft.templates) == 10
    assert len(draft.packages) == 39
    assert draft.skipped == ()
    assert len(draft.notes) == 2
    records = planner.list_parts()
    source_ids = [record.spec.source_id for record in records]
    assert len(source_ids) == len(set(source_ids)) == 51
    helper = _row(env, piece_key="20509.3")
    assert helper.source_id == HELPER_SOURCE
    assert helper.selectable is False
    assert helper.legacy is False
    start = next(record for record in records if record.spec.piece_key == START_PIECE_KEY)
    standard = next(
        record
        for record in records
        if record.spec.article_number == "20509" and not record.spec.piece_key
    )
    assert start.spec.article_number == "20509"
    assert standard.spec.article_number == "20509"
    assert start.spec.source_id != standard.spec.source_id
    assert is_start_finish_part(start.spec)
    assert not is_start_finish_part(standard.spec)
    assert standard.spec.systems == ("evolution", "digital")
    assert start.spec.systems == ("evolution",)
    assert "digital" in _row(env, source_id=HELPER_SOURCE).systems
    before = _source_count(env)
    planner.library.import_carrera_library()
    assert _source_count(env) == before
    packages = {package.article_number: package for package in planner.list_packages()}
    assert packages["20509"].contents[0].part_id == standard.id
    assert packages["20509.2"].contents[0].part_id == start.id
    assert "20509.3" not in packages


def test_straights_curves_borders_and_switches_keep_the_source_geometry(env: Env) -> None:
    planner = _planner(env)
    by_source = {record.spec.source_id: record.spec for record in planner.list_parts()}
    straight = next(
        spec
        for spec in by_source.values()
        if spec.article_number == "20509" and not spec.piece_key
    )
    slots = [path for path in straight.slot_paths if path.kind == PATH_SLOT]
    assert len(slots) == 2
    assert straight.radius_mm is None
    ys = sorted(span.y0 for path in slots for span in path.spans[:1])
    assert ys[0] == pytest_approx(-49.5)
    assert ys[1] == pytest_approx(49.5)
    rails = [connector for connector in straight.connectors if connector.kind == TRACK]
    assert {round(connector.neighbor_x, 1) for connector in rails} == {-345.0, 345.0}
    curve = by_source["cc253612-a0cb-4b17-b940-18460c337acb"]
    assert curve.radius_mm is None
    assert any(path.kind == PATH_SLOT for path in curve.slot_paths)
    borders = [spec for spec in by_source.values() if spec.category == "border"]
    assert len(borders) == 21
    assert all(spec.lane_count == 0 for spec in borders)
    assert all(not any(path.kind == PATH_SLOT for path in spec.slot_paths) for spec in borders)
    assert all(spec.attachment is None and spec.radius_mm is None for spec in borders)
    halves = [spec for spec in by_source.values() if spec.article_number == "30343"]
    assert {spec.piece_key for spec in halves} == {"30343.1", "30343.2"}
    templates = planner.list_templates()
    shell = next(template for template in templates if template.article_number == "30343")
    assert {member.part_id for member in shell.members} == {
        next(
            record.id
            for record in planner.list_parts()
            if record.spec.source_id == spec.source_id
        )
        for spec in halves
    }
    assert all(template.article_number != "30343" or template.source_id for template in templates)
    assert not any(record.spec.source_id == shell.source_id for record in planner.list_parts())
    right = [spec for spec in by_source.values() if spec.article_number == "30345"]
    left_half = next(spec for spec in right if "C_Dig_right" in _types(spec))
    other = next(spec for spec in right if spec is not left_half)
    assert connectors_compatible(
        _typed(left_half),
        _typed(other),
        left_source=left_half.source_id,
        right_source=other.source_id,
    )
    assert not connectors_compatible(
        ConnectorSpec("a", 0, 0, 0, 0, TRACK, (1, 2), target_source_id="nope", exclusive=True),
        ConnectorSpec("b", 0, 0, 0, 180, TRACK, (1, 2), target_source_id="nope", exclusive=True),
        left_source="one",
        right_source="two",
    )
    start = next(spec for spec in by_source.values() if spec.piece_key == START_PIECE_KEY)
    assert start.article_number == straight.article_number
    assert start.source_id != straight.source_id
    assert any(path.kind == "start" for path in start.slot_paths)
    assert not any(path.kind == "start" for path in straight.slot_paths)
    lane_change = next(spec for spec in by_source.values() if spec.article_number == "30362")
    assert lane_change.source_id != curve.source_id
    assert any(connector.exclusive for connector in lane_change.connectors)
    assert not any(connector.exclusive and connector.edge_types for connector in curve.connectors)


def test_placement_snaps_on_attach_points_and_plain_rail_ends(env: Env) -> None:
    planner = _planner(env)
    records = {record.spec.source_id: record for record in planner.list_parts()}
    straight = next(
        record.spec
        for record in records.values()
        if record.spec.article_number == "20509" and not record.spec.piece_key
    )
    host = PartInstance("host", 1, 0.0, 0.0)
    seated = snap_pose(
        straight,
        Pose(330.0, 4.0, 0.0),
        ((host, straight),),
        snap_mm=30,
        grid_mm=None,
    )
    assert seated.x_mm == pytest_approx(345)
    assert seated.y_mm == pytest_approx(0)
    curve = records["cc253612-a0cb-4b17-b940-18460c337acb"].spec
    pair = _compatible_pair(straight, curve)
    assert pair is not None
    source, target = pair
    expected = join_pose(host, source, target)
    near = Pose(expected.x_mm + 8, expected.y_mm - 3, expected.rotation_z_deg)
    snapped = snap_pose(curve, near, ((host, straight),), snap_mm=40, grid_mm=None)
    assert snapped.x_mm == pytest_approx(expected.x_mm)
    assert snapped.y_mm == pytest_approx(expected.y_mm)
    border_joint = next(
        connector for connector in straight.connectors if connector.kind == BORDER_JOINT
    )
    border = records[border_joint.target_source_id].spec
    assert border.lane_count == 0
    border_pose = snap_pose(
        border,
        Pose(border_joint.neighbor_x + 6, border_joint.neighbor_y - 4, border_joint.neighbor_angle),
        ((host, straight),),
        snap_mm=20,
        grid_mm=None,
    )
    assert border_pose.x_mm == pytest_approx(border_joint.neighbor_x)
    assert border_pose.y_mm == pytest_approx(border_joint.neighbor_y)
    assert border.radius_mm is None
    meeting = _meeting_rails(straight, curve)
    assert meeting is not None
    straight_joint, curve_joint = meeting
    pose = join_pose(host, straight_joint, curve_joint)
    seated_curve = PartInstance(
        "curve", 2, pose.x_mm, pose.y_mm, rotation_z_deg=pose.rotation_z_deg
    )
    for lane in (1, 2):
        start = lane_world_point(host, straight_joint, lane)
        end = lane_world_point(seated_curve, curve_joint, lane)
        assert start == pytest.approx(end, abs=0.05)
    left = extend_pose(host, straight_joint, curve, EXTEND_LEFT)
    right = extend_pose(host, straight_joint, curve, EXTEND_RIGHT)
    assert left is not None and right is not None
    assert _turn(host, straight_joint, left, curve) == pytest.approx(-60, abs=0.05)
    assert _turn(host, straight_joint, right, curve) == pytest.approx(60, abs=0.05)
    forward = extend_pose(host, straight_joint, straight, EXTEND_STRAIGHT)
    assert forward is not None
    assert _turn(host, straight_joint, forward, straight) == pytest.approx(0, abs=0.05)


def test_a_plan_round_trips_and_keeps_an_unknown_part(env: Env) -> None:
    planner = _planner(env)
    straight = next(
        record
        for record in planner.list_parts()
        if record.spec.article_number == "20509" and not record.spec.piece_key
    )
    track = env.track("Runde", lanes=2)
    catalog = {straight.id: straight.spec}
    plan = place_instance(empty_plan(track.id), straight.id, straight.spec, 0, 0, 0, catalog)
    missing = PartInstance("missing-1", 999999, 40.0, 15.0, rotation_z_deg=12)
    plan = replace(plan, instances=(*plan.instances, missing))
    planner.save(plan)
    document = to_document(plan)
    assert any(row["part_id"] == 999999 for row in document["instances"])
    loaded = planner.load(track.id)
    assert {item.part_id for item in loaded.instances} == {straight.id, 999999}
    missing_item = next(item for item in loaded.instances if item.part_id == 999999)
    assert missing_item.x_mm == pytest_approx(40)
    planner.save(loaded)
    again = planner.load(track.id)
    assert {item.part_id for item in again.instances} == {straight.id, 999999}


def test_an_unedited_import_keeps_its_identity_in_the_editor(env: Env) -> None:
    planner = _planner(env)
    record = next(item for item in planner.list_parts() if item.spec.category == "border")
    session = GraphicSession()
    session.load(record.spec)
    compiled = session.compile(
        article_number=record.spec.article_number,
        scale=record.spec.scale,
        name=record.spec.name,
        manufacturer=record.spec.manufacturer,
        description=record.spec.description,
        category=record.spec.category,
        as_draft=False,
    )
    assert compiled.source_id == record.spec.source_id
    assert compiled.piece_key == record.spec.piece_key
    assert compiled.lane_count == 0
    assert compiled.outline == record.spec.outline
    saved = planner.update_part(record.id, compiled)
    assert saved.id == record.id
    assert saved.spec.source_id == record.spec.source_id


def _tracks(database: object) -> object:
    from slot_racing.modules.tracks.service import TrackService

    return TrackService(database)  # type: ignore[arg-type]


def _row(env: Env, *, piece_key: str = "", source_id: str = "") -> TrackPartDefinition:
    planner = _planner(env)
    planner.list_parts()
    with env.runtime.database.session() as session:
        query = select(TrackPartDefinition)
        if piece_key:
            query = query.where(TrackPartDefinition.piece_key == piece_key)
        if source_id:
            query = query.where(TrackPartDefinition.source_id == source_id)
        row = session.scalars(query).one()
        session.expunge(row)
        return row


def _source_count(env: Env) -> int:
    with env.runtime.database.session() as session:
        return int(
            session.scalar(
                select(func.count())
                .select_from(TrackPartDefinition)
                .where(TrackPartDefinition.source_id != "")
            )
            or 0
        )


def _types(spec: object) -> tuple[str, ...]:
    found: list[str] = []
    for connector in spec.connectors:  # type: ignore[attr-defined]
        found.extend(connector.edge_types)
    return tuple(found)


def _typed(spec: object) -> ConnectorSpec:
    return next(
        connector
        for connector in spec.connectors  # type: ignore[attr-defined]
        if connector.exclusive and connector.edge_types
    )


def _meeting_rails(straight: object, curve: object) -> tuple[ConnectorSpec, ConnectorSpec] | None:
    host = PartInstance("host", 1, 0.0, 0.0)
    best: tuple[float, ConnectorSpec, ConnectorSpec] | None = None
    for source in straight.connectors:  # type: ignore[attr-defined]
        if source.exclusive:
            continue
        for target in curve.connectors:  # type: ignore[attr-defined]
            if target.exclusive:
                continue
            pose = join_pose(host, source, target)
            seated = PartInstance(
                "curve", 2, pose.x_mm, pose.y_mm, rotation_z_deg=pose.rotation_z_deg
            )
            gap = 0.0
            for lane in source.lanes:
                if lane not in target.lanes:
                    gap = 1e9
                    break
                start = lane_world_point(host, source, lane)
                end = lane_world_point(seated, target, lane)
                gap = max(gap, abs(start[0] - end[0]) + abs(start[1] - end[1]))
            if best is None or gap < best[0]:
                best = (gap, source, target)
    if best is None:
        return None
    return best[1], best[2]


def _turn(
    host: PartInstance, joint: ConnectorSpec, pose: Pose, spec: object
) -> float:
    seated = PartInstance("next", 2, pose.x_mm, pose.y_mm, rotation_z_deg=pose.rotation_z_deg)
    delta = placed_continuation_delta_deg(host, joint, seated, spec)  # type: ignore[arg-type]
    assert delta is not None
    return delta


def _compatible_pair(left: object, right: object) -> tuple[ConnectorSpec, ConnectorSpec] | None:
    for source in left.connectors:  # type: ignore[attr-defined]
        for target in right.connectors:  # type: ignore[attr-defined]
            if connectors_compatible(
                source,
                target,
                left_source=left.source_id,  # type: ignore[attr-defined]
                right_source=right.source_id,  # type: ignore[attr-defined]
            ):
                return source, target
    return None


def pytest_approx(value: float) -> float:
    return pytest.approx(value)  # type: ignore[no-any-return]


def test_suppressed_old_packages_are_not_recreated(env: Env) -> None:
    planner = _planner(env)
    planner.list_parts()
    with env.runtime.database.session() as session:
        session.add(
            TrackPartPackage(
                manufacturer="Carrera",
                article_number="20020601",
                name="Standardgerade",
            )
        )
    planner.library.retire_bundled_catalog()
    with env.runtime.database.session() as session:
        row = session.scalars(
            select(TrackPartPackage).where(TrackPartPackage.article_number == "20020601")
        ).one()
        assert row.suppressed
    assert all(package.article_number != "20020601" for package in planner.list_packages())
