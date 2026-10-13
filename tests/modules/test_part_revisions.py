"""Geometry revisions stay off saved plans until a track adopts them."""

from __future__ import annotations

from dataclasses import replace

import pytest
from sqlalchemy import select

from slot_racing.modules.track_planner.document import empty_plan, move_instance, place_instance
from slot_racing.modules.track_planner.models import TrackPartDefinition
from slot_racing.modules.track_planner.parts import (
    ORIGIN_CATALOG,
    ORIGIN_MODIFIED,
    STANDARD_STRAIGHT_ARTICLE,
    PartRecord,
    PartSpec,
)
from slot_racing.modules.track_planner.service import TrackPlannerService
from slot_racing.modules.tracks.models import TrackLayout
from tests.modules.conftest import Env
from tests.modules.test_track_parts import _planner


def test_a_rename_keeps_the_lineage_and_does_not_reseed_the_catalogue(env: Env) -> None:
    planner = _planner(env)
    straight = _article(planner, STANDARD_STRAIGHT_ARTICLE)
    renamed = planner.update_part(straight.id, replace(straight.spec, name="Umbenannt"))
    assert renamed.id == straight.id
    assert renamed.lineage_id == straight.lineage_id
    assert renamed.revision == 1
    assert renamed.origin == ORIGIN_CATALOG
    again = [
        record
        for record in planner.list_parts()
        if record.spec.article_number == STANDARD_STRAIGHT_ARTICLE
    ]
    assert len(again) == 1
    assert again[0].spec.name == "Umbenannt"
    assert again[0].lineage_id == straight.lineage_id


def test_editing_a_used_part_freezes_the_placed_geometry(env: Env) -> None:
    planner = _planner(env)
    straight = _article(planner, STANDARD_STRAIGHT_ARTICLE)
    track = env.track("Oval", lanes=2)
    planner.set_stock(straight.id, 3)
    plan = place_instance(
        empty_plan(track.id), straight.id, straight.spec, 0, 0, 15, _catalog(straight)
    )
    plan = place_instance(plan, straight.id, straight.spec, 4000, 0, 15, _catalog(straight))
    planner.save(plan)
    revised = planner.update_part(straight.id, replace(straight.spec, length_mm=400))
    assert revised.id != straight.id
    assert revised.lineage_id == straight.lineage_id
    assert revised.revision == 2
    assert revised.origin == ORIGIN_MODIFIED
    assert revised.spec.length_mm == pytest.approx(400)
    frozen = planner.library.require(straight.id)
    assert frozen.spec.length_mm == pytest.approx(345)
    assert frozen.is_current is False
    loaded = planner.load(track.id)
    assert [item.part_id for item in loaded.instances] == [straight.id, straight.id]
    assert [(item.x_mm, item.y_mm, item.rotation_z_deg) for item in loaded.instances] == [
        (0, 0, 15),
        (4000, 0, 15),
    ]
    offered = {record.id for record in planner.list_parts()}
    assert revised.id in offered
    assert straight.id not in offered
    owned = planner.stock_quantities()
    assert owned[revised.id] == 3
    assert straight.id not in owned
    report = planner.inventory_for(loaded.instances)
    balance = report.balance(revised.id)
    assert balance.used == 2
    assert balance.owned == 3
    assert balance.available == 1


def test_adopting_updates_only_the_chosen_track_and_leaves_the_archive(env: Env) -> None:
    planner = _planner(env)
    straight = _article(planner, STANDARD_STRAIGHT_ARTICLE)
    first = env.track("Eins", lanes=2)
    second = env.track("Zwei", lanes=2)
    catalog = _catalog(straight)
    planner.save(
        place_instance(empty_plan(first.id), straight.id, straight.spec, 10, 20, 5, catalog)
    )
    planner.save(
        place_instance(empty_plan(second.id), straight.id, straight.spec, 30, 40, 0, catalog)
    )
    revised = planner.update_part(straight.id, replace(straight.spec, length_mm=400))
    affected = planner.tracks_on_previous_geometry(revised.id)
    assert {track_id for track_id, _name in affected} == {first.id, second.id}
    assert dict(affected)[first.id] == "Eins"
    moved = move_instance(planner.load(first.id), planner.load(first.id).instances[0].id, 12, 20)
    planner.save(moved)
    assert planner.adopt_revision(revised.id, ()) == 0
    assert planner.load(first.id).instances[0].part_id == straight.id
    changed = planner.adopt_revision(revised.id, (first.id,))
    assert changed == 1
    adopted = planner.load(first.id)
    assert adopted.instances[0].part_id == revised.id
    pose = adopted.instances[0]
    assert (pose.x_mm, pose.y_mm, pose.rotation_z_deg) == (12, 20, 5)
    assert planner.load(second.id).instances[0].part_id == straight.id
    with env.runtime.database.session() as session:
        archives = session.scalars(select(TrackLayout).where(TrackLayout.name != "plan")).all()
    assert archives
    assert all(
        instance["part_id"] == straight.id
        for layout in archives
        for instance in layout.data["instances"]
    )


def test_restore_puts_the_catalogue_back_without_moving_a_frozen_plan(env: Env) -> None:
    planner = _planner(env)
    straight = _article(planner, STANDARD_STRAIGHT_ARTICLE)
    track = env.track("Restore", lanes=2)
    planner.save(
        place_instance(
            empty_plan(track.id), straight.id, straight.spec, 1, 2, 0, _catalog(straight)
        )
    )
    revised = planner.update_part(straight.id, replace(straight.spec, length_mm=400))
    restored = planner.restore_catalog(revised.id)
    assert restored.spec.length_mm == pytest.approx(345)
    assert restored.origin == ORIGIN_CATALOG
    assert planner.load(track.id).instances[0].part_id == straight.id
    assert planner.library.require(straight.id).spec.length_mm == pytest.approx(345)


def test_an_unused_geometry_edit_stays_on_the_same_row(env: Env) -> None:
    planner = _planner(env)
    straight = _article(planner, STANDARD_STRAIGHT_ARTICLE)
    edited = planner.update_part(straight.id, replace(straight.spec, length_mm=400))
    assert edited.id == straight.id
    assert edited.origin == ORIGIN_MODIFIED
    assert edited.spec.length_mm == pytest.approx(400)
    reloaded = _article(planner, STANDARD_STRAIGHT_ARTICLE)
    assert reloaded.id == straight.id
    assert reloaded.spec.length_mm == pytest.approx(400)


def test_shared_article_numbers_stay_separate(env: Env) -> None:
    planner = _planner(env)
    matches = planner.library.current_with_article("20020563")
    assert len(matches) == 2
    assert len({record.lineage_id for record in matches}) == 2
    assert {record.spec.name for record in matches} == {
        "Außenrandstreifen Kurve 3/30°",
        "Endstück Kurve 3/30°",
    }


def test_migration_assigns_a_lineage_without_changing_a_placed_part(env: Env) -> None:
    planner = _planner(env)
    straight = _article(planner, STANDARD_STRAIGHT_ARTICLE)
    with env.runtime.database.session() as session:
        row = session.get(TrackPartDefinition, straight.id)
        assert row is not None
        assert row.lineage_id
        assert row.revision == 1
        assert row.is_current is True
        assert row.origin == ORIGIN_CATALOG
        assert row.catalog_key
        assert row.manufacturer == "Carrera"
        assert row.length_mm == pytest.approx(345)


def _article(planner: TrackPlannerService, article: str) -> PartRecord:
    planner.list_parts()
    return next(record for record in planner.list_parts() if record.spec.article_number == article)


def _catalog(record: PartRecord) -> dict[int, PartSpec]:
    return {record.id: record.spec}
