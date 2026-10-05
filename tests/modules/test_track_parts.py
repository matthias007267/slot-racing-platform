"""The part library is stored once. A plan only stores placed instances."""

from __future__ import annotations

from pathlib import Path

import pytest
from pytestqt.qtbot import QtBot
from sqlalchemy import text

from slot_racing.app.runtime import Runtime
from slot_racing.core.backup import create_backup, restore_backup
from slot_racing.core.config import AppConfig
from slot_racing.core.domain import TrackId
from slot_racing.core.errors import ValidationError
from slot_racing.modules.track_planner.document import (
    COUNTERCLOCKWISE,
    add_instance,
    empty_plan,
    place_instance,
    place_start_finish,
    remove_instance,
    rotate_instance,
    set_direction,
    set_plan_grid,
)
from slot_racing.modules.track_planner.models import TrackPartDefinition
from slot_racing.modules.track_planner.parts import (
    BORDER,
    CATEGORIES,
    CONNECTOR_KINDS,
    CROSSING,
    CURVE,
    LANE_CHANGE,
    PITLANE,
    SPECIAL,
    STRAIGHT,
    SUPPORT,
    SWITCH,
    ConnectorSpec,
    PartInstance,
    PartSpec,
    Pose,
    build_part,
    connectors_compatible,
    implied_scale,
    lane_world_point,
    pit_end,
    rotate_xy,
    snap_pose,
    standard_catalog,
    world_xy,
)
from slot_racing.modules.track_planner.service import TrackPlannerService
from slot_racing.modules.track_planner.ui.library_dialog import PartDialog
from slot_racing.modules.track_planner.ui.page import PlannerPage
from slot_racing.modules.tracks.service import TrackInput, TrackService
from tests.modules.conftest import Env
from tests.modules.test_ui_management import open_page

_ANGLES = (13.0, 37.0, 82.0, 127.0)


def test_a_part_stores_system_article_measures_and_connectors(env: Env) -> None:
    planner = _planner(env)
    spec = build_part(
        system="Eigenbau",
        article_number="EB-42",
        scale="1:24",
        name="Sondergerade",
        category=STRAIGHT,
        length_mm=250,
        width_mm=180,
        height_mm=12,
        radius_mm=None,
        angle_deg=None,
        lane_count=2,
    )
    created = planner.add_part(spec)
    loaded = planner.library.require(created.id).spec
    assert loaded.system == "Eigenbau"
    assert loaded.article_number == "EB-42"
    assert loaded.scale == "1:24"
    assert loaded.length_mm == pytest.approx(250)
    assert loaded.width_mm == pytest.approx(180)
    assert loaded.height_mm == pytest.approx(12)
    assert loaded.radius_mm is None
    assert loaded.angle_deg == pytest.approx(0)
    assert loaded.lane_count == 2
    assert loaded.connectors[0].name == "a"
    assert loaded.connectors[0].x_mm == pytest.approx(-125)
    assert loaded.connectors[0].direction_deg == pytest.approx(180)
    assert loaded.connectors[0].lanes == (1, 2)
    assert loaded.connectors[1].name == "b"
    assert loaded.connectors[1].x_mm == pytest.approx(125)
    assert loaded.connectors[1].direction_deg == pytest.approx(0)
    assert loaded.connectors[1].lanes == (1, 2)
    assert loaded.outline


def test_a_curve_stores_radius_and_angle_without_a_length(env: Env) -> None:
    planner = _planner(env)
    created = planner.add_part(
        build_part(
            system="Eigenbau",
            article_number="EB-R2",
            scale="1:32",
            name="Kurve",
            category=CURVE,
            length_mm=None,
            width_mm=None,
            height_mm=None,
            radius_mm=500,
            angle_deg=30,
            lane_count=3,
        )
    )
    loaded = planner.library.require(created.id).spec
    assert loaded.length_mm is None
    assert loaded.radius_mm == pytest.approx(500)
    assert loaded.angle_deg == pytest.approx(30)
    assert loaded.lane_count == 3
    assert len(loaded.connectors) == 2
    assert loaded.connectors[0].lanes == (1, 2, 3)


def test_system_and_article_identify_a_part_and_scale_is_not_repeated(env: Env) -> None:
    planner = _planner(env)
    known = build_part(
        system="Carrera Digital 132",
        article_number="CUSTOM-1",
        scale="1:32",
        name="Zusatzgerade",
        category=STRAIGHT,
        length_mm=200,
        width_mm=None,
        height_mm=None,
        radius_mm=None,
        angle_deg=None,
        lane_count=2,
    )
    assert known.scale == "1:32"
    assert implied_scale(known.system) == "1:32"
    stored = planner.add_part(known)
    assert planner.library.require(stored.id).spec.scale == "1:32"
    with pytest.raises(ValidationError) as duplicate:
        planner.add_part(known)
    assert duplicate.value.key == "error.planner.part_exists"
    other_system = planner.add_part(
        build_part(
            system="Eigenbau",
            article_number="CUSTOM-1",
            scale="1:32",
            name="Dieselbe Nummer",
            category=STRAIGHT,
            length_mm=200,
            width_mm=None,
            height_mm=None,
            radius_mm=None,
            angle_deg=None,
            lane_count=2,
        )
    )
    assert other_system.spec.scale == "1:32"
    other_scale = planner.add_part(
        build_part(
            system="Eigenbau",
            article_number="CUSTOM-1",
            scale="1:43",
            name="Anderer Maßstab",
            category=STRAIGHT,
            length_mm=200,
            width_mm=None,
            height_mm=None,
            radius_mm=None,
            angle_deg=None,
            lane_count=2,
        )
    )
    assert other_scale.id != other_system.id
    with pytest.raises(ValidationError) as missing:
        build_part(
            system="Eigenbau",
            article_number="OHNE",
            scale=None,
            name="Ohne Maßstab",
            category=STRAIGHT,
            length_mm=200,
            width_mm=None,
            height_mm=None,
            radius_mm=None,
            angle_deg=None,
            lane_count=2,
        )
    assert missing.value.key == "error.planner.scale"
    with pytest.raises(ValidationError) as conflicting:
        build_part(
            system="Carrera Digital 132",
            article_number="CUSTOM-2",
            scale="1:24",
            name="Falscher Maßstab",
            category=STRAIGHT,
            length_mm=200,
            width_mm=None,
            height_mm=None,
            radius_mm=None,
            angle_deg=None,
            lane_count=2,
        )
    assert conflicting.value.key == "error.planner.scale"
    assert "manufacturer" not in TrackPartDefinition.__table__.columns
    assert "manufacturer" not in PartSpec.__dataclass_fields__


def test_the_seeded_catalogue_covers_the_part_categories(env: Env) -> None:
    planner = _planner(env)
    by_article = {record.spec.article_number: record.spec for record in planner.list_parts()}
    straight = by_article["20020601"]
    assert straight.category == STRAIGHT
    assert straight.length_mm == pytest.approx(345)
    assert straight.radius_mm is None
    assert straight.angle_deg == pytest.approx(0)
    assert straight.scale == "1:32"
    assert {spec.scale for spec in by_article.values()} == {"1:32"}
    assert by_article["20020611"].length_mm == pytest.approx(115)
    assert by_article["20020612"].length_mm == pytest.approx(86)
    curve = by_article["20020572"]
    assert curve.category == CURVE
    assert curve.radius_mm == pytest.approx(500)
    assert curve.angle_deg == pytest.approx(30)
    assert curve.length_mm is None
    assert {by_article[article].category for article in ("20030343", "20030345", "20030347")} == {
        LANE_CHANGE
    }
    assert by_article["20020587"].category == CROSSING
    assert len(by_article["20020587"].connectors) == 4
    assert by_article["20020517"].category == SWITCH
    assert by_article["20030341"].category == PITLANE
    assert by_article["20030341"].lane_count == 1
    assert by_article["20030356-E"].category == PITLANE
    assert by_article["20030356-A"].category == PITLANE
    assert by_article["20030350"].category == SPECIAL
    assert by_article["20020574"].radius_mm == pytest.approx(300)
    assert by_article["20020560"].category == BORDER
    assert by_article["20020560"].connectors[0].kind == "border"
    assert SUPPORT in CATEGORIES
    assert set(CATEGORIES) >= {
        STRAIGHT,
        CURVE,
        LANE_CHANGE,
        CROSSING,
        SWITCH,
        PITLANE,
        SPECIAL,
        BORDER,
        SUPPORT,
    }
    again = planner.list_parts()
    assert [record.id for record in again] == [record.id for record in planner.list_parts()]
    assert len(again) == len(standard_catalog())


def test_the_same_part_can_be_placed_twice_without_changing_the_definition(env: Env) -> None:
    planner = _planner(env)
    track = env.track("Heim", lanes=3)
    other = env.track("Gast", lanes=2)
    part = planner.add_part(_straight_spec("EB-USE", lanes=2))
    catalog = {part.id: part.spec}
    home = empty_plan(track.id)
    for index, angle in enumerate(_ANGLES):
        home = place_instance(home, part.id, part.spec, 0, index * 1000, angle, catalog)
    home = rotate_instance(home, home.instances[0].id, 183)
    home = place_start_finish(set_direction(home, COUNTERCLOCKWISE), 2, 1)
    away = place_instance(empty_plan(other.id), part.id, part.spec, 40, 50, 47, catalog)
    planner.save(home)
    planner.save(away)

    loaded_home = planner.load(track.id)
    loaded_away = planner.load(other.id)
    ordered = sorted(loaded_home.instances, key=lambda instance: instance.y_mm)
    assert [instance.part_id for instance in ordered] == [part.id] * 4
    _same(tuple(instance.rotation_z_deg for instance in ordered), (183, 37, 82, 127))
    _same(tuple(instance.y_mm for instance in ordered), (0, 1000, 2000, 3000))
    assert all(instance.x_mm == pytest.approx(0) for instance in ordered)
    assert loaded_away.instances[0].rotation_z_deg == pytest.approx(47)
    assert loaded_home.direction == COUNTERCLOCKWISE
    start = loaded_home.start_finish()
    assert start is not None and (start.x, start.y) == (2, 1)
    assert planner.library.require(part.id).spec.length_mm == pytest.approx(200)
    kept = remove_instance(loaded_home, loaded_home.instances[0].id)
    planner.save(kept)
    assert len(planner.load(track.id).instances) == 3
    assert planner.load(other.id).instances[0].part_id == part.id
    assert planner.library.require(part.id).spec.article_number == "EB-USE"
    stored_track = env.tracks.get_track(track.id)
    assert stored_track is not None and stored_track.lane_count == 3


def test_an_instance_can_store_height_and_the_other_rotations(env: Env) -> None:
    planner = _planner(env)
    track = env.track()
    part = planner.add_part(_straight_spec("EB-3D", lanes=2))
    instance = PartInstance(
        "body-1",
        part.id,
        10,
        20,
        z_mm=30,
        rotation_x_deg=5,
        rotation_y_deg=8,
        rotation_z_deg=127,
    )
    planner.save(place_start_finish(add_instance(empty_plan(track.id), instance), 1, 1))
    loaded = planner.load(track.id).instances[0]
    _same((loaded.x_mm, loaded.y_mm, loaded.z_mm), (10, 20, 30))
    _same(
        (loaded.rotation_x_deg, loaded.rotation_y_deg, loaded.rotation_z_deg),
        (5, 8, 127),
    )


def test_the_grid_can_be_turned_off_and_snap_still_wins(env: Env) -> None:
    planner = _planner(env)
    track = env.track()
    part = planner.add_part(_straight_spec("EB-GRID", lanes=2, length=200))
    catalog = {part.id: part.spec}
    free = set_plan_grid(empty_plan(track.id), enabled=False, grid_mm=10, snap_mm=25)
    placed = place_instance(free, part.id, part.spec, 13.2, 7.8, 37, catalog)
    assert placed.grid_enabled is False
    _same((placed.instances[0].x_mm, placed.instances[0].y_mm), (13.2, 7.8))
    assert placed.instances[0].rotation_z_deg == pytest.approx(37)
    locked = set_plan_grid(empty_plan(track.id), enabled=True, grid_mm=10, snap_mm=25)
    gridded = place_instance(locked, part.id, part.spec, 13.2, 7.8, 37, catalog)
    _same((gridded.instances[0].x_mm, gridded.instances[0].y_mm), (10, 10))
    planner.save(placed)
    assert planner.load(track.id).grid_enabled is False
    assert planner.load(track.id).instances[0].x_mm == pytest.approx(13.2)


def test_compatible_joints_dock_and_keep_the_lanes_in_order() -> None:
    spec = _straight_spec("local", lanes=2, length=200)
    target = PartInstance("fixed", 1, 0.0, 0.0)
    pose = snap_pose(spec, Pose(190, 3, 0), ((target, spec),), snap_mm=25, grid_mm=10)
    _same((pose.x_mm, pose.y_mm, pose.rotation_z_deg), (200, 0, 0))
    moving = PartInstance("move", 1, pose.x_mm, pose.y_mm, rotation_z_deg=pose.rotation_z_deg)
    for lane, side in ((1, -50.0), (2, 50.0)):
        start = lane_world_point(target, spec.connectors[1], lane)
        joined = lane_world_point(moving, spec.connectors[0], lane)
        assert joined == pytest.approx(start)
        assert joined[1] == pytest.approx(side)
    assert lane_world_point(moving, spec.connectors[0], 1) != pytest.approx(
        lane_world_point(target, spec.connectors[1], 2)
    )


def test_docking_corrects_a_small_rotation_and_ignores_a_distant_part() -> None:
    spec = _straight_spec("local", lanes=2, length=200)
    target = PartInstance("fixed", 1, 0.0, 0.0)
    joint = spec.connectors[1]
    target_point = world_xy(target, joint.x_mm, joint.y_mm)
    proposed_rotation = 5.0
    rotated = rotate_xy(spec.connectors[0].x_mm, spec.connectors[0].y_mm, proposed_rotation)
    origin = (target_point[0] - rotated[0], target_point[1] - rotated[1])
    aligned = snap_pose(
        spec,
        Pose(origin[0], origin[1], proposed_rotation),
        ((target, spec),),
        snap_mm=25,
        grid_mm=10,
    )
    assert aligned.rotation_z_deg == pytest.approx(0)
    _same((aligned.x_mm, aligned.y_mm), (200, 0))
    distant = snap_pose(spec, Pose(13.2, 7.8, 37), ((target, spec),), snap_mm=25, grid_mm=10)
    _same((distant.x_mm, distant.y_mm, distant.rotation_z_deg), (10, 10, 37))
    free = snap_pose(spec, Pose(13.2, 7.8, 37), (), snap_mm=25, grid_mm=None)
    _same((free.x_mm, free.y_mm, free.rotation_z_deg), (13.2, 7.8, 37))


def test_incompatible_joints_do_not_dock() -> None:
    wide = _straight_spec("wide", lanes=2, length=200)
    narrow = _straight_spec("narrow", lanes=1, length=200)
    border = build_part(
        system="Eigenbau",
        article_number="RAND",
        scale="1:32",
        name="Rand",
        category=BORDER,
        length_mm=200,
        width_mm=40,
        height_mm=None,
        radius_mm=None,
        angle_deg=None,
        lane_count=2,
    )
    support = build_part(
        system="Eigenbau",
        article_number="STUETZE",
        scale="1:32",
        name="Stütze",
        category=SUPPORT,
        length_mm=200,
        width_mm=40,
        height_mm=80,
        radius_mm=None,
        angle_deg=None,
        lane_count=2,
    )
    target = PartInstance("fixed", 1, 0.0, 0.0)
    for moving in (narrow, border, support):
        pose = snap_pose(moving, Pose(200, 0, 13), ((target, wide),), snap_mm=50, grid_mm=None)
        assert pose.rotation_z_deg == pytest.approx(13)
        assert pose.x_mm == pytest.approx(200)
    reversed_lanes = ConnectorSpec("a", -100, 0, 0, 180, "track", (2, 1))
    assert not connectors_compatible(reversed_lanes, wide.connectors[1])
    assert border.connectors[0].kind == "border"
    assert support.connectors[0].kind == "support"
    assert "track" in CONNECTOR_KINDS


def test_equal_distances_pick_the_same_joint_every_time() -> None:
    spec = _straight_spec("local", lanes=2, length=200)
    left = PartInstance("a", 1, 10.0, 0.0)
    right = PartInstance("b", 1, 0.0, 0.0)
    first = ((right, spec), (left, spec))
    second = ((left, spec), (right, spec))
    poses = [
        snap_pose(spec, Pose(205, 0, 0), placed, snap_mm=25, grid_mm=None)
        for placed in (first, second, first)
    ]
    assert poses[0].x_mm == pytest.approx(210)
    assert poses[0] == poses[1] == poses[2]


def test_two_three_and_four_lanes_only_dock_to_the_same_count() -> None:
    offsets = {2: (-50.0, 50.0), 3: (-100.0, 0.0, 100.0), 4: (-150.0, -50.0, 50.0, 150.0)}
    specs = {count: _straight_spec(f"L{count}", lanes=count, length=200) for count in (2, 3, 4)}
    for count, spec in specs.items():
        target = PartInstance("fixed", 1, 0.0, 0.0)
        pose = snap_pose(spec, Pose(190, 0, 0), ((target, spec),), snap_mm=25, grid_mm=None)
        moving = PartInstance("move", 1, pose.x_mm, pose.y_mm, rotation_z_deg=pose.rotation_z_deg)
        for lane, side in zip(range(1, count + 1), offsets[count], strict=True):
            assert lane_world_point(moving, spec.connectors[0], lane)[1] == pytest.approx(side)
            assert lane_world_point(moving, spec.connectors[0], lane) == pytest.approx(
                lane_world_point(target, spec.connectors[1], lane)
            )
        other = specs[2 if count == 4 else count + 1]
        missed = snap_pose(other, Pose(200, 0, 13), ((target, spec),), snap_mm=50, grid_mm=None)
        assert missed.rotation_z_deg == pytest.approx(13)
        assert missed.x_mm == pytest.approx(200)


def test_a_rotated_part_keeps_lane_one_on_the_same_side() -> None:
    spec = _straight_spec("local", lanes=2, length=200)
    target = PartInstance("fixed", 1, 0.0, 0.0, rotation_z_deg=47)
    joint = spec.connectors[1]
    point = world_xy(target, joint.x_mm, joint.y_mm)
    rotated = rotate_xy(spec.connectors[0].x_mm, spec.connectors[0].y_mm, 47)
    origin = (point[0] - rotated[0], point[1] - rotated[1])
    pose = snap_pose(
        spec, Pose(origin[0], origin[1], 42), ((target, spec),), snap_mm=25, grid_mm=None
    )
    assert pose.rotation_z_deg == pytest.approx(47)
    moving = PartInstance("move", 1, pose.x_mm, pose.y_mm, rotation_z_deg=pose.rotation_z_deg)
    for lane in (1, 2):
        assert lane_world_point(moving, spec.connectors[0], lane) == pytest.approx(
            lane_world_point(target, spec.connectors[1], lane)
        )


def test_a_pit_straight_docks_to_the_pit_spur_only() -> None:
    entry = pit_end("Carrera Digital 132", "20030356-E", "Pitlane-Einfahrt", pit_side=1.0)
    pit = next(spec for spec in standard_catalog() if spec.article_number == "20030341")
    placed = PartInstance("entry", 1, 0.0, 0.0)
    spur = next(joint for joint in entry.connectors if joint.name == "pit")
    point = world_xy(placed, spur.x_mm, spur.y_mm)
    rotated = rotate_xy(pit.connectors[0].x_mm, pit.connectors[0].y_mm, 90)
    origin = (point[0] - rotated[0], point[1] - rotated[1])
    docked = snap_pose(
        pit, Pose(origin[0], origin[1], 90), ((placed, entry),), snap_mm=25, grid_mm=10
    )
    assert docked.rotation_z_deg == pytest.approx(90)
    moving = PartInstance("pit", 1, docked.x_mm, docked.y_mm, rotation_z_deg=docked.rotation_z_deg)
    assert lane_world_point(moving, pit.connectors[0], 1) == pytest.approx(
        lane_world_point(placed, spur, 1)
    )
    missed = snap_pose(pit, Pose(345, 0, 0), ((placed, entry),), snap_mm=25, grid_mm=None)
    _same((missed.x_mm, missed.rotation_z_deg), (345, 0))


def test_parts_and_plans_survive_reopening_the_database(tmp_path: Path) -> None:
    config = AppConfig(database_path=tmp_path / "slot_racing.db")
    runtime = Runtime.create(config, config_path=tmp_path / "config.json")
    try:
        tracks = runtime.services.get(TrackService)
        planner = runtime.services.get(TrackPlannerService)
        track = tracks.create_track(TrackInput(name="Oval", lane_count=4))
        part = planner.add_part(_straight_spec("EB-KEEP", lanes=4, length=345))
        catalog = {part.id: part.spec}
        plan = place_instance(empty_plan(track.id), part.id, part.spec, 0, 0, 82, catalog)
        plan = place_start_finish(plan, 4, 2)
        planner.save(plan)
        track_id = track.id
        part_id = part.id
    finally:
        runtime.shutdown()

    reopened = Runtime.create(config, config_path=tmp_path / "config.json")
    try:
        planner = reopened.services.get(TrackPlannerService)
        restored = planner.library.require(part_id).spec
        assert restored.article_number == "EB-KEEP"
        assert restored.lane_count == 4
        assert restored.scale == "1:32"
        seeded = next(
            record.spec
            for record in planner.list_parts()
            if record.spec.article_number == "20020601"
        )
        assert seeded.system == "Carrera Digital 132"
        assert seeded.scale == "1:32"
        loaded = planner.load(track_id)
        assert loaded.instances[0].rotation_z_deg == pytest.approx(82)
        assert loaded.instances[0].part_id == part_id
        start = loaded.start_finish()
        assert start is not None and (start.x, start.y) == (4, 2)
        stored = reopened.services.get(TrackService).get_track(track_id)
        assert stored is not None and stored.lane_count == 4
    finally:
        reopened.shutdown()


def test_backup_restores_the_library_and_the_plan(tmp_path: Path) -> None:
    source_dir = tmp_path / "source"
    source_dir.mkdir()
    source = Runtime.create(
        AppConfig(database_path=source_dir / "slot_racing.db", language="de"),
        config_path=source_dir / "config.json",
    )
    try:
        tracks = source.services.get(TrackService)
        planner = source.services.get(TrackPlannerService)
        track = tracks.create_track(TrackInput(name="Heim", lane_count=3))
        part = planner.add_part(
            _straight_spec("EB-BACK", lanes=3, length=200, scale="1:43", system="Eigenbau")
        )
        catalog = {part.id: part.spec}
        plan = set_plan_grid(empty_plan(track.id), enabled=False, grid_mm=10, snap_mm=30)
        plan = place_instance(plan, part.id, part.spec, 15, 25, 127, catalog)
        plan = place_start_finish(set_direction(plan, COUNTERCLOCKWISE), 6, 3)
        planner.save(plan)
        archive = create_backup(source.database, source.config, tmp_path / "backups")
        track_id = int(track.id)
        article = part.spec.article_number
    finally:
        source.shutdown()

    other_dir = tmp_path / "other"
    other_dir.mkdir()
    runtime = Runtime.create(
        AppConfig(database_path=other_dir / "slot_racing.db", language="en"),
        config_path=other_dir / "config.json",
    )
    try:
        runtime.services.get(TrackService).create_track(TrackInput(name="Leer", lane_count=2))
        restore_backup(
            archive,
            runtime.database,
            runtime.config,
            runtime.config_path,
            backup_directory=other_dir / "backups",
        )
        planner = runtime.services.get(TrackPlannerService)
        restored = next(
            record for record in planner.list_parts() if record.spec.article_number == article
        )
        assert restored.spec.system == "Eigenbau"
        assert restored.spec.scale == "1:43"
        seeded = next(
            record.spec
            for record in planner.list_parts()
            if record.spec.article_number == "20020601"
        )
        assert seeded.scale == "1:32"
        assert restored.spec.lane_count == 3
        assert restored.spec.length_mm == pytest.approx(200)
        assert len(restored.spec.connectors) == 2
        loaded = planner.load(TrackId(track_id))
        assert loaded.grid_enabled is False
        assert loaded.snap_mm == pytest.approx(30)
        assert loaded.direction == COUNTERCLOCKWISE
        assert loaded.instances[0].part_id == restored.id
        _same((loaded.instances[0].x_mm, loaded.instances[0].y_mm), (15, 25))
        assert loaded.instances[0].rotation_z_deg == pytest.approx(127)
        start = loaded.start_finish()
        assert start is not None and (start.x, start.y) == (6, 3)
        stored = runtime.services.get(TrackService).get_track(TrackId(track_id))
        assert stored is not None and stored.lane_count == 3
    finally:
        runtime.shutdown()


def test_the_library_dialog_adds_a_part_that_the_planner_can_place(qtbot: QtBot, env: Env) -> None:
    planner = _planner(env)
    dialog = PartDialog(env.runtime.translator, planner)
    qtbot.addWidget(dialog)
    dialog.system.setCurrentText("Carrera Digital 132")
    assert not dialog.scale.isEnabled()
    assert dialog.scale.currentData() == "1:32"
    dialog.system.setCurrentText("Eigenbau")
    assert dialog.scale.isEnabled()
    dialog.article.setText("EB-DLG")
    dialog.scale.setCurrentIndex(dialog.scale.findData("1:43"))
    dialog.name.setText("Dialoggerade")
    dialog.length.setValue(180)
    dialog.lanes.setValue(4)
    dialog.accept()
    assert dialog.result() == dialog.DialogCode.Accepted
    created = dialog.created
    assert created is not None and created.scale == "1:43" and created.lane_count == 4
    stored = next(
        record for record in planner.list_parts() if record.spec.article_number == "EB-DLG"
    )
    track = env.track(lanes=4)
    window, page = open_page(qtbot, env, "track_planner")
    window.show()
    assert isinstance(page, PlannerPage)
    _select(page, track.id)
    row = _library_row(page, "EB-DLG")
    page.library.setCurrentRow(row)
    page.place_part.click()
    assert page.plan().instances[0].part_id == stored.id
    page.grid.setChecked(False)
    page.rotation_free.setValue(37)
    page.x_mm.setValue(13.4)
    page.y_mm.setValue(8.2)
    placed = page.plan().instances[0]
    assert placed.rotation_z_deg == pytest.approx(37)
    _same((placed.x_mm, placed.y_mm), (13.4, 8.2))
    page.grid.setChecked(True)
    page.x_mm.setValue(23.4)
    snapped = page.plan().instances[0]
    _same((snapped.x_mm, snapped.y_mm), (20, 10))
    assert snapped.rotation_z_deg == pytest.approx(37)
    before = page.library.count()
    page.delete_button.click()
    assert page.plan().instances == ()
    assert page.library.count() == before
    assert planner.library.require(stored.id).spec.name == "Dialoggerade"
    carrera = PartDialog(env.runtime.translator, planner)
    qtbot.addWidget(carrera)
    carrera.article.setText("20577")
    carrera.name.setText("Carrera aus Dialog")
    carrera.length.setValue(200)
    carrera.accept()
    assert carrera.created is not None
    assert carrera.created.system == "Carrera Digital 132"
    assert carrera.created.scale == "1:32"
    saved = next(record for record in planner.list_parts() if record.spec.article_number == "20577")
    assert saved.spec.scale == "1:32"


def test_every_scale_is_stored_and_an_invalid_scale_is_rejected(env: Env) -> None:
    planner = _planner(env)
    for scale in ("1:24", "1:32", "1:43"):
        created = planner.add_part(_straight_spec(f"EB-{scale}", lanes=2, scale=scale))
        loaded = planner.library.require(created.id).spec
        assert created.spec.scale == scale
        assert loaded.scale == scale
        assert loaded.system == "Eigenbau"
    for scale in ("1:18", "1:64", "32", ""):
        with pytest.raises(ValidationError) as caught:
            _straight_spec(f"EB-{scale}", lanes=2, scale=scale)
        assert caught.value.key == "error.planner.scale"


def test_carrera_systems_store_their_scale_explicitly(env: Env) -> None:
    planner = _planner(env)
    systems = (
        ("Carrera Digital 132", "1:32"),
        ("Carrera Digital 124", "1:24"),
        ("Carrera Evolution", "1:32"),
        ("Carrera GO!!!", "1:43"),
    )
    for system, scale in systems:
        omitted = _straight_spec("20599", lanes=2, scale=None, system=system)
        explicit = _straight_spec("20598", lanes=2, scale=scale, system=system)
        assert omitted.scale == scale
        assert explicit.scale == scale
        stored = planner.add_part(omitted)
        assert planner.library.require(stored.id).spec.scale == scale
    again = _straight_spec("20599", lanes=2, scale=None, system="Carrera Digital 132")
    with pytest.raises(ValidationError) as caught:
        planner.add_part(again)
    assert caught.value.key == "error.planner.part_exists"
    with env.runtime.database.engine.connect() as connection:
        sql = connection.execute(
            text(
                "SELECT sql FROM sqlite_master "
                "WHERE type = 'table' AND name = 'track_part_definitions'"
            )
        ).scalar_one()
    assert "uq_track_part_definitions_system_article_number_scale" in str(sql)


def _straight_spec(
    article: str,
    *,
    lanes: int,
    length: float = 200,
    scale: str | None = "1:32",
    system: str = "Eigenbau",
) -> PartSpec:
    return build_part(
        system=system,
        article_number=article,
        scale=scale,
        name=article,
        category=STRAIGHT,
        length_mm=length,
        width_mm=None,
        height_mm=None,
        radius_mm=None,
        angle_deg=None,
        lane_count=lanes,
    )


def _same(actual: tuple[float, ...], expected: tuple[float, ...]) -> None:
    assert len(actual) == len(expected)
    for left, right in zip(actual, expected, strict=True):
        assert left == pytest.approx(right)


def _planner(env: Env) -> TrackPlannerService:
    return env.runtime.services.get(TrackPlannerService)


def _select(page: PlannerPage, track_id: TrackId) -> None:
    index = page.track_combo.findData(track_id)
    assert index >= 0
    page.track_combo.setCurrentIndex(index)


def _library_row(page: PlannerPage, article: str) -> int:
    for row in range(page.library.count()):
        item = page.library.item(row)
        if item is not None and article in item.text():
            return row
    raise AssertionError(article)
