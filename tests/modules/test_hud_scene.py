"""The HUD scene is a picture of the live view. It does not run the race."""

from __future__ import annotations

import json
import time
from dataclasses import replace

import pytest
from sqlalchemy import func, select

from slot_racing.core.clock import format_duration
from slot_racing.core.domain import RaceId, RaceMode, RaceStatus
from slot_racing.core.events import Event
from slot_racing.modules.races.hud import (
    DISPLAY_ALWAYS,
    DISPLAY_HIDE,
    FIELD_DRIVER,
    FIELD_REMAINING_LAPS,
    FIELD_REMAINING_TIME,
    FIELD_TOTAL,
    LANE_FIT_FLOOR,
    LIGHT_ASPECT,
    PANEL_RECORDS,
    VIEW_BETWEEN,
    VIEW_LIVE,
    VIEW_RESULTS,
    FieldStyle,
    HudLayout,
    ViewStyle,
    effective_px,
    factory_layout,
    light_gantry,
    light_pixels,
    with_view,
)
from slot_racing.modules.races.hud_scene import (
    BRIEFING_TITLE_BASE,
    COLOR_ROLES,
    FIT_FONT_MAX,
    FIT_FONT_MIN,
    HOST_MEASUREMENTS,
    LANE_CARD_MARGIN,
    HudCue,
    HudPresentation,
    HudScene,
    HudViewport,
    build_hud_scene,
    scene_payload,
)
from slot_racing.modules.races.models import Lap, Race
from slot_racing.modules.races.runner import LiveRow, RaceSnapshot
from slot_racing.modules.races.time_trial_board import (
    ActiveLaneLine,
    DriverAttempt,
    LaneRecordLine,
    RecentLapLine,
    TimeTrialBoard,
)
from slot_racing.modules.races.types import HeatBriefing, HeatSeat, LaneChange
from slot_racing.modules.races.ui.formatting import best_lap_display, remaining_laps, remaining_time
from tests.modules.conftest import Env
from tests.modules.test_hud_editor import _translator

_LAP = 5_123_000_000
_BEST = 3_026_000_000


def test_a_running_lap_race_uses_the_same_texts_as_the_widgets() -> None:
    scene = _scene(_snapshot(RaceStatus.RUNNING), lane_count=2)
    assert scene.phase == "running"
    assert scene.mode == "laps"
    assert scene.clock.value.text == format_duration(90_000_000_000)
    assert scene.status_line.value.text == "● Läuft"
    assert scene.status_line.tone == "ok"
    assert scene.status_line.panel_tone == "ok"
    assert scene.message.text == "Rennen gestartet"
    assert scene.lane_columns == 2
    assert [card.lane for card in scene.lanes] == [1, 2]
    assert scene.lanes[0].driver == "Zoe"
    assert scene.lanes[0].vehicle == "Porsche"
    assert scene.lanes[0].position == 1
    assert scene.lanes[1].driver == "Anna"
    solo = _scene(
        _snapshot(RaceStatus.RUNNING, rows=(snapshot_rows()[1],)),
        lane_count=2,
    )
    assert solo.lanes[0].driver == "Zoe"
    assert solo.lanes[1].driver == "frei"
    assert solo.lanes[1].occupied is False
    zoe = {item.id: item for item in scene.lanes[0].fields}
    assert zoe["position"].text == "P1"
    assert zoe["lap"].text == "2 / 8"
    assert zoe["remaining_laps"].text == "6"
    assert zoe["remaining_laps"].visible is True
    assert zoe["remaining_time"].visible is False
    assert zoe["total"].visible is True
    assert zoe["last"].text == format_duration(_LAP)
    assert zoe["best"].text == "1"
    assert zoe["best_time"].text == "3,026 s"
    assert zoe["start"].text == "7"
    assert zoe["status"].text == "Fährt"
    assert zoe["driver"].font_px == effective_px(36, 100, 100)
    assert zoe["driver"].caption == ""
    assert zoe["last"].caption == "Letzte Runde"
    assert zoe["last"].caption_px <= zoe["last"].font_px
    assert scene.ranking.rows[0].cells[1] == "Anna"
    assert scene.ranking.rows[0].leader is False
    assert scene.ranking.rows[1].cells[1] == "Zoe"
    assert scene.ranking.rows[1].leader is True
    assert [row.position for row in scene.ranking.rows] == [2, 1]
    assert scene.controls[0].id == "pause" and scene.controls[0].enabled is True
    assert scene.controls[1].enabled is False
    assert all(control.host_only for control in scene.controls)
    assert scene.records is None
    assert scene.briefing is None
    assert scene.readable is True
    assert scene.scrolls is False


def test_field_visibility_follows_the_saved_style_and_the_race_mode() -> None:
    hidden = _with_field(FIELD_DRIVER, FieldStyle(display=DISPLAY_HIDE))
    scene = _scene(_snapshot(RaceStatus.RUNNING), layout=hidden)
    driver = next(item for item in scene.lanes[0].fields if item.id == FIELD_DRIVER)
    assert driver.visible is False
    assert driver.text == "Zoe"

    trial = _scene(
        _snapshot(RaceStatus.RUNNING, laps=0),
        mode=RaceMode.TIME_TRIAL,
        duration_minutes=5,
        lane_count=1,
    )
    fields = {item.id: item for item in trial.lanes[0].fields}
    assert fields[FIELD_TOTAL].visible is False
    assert fields[FIELD_REMAINING_LAPS].visible is False
    assert fields[FIELD_REMAINING_TIME].visible is True
    assert fields[FIELD_REMAINING_TIME].text == remaining_time(90_000_000_000, 5)
    forced = _scene(
        _snapshot(RaceStatus.RUNNING, laps=0),
        mode=RaceMode.TIME_TRIAL,
        layout=_with_field(FIELD_REMAINING_LAPS, FieldStyle(display=DISPLAY_ALWAYS)),
        lane_count=1,
    )
    # A time trial has no lap target, so the value does not exist and stays hidden.
    still = next(item for item in forced.lanes[0].fields if item.id == FIELD_REMAINING_LAPS)
    assert still.visible is False
    assert remaining_laps(2, 0) is None


def test_time_trial_records_and_an_empty_snapshot_stay_display_only() -> None:
    board = TimeTrialBoard(
        records=(LaneRecordLine(1, "Zoe", "Porsche", _BEST), LaneRecordLine(2, None, None, None)),
        active=(
            ActiveLaneLine(1, "Zoe", "Porsche", True),
            ActiveLaneLine(2, None, None, False),
        ),
        attempts=(
            DriverAttempt(
                1,
                "Zoe",
                "Porsche",
                (RecentLapLine(-1, _LAP),),
                _BEST,
                _BEST,
            ),
        ),
    )
    scene = _scene(
        _snapshot(RaceStatus.PAUSED, laps=0),
        mode=RaceMode.TIME_TRIAL,
        duration_minutes=10,
        records=board,
        can_pause=False,
        can_resume=True,
    )
    assert scene.phase == "paused"
    assert scene.status_line.tone == "warn"
    assert scene.message.text == "Rennen pausiert"
    assert scene.records is not None
    assert scene.records.name == "Finale"
    assert scene.records.records_rows[0] == ("1", "Zoe", "Porsche", "3,026 s")
    assert scene.records.records_rows[1] == ("2", "-", "-", "-")
    assert scene.records.active_rows[1] == ("2", "frei", "-")
    assert scene.records.active_muted_rows == (1,)
    assert scene.records.attempts[0].rows == (("-1", "5,123 s"),)
    assert "3,026 s" in scene.records.attempts[0].session_best
    assert scene.panel("records").share == 1
    assert scene.controls[1].enabled is True

    empty = _scene(_snapshot(RaceStatus.CREATED, rows=(), laps=0, elapsed=0), lane_count=0)
    assert empty.phase == "prepared"
    assert empty.lanes == ()
    assert empty.ranking.rows == ()
    assert empty.message.text == "—"
    assert empty.clock.value.text == "0:00.000"
    assert empty.records is None


def test_start_lights_use_the_existing_gantry_and_do_not_invent_a_step() -> None:
    cue = HudCue(active=True, lit_lights=3, go=False)
    scene = _scene(_snapshot(RaceStatus.CREATED, elapsed=0), cue=cue, lane_count=2)
    assert scene.phase == "start"
    assert scene.lights.visible is True
    assert scene.lights.lit_lights == 3
    rect = light_pixels(factory_layout().lights, scene.viewport.width, scene.viewport.height)
    # The gantry is placed on the stage, which fills this viewport.
    assert (scene.lights.x, scene.lights.y, scene.lights.width, scene.lights.height) == (
        rect.x,
        rect.y,
        rect.width,
        rect.height,
    )
    assert abs(scene.lights.width / scene.lights.height - LIGHT_ASPECT) < 0.02
    gantry = light_gantry(scene.lights.width, scene.lights.height)
    assert [(lamp.x, lamp.y, lamp.size) for lamp in scene.lights.lamps] == [
        (face[0], face[1], face[2]) for face in gantry.lamps
    ]
    assert [lamp.lit for lamp in scene.lights.lamps] == [True, True, True, False, False]

    gone = _scene(
        _snapshot(RaceStatus.CREATED, elapsed=0),
        cue=HudCue(active=True, lit_lights=4, go=True),
    )
    assert gone.lights.go is True
    assert all(not lamp.lit for lamp in gone.lights.lamps)
    idle = _scene(_snapshot(RaceStatus.RUNNING))
    assert idle.lights.visible is False
    assert idle.lights.width > 0


def test_between_heats_uses_the_briefing_and_hides_the_stage() -> None:
    briefing = HeatBriefing(
        race_id=RaceId(1),
        sequence=2,
        heat_count=3,
        seats=(HeatSeat(1, 4, "Zoe"), HeatSeat(2, None, "")),
        changes=(LaneChange("Zoe", 2, 1), LaneChange("Anna", None, 2)),
        drivers=((4, "Zoe"),),
    )
    scene = _scene(
        _snapshot(RaceStatus.FINISHED),
        view=VIEW_BETWEEN,
        briefing=briefing,
        viewport=HudViewport(960, 700),
    )
    assert scene.phase == "between"
    assert scene.briefing is not None
    assert scene.panel("briefing").width == 960
    assert "clock" not in [panel.id for panel in scene.panels]
    assert scene.briefing.title == "Durchlauf 2"
    assert scene.briefing.seats[0].driver == "Zoe"
    assert scene.briefing.seats[1].driver == "frei"
    assert scene.briefing.seats[0].column == 0
    assert scene.briefing.seats[1].column == 1
    assert scene.briefing.changes == ("Zoe: Bahn 2 → Bahn 1",)
    assert scene.briefing.rotation == "Spurwechsel"
    assert scene.briefing.hint == "Bitte Fahrer bereitmachen."
    assert scene.briefing.drivers == ("Zoe",)
    assert all(control.host_only for control in scene.briefing.controls)
    assert scene.briefing.title_px == _title_px(960)
    missing = _scene(_snapshot(RaceStatus.FINISHED), view=VIEW_BETWEEN)
    assert missing.briefing is None
    assert "briefing-missing" in missing.notes
    results = _scene(_snapshot(RaceStatus.FINISHED), view=VIEW_RESULTS)
    assert "results-not-live-hud" in results.notes
    assert results.phase == "finished"


def test_finished_and_aborted_keep_the_snapshot_status() -> None:
    finished = _scene(_snapshot(RaceStatus.FINISHED))
    assert finished.phase == "finished"
    assert finished.status_line.value.text == "● Beendet"
    assert finished.status_line.tone == "info"
    assert finished.message.text == "Rennen beendet"
    assert finished.lanes[0].fields[-1].text == "Ausgeschieden"
    aborted = _scene(_snapshot(RaceStatus.FINISHED, aborted=True))
    assert aborted.phase == "aborted"
    assert aborted.status_line.value.text == "● Abgebrochen"
    assert aborted.status_line.tone == "error"
    assert aborted.message.text == "Rennen abgebrochen"


def test_known_viewports_keep_the_same_shares() -> None:
    scene = _scene(_snapshot(RaceStatus.RUNNING), lane_count=4, viewport=HudViewport(1920, 1080))
    assert [panel.share for panel in scene.panels[:4]] == [1, 1, 3, 1]
    assert scene.row_weights == (1, 4, 1)
    clock, status, lanes, ranking = scene.panels[:4]
    assert (clock.x, clock.y, clock.width, clock.height) == (0, 0, 956, 208)
    assert status.x == clock.width + scene.stage_gap
    assert lanes.width == 1284
    assert ranking.width == 628
    assert lanes.y == 216
    assert ranking.y == lanes.y
    assert scene.lane_columns == 4
    assert scene.readable is True
    assert scene.scrolls is False
    assert all(card.width >= 240 for card in scene.lanes)


@pytest.mark.parametrize(
    ("width", "height"),
    [(1920, 1080), (1280, 720), (1024, 768), (768, 1024), (390, 844)],
)
def test_every_requested_viewport_stays_inside_its_own_rules(width: int, height: int) -> None:
    scene = _scene(_snapshot(RaceStatus.RUNNING), lane_count=4, viewport=HudViewport(width, height))
    assert scene.viewport == HudViewport(width, height)
    assert scene.panel("clock").share == 1
    assert scene.panel("lanes").share == 3
    for panel in scene.panels:
        assert panel.width >= 0
        assert panel.height >= 0
    if (width, height) == (390, 844):
        assert scene.scrolls is True
        assert "stage-scrolls" in scene.limits
        assert scene.readable is False
    if (width, height) == (768, 1024):
        assert scene.lane_columns == 1
        assert scene.scrolls is False
    if (width, height) in {(1920, 1080), (1280, 720), (1024, 768)}:
        assert scene.readable is True
        assert scene.scrolls is False


def test_a_host_message_and_warning_are_shown_verbatim() -> None:
    scene = _scene(
        _snapshot(RaceStatus.RUNNING, errors=("Kamera",)),
        message="Zoe beendet das Rennen",
        warning="",
    )
    assert scene.message.text == "Zoe beendet das Rennen"
    assert scene.warning.text == "Warnung: Kamera"
    explicit = _scene(_snapshot(RaceStatus.RUNNING), warning="Bitte warten")
    assert explicit.warning.text == "Bitte warten"


def test_building_a_scene_does_not_change_its_inputs() -> None:
    layout = factory_layout()
    snapshot = _snapshot(RaceStatus.RUNNING)
    fields = layout.fields
    rows = snapshot.rows
    times = snapshot.rows[0].lap_times_ns
    presentation = _presentation()
    scene = build_hud_scene(layout, snapshot, HudViewport(1920, 1080), presentation)
    assert layout.fields is fields
    assert snapshot.rows is rows
    assert snapshot.rows[0].lap_times_ns is times
    assert snapshot.elapsed_ns == 90_000_000_000
    assert scene.elapsed_ns == snapshot.elapsed_ns
    assert scene.lanes[0].fit_floor == LANE_FIT_FLOOR
    assert "lane-font-fit" in scene.measurements
    assert scene.measurements == HOST_MEASUREMENTS
    payload = scene_payload(scene)
    encoded = json.dumps(payload)
    assert '"Zoe"' in encoded
    assert "callable" not in encoded
    again = scene_payload(scene)
    assert again == payload


def test_colors_and_card_margin_are_the_documented_tokens() -> None:
    assert LANE_CARD_MARGIN == 8
    assert dict(COLOR_ROLES)["accent"] == "#2FBF71"
    assert FIT_FONT_MIN == 12
    assert FIT_FONT_MAX == 64


def test_scene_creation_stays_fast_enough_for_a_live_refresh() -> None:
    rows = tuple(
        LiveRow(
            position=index + 1,
            lane=index + 1,
            driver_label=f"Fahrer {index + 1}",
            vehicle_label=f"Wagen {index + 1}",
            start_number=index + 1,
            current_lap=20,
            laps_completed=19,
            last_lap_ns=_LAP + index,
            total_time_ns=_LAP * 19,
            best_lap_ns=_BEST,
            finished=False,
            lap_times_ns=tuple(_BEST + lap * 1_000_000 for lap in range(20)),
        )
        for index in range(12)
    )
    snapshot = _snapshot(RaceStatus.RUNNING, rows=rows, laps=40)
    presentation = _presentation(lane_count=12)
    viewport = HudViewport(1920, 1080)
    layout = factory_layout()
    build_hud_scene(layout, snapshot, viewport, presentation)
    samples = []
    for _ in range(40):
        started = time.perf_counter()
        build_hud_scene(layout, snapshot, viewport, presentation)
        samples.append(time.perf_counter() - started)
    samples.sort()
    median = samples[len(samples) // 2]
    worst = samples[-1]
    print(f"hud-scene 12 drivers, median {median * 1_000:.3f} ms, max {worst * 1_000:.3f} ms")
    assert worst < 0.05
    assert median < 0.01


def test_building_a_scene_does_not_touch_the_race_or_the_database(env: Env) -> None:
    track = env.track("Heimbahn", lanes=2)
    zoe = env.driver("Zoe", number=7)
    anna = env.driver("Anna", number=3)
    porsche = env.vehicle("Porsche", driver_id=zoe.id)
    ferrari = env.vehicle("Ferrari", driver_id=anna.id)
    race = env.races.create_race("Finale", track.id, 8)
    env.races.add_participant(race.id, zoe.id, porsche.id, 1)
    env.races.add_participant(race.id, anna.id, ferrari.id, 2)
    runner = env.controller.prepare_race(race.id)
    snapshot = runner.snapshot()
    elapsed = snapshot.elapsed_ns
    clock = env.clock.now_ns()
    seen: list[str] = []
    subscription = env.runtime.bus.subscribe(Event, lambda event: seen.append(type(event).__name__))
    seen.clear()
    races_before = _rows(env, Race)
    laps_before = _rows(env, Lap)
    scene = build_hud_scene(
        factory_layout(),
        snapshot,
        HudViewport(1280, 720),
        HudPresentation(
            translator=env.runtime.translator,
            mode=runner.race.mode,
            lane_count=runner.race.lane_count,
            duration_minutes=runner.race.duration_minutes,
        ),
    )
    assert seen == []
    assert runner.status is RaceStatus.CREATED
    assert runner.snapshot().elapsed_ns == elapsed
    assert env.clock.now_ns() == clock
    assert _rows(env, Race) == races_before
    assert _rows(env, Lap) == laps_before
    assert scene.phase == "prepared"
    assert scene.race_name == "Finale"
    subscription.cancel()


def _scene(
    snapshot: RaceSnapshot,
    *,
    layout: HudLayout | None = None,
    mode: RaceMode = RaceMode.LAPS,
    lane_count: int = 2,
    duration_minutes: int | None = None,
    message: str = "",
    warning: str = "",
    cue: HudCue | None = None,
    view: str = VIEW_LIVE,
    briefing: HeatBriefing | None = None,
    records: TimeTrialBoard | None = None,
    can_pause: bool = True,
    can_resume: bool = False,
    viewport: HudViewport | None = None,
) -> HudScene:
    chosen = factory_layout() if layout is None else layout
    return build_hud_scene(
        chosen,
        snapshot,
        viewport or HudViewport(1920, 1080),
        _presentation(
            mode=mode,
            lane_count=lane_count,
            duration_minutes=duration_minutes,
            message=message,
            warning=warning,
            cue=cue,
            view=view,
            briefing=briefing,
            records=records,
            can_pause=can_pause,
            can_resume=can_resume,
        ),
    )


def _presentation(
    *,
    mode: RaceMode = RaceMode.LAPS,
    lane_count: int = 2,
    duration_minutes: int | None = None,
    message: str = "",
    warning: str = "",
    cue: HudCue | None = None,
    view: str = VIEW_LIVE,
    briefing: HeatBriefing | None = None,
    records: TimeTrialBoard | None = None,
    can_pause: bool = True,
    can_resume: bool = False,
) -> HudPresentation:
    return HudPresentation(
        translator=_translator(),
        mode=mode,
        lane_count=lane_count,
        duration_minutes=duration_minutes,
        message=message,
        warning=warning,
        cue=cue or HudCue(),
        view=view,
        briefing=briefing,
        records=records,
        can_pause=can_pause,
        can_resume=can_resume,
    )


def snapshot_rows() -> tuple[LiveRow, ...]:
    return (
        LiveRow(2, 2, "Anna", "Ferrari", 3, 1, 0, None, None, None, False, ()),
        LiveRow(1, 1, "Zoe", "Porsche", 7, 2, 2, _LAP, _LAP * 2, _BEST, False, (_BEST, _LAP)),
    )


def _snapshot(
    status: RaceStatus,
    *,
    rows: tuple[LiveRow, ...] | None = None,
    laps: int = 8,
    elapsed: int = 90_000_000_000,
    aborted: bool = False,
    errors: tuple[str, ...] = (),
) -> RaceSnapshot:
    if rows is None:
        rows = snapshot_rows()
    return RaceSnapshot(
        RaceId(1),
        "Finale",
        "Heimbahn",
        "simulation",
        status,
        aborted,
        laps,
        elapsed,
        rows,
        errors,
    )


def _with_field(field_id: str, style: FieldStyle) -> HudLayout:
    layout = factory_layout()
    return replace(
        layout,
        fields=tuple((key, style if key == field_id else item) for key, item in layout.fields),
    )


def _title_px(width: int) -> int:
    from slot_racing.modules.races.hud import briefing_px

    return briefing_px(BRIEFING_TITLE_BASE, width, 100)


def _rows(env: Env, model: type[Race] | type[Lap]) -> int:
    with env.runtime.database.session() as session:
        found = session.scalar(select(func.count()).select_from(model))
    return int(found or 0)


def test_records_can_be_forced_or_hidden_without_a_second_rule() -> None:
    hidden = with_view(
        factory_layout(), VIEW_LIVE, ViewStyle(panels=((PANEL_RECORDS, DISPLAY_HIDE),))
    )
    scene = _scene(_snapshot(RaceStatus.RUNNING, laps=0), mode=RaceMode.TIME_TRIAL, layout=hidden)
    assert scene.records is None
    forced = with_view(
        factory_layout(), VIEW_LIVE, ViewStyle(panels=((PANEL_RECORDS, DISPLAY_ALWAYS),))
    )
    shown = _scene(_snapshot(RaceStatus.RUNNING), layout=forced, records=None)
    assert shown.records is not None
    assert shown.records.records_rows == ()
    assert "records-missing" in shown.notes


def test_best_lap_text_matches_the_formatter() -> None:
    number, text = best_lap_display((_BEST, _LAP))
    scene = _scene(_snapshot(RaceStatus.RUNNING))
    fields = {item.id: item for item in scene.lanes[0].fields}
    assert (fields["best"].text, fields["best_time"].text) == (number, text)
