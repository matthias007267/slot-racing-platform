"""Slot strokes end on the groove, without a round cap past the roadway."""

from __future__ import annotations

import math

import pytest
from PySide6.QtGui import QColor, QImage, QPainter
from pytestqt.qtbot import QtBot

from slot_racing.modules.track_planner.appearance import SLOT_RIM_MM
from slot_racing.modules.track_planner.figure import track_figure
from slot_racing.modules.track_planner.parts import (
    LANE_PITCH_MM,
    SPAN_ARC,
    PartInstance,
    PartSpec,
    join_pose,
    span_point,
    standard_catalog,
)
from slot_racing.modules.track_planner.ui.track_paint import paint_part

_PROBE_MM = SLOT_RIM_MM / 4.0


def _spec(article: str) -> PartSpec:
    return next(spec for spec in standard_catalog() if spec.article_number == article)


def _paint(parts: list[tuple[PartInstance, PartSpec]]) -> QImage:
    image = QImage(1400, 1000, QImage.Format.Format_RGB32)
    image.fill(QColor("#ff00ff"))
    painter = QPainter(image)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, False)
    painter.translate(200.0, 500.0)
    for instance, spec in parts:
        painter.save()
        painter.translate(instance.x_mm, instance.y_mm)
        painter.rotate(instance.rotation_z_deg)
        paint_part(
            painter,
            spec,
            color_coding=False,
            selected=False,
            start_straight=False,
        )
        painter.restore()
    painter.end()
    return image


def _color(image: QImage, x_mm: float, y_mm: float) -> QColor:
    return image.pixelColor(round(200.0 + x_mm), round(500.0 + y_mm))


def _slot(color: QColor) -> bool:
    return color.red() < 45 and color.blue() < 60


def test_a_straight_slot_keeps_its_geometric_ends() -> None:
    spec = _spec("20020601")
    assert spec.length_mm is not None
    for path in track_figure(spec).slots:
        span = path.spans[0]
        assert span.x0 == pytest.approx(-spec.length_mm / 2)
        assert span.x1 == pytest.approx(spec.length_mm / 2)
        assert span.y0 == pytest.approx(span.y1)


def test_straight_slot_ink_ends_flush_at_both_ends(qtbot: QtBot) -> None:
    del qtbot
    spec = _spec("20020601")
    assert spec.length_mm is not None
    image = _paint([(PartInstance("a", 1, 0.0, 0.0), spec)])
    y = -LANE_PITCH_MM / 2
    half = spec.length_mm / 2
    for end in (-half, half):
        direction = -1.0 if end < 0 else 1.0
        assert _slot(_color(image, end - direction * _PROBE_MM, y))
        outside = _color(image, end + direction * _PROBE_MM, y)
        assert not _slot(outside)
        assert outside.red() > 200


def test_a_rotated_straight_stays_flush(qtbot: QtBot) -> None:
    del qtbot
    spec = _spec("20020601")
    assert spec.length_mm is not None
    image = _paint([(PartInstance("a", 1, 0.0, 0.0, rotation_z_deg=90.0), spec)])
    half = spec.length_mm / 2
    x = LANE_PITCH_MM / 2
    assert _slot(_color(image, x, half - _PROBE_MM))
    assert not _slot(_color(image, x, half + _PROBE_MM))
    assert _slot(_color(image, x, -half + _PROBE_MM))
    assert not _slot(_color(image, x, -half - _PROBE_MM))


def test_two_straights_meet_without_a_gap_or_a_cap(qtbot: QtBot) -> None:
    del qtbot
    spec = _spec("20020601")
    assert spec.length_mm is not None
    host = PartInstance("a", 1, 0.0, 0.0)
    pose = join_pose(host, spec.connectors[1], spec.connectors[0])
    guest = PartInstance("b", 1, pose.x_mm, pose.y_mm, rotation_z_deg=pose.rotation_z_deg)
    image = _paint([(host, spec), (guest, spec)])
    y = -LANE_PITCH_MM / 2
    joint = spec.length_mm / 2
    assert _slot(_color(image, joint - _PROBE_MM, y))
    assert _slot(_color(image, joint + _PROBE_MM, y))
    free = spec.length_mm + joint
    assert not _slot(_color(image, free + _PROBE_MM, y))
    assert not _slot(_color(image, -joint - _PROBE_MM, y))


def test_a_curve_slot_ends_on_the_arc_and_meets_a_straight(qtbot: QtBot) -> None:
    del qtbot
    curve = _spec("20020571")
    straight = _spec("20020601")
    assert straight.length_mm is not None
    span = track_figure(curve).slots[0].spans[0]
    assert span.kind == SPAN_ARC
    assert span.sweep_deg == pytest.approx(curve.angle_deg or 0.0)
    end = span_point(span, 1.0)
    tangent = math.radians(span.start_deg + span.sweep_deg + 90.0)
    step = (_PROBE_MM * math.cos(tangent), _PROBE_MM * math.sin(tangent))
    image = _paint([(PartInstance("c", 2, 0.0, 0.0), curve)])
    assert _slot(_color(image, end[0] - step[0], end[1] - step[1]))
    outside = _color(image, end[0] + step[0], end[1] + step[1])
    assert not _slot(outside)

    host = PartInstance("s", 1, 0.0, 0.0)
    pose = join_pose(host, straight.connectors[1], curve.connectors[0])
    guest = PartInstance("c", 2, pose.x_mm, pose.y_mm, rotation_z_deg=pose.rotation_z_deg)
    joined = _paint([(host, straight), (guest, curve)])
    y = -LANE_PITCH_MM / 2
    joint = straight.length_mm / 2
    assert _slot(_color(joined, joint - _PROBE_MM, y))
    assert _slot(_color(joined, joint + _PROBE_MM, y))
