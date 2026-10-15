"""Column widths of the live ranking and the ranking between heats."""

from __future__ import annotations

from dataclasses import replace

import pytest
from PySide6.QtCore import QEvent, QPoint
from PySide6.QtWidgets import QApplication, QSpinBox, QTableWidget
from pytestqt.qtbot import QtBot

from slot_racing.core.domain import RaceId, RaceStatus
from slot_racing.modules.races.hud import (
    RANKING_COLUMN_IDS,
    RANKING_DEFAULT_WEIGHTS,
    VIEW_BETWEEN,
    VIEW_LIVE,
    BetweenRanking,
    HudConfigurationStore,
    assign_column_share,
    coerce,
    default_hud_configuration,
    factory_ranking_widths,
    normalize_ranking_widths,
    ranking_column_pixels,
    ranking_width_shares,
    to_document,
    visible_ranking_columns,
)
from slot_racing.modules.races.hud_scene import HudColumn, HudViewport, build_hud_scene
from slot_racing.modules.races.runner import LiveRow
from slot_racing.modules.races.types import HeatBriefing
from slot_racing.modules.races.ui.between_ranking import ConfiguredRanking
from slot_racing.modules.races.ui.hud_editor import HudEditor, PlacementHandle
from slot_racing.modules.races.ui.hud_widgets import ranking_shown_text
from tests.database import migrated_database
from tests.modules.test_hud_editor import _mouse, _shown, _translator
from tests.modules.test_hud_scene import _presentation, _snapshot


def test_defaults_give_the_time_columns_a_usable_share() -> None:
    weights = dict(factory_ranking_widths())
    assert weights["best_lap"] > weights["position"]
    assert weights["total_time"] > weights["laps"]
    assert weights["last_lap"] > weights["status"]
    assert sum(weights.values()) == sum(RANKING_DEFAULT_WEIGHTS.values())


def test_an_older_layout_gains_the_default_widths() -> None:
    document = to_document(default_hud_configuration())
    layout = document["layouts"][0]
    assert isinstance(layout, dict)
    del layout["ranking_widths"]
    between = layout["between_ranking"]
    assert isinstance(between, dict)
    del between["widths"]
    loaded = coerce(document).default_layout()
    assert loaded.ranking_widths == factory_ranking_widths()
    assert loaded.between_ranking.widths == factory_ranking_widths()


def test_widths_follow_the_column_id_when_columns_move_or_hide() -> None:
    frame = replace(
        BetweenRanking(),
        widths=assign_column_share(
            factory_ranking_widths(),
            visible_ranking_columns(BetweenRanking()),
            "best_lap",
            30,
        ),
    )
    hidden = tuple((column_id, column_id != "status") for column_id, _shown in frame.columns)
    moved = list(hidden)
    best = next(index for index, (column_id, _shown) in enumerate(moved) if column_id == "best_lap")
    moved[best], moved[best - 1] = moved[best - 1], moved[best]
    changed = replace(frame, columns=tuple(moved))
    assert dict(changed.widths)["best_lap"] == dict(frame.widths)["best_lap"]
    assert dict(changed.widths)["status"] == dict(frame.widths)["status"]
    shown = visible_ranking_columns(changed)
    assert "status" not in shown
    pixels = dict(ranking_column_pixels(shown, changed.widths, 500))
    assert "status" not in pixels
    assert sum(pixels.values()) == 500
    assert pixels["best_lap"] > pixels["position"]


def test_the_same_shares_scale_with_the_table() -> None:
    columns = ("position", "driver", "best_lap", "total_time")
    narrow = dict(ranking_column_pixels(columns, factory_ranking_widths(), 200))
    wide = dict(ranking_column_pixels(columns, factory_ranking_widths(), 400))
    for column_id in columns:
        assert wide[column_id] == pytest.approx(narrow[column_id] * 2, abs=1)
    assert sum(narrow.values()) == 200
    assert sum(wide.values()) == 400
    shares = dict(ranking_width_shares(columns, factory_ranking_widths()))
    assert sum(shares.values()) == pytest.approx(1)
    assert min(shares.values()) >= 0.04


def test_live_and_between_widths_stay_independent() -> None:
    live = assign_column_share(
        factory_ranking_widths(), ("position", "driver", "laps", "best_lap"), "best_lap", 28
    )
    between = assign_column_share(
        factory_ranking_widths(),
        visible_ranking_columns(BetweenRanking()),
        "total_time",
        24,
    )
    assert dict(live)["best_lap"] != dict(between)["best_lap"]
    assert dict(live)["total_time"] == RANKING_DEFAULT_WEIGHTS["total_time"]
    assert dict(between)["total_time"] != RANKING_DEFAULT_WEIGHTS["total_time"]
    reset = normalize_ranking_widths(())
    assert reset == factory_ranking_widths()


def test_unknown_width_keys_are_dropped_and_missing_columns_use_the_default() -> None:
    loaded = normalize_ranking_widths(
        [{"id": "best_lap", "width": 400}, {"id": "nope", "width": 9}, ("driver", 50)]
    )
    assert dict(loaded)["best_lap"] == 400
    assert dict(loaded)["driver"] == 50
    assert dict(loaded)["total_time"] == RANKING_DEFAULT_WEIGHTS["total_time"]
    assert "nope" not in dict(loaded)
    assert [column_id for column_id, _weight in loaded] == list(RANKING_COLUMN_IDS)


def test_the_editor_spins_and_the_header_border_share_one_width(qtbot: QtBot) -> None:
    store = HudConfigurationStore(migrated_database())
    editor = _shown(qtbot, store)
    editor.select_view(VIEW_BETWEEN)
    qtbot.waitUntil(lambda: editor.between.ranking.table.columnCount() >= 4)
    QApplication.processEvents()
    table = editor.between.ranking.table
    before = editor.selected_layout().between_ranking
    origin = (before.x, before.y, before.width, before.height)
    handle = editor.findChild(PlacementHandle, "hud-ranking-handle")
    assert isinstance(handle, PlacementHandle)
    local, global_pos = _border(handle, table, 1)
    _mouse(handle, QEvent.Type.MouseButtonPress, local, global_pos)
    _mouse(
        handle,
        QEvent.Type.MouseMove,
        (local[0] + 48, local[1]),
        (global_pos[0] + 48, global_pos[1]),
    )
    _mouse(
        handle,
        QEvent.Type.MouseButtonRelease,
        (local[0] + 48, local[1]),
        (global_pos[0] + 48, global_pos[1]),
    )
    dragged = editor.selected_layout().between_ranking
    assert (dragged.x, dragged.y, dragged.width, dragged.height) == pytest.approx(origin)
    assert dragged.widths != before.widths
    shares = dict(ranking_width_shares(visible_ranking_columns(dragged), dragged.widths))
    spin = editor.findChild(QSpinBox, "hud-ranking-width-driver")
    assert isinstance(spin, QSpinBox)
    assert spin.value() == round(shares["driver"] * 100)
    spin.setValue(36 if spin.value() != 36 else 22)
    QApplication.processEvents()
    typed = editor.selected_layout().between_ranking
    typed_shares = dict(ranking_width_shares(visible_ranking_columns(typed), typed.widths))
    assert round(typed_shares["driver"] * 100) == spin.value()
    assert (typed.x, typed.y) == pytest.approx((dragged.x, dragged.y))

    editor.reset_between_column_widths()
    assert editor.selected_layout().between_ranking.widths == factory_ranking_widths()
    assert editor.selected_layout().ranking_widths == factory_ranking_widths()

    editor.select_view(VIEW_LIVE)
    qtbot.waitUntil(lambda: editor.stage.ranking.width() > 40)
    live_before = editor.selected_layout().ranking_widths
    live_spin = editor.findChild(QSpinBox, "hud-live-width-driver")
    assert isinstance(live_spin, QSpinBox)
    assert live_spin.isVisible()
    live_spin.setValue(40 if live_spin.value() != 40 else 18)
    QApplication.processEvents()
    assert editor.selected_layout().ranking_widths != live_before
    assert editor.selected_layout().between_ranking.widths == factory_ranking_widths()
    editor.reset_live_column_widths()
    assert editor.selected_layout().ranking_widths == factory_ranking_widths()
    assert editor.selected_layout().between_ranking.widths == factory_ranking_widths()


def test_saved_widths_reach_the_preview_the_race_table_and_the_scene(qtbot: QtBot) -> None:
    store = HudConfigurationStore(migrated_database())
    editor = _shown(qtbot, store)
    editor.select_view(VIEW_BETWEEN)
    qtbot.waitUntil(lambda: editor.between.ranking.width() > 80)
    editor.set_between_column_width("total_time", 26)
    editor.set_between_column_width("best_lap", 22)
    editor.select_view(VIEW_LIVE)
    qtbot.waitUntil(lambda: not editor.stage.ranking.table.isColumnHidden(1))
    editor.set_live_column_width("driver", 42)
    editor.save()

    reloaded = HudEditor(_translator(), store)
    qtbot.addWidget(reloaded)
    reloaded.resize(1400, 860)
    reloaded.show()
    reloaded.select_view(VIEW_BETWEEN)
    qtbot.waitUntil(lambda: reloaded.between.ranking.table.viewport().width() > 80)
    QApplication.processEvents()
    kept = reloaded.selected_layout()
    assert (
        dict(kept.between_ranking.widths)["total_time"]
        == dict(editor.selected_layout().between_ranking.widths)["total_time"]
    )
    assert (
        dict(kept.ranking_widths)["driver"]
        == dict(editor.selected_layout().ranking_widths)["driver"]
    )
    _assert_table_follows(
        reloaded.between.ranking.table,
        visible_ranking_columns(kept.between_ranking),
        kept.between_ranking.widths,
    )

    ranking = ConfiguredRanking(_translator())
    qtbot.addWidget(ranking)
    ranking.resize(640, 280)
    ranking.show()
    ranking.show_standings(_snapshot(RaceStatus.RUNNING), kept.between_ranking)
    QApplication.processEvents()
    column_ids = visible_ranking_columns(kept.between_ranking)
    _assert_table_follows(ranking.table, column_ids, kept.between_ranking.widths)
    ranking.resize(320, 200)
    QApplication.processEvents()
    _assert_table_follows(ranking.table, column_ids, kept.between_ranking.widths)

    between_scene = build_hud_scene(
        kept,
        _snapshot(RaceStatus.RUNNING),
        HudViewport(1280, 720),
        _presentation(view=VIEW_BETWEEN, briefing=HeatBriefing(RaceId(1), 2, 5, (), (), ())),
    )
    _assert_scene_shares(
        between_scene.ranking.columns,
        visible_ranking_columns(kept.between_ranking),
        kept.between_ranking.widths,
    )
    live_scene = build_hud_scene(
        kept,
        _snapshot(RaceStatus.RUNNING),
        HudViewport(1600, 900),
        _presentation(view=VIEW_LIVE),
    )
    visible = tuple(column.id for column in live_scene.ranking.columns if column.visible)
    _assert_scene_shares(live_scene.ranking.columns, visible, kept.ranking_widths)
    assert live_scene.ranking.fit_columns is False
    assert between_scene.ranking.fit_columns is False


def test_a_narrow_between_ranking_keeps_shares_and_tips_the_full_name(qtbot: QtBot) -> None:
    name = "Maximilian Mustermann"
    frame = replace(
        BetweenRanking(),
        widths=assign_column_share(
            factory_ranking_widths(),
            visible_ranking_columns(BetweenRanking()),
            "driver",
            12,
        ),
    )
    ranking = ConfiguredRanking(_translator())
    qtbot.addWidget(ranking)
    ranking.resize(280, 160)
    ranking.show()
    ranking.show_standings(
        _snapshot(
            RaceStatus.RUNNING,
            rows=(LiveRow(1, 1, name, "Porsche", 7, 1, 3, None, None, None, False, ()),),
        ),
        frame,
    )
    QApplication.processEvents()
    column_ids = visible_ranking_columns(frame)
    _assert_table_follows(ranking.table, column_ids, frame.widths)
    index = column_ids.index("driver")
    item = ranking.table.item(0, index)
    assert item is not None and item.text() == name
    assert ranking_shown_text(name, ranking.table.font(), ranking.table.columnWidth(index)) != name
    assert item.toolTip() == name


def test_dragging_the_ranking_body_still_moves_the_element(qtbot: QtBot) -> None:
    editor = _shown(qtbot)
    editor.select_view(VIEW_BETWEEN)
    qtbot.waitUntil(lambda: editor.between.surface.width() > 200)
    handle = editor.findChild(PlacementHandle, "hud-ranking-handle")
    assert isinstance(handle, PlacementHandle)
    before = editor.selected_layout().between_ranking
    _mouse(handle, QEvent.Type.MouseButtonPress, (12, 12), (80, 80))
    _mouse(handle, QEvent.Type.MouseMove, (12, 12), (160, 130))
    _mouse(handle, QEvent.Type.MouseButtonRelease, (12, 12), (160, 130))
    moved = editor.selected_layout().between_ranking
    assert moved.x != pytest.approx(before.x)
    assert moved.widths == before.widths


def _border(
    handle: PlacementHandle, table: QTableWidget, column: int
) -> tuple[tuple[int, int], tuple[int, int]]:
    header = table.horizontalHeader()
    right = header.sectionViewportPosition(column) + header.sectionSize(column)
    global_point = header.mapToGlobal(QPoint(right, header.height() // 2))
    local_point = handle.mapFromGlobal(global_point)
    return (local_point.x(), local_point.y()), (global_point.x(), global_point.y())


def _assert_table_follows(
    table: QTableWidget,
    column_ids: tuple[str, ...],
    widths: tuple[tuple[str, int], ...],
) -> None:
    viewport = table.viewport().width()
    assert viewport > 1
    expected = dict(ranking_column_pixels(column_ids, widths, viewport))
    total = 0
    for index, column_id in enumerate(column_ids):
        assert table.columnWidth(index) == pytest.approx(expected[column_id], abs=2)
        total += table.columnWidth(index)
    assert total == pytest.approx(viewport, abs=4)
    assert table.horizontalScrollBar().maximum() == 0


def _assert_scene_shares(
    columns: tuple[HudColumn, ...],
    visible_ids: tuple[str, ...],
    widths: tuple[tuple[str, int], ...],
) -> None:
    shares = dict(ranking_width_shares(visible_ids, widths))
    seen = {column.id: column.share for column in columns if column.visible}
    assert set(seen) == set(visible_ids)
    for column_id, share in shares.items():
        assert seen[column_id] == pytest.approx(share)
    assert all(column.share == 0 for column in columns if not column.visible)
