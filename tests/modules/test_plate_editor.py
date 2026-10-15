"""A drawn plate can be saved as a draft, and existing parts stay untouched."""

from __future__ import annotations

import pytest
from PySide6.QtWidgets import QLabel, QPushButton
from pytestqt.qtbot import QtBot
from sqlalchemy import select

from slot_racing.modules.track_planner.document import empty_plan, place_instance
from slot_racing.modules.track_planner.parts import STANDARD_STRAIGHT_ARTICLE, same_geometry
from slot_racing.modules.track_planner.ui.page import PlannerPage
from slot_racing.modules.track_planner.ui.part_editor import PartDialog
from slot_racing.modules.tracks.models import TrackLayout
from tests.modules.conftest import Env
from tests.modules.test_graphic_session import draw_straight
from tests.modules.test_part_revisions import _article, _catalog
from tests.modules.test_track_parts import _library_row, _planner, _select
from tests.modules.test_ui_management import open_page


def _editor(qtbot: QtBot, env: Env, record: object = None) -> PartDialog:
    dialog = PartDialog(env.runtime.translator, _planner(env), record)  # type: ignore[arg-type]
    qtbot.addWidget(dialog)
    dialog.resize(1100, 760)
    dialog.show()
    return dialog


def _close(dialog: PartDialog) -> None:
    dialog._dismissed = True
    dialog.close()


def test_a_dirty_dialog_closes_when_the_test_ends(qtbot: QtBot, env: Env) -> None:
    dialog = _editor(qtbot, env)
    dialog.name.setText("Nicht gespeichert")
    assert dialog._dirty()
    assert not dialog._dismissed


def test_an_open_plate_is_not_stored_as_a_part(qtbot: QtBot, env: Env) -> None:
    planner = _planner(env)
    dialog = _editor(qtbot, env)
    dialog.article.setText("EB-OPEN")
    dialog.name.setText("Offen")
    dialog.graphic.session.snap_grid = False
    dialog.graphic.session.snap_points = False
    dialog.graphic.session.set_tool("plate")
    dialog.graphic.session.click(0.0, 0.0, tolerance_mm=6)
    dialog.graphic.session.click(40.0, 0.0, tolerance_mm=6)
    dialog.accept()
    assert dialog.saved is None
    assert dialog.isVisible()
    assert "Bodenplatte" in dialog.message.text()
    assert not any(record.spec.article_number == "EB-OPEN" for record in planner.list_parts())
    _close(dialog)


def test_a_draft_stays_out_of_the_planner_palette(qtbot: QtBot, env: Env) -> None:
    planner = _planner(env)
    dialog = _editor(qtbot, env)
    dialog.article.setText("EB-HIDE")
    dialog.name.setText("Versteckt")
    dialog.graphic.session.snap_grid = False
    dialog.graphic.session.set_tool("plate")
    for point in ((0.0, 0.0), (200.0, 0.0), (200.0, 120.0), (0.0, 120.0)):
        dialog.graphic.session.click(*point, tolerance_mm=6)
    dialog.graphic.session.click(0.0, 0.0, tolerance_mm=6)
    draft = dialog.findChild(QPushButton, "part-editor-draft")
    assert isinstance(draft, QPushButton)
    draft.click()
    assert dialog.saved is not None
    assert dialog.saved.spec.category == "draft"
    track = env.track("Palette", lanes=2)
    window, page = open_page(qtbot, env, "track_planner")
    window.show()
    assert isinstance(page, PlannerPage)
    _select(page, track.id)
    with pytest.raises(AssertionError, match="EB-HIDE"):
        _library_row(page, "EB-HIDE")
    assert _library_row(page, STANDARD_STRAIGHT_ARTICLE) >= 0
    from slot_racing.modules.track_planner.ui.library_manager import LibraryManager

    manager = LibraryManager(env.runtime.translator, planner)
    qtbot.addWidget(manager)
    manager.show()
    manager.search.setText("EB-HIDE")
    visible = [row for row in manager._rows if not row.isHidden()]
    assert any(row.part_id == dialog.saved.id for row in visible)
    labels = []
    for row in range(page.library.count()):
        item = page.library.item(row)
        card = None if item is None else page.library.itemWidget(item)
        label = None if card is None else card.findChild(QLabel, "planner-part-article")
        if label is not None:
            labels.append(label.text())
    assert all("EB-HIDE" not in text for text in labels)
    manager.close()


def test_opening_a_used_part_does_not_fork_a_revision(qtbot: QtBot, env: Env) -> None:
    planner = _planner(env)
    record = _article(planner, "20020517")
    track = env.track("Bleibt", lanes=2)
    planner.save(
        place_instance(empty_plan(track.id), record.id, record.spec, 4, 5, 0, _catalog(record))
    )
    with env.runtime.database.session() as session:
        before = [
            (row.id, row.data)
            for row in session.scalars(select(TrackLayout).where(TrackLayout.name != "plan"))
        ]
    revision = planner.library.require(record.id).revision
    dialog = _editor(qtbot, env, record)
    dialog.preview.repaint()
    assert dialog.preview._spec is not None
    assert same_geometry(dialog.preview._spec, record.spec)
    _close(dialog)
    kept = planner.library.require(record.id)
    assert kept.id == record.id
    assert kept.revision == revision
    assert same_geometry(kept.spec, record.spec)
    assert planner.load(track.id).instances[0].part_id == record.id
    with env.runtime.database.session() as session:
        after = [
            (row.id, row.data)
            for row in session.scalars(select(TrackLayout).where(TrackLayout.name != "plan"))
        ]
    assert after == before


def test_a_straight_from_the_editor_can_be_placed(qtbot: QtBot, env: Env) -> None:
    dialog = _editor(qtbot, env)
    dialog.article.setText("EB-PLACE")
    dialog.name.setText("Platzierbar")
    draw_straight(dialog.graphic.session, length=160.0, lanes=2)
    dialog.accept()
    assert dialog.saved is not None
    track = env.track("Platz", lanes=2)
    window, page = open_page(qtbot, env, "track_planner")
    window.show()
    assert isinstance(page, PlannerPage)
    _select(page, track.id)
    page.library.setCurrentRow(_library_row(page, "EB-PLACE"))
    page.place_part.click()
    assert page.plan().instances[0].part_id == dialog.saved.id
