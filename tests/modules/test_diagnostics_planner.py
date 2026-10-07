"""Breadcrumbs for the planner library, part deletion and page changes."""

from __future__ import annotations

from contextlib import suppress
from pathlib import Path

from pytestqt.qtbot import QtBot

from slot_racing.core.diagnostics import current, install
from slot_racing.core.errors import ValidationError
from slot_racing.modules.track_planner.document import empty_plan, place_instance
from slot_racing.modules.track_planner.ui.library_manager import LibraryManager
from slot_racing.modules.track_planner.ui.page import PlannerPage
from tests.modules.conftest import Env
from tests.modules.test_track_parts import _planner, _straight_spec
from tests.modules.test_ui_management import open_page


def test_opening_the_planner_records_the_library_phases(
    qtbot: QtBot, env: Env, tmp_path: Path
) -> None:
    service = install(tmp_path)
    try:
        env.track("Heim", lanes=2)
        window, page = open_page(qtbot, env, "track_planner")
        assert isinstance(page, PlannerPage)
        window.show()
        page.hide()
        names = [item.event for item in service.breadcrumbs.snapshot()]
        _assert_order(
            names,
            [
                "TRACK_PLANNER_OPEN",
                "LIBRARY_VIEW_OPEN_REQUEST",
                "LIBRARY_DATA_LOAD_START",
                "LIBRARY_DATA_LOAD_COMPLETE",
                "LIBRARY_WIDGET_BUILD_START",
                "LIBRARY_WIDGET_BUILD_COMPLETE",
                "LIBRARY_VIEW_VISIBLE",
                "LIBRARY_VIEW_CLOSE",
            ],
        )
        dialog = LibraryManager(env.runtime.translator, _planner(env))
        qtbot.addWidget(dialog)
        dialog.close()
        names = [item.event for item in service.breadcrumbs.snapshot()]
        _assert_order(names, ["LIBRARY_MANAGER_OPEN", "LIBRARY_MANAGER_CLOSE"])
    finally:
        running = current()
        if running is not None:
            running.close()


def test_part_deletion_records_success_or_a_block(env: Env, tmp_path: Path) -> None:
    service = install(tmp_path)
    try:
        planner = _planner(env)
        custom = planner.add_part(_straight_spec("EB-LOG", lanes=2, scale="1:24"))
        planner.delete_part(custom.id)
        kept = planner.add_part(_straight_spec("EB-LOG-USED", lanes=2, scale="1:43"))
        track = env.track("Belegt", lanes=2)
        plan = place_instance(
            empty_plan(track.id), kept.id, kept.spec, 0, 0, 0, {kept.id: kept.spec}
        )
        planner.save(plan)
        with suppress(ValidationError):
            planner.delete_part(kept.id)
        pairs = [
            (item.event, dict(item.fields).get("part_id"))
            for item in service.breadcrumbs.snapshot()
            if item.event.startswith("PART_DELETE")
        ]
        assert ("PART_DELETE_REQUEST", str(custom.id)) in pairs
        assert ("PART_DELETE_SUCCESS", str(custom.id)) in pairs
        assert ("PART_DELETE_REQUEST", str(kept.id)) in pairs
        assert ("PART_DELETE_BLOCKED", str(kept.id)) in pairs
        rendered = "\n".join(item.render() for item in service.breadcrumbs.snapshot())
        assert "EB-LOG" not in rendered
    finally:
        running = current()
        if running is not None:
            running.close()


def test_the_library_manager_blocks_a_used_part_before_deleting(
    qtbot: QtBot, env: Env, tmp_path: Path
) -> None:
    service = install(tmp_path)
    try:
        planner = _planner(env)
        part = planner.add_part(_straight_spec("EB-UI-BLOCK", lanes=2, scale="1:24"))
        dialog = LibraryManager(env.runtime.translator, planner, used_part_ids={part.id})
        qtbot.addWidget(dialog)
        dialog.select_part(part.id)
        dialog.delete_selected()
        events = [
            item.event
            for item in service.breadcrumbs.snapshot()
            if dict(item.fields).get("part_id") == str(part.id)
        ]
        assert events == ["PART_CREATE", "PART_DELETE_REQUEST", "PART_DELETE_BLOCKED"]
        dialog.close()
    finally:
        running = current()
        if running is not None:
            running.close()


def _assert_order(events: list[str], expected: list[str]) -> None:
    cursor = 0
    for name in expected:
        while cursor < len(events) and events[cursor] != name:
            cursor += 1
        assert cursor < len(events), name
        cursor += 1
