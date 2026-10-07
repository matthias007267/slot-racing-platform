"""Reopen the stock collection through the real modal dialog."""

from __future__ import annotations

import gc
from collections.abc import Callable
from pathlib import Path

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication, QLineEdit, QPushButton, QWidget
from pytestqt.qtbot import QtBot
from shiboken6 import Shiboken

from slot_racing.core.diagnostics import current, install
from slot_racing.modules.track_planner.parts import STANDARD_STRAIGHT_ARTICLE
from slot_racing.modules.track_planner.ui.collection_dialog import CollectionDialog
from slot_racing.modules.track_planner.ui.library_dialog import PartDialog
from slot_racing.modules.track_planner.ui.library_manager import LibraryManager
from slot_racing.modules.track_planner.ui.page import PlannerPage
from tests.modules.conftest import Env
from tests.modules.test_track_parts import _select
from tests.modules.test_ui_management import open_page


def test_the_collection_dialog_survives_repeated_open_and_close(
    qtbot: QtBot, env: Env, tmp_path: Path
) -> None:
    service = install(tmp_path)
    try:
        page = _open(qtbot, env, "Sammlung")
        part_id = _part_id(page, STANDARD_STRAIGHT_ARTICLE)
        seen: list[str] = []

        def close_without_editing() -> None:
            dialog = _require_collection()
            seen.append(_quantity(dialog, part_id))
            _assert_single_connections(dialog, part_id)
            _assert_row_parent(dialog, part_id)
            _click(dialog, "collection-close")

        for _ in range(4):
            _run_modal(page.manage_stock.click, close_without_editing)
            _assert_dialog_gone(page, CollectionDialog)

        assert seen == [seen[0]] * 4

        def increase() -> None:
            dialog = _require_collection()
            _assert_single_connections(dialog, part_id)
            _click(dialog, f"collection-plus-{part_id}")
            _click(dialog, "collection-close")

        _run_modal(page.manage_stock.click, increase)
        _assert_dialog_gone(page, CollectionDialog)
        changed = str(int(seen[0]) + 1)

        def expect_changed() -> None:
            dialog = _require_collection()
            assert _quantity(dialog, part_id) == changed
            _assert_single_connections(dialog, part_id)
            _click(dialog, "collection-close")

        _run_modal(page.manage_stock.click, expect_changed)
        _assert_dialog_gone(page, CollectionDialog)
        assert page._planner.stock_quantities()[part_id] == int(changed)

        page.library.setCurrentRow(0)
        page.place_part.click()
        assert page.plan().instances
        assert page.manage_stock.isEnabled()
        assert page.canvas.isEnabled()

        names = [
            item.event
            for item in service.breadcrumbs.snapshot()
            if item.event.startswith("COLLECTION_DIALOG_")
        ]
        assert (
            names
            == [
                "COLLECTION_DIALOG_CREATE",
                "COLLECTION_DIALOG_OPEN",
                "COLLECTION_DIALOG_RETURN",
                "COLLECTION_DIALOG_DESTROY",
            ]
            * 6
        )
    finally:
        running = current()
        if running is not None:
            running.close()


def test_library_manager_and_part_dialog_survive_repeated_open_and_close(
    qtbot: QtBot, env: Env
) -> None:
    page = _open(qtbot, env, "Bibliothek")

    def close_library() -> None:
        dialog = QApplication.activeModalWidget()
        assert isinstance(dialog, LibraryManager)
        dialog.close()

    def reject_part() -> None:
        dialog = QApplication.activeModalWidget()
        assert isinstance(dialog, PartDialog)
        dialog.reject()

    for _ in range(3):
        _run_modal(page.manage_library.click, close_library)
        _assert_dialog_gone(page, LibraryManager)
    for _ in range(3):
        _run_modal(page.add_part.click, reject_part)
        _assert_dialog_gone(page, PartDialog)

    page.library.setCurrentRow(0)
    before = len(page.plan().instances)
    page.place_part.click()
    assert len(page.plan().instances) == before + 1
    assert page.manage_library.isEnabled()
    assert page.add_part.isEnabled()


def _open(qtbot: QtBot, env: Env, name: str) -> PlannerPage:
    track = env.track(name, lanes=2)
    window, page = open_page(qtbot, env, "track_planner")
    window.show()
    assert isinstance(page, PlannerPage)
    _select(page, track.id)
    return page


def _run_modal(open_dialog: Callable[[], None], finish: Callable[[], None]) -> None:
    QTimer.singleShot(0, finish)
    open_dialog()
    QApplication.processEvents()
    gc.collect()


def _require_collection() -> CollectionDialog:
    dialog = QApplication.activeModalWidget()
    assert isinstance(dialog, CollectionDialog)
    return dialog


def _quantity(dialog: CollectionDialog, part_id: int) -> str:
    field = dialog.findChild(QLineEdit, f"collection-quantity-{part_id}")
    assert isinstance(field, QLineEdit)
    return field.text()


def _click(dialog: CollectionDialog, object_name: str) -> None:
    button = dialog.findChild(QPushButton, object_name)
    assert isinstance(button, QPushButton)
    button.click()


def _assert_single_connections(dialog: CollectionDialog, part_id: int) -> None:
    close = dialog.findChild(QPushButton, "collection-close")
    plus = dialog.findChild(QPushButton, f"collection-plus-{part_id}")
    minus = dialog.findChild(QPushButton, f"collection-minus-{part_id}")
    field = dialog.findChild(QLineEdit, f"collection-quantity-{part_id}")
    assert isinstance(close, QPushButton)
    assert isinstance(plus, QPushButton)
    assert isinstance(minus, QPushButton)
    assert isinstance(field, QLineEdit)
    assert close.receivers("2clicked()") == 1
    assert plus.receivers("2clicked()") == 1
    assert minus.receivers("2clicked()") == 1
    assert field.receivers("2editingFinished()") == 1


def _assert_row_parent(dialog: CollectionDialog, part_id: int) -> None:
    row = dialog.findChild(QWidget, f"collection-row-{part_id}")
    listing = dialog.findChild(QWidget, "collection-list")
    assert isinstance(row, QWidget)
    assert isinstance(listing, QWidget)
    assert row.parent() is listing


def _assert_dialog_gone(page: PlannerPage, kind: type[QWidget]) -> None:
    if kind is CollectionDialog:
        assert page._collection_dialog is None
    lingering = [
        widget
        for widget in QApplication.allWidgets()
        if isinstance(widget, kind) and Shiboken.isValid(widget)
    ]
    assert lingering == []


def _part_id(page: PlannerPage, article: str) -> int:
    for record in page._planner.list_parts():
        if record.spec.article_number == article:
            return record.id
    raise AssertionError(article)
