"""The part list keeps the column height. Actions sit on a wrapping bar below it."""

from __future__ import annotations

from PySide6.QtCore import QPoint, QRect
from PySide6.QtGui import QFont
from PySide6.QtWidgets import QApplication, QPushButton, QScrollArea, QWidget
from pytestqt.qtbot import QtBot

from slot_racing.app.main_window import MainWindow
from slot_racing.modules.track_planner.ui.page import PlannerPage
from tests.modules.conftest import Env
from tests.modules.test_ui_management import open_page

_SIZES = ((1600, 1000), (1280, 720), (1024, 768), (860, 600), (720, 560))


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
    body = page.findChild(QWidget, "planner-body")
    assert panel is not None and library_scroll is not None
    assert stage is not None and properties is not None and body is not None

    for width, height in _SIZES:
        _activate(window, width, height)
        _assert_column_gives_the_list_the_leftover_height(page, panel, library_scroll)
        _assert_bar_spans_the_planner(page, library_scroll, stage, properties, body)
        _assert_buttons_are_fully_visible(page)
        _assert_toolbar_buttons_are_inside(page)
        _assert_flow_heights_match_their_width(page)
        _assert_wraps_exactly_when_the_row_is_narrower_than_one_line(page)

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
    _assert_buttons_are_fully_visible(page)
    _assert_toolbar_buttons_are_inside(page)

    # The bar is as wide as the planner row, not the window. The row keeps the
    # column minimum and scrolls sideways, so a one-button window does not make
    # the bar one button wide. Wrapping follows that row width.
    narrow = page.action_bar.minimumSizeHint().width() + 24
    _activate(window, narrow, 700)
    _assert_bar_spans_the_planner(page, library_scroll, stage, properties, body)
    _assert_buttons_are_fully_visible(page)
    _assert_wraps_exactly_when_the_row_is_narrower_than_one_line(page)

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

    for width, height in ((1600, 1000), (720, 560), (1600, 1000), (860, 600)):
        _activate(window, width, height)
        _assert_bar_spans_the_planner(page, library_scroll, stage, properties, body)
        _assert_buttons_are_fully_visible(page)
        _assert_toolbar_buttons_are_inside(page)
        _assert_flow_heights_match_their_width(page)
        _assert_wraps_exactly_when_the_row_is_narrower_than_one_line(page)
        _assert_column_gives_the_list_the_leftover_height(page, panel, library_scroll)
        _assert_stage_keeps_the_leftover(page, stage, above_floor=height >= 600)
        assert properties.width() > 0

    _assert_wrapped_height_is_released(window, page, stage)


def test_the_bar_covers_the_row_when_the_columns_are_wider_than_the_window(
    qtbot: QtBot, env: Env
) -> None:
    """Windows fonts make the property column wider than the page.

    The row then scrolls sideways. The action bar has to scroll with it,
    and a larger font has to wrap the toolbar inside its own height.
    """
    env.track("Leiste", lanes=2)
    window, page = open_page(qtbot, env, "track_planner")
    window.show()
    assert isinstance(page, PlannerPage)
    properties = page.findChild(QScrollArea, "planner-properties-scroll")
    panel = page.findChild(QWidget, "planner-library-panel")
    library_scroll = page.findChild(QScrollArea, "planner-library-scroll")
    stage = page.findChild(QWidget, "planner-stage")
    body = page.findChild(QWidget, "planner-body")
    assert properties is not None and panel is not None
    assert library_scroll is not None and stage is not None and body is not None
    properties.setMinimumWidth(max(properties.minimumWidth(), 520))
    font = QFont(page.font())
    font.setPointSize(font.pointSize() + 6)
    page.setFont(font)
    page._fit_toolbar()

    for width, height in _SIZES:
        _activate(window, width, height)
        _assert_bar_spans_the_planner(page, library_scroll, stage, properties, body)
        _assert_buttons_are_fully_visible(page)
        _assert_toolbar_buttons_are_inside(page)
        _assert_flow_heights_match_their_width(page)
        assert body.width() + 1 >= properties.width()
        assert page.library.height() > 40
        _assert_stage_keeps_the_leftover(page, stage, above_floor=height >= 600)


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
    page: PlannerPage,
    library_scroll: QWidget,
    stage: QWidget,
    properties: QWidget,
    body: QWidget,
) -> None:
    library_box = _on_page(page, library_scroll)
    stage_box = _on_page(page, stage)
    properties_box = _on_page(page, properties)
    bar_box = _on_page(page, page.action_bar)
    body_box = _on_page(page, body)
    assert abs(bar_box.left() - body_box.left()) <= 1
    assert abs(bar_box.right() - body_box.right()) <= 1
    assert bar_box.top() + 1 >= library_box.bottom()
    assert bar_box.top() + 1 >= stage_box.bottom()
    assert bar_box.left() <= library_box.left() + 1
    assert bar_box.right() + 1 >= properties_box.right()
    assert bar_box.right() + 1 >= stage_box.right()


def _assert_buttons_are_fully_visible(page: PlannerPage) -> None:
    _assert_contained(page.action_bar, page._action_buttons)


def _assert_toolbar_buttons_are_inside(page: PlannerPage) -> None:
    _assert_contained(page.toolbar, page._toolbar_widgets)


def _assert_contained(host: QWidget, widgets: tuple[QWidget, ...]) -> None:
    boxes: list[QRect] = []
    for widget in widgets:
        if not widget.isVisible():
            continue
        if isinstance(widget, QPushButton):
            assert widget.width() >= widget.fontMetrics().horizontalAdvance(widget.text())
            assert not widget.text().endswith("…")
        rect = widget.geometry()
        assert rect.left() >= -1
        assert rect.top() >= -1
        assert rect.right() <= host.width() + 1
        assert rect.bottom() <= host.height() + 1
        boxes.append(rect)
    for index, left in enumerate(boxes):
        for right in boxes[index + 1 :]:
            assert not left.intersects(right)


def _assert_stage_keeps_the_leftover(
    page: PlannerPage, stage: QWidget, *, above_floor: bool
) -> None:
    """48 px is the stage's defined floor, not a stuck layout.

    The canvas publishes no minimum of its own. On a short window the wrapped
    toolbar can leave the column exactly that floor, and the stage may sit on
    it. A normal window has to hand the leftover back, above the floor.
    """
    floor = stage.minimumSizeHint().height()
    assert stage.height() >= floor
    if above_floor:
        assert stage.height() > floor
    assert page.canvas.height() == stage.height()
    assert page.canvas.geometry().top() >= 0
    assert page.canvas.geometry().bottom() <= stage.height()


def _assert_wrapped_height_is_released(
    window: MainWindow, page: PlannerPage, stage: QWidget
) -> None:
    """720 x 560, then 1600 x 1000, then 860 x 600 must not keep the narrow wrap."""
    _activate(window, 720, 560)
    narrow_toolbar = page.toolbar.height()
    narrow_action = page.action_bar.height()
    narrow_stage = stage.height()
    _assert_flow_heights_match_their_width(page)
    assert narrow_stage >= stage.minimumSizeHint().height()

    _activate(window, 1600, 1000)
    assert page.toolbar.height() < narrow_toolbar
    assert page.action_bar.height() <= narrow_action
    assert page.toolbar.minimumHeight() < narrow_toolbar
    assert stage.height() > narrow_stage
    _assert_flow_heights_match_their_width(page)

    _activate(window, 860, 600)
    assert page.toolbar.height() < narrow_toolbar
    assert page.action_bar.height() <= narrow_action
    assert stage.height() > stage.minimumSizeHint().height()
    assert stage.height() > narrow_stage
    _assert_flow_heights_match_their_width(page)
    _assert_buttons_are_fully_visible(page)
    _assert_toolbar_buttons_are_inside(page)


def _assert_flow_heights_match_their_width(page: PlannerPage) -> None:
    for host in (page.toolbar, page.action_bar):
        needed = host.heightForWidth(host.width())
        assert host.minimumHeight() == needed
        assert abs(host.height() - needed) <= 1


def _assert_wraps_exactly_when_the_row_is_narrower_than_one_line(page: PlannerPage) -> None:
    if page.action_bar.width() + 1 < page.action_bar.sizeHint().width():
        assert _rows(page) > 1
    else:
        assert _rows(page) == 1


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
    layout.activate()
    QApplication.processEvents()
