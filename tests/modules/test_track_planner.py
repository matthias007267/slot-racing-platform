"""Track plans are stored with a track and edited without touching its lane count."""

from __future__ import annotations

from pathlib import Path

import pytest
from PySide6.QtWidgets import QLabel
from pytestqt.qtbot import QtBot

from slot_racing.app.runtime import Runtime
from slot_racing.core.backup import create_backup, restore_backup
from slot_racing.core.config import AppConfig
from slot_racing.core.domain import TrackId
from slot_racing.core.errors import ValidationError
from slot_racing.modules.track_planner.document import (
    CLOCKWISE,
    COUNTERCLOCKWISE,
    CURVE_90,
    SENSOR,
    STRAIGHT_H,
    STRAIGHT_V,
    Marker,
    TrackPlan,
    add_piece,
    empty_plan,
    lane_centers,
    move_marker,
    move_piece,
    place_start_finish,
    remove_piece,
    reset_plan,
    set_direction,
    travel_vector,
)
from slot_racing.modules.track_planner.service import TrackPlannerService
from slot_racing.modules.track_planner.ui.page import PlannerPage
from slot_racing.modules.tracks.models import TrackLayout
from slot_racing.modules.tracks.service import TrackInput, TrackService
from tests.modules.conftest import Env
from tests.modules.test_ui_management import open_page


def test_a_plan_can_be_saved_and_loaded(env: Env) -> None:
    track = env.track("Heim", lanes=3)
    planner = _planner(env)
    plan = add_piece(planner.load(track.id), STRAIGHT_H, 0, 0)
    plan = add_piece(plan, CURVE_90, 4, 0, rotation=90)
    plan = place_start_finish(plan, 1, 0, plan.pieces[0].id)
    plan = set_direction(plan, COUNTERCLOCKWISE)
    planner.save(plan)

    loaded = planner.load(track.id)
    assert loaded.track_id == track.id
    assert loaded.version == 1
    assert loaded.direction == COUNTERCLOCKWISE
    assert [(piece.piece_type, piece.x, piece.y, piece.rotation) for piece in loaded.pieces] == [
        (STRAIGHT_H, 0, 0, 0),
        (CURVE_90, 4, 0, 90),
    ]
    start = loaded.start_finish()
    assert start is not None
    assert (start.x, start.y, start.piece_id, start.lane) == (1, 0, loaded.pieces[0].id, None)
    stored = env.tracks.get_track(track.id)
    assert stored is not None and stored.lane_count == 3


def test_tracks_keep_independent_plans_and_a_missing_plan_stays_empty(env: Env) -> None:
    home = env.track("Heim")
    away = env.track("Gast", lanes=4)
    planner = _planner(env)
    planner.save(add_piece(planner.load(home.id), STRAIGHT_V, 2, 2))
    assert planner.load(away.id).pieces == ()
    assert planner.load(home.id).pieces[0].piece_type == STRAIGHT_V
    away_track = env.tracks.get_track(away.id)
    assert away_track is not None and away_track.lane_count == 4
    race = env.races.create_race("Sonntag", away.id, 5)
    assert race.lane_count == 4


def test_editing_a_plan_saves_discards_and_resets(env: Env) -> None:
    track = env.track()
    planner = _planner(env)
    plan = add_piece(empty_plan(track.id), STRAIGHT_H, 0, 0)
    piece_id = plan.pieces[0].id
    moved = move_piece(plan, piece_id, 4, 2)
    assert moved.pieces[0].x == 4 and moved.pieces[0].y == 2
    removed = remove_piece(moved, piece_id)
    assert removed.pieces == ()
    placed = place_start_finish(add_piece(removed, STRAIGHT_V, 0, 0), 2, 1)
    start = placed.start_finish()
    assert start is not None
    shifted = move_marker(placed, start.id, 6, 3)
    moved_start = shifted.start_finish()
    assert moved_start is not None and (moved_start.x, moved_start.y) == (6, 3)
    turned = set_direction(shifted, COUNTERCLOCKWISE)
    assert travel_vector(STRAIGHT_H, 0, turned.direction) == (-1, 0)
    assert travel_vector(STRAIGHT_H, 0, CLOCKWISE) == (1, 0)
    planner.save(turned)
    assert planner.load(track.id).direction == COUNTERCLOCKWISE
    assert reset_plan(turned).pieces == ()
    assert planner.load(track.id).pieces  # unsaved reset does not replace the stored plan
    planner.save(reset_plan(turned))
    assert planner.load(track.id).pieces == ()
    assert planner.load(track.id).start_finish() is None


def test_lane_count_comes_from_the_track_and_is_not_rewritten(env: Env) -> None:
    planner = _planner(env)
    for lanes in (2, 3, 4):
        track = env.track(f"Bahn {lanes}", lanes=lanes)
        assert len(lane_centers(lanes, 32)) == lanes
        plan = add_piece(planner.load(track.id), STRAIGHT_H, 0, 0)
        piece = plan.pieces[0]
        sensor = Marker("sensor-1", SENSOR, 1, 1, piece.id, lane=lanes)
        stored = planner.save(
            TrackPlan(plan.track_id, plan.version, plan.direction, plan.pieces, (sensor,))
        )
        assert planner.load(track.id).markers[0].lane == lanes
        stored_track = env.tracks.get_track(track.id)
        assert stored_track is not None and stored_track.lane_count == lanes
        too_many = TrackPlan(
            stored.track_id,
            stored.version,
            stored.direction,
            stored.pieces,
            (Marker("sensor-2", SENSOR, 1, 1, piece.id, lane=lanes + 1),),
        )
        with pytest.raises(ValidationError) as caught:
            planner.save(too_many)
        assert caught.value.key == "error.planner.lane"
        still = env.tracks.get_track(track.id)
        assert still is not None and still.lane_count == lanes


def test_a_broken_plan_is_rejected_and_the_track_stays(env: Env) -> None:
    track = env.track("Ring", lanes=2)
    with env.runtime.database.session() as session:
        session.add(TrackLayout(track_id=int(track.id), name="plan", data={"version": 99}))
    with pytest.raises(ValidationError) as caught:
        _planner(env).load(track.id)
    assert caught.value.key == "error.planner.invalid"
    stored = env.tracks.get_track(track.id)
    assert stored is not None and stored.lane_count == 2


def test_a_backup_restores_the_plan(tmp_path: Path) -> None:
    config = AppConfig(
        database_path=tmp_path / "slot_racing.db",
        backup_directory=tmp_path / "backups",
    )
    runtime = Runtime.create(config, config_path=tmp_path / "config.json")
    try:
        tracks = runtime.services.get(TrackService)
        planner = runtime.services.get(TrackPlannerService)
        track = tracks.create_track(TrackInput(name="Oval", lane_count=3))
        plan = set_direction(add_piece(planner.load(track.id), CURVE_90, 0, 0), COUNTERCLOCKWISE)
        plan = place_start_finish(plan, 2, 2, plan.pieces[0].id)
        planner.save(plan)
        archive = create_backup(runtime.database, runtime.config, tmp_path / "backups")
        planner.save(reset_plan(plan))
        restore_backup(
            archive,
            runtime.database,
            runtime.config,
            runtime.config_path,
            backup_directory=config.resolved_backup_directory(),
        )
        restored = planner.load(track.id)
        assert restored.direction == COUNTERCLOCKWISE
        assert restored.pieces[0].piece_type == CURVE_90
        assert restored.start_finish() is not None
        restored_track = tracks.get_track(track.id)
        assert restored_track is not None and restored_track.lane_count == 3
    finally:
        runtime.shutdown()


def test_the_editor_saves_discards_and_shows_the_track_lanes(qtbot: QtBot, env: Env) -> None:
    two = env.track("Alpha", lanes=2)
    three = env.track("Beta", lanes=3)
    four = env.track("Gamma", lanes=4)
    window, page = open_page(qtbot, env, "track_planner")
    window.show()
    assert isinstance(page, PlannerPage)
    assert page.findChild(QLabel, "planner-lanes") is not None
    _select(page, two.id)
    assert page.lanes.text() == "Bahnen: 2"
    _select(page, three.id)
    assert page.lanes.text() == "Bahnen: 3"
    _select(page, four.id)
    assert page.lanes.text() == "Bahnen: 4"

    page.add_horizontal.click()
    assert len(page.plan().pieces) == 1
    page.delete_button.click()
    assert page.plan().pieces == ()
    page.add_horizontal.click()
    page.add_curve.click()
    page.add_start.click()
    assert [piece.piece_type for piece in page.plan().pieces] == [STRAIGHT_H, CURVE_90]
    start = page.plan().start_finish()
    assert start is not None
    page.x_spin.setValue(8)
    moved = page.plan().start_finish()
    assert moved is not None and moved.x == 8
    page.direction_button.click()
    assert page.plan().direction == COUNTERCLOCKWISE
    assert page.direction_button.text() == "Gegen den Uhrzeigersinn"
    page.save_button.click()
    assert _planner(env).load(four.id).direction == COUNTERCLOCKWISE
    assert len(_planner(env).load(four.id).pieces) == 2

    page.add_vertical.click()
    assert len(page.plan().pieces) == 3
    page.discard_button.click()
    assert len(page.plan().pieces) == 2
    kept = env.tracks.get_track(four.id)
    assert kept is not None and kept.lane_count == 4


def _planner(env: Env) -> TrackPlannerService:
    return env.runtime.services.get(TrackPlannerService)


def _select(page: PlannerPage, track_id: TrackId) -> None:
    index = page.track_combo.findData(track_id)
    assert index >= 0
    page.track_combo.setCurrentIndex(index)
