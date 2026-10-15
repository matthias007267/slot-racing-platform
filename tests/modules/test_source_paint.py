"""Source drawings stay measurable, coloured, and editable as centre marks."""

from __future__ import annotations

import copy
from dataclasses import replace

import pytest
from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QImage, QPainter
from PySide6.QtWidgets import QPushButton
from pytestqt.qtbot import QtBot
from sqlalchemy import select

from slot_racing.modules.track_planner.catalog_import import (
    HELPER_PIECE_KEY,
    _drawing_paths,
    load_carrera_library,
)
from slot_racing.modules.track_planner.document import empty_plan, place_instance
from slot_racing.modules.track_planner.graphic_session import (
    KIND_CENTER,
    TOOL_ARC,
    TOOL_BEZIER,
    TOOL_CENTER,
    TOOL_SELECT,
    GraphicSession,
)
from slot_racing.modules.track_planner.models import (
    TrackPartConnector,
    TrackPartDefinition,
    TrackPartStock,
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
    START_PIECE_KEY,
    PartSpec,
    SlotPath,
    SlotSpan,
    decode_slot_paths,
    encode_geometry,
    encode_slot_paths,
)
from slot_racing.modules.track_planner.ui.part_editor import PartDialog
from slot_racing.modules.track_planner.ui.track_paint import paint_part
from tests.modules.conftest import Env
from tests.modules.test_graphic_editor import _editor
from tests.modules.test_graphic_session import draw_straight
from tests.modules.test_track_parts import _planner, _straight_spec

_SIZE = 900
_SCALE = 2.0


def _spec(article: str, *, piece_key: str = "") -> PartSpec:
    return next(
        spec
        for spec in load_carrera_library().parts
        if spec.article_number == article and spec.piece_key == piece_key
    )


def _render(
    spec: PartSpec,
    *,
    scale: float = _SCALE,
    rotation: float = 0.0,
    coded: bool = False,
    selected: bool = False,
) -> QImage:
    image = QImage(_SIZE, _SIZE, QImage.Format.Format_ARGB32_Premultiplied)
    image.fill(Qt.GlobalColor.white)
    painter = QPainter(image)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, False)
    painter.translate(_SIZE / 2.0, _SIZE / 2.0)
    painter.rotate(rotation)
    painter.scale(scale, scale)
    paint_part(painter, spec, color_coding=coded, selected=selected, start_straight=False)
    painter.end()
    return image


def _hex(image: QImage, x_mm: float, y_mm: float, *, scale: float = _SCALE) -> str:
    pixel = image.pixelColor(int(_SIZE / 2.0 + x_mm * scale), int(_SIZE / 2.0 + y_mm * scale))
    return f"#{pixel.red():02x}{pixel.green():02x}{pixel.blue():02x}"


def _count(image: QImage, color: str) -> int:
    target = QColor(color)
    found = 0
    for y_px in range(0, image.height(), 4):
        for x_px in range(0, image.width(), 4):
            pixel = image.pixelColor(x_px, y_px)
            if (pixel.red(), pixel.green(), pixel.blue()) == (
                target.red(),
                target.green(),
                target.blue(),
            ):
                found += 1
    return found


def _signature(image: QImage) -> tuple[int, ...]:
    return tuple(
        image.pixel(x_px, y_px)
        for y_px in range(0, image.height(), 24)
        for x_px in range(0, image.width(), 24)
    )


def test_drawing_attributes_round_trip_without_dropping_unknown_keys() -> None:
    paths, notes = _drawing_paths(
        [
            {
                "type": "area",
                "path": "M 0 0 L 10 0 L 10 4 Z",
                "stroke-width": 1.5,
                "stroke-color": "#112233",
                "stroke-linecap": "round",
                "fill-color": "#333333",
                "fill-color-alt": "#b66948",
                "opacity": 0.4,
            }
        ]
    )
    assert notes == ("unknown drawing attribute opacity",)
    assert len(paths) == 1
    drawing = paths[0]
    assert drawing.kind == PATH_DRAW
    assert drawing.closed
    assert drawing.stroke == "#112233"
    assert drawing.width_mm == pytest.approx(1.5)
    assert drawing.linecap == "round"
    assert drawing.fill == "#333333"
    assert drawing.fill_alt == "#b66948"
    assert drawing.extras == (("opacity", "0.4"),)
    restored = decode_slot_paths(encode_slot_paths(paths))
    assert restored == paths
    paths, cap_notes = _drawing_paths(
        [
            {
                "type": "path",
                "path": "M 0 0 L 8 0",
                "stroke-width": 2,
                "stroke-color": "#abcdef80",
                "stroke-linecap": "triangle",
            }
        ]
    )
    assert "unknown line cap triangle" in cap_notes
    assert paths[0].linecap == "triangle"
    assert paths[0].stroke == "#abcdef80"
    translucent = QColor("#abcdef80")
    assert translucent.isValid()
    assert translucent.alpha() < 255


def test_the_source_files_keep_every_used_drawing_attribute() -> None:
    draft = load_carrera_library()
    assert [note.detail for note in draft.notes] == [
        "C_Dig_right mates with C_Dig_left",
        "C_Dig_left mates with C_Dig_right",
    ]
    straight = next(spec for spec in draft.parts if spec.source_id == ACTIVE_STRAIGHT_SOURCE)
    slots = [path for path in straight.slot_paths if path.kind == PATH_SLOT]
    assert len(slots) == 2
    assert straight.lane_count == 2
    assert slots[0].spans[0].y0 == pytest.approx(49.5)
    assert slots[1].spans[0].y0 == pytest.approx(-49.5)
    assert abs(slots[0].spans[0].y0 - slots[1].spans[0].y0) == pytest.approx(99.0)
    assert slots[0].stroke is None
    center = next(path for path in straight.slot_paths if path.kind == PATH_CENTER)
    assert center.stroke == "#e0e0e0"
    assert center.width_mm == pytest.approx(4.0)
    assert center.linecap == "butt"
    area = next(path for path in straight.slot_paths if path.fill == "#333333")
    assert area.fill_alt == "#333333"
    assert area.width_mm == pytest.approx(1.0)
    curve = next(spec for spec in draft.parts if spec.source_id == ACTIVE_CURVE_SOURCE)
    radii = sorted(
        span.radius_mm
        for path in curve.slot_paths
        if path.kind == PATH_SLOT
        for span in path.spans
        if span.kind == SPAN_ARC
    )
    assert radii == pytest.approx([247.5, 346.5])
    assert radii[1] - radii[0] == pytest.approx(99.0)


def test_borders_start_straights_and_digital_parts_keep_their_colours() -> None:
    straight = _spec("20509")
    start = next(spec for spec in load_carrera_library().parts if spec.piece_key == START_PIECE_KEY)
    border = _spec("20560")
    counter = _spec("30342")
    plain = _render(straight)
    marked = _render(start)
    assert _hex(plain, 0.0, 20.0) == "#333333"
    assert _hex(plain, 0.0, 0.0) == "#e0e0e0"
    assert _hex(plain, 0.0, 49.5) == "#000000"
    assert _hex(plain, 0.0, 56.0) == "#ebebeb"
    assert _hex(marked, -138.0, -85.8) == "#ffffff"
    assert _hex(plain, -138.0, -85.8) == "#333333"
    assert any(
        path.kind == PATH_START and path.stroke == "#ffffff" and path.linecap == "square"
        for path in start.slot_paths
    )
    assert not any(path.kind == PATH_START for path in straight.slot_paths)
    stripes = _render(border)
    assert _hex(stripes, -120.0, -45.0) == "#b51a00"
    assert _hex(stripes, -160.0, -45.0) == "#ffffff"
    assert _hex(stripes, 0.0, 0.0) == "#333333"
    curve = next(
        spec for spec in load_carrera_library().parts if spec.source_id == ACTIVE_CURVE_SOURCE
    )
    assert _count(_render(curve), "#333333") > 100
    assert _count(_render(curve), "#b66948") == 0
    assert _count(_render(curve, coded=True), "#b66948") > 100
    assert _count(_render(curve, coded=True), "#333333") == 0
    painted = _render(counter)
    assert _count(painted, "#b51a00") > 0
    assert _count(painted, "#222222") > 0
    template = next(
        item for item in load_carrera_library().templates if item.article_number == "30343"
    )
    members = [
        next(spec for spec in load_carrera_library().parts if spec.source_id == member.source_id)
        for member in template.members
    ]
    assert len(members) == 2
    assert {member.piece_key for member in members} == {"30343.1", "30343.2"}
    assert all(any(path.fill == "#333333" for path in member.slot_paths) for member in members)
    assert all(len(member.connectors) >= 2 for member in members)
    narrow = next(
        spec for spec in load_carrera_library().parts if spec.piece_key == "30350/30351.1"
    )
    assert any(path.fill for path in narrow.slot_paths)
    assert _count(_render(narrow), "#333333") > 0


def test_a_part_without_a_centre_mark_still_paints_and_transforms() -> None:
    helper = next(
        spec for spec in load_carrera_library().parts if spec.piece_key == HELPER_PIECE_KEY
    )
    assert not any(path.kind == PATH_CENTER for path in helper.slot_paths)
    assert any(path.kind == PATH_DRAW and path.fill for path in helper.slot_paths)
    image = _render(helper)
    assert _hex(image, 0.0, 0.0) == "#333333"
    assert _hex(image, 0.0, 49.5) == "#000000"
    selected = _render(helper, selected=True)
    assert _hex(selected, 0.0, 20.0) == "#333333"
    close = _render(helper, scale=1.0)
    wide = _render(helper, scale=4.0)
    assert _hex(close, 0.0, 20.0, scale=1.0) == "#333333"
    assert _hex(wide, 0.0, 20.0, scale=4.0) == "#333333"
    turned = _render(helper, rotation=90.0)
    assert _signature(image) != _signature(turned)
    assert _count(turned, "#333333") > 100
    assert _count(turned, "#000000") > 0
    bare = _straight_spec("EB-PLAIN", lanes=2)
    assert bare.slot_paths == ()
    plain = _render(bare)
    assert _signature(plain) != _signature(_render(bare, rotation=15.0))
    assert any(pixel != 0xFFFFFFFF for pixel in _signature(plain))


def test_centre_marks_are_stored_edited_and_do_not_add_a_lane() -> None:
    source = next(
        spec for spec in load_carrera_library().parts if spec.source_id == ACTIVE_STRAIGHT_SOURCE
    )
    session = GraphicSession()
    session.snap_grid = False
    session.snap_points = False
    session.load(source)
    imported = [item.spans for item in session.elements if item.kind == KIND_CENTER]
    assert imported
    session.set_tool(TOOL_CENTER)
    session.click(12.0, 8.0, tolerance_mm=1)
    session.click(90.0, 8.0, tolerance_mm=1)
    session.set_tool(TOOL_SELECT)
    custom_id = next(item.id for item in session.elements if item.z_order < 0)
    session.select_ids(frozenset({custom_id}))
    session.set_mark_style(stroke="#445566", width_mm=2.5, dashed=True)
    session.click(50.0, 8.0, tolerance_mm=4)
    session.move(50.0, 18.0, tolerance_mm=1)
    session.release()
    compiled = session.compile(
        article_number=source.article_number,
        scale=source.scale,
        name=source.name,
        manufacturer=source.manufacturer,
        description=source.description,
        category=source.category,
        as_draft=False,
    )
    assert compiled.source_id == source.source_id
    assert compiled.piece_key == source.piece_key
    assert compiled.lane_count == source.lane_count
    assert compiled.connectors == source.connectors
    assert [path for path in compiled.slot_paths if path.kind == PATH_SLOT] == [
        path for path in source.slot_paths if path.kind == PATH_SLOT
    ]
    assert [path for path in compiled.slot_paths if path.kind == PATH_DRAW] == [
        path for path in source.slot_paths if path.kind == PATH_DRAW
    ]
    custom = next(path for path in compiled.slot_paths if path.stroke == "#445566")
    assert custom.kind == PATH_CENTER
    assert custom.lane is None
    assert custom.dashed
    assert custom.width_mm == pytest.approx(2.5)
    assert custom.spans[0].y0 == pytest.approx(18.0)
    kept = [
        item.spans for item in session.elements if item.kind == KIND_CENTER and item.z_order >= 0
    ]
    assert kept == imported
    restored = decode_slot_paths(encode_slot_paths(compiled.slot_paths))
    assert next(path for path in restored if path.stroke == "#445566").dashed
    reloaded = GraphicSession()
    reloaded.load(compiled)
    again = next(item for item in reloaded.elements if item.stroke == "#445566")
    assert again.dashed
    assert again.spans[0].x1 == pytest.approx(90.0)
    reloaded.select_ids(frozenset({again.id}))
    reloaded.delete_selection()
    without = reloaded.compile(
        article_number=source.article_number,
        scale=source.scale,
        name=source.name,
        manufacturer=source.manufacturer,
        description="",
        category=source.category,
        as_draft=False,
    )
    assert not any(path.stroke == "#445566" for path in without.slot_paths)
    assert any(path.fill for path in without.slot_paths)
    curved = GraphicSession()
    curved.snap_grid = False
    curved.snap_points = False
    curved.set_tool(TOOL_CENTER)
    curved.set_tool(TOOL_ARC)
    curved.click(0.0, 0.0, tolerance_mm=1)
    curved.click(60.0, 0.0, tolerance_mm=1)
    curved.click(30.0, 24.0, tolerance_mm=1)
    curved.set_tool(TOOL_BEZIER)
    curved.click(60.0, 0.0, tolerance_mm=1)
    curved.click(90.0, 10.0, tolerance_mm=1)
    curved.click(80.0, 20.0, tolerance_mm=1)
    spans = next(item.spans for item in curved.elements if item.kind == KIND_CENTER)
    assert [span.kind for span in spans] == [SPAN_ARC, "bezier"]
    assert next(item.lane for item in curved.elements if item.kind == KIND_CENTER) is None


def test_a_new_part_can_save_a_centre_mark_and_an_old_plan_stays_put(env: Env) -> None:
    planner = _planner(env)
    session = GraphicSession()
    draw_straight(session, length=160.0, lanes=2)
    before = session.compile(
        article_number="EB-MARK",
        scale="1:24",
        name="Eigene Gerade",
        manufacturer="Werkstatt",
        description="",
        category="straight",
        as_draft=False,
    )
    lanes = before.lane_count
    joints = len(before.connectors)
    session.set_tool(TOOL_CENTER)
    session.click(20.0, 0.0, tolerance_mm=6)
    session.click(120.0, 0.0, tolerance_mm=6)
    session.set_mark_style(stroke="#778899", width_mm=3.0, dashed=False)
    spec = session.compile(
        article_number="EB-MARK",
        scale="1:24",
        name="Eigene Gerade",
        manufacturer="Werkstatt",
        description="",
        category="straight",
        as_draft=False,
    )
    assert spec.lane_count == lanes
    assert len(spec.connectors) == joints
    record = planner.add_part(spec)
    loaded = planner.library.require(record.id).spec
    assert any(path.kind == PATH_CENTER and path.stroke == "#778899" for path in loaded.slot_paths)
    track = env.track("Markierung", lanes=2)
    plan = place_instance(empty_plan(track.id), record.id, loaded, 0, 0, 0, {record.id: loaded})
    planner.save(plan)
    moved = SlotPath(
        (SlotSpan.line(20.0, 4.0, 120.0, 4.0),),
        PATH_CENTER,
        stroke="#778899",
        width_mm=3.0,
        dashed=True,
        z_order=-1,
    )
    updated = planner.update_part(
        record.id,
        replace(
            loaded,
            slot_paths=tuple(
                moved if path.kind == PATH_CENTER else path for path in loaded.slot_paths
            ),
        ),
    )
    assert updated.id != record.id
    assert planner.load(track.id).instances[0].part_id == record.id
    assert planner.library.require(record.id).spec.slot_paths == loaded.slot_paths
    assert any(path.dashed for path in updated.spec.slot_paths)
    assert not any(path.dashed for path in planner.library.require(record.id).spec.slot_paths)


def test_missing_source_paint_is_restored_without_touching_stock_or_joints(env: Env) -> None:
    planner = _planner(env)
    planner.list_parts()
    straight = next(
        record for record in planner.list_parts() if record.spec.source_id == ACTIVE_STRAIGHT_SOURCE
    )
    planner.set_stock(straight.id, 4)
    with env.runtime.database.session() as session:
        row = session.get(TrackPartDefinition, straight.id)
        assert row is not None
        joints = [
            (item.name, item.x_mm, item.y_mm, item.direction_deg)
            for item in session.scalars(
                select(TrackPartConnector)
                .where(TrackPartConnector.part_id == straight.id)
                .order_by(TrackPartConnector.id)
            )
        ]
        outline = copy.deepcopy(row.outline)
        article = row.article_number
        source_id = row.source_id
        piece_key = row.piece_key
        stripped = tuple(path for path in straight.spec.slot_paths if path.kind == PATH_SLOT)
        row.slot_paths = encode_geometry(replace(straight.spec, slot_paths=stripped))
        assert row.origin == ORIGIN_CATALOG
    planner.library.ensure_seed()
    restored = planner.library.require(straight.id)
    assert restored.id == straight.id
    assert restored.spec.article_number == article
    assert restored.spec.source_id == source_id
    assert restored.spec.piece_key == piece_key
    assert any(path.fill == "#333333" for path in restored.spec.slot_paths)
    assert any(
        path.kind == PATH_CENTER and path.stroke == "#e0e0e0" for path in restored.spec.slot_paths
    )
    assert [path for path in restored.spec.slot_paths if path.kind == PATH_SLOT] == list(stripped)
    with env.runtime.database.session() as session:
        row = session.get(TrackPartDefinition, straight.id)
        assert row is not None
        assert row.outline == outline
        stock = session.get(TrackPartStock, straight.id)
        assert stock is not None and stock.quantity == 4
        raw = copy.deepcopy(row.slot_paths)
        current_joints = [
            (item.name, item.x_mm, item.y_mm, item.direction_deg)
            for item in session.scalars(
                select(TrackPartConnector)
                .where(TrackPartConnector.part_id == straight.id)
                .order_by(TrackPartConnector.id)
            )
        ]
    assert current_joints == joints
    planner.library.ensure_seed()
    with env.runtime.database.session() as session:
        row = session.get(TrackPartDefinition, straight.id)
        assert row is not None
        assert row.slot_paths == raw


def test_a_modified_groove_is_not_repainted_from_the_source_file(env: Env) -> None:
    planner = _planner(env)
    straight = next(
        record for record in planner.list_parts() if record.spec.source_id == ACTIVE_STRAIGHT_SOURCE
    )
    with env.runtime.database.session() as session:
        row = session.get(TrackPartDefinition, straight.id)
        assert row is not None
        row.origin = ORIGIN_MODIFIED
        stripped = tuple(path for path in straight.spec.slot_paths if path.kind == PATH_SLOT)
        row.slot_paths = encode_geometry(replace(straight.spec, slot_paths=stripped))
    planner.library.ensure_seed()
    kept = planner.library.require(straight.id)
    assert not any(path.fill for path in kept.spec.slot_paths)
    assert kept.spec.source_id == straight.spec.source_id


def test_an_old_geometry_payload_still_loads_and_the_editor_tool_is_live(
    qtbot: QtBot, env: Env
) -> None:
    payload = [
        {
            "kind": "slot",
            "lane": 1,
            "spans": [{"kind": "line", "x0": 0, "y0": -50, "x1": 100, "y1": -50}],
        }
    ]
    restored = decode_slot_paths(payload)
    assert restored[0].stroke is None
    assert restored[0].fill is None
    assert "stroke" not in encode_slot_paths(restored)[0]
    planner = _planner(env)
    record = next(
        item for item in planner.list_parts() if item.spec.source_id == ACTIVE_STRAIGHT_SOURCE
    )
    dialog = _editor(qtbot, env, record)
    button = dialog.findChild(QPushButton, "graphic-center")
    assert button is not None and button.text() == "Mittelmarkierung"
    button.click()
    assert dialog.graphic.session.tool == TOOL_CENTER
    dialog.graphic.session.snap_grid = False
    dialog.graphic.session.snap_points = False
    dialog.graphic.session.click(16.0, 7.0, tolerance_mm=1)
    dialog.graphic.session.click(64.0, 7.0, tolerance_mm=1)
    dialog.graphic.mark_color.setText("#cc2200")
    dialog.graphic.mark_width.setValue(3.0)
    dialog.graphic.mark_dashed.setChecked(True)
    preview = dialog._preview_spec()
    assert preview is not None
    mark = next(path for path in preview.slot_paths if path.stroke == "#cc2200")
    assert mark.kind == PATH_CENTER and mark.dashed and mark.width_mm == pytest.approx(3.0)
    assert mark.lane is None
    assert preview.source_id == record.spec.source_id
    assert preview.lane_count == record.spec.lane_count
    dialog.graphic.canvas.set_view(3.0, 80.0, 90.0)
    dialog.graphic.canvas.update()
    dialog.submit()
    assert dialog.saved is not None
    assert dialog.saved.id == record.id
    assert dialog.saved.spec.piece_key == record.spec.piece_key
    reopened = PartDialog(env.runtime.translator, planner, dialog.saved)
    qtbot.addWidget(reopened)
    reopened.show()
    qtbot.waitUntil(lambda: reopened.graphic.canvas.width() > 200)
    loaded = next(item for item in reopened.graphic.session.elements if item.stroke == "#cc2200")
    assert loaded.dashed and loaded.width_mm == pytest.approx(3.0)
    reopened.graphic.session.select_ids(frozenset({loaded.id}))
    reopened.graphic.session.set_mark_style(stroke="#00aa44", width_mm=1.5, dashed=False)
    edited = reopened._preview_spec()
    assert edited is not None
    assert any(path.stroke == "#00aa44" and not path.dashed for path in edited.slot_paths)
