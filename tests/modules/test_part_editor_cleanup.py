"""The parametric form is gone. Drawing, plans and revisions stay."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

from PySide6.QtWidgets import QPushButton
from pytestqt.qtbot import QtBot
from sqlalchemy import select

from slot_racing.modules.track_planner import editing as ed
from slot_racing.modules.track_planner.attachment import attachment_fits
from slot_racing.modules.track_planner.document import empty_plan, place_instance
from slot_racing.modules.track_planner.parts import (
    SPAN_BEZIER,
    STANDARD_STRAIGHT_ARTICLE,
    rails_can_join,
    same_geometry,
)
from slot_racing.modules.track_planner.plugin import TrackPlannerPlugin
from slot_racing.modules.track_planner.ui.library_manager import LibraryManager
from slot_racing.modules.track_planner.ui.part_editor import PartDialog
from slot_racing.modules.tracks.models import TrackLayout
from tests.modules.conftest import Env
from tests.modules.test_graphic_session import draw_straight
from tests.modules.test_parametric_editor import _draft, _save_draft
from tests.modules.test_part_revisions import _article, _catalog
from tests.modules.test_track_parts import _planner

_REMOVED = (
    "part_graphic",
    "part_canvas",
    "edit_tools",
    "edit_session",
    "graphic_translations",
)


def test_removed_editor_modules_are_not_imported() -> None:
    """Test A: startup code no longer names the old drawing surface."""
    root = Path("src/slot_racing")
    offenders: list[str] = []
    for path in root.rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        for name in _REMOVED:
            if name in text:
                offenders.append(f"{path}:{name}")
    assert offenders == []
    assert TrackPlannerPlugin.manifest.name == "track_planner"
    for name in _REMOVED:
        assert not Path(f"src/slot_racing/modules/track_planner/{name}.py").exists()
        assert not Path(f"src/slot_racing/modules/track_planner/ui/{name}.py").exists()


def test_the_part_dialog_draws_and_has_no_parameter_tab(qtbot: QtBot, env: Env) -> None:
    """Test B: the element editor is the only surface, and the old tools stay gone."""
    dialog = PartDialog(env.runtime.translator, _planner(env))
    qtbot.addWidget(dialog)
    dialog.show()
    assert dialog.findChild(QPushButton, "graphic-plate") is not None
    assert dialog.findChild(QPushButton, "graphic-lane") is not None
    assert dialog.findChild(QPushButton, "graphic-bezier") is not None
    assert dialog.findChild(QPushButton, "plate-center") is None
    assert not hasattr(dialog, "length")
    assert not hasattr(dialog, "tabs")
    assert dialog.article.objectName() == "part-article"
    assert dialog.category.isEnabled()
    dialog._dismissed = True
    dialog.close()


def test_a_new_parametric_part_saves_and_reopens(qtbot: QtBot, env: Env) -> None:
    """Test C: create, save and open a simple straight again."""
    planner = _planner(env)
    dialog = PartDialog(env.runtime.translator, planner)
    qtbot.addWidget(dialog)
    dialog.show()
    dialog.article.setText("EB-CLEAN")
    dialog.name.setText("Saubere Gerade")
    draw_straight(dialog.graphic.session, length=220.0, lanes=2)
    dialog.accept()
    assert dialog.saved is not None
    stored = planner.library.require(dialog.saved.id)
    assert stored.spec.lane_count == 2
    assert len(stored.spec.connectors) == 2
    again = PartDialog(env.runtime.translator, planner, stored)
    qtbot.addWidget(again)
    again.show()
    again.preview.repaint()
    assert again.preview._spec is not None
    assert same_geometry(again.preview._spec, stored.spec)
    again._dismissed = True
    again.close()


def test_opening_a_carrera_part_does_not_change_its_geometry(qtbot: QtBot, env: Env) -> None:
    """Test D: opening a catalogue straight leaves the stored body alone."""
    planner = _planner(env)
    record = _article(planner, STANDARD_STRAIGHT_ARTICLE)
    before = record.spec
    dialog = PartDialog(env.runtime.translator, planner, record)
    qtbot.addWidget(dialog)
    dialog.show()
    dialog.preview.repaint()
    assert dialog.preview._spec is not None
    assert same_geometry(dialog.preview._spec, before)
    dialog._dismissed = True
    dialog.close()
    assert same_geometry(planner.library.require(record.id).spec, before)


def test_a_complex_part_cannot_be_simplified_by_the_form(qtbot: QtBot, env: Env) -> None:
    """Test E: explicit paths stay locked and a text save does not rewrite them."""
    planner = _planner(env)
    switch = _article(planner, "20020517")
    custom = _save_draft(planner, _draft(length_mm=400.0), article="EB-BZ", name="Bogen")
    bent = ed.explicit(custom.spec)
    ref = ed.groove_refs(bent)[0]
    bent = ed.convert_span(bent, ref, 0, SPAN_BEZIER)
    bent = ed.finalize(bent, custom.spec)
    stored_bent = planner.update_part(custom.id, bent)
    for record in (switch, stored_bent):
        dialog = PartDialog(env.runtime.translator, planner, record)
        qtbot.addWidget(dialog)
        dialog.show()
        dialog.preview.repaint()
        assert dialog.graphic.editable
        assert dialog.graphic.session.elements
        dialog.description.setPlainText("Nur Text")
        dialog.accept()
        kept = planner.library.require(record.id)
        assert kept.spec.description == "Nur Text"
        assert same_geometry(kept.spec, record.spec)
        assert kept.spec.length_mm == record.spec.length_mm
        dialog._dismissed = True


def test_the_library_lists_adds_edits_and_duplicates(qtbot: QtBot, env: Env) -> None:
    """Test F: library operations that do not depend on a drawing surface."""
    planner = _planner(env)
    created = _save_draft(planner, _draft(length_mm=180.0), article="EB-LIB", name="Liste")
    manager = LibraryManager(env.runtime.translator, planner)
    qtbot.addWidget(manager)
    manager.show()
    assert manager.search.text() == ""
    manager.search.setText("EB-LIB")
    visible = [row for row in manager._rows if not row.isHidden()]
    assert any(row.part_id == created.id for row in visible)
    manager.select_part(created.id)
    assert manager.edit_button.isEnabled()
    assert manager.duplicate_button.isEnabled()
    copy = PartDialog(env.runtime.translator, planner, created, duplicate=True)
    qtbot.addWidget(copy)
    copy.show()
    copy.article.setText("EB-LIB2")
    copy.accept()
    assert copy.saved is not None
    assert copy.saved.id != created.id
    assert same_geometry(copy.saved.spec, created.spec)
    assert any(record.spec.article_number == "EB-LIB2" for record in planner.list_parts())
    manager.close()


def test_a_part_can_still_be_placed_and_reloaded(env: Env) -> None:
    """Test G: the planner stores and reloads a placed part."""
    planner = _planner(env)
    record = _save_draft(planner, _draft(length_mm=345.0), article="EB-PLAN", name="Planstück")
    track = env.track("Plan", lanes=2)
    catalog = {record.id: record.spec}
    planner.save(place_instance(empty_plan(track.id), record.id, record.spec, 10, 20, 0, catalog))
    loaded = planner.load(track.id)
    assert loaded.instances[0].part_id == record.id
    assert (loaded.instances[0].x_mm, loaded.instances[0].y_mm) == (10, 20)


def test_plans_and_archives_keep_their_revision_when_text_changes(env: Env) -> None:
    """Test H: a text save does not move current plans or archives."""
    planner = _planner(env)
    record = _article(planner, "20020517")
    first = env.track("Bleibt", lanes=2)
    catalog = _catalog(record)
    planner.save(place_instance(empty_plan(first.id), record.id, record.spec, 4, 5, 0, catalog))
    planner.save(planner.load(first.id))
    with env.runtime.database.session() as session:
        before = [
            (row.id, row.data)
            for row in session.scalars(select(TrackLayout).where(TrackLayout.name != "plan"))
        ]
    renamed = planner.update_part(record.id, replace(record.spec, description="Archiv bleibt"))
    assert renamed.id == record.id
    assert planner.load(first.id).instances[0].part_id == record.id
    assert same_geometry(planner.library.require(record.id).spec, record.spec)
    with env.runtime.database.session() as session:
        after = [
            (row.id, row.data)
            for row in session.scalars(select(TrackLayout).where(TrackLayout.name != "plan"))
        ]
    assert after == before


def test_rail_joints_and_border_edges_stay_separate(env: Env) -> None:
    """Test I: rails join on track joints; a border still fits only its host edge."""
    planner = _planner(env)
    rail = _save_draft(planner, _draft(length_mm=345.0), article="EB-RAIL", name="Schiene")
    border = _save_draft(
        planner,
        _draft(category="border", body="border", length_mm=345.0, width_mm=40.0),
        article="EB-EDGE",
        name="Kante",
    )
    assert rail.spec.connectors
    assert all(joint.kind == "track" for joint in rail.spec.connectors)
    assert border.spec.connectors == ()
    assert rails_can_join(rail.spec.connectors[0], rail.spec.connectors[1])
    assert attachment_fits(border.spec, rail.spec, "left")
    assert attachment_fits(border.spec, rail.spec, "right")
    assert not attachment_fits(border.spec, rail.spec, "outer")
