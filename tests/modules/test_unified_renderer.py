"""One part painter for the plan, the library and both editors."""

from __future__ import annotations

from dataclasses import replace

import pytest
from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QColor, QImage, QPainter
from pytestqt.qtbot import QtBot

from slot_racing.core.domain import TrackId
from slot_racing.modules.track_planner.catalog_import import _short_label, load_carrera_library
from slot_racing.modules.track_planner.document import empty_plan, place_instance
from slot_racing.modules.track_planner.graphic_session import TOOL_CENTER, GraphicSession
from slot_racing.modules.track_planner.parts import (
    ACTIVE_CURVE_SOURCE,
    ACTIVE_STRAIGHT_SOURCE,
    PATH_CENTER,
    PATH_CONTOUR,
    PATH_DRAW,
    PartInstance,
    PartSpec,
    Pose,
    SlotPath,
    SlotSpan,
    same_geometry,
    snap_pose,
)
from slot_racing.modules.track_planner.plate_session import PlateSession
from slot_racing.modules.track_planner.ui.canvas import MM, InstanceItem
from slot_racing.modules.track_planner.ui.graphic_editor import GraphicEditor
from slot_racing.modules.track_planner.ui.page import PlannerPage
from slot_racing.modules.track_planner.ui.plate_editor import PlateEditor
from slot_racing.modules.track_planner.ui.track_paint import paint_part, short_label_rect
from slot_racing.uikit.theme import COLORS
from tests.modules.conftest import Env
from tests.modules.test_graphic_session import draw_straight
from tests.modules.test_track_parts import _library_row, _planner, _select, _straight_spec
from tests.modules.test_track_rendering import _preview
from tests.modules.test_ui_management import open_page

_SIZE = 900
_SCALE = 2.0


def _by_source(source_id: str) -> PartSpec:
    return next(spec for spec in load_carrera_library().parts if spec.source_id == source_id)


def _article(article: str) -> PartSpec:
    return next(
        spec
        for spec in load_carrera_library().parts
        if spec.article_number == article and spec.piece_key == ""
    )


def _rect(x0: float, y0: float, x1: float, y1: float) -> tuple[SlotSpan, ...]:
    return (
        SlotSpan.line(x0, y0, x1, y0),
        SlotSpan.line(x1, y0, x1, y1),
        SlotSpan.line(x1, y1, x0, y1),
        SlotSpan.line(x0, y1, x0, y0),
    )


def _painted(
    paths: tuple[SlotPath, ...],
    *,
    contours: tuple[SlotPath, ...] = (),
    outline: tuple[tuple[float, float], ...] = (),
    label: str = "",
    center: tuple[float, float] = (0.0, 0.0),
    angle: float = 0.0,
) -> PartSpec:
    return PartSpec(
        article_number="T",
        scale="1:24",
        name="T",
        category="straight",
        length_mm=80.0,
        width_mm=40.0,
        height_mm=None,
        radius_mm=None,
        angle_deg=None,
        lane_count=1,
        connectors=(),
        outline=outline,
        slot_paths=paths,
        contours=contours,
        short_label=label,
        center_x_mm=center[0],
        center_y_mm=center[1],
        center_angle_deg=angle,
    )


def _render(
    spec: PartSpec,
    *,
    scale: float = _SCALE,
    rotation: float = 0.0,
    coded: bool = False,
    selected: bool = False,
    label: bool = False,
    origin: tuple[float, float] = (0.0, 0.0),
) -> QImage:
    image = QImage(_SIZE, _SIZE, QImage.Format.Format_ARGB32_Premultiplied)
    image.fill(Qt.GlobalColor.white)
    painter = QPainter(image)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, False)
    painter.translate(_SIZE / 2.0, _SIZE / 2.0)
    painter.rotate(rotation)
    painter.scale(scale, scale)
    painter.translate(-origin[0], -origin[1])
    paint_part(
        painter,
        spec,
        color_coding=coded,
        selected=selected,
        start_straight=False,
        show_short_label=label,
    )
    painter.end()
    return image


def _hex(
    image: QImage,
    x_mm: float,
    y_mm: float,
    *,
    scale: float = _SCALE,
    origin: tuple[float, float] = (0.0, 0.0),
) -> str:
    pixel = image.pixelColor(
        int(_SIZE / 2.0 + (x_mm - origin[0]) * scale),
        int(_SIZE / 2.0 + (y_mm - origin[1]) * scale),
    )
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


def _black_run(image: QImage, x_px: int) -> int:
    best = 0
    run = 0
    for y_px in range(image.height()):
        pixel = image.pixelColor(x_px, y_px)
        if pixel.red() < 16 and pixel.green() < 16 and pixel.blue() < 16:
            run += 1
            best = max(best, run)
        else:
            run = 0
    return best


def _delta_box(
    before: QImage, after: QImage, *, window: int = 250
) -> tuple[int, int, float, float]:
    xs: list[int] = []
    ys: list[int] = []
    left = before.width() // 2 - window
    right = before.width() // 2 + window
    top = before.height() // 2 - window
    bottom = before.height() // 2 + window
    for y_px in range(top, bottom, 2):
        for x_px in range(left, right, 2):
            if before.pixel(x_px, y_px) != after.pixel(x_px, y_px):
                xs.append(x_px)
                ys.append(y_px)
    assert xs
    return max(xs) - min(xs), max(ys) - min(ys), sum(xs) / len(xs), sum(ys) / len(ys)


def _assert_badge_has_pad_and_ink(before: QImage, after: QImage) -> None:
    """The badge is a white plate with dark glyphs, independent of one sample point."""
    pad = 0
    ink = 0
    left = before.width() // 2 - 250
    right = before.width() // 2 + 250
    top = before.height() // 2 - 250
    bottom = before.height() // 2 + 250
    for y_px in range(top, bottom, 2):
        for x_px in range(left, right, 2):
            if before.pixel(x_px, y_px) == after.pixel(x_px, y_px):
                continue
            color = after.pixelColor(x_px, y_px)
            if color.red() > 240 and color.green() > 240 and color.blue() > 240:
                pad += 1
            elif color.red() < 40 and color.green() < 40 and color.blue() < 40:
                ink += 1
    assert pad > 10
    assert ink > 4


def test_german_short_labels_come_from_the_file_and_empty_ones_stay_blank() -> None:
    straight = _by_source(ACTIVE_STRAIGHT_SOURCE)
    curve = _by_source(ACTIVE_CURVE_SOURCE)
    border = _article("20560")
    assert straight.short_label == "1/1"
    assert straight.center_angle_deg == pytest.approx(0.0)
    assert curve.short_label == "K1/60"
    assert curve.center_y_mm == pytest.approx(-297.0)
    assert curve.center_angle_deg == pytest.approx(0.0)
    assert border.short_label == ""
    assert _short_label({"shortLabel": {"en": "K1", "de": ""}}) == "K1"
    assert _short_label({"shortLabel": {}}) == ""
    assert _short_label({"shortLabel": {"de": " 1/1 "}}) == "1/1"
    assert same_geometry(curve, replace(curve, short_label="ZZ", center_angle_deg=40.0))


def test_straights_curves_switches_and_borders_keep_their_source_ink() -> None:
    straight = _by_source(ACTIVE_STRAIGHT_SOURCE)
    curve = _by_source(ACTIVE_CURVE_SOURCE)
    switch = _article("20517")
    border = _article("20560")
    assert switch.category == "switch"
    assert _hex(_render(straight), 0.0, 20.0) == "#333333"
    assert _hex(_render(straight), 0.0, 0.0) == "#e0e0e0"
    assert _hex(_render(straight), 0.0, 49.5) == "#000000"
    assert _count(_render(curve), "#333333") > 100
    assert _count(_render(switch), "#333333") > 50
    stripes = _render(border)
    assert _hex(stripes, -120.0, -45.0) == "#b51a00"
    assert _hex(stripes, -160.0, -45.0) == "#ffffff"
    opened = replace(straight, contours=())
    assert _hex(_render(opened), 0.0, 20.0) == "#333333"
    assert _hex(_render(opened), 0.0, 49.5) == "#000000"


def test_paint_order_colour_variants_transparency_and_dashes() -> None:
    back = SlotPath(_rect(-20, -20, 20, 20), PATH_DRAW, fill="#ff0000", z_order=1)
    front = SlotPath(_rect(-8, -8, 8, 8), PATH_DRAW, fill="#0000ff", z_order=0)
    stacked = _render(_painted((back, front)))
    assert _hex(stacked, 0.0, 0.0) == "#0000ff"
    assert _hex(stacked, 14.0, 0.0) == "#ff0000"
    swapped = _render(_painted((replace(back, z_order=0), replace(front, z_order=1))))
    assert _hex(swapped, 0.0, 0.0) == "#ff0000"
    tinted = SlotPath(
        _rect(-12, -12, 12, 12), PATH_DRAW, fill="#112233", fill_alt="#b66948", z_order=0
    )
    assert _hex(_render(_painted((tinted,))), 0.0, 0.0) == "#112233"
    assert _hex(_render(_painted((tinted,)), coded=True), 0.0, 0.0) == "#b66948"
    glass = SlotPath(_rect(-10, -10, 10, 10), PATH_DRAW, fill="#80ff0000", z_order=0)
    pixel = QColor(_hex(_render(_painted((glass,))), 0.0, 0.0))
    assert pixel.red() > 200
    assert 80 < pixel.green() < 200
    assert abs(pixel.green() - pixel.blue()) < 8
    stroke = SlotPath(
        (SlotSpan.line(-30, 0, 30, 0),),
        PATH_DRAW,
        stroke="#000000",
        width_mm=4.0,
        linecap="butt",
        z_order=0,
    )
    solid = _render(_painted((stroke,)))
    dashed = _render(_painted((replace(stroke, dashed=True),)))
    assert _count(solid, "#000000") > _count(dashed, "#000000") > 0


def test_clip_cuts_overflow_on_a_box_and_on_a_slanted_edge() -> None:
    fill = SlotPath(_rect(-40, -40, 40, 40), PATH_DRAW, fill="#ff0000", z_order=0)
    box = SlotPath(_rect(-20, -20, 20, 20), PATH_CONTOUR)
    outline = ((-20.0, -20.0), (20.0, -20.0), (20.0, 20.0), (-20.0, 20.0))
    clipped = _painted((fill,), contours=(box,), outline=outline)
    image = _render(clipped)
    assert _hex(image, 0.0, 0.0) == "#ff0000"
    assert _hex(image, 30.0, 0.0) == "#ffffff"
    assert _hex(_render(_painted((fill,))), 30.0, 0.0) == "#ff0000"
    selected = _render(clipped, selected=True)
    assert _hex(selected, 21.0, 0.0) == "#2fbf71"
    assert _hex(image, 21.0, 0.0) == "#ffffff"
    triangle = SlotPath(
        (
            SlotSpan.line(0, 0, 40, 0),
            SlotSpan.line(40, 0, 0, 30),
            SlotSpan.line(0, 30, 0, 0),
        ),
        PATH_CONTOUR,
    )
    stroke = SlotPath(
        (SlotSpan.line(-10, 0, 50, 0),),
        PATH_DRAW,
        stroke="#000000",
        width_mm=8.0,
        linecap="butt",
        z_order=0,
    )
    cut = _render(_painted((stroke,), contours=(triangle,)))
    assert _hex(cut, 10.0, 0.0) == "#000000"
    assert _hex(cut, 20.0, 1.0) == "#000000"
    assert _hex(cut, 45.0, 0.0) == "#ffffff"
    assert _hex(cut, 20.0, -2.0) == "#ffffff"
    open_edge = _render(_painted((stroke,), contours=()))
    assert _hex(open_edge, 45.0, 0.0) == "#000000"
    upright = _count(image, "#ff0000")
    turned = _count(_render(clipped, rotation=90.0), "#ff0000")
    assert upright > 40
    assert turned == pytest.approx(upright, rel=0.25)
    arcs = (
        SlotPath((SlotSpan.arc(0, 0, 80, 0, 90),), PATH_CONTOUR),
        SlotPath((SlotSpan.arc(0, 0, 40, 0, 90),), PATH_CONTOUR),
    )
    wide = SlotPath(_rect(-120, -120, 120, 120), PATH_DRAW, fill="#ff0000", z_order=0)
    assert _hex(_render(_painted((wide,), contours=arcs)), 90.0, 0.0) == "#ff0000"


def test_a_source_stroke_width_scales_with_the_view() -> None:
    def stroke(width: float) -> PartSpec:
        return _painted(
            (
                SlotPath(
                    (SlotSpan.line(-40, 0, 40, 0),),
                    PATH_DRAW,
                    stroke="#000000",
                    width_mm=width,
                    linecap="butt",
                    z_order=0,
                ),
            )
        )

    narrow = _black_run(_render(stroke(4.0), scale=2.0), _SIZE // 2)
    wide = _black_run(_render(stroke(8.0), scale=2.0), _SIZE // 2)
    zoomed = _black_run(_render(stroke(4.0), scale=4.0), _SIZE // 2)
    assert narrow >= 6
    assert wide == pytest.approx(narrow * 2, abs=2)
    assert zoomed == pytest.approx(narrow * 2, abs=2)


def test_short_labels_follow_the_centre_and_its_angle(qapp: object) -> None:
    curve = _by_source(ACTIVE_CURVE_SOURCE)
    origin = (curve.center_x_mm, curve.center_y_mm)
    hidden = _render(curve, label=False, origin=origin, scale=1.5)
    shown = _render(curve, label=True, origin=origin, scale=1.5)
    turned = _render(replace(curve, center_angle_deg=90.0), label=True, origin=origin, scale=1.5)
    shifted = _render(
        replace(curve, center_x_mm=curve.center_x_mm + 36.0),
        label=True,
        origin=origin,
        scale=1.5,
    )
    assert _hex(hidden, origin[0], origin[1], scale=1.5, origin=origin) == "#333333"
    assert _signature(hidden) != _signature(shown)
    width, height, center_x, center_y = _delta_box(hidden, shown)
    plain_turned = _render(
        replace(curve, center_angle_deg=90.0), label=False, origin=origin, scale=1.5
    )
    turned_w, turned_h, _, _ = _delta_box(plain_turned, turned)
    plain_shifted = _render(
        replace(curve, center_x_mm=curve.center_x_mm + 36.0),
        label=False,
        origin=origin,
        scale=1.5,
    )
    _, _, moved_x, moved_y = _delta_box(plain_shifted, shifted)
    assert width > height * 2
    assert turned_h > turned_w * 2
    assert moved_x == pytest.approx(center_x + 36.0 * 1.5, abs=8)
    assert moved_y == pytest.approx(center_y, abs=8)
    close_off = _render(curve, label=False, origin=origin, scale=1.0)
    close_on = _render(curve, label=True, origin=origin, scale=1.0)
    far_off = _render(curve, label=False, origin=origin, scale=3.0)
    far_on = _render(curve, label=True, origin=origin, scale=3.0)
    close_w, close_h, _, _ = _delta_box(close_off, close_on, window=140)
    far_w, far_h, _, _ = _delta_box(far_off, far_on, window=420)
    assert close_w > 40 and close_h > 16
    assert far_w == pytest.approx(close_w * 3, abs=12)
    assert far_h == pytest.approx(close_h * 3, abs=12)
    straight = _by_source(ACTIVE_STRAIGHT_SOURCE)
    plain_straight = _render(straight)
    labelled_straight = _render(straight, label=True)
    assert _hex(plain_straight, 0.0, 0.0) == "#e0e0e0"
    # The badge, not one glyph pixel. A wider face paints "1/1" through the
    # point that is padding on another face; the plate under it is not white.
    straight_w, straight_h, straight_x, straight_y = _delta_box(plain_straight, labelled_straight)
    assert straight_w > straight_h
    assert straight_x == pytest.approx(_SIZE / 2, abs=8)
    assert straight_y == pytest.approx(_SIZE / 2, abs=8)
    upright = _render(replace(straight, center_angle_deg=90.0), label=True)
    upright_plain = _render(replace(straight, center_angle_deg=90.0))
    upright_w, upright_h, _, _ = _delta_box(upright_plain, upright)
    assert upright_h > upright_w
    _assert_badge_has_pad_and_ink(plain_straight, labelled_straight)


def test_centre_marks_and_a_custom_part_without_source_drawings() -> None:
    straight = _by_source(ACTIVE_STRAIGHT_SOURCE)
    assert _hex(_render(straight), 0.0, 0.0) == "#e0e0e0"
    mark = SlotPath(
        (SlotSpan.line(-20, 0, 20, 0),),
        PATH_CENTER,
        stroke="#445566",
        width_mm=4.0,
        z_order=-1,
    )
    custom = _render(_painted((mark,)))
    assert _hex(custom, 0.0, 0.0) == "#445566"
    bare = _straight_spec("EB-BARE", lanes=2)
    assert bare.slot_paths == ()
    assert bare.contours == ()
    assert bare.short_label == ""
    plain = _render(bare)
    assert any(pixel != 0xFFFFFFFF for pixel in _signature(plain))
    assert _signature(plain) != _signature(_render(bare, rotation=20.0))


def test_a_saved_mark_paints_the_same_after_it_is_loaded_again(env: Env) -> None:
    planner = _planner(env)
    session = GraphicSession()
    draw_straight(session, length=160.0, lanes=2)
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
    painted = _render(spec)
    assert _count(painted, "#778899") > 0
    record = planner.add_part(spec)
    loaded = planner.library.require(record.id).spec
    assert _signature(_render(loaded)) == _signature(painted)
    assert loaded.connectors == spec.connectors
    assert loaded.outline == spec.outline


def test_labels_do_not_move_a_snapped_curve() -> None:
    curve = _by_source(ACTIVE_CURVE_SOURCE)
    host = PartInstance("host", 1, 0.0, 0.0)
    seated = snap_pose(curve, Pose(12.0, -6.0, 55.0), ((host, curve),), snap_mm=200, grid_mm=None)
    assert (seated.x_mm, seated.y_mm, seated.rotation_z_deg) == pytest.approx((0.0, 0.0, 60.0))
    labeled = replace(curve, short_label="ZZ", center_angle_deg=33.0)
    again = snap_pose(
        labeled, Pose(12.0, -6.0, 55.0), ((host, labeled),), snap_mm=200, grid_mm=None
    )
    assert (again.x_mm, again.y_mm, again.rotation_z_deg) == pytest.approx((0.0, 0.0, 60.0))
    plan = place_instance(empty_plan(TrackId(1)), 7, curve, 80.0, -25.0, 15.0, {7: curve})
    placed = plan.instances[0]
    assert (placed.x_mm, placed.y_mm, placed.rotation_z_deg) == pytest.approx((80.0, -25.0, 15.0))


def test_the_editor_uses_the_same_body_as_the_plan(qtbot: QtBot, env: Env) -> None:
    straight = _by_source(ACTIVE_STRAIGHT_SOURCE)
    shared = _render(straight)
    editor = GraphicEditor(env.runtime.translator)
    qtbot.addWidget(editor)
    editor.session.load(straight)
    editor.session.grid_mm = 0
    editor.spec_provider = lambda: straight
    editor.resize(1100, 800)
    editor.show()
    qtbot.waitUntil(lambda: editor.canvas.width() > 400)
    canvas = editor.canvas
    canvas.set_view(2.0, canvas.width() / 2.0, canvas.height() / 2.0)
    image = canvas.grab().toImage()
    center_x = int(canvas.width() / 2.0)
    road = image.pixelColor(center_x, int(canvas.height() / 2.0 + 40.0))
    assert (road.red(), road.green(), road.blue()) == (0x33, 0x33, 0x33)
    assert _hex(shared, 0.0, 20.0) == "#333333"
    info = QColor(COLORS.info)
    for y_px in range(0, image.height(), 2):
        for x_px in range(0, image.width(), 2):
            pixel = image.pixelColor(x_px, y_px)
            assert (pixel.red(), pixel.green(), pixel.blue()) != (
                info.red(),
                info.green(),
                info.blue(),
            )


def test_a_closed_plate_does_not_repaint_its_lane_in_the_editor(qtbot: QtBot, env: Env) -> None:
    editor = PlateEditor(env.runtime.translator)
    qtbot.addWidget(editor)
    editor.session = PlateSession(
        ((0.0, -50.0), (180.0, -50.0), (180.0, 50.0), (0.0, 50.0)),
        closed=True,
        lanes=((SlotSpan.line(0.0, 0.0, 180.0, 0.0),),),
    )
    editor.resize(1000, 700)
    editor.show()
    qtbot.waitUntil(lambda: editor.canvas.width() > 300)
    image = editor.canvas.grab().toImage()
    mid = image.pixelColor(editor.canvas.width() // 2, editor.canvas.height() // 2)
    assert mid.name().lower() not in {
        QColor(COLORS.info).name().lower(),
        QColor(COLORS.accent).name().lower(),
    }


def test_short_labels_are_a_plan_switch_and_stay_out_of_the_library(qtbot: QtBot, env: Env) -> None:
    track = env.track("Ring", lanes=2)
    window, page = open_page(qtbot, env, "track_planner")
    window.show()
    assert isinstance(page, PlannerPage)
    _select(page, track.id)
    assert page.short_labels.text() == "Kurzbezeichnung"
    assert page.short_labels.isChecked() is False
    assert not hasattr(env.runtime.config, "track_planner_short_labels")
    stored = next(
        record
        for record in page._planner.list_parts()
        if record.spec.source_id == ACTIVE_CURVE_SOURCE
    )
    assert stored.spec.short_label == "K1/60"
    assert stored.spec.center_angle_deg == pytest.approx(0.0)
    row = _library_row(page, "20577")
    page.library.setCurrentRow(row)
    page.place_part.click()
    item = next(piece for piece in page.canvas.scene().items() if isinstance(piece, InstanceItem))
    assert item.spec.short_label == "K1/30"
    assert item._short_labels is False
    shape = item.shape().boundingRect()
    preview = _preview(page, "20577")
    before = _pixels(preview.grab().toImage())
    page.short_labels.click()
    assert page.short_labels.isChecked() is True
    assert item._short_labels is True
    assert item.shape().boundingRect() == shape
    badge = _badge(item)
    assert badge is not None
    assert item.boundingRect().contains(badge)
    assert env.runtime.config.track_planner_color_coding is False
    assert _pixels(preview.grab().toImage()) == before
    page.short_labels.click()
    assert item._short_labels is False
    assert item.boundingRect() == item._content_bounds()


def _pixels(image: QImage) -> tuple[int, ...]:
    return tuple(
        image.pixel(x_px, y_px)
        for y_px in range(0, image.height(), 3)
        for x_px in range(0, image.width(), 3)
    )


def test_a_placed_part_keeps_its_hit_shape_when_the_label_is_shown(qapp: object) -> None:
    spec = _painted(
        (SlotPath(_rect(-10, -10, 10, 10), PATH_DRAW, fill="#333333", z_order=0),),
        contours=(SlotPath(_rect(-10, -10, 10, 10), PATH_CONTOUR),),
        outline=((-10.0, -10.0), (10.0, -10.0), (10.0, 10.0), (-10.0, 10.0)),
        label="AB",
        center=(0.0, 80.0),
    )
    item = InstanceItem(PartInstance("a", 1, 10.0, -4.0, rotation_z_deg=15.0), spec, _ignore)
    shape = item.shape().boundingRect()
    bounds = item.boundingRect()
    item.set_short_labels(True)
    assert item.shape().boundingRect() == shape
    assert item.boundingRect().height() > bounds.height()
    badge = _badge(item)
    assert badge is not None
    assert item.boundingRect().contains(badge)
    assert not shape.contains(badge.center())
    assert item.pos().x() == pytest.approx(10.0 * MM)
    assert item.rotation() == pytest.approx(15.0)


def _badge(item: InstanceItem) -> QRectF | None:
    rect = short_label_rect(item.spec)
    if rect is None:
        return None
    return QRectF(rect.x() * MM, rect.y() * MM, rect.width() * MM, rect.height() * MM)


def _ignore(_group: str, _moves: list[tuple[str, float, float]]) -> None:
    return None
