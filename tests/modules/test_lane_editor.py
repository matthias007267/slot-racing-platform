"""Lane geometry from the element editor reopens, and a text save keeps a complex part."""

from __future__ import annotations

from pytestqt.qtbot import QtBot
from sqlalchemy import select

from slot_racing.modules.track_planner import editing as ed
from slot_racing.modules.track_planner.models import TrackPartDefinition
from slot_racing.modules.track_planner.parts import (
    SPAN_BEZIER,
    STANDARD_CURVE_ARTICLE,
    STANDARD_STRAIGHT_ARTICLE,
    same_geometry,
)
from slot_racing.modules.track_planner.ui.part_editor import PartDialog
from tests.modules.conftest import Env
from tests.modules.test_graphic_session import draw_straight
from tests.modules.test_parametric_editor import _draft, _save_draft
from tests.modules.test_part_revisions import _article
from tests.modules.test_track_parts import _planner


def _close(dialog: PartDialog) -> None:
    dialog._dismissed = True
    dialog.close()


def test_opening_a_draft_without_edits_keeps_the_revision(qtbot: QtBot, env: Env) -> None:
    planner = _planner(env)
    dialog = PartDialog(env.runtime.translator, planner)
    qtbot.addWidget(dialog)
    dialog.show()
    dialog.article.setText("EB-REV")
    dialog.name.setText("Revision")
    draw_straight(dialog.graphic.session, length=140.0, lanes=2)
    draft = dialog.findChild(type(dialog.graphic.finish_button), "part-editor-draft")
    assert draft is not None
    draft.click()
    assert dialog.saved is not None
    saved = dialog.saved
    opened = PartDialog(env.runtime.translator, planner, planner.library.require(saved.id))
    qtbot.addWidget(opened)
    opened.show()
    opened.description.setPlainText("Unverändert")
    draft_again = opened.findChild(type(opened.graphic.finish_button), "part-editor-draft")
    assert draft_again is not None
    draft_again.click()
    kept = planner.library.require(saved.id)
    assert kept.id == saved.id
    assert kept.revision == saved.revision
    assert kept.spec.description == "Unverändert"
    assert same_geometry(kept.spec, saved.spec)
    edited = PartDialog(env.runtime.translator, planner, kept)
    qtbot.addWidget(edited)
    edited.show()
    lane = edited.graphic.session.lanes()[0]
    edited.graphic.session.select_ids(frozenset({lane.id}))
    edited.graphic.session.set_metric("x", lane.spans[0].x0 + 15.0)
    save = edited.findChild(type(edited.graphic.finish_button), "part-editor-draft")
    assert save is not None
    save.click()
    assert edited.saved is not None
    assert edited.saved.id == saved.id
    assert edited.saved.revision == saved.revision
    assert not same_geometry(edited.saved.spec, saved.spec)
    with env.runtime.database.session() as session:
        rows = session.scalars(
            select(TrackPartDefinition).where(TrackPartDefinition.lineage_id == saved.lineage_id)
        ).all()
    assert [(row.id, row.revision) for row in rows] == [(saved.id, saved.revision)]


def test_complex_parts_keep_their_geometry_when_metadata_changes(qtbot: QtBot, env: Env) -> None:
    planner = _planner(env)
    custom = _save_draft(planner, _draft(length_mm=400.0), article="EB-BZ3", name="Bogen")
    bent = ed.explicit(custom.spec)
    ref = ed.groove_refs(bent)[0]
    bent = ed.convert_span(bent, ref, 0, SPAN_BEZIER)
    bent = ed.finalize(bent, custom.spec)
    complex_part = planner.update_part(custom.id, bent)
    records = (
        _article(planner, STANDARD_CURVE_ARTICLE),
        _article(planner, STANDARD_STRAIGHT_ARTICLE),
        _article(planner, "20020517"),
        complex_part,
    )
    for record in records:
        before = record.spec
        dialog = PartDialog(env.runtime.translator, planner, record)
        qtbot.addWidget(dialog)
        dialog.show()
        dialog.preview.repaint()
        dialog.description.setPlainText(f"{record.spec.description} Hinweis")
        dialog.accept()
        assert dialog.saved is not None
        assert dialog.saved.revision == record.revision
        assert same_geometry(dialog.saved.spec, before)
        assert dialog.saved.spec.slot_paths == before.slot_paths
        _close(dialog)
