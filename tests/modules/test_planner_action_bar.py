"""The part list keeps the column height. Actions sit on a wrapping bar below it."""

from __future__ import annotations

from PySide6.QtCore import QPoint, QRect
from PySide6.QtWidgets import QApplication, QScrollArea, QWidget
from pytestqt.qtbot import QtBot

from slot_racing.app.main_window import MainWindow
from slot_racing.modules.track_planner.ui.page import PlannerPage
from tests.modules.conftest import Env
from tests.modules.test_ui_management import open_page


def test_the_library_fills_the_column_and_actions_sit_below_the_planner(
    qtbot: QtBot, env: Env
) -> None:
    env.track("Leiste", lanes=2)
    window, page = open_page(qtbot, env, "track_planner")
    window.show()
    assert isinstance(page, PlannerPage)
    panel = page.findChild(QWidget, "planner-library-panel")
    library_scroll = page.findChild(QScrollArea, "planner-library-scroll")
    stage = page.findChild(QWidget, "planner-stage")
    properties = page.findChild(QScrollArea, "planner-properties-scroll")
    assert panel is not None and library_scroll is not None
    assert stage is not None and properties is not None

    for width, height in ((1600, 1000), (1280, 720), (1024, 768), (860, 600)):
        _activate(window, width, height)
        _assert_column_gives_the_list_the_leftover_height(page, panel, library_scroll)
        _assert_bar_spans_the_planner(page, library_scroll, stage, properties)
        _assert_buttons_are_fully_visible(page)

    _activate(window, 1600, 640)
    short_library = page.library.height()
    short_canvas = page.canvas.height()
    short_bar = page.action_bar.height()
    _activate(window, 1600, 980)
    assert page.library.height() - short_library > 200
    assert page.canvas.height() - short_canvas > 200
    assert abs(page.action_bar.height() - short_bar) <= 2
    assert page.library.height() > page.action_bar.height()

    slack = 40
    wide = page.action_bar.sizeHint().width() + window.width() - page.action_bar.width() + slack
    _activate(window, wide, 800)
    assert page.action_bar.width() + 1 >= page.action_bar.sizeHint().width()
    assert _rows(page) == 1

    narrow = page.action_bar.minimumSizeHint().width() + 24
    _activate(window, narrow, 700)
    assert page.action_bar.width() < page.action_bar.sizeHint().width()
    assert _rows(page) > 1
    _assert_buttons_are_fully_visible(page)

    _activate(window, 860, 600)
    wrapped = (_rows(page), page.library.height(), page.action_bar.height())
    _activate(window, wide, 900)
    opened = (_rows(page), page.library.height(), page.action_bar.height())
    _activate(window, 860, 600)
    restored = (_rows(page), page.library.height(), page.action_bar.height())
    assert opened[0] == 1
    assert opened[1] > wrapped[1] + 100
    assert opened[2] <= wrapped[2]
    assert restored[0] == wrapped[0]
    assert abs(restored[1] - wrapped[1]) <= 2
    assert abs(restored[2] - wrapped[2]) <= 2
    assert page.library.verticalScrollBar().maximum() > 0


def _assert_column_gives_the_list_the_leftover_height(
    page: PlannerPage, panel: QWidget, library_scroll: QScrollArea
) -> None:
    for button in page._action_buttons:
        assert not panel.isAncestorOf(button)
        assert page.action_bar.isAncestorOf(button)
    leftover = panel.height() - page.library.geometry().bottom()
    assert leftover < page.manage_stock.height()
    viewport = library_scroll.viewport().height()
    if viewport >= panel.minimumSizeHint().height():
        assert page.library.height() > page.action_bar.height()


def _assert_bar_spans_the_planner(
    page: PlannerPage, library_scroll: QWidget, stage: QWidget, properties: QWidget
) -> None:
    library_box = _on_page(page, library_scroll)
    stage_box = _on_page(page, stage)
    properties_box = _on_page(page, properties)
    bar_box = _on_page(page, page.action_bar)
    assert bar_box.top() + 1 >= library_box.bottom()
    assert bar_box.top() + 1 >= stage_box.bottom()
    assert bar_box.left() <= library_box.left() + 1
    assert bar_box.right() + 1 >= properties_box.right()
    assert bar_box.right() + 1 >= stage_box.right()


def _assert_buttons_are_fully_visible(page: PlannerPage) -> None:
    boxes: list[QRect] = []
    for button in page._action_buttons:
        if not button.isVisible():
            continue
        assert button.width() >= button.fontMetrics().horizontalAdvance(button.text())
        assert not button.text().endswith("…")
        rect = button.geometry()
        assert rect.right() <= page.action_bar.width() + 1
        assert rect.bottom() <= page.action_bar.height() + 1
        assert rect.left() >= -1
        assert rect.top() >= -1
        boxes.append(rect)
    for index, left in enumerate(boxes):
        for right in boxes[index + 1 :]:
            assert not left.intersects(right)


def _rows(page: PlannerPage) -> int:
    return len(
        {
            button.geometry().top()
            for button in page._action_buttons
            if button.isVisible() and button.width() > 0
        }
    )


def _on_page(page: QWidget, widget: QWidget) -> QRect:
    return QRect(widget.mapTo(page, QPoint(0, 0)), widget.size())


def _activate(window: MainWindow, width: int, height: int) -> None:
    window.resize(width, height)
    QApplication.processEvents()
    layout = window.layout()
    assert layout is not None
    layout.activate()
    QApplication.processEvents()
