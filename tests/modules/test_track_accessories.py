"""Attached parts follow one rail and stay out of the track topology."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest
from pytestqt.qtbot import QtBot
from sqlalchemy import select

from slot_racing.app.runtime import Runtime
from slot_racing.core.backup import create_backup, restore_backup
from slot_racing.core.config import AppConfig
from slot_racing.core.diagnostics import current, install
from slot_racing.core.domain import TrackId
from slot_racing.core.errors import ValidationError
from slot_racing.modules.track_planner.attachment import placed_outline, radii
from slot_racing.modules.track_planner.document import (
    TrackPlan,
    add_instance,
    attach_accessory,
    clone_plan,
    duplicate_instances,
    empty_plan,
    extend_from_connector,
    move_instance,
    parse_plan,
    place_instance,
    remove_instances,
    reposition_selection,
    rotate_instance,
    set_plan_grid,
    to_document,
    toggle_group,
)
from slot_racing.modules.track_planner.figure import track_figure
from slot_racing.modules.track_planner.inventory import analyze_inventory
from slot_racing.modules.track_planner.lane_length import display_lane_lengths
from slot_racing.modules.track_planner.models import TrackPartConnector, TrackPartDefinition
from slot_racing.modules.track_planner.parts import (
    PartInstance,
    PartSpec,
    free_connector_indexes,
    standard_catalog,
    world_xy,
)
from slot_racing.modules.track_planner.service import TrackPlannerService
from slot_racing.modules.tracks.service import TrackInput, TrackService
from tests.modules.conftest import Env
from tests.modules.test_track_extend_arrows import _open, _place, _select_instance
from tests.modules.test_track_parts import _planner


def test_a_parent_stores_each_slot_and_reloads_it(env: Env) -> None:
    planner = _planner(env)
    catalog = _catalog(planner)
    track = env.track("Bindung", lanes=2)
    straight = _id(planner, "20020601")
    curve = _id(planner, "20020573")
    left = _id(planner, "20020560")
    outer = _id(planner, "20020563")
    inner = _id(planner, "20020592")

    bare = place_instance(empty_plan(track.id), straight, catalog[straight], 0, 0, 0, catalog)
    assert bare.instances[0].host_id is None
    plan = attach_accessory(bare, bare.instances[0].id, left, catalog[left], "left", catalog)
    plan = attach_accessory(plan, bare.instances[0].id, left, catalog[left], "right", catalog)
    curved = place_instance(empty_plan(track.id), curve, catalog[curve], 0, 0, 0, catalog)
    curved = attach_accessory(
        curved, curved.instances[0].id, outer, catalog[outer], "outer", catalog
    )
    curved = attach_accessory(
        curved, curved.instances[0].id, inner, catalog[inner], "inner", catalog
    )

    slots = {instance.attachment_slot for instance in plan.instances}
    assert slots == {None, "left", "right"}
    assert {instance.attachment_slot for instance in curved.instances} == {None, "outer", "inner"}
    for instance in (*plan.instances, *curved.instances):
        if instance.host_id is not None:
            assert (
                instance.host_id == plan.instances[0].id
                or instance.host_id == curved.instances[0].id
            )

    planner.save(plan)
    loaded = planner.load(track.id)
    by_slot = {instance.attachment_slot: instance for instance in loaded.instances}
    assert by_slot["left"].host_id == by_slot[None].id
    assert by_slot["right"].host_id == by_slot[None].id
    assert by_slot["left"].part_id == left
    parsed = parse_plan(track.id, to_document(loaded))
    assert {(item.host_id, item.attachment_slot) for item in parsed.instances} == {
        (item.host_id, item.attachment_slot) for item in loaded.instances
    }
    legacy = to_document(bare)
    assert "host_id" not in legacy["instances"][0]
    assert parse_plan(track.id, legacy).instances[0].x_mm == pytest.approx(0)


def test_reference_strips_fit_only_their_host(env: Env) -> None:
    planner = _planner(env)
    straight = _spec(planner, "20020601")
    short = _spec(planner, "20020611")
    flat_r3 = _spec(planner, "20020573")
    flat_r2 = _spec(planner, "20020572")
    flat_r3_15 = _with_angle(flat_r3, 15.0)
    banked_r3 = _spec(planner, "20020576")
    side = _spec(planner, "20020560")
    outer = _spec(planner, "20020563")
    inner = _spec(planner, "20020592")
    from slot_racing.modules.track_planner.attachment import attachment_fits

    assert attachment_fits(side, straight, "left")
    assert attachment_fits(side, straight, "right")
    assert attachment_fits(outer, flat_r3, "outer")
    assert attachment_fits(inner, flat_r3, "inner")
    assert not attachment_fits(outer, flat_r2, "outer")
    assert not attachment_fits(outer, flat_r3_15, "outer")
    assert not attachment_fits(outer, straight, "outer")
    assert not attachment_fits(side, flat_r3, "left")
    assert not attachment_fits(outer, flat_r3, "inner")
    assert not attachment_fits(inner, flat_r3, "outer")
    assert not attachment_fits(outer, banked_r3, "outer")
    assert not attachment_fits(side, short, "left")
    assert not attachment_fits(side, straight, "outer")


def test_parent_move_and_rotation_are_copied_without_drift(env: Env) -> None:
    planner = _planner(env)
    catalog = _catalog(planner)
    track = env.track("Folge", lanes=2)
    straight = _id(planner, "20020601")
    side = _id(planner, "20020560")
    plan = place_instance(empty_plan(track.id), straight, catalog[straight], 0, 0, 0, catalog)
    parent_id = plan.instances[0].id
    plan = attach_accessory(plan, parent_id, side, catalog[side], "left", catalog)
    plan = move_instance(plan, parent_id, 1200, 800)
    plan = rotate_instance(plan, parent_id, 90)
    parent = _by_id(plan, parent_id)
    accessory = _accessory(plan)
    assert (accessory.x_mm, accessory.y_mm, accessory.rotation_z_deg) == (
        parent.x_mm,
        parent.y_mm,
        parent.rotation_z_deg,
    )
    assert world_xy(parent, 0, -120) == pytest.approx((1320, 800))
    assert world_xy(accessory, 0, -120) == pytest.approx((1320, 800))
    for step in range(1, 9):
        plan = rotate_instance(plan, parent_id, step * 45)
    parent = _by_id(plan, parent_id)
    accessory = _accessory(plan)
    assert accessory.rotation_z_deg == parent.rotation_z_deg
    assert accessory.x_mm == parent.x_mm
    assert accessory.y_mm == parent.y_mm
    assert accessory.rotation_z_deg % 360 == 0
    nudged = move_instance(plan, accessory.id, 9999, 9999)
    assert (_accessory(nudged).x_mm, _accessory(nudged).y_mm) == pytest.approx((1200, 800))


def test_deleting_the_parent_removes_the_accessory_and_undo_restores_both(
    qtbot: QtBot, env: Env
) -> None:
    page = _open(qtbot, env, "Loeschen")
    parent = _place(page, "20020601")
    page.add_accessory(parent.id, "left")
    accessory_id = _accessory(page.plan()).id
    _select_instance(page, parent.id)
    page.delete_selected()
    assert page.plan().instances == ()
    page.undo()
    restored = {instance.id: instance for instance in page.plan().instances}
    assert restored[accessory_id].host_id == parent.id
    _select_instance(page, accessory_id)
    page.delete_selected()
    assert [instance.id for instance in page.plan().instances] == [parent.id]
    page.undo()
    assert _accessory(page.plan()).host_id == parent.id


def test_copy_points_at_the_new_parent(env: Env) -> None:
    planner = _planner(env)
    catalog = _catalog(planner)
    track = env.track("Kopie", lanes=2)
    straight = _id(planner, "20020601")
    side = _id(planner, "20020560")
    plan = place_instance(empty_plan(track.id), straight, catalog[straight], 10, 20, 0, catalog)
    parent_id = plan.instances[0].id
    plan = attach_accessory(plan, parent_id, side, catalog[side], "right", catalog)
    copied, _created = duplicate_instances(plan, [parent_id], 400, 0)
    originals = {instance.id for instance in plan.instances}
    clones = [instance for instance in copied.instances if instance.id not in originals]
    new_parent = next(instance for instance in clones if instance.host_id is None)
    new_accessory = next(instance for instance in clones if instance.host_id is not None)
    assert new_accessory.host_id == new_parent.id
    assert new_accessory.host_id != parent_id
    assert new_accessory.id != _accessory(plan).id
    cloned = clone_plan(plan, TrackId(int(track.id) + 1))
    cloned_accessory = _accessory(cloned)
    assert cloned_accessory.host_id != parent_id
    assert any(instance.id == cloned_accessory.host_id for instance in cloned.instances)


def test_a_group_moves_the_accessory_once(env: Env) -> None:
    planner = _planner(env)
    catalog = _catalog(planner)
    track = env.track("Gruppe", lanes=2)
    straight = _id(planner, "20020601")
    side = _id(planner, "20020560")
    plan = place_instance(empty_plan(track.id), straight, catalog[straight], 0, 0, 0, catalog)
    plan = place_instance(plan, straight, catalog[straight], 5000, 0, 0, catalog)
    parent_id = plan.instances[0].id
    other_id = plan.instances[1].id
    plan = attach_accessory(plan, parent_id, side, catalog[side], "left", catalog)
    plan = set_plan_grid(plan, enabled=False, grid_mm=100, snap_mm=1)
    plan = toggle_group(plan, [parent_id, other_id, _accessory(plan).id])
    assert _by_id(plan, parent_id).group_id is not None
    assert _accessory(plan).group_id is None
    moved = reposition_selection(
        plan,
        {parent_id: (80.0, 30.0), other_id: (5080.0, 30.0), _accessory(plan).id: (160.0, 60.0)},
        parent_id,
        catalog,
    )
    assert _by_id(moved, parent_id).x_mm == pytest.approx(80)
    assert _accessory(moved).x_mm == pytest.approx(80)
    assert _accessory(moved).y_mm == pytest.approx(30)
    assert _by_id(moved, other_id).x_mm == pytest.approx(5080)


def test_lane_lengths_ignore_border_strips(env: Env) -> None:
    planner = _planner(env)
    catalog = _catalog(planner)
    track = env.track("Laenge", lanes=2)
    straight = _id(planner, "20020601")
    side = _id(planner, "20020560")
    plan = place_instance(empty_plan(track.id), straight, catalog[straight], 0, 0, 0, catalog)
    for _index in range(3):
        plan = extend_from_connector(plan, plan.instances[-1].id, 1, "straight", catalog)
    before = display_lane_lengths(plan.instances, catalog)
    assert before
    plan = attach_accessory(plan, plan.instances[0].id, side, catalog[side], "left", catalog)
    plan = attach_accessory(plan, plan.instances[0].id, side, catalog[side], "right", catalog)
    assert display_lane_lengths(plan.instances, catalog) == before


def test_attached_parts_are_not_track_joints(env: Env) -> None:
    planner = _planner(env)
    catalog = _catalog(planner)
    track = env.track("Snap", lanes=2)
    straight = _id(planner, "20020601")
    side = _id(planner, "20020560")
    spec = catalog[straight]
    plan = place_instance(empty_plan(track.id), straight, spec, 0, 0, 0, catalog)
    parent = plan.instances[0]
    free = free_connector_indexes(parent, spec, [(parent, spec)])
    plan = attach_accessory(plan, parent.id, side, catalog[side], "left", catalog)
    accessory = _accessory(plan)
    assert catalog[side].connectors == ()
    assert (
        free_connector_indexes(
            accessory, catalog[side], [(parent, spec), (accessory, catalog[side])]
        )
        == ()
    )
    assert (
        free_connector_indexes(parent, spec, [(parent, spec), (accessory, catalog[side])]) == free
    )
    with pytest.raises(ValidationError) as error:
        extend_from_connector(plan, accessory.id, 0, "straight", catalog)
    assert error.value.key == "error.planner.extend"
    extended = extend_from_connector(plan, parent.id, 1, "straight", catalog)
    assert len(extended.instances) == 3
    plain = reposition_selection(plan, {parent.id: (10.0, 10.0)}, parent.id, catalog)
    # The strip is proposed as a second move and must not change the rail's snap.
    with_strip = reposition_selection(
        plan,
        {parent.id: (10.0, 10.0), accessory.id: (40.0, 40.0)},
        accessory.id,
        catalog,
    )
    assert _by_id(with_strip, parent.id).x_mm == pytest.approx(_by_id(plain, parent.id).x_mm)
    assert _by_id(with_strip, parent.id).y_mm == pytest.approx(_by_id(plain, parent.id).y_mm)
    assert _accessory(with_strip).x_mm == pytest.approx(_by_id(with_strip, parent.id).x_mm)


def test_stock_counts_one_strip_and_does_not_expand_a_box(env: Env) -> None:
    planner = _planner(env)
    catalog = _catalog(planner)
    track = env.track("Bestand", lanes=2)
    straight = _id(planner, "20020601")
    side = _id(planner, "20020560")
    planner.set_stock(side, 1)
    plan = place_instance(empty_plan(track.id), straight, catalog[straight], 0, 0, 0, catalog)
    plan = attach_accessory(plan, plan.instances[0].id, side, catalog[side], "left", catalog)
    owned = planner.stock_quantities()
    balance = analyze_inventory(plan.instances, owned).balance(side)
    assert owned[side] == 1
    assert balance.used == 1
    assert balance.available == 0
    plan = remove_instances(plan, [_accessory(plan).id])
    assert analyze_inventory(plan.instances, owned).balance(side).available == 1
    plan = attach_accessory(plan, plan.instances[0].id, side, catalog[side], "left", catalog)
    plan = attach_accessory(plan, plan.instances[0].id, side, catalog[side], "right", catalog)
    over = analyze_inventory(plan.instances, owned).balance(side)
    assert over.used == 2
    assert over.available == -1
    assert len(over.excess_ids) == 1
    assert owned[side] == 1


def test_strip_geometry_follows_the_slot_and_has_no_grooves() -> None:
    side = _article("20020560")
    outer = _article("20020563")
    inner = _article("20020592")
    left = placed_outline(side, "left")
    right = placed_outline(side, "right")
    assert all(y_mm <= -100 for _x_mm, y_mm in left)
    assert all(y_mm >= -140 for _x_mm, y_mm in left)
    assert all(y_mm >= 100 for _x_mm, y_mm in right)
    assert min(radii(placed_outline(outer, "outer"))) == pytest.approx(800)
    assert max(radii(placed_outline(outer, "outer"))) == pytest.approx(840)
    assert min(radii(placed_outline(inner, "inner"))) == pytest.approx(560)
    assert max(radii(placed_outline(inner, "inner"))) == pytest.approx(600)
    for spec, slot in ((side, "left"), (outer, "outer"), (inner, "inner")):
        figure = track_figure(spec, slot)
        assert figure.slots == ()
        assert figure.centerlines == ()
        assert figure.stripes
        assert track_figure(spec, slot) is figure
    source = Path("src/slot_racing/modules/track_planner/figure.py").read_text(encoding="utf-8")
    paint = Path("src/slot_racing/modules/track_planner/ui/track_paint.py").read_text(
        encoding="utf-8"
    )
    assert "20020560" not in source and "20020563" not in source and "20020592" not in source
    assert "20020560" not in paint and "20020563" not in paint


def test_accessory_actions_are_breadcrumbs_without_article_text(
    qtbot: QtBot, env: Env, tmp_path: Path
) -> None:
    service = install(tmp_path)
    try:
        page = _open(qtbot, env, "Log")
        parent = _place(page, "20020601")
        page.add_accessory(parent.id, "left")
        page.add_accessory(parent.id, "left")
        _select_instance(page, _accessory(page.plan()).id)
        page.delete_selected()
        names = [item.event for item in service.breadcrumbs.snapshot()]
        assert "ACCESSORY_ADD" in names
        assert "ACCESSORY_ADD_BLOCKED" in names
        assert "ACCESSORY_REMOVE" in names
        added = next(
            item for item in service.breadcrumbs.snapshot() if item.event == "ACCESSORY_ADD"
        )
        fields = dict(added.fields)
        assert fields["parent_instance_id"] == parent.id
        assert fields["attachment_slot"] == "left"
        assert "part_definition_id" in fields
        assert "accessory_instance_id" in fields
        rendered = "\n".join(item.render() for item in service.breadcrumbs.snapshot())
        assert "20020560" not in rendered
        assert "Randstreifen" not in rendered
    finally:
        running = current()
        if running is not None:
            running.close()


def test_the_menu_offers_only_compatible_sides(qtbot: QtBot, env: Env) -> None:
    from PySide6.QtGui import QAction

    page = _open(qtbot, env, "Menue")
    parent = _place(page, "20020601")
    menu = page.accessory_menu(parent.id)
    assert menu is not None
    left = menu.findChild(QAction, "planner-accessory-left")
    right = menu.findChild(QAction, "planner-accessory-right")
    assert left is not None and left.isEnabled() and left.text() == "Links"
    assert right is not None and right.isEnabled()
    assert menu.findChild(QAction, "planner-accessory-outer") is None
    page.add_accessory(parent.id, "left")
    again = page.accessory_menu(parent.id)
    assert again is not None
    taken = again.findChild(QAction, "planner-accessory-left")
    assert taken is not None and not taken.isEnabled()

    curve = _id(_planner(env), "20020572")
    updated = place_instance(page.plan(), curve, page._parts[curve], 4000, 0, 0, page._parts)
    page._commit(updated, updated.instances[-1].id)
    host = next(instance for instance in page.plan().instances if instance.part_id == curve)
    curve_menu = page.accessory_menu(host.id)
    assert curve_menu is not None
    outer = curve_menu.findChild(QAction, "planner-accessory-outer")
    assert outer is not None and not outer.isEnabled()
    assert curve_menu.findChild(QAction, "planner-accessory-left") is None


def test_collection_mode_uses_the_existing_stock_rule(qtbot: QtBot, env: Env) -> None:
    page = _open(qtbot, env, "Sammlung")
    page.announce_stock_problem = lambda: None
    parent = _place(page, "20020601")
    side = _id(page._planner, "20020560")
    straight = _id(page._planner, "20020601")
    page._planner.set_stock(straight, 1)
    page._planner.set_stock(side, 0)
    page._stock = page._planner.stock_quantities()
    page.mode_collection.click()
    page.add_accessory(parent.id, "right")
    assert len(page.plan().instances) == 1
    page.show_all_parts.click()
    page.add_accessory(parent.id, "right")
    assert len(page.plan().instances) == 2
    assert _accessory(page.plan()).host_id == parent.id


def test_an_old_border_row_refreshes_without_moving_instances(env: Env) -> None:
    planner = _planner(env)
    border_id = _id(planner, "20020560")
    track = env.track("Alt", lanes=2)
    plan = add_instance(
        empty_plan(track.id),
        PartInstance(id="legacy-strip", part_id=border_id, x_mm=12, y_mm=34, rotation_z_deg=15),
    )
    planner.save(plan)
    with env.runtime.database.session() as session:
        row = session.get(TrackPartDefinition, border_id)
        assert row is not None
        row.attachment_host_shape = None
        row.attachment_slots = None
        session.add(
            TrackPartConnector(
                part_id=border_id,
                name="a",
                x_mm=-172.5,
                y_mm=0,
                z_mm=0,
                direction_deg=180,
                kind="border",
                lanes=[1, 2],
                sort_order=0,
            )
        )
    planner.library.ensure_seed()
    stored = planner.library.require(border_id).spec
    assert stored.connectors == ()
    assert stored.attachment is not None
    assert stored.name == "Randstreifen Standardgerade"
    loaded = planner.load(track.id)
    assert loaded.instances[0].id == "legacy-strip"
    assert loaded.instances[0].x_mm == pytest.approx(12)
    assert loaded.instances[0].y_mm == pytest.approx(34)
    assert loaded.instances[0].rotation_z_deg == pytest.approx(15)
    assert loaded.instances[0].host_id is None
    with env.runtime.database.session() as session:
        assert (
            session.scalars(
                select(TrackPartConnector).where(TrackPartConnector.part_id == border_id)
            ).all()
            == []
        )


def test_backup_restores_the_parent_link(tmp_path: Path) -> None:
    source_dir = tmp_path / "source"
    source_dir.mkdir()
    source = Runtime.create(
        AppConfig(database_path=source_dir / "slot_racing.db", language="de"),
        config_path=source_dir / "config.json",
    )
    try:
        tracks = source.services.get(TrackService)
        planner = source.services.get(TrackPlannerService)
        track = tracks.create_track(TrackInput(name="Backup", lane_count=2))
        catalog = {record.id: record.spec for record in planner.list_parts()}
        straight = next(
            record.id for record in planner.list_parts() if record.spec.article_number == "20020601"
        )
        side = next(
            record.id for record in planner.list_parts() if record.spec.article_number == "20020563"
        )
        curve = next(
            record.id for record in planner.list_parts() if record.spec.article_number == "20020573"
        )
        plan = place_instance(empty_plan(track.id), curve, catalog[curve], 5, 6, 0, catalog)
        plan = attach_accessory(plan, plan.instances[0].id, side, catalog[side], "outer", catalog)
        plan = place_instance(plan, straight, catalog[straight], 800, 0, 0, catalog)
        planner.save(plan)
        archive = create_backup(source.database, source.config, tmp_path / "backups")
        track_id = int(track.id)
        parent_part = curve
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
        accessory = next(instance for instance in loaded.instances if instance.host_id is not None)
        parent = next(instance for instance in loaded.instances if instance.id == accessory.host_id)
        assert parent.part_id == parent_part
        assert accessory.attachment_slot == "outer"
        assert parent.x_mm == pytest.approx(5)
        assert parent.y_mm == pytest.approx(6)
        assert accessory.x_mm == pytest.approx(parent.x_mm)
        assert len(loaded.instances) == 3
    finally:
        runtime.shutdown()


def _catalog(planner: TrackPlannerService) -> dict[int, PartSpec]:
    return {record.id: record.spec for record in planner.list_parts()}


def _id(planner: TrackPlannerService, article: str) -> int:
    for record in planner.list_parts():
        if record.spec.article_number == article:
            return record.id
    raise AssertionError(article)


def _spec(planner: TrackPlannerService, article: str) -> PartSpec:
    return _catalog(planner)[_id(planner, article)]


def _article(article: str) -> PartSpec:
    return next(spec for spec in standard_catalog() if spec.article_number == article)


def _with_angle(spec: PartSpec, angle: float) -> PartSpec:
    return replace(spec, angle_deg=angle)


def _by_id(plan: TrackPlan, instance_id: str) -> PartInstance:
    return next(instance for instance in plan.instances if instance.id == instance_id)


def _accessory(plan: TrackPlan) -> PartInstance:
    return next(instance for instance in plan.instances if instance.host_id is not None)
