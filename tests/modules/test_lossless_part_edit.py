"""An edit of a catalogue part changes only the elements that were edited.

PR #121 (cubic arcs) is not on this branch. These tests do not depend on it
and do not replace ``arcTo``.
"""

from __future__ import annotations

import inspect
from dataclasses import replace

import pytest
from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QImage, QPainter
from pytestqt.qtbot import QtBot

from slot_racing.modules.track_planner.catalog_import import load_carrera_library
from slot_racing.modules.track_planner.document import empty_plan, place_instance
from slot_racing.modules.track_planner.graphic_session import (
    KIND_CENTER,
    TOOL_LANE,
    TOOL_SELECT,
    GraphicSession,
)
from slot_racing.modules.track_planner.parts import (
    ACTIVE_CURVE_SOURCE,
    ACTIVE_STRAIGHT_SOURCE,
    ORIGIN_CATALOG,
    ORIGIN_MODIFIED,
    PATH_CENTER,
    PATH_DRAW,
    PATH_SLOT,
    PATH_START,
    SPAN_ARC,
    SPAN_BEZIER,
    TRACK,
    PartSpec,
    SlotPath,
    as_segment,
    same_geometry,
)
from slot_racing.modules.track_planner.ui.graphic_editor import GraphicEditor
from slot_racing.modules.track_planner.ui.track_paint import _add_arc, paint_part
from slot_racing.uikit.theme import COLORS
from tests.modules.conftest import Env
from tests.modules.test_track_parts import _planner

_SIZE = 900
_SCALE = 2.0


def _source(source_id: str) -> PartSpec:
    return next(spec for spec in load_carrera_library().parts if spec.source_id == source_id)


def _article(article: str) -> PartSpec:
    return next(
        spec
        for spec in load_carrera_library().parts
        if spec.article_number == article and spec.piece_key == ""
    )


def _open(spec: PartSpec) -> GraphicSession:
    session = GraphicSession()
    session.snap_grid = False
    session.snap_points = False
    session.load(spec)
    return session


def _compile(session: GraphicSession, spec: PartSpec, *, draft: bool = False) -> PartSpec:
    return session.compile(
        article_number=spec.article_number,
        scale=spec.scale,
        name=spec.name,
        manufacturer=spec.manufacturer,
        description=spec.description,
        category=spec.category,
        as_draft=draft,
    )


def _kind(spec: PartSpec, kind: str) -> tuple[SlotPath, ...]:
    return tuple(path for path in spec.slot_paths if path.kind == kind)


def _paint(path: SlotPath) -> tuple[object, ...]:
    return (
        path.kind,
        path.stroke,
        path.width_mm,
        path.linecap,
        path.fill,
        path.fill_alt,
        path.dashed,
        path.closed,
        path.z_order,
        path.extras,
    )


def _render(spec: PartSpec) -> QImage:
    image = QImage(_SIZE, _SIZE, QImage.Format.Format_ARGB32_Premultiplied)
    image.fill(Qt.GlobalColor.white)
    painter = QPainter(image)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, False)
    painter.translate(_SIZE / 2.0, _SIZE / 2.0)
    painter.scale(_SCALE, _SCALE)
    paint_part(painter, spec, color_coding=False, selected=False, start_straight=False)
    painter.end()
    return image


def _hex(image: QImage, x_mm: float, y_mm: float) -> str:
    pixel = image.pixelColor(int(_SIZE / 2.0 + x_mm * _SCALE), int(_SIZE / 2.0 + y_mm * _SCALE))
    return f"#{pixel.red():02x}{pixel.green():02x}{pixel.blue():02x}"


def _shorten(session: GraphicSession, length: float) -> None:
    session.set_tool(TOOL_SELECT)
    session.select_ids(frozenset({"lane-1"}))
    session.set_metric("length", length)


def test_an_open_catalogue_part_saves_without_changing_the_drawing() -> None:
    straight = _source(ACTIVE_STRAIGHT_SOURCE)
    session = _open(straight)
    assert not session.edited
    assert session.unlinked == frozenset()
    compiled = _compile(session, straight)
    assert compiled.source_id == straight.source_id
    assert compiled.piece_key == straight.piece_key
    assert compiled.slot_paths == straight.slot_paths
    assert compiled.contours == straight.contours
    assert compiled.connectors == straight.connectors
    assert compiled.center_y_mm == straight.center_y_mm
    assert compiled.short_label == straight.short_label
    assert same_geometry(compiled, straight)
    assert session.unlinked == frozenset()


def test_one_lane_edit_moves_only_its_groove_strokes() -> None:
    straight = _source(ACTIVE_STRAIGHT_SOURCE)
    session = _open(straight)
    _shorten(session, 300.0)
    compiled = _compile(session, straight)
    original = next(path for path in _kind(straight, PATH_SLOT) if path.lane == 1)
    updated = next(path for path in _kind(compiled, PATH_SLOT) if path.lane == 1)
    assert updated.spans != original.spans
    assert updated.spans[0].x1 == pytest.approx(127.5)
    assert updated.spans[0].y1 == pytest.approx(original.spans[0].y1)
    followers = [
        path for path in _kind(compiled, PATH_DRAW) if not path.fill and path.spans == updated.spans
    ]
    assert {(path.stroke, path.width_mm, path.z_order) for path in followers} == {
        ("#000000", 7.0, 7),
        ("#ebebeb", 16.0, 8),
    }
    assert _kind(compiled, PATH_SLOT)[1] == _kind(straight, PATH_SLOT)[1]
    kept = [
        path
        for path in straight.slot_paths
        if path.kind == PATH_DRAW and path.spans != original.spans
    ]
    assert [
        path
        for path in compiled.slot_paths
        if path.kind == PATH_DRAW and path.spans != updated.spans
    ] == kept
    assert len(compiled.slot_paths) == len(straight.slot_paths)
    assert compiled.connectors == straight.connectors
    assert compiled.contours == straight.contours
    assert compiled.outline == straight.outline
    assert compiled.source_id == straight.source_id
    assert compiled.length_mm == straight.length_mm
    assert compiled.center_x_mm == straight.center_x_mm
    assert compiled.short_label == straight.short_label
    assert session.unlinked == frozenset()


def test_adding_and_removing_a_lane_touches_only_that_groove(env: Env) -> None:
    straight = _source(ACTIVE_STRAIGHT_SOURCE)
    session = _open(straight)
    session.set_tool(TOOL_LANE)
    session.click(-80.0, 0.0, tolerance_mm=1)
    session.click(80.0, 0.0, tolerance_mm=1)
    session.finish_path()
    added = next(item for item in session.lanes() if item.lane == 3)
    for name in ("j0", "j3"):
        session.select_ids(frozenset({f"joint-{name}"}))
        session.set_metric("lanes", "1, 2, 3")
    compiled = _compile(session, straight)
    slots = _kind(compiled, PATH_SLOT)
    assert [path.lane for path in slots] == [1, 2, 3]
    assert slots[0] == _kind(straight, PATH_SLOT)[0]
    assert slots[1] == _kind(straight, PATH_SLOT)[1]
    clones = [path for path in _kind(compiled, PATH_DRAW) if path.spans == added.spans]
    assert {(path.stroke, path.width_mm) for path in clones} == {
        ("#000000", 7.0),
        ("#ebebeb", 16.0),
    }
    assert len(compiled.slot_paths) == len(straight.slot_paths) + 3
    borders = [item for item in compiled.connectors if item.kind != TRACK]
    assert borders == [item for item in straight.connectors if item.kind != TRACK]
    assert compiled.source_id == straight.source_id
    planner = _planner(env)
    record = planner.add_part(
        replace(compiled, article_number="EB-ADD", name="Spur ergänzt", source_id="", piece_key="")
    )
    loaded = planner.library.require(record.id).spec
    assert _kind(loaded, PATH_SLOT)[2].spans == added.spans
    assert any(path.spans == added.spans and path.stroke == "#000000" for path in loaded.slot_paths)

    removed = _open(straight)
    lane = next(path for path in _kind(straight, PATH_SLOT) if path.lane == 2)
    removed.select_ids(frozenset({"lane-2"}))
    removed.delete_selection()
    shorter = _compile(removed, straight)
    assert [path.lane for path in _kind(shorter, PATH_SLOT)] == [1]
    assert not any(path.spans == lane.spans for path in shorter.slot_paths)
    assert any(path.fill == "#333333" for path in shorter.slot_paths)
    assert len(_kind(shorter, PATH_CENTER)) == len(_kind(straight, PATH_CENTER))
    assert shorter.connectors == straight.connectors
    saved = planner.add_part(
        replace(shorter, article_number="EB-DEL", name="Spur entfernt", source_id="", piece_key="")
    )
    assert [path.lane for path in _kind(planner.library.require(saved.id).spec, PATH_SLOT)] == [1]


def test_an_arc_and_a_bezier_keep_the_rest_of_the_drawing() -> None:
    curve = _source(ACTIVE_CURVE_SOURCE)
    session = _open(curve)
    lane = next(item for item in session.lanes() if item.spans[0].kind == SPAN_ARC)
    sweep = lane.spans[0].sweep_deg
    mid = as_segment(lane.spans[0]).point_at(0.5)
    session.set_tool(TOOL_SELECT)
    session.select_ids(frozenset({lane.id}))
    session.click(mid[0], mid[1], tolerance_mm=8)
    session.set_metric("sweep", sweep - 10.0)
    compiled = _compile(session, curve)
    updated = next(path for path in _kind(compiled, PATH_SLOT) if path.lane == lane.lane)
    assert updated.spans[0].kind == SPAN_ARC
    assert updated.spans[0].sweep_deg == pytest.approx(sweep - 10.0)
    assert updated.spans[0].radius_mm == pytest.approx(lane.spans[0].radius_mm)
    assert {
        (path.stroke, path.width_mm)
        for path in _kind(compiled, PATH_DRAW)
        if not path.fill and path.spans == updated.spans
    } == {("#000000", 7.0), ("#ebebeb", 16.0)}
    other = next(path for path in _kind(curve, PATH_SLOT) if path.lane != lane.lane)
    assert next(path for path in _kind(compiled, PATH_SLOT) if path.lane == other.lane) == other
    road = next(path for path in curve.slot_paths if path.fill == "#333333")
    assert next(path for path in compiled.slot_paths if path.fill == "#333333") == road
    assert compiled.contours == curve.contours
    assert compiled.connectors == curve.connectors
    assert compiled.border_edges == curve.border_edges
    assert compiled.attachment == curve.attachment
    assert compiled.source_id == curve.source_id

    switch = next(
        spec
        for spec in load_carrera_library().parts
        if spec.article_number == "20517"
        and any(
            span.kind == SPAN_BEZIER
            for path in spec.slot_paths
            if path.kind == PATH_SLOT
            for span in path.spans
        )
    )
    bent = _open(switch)
    groove = next(
        item for item in bent.lanes() if any(span.kind == SPAN_BEZIER for span in item.spans)
    )
    span = next(item for item in groove.spans if item.kind == SPAN_BEZIER)
    bent.set_tool(TOOL_SELECT)
    bent.select_ids(frozenset({groove.id}))
    bent.click(span.c1x, span.c1y, tolerance_mm=6)
    bent.move(span.c1x + 12.0, span.c1y + 8.0, tolerance_mm=1)
    bent.release()
    compiled_switch = _compile(bent, switch)
    moved = next(path for path in _kind(compiled_switch, PATH_SLOT) if path.lane == groove.lane)
    moved_span = next(item for item in moved.spans if item.kind == SPAN_BEZIER)
    assert moved_span.c1x == pytest.approx(span.c1x + 12.0)
    assert moved_span.c1y == pytest.approx(span.c1y + 8.0)
    assert any(
        path.kind == PATH_DRAW and not path.fill and path.spans == moved.spans
        for path in compiled_switch.slot_paths
    )
    untouched = [
        path
        for path in switch.slot_paths
        if not (path.kind in {PATH_SLOT, PATH_DRAW} and path.spans == groove.spans)
    ]
    assert [
        path
        for path in compiled_switch.slot_paths
        if not (path.kind in {PATH_SLOT, PATH_DRAW} and path.spans == moved.spans)
    ] == untouched


def test_a_plate_move_updates_only_the_matching_fill() -> None:
    straight = _source(ACTIVE_STRAIGHT_SOURCE)
    session = _open(straight)
    session.set_tool(TOOL_SELECT)
    session.click(0.0, 90.0, tolerance_mm=15)
    assert session.selected == frozenset({"plate-1"})
    session.move(0.0, 110.0, tolerance_mm=1)
    session.release()
    compiled = _compile(session, straight)
    assert compiled.contours[0].spans != straight.contours[0].spans
    assert compiled.contours[0].spans[0].y0 == pytest.approx(
        straight.contours[0].spans[0].y0 + 20.0
    )
    fill = next(path for path in compiled.slot_paths if path.fill == "#333333")
    assert fill.spans == compiled.contours[0].spans
    assert fill.stroke == "#000000"
    assert fill.z_order == 11
    grooves = [path for path in straight.slot_paths if path.kind == PATH_DRAW and not path.fill]
    assert [
        path for path in compiled.slot_paths if path.kind == PATH_DRAW and not path.fill
    ] == grooves
    assert _kind(compiled, PATH_SLOT) == _kind(straight, PATH_SLOT)
    assert _kind(compiled, PATH_CENTER) == _kind(straight, PATH_CENTER)
    assert compiled.connectors == straight.connectors
    assert compiled.source_id == straight.source_id
    assert len(compiled.contours) == len(straight.contours)
    assert session.unlinked == frozenset()


def test_colours_borders_and_centre_marks_survive_an_edit() -> None:
    straight = _source(ACTIVE_STRAIGHT_SOURCE)
    session = _open(straight)
    _shorten(session, 280.0)
    mark = next(item for item in session.elements if item.kind == KIND_CENTER)
    session.select_ids(frozenset({mark.id}))
    session.set_mark_style(stroke="#445566", width_mm=2.0, dashed=True)
    compiled = _compile(session, straight)
    assert any(path.fill == "#333333" for path in compiled.slot_paths)
    assert {
        (path.stroke, path.width_mm) for path in _kind(compiled, PATH_DRAW) if not path.fill
    } == {
        ("#000000", 7.0),
        ("#ebebeb", 16.0),
    }
    edited = next(path for path in compiled.slot_paths if path.stroke == "#445566")
    assert edited.kind == PATH_CENTER
    assert edited.dashed
    assert edited.width_mm == pytest.approx(2.0)
    others = [path for path in _kind(compiled, PATH_CENTER) if path.stroke != "#445566"]
    assert others == [path for path in _kind(straight, PATH_CENTER) if path.spans != mark.spans]
    border = _article("20560")
    opened = _compile(_open(border), border)
    assert opened.slot_paths == border.slot_paths
    assert any(path.fill == "#b51a00" for path in opened.slot_paths)
    assert any(path.fill == "#ffffff" for path in opened.slot_paths)
    assert opened.connectors == border.connectors


def test_paint_order_and_the_clip_stay_when_a_lane_changes() -> None:
    straight = _source(ACTIVE_STRAIGHT_SOURCE)
    compiled = _compile(_open(straight), straight)
    session = _open(straight)
    _shorten(session, 300.0)
    compiled = _compile(session, straight)
    assert [(path.kind, path.z_order) for path in compiled.slot_paths] == [
        (path.kind, path.z_order) for path in straight.slot_paths
    ]
    assert compiled.contours == straight.contours
    assert compiled.outline == straight.outline
    image = _render(compiled)
    assert _hex(image, 0.0, 20.0) == "#333333"
    assert _hex(image, 0.0, 49.5) == "#000000"
    assert _hex(image, 160.0, 49.5) == "#333333"


def test_a_lane_edit_does_not_rebuild_connectors() -> None:
    straight = _source(ACTIVE_STRAIGHT_SOURCE)
    compiled = _compile(_open(straight), straight)
    session = _open(straight)
    _shorten(session, 300.0)
    compiled = _compile(session, straight)
    assert compiled.connectors == straight.connectors
    marks = _open(straight)
    mark = next(item for item in marks.elements if item.kind == KIND_CENTER)
    marks.select_ids(frozenset({mark.id}))
    marks.set_mark_style(dashed=True)
    assert _compile(marks, straight).connectors == straight.connectors


def test_one_joint_moves_without_rewriting_the_others() -> None:
    straight = _source(ACTIVE_STRAIGHT_SOURCE)
    session = _open(straight)
    session.select_ids(frozenset({"joint-j0"}))
    session.set_metric("x", 180.0)
    compiled = _compile(session, straight)
    moved = next(item for item in compiled.connectors if item.name == "j0")
    original = next(item for item in straight.connectors if item.name == "j0")
    assert moved.x_mm == pytest.approx(180.0)
    assert moved.y_mm == original.y_mm
    assert moved.direction_deg == original.direction_deg
    assert moved.lanes == original.lanes
    assert moved.target_source_id == original.target_source_id
    assert moved.kind == TRACK
    assert [item for item in compiled.connectors if item.name != "j0"] == [
        item for item in straight.connectors if item.name != "j0"
    ]
    assert _kind(compiled, PATH_DRAW) == _kind(straight, PATH_DRAW)
    assert compiled.source_id == straight.source_id


def test_a_part_without_source_id_keeps_its_drawing(env: Env) -> None:
    straight = _source(ACTIVE_STRAIGHT_SOURCE)
    copied = replace(
        straight,
        source_id="",
        piece_key="",
        article_number="EB-OWN",
        name="Eigene Gerade",
        selectable=True,
    )
    session = _open(copied)
    _shorten(session, 300.0)
    compiled = _compile(session, copied)
    assert compiled.source_id == ""
    assert compiled.short_label == straight.short_label
    assert compiled.center_y_mm == straight.center_y_mm
    updated = next(path for path in _kind(compiled, PATH_SLOT) if path.lane == 1)
    assert any(
        path.kind == PATH_DRAW and path.stroke == "#ebebeb" and path.spans == updated.spans
        for path in compiled.slot_paths
    )
    assert any(path.fill == "#333333" for path in compiled.slot_paths)
    planner = _planner(env)
    record = planner.add_part(compiled)
    loaded = planner.library.require(record.id).spec
    assert loaded.source_id == ""
    assert loaded.slot_paths == compiled.slot_paths
    assert loaded.connectors == compiled.connectors


def test_copy_save_and_reload_then_edit_again(env: Env) -> None:
    planner = _planner(env)
    straight = _source(ACTIVE_STRAIGHT_SOURCE)
    session = _open(straight)
    _shorten(session, 300.0)
    compiled = _compile(session, straight)
    record = planner.add_part(
        replace(compiled, article_number="EB-COPY", name="Kopie", source_id="", piece_key="")
    )
    loaded = planner.library.require(record.id).spec
    assert loaded.source_id == ""
    assert loaded.slot_paths == compiled.slot_paths
    again = _open(loaded)
    _shorten(again, 250.0)
    second = _compile(again, loaded)
    assert next(path for path in _kind(second, PATH_SLOT) if path.lane == 1).spans[0].x1 == (
        pytest.approx(-172.5 + 250.0)
    )
    assert len(second.slot_paths) == len(straight.slot_paths)
    assert any(path.fill == "#333333" for path in second.slot_paths)
    stored = planner.update_part(record.id, second)
    assert planner.library.require(stored.id).spec.slot_paths == second.slot_paths


def test_several_edits_do_not_accumulate_drawings(env: Env) -> None:
    straight = _source(ACTIVE_STRAIGHT_SOURCE)
    spec = straight
    for length in (320.0, 300.0, 260.0):
        session = _open(spec)
        _shorten(session, length)
        spec = _compile(session, spec)
    assert len(spec.slot_paths) == len(straight.slot_paths)
    assert (
        sum(1 for path in spec.slot_paths if path.stroke == "#000000" and path.width_mm == 7) == 2
    )
    assert sum(1 for path in spec.slot_paths if path.fill == "#333333") == 1
    assert spec.source_id == straight.source_id
    reloaded = _open(spec)
    assert not reloaded.edited
    assert _compile(reloaded, spec).slot_paths == spec.slot_paths
    planner = _planner(env)
    record = planner.add_part(
        replace(spec, article_number="EB-MANY", name="Mehrfach", source_id="", piece_key="")
    )
    assert planner.library.require(record.id).spec.slot_paths == spec.slot_paths


def test_the_editor_and_the_planner_paint_the_edited_body(qtbot: QtBot, env: Env) -> None:
    straight = _source(ACTIVE_STRAIGHT_SOURCE)
    session = _open(straight)
    _shorten(session, 300.0)
    compiled = _compile(session, straight)
    shared = _render(compiled)
    assert _hex(shared, 0.0, 49.5) == "#000000"
    assert _hex(shared, 160.0, 49.5) == "#333333"
    assert _hex(shared, 0.0, -49.5) == "#000000"
    editor = GraphicEditor(env.runtime.translator)
    qtbot.addWidget(editor)
    editor.session.load(straight)
    editor.session.snap_grid = False
    editor.session.snap_points = False
    editor.session.grid_mm = 0
    editor.spec_provider = lambda: compiled
    editor.resize(1100, 800)
    editor.show()
    qtbot.waitUntil(lambda: editor.canvas.width() > 400)
    canvas = editor.canvas
    canvas.set_view(_SCALE, canvas.width() / 2.0, canvas.height() / 2.0)
    image = canvas.grab().toImage()
    center_x = int(canvas.width() / 2.0)
    center_y = int(canvas.height() / 2.0)

    def at(x_mm: float, y_mm: float) -> QColor:
        return image.pixelColor(int(center_x + x_mm * _SCALE), int(center_y + y_mm * _SCALE))

    road = at(0.0, 20.0)
    vacated = at(160.0, 49.5)
    assert (road.red(), road.green(), road.blue()) == (0x33, 0x33, 0x33)
    assert (vacated.red(), vacated.green(), vacated.blue()) == (0x33, 0x33, 0x33)
    groove = at(0.0, 49.5)
    assert groove.red() < 40 and groove.green() < 40 and groove.blue() < 40
    info = QColor(COLORS.info)
    for y_px in range(0, image.height(), 3):
        for x_px in range(0, image.width(), 3):
            pixel = image.pixelColor(x_px, y_px)
            assert (pixel.red(), pixel.green(), pixel.blue()) != (
                info.red(),
                info.green(),
                info.blue(),
            )


def test_the_arc_painter_on_main_is_left_alone() -> None:
    source = inspect.getsource(_add_arc)
    assert "arcTo" in source
    assert "cubicTo" not in source


def test_saved_plans_stock_and_old_revisions_stay(env: Env) -> None:
    planner = _planner(env)
    planner.list_parts()
    record = next(
        item for item in planner.list_parts() if item.spec.source_id == ACTIVE_STRAIGHT_SOURCE
    )
    planner.set_stock(record.id, 5)
    track = env.track("Unberührt", lanes=2)
    plan = place_instance(
        empty_plan(track.id),
        record.id,
        record.spec,
        40.0,
        -15.0,
        10.0,
        {record.id: record.spec},
    )
    planner.save(plan)
    before = [
        (item.part_id, item.x_mm, item.y_mm, item.rotation_z_deg)
        for item in planner.load(track.id).instances
    ]
    session = _open(record.spec)
    _shorten(session, 300.0)
    compiled = _compile(session, record.spec)
    updated = planner.update_part(record.id, compiled)
    assert updated.id != record.id
    assert updated.origin == ORIGIN_MODIFIED
    assert updated.spec.source_id == record.spec.source_id
    assert planner.load(track.id).instances[0].part_id == record.id
    after = [
        (item.part_id, item.x_mm, item.y_mm, item.rotation_z_deg)
        for item in planner.load(track.id).instances
    ]
    assert after == before
    previous = planner.library.require(record.id)
    assert previous.origin == ORIGIN_CATALOG
    assert previous.spec.slot_paths == record.spec.slot_paths
    assert previous.is_current is False
    amounts = planner.library.stock_quantities()
    assert amounts.get(updated.id) == 5
    assert amounts.get(record.id, 0) == 0
    planner.library.ensure_seed()
    current = planner.library.require(updated.id)
    assert next(path for path in _kind(current.spec, PATH_SLOT) if path.lane == 1).spans[
        0
    ].x1 == pytest.approx(127.5)
    assert any(path.fill == "#333333" for path in current.spec.slot_paths)


def test_an_unmatched_drawing_is_kept_and_the_editor_says_so(qtbot: QtBot, env: Env) -> None:
    straight = _source(ACTIVE_STRAIGHT_SOURCE)
    bare_paths = tuple(
        path
        for path in straight.slot_paths
        if path.kind != PATH_DRAW or path.fill or path.kind == PATH_START
    )
    bare = replace(straight, slot_paths=bare_paths, article_number="EB-BARE", name="Ohne Rille")
    session = _open(bare)
    _shorten(session, 300.0)
    compiled = _compile(session, bare)
    assert "lane-1" in session.unlinked
    updated = next(path for path in _kind(compiled, PATH_SLOT) if path.lane == 1)
    assert updated.spans[0].x1 == pytest.approx(127.5)
    assert [path for path in compiled.slot_paths if path.kind == PATH_DRAW] == [
        path for path in bare.slot_paths if path.kind == PATH_DRAW
    ]
    assert not any(
        path.kind == PATH_DRAW and not path.fill and path.spans == updated.spans
        for path in compiled.slot_paths
    )
    editor = GraphicEditor(env.runtime.translator)
    qtbot.addWidget(editor)
    editor.session = session
    editor.spec_provider = lambda: _compile(session, bare)
    editor.note_change()
    assert "unverändert" in editor.issue.text()

    offset = replace(
        straight,
        slot_paths=tuple(
            replace(
                path,
                spans=(replace(path.spans[0], y0=path.spans[0].y0 + 8, y1=path.spans[0].y1 + 8),),
            )
            if path.kind == PATH_DRAW and path.stroke == "#000000" and path.z_order == 7
            else path
            for path in straight.slot_paths
        ),
    )
    shifted = _open(offset)
    _shorten(shifted, 300.0)
    kept = _compile(shifted, offset)
    leftover = next(
        path
        for path in kept.slot_paths
        if path.kind == PATH_DRAW and path.stroke == "#000000" and path.z_order == 7
    )
    assert leftover.spans[0].y0 == pytest.approx(49.5 + 8.0)
    grey = next(path for path in kept.slot_paths if path.z_order == 8)
    assert grey.spans[0].x1 == pytest.approx(127.5)
    assert len(kept.slot_paths) == len(offset.slot_paths)
