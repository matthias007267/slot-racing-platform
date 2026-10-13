"""Acceptance scenarios of the graphical part editor, driven through the dialog and its canvas.

The widgets are exercised with pytest-qt only. No real window is clicked.
"""

from __future__ import annotations

import math
from dataclasses import replace

import pytest
from PySide6.QtWidgets import QComboBox, QDialog, QLabel, QLineEdit
from pytestqt.qtbot import QtBot
from sqlalchemy import select

from slot_racing.modules.track_planner import editing as ed
from slot_racing.modules.track_planner.attachment import attachment_fits
from slot_racing.modules.track_planner.document import (
    TrackPlan,
    attach_accessory,
    empty_plan,
    place_instance,
)
from slot_racing.modules.track_planner.lane_length import connected_runs
from slot_racing.modules.track_planner.models import TrackPartDefinition
from slot_racing.modules.track_planner.parametric import (
    BODY_BORDER,
    SIDE_BOTH,
    spec_from_draft,
)
from slot_racing.modules.track_planner.parts import (
    BORDER,
    SPAN_BEZIER,
    SPECIAL,
    STANDARD_STRAIGHT_ARTICLE,
    PartInstance,
    PartRecord,
    Pose,
    driving_paths,
    join_pose,
    rails_can_join,
    same_geometry,
    snap_pose,
)
from slot_racing.modules.track_planner.service import TrackPlannerService
from slot_racing.modules.track_planner.ui.part_editor import PartDialog
from slot_racing.modules.tracks.models import TrackLayout
from tests.modules.conftest import Env
from tests.modules.test_graphic_editor_ui import (
    _button,
    _click,
    _custom_straight,
    _drag,
    _draw,
    _fill_identity,
    _no_modal_questions,  # noqa: F401  (autouse fixture)
    _open,
    _squeeze,
    _tool,
)
from tests.modules.test_part_revisions import _article, _catalog
from tests.modules.test_track_parts import _planner


def _save(dialog: PartDialog) -> None:
    dialog.accept()
    assert dialog.result() == QDialog.DialogCode.Accepted


def _seat_next(
    plan_part: PartRecord, plan: TrackPlan, first: int, second: int, nudge: float = 6.0
) -> TrackPlan:
    """Place a copy of ``plan_part`` next to the last one and let the rail snap pull it in."""
    last = plan.instances[-1]
    pose = join_pose(last, plan_part.spec.connectors[first], plan_part.spec.connectors[second])
    return place_instance(
        plan,
        plan_part.id,
        plan_part.spec,
        pose.x_mm + nudge,
        pose.y_mm + nudge / 2.0,
        pose.rotation_z_deg,
        {plan_part.id: plan_part.spec},
    )


# --------------------------------------------------------------------------------------------
# 1. Engstelle: two lanes squeeze to the middle and apart


def test_a_new_engstelle_is_drawn_saved_and_connected_in_the_planner(
    qtbot: QtBot, env: Env
) -> None:
    planner = _planner(env)
    with _open(qtbot, env, planner) as dialog:
        _fill_identity(dialog, "EB-ENG", "Eigene Engstelle")
        editor = _draw(dialog)
        _squeeze(editor)
        spec = editor.session.spec
        lane_one = ed.groove_refs(spec)[0]
        assert len(ed.spans_of(spec, lane_one)) == 5
        assert editor.session.issues() == ()

        _button(editor, "gfx-contour-from-outline").click()
        assert editor.session.spec.contours
        _tool(editor, "contour")
        corner = max(
            ((span.x1, span.y1) for span in editor.session.spec.contours[0].spans),
            key=lambda point: point[0] - point[1],
        )
        _drag(editor.canvas, corner, (corner[0], corner[1] + 8.0))
        contour_points = [(span.x1, span.y1) for span in editor.session.spec.contours[0].spans]
        assert any(
            abs(x - corner[0]) < 1e-6 and abs(y - (corner[1] + 8.0)) < 1.5
            for x, y in contour_points
        )
        assert not any(abs(y - corner[1]) < 1e-6 and x > 0 for x, y in contour_points)

        _tool(editor, "connector")
        _click(editor.canvas, 172.5, 0.0)
        lanes = editor.findChild(QLineEdit, "gfx-conn-lanes")
        assert isinstance(lanes, QLineEdit)
        lanes.setText("1, 2")
        lanes.editingFinished.emit()
        assert editor.session.issues() == ()
        _save(dialog)

    record = _article(planner, "EB-ENG")
    assert record.spec.category == SPECIAL
    assert len(record.spec.connectors) == 2
    assert all(connector.lanes == (1, 2) for connector in record.spec.connectors)
    single = sorted(path.length_mm() for path in driving_paths(record.spec))
    assert len(single) == 2
    shortcut = 22.5 * 2 + 2 * math.hypot(80.0, 30.0) + 140.0
    assert single[0] == pytest.approx(shortcut, abs=3.0)

    track = env.track("Engstelle", lanes=2)
    plan = place_instance(
        empty_plan(track.id), record.id, record.spec, 0, 0, 0, {record.id: record.spec}
    )
    plan = _seat_next(record, plan, 1, 0)
    first, second = plan.instances
    seated = join_pose(first, record.spec.connectors[1], record.spec.connectors[0])
    assert second.x_mm == pytest.approx(seated.x_mm)
    assert second.y_mm == pytest.approx(seated.y_mm)
    assert second.rotation_z_deg == pytest.approx(seated.rotation_z_deg)
    assert rails_can_join(record.spec.connectors[1], record.spec.connectors[0])
    runs = connected_runs(plan.instances, {record.id: record.spec})
    assert runs[0].part_count == 2
    assert len(runs[0].lanes) == 2
    assert runs[0].lanes[0].length_mm == pytest.approx(2 * single[0], abs=0.02)
    planner.save(plan)
    again = planner.load(track.id)
    assert [item.part_id for item in again.instances] == [record.id, record.id]


# --------------------------------------------------------------------------------------------
# 2. Schikane: several segments per lane, Bézier control points


def test_a_new_chicane_with_bezier_control_points_is_saved_and_used(qtbot: QtBot, env: Env) -> None:
    planner = _planner(env)
    with _open(qtbot, env, planner) as dialog:
        _fill_identity(dialog, "EB-SCH", "Eigene Schikane")
        editor = _draw(dialog)
        canvas = editor.canvas
        editor.controller.run(ed.explicit)
        ref = ed.groove_refs(editor.session.spec)[0]
        other = ed.groove_refs(editor.session.spec)[1]
        _tool(editor, "add_point")
        _click(canvas, 0.0, -50.0)
        _click(canvas, 0.0, 50.0)
        editor.controller.run(lambda spec: ed.convert_span(spec, ref, 0, "bezier"))
        editor.controller.run(lambda spec: ed.convert_span(spec, other, 0, "bezier"))
        first = ed.spans_of(editor.session.spec, ref)[0]
        assert first.kind == SPAN_BEZIER
        p1 = ed.handle_points(first)["p1"]
        _tool(editor, "move")
        p2 = ed.handle_points(first)["p2"]
        _drag(canvas, p2, (p2[0], p2[1] - 18.0))
        moved = ed.handle_points(ed.spans_of(editor.session.spec, ref)[0])["p2"]
        assert moved[1] == pytest.approx(p2[1] - 18.0, abs=1.5)
        assert ed.handle_points(ed.spans_of(editor.session.spec, ref)[0])["p1"] == p1
        before = editor.session.spec
        editor.session.undo()
        assert editor.session.spec != before
        editor.session.redo()
        assert editor.session.spec == before

        _tool(editor, "select")
        _click(canvas, 0.0, -50.0)
        target = editor.controller.selection
        assert target is not None
        tangent = editor.findChild(QComboBox, "gfx-tangent")
        assert isinstance(tangent, QComboBox)
        report = ed.joint_report(editor.session.spec, ref)
        assert isinstance(report, tuple)
        assert editor.session.issues() == ()
        drawn_p1 = ed.handle_points(ed.spans_of(editor.session.spec, ref)[0])["p1"]
        _save(dialog)

    record = _article(planner, "EB-SCH")
    stored = ed.spans_of(record.spec, ed.groove_refs(record.spec)[0])
    assert stored[0].kind == SPAN_BEZIER
    assert ed.handle_points(stored[0])["p1"] == drawn_p1
    track = env.track("Schikane", lanes=2)
    plan = place_instance(
        empty_plan(track.id), record.id, record.spec, 0, 0, 0, {record.id: record.spec}
    )
    plan = _seat_next(record, plan, 1, 0)
    runs = connected_runs(plan.instances, {record.id: record.spec})
    assert runs[0].part_count == 2
    assert len(runs[0].lanes) == 2
    planner.save(plan)
    loaded = planner.library.require(record.id).spec
    assert (
        ed.handle_points(ed.spans_of(loaded, ed.groove_refs(loaded)[0])[0])["p1"]
        == (ed.handle_points(stored[0])["p1"])
    )


# --------------------------------------------------------------------------------------------
# 3. Own border: matching attachment edge, strip contour, never a rail joint


def _host_with_edges(planner: TrackPlannerService) -> PartRecord:
    base = spec_from_draft(
        _custom_straight(345.0),
        article_number="EB-HOST",
        scale="1:24",
        name="Eigene Gerade mit Kanten",
        manufacturer="",
        description="",
        height_mm=None,
    )
    return planner.add_part(ed.with_side_edges(ed.explicit(base)))


def _standard_border(planner: TrackPlannerService) -> PartRecord:
    draft = replace(
        _custom_straight(345.0),
        category=BORDER,
        body=BODY_BORDER,
        width_mm=40.0,
        border_side=SIDE_BOTH,
    )
    spec = spec_from_draft(
        draft,
        article_number="EB-RAND",
        scale="1:24",
        name="Eigener Rand",
        manufacturer="",
        description="",
        height_mm=None,
    )
    return planner.add_part(spec)


def test_an_own_border_is_attached_to_a_compatible_part_and_never_joins_a_rail(
    qtbot: QtBot, env: Env
) -> None:
    planner = _planner(env)
    host = _host_with_edges(planner)
    other = _article(planner, STANDARD_STRAIGHT_ARTICLE)
    border = _standard_border(planner)
    with _open(qtbot, env, planner, border) as dialog:
        editor = _draw(dialog)
        assert editor.canvas.read_only
        _button(editor, "gfx-border-convert").click()
        assert not editor.canvas.read_only
        picker = editor.findChild(QComboBox, "gfx-host-part")
        assert isinstance(picker, QComboBox)
        picker.setCurrentIndex(picker.findData(host.id))
        slot = editor.findChild(QComboBox, "gfx-host-slot")
        assert isinstance(slot, QComboBox)
        slot.setCurrentIndex(slot.findData("left"))
        _button(editor, "gfx-copy-edge").click()
        _button(editor, "gfx-strip-make").click()
        assert editor.session.issues() == ()
        _tool(editor, "edge")
        assert editor.controller.layer == ed.LAYER_ATTACH
        _save(dialog)

    current = next(
        record for record in planner.list_parts() if record.spec.article_number == "EB-RAND"
    )
    assert current.spec.attachment is not None
    assert current.spec.attachment.edge
    assert current.spec.connectors == ()
    assert current.spec.contours
    assert attachment_fits(current.spec, host.spec, "left")
    assert not attachment_fits(current.spec, host.spec, "right")
    assert not attachment_fits(current.spec, other.spec, "left")

    track = env.track("Rand", lanes=2)
    catalog = {host.id: host.spec, current.id: current.spec}
    plan = place_instance(empty_plan(track.id), host.id, host.spec, 120, 50, 37, catalog)
    mounted = attach_accessory(
        plan, plan.instances[0].id, current.id, current.spec, "left", catalog
    )
    strip = next(item for item in mounted.instances if item.part_id == current.id)
    assert strip.host_id == plan.instances[0].id
    with pytest.raises(Exception):  # noqa: B017
        attach_accessory(plan, plan.instances[0].id, current.id, current.spec, "right", catalog)

    rail = PartInstance("rail", host.id, 0, 0)
    ground = PartInstance("strip", current.id, 0, 140)
    pose = join_pose(rail, host.spec.connectors[1], host.spec.connectors[0])
    snapped = snap_pose(
        host.spec,
        Pose(pose.x_mm + 5, pose.y_mm + 3, pose.rotation_z_deg),
        ((rail, host.spec), (ground, current.spec)),
        snap_mm=40,
        grid_mm=None,
    )
    assert snapped.x_mm == pytest.approx(pose.x_mm)
    idle = snap_pose(
        current.spec,
        Pose(pose.x_mm, pose.y_mm, 0),
        ((rail, host.spec),),
        snap_mm=500,
        grid_mm=None,
    )
    assert idle.x_mm == pytest.approx(pose.x_mm)
    assert not any(
        rails_can_join(connector, host.spec.connectors[0]) for connector in current.spec.connectors
    )


# --------------------------------------------------------------------------------------------
# 4. A Carrera Engstelle is edited graphically: new revision, old tracks keep their geometry


def test_editing_a_carrera_engstelle_forks_a_revision_and_adoption_stays_explicit(
    qtbot: QtBot, env: Env
) -> None:
    planner = _planner(env)
    original = _article(planner, "20030350")
    first = env.track("Eins", lanes=2)
    second = env.track("Zwei", lanes=2)
    catalog = _catalog(original)
    planner.set_stock(original.id, 3)
    for track in (first, second):
        planner.save(
            place_instance(empty_plan(track.id), original.id, original.spec, 5, 6, 0, catalog)
        )
    before = [dict(row) for row in _archive_rows(env)]

    with _open(qtbot, env, planner, original) as dialog:
        editor = _draw(dialog)
        assert not editor.canvas.read_only
        ref = ed.groove_refs(editor.session.spec)[0]
        node = ed.PointRef(ref, 1, "start")
        x, y = ed.handle_points(ed.spans_of(editor.session.spec, ref)[1])["start"]
        editor.controller.run(lambda spec: ed.move_point(spec, node, x, y + 4.0))
        assert editor.session.issues() == ()
        _save(dialog)

    revised = next(
        record
        for record in planner.list_parts()
        if record.spec.article_number == "20030350" and record.id != original.id
    )
    assert revised.revision == 2
    assert not same_geometry(revised.spec, original.spec)
    assert same_geometry(planner.library.require(original.id).spec, original.spec)
    for track in (first, second):
        assert planner.load(track.id).instances[0].part_id == original.id
    waiting = {int(track_id) for track_id, _name in planner.tracks_on_previous_geometry(revised.id)}
    assert waiting == {int(first.id), int(second.id)}
    assert planner.adopt_revision(revised.id, [first.id]) == 1
    assert planner.load(first.id).instances[0].part_id == revised.id
    assert planner.load(second.id).instances[0].part_id == original.id
    assert planner.stock_quantities()[revised.id] == 3
    assert [dict(row) for row in _archive_rows(env)] == before


def _archive_rows(env: Env) -> list[dict[str, object]]:
    with env.runtime.database.session() as session:
        rows = session.scalars(select(TrackLayout).where(TrackLayout.name != "plan")).all()
        return [{"id": row.id, "data": row.data} for row in rows]


# --------------------------------------------------------------------------------------------
# Unsupported and damaged parts, persistence details


def test_a_switch_is_shown_read_only_and_only_its_text_can_change(qtbot: QtBot, env: Env) -> None:
    planner = _planner(env)
    switch = next(
        record
        for record in planner.list_parts()
        if ed.support_reason(record.spec) not in {"", ed.SUPPORT_OK}
    )
    with _open(qtbot, env, planner, switch) as dialog:
        editor = _draw(dialog)
        assert editor.canvas.read_only
        notice = editor.findChild(QLabel, "gfx-notice")
        assert isinstance(notice, QLabel)
        assert notice.text()
        _click(editor.canvas, 0.0, 0.0)
        assert not editor.session.can_undo
        dialog.description.setPlainText("Nur Text")
        _save(dialog)
    kept = planner.library.require(switch.id)
    assert same_geometry(kept.spec, switch.spec)
    assert kept.spec.description == "Nur Text"


def test_a_corrupt_stored_geometry_is_never_replaced_by_the_editor(qtbot: QtBot, env: Env) -> None:
    planner = _planner(env)
    created = _saved(planner)
    with env.runtime.database.session() as session:
        row = session.get(TrackPartDefinition, created.id)
        assert row is not None
        row.slot_paths = [{"kind": "slot", "spans": "broken"}]
    record = planner.library.require(created.id)
    assert record.geometry_corrupt
    with _open(qtbot, env, planner, record) as dialog:
        editor = _draw(dialog)
        assert editor.canvas.read_only
        _click(editor.canvas, 0.0, 0.0)
        assert not editor.session.can_undo
        dialog.name.setText("Text bleibt")
        _save(dialog)
    with env.runtime.database.session() as session:
        stored = session.get(TrackPartDefinition, created.id)
        assert stored is not None
        assert stored.slot_paths == [{"kind": "slot", "spans": "broken"}]
        assert stored.name == "Text bleibt"


def test_a_drawn_part_and_its_duplicate_keep_their_control_points_after_reload(
    qtbot: QtBot, env: Env
) -> None:
    planner = _planner(env)
    created = _saved(planner)
    with _open(qtbot, env, planner, created) as dialog:
        editor = _draw(dialog)
        ref = ed.groove_refs(editor.session.spec)[0]
        editor.controller.run(lambda spec: ed.split_span(spec, ref, 0, 0.5))
        editor.controller.run(lambda spec: ed.convert_span(spec, ref, 1, "bezier"))
        p1 = ed.handle_points(ed.spans_of(editor.session.spec, ref)[1])["p1"]
        _tool(editor, "move")
        _drag(editor.canvas, p1, (p1[0] + 20.0, p1[1] - 10.0))
        drawn = ed.handle_points(ed.spans_of(editor.session.spec, ref)[1])
        _save(dialog)
    after = next(r for r in planner.list_parts() if r.spec.article_number == "EB-G")
    stored = ed.handle_points(ed.spans_of(after.spec, ed.groove_refs(after.spec)[0])[1])
    assert stored == drawn

    with _open(qtbot, env, planner, after, duplicate=True) as dialog:
        _fill_identity(dialog, "EB-G2", "Kopie")
        editor = _draw(dialog)
        assert ed.handle_points(ed.spans_of(editor.session.spec, ref)[1]) == drawn
        _save(dialog)
    copy = next(r for r in planner.list_parts() if r.spec.article_number == "EB-G2")
    assert copy.id != after.id
    assert same_geometry(copy.spec, after.spec)
    assert same_geometry(planner.library.require(after.id).spec, after.spec)


def _saved(planner: TrackPlannerService) -> PartRecord:
    from tests.modules.test_graphic_editor_ui import _saved_straight

    return _saved_straight(planner)
