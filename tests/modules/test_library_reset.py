"""Reset and reconcile the managed catalogue without touching custom parts."""

from __future__ import annotations

import pytest
from sqlalchemy import func, select

from slot_racing.core.storage.base import Base
from slot_racing.modules.track_planner.document import empty_plan, place_instance
from slot_racing.modules.track_planner.models import (
    TrackPartDefinition,
    TrackPartPackageStock,
    TrackPartStock,
    TrackPlanInstance,
)
from slot_racing.modules.track_planner.packages import (
    PackageContentSpec,
    PackageSpec,
    standard_packages,
)
from slot_racing.modules.track_planner.parts import (
    CURVE,
    ORIGIN_CATALOG,
    ORIGIN_CUSTOM,
    build_part,
)
from tests.modules.conftest import Env
from tests.modules.test_track_parts import _planner, _straight_spec

_KEPT_TABLES = tuple(name for name in Base.metadata.tables if not name.startswith("track_part_"))


def test_regular_reconcile_drops_an_obsolete_catalog_part_and_keeps_a_custom_one(
    env: Env,
) -> None:
    planner = _planner(env)
    seeded = planner.list_parts()
    catalog_name = next(
        record.spec.name for record in seeded if record.spec.article_number == "20571"
    )
    custom = planner.add_part(_straight_spec("EB-BLEIBT", lanes=2))
    planner.set_stock(custom.id, 3)
    obsolete = planner.add_part(_straight_spec("ALT-WEG", lanes=2))
    _mark_supplied(env, obsolete.id, source_id="gone-source", catalog_key="gone-source")
    twin = planner.add_part(
        build_part(
            article_number="EB-NAME",
            scale="1:32",
            name=catalog_name,
            category=CURVE,
            length_mm=None,
            width_mm=None,
            height_mm=None,
            radius_mm=400,
            angle_deg=60,
            lane_count=2,
        )
    )
    before = _table_counts(env)
    planner.library.ensure_seed()
    active = {record.id for record in planner.list_parts()}
    assert custom.id in active
    assert twin.id in active
    assert obsolete.id not in active
    with env.runtime.database.session() as session:
        assert session.get(TrackPartDefinition, obsolete.id) is None
        stock = session.get(TrackPartStock, custom.id)
        assert stock is not None and stock.quantity == 3
    assert _table_counts(env) == before


def test_a_referenced_obsolete_part_is_retired_and_stays_resolvable(env: Env) -> None:
    planner = _planner(env)
    planner.list_parts()
    obsolete = planner.add_part(_straight_spec("ALT-REF", lanes=2))
    planner.set_stock(obsolete.id, 4)
    track = env.track("Altstrecke", lanes=2)
    plan = place_instance(
        empty_plan(track.id), obsolete.id, obsolete.spec, 12, 8, 0, {obsolete.id: obsolete.spec}
    )
    planner.save(plan)
    _mark_supplied(env, obsolete.id, source_id="", catalog_key="archived-straight")
    kept = _table_counts(env)
    report = planner.preview_library_reset()
    assert any(entry.article_number == "ALT-REF" and entry.stock == 4 for entry in report.retired)
    assert all(entry.article_number != "ALT-REF" for entry in report.removed)
    with env.runtime.database.session() as session:
        assert session.get(TrackPartDefinition, obsolete.id) is not None
    applied = planner.reset_library()
    assert any(entry.article_number == "ALT-REF" for entry in applied.retired)
    assert all(record.id != obsolete.id for record in planner.list_parts())
    with env.runtime.database.session() as session:
        row = session.get(TrackPartDefinition, obsolete.id)
        assert row is not None and row.legacy and not row.selectable
        stock = session.get(TrackPartStock, obsolete.id)
        assert stock is not None and stock.quantity == 4
        assert session.get(TrackPlanInstance, plan.instances[0].id) is not None
    loaded = planner.load(track.id)
    assert loaded.instances[0].part_id == obsolete.id
    assert planner.library.require(obsolete.id).spec.article_number == "ALT-REF"
    again = planner.preview_library_reset()
    assert again.is_empty()
    assert planner.reset_library().is_empty()
    assert _table_counts(env) == kept


def test_reset_updates_a_catalog_part_in_place_and_keeps_stock(env: Env) -> None:
    planner = _planner(env)
    straight = next(
        record
        for record in planner.list_parts()
        if record.spec.article_number == "20509" and not record.spec.piece_key
    )
    package = next(
        item
        for item in planner.list_packages()
        if item.article_number == straight.spec.article_number
    )
    planner.set_stock(straight.id, 7)
    planner.set_package_stock(package.id, 2)
    original = straight.spec.name
    with env.runtime.database.session() as session:
        row = session.get(TrackPartDefinition, straight.id)
        assert row is not None
        row.name = "Umbenannt"
    preview = planner.preview_library_reset()
    assert any(
        entry.article_number == "20509" and entry.name == "Umbenannt" for entry in preview.updated
    )
    with env.runtime.database.session() as session:
        row = session.get(TrackPartDefinition, straight.id)
        assert row is not None and row.name == "Umbenannt"
    planner.reset_library()
    restored = planner.library.require(straight.id)
    assert restored.id == straight.id
    assert restored.spec.name == original
    assert planner.stock_breakdown()[straight.id].adjustment == 7
    assert next(item.quantity for item in planner.list_packages() if item.id == package.id) == 2
    assert planner.preview_library_reset().is_empty()


def test_ensure_seed_does_not_overwrite_a_modified_catalog_name(env: Env) -> None:
    planner = _planner(env)
    straight = next(
        record
        for record in planner.list_parts()
        if record.spec.article_number == "20509" and not record.spec.piece_key
    )
    with env.runtime.database.session() as session:
        row = session.get(TrackPartDefinition, straight.id)
        assert row is not None
        row.name = "Eigene Bezeichnung"
    planner.library.ensure_seed()
    assert planner.library.require(straight.id).spec.name == "Eigene Bezeichnung"


def test_reset_keeps_a_custom_package_and_suppresses_an_owned_old_box(env: Env) -> None:
    planner = _planner(env)
    planner.list_parts()
    custom = planner.add_part(_straight_spec("EB-BOX", lanes=2))
    own = planner.add_package(
        PackageSpec(
            "Werkstatt",
            "BOX-1",
            "Eigene Kiste",
            (PackageContentSpec(custom.spec.name, custom.spec.article_number, 2),),
        )
    )
    planner.set_package_stock(own.id, 3)
    old = standard_packages()[1]
    for content in old.contents:
        planner.add_part(_straight_spec(content.article_number, lanes=2))
        with env.runtime.database.session() as session:
            row = session.scalar(
                select(TrackPartDefinition).where(
                    TrackPartDefinition.article_number == content.article_number,
                    TrackPartDefinition.is_current.is_(True),
                )
            )
            assert row is not None
            row.name = content.part_name
    boxed = planner.add_package(old)
    planner.set_package_stock(boxed.id, 1)
    with env.runtime.database.session() as session:
        from slot_racing.modules.track_planner.models import TrackPartPackage

        stored_box = session.get(TrackPartPackage, boxed.id)
        assert stored_box is not None
        stored_box.bundled = True
    planner.reset_library()
    packages = {package.id: package for package in planner.list_packages()}
    assert packages[own.id].quantity == 3
    assert boxed.id not in packages
    with env.runtime.database.session() as session:
        from slot_racing.modules.track_planner.models import TrackPartPackage

        stored = session.get(TrackPartPackage, boxed.id)
        assert stored is not None and stored.suppressed and stored.bundled
        stock = session.get(TrackPartPackageStock, boxed.id)
        assert stock is not None and stock.quantity == 1
    assert any(record.id == custom.id for record in planner.list_parts())


def test_a_failed_reset_rolls_back(env: Env, monkeypatch: pytest.MonkeyPatch) -> None:
    planner = _planner(env)
    straight = next(
        record
        for record in planner.list_parts()
        if record.spec.article_number == "20509" and not record.spec.piece_key
    )
    with env.runtime.database.session() as session:
        row = session.get(TrackPartDefinition, straight.id)
        assert row is not None
        row.name = "Abweichend"
    obsolete = planner.add_part(_straight_spec("ALT-ATOM", lanes=2))
    _mark_supplied(env, obsolete.id, source_id="atom-source", catalog_key="atom-source")

    def boom(*_args: object, **_kwargs: object) -> None:
        raise RuntimeError("stop")

    monkeypatch.setattr(planner.library, "_apply", boom)
    with pytest.raises(RuntimeError, match="stop"):
        planner.reset_library()
    with env.runtime.database.session() as session:
        row = session.get(TrackPartDefinition, straight.id)
        assert row is not None and row.name == "Abweichend"
        assert session.get(TrackPartDefinition, obsolete.id) is not None
        assert (
            session.scalar(
                select(func.count())
                .select_from(TrackPartDefinition)
                .where(TrackPartDefinition.source_id == straight.spec.source_id)
            )
            == 1
        )


def _mark_supplied(env: Env, part_id: int, *, source_id: str, catalog_key: str) -> None:
    with env.runtime.database.session() as session:
        row = session.get(TrackPartDefinition, part_id)
        assert row is not None
        row.origin = ORIGIN_CATALOG
        row.source_id = source_id
        row.catalog_key = catalog_key
        assert row.origin != ORIGIN_CUSTOM


def _table_counts(env: Env) -> dict[str, int]:
    with env.runtime.database.session() as session:
        return {
            name: int(session.scalar(select(func.count()).select_from(table)) or 0)
            for name, table in Base.metadata.tables.items()
            if name in _KEPT_TABLES
        }
