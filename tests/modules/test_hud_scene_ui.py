"""The Qt live HUD and the scene agree on the texts and the nominal type sizes."""

from __future__ import annotations

from PySide6.QtWidgets import QApplication, QLabel
from pytestqt.qtbot import QtBot

from slot_racing.core.domain import RaceId, RaceMode, RaceStatus
from slot_racing.modules.races.hud import (
    CLOCK_MIN_HEIGHT,
    CLOCK_MIN_WIDTH,
    LANES_MIN_HEIGHT,
    RANKING_MIN_HEIGHT,
    RANKING_MIN_WIDTH,
    factory_layout,
    light_gantry,
)
from slot_racing.modules.races.hud_scene import (
    BRIEFING_CAPTION_BASE,
    BRIEFING_CHANGE_BASE,
    BRIEFING_DRIVER_BASE,
    BRIEFING_HINT_BASE,
    BRIEFING_TITLE_BASE,
    CARD_TITLE_PX,
    COLOR_ROLES,
    FIT_FONT_MAX,
    FIT_FONT_MIN,
    FIT_PLACEHOLDER_MAX,
    LANE_CARD_MARGIN,
    MESSAGE_CAPTION_MIN_HEIGHT,
    RECORD_BADGE_PX,
    RECORD_BODY_PX,
    RECORD_HEADER_PX,
    RECORD_LANE_PX,
    RECORD_ROW_PX,
    RECORD_SECTION_PX,
    STATUS_CAPTION_MIN_HEIGHT,
    HudPresentation,
    HudViewport,
    build_hud_scene,
)
from slot_racing.modules.races.types import HeatBriefing, HeatSeat
from slot_racing.modules.races.ui.hud_widgets import (
    _MAX_FONT,
    _MESSAGE_CAPTION_HEIGHT,
    _MIN_FONT,
    _PLACEHOLDER_MAX_FONT,
    _STATUS_CAPTION_HEIGHT,
)
from slot_racing.modules.races.ui.lane_card import LaneCard
from slot_racing.modules.races.ui.live_stage import LiveHudStage
from slot_racing.modules.races.ui.live_view import LiveRaceView
from slot_racing.modules.races.ui.race_briefing import apply_text_size
from slot_racing.modules.races.ui.races_page import RacesPage
from slot_racing.modules.races.ui.start_lights import StartLightWidget
from slot_racing.modules.races.ui.time_trial_board_view import (
    _BADGE_PX,
    _BODY_PX,
    _HEADER_PX,
    _LANE_PX,
    _ROW_PX,
)
from slot_racing.uikit.theme import (
    COLORS,
    FONT_CAPTION,
    FONT_HEAT,
    FONT_HINT,
    FONT_LANE,
    FONT_VALUE,
    SPACE,
    stylesheet,
)
from tests.modules.conftest import Env
from tests.modules.test_hud_editor import _translator
from tests.modules.test_hud_scene import _presentation, _snapshot
from tests.modules.test_live_race_ui import _ready_race
from tests.modules.test_ui_management import open_page
from tests.support.start_sequence import release_start_lights


def test_scene_tokens_match_the_widgets_and_the_theme() -> None:
    assert (FIT_FONT_MIN, FIT_FONT_MAX, FIT_PLACEHOLDER_MAX) == (
        _MIN_FONT,
        _MAX_FONT,
        _PLACEHOLDER_MAX_FONT,
    )
    assert STATUS_CAPTION_MIN_HEIGHT == _STATUS_CAPTION_HEIGHT
    assert MESSAGE_CAPTION_MIN_HEIGHT == _MESSAGE_CAPTION_HEIGHT
    assert SPACE.sm == LANE_CARD_MARGIN
    assert (BRIEFING_CAPTION_BASE, BRIEFING_TITLE_BASE, BRIEFING_DRIVER_BASE) == (
        FONT_CAPTION,
        FONT_HEAT,
        FONT_LANE,
    )
    assert (BRIEFING_HINT_BASE, BRIEFING_CHANGE_BASE) == (FONT_HINT, FONT_VALUE)
    assert (RECORD_BADGE_PX, RECORD_LANE_PX, RECORD_BODY_PX, RECORD_ROW_PX, RECORD_HEADER_PX) == (
        _BADGE_PX,
        _LANE_PX,
        _BODY_PX,
        _ROW_PX,
        _HEADER_PX,
    )
    block = stylesheet().split('QLabel[role="card-title"]', 1)[1].split("}", 1)[0]
    assert f"font-size: {CARD_TITLE_PX}px;" in block
    section = stylesheet().split('QLabel[role="section"]', 1)[1].split("}", 1)[0]
    assert f"font-size: {RECORD_SECTION_PX}px;" in section
    for name, value in COLOR_ROLES:
        assert getattr(COLORS, name) == value


def test_a_tall_lane_card_uses_the_nominal_scene_size(qtbot: QtBot) -> None:
    translator = _translator()
    card = LaneCard(translator, 1)
    qtbot.addWidget(card)
    card.resize(480, 1400)
    card.show()
    snapshot = _snapshot(RaceStatus.RUNNING)
    card.show_driver(
        snapshot.rows[1],
        laps=snapshot.laps,
        mode=RaceMode.LAPS,
        paused=False,
        ended=False,
        elapsed_ns=snapshot.elapsed_ns,
    )
    QApplication.processEvents()
    assert card._fit_factor == 1.0
    scene = build_hud_scene(
        factory_layout(),
        snapshot,
        HudViewport(1920, 1080),
        _presentation(),
    )
    fields = {item.id: item for item in scene.lanes[0].fields}
    labels = {
        "lane": card.lane_label,
        "position": card.position_label,
        "driver": card.driver_label,
        "vehicle": card.vehicle_label,
        "start": card.start_label,
        "lap": card.lap_label,
        "remaining_laps": card.remaining_laps_label,
        "last": card.last_label,
        "best": card.best_label,
        "best_time": card.best_time_label,
        "total": card.total_label,
        "status": card.status_label,
    }
    for field_id, label in labels.items():
        assert label.text() == fields[field_id].text
        assert label.isVisibleTo(card) is fields[field_id].visible
        if fields[field_id].visible:
            assert label.font().pixelSize() == fields[field_id].font_px


def test_the_stage_shows_the_same_standings_as_the_scene(qtbot: QtBot) -> None:
    translator = _translator()
    stage = LiveHudStage(translator)
    qtbot.addWidget(stage)
    stage.resize(1600, 1200)
    stage.show()
    QApplication.processEvents()
    snapshot = _snapshot(RaceStatus.RUNNING)
    stage.show_standings(snapshot, mode=RaceMode.LAPS, lane_count=2)
    QApplication.processEvents()
    scene = build_hud_scene(
        stage.layout_spec(),
        snapshot,
        HudViewport(stage.width(), stage.height()),
        HudPresentation(
            translator=translator,
            mode=RaceMode.LAPS,
            lane_count=2,
            can_pause=True,
        ),
    )
    assert stage.time_label.text() == scene.clock.value.text
    assert stage.status_label.text() == scene.status_line.value.text
    assert stage.status_label.property("tone") == scene.status_line.tone
    assert stage._top.stretch(0) == scene.panel("clock").share
    assert stage._top.stretch(1) == scene.panel("status").share
    assert stage._middle.stretch(0) == scene.panel("lanes").share
    assert stage._middle.stretch(1) == scene.panel("ranking").share
    assert stage.clock.minimumWidth() == CLOCK_MIN_WIDTH
    assert stage.clock.minimumHeight() == CLOCK_MIN_HEIGHT
    assert stage.lanes.minimumHeight() == LANES_MIN_HEIGHT
    assert stage.ranking.minimumWidth() == RANKING_MIN_WIDTH
    assert stage.ranking.minimumHeight() == RANKING_MIN_HEIGHT
    assert scene.ranking.row_px == 32
    assert "ranking-row-fit" in scene.measurements
    for lane, card in stage.lanes.cards.items():
        pictured = scene.lanes[lane - 1]
        for field_id, label in (
            ("driver", card.driver_label),
            ("vehicle", card.vehicle_label),
            ("lap", card.lap_label),
            ("last", card.last_label),
            ("best", card.best_label),
            ("best_time", card.best_time_label),
            ("total", card.total_label),
            ("status", card.status_label),
        ):
            field = next(item for item in pictured.fields if item.id == field_id)
            assert label.text() == field.text
    headers = []
    for column in range(stage.table.columnCount()):
        header = stage.table.horizontalHeaderItem(column)
        assert header is not None
        headers.append(header.text())
    assert headers == [column.header for column in scene.ranking.columns]
    for row in range(stage.table.rowCount()):
        for column in range(stage.table.columnCount()):
            if stage.table.isColumnHidden(column):
                continue
            item = stage.table.item(row, column)
            assert item is not None
            assert item.text() == scene.ranking.rows[row].cells[column]
    assert len(scene.ranking.columns) == stage.table.columnCount()


def test_briefing_type_and_the_gantry_follow_the_shared_geometry(qtbot: QtBot) -> None:
    label = QLabel("Durchlauf 2")
    qtbot.addWidget(label)
    apply_text_size(label, FONT_HEAT, 960, bold=True, scale=100)
    scene = build_hud_scene(
        factory_layout(),
        _snapshot(RaceStatus.FINISHED),
        HudViewport(960, 700),
        _presentation(view="between", briefing=_briefing()),
    )
    assert scene.briefing is not None
    assert label.font().pixelSize() == scene.briefing.title_px

    lights = StartLightWidget()
    qtbot.addWidget(lights)
    lights.resize(800, lights.heightForWidth(800))
    lights.show_lights(2)
    placed = light_gantry(lights.width(), lights.height())
    for rect, face in zip(lights.lamp_rects(), placed.lamps, strict=True):
        assert abs(rect.x() - face[0]) < 0.01
        assert abs(rect.y() - face[1]) < 0.01
        assert abs(rect.width() - face[2]) < 0.01
        assert abs(rect.height() - face[3]) < 0.01


def test_the_live_view_matches_the_scene_after_the_start(qtbot: QtBot, env: Env) -> None:
    _ready_race(env, laps=4)
    _window, page = open_page(qtbot, env, "races")
    assert isinstance(page, RacesPage)
    page.refresh()
    page.table.selectRow(0)
    page.buttons["start"].click()
    live = page.current_view()
    assert isinstance(live, LiveRaceView)
    release_start_lights(live)
    live.resize(1600, 1100)
    QApplication.processEvents()
    runner = live.runner
    assert runner is not None
    snapshot = runner.snapshot()
    scene = build_hud_scene(
        live.bound_layout(),
        snapshot,
        HudViewport(live.stage.width(), live.stage.height()),
        HudPresentation(
            translator=env.runtime.translator,
            mode=runner.race.mode,
            lane_count=runner.race.lane_count,
            duration_minutes=runner.race.duration_minutes,
            can_pause=live.pause_button.isEnabled(),
            can_resume=live.resume_button.isEnabled(),
            can_stop=live.stop_button.isEnabled(),
            can_results=live.results_button.isEnabled(),
        ),
    )
    assert live.time_label.text() == scene.clock.value.text
    assert live.status_label.text() == scene.status_line.value.text
    assert live.messages.message_label.text() == scene.message.text
    assert live.pause_button.text() == scene.controls[0].label
    assert live.pause_button.isEnabled() is scene.controls[0].enabled
    assert live.resume_button.isEnabled() is scene.controls[1].enabled
    assert live.stop_button.isEnabled() is scene.controls[2].enabled
    assert live.results_button.isEnabled() is scene.controls[3].enabled
    assert live.back_button.isEnabled() is scene.controls[4].enabled
    for lane, card in live.lanes.cards.items():
        pictured = next(item for item in scene.lanes if item.lane == lane)
        assert card.driver_label.text() == pictured.driver
        assert card.vehicle_label.text() == next(
            field.text for field in pictured.fields if field.id == "vehicle"
        )
        assert card.lap_label.text() == next(
            field.text for field in pictured.fields if field.id == "lap"
        )
    before = live.time_label.text()
    build_hud_scene(
        live.bound_layout(),
        snapshot,
        HudViewport(live.stage.width(), live.stage.height()),
        HudPresentation(
            translator=env.runtime.translator,
            mode=runner.race.mode,
            lane_count=runner.race.lane_count,
        ),
    )
    assert live.time_label.text() == before
    assert runner.status is RaceStatus.RUNNING


def _briefing() -> HeatBriefing:
    return HeatBriefing(
        race_id=RaceId(1),
        sequence=2,
        heat_count=2,
        seats=(HeatSeat(1, 1, "Zoe"),),
        changes=(),
        drivers=((1, "Zoe"),),
    )
