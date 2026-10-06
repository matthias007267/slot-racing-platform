"""Catalogue parts, start/finish, grid defaults, toolbar and library delete."""

from __future__ import annotations

import math
from pathlib import Path

import pytest
from PySide6.QtGui import QFont
from PySide6.QtWidgets import QApplication, QPushButton
from pytestqt.qtbot import QtBot
from sqlalchemy import select

from slot_racing.app.runtime import Runtime
from slot_racing.core.backup import create_backup, restore_backup
from slot_racing.core.config import AppConfig
from slot_racing.core.domain import TrackId
from slot_racing.core.errors import ValidationError
from slot_racing.modules.track_planner.document import empty_plan, parse_plan, place_instance
from slot_racing.modules.track_planner.figure import track_figure
from slot_racing.modules.track_planner.inventory import analyze_inventory
from slot_racing.modules.track_planner.lane_length import connected_runs
from slot_racing.modules.track_planner.models import TrackPartDefinition, TrackPartStock
from slot_racing.modules.track_planner.parts import (
    BOTTLENECK_INNER_OFFSET_MM,
    BOTTLENECK_LENGTH_MM,
    CATALOG_SCALE,
    DEFAULT_GRID_MM,
    DEFAULT_SNAP_MM,
    SPECIAL,
    START_FINISH_ARTICLE,
    STRAIGHT,
    PartInstance,
    curve_lane_radius,
    identity_key,
    is_start_finish_part,
    lane_offset_mm,
    span_point,
    standard_catalog,
)
from slot_racing.modules.track_planner.service import TrackPlannerService
from slot_racing.modules.track_planner.ui.library_manager import LibraryManager
from slot_racing.modules.track_planner.ui.page import PlannerPage
from slot_racing.modules.tracks.service import TrackService
from tests.modules.conftest import Env
from tests.modules.test_track_parts import _library_row, _planner, _select, _straight_spec
from tests.modules.test_ui_management import open_page


def test_the_new_carrera_parts_match_the_verified_catalogue() -> None:
    by_article = {spec.article_number: spec for spec in standard_catalog()}
    identities = [identity_key(spec.name, spec.article_number) for spec in standard_catalog()]
    assert len(identities) == len(set(identities))
    assert "system" not in by_article["20020516"].__dataclass_fields__

    narrow = by_article["20020516"]
    assert narrow.name == "Engstelle"
    assert narrow.scale == CATALOG_SCALE
    assert narrow.category == SPECIAL
    assert narrow.length_mm == pytest.approx(BOTTLENECK_LENGTH_MM)
    assert narrow.lane_count == 2
    assert len(narrow.connectors) == 2
    assert narrow.connectors[0].lanes == (1, 2)
    assert narrow.slot_paths
    figure = track_figure(narrow)
    assert len(figure.slots) == 2
    for index, path in enumerate(figure.slots):
        start = span_point(path.spans[0], 0.0)
        end = span_point(path.spans[-1], 1.0)
        y = lane_offset_mm(index, 2)
        assert start[1] == pytest.approx(y)
        assert end[1] == pytest.approx(y)
        assert abs(start[0]) == pytest.approx(BOTTLENECK_LENGTH_MM / 2)
        narrowed = span_point(path.spans[1], 1.0)
        assert abs(narrowed[1]) == pytest.approx(BOTTLENECK_INNER_OFFSET_MM)
        assert abs(narrowed[1]) < abs(y)
    assert by_article["20030350"].name == "Engstelle links"
    assert by_article["20030351"].name == "Engstelle rechts"

    expected = {
        "20020574": (300.0, 30.0),
        "20020575": (500.0, 30.0),
        "20020576": (700.0, 30.0),
        "20020579": (900.0, 15.0),
    }
    flat = {"20020575": "20020572", "20020576": "20020573", "20020579": "20020578"}
    for article, (radius, angle) in expected.items():
        spec = by_article[article]
        assert spec.name.startswith("Steilkurve")
        assert spec.scale == "1:24"
        assert spec.category == SPECIAL
        assert spec.radius_mm == pytest.approx(radius)
        assert spec.angle_deg == pytest.approx(angle)
        assert spec.lane_count == 2
        assert len(spec.connectors) == 2
        if article in flat:
            assert spec.radius_mm == pytest.approx(by_article[flat[article]].radius_mm)
            assert spec.angle_deg == pytest.approx(by_article[flat[article]].angle_deg)
            assert spec.name != by_article[flat[article]].name
        outer = curve_lane_radius(radius, 0, 2)
        inner = curve_lane_radius(radius, 1, 2)
        assert outer == pytest.approx(radius + 50)
        assert inner == pytest.approx(radius - 50)

    rail = by_article[START_FINISH_ARTICLE]
    assert rail.name == "Anschlussgerade"
    assert rail.scale == "1:24"
    assert rail.category == STRAIGHT
    assert rail.length_mm == pytest.approx(345)
    assert rail.lane_count == 2
    assert len(rail.connectors) == 2
    assert is_start_finish_part(rail)
    assert not is_start_finish_part(by_article["20020601"])
    assert rail.article_number != "20020601"


def test_the_bottleneck_and_steep_curves_have_a_real_lane_length() -> None:
    by_article = {spec.article_number: spec for spec in standard_catalog()}
    narrow = by_article["20020516"]
    instance = PartInstance("narrow", 1, 0.0, 0.0)
    lanes = connected_runs((instance,), {1: narrow})[0].lanes
    assert len(lanes) == 2
    assert lanes[0].length_mm == pytest.approx(lanes[1].length_mm)
    assert lanes[0].length_mm > BOTTLENECK_LENGTH_MM

    steep = by_article["20020575"]
    curve = PartInstance("steep", 1, 0.0, 0.0)
    curved = connected_runs((curve,), {1: steep})[0].lanes
    lengths = sorted(row.length_mm for row in curved)
    assert lengths[0] == pytest.approx(450 * math.pi / 6)
    assert lengths[1] == pytest.approx(550 * math.pi / 6)


def test_placing_the_connecting_rail_does_not_spend_a_standard_straight(env: Env) -> None:
    planner = _planner(env)
    records = {record.spec.article_number: record for record in planner.list_parts()}
    straight = records["20020601"]
    rail = records["20020518"]
    planner.set_stock(straight.id, 10)
    planner.set_stock(rail.id, 1)
    track = env.track("Anschluss", lanes=2)
    catalog = {straight.id: straight.spec, rail.id: rail.spec}
    plan = place_instance(empty_plan(track.id), rail.id, rail.spec, 0, 0, 0, catalog)
    assert plan.instances[0].start_straight
    assert plan.instances[0].part_id == rail.id
    plan = place_instance(plan, straight.id, straight.spec, 2000, 0, 0, catalog)
    assert not plan.instances[1].start_straight
    planner.save(plan)
    loaded = planner.load(track.id)
    assert loaded.instances[0].start_straight
    assert loaded.instances[0].part_id == rail.id
    owned = planner.stock_quantities()
    report = analyze_inventory(loaded.instances, owned)
    assert report.balance(straight.id).owned == 10
    assert report.balance(straight.id).used == 1
    assert report.balance(rail.id).owned == 1
    assert report.balance(rail.id).used == 1
    assert report.balance(rail.id).available == 0
    rotated = loaded.instances[0]
    assert rotated.start_straight
    planner.save(place_instance(loaded, rail.id, rail.spec, 0, 800, 90, catalog))
    again = planner.load(track.id)
    marked = [item for item in again.instances if item.start_straight]
    assert len(marked) == 1
    assert marked[0].part_id == rail.id


def test_a_new_plan_uses_the_grid_and_snap_defaults_and_keeps_saved_values(env: Env) -> None:
    assert DEFAULT_GRID_MM == 100
    assert DEFAULT_SNAP_MM == 200
    fresh = empty_plan(TrackId(1))
    assert fresh.grid_mm == pytest.approx(100)
    assert fresh.snap_mm == pytest.approx(200)
    missing = parse_plan(
        TrackId(1),
        {"version": 1, "direction": "clockwise", "pieces": [], "markers": []},
    )
    assert missing.grid_mm == pytest.approx(100)
    assert missing.snap_mm == pytest.approx(200)

    planner = _planner(env)
    track = env.track("Raster", lanes=2)
    opened = planner.load(track.id)
    assert opened.grid_mm == pytest.approx(100)
    assert opened.snap_mm == pytest.approx(200)
    part = planner.add_part(_straight_spec("EB-POS", lanes=2))
    free = empty_plan(track.id)
    assert free.grid_enabled is False
    placed = place_instance(free, part.id, part.spec, 12.4, 7.3, 0, {part.id: part.spec})
    assert placed.instances[0].x_mm == pytest.approx(12.4)
    assert placed.instances[0].y_mm == pytest.approx(7.3)

    from slot_racing.modules.track_planner.document import set_plan_grid

    stored = set_plan_grid(placed, enabled=True, grid_mm=40, snap_mm=80)
    planner.save(stored)
    reloaded = TrackPlannerService(env.runtime.database, env.tracks).load(track.id)
    assert reloaded.grid_mm == pytest.approx(40)
    assert reloaded.snap_mm == pytest.approx(80)
    assert reloaded.grid_enabled is True


def test_the_planner_shows_the_defaults_and_keeps_a_saved_choice(qtbot: QtBot, env: Env) -> None:
    track = env.track("Anzeige", lanes=2)
    window, page = open_page(qtbot, env, "track_planner")
    window.show()
    assert isinstance(page, PlannerPage)
    _select(page, track.id)
    assert page.grid_size.value() == pytest.approx(100)
    assert page.snap_distance.value() == pytest.approx(200)
    assert page.plan().grid_mm == pytest.approx(100)
    assert page.plan().snap_mm == pytest.approx(200)
    page.grid_size.setValue(40)
    page.snap_distance.setValue(80)
    page.save()
    again, reopened = open_page(qtbot, env, "track_planner")
    again.show()
    assert isinstance(reopened, PlannerPage)
    _select(reopened, track.id)
    assert reopened.grid_size.value() == pytest.approx(40)
    assert reopened.snap_distance.value() == pytest.approx(80)
    assert reopened.plan().grid_mm == pytest.approx(40)
    assert reopened.plan().snap_mm == pytest.approx(80)
    before = (reopened.plan().instances, reopened.plan().grid_enabled)
    reopened.grid.setChecked(False)
    assert reopened.plan().grid_enabled is False
    assert reopened.plan().instances == before[0]


def test_toolbar_buttons_stay_readable_when_the_window_shrinks(qtbot: QtBot, env: Env) -> None:
    env.track("Toolbar", lanes=2)
    window, page = open_page(qtbot, env, "track_planner")
    window.show()
    assert isinstance(page, PlannerPage)
    buttons = [widget for widget in page._toolbar_widgets if isinstance(widget, QPushButton)]
    assert buttons

    def readable(width: int, height: int) -> None:
        window.resize(width, height)
        toolbar_layout = page.toolbar.layout()
        window_layout = window.layout()
        assert toolbar_layout is not None and window_layout is not None
        toolbar_layout.activate()
        window_layout.activate()
        QApplication.processEvents()
        boxes = []
        for button in buttons:
            assert button.isVisible()
            assert button.width() + 1 >= button.minimumWidth()
            text_width = button.fontMetrics().horizontalAdvance(button.text())
            assert button.width() >= text_width
            assert not button.text().endswith("…")
            rect = button.geometry()
            assert rect.right() <= page.toolbar.width() + 1
            assert rect.bottom() <= page.toolbar.height() + 1
            boxes.append(rect)
        for index, left in enumerate(boxes):
            for right in boxes[index + 1 :]:
                assert not left.intersects(right)
        for control in (
            page.compatible,
            page.manage_library,
            page.delete_track_button,
            page.manage_stock,
        ):
            assert control.width() >= control.fontMetrics().horizontalAdvance(control.text())

    for size in ((1280, 800), (1024, 768), (860, 640), (720, 560)):
        readable(*size)

    wide = page.toolbar.sizeHint().width() + window.width() - page.toolbar.width() + 40
    window.resize(max(wide, 1800), 800)
    wide_layout = page.toolbar.layout()
    assert wide_layout is not None
    wide_layout.activate()
    QApplication.processEvents()
    tops = {button.geometry().top() for button in buttons}
    assert len(tops) == 1

    font = QFont(page.font())
    font.setPointSize(font.pointSize() + 4)
    page.setFont(font)
    page._fit_toolbar()
    readable(860, 640)


def test_deleting_an_unused_catalog_part_does_not_reseed_it(env: Env) -> None:
    planner = _planner(env)
    before = planner.list_parts()
    crossing = next(record for record in before if record.spec.article_number == "20020587")
    planner.delete_part(crossing.id)
    after = planner.list_parts()
    assert [record.id for record in after] == [
        record.id for record in before if record.id != crossing.id
    ]
    assert all(record.spec.article_number != "20020587" for record in after)
    restarted = TrackPlannerService(env.runtime.database, env.tracks)
    assert all(record.spec.article_number != "20020587" for record in restarted.list_parts())
    with env.runtime.database.session() as session:
        row = session.get(TrackPartDefinition, crossing.id)
        assert row is not None and row.suppressed


def test_an_unused_custom_part_is_deleted_and_a_used_part_is_kept(env: Env) -> None:
    planner = _planner(env)
    planner.list_parts()
    custom = planner.add_part(_straight_spec("EB-DEL", lanes=2, scale="1:24"))
    planner.set_stock(custom.id, 3)
    planner.delete_part(custom.id)
    assert all(record.id != custom.id for record in planner.list_parts())
    with env.runtime.database.session() as session:
        assert session.get(TrackPartDefinition, custom.id) is None
        assert session.get(TrackPartStock, custom.id) is None
    reloaded = TrackPlannerService(env.runtime.database, env.tracks)
    assert all(record.spec.article_number != "EB-DEL" for record in reloaded.list_parts())

    kept = planner.add_part(_straight_spec("EB-USED", lanes=2, scale="1:43"))
    track = env.track("Belegt", lanes=2)
    plan = place_instance(empty_plan(track.id), kept.id, kept.spec, 0, 0, 0, {kept.id: kept.spec})
    planner.save(plan)
    planner.set_stock(kept.id, 2)
    with pytest.raises(ValidationError) as caught:
        planner.delete_part(kept.id)
    assert caught.value.key == "error.planner.part_in_use"
    assert any(record.id == kept.id for record in planner.list_parts())
    loaded = planner.load(track.id)
    assert loaded.instances[0].part_id == kept.id
    assert planner.library.require(kept.id).spec.name == "EB-USED"
    with env.runtime.database.session() as session:
        stock = session.get(TrackPartStock, kept.id)
        assert stock is not None and stock.quantity == 2


def test_suppressing_a_catalog_part_keeps_its_stock_and_hides_it(qtbot: QtBot, env: Env) -> None:
    planner = _planner(env)
    crossing = next(
        record for record in planner.list_parts() if record.spec.article_number == "20020587"
    )
    planner.set_stock(crossing.id, 4)
    track = env.track("Manager", lanes=2)
    window, page = open_page(qtbot, env, "track_planner")
    window.show()
    assert isinstance(page, PlannerPage)
    _select(page, track.id)
    assert _library_row(page, "20020587") >= 0
    manager = LibraryManager(env.runtime.translator, planner)
    qtbot.addWidget(manager)
    manager.select_part(crossing.id)
    manager.delete_selected()
    assert "gelöscht" in manager.status.text()
    assert all(row.part_id != crossing.id for row in manager._rows)
    page._load_parts()
    with pytest.raises(AssertionError):
        _library_row(page, "20020587")
    assert all(record.spec.article_number != "20020587" for record in planner.list_parts())
    with env.runtime.database.session() as session:
        row = session.get(TrackPartDefinition, crossing.id)
        stock = session.get(TrackPartStock, crossing.id)
        assert row is not None and row.suppressed
        assert stock is not None and stock.quantity == 4
        assert session.scalar(
            select(TrackPartDefinition.id).where(TrackPartDefinition.id == crossing.id)
        )


def test_a_removed_catalog_part_stays_removed_after_backup(tmp_path: Path) -> None:
    source_dir = tmp_path / "source"
    source_dir.mkdir()
    source = Runtime.create(
        AppConfig(database_path=source_dir / "slot_racing.db", language="de"),
        config_path=source_dir / "config.json",
    )
    try:
        planner = source.services.get(TrackPlannerService)
        crossing = next(
            record for record in planner.list_parts() if record.spec.article_number == "20020587"
        )
        planner.set_stock(crossing.id, 6)
        planner.delete_part(crossing.id)
        archive = create_backup(source.database, source.config, tmp_path / "backups")
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
        planner = TrackPlannerService(runtime.database, runtime.services.get(TrackService))
        assert all(record.spec.article_number != "20020587" for record in planner.list_parts())
        straight = next(
            record for record in planner.list_parts() if record.spec.article_number == "20020601"
        )
        assert straight.spec.name == "Standardgerade"
        with runtime.database.session() as session:
            row = session.scalars(
                select(TrackPartDefinition).where(TrackPartDefinition.article_number == "20020587")
            ).one()
            assert row.suppressed
            stock = session.get(TrackPartStock, row.id)
            assert stock is not None and stock.quantity == 6
    finally:
        runtime.shutdown()


def test_the_library_manager_deletes_a_custom_part_from_both_lists(qtbot: QtBot, env: Env) -> None:
    planner = _planner(env)
    custom = planner.add_part(_straight_spec("EB-UI", lanes=2, scale="1:32"))
    track = env.track("Custom", lanes=2)
    window, page = open_page(qtbot, env, "track_planner")
    window.show()
    assert isinstance(page, PlannerPage)
    _select(page, track.id)
    assert _library_row(page, "EB-UI") >= 0
    manager = LibraryManager(env.runtime.translator, planner)
    qtbot.addWidget(manager)
    manager.select_part(custom.id)
    manager.delete_selected()
    page._load_parts()
    with pytest.raises(AssertionError):
        _library_row(page, "EB-UI")
    fresh = TrackPlannerService(env.runtime.database, env.tracks)
    assert all(record.id != custom.id for record in fresh.list_parts())
