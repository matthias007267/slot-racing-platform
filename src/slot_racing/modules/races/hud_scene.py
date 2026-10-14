"""Qt-free picture of the live HUD.

``RaceSnapshot`` is the standings and the clock. It does not carry the race mode,
the lane count, the time-trial duration, the start-light step, the heat briefing,
the records board, or a lap notice. Those arrive in :class:`HudPresentation`.
The scene only formats and places what the PC widgets already show. It does not
read the database, publish events, or move the race clock.

Lane cards publish the nominal type size from :func:`effective_px`. The PC then
shrinks that size with :func:`lane_fit_factor` after measuring the text in Qt.
The scene keeps the factor and the floor, and leaves the measurement to the host.
Clock, status and the ranking columns do the same: the fit range is here, the
pixel size stays with the widget that can see the font.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import asdict, dataclass, field, replace
from math import ceil

from slot_racing.core.clock import format_duration
from slot_racing.core.domain import RaceMode, RaceStatus
from slot_racing.core.i18n import Translator
from slot_racing.modules.races.hud import (
    BETWEEN_RANKING_FONT_BASE,
    BOTTOM_MIN_HEIGHT,
    CLOCK_MIN_HEIGHT,
    CLOCK_MIN_WIDTH,
    FIELD_BASE_PX,
    FIELD_BEST,
    FIELD_BEST_TIME,
    FIELD_IDS,
    FIELD_LAST,
    FIELD_REMAINING_LAPS,
    FIELD_REMAINING_TIME,
    FIELD_START,
    FIELD_TOTAL,
    FONT_FLOOR,
    LANE_CARD_MIN_HEIGHT,
    LANE_COLUMN_WIDTH,
    LANE_FIT_FLOOR,
    LANE_LINE_GAP,
    LANES_MIN_HEIGHT,
    LIGHT_ASPECT,
    LIGHT_LAMP_COUNT,
    RANKING_COLUMN_IDS,
    RANKING_COLUMN_LABELS,
    RANKING_MIN_HEIGHT,
    RANKING_MIN_WIDTH,
    RECORDS_MIN_HEIGHT,
    REGION_IDS,
    STAGE_GAP,
    TEXT_DRIVER,
    TEXT_HEADER,
    TEXT_PLACE,
    TEXT_TIME,
    VIEW_BETWEEN,
    VIEW_LIVE,
    VIEW_RESULTS,
    HudLayout,
    HudWidgetConfig,
    between_ranking_pixels,
    briefing_px,
    caption_px,
    effective_px,
    field_shown,
    lane_columns,
    light_gantry,
    light_pixels,
    project_ranking_cells,
    records_shown,
    region_frame,
    regions_ready,
    to_pixels,
    visible_ranking_columns,
)
from slot_racing.modules.races.runner import LiveRow, RaceSnapshot
from slot_racing.modules.races.time_trial_board import TimeTrialBoard, format_lap_seconds
from slot_racing.modules.races.types import HeatBriefing
from slot_racing.modules.races.ui.formatting import (
    EMPTY_DISPLAY,
    best_lap_display,
    format_lap_progress,
    format_lap_time,
    participant_status_key,
    ranking_cells,
    remaining_laps,
    remaining_time,
    start_number_text,
    status_message,
    status_presentation,
)

# The lane card pads with the theme's small step. The scene cannot import the theme.
LANE_CARD_MARGIN = 8
# Vertical bands of the live stage. They are not stored on ``HudLayout``.
STAGE_ROW_WEIGHTS = (1, 4, 1)
BOTTOM_WEIGHTS = (1, 1)
# FitLabel on the clock and the status word.
FIT_FONT_MIN = 12
FIT_FONT_MAX = 64
FIT_PLACEHOLDER_MAX = 22
STATUS_CAPTION_MIN_HEIGHT = 56
MESSAGE_CAPTION_MIN_HEIGHT = 64
CARD_TITLE_PX = 11
RANKING_ROW_PX = 32
# Briefing steps. The theme module owns the widgets; these are the same integers.
BRIEFING_TITLE_BASE = 34
BRIEFING_CAPTION_BASE = 16
BRIEFING_DRIVER_BASE = 28
BRIEFING_HINT_BASE = 16
BRIEFING_CHANGE_BASE = 20
# Records board type sizes set in code, not by a font metric.
RECORD_BADGE_PX = 36
RECORD_LANE_PX = 26
RECORD_BODY_PX = 15
RECORD_ROW_PX = 30
RECORD_HEADER_PX = 28
RECORD_SECTION_PX = 13

# Roles a browser can paint without the Qt stylesheet. A test keeps them on the theme.
COLOR_ROLES: tuple[tuple[str, str], ...] = (
    ("background", "#121417"),
    ("surface", "#1A1E24"),
    ("elevated", "#222831"),
    ("border", "#2E3540"),
    ("text", "#E8EDF2"),
    ("text_secondary", "#A8B0BA"),
    ("text_muted", "#8B949E"),
    ("accent", "#2FBF71"),
    ("warning", "#E2B340"),
    ("error", "#E15B5B"),
    ("info", "#7EB6D9"),
)
_TONE_ROLE = {
    "ok": "accent",
    "warn": "warning",
    "error": "error",
    "info": "info",
    "muted": "text_muted",
}
_CAPTION_KEYS = {
    FIELD_START: "race.column.start_number",
    FIELD_REMAINING_LAPS: "race.column.remaining_laps",
    FIELD_REMAINING_TIME: "race.column.remaining_time",
    FIELD_LAST: "race.column.last_lap",
    FIELD_BEST: "hud.field.best",
    FIELD_BEST_TIME: "hud.field.best_time",
    FIELD_TOTAL: "race.column.total_time",
}
_RANKING_STYLE: dict[str, tuple[str, str, str]] = {
    "position": ("primary", "text", "always"),
    "driver": ("primary", "text", "leader"),
    "vehicle": ("optional", "text_secondary", "never"),
    "start_number": ("optional", "text", "never"),
    "lane": ("optional", "text", "never"),
    "laps": ("primary", "text", "never"),
    "current_lap": ("optional", "text", "never"),
    "last_lap": ("optional", "text", "never"),
    "best_lap": ("preferred", "text", "never"),
    "total_time": ("optional", "text", "never"),
    "progress": ("optional", "text", "never"),
    "status": ("optional", "text", "never"),
}
_RANKING_COLUMNS: tuple[tuple[str, str, str, str, str], ...] = tuple(
    (column_id, RANKING_COLUMN_LABELS[column_id], *_RANKING_STYLE[column_id])
    for column_id in RANKING_COLUMN_IDS
)
# The PC measures these. The scene names them so a browser can do the same job.
HOST_MEASUREMENTS: tuple[str, ...] = (
    "lane-font-fit",
    "clock-font-fit",
    "status-font-fit",
    "ranking-column-hide",
    "ranking-row-fit",
    "control-font-fit",
)


@dataclass(frozen=True, slots=True)
class HudViewport:
    width: int
    height: int


@dataclass(frozen=True, slots=True)
class HudCue:
    """One start-light step. The cue still owns the sequence."""

    active: bool = False
    lit_lights: int = 0
    go: bool = False


def start_gantry_active(has_cue: bool, status: RaceStatus) -> bool:
    """Show the start gantry only while that heat is still waiting for the engine.

    A cue object can outlive the countdown. Once the race is running, aborted or finished,
    the housing stays hidden. The next heat is created again, so the gantry can return.
    """
    return has_cue and status is RaceStatus.CREATED


@dataclass(frozen=True, slots=True)
class HudPresentation:
    """Everything the live view knows that a snapshot does not."""

    translator: Translator
    mode: RaceMode
    lane_count: int
    duration_minutes: int | None = None
    message: str = ""
    warning: str = ""
    cue: HudCue = field(default_factory=HudCue)
    view: str = VIEW_LIVE
    briefing: HeatBriefing | None = None
    records: TimeTrialBoard | None = None
    can_pause: bool = False
    can_resume: bool = False
    can_stop: bool = False
    can_results: bool = False


@dataclass(frozen=True, slots=True)
class HudBox:
    id: str
    x: int
    y: int
    width: int
    height: int
    share: int
    visible: bool
    minimum_width: int
    minimum_height: int
    readable: bool


@dataclass(frozen=True, slots=True)
class HudText:
    text: str
    align: str
    color_role: str
    bold: bool
    visible: bool = True
    font_px: int | None = None
    font_min_px: int | None = None
    font_max_px: int | None = None


@dataclass(frozen=True, slots=True)
class HudReadout:
    caption: HudText
    value: HudText
    tone: str = ""
    panel_tone: str = ""


@dataclass(frozen=True, slots=True)
class HudField:
    id: str
    visible: bool
    text: str
    font_px: int
    caption: str
    caption_px: int
    align: str
    bold: bool
    color_role: str


@dataclass(frozen=True, slots=True)
class HudLaneCard:
    lane: int
    occupied: bool
    driver: str
    vehicle: str
    position: int | None
    x: int
    y: int
    width: int
    height: int
    row: int
    column: int
    fields: tuple[HudField, ...]
    nominal_stack_px: int
    fit_floor: float
    font_floor: int
    line_gap: int
    margin: int


# Width gates of the PC ranking table, before it measures text and may drop the best lap.
RANKING_WIDTH_FLOOR = 40
RANKING_VEHICLE_MIN_WIDTH = 300
RANKING_FULL_TABLE_WIDTH = 980
RANKING_BEST_RESERVE = 64
RANKING_CELL_PAD = 16
RANKING_PRIMARY_IDS = ("position", "driver", "laps")
RANKING_BEST_ID = "best_lap"
_RANKING_INDEX = {column[0]: index for index, column in enumerate(_RANKING_COLUMNS)}
_RANKING_VEHICLE = _RANKING_INDEX["vehicle"]
_RANKING_OPTIONAL = frozenset(
    _RANKING_INDEX[name]
    for name in (
        "start_number",
        "lane",
        "current_lap",
        "last_lap",
        "total_time",
        "progress",
        "status",
    )
)


def ranking_width_hidden(width: int) -> frozenset[int] | None:
    """Column indexes the PC table hides from width alone.

    ``None`` means the panel is too narrow to remeasure, so the PC leaves the
    columns as they are. The best-lap column is not in this set: the table may
    still hide it when the measured text leaves fewer than
    :data:`RANKING_BEST_RESERVE` pixels.
    """
    if width < RANKING_WIDTH_FLOOR:
        return None
    hidden: set[int] = set()
    if width < RANKING_VEHICLE_MIN_WIDTH:
        hidden.add(_RANKING_VEHICLE)
    if width < RANKING_FULL_TABLE_WIDTH:
        hidden.update(_RANKING_OPTIONAL)
    return frozenset(hidden)


def ranking_column_visible(column_id: str, width: int) -> bool:
    """Whether one ranking column is shown at this panel width."""
    hidden = ranking_width_hidden(width)
    if hidden is None:
        return True
    index = _RANKING_INDEX.get(column_id)
    if index is None:
        return True
    return index not in hidden


def ranking_best_fits(viewport: int, primary_px: int) -> bool:
    """Whether the best-lap column keeps its reserve after the primary columns.

    ``primary_px`` is the host's measured width of place, driver and lap count,
    including padding. Below the width floor the PC leaves every column alone.
    The PC can still drop the column afterwards when the assigned pixel widths
    overflow its own scrollbar. That last step needs the widget's layout, so a
    browser that passed this check keeps the column and shortens the text.
    """
    if viewport < RANKING_WIDTH_FLOOR:
        return True
    return viewport - primary_px >= RANKING_BEST_RESERVE


@dataclass(frozen=True, slots=True)
class HudColumn:
    id: str
    header: str
    priority: str
    color_role: str
    bold: str
    visible: bool = True


@dataclass(frozen=True, slots=True)
class HudRankingRow:
    position: int
    lane: int
    cells: tuple[str, ...]
    leader: bool
    participant_id: int = 0


@dataclass(frozen=True, slots=True)
class HudRanking:
    caption: str
    columns: tuple[HudColumn, ...]
    rows: tuple[HudRankingRow, ...]
    row_px: int
    columns_are_complete: bool
    primary_ids: tuple[str, ...] = RANKING_PRIMARY_IDS
    best_id: str = RANKING_BEST_ID
    best_reserve_px: int = RANKING_BEST_RESERVE
    width_floor_px: int = RANKING_WIDTH_FLOOR
    cell_pad_px: int = RANKING_CELL_PAD
    font_px: int | None = None
    scrolls: bool = False
    """The panel scrolls instead of clipping rows or configured columns."""
    fit_columns: bool = True
    """Width fitting may hide a column. The between-heats ranking leaves that off."""


@dataclass(frozen=True, slots=True)
class HudControl:
    id: str
    label: str
    enabled: bool
    host_only: bool
    tone: str
    row: int = 0


@dataclass(frozen=True, slots=True)
class HudLamp:
    index: int
    x: float
    y: float
    size: float
    lit: bool


@dataclass(frozen=True, slots=True)
class HudLightGeometry:
    visible: bool
    active: bool
    lit_lights: int
    go: bool
    x: int
    y: int
    width: int
    height: int
    aspect: float
    housing: tuple[float, float, float, float]
    lamps: tuple[HudLamp, ...]
    on_role: str
    off_role: str
    housing_role: str
    border_role: str


@dataclass(frozen=True, slots=True)
class HudAttempt:
    lane: int
    badge: str
    title: str
    columns: tuple[str, ...]
    rows: tuple[tuple[str, ...], ...]
    session_best: str
    lane_record: str
    badge_px: int


@dataclass(frozen=True, slots=True)
class HudRecords:
    box: HudBox
    name: str
    status_text: str
    status_tone: str
    elapsed_text: str
    warning: str
    records_title: str
    records_columns: tuple[str, ...]
    records_rows: tuple[tuple[str, ...], ...]
    active_title: str
    active_columns: tuple[str, ...]
    active_rows: tuple[tuple[str, ...], ...]
    active_muted_rows: tuple[int, ...]
    attempts: tuple[HudAttempt, ...]
    badge_px: int
    lane_px: int
    body_px: int
    row_px: int
    header_px: int
    section_px: int
    controls: tuple[HudControl, ...]


@dataclass(frozen=True, slots=True)
class HudSeat:
    lane: int
    lane_text: str
    driver: str
    row: int
    column: int
    lane_px: int
    driver_px: int


@dataclass(frozen=True, slots=True)
class HudBriefing:
    box: HudBox
    sequence: int
    title: str
    title_px: int
    seats: tuple[HudSeat, ...]
    seat_columns: int
    rotation: str
    rotation_px: int
    changes: tuple[str, ...]
    change_px: int
    hint: str
    hint_px: int
    drivers: tuple[str, ...]
    controls: tuple[HudControl, ...]


@dataclass(frozen=True, slots=True)
class HudScene:
    viewport: HudViewport
    layout_id: str
    font_scale: int
    alignment: str
    view: str
    phase: str
    mode: str
    race_name: str
    track_name: str
    status: str
    aborted: bool
    elapsed_ns: int
    panels: tuple[HudBox, ...]
    clock: HudReadout
    status_line: HudReadout
    lane_columns: int
    lane_rows: int
    lanes: tuple[HudLaneCard, ...]
    ranking: HudRanking
    message_caption: HudText
    message: HudText
    warning: HudText
    controls: tuple[HudControl, ...]
    lights: HudLightGeometry
    records: HudRecords | None
    briefing: HudBriefing | None
    scrolls: bool
    horizontal_overflow: bool
    readable: bool
    limits: tuple[str, ...]
    notes: tuple[str, ...]
    measurements: tuple[str, ...]
    colors: tuple[tuple[str, str], ...]
    stage_gap: int
    card_gap: int
    row_weights: tuple[int, int, int]

    def panel(self, panel_id: str) -> HudBox:
        for item in self.panels:
            if item.id == panel_id:
                return item
        raise KeyError(panel_id)


def build_hud_scene(
    layout: HudLayout,
    snapshot: RaceSnapshot,
    viewport: HudViewport,
    presentation: HudPresentation,
) -> HudScene:
    """Resolve one HUD. The arguments are only read."""
    translator = presentation.translator
    translate = translator.translate
    status_text, tone = status_presentation(translator, snapshot)
    phase = _phase(snapshot, presentation)
    notes = _notes(presentation)
    show_briefing = presentation.view == VIEW_BETWEEN and presentation.briefing is not None
    show_records = not show_briefing and records_shown(
        layout, time_trial=presentation.mode is RaceMode.TIME_TRIAL
    )
    if show_records and presentation.records is None:
        notes = (*notes, "records-missing")

    width = max(0, viewport.width)
    height = max(0, viewport.height)
    lanes_count = max(0, presentation.lane_count)
    stage_box, records_box, _overflow = _surfaces(
        layout, width, height, lanes_count, show_records=show_records and not show_briefing
    )
    panels: list[HudBox] = []
    limits: list[str] = []
    cards: tuple[HudLaneCard, ...] = ()
    columns = 1
    rows = 0
    between_ranking = None
    if show_briefing:
        panels.append(_box("briefing", 0, 0, width, height, share=1, minimum_height=0))
        if layout.between_ranking.visible:
            between_ranking = between_ranking_pixels(layout.between_ranking, width, height)
            panels.append(
                _box(
                    "ranking",
                    between_ranking.x,
                    between_ranking.y,
                    between_ranking.width,
                    between_ranking.height,
                    share=1,
                    minimum_height=0,
                )
            )
    else:
        placed = _place_stage(layout, stage_box, lanes_count)
        panels.extend(placed.panels)
        cards = _cards(
            layout,
            snapshot,
            presentation,
            placed.lanes_box,
            placed.columns,
            placed.rows,
        )
        columns = placed.columns
        rows = placed.rows
        if show_records and records_box is not None:
            panels.append(records_box)
        if any(card.width < LANE_COLUMN_WIDTH for card in cards):
            limits.append("lane-card-narrow")
    scrolls = any(item.y + item.height > height for item in panels)
    horizontal = any(item.x + item.width > width for item in panels)
    if scrolls:
        limits.append("stage-scrolls")
    if horizontal:
        limits.append("horizontal-overflow")

    readable = not horizontal and "lane-card-narrow" not in limits
    readable = readable and all(item.readable for item in panels) and not scrolls
    lights = _lights(layout, presentation.cue, stage_box)
    warning = _warning_text(presentation, snapshot)
    message = presentation.message or status_message(translate, snapshot) or EMPTY_DISPLAY
    status_line = _status_line(translate, status_text, tone)
    if _panel_height(panels, "status") < STATUS_CAPTION_MIN_HEIGHT:
        status_line = replace(status_line, caption=replace(status_line.caption, visible=False))
    return HudScene(
        viewport=HudViewport(width, height),
        layout_id=layout.id,
        font_scale=layout.font_scale,
        alignment=layout.alignment,
        view=presentation.view,
        phase=phase,
        mode=presentation.mode.value,
        race_name=snapshot.name,
        track_name=snapshot.track_name,
        status=snapshot.status.value,
        aborted=snapshot.aborted,
        elapsed_ns=snapshot.elapsed_ns,
        panels=tuple(panels),
        clock=_clock(translate, snapshot),
        status_line=status_line,
        lane_columns=columns,
        lane_rows=rows,
        lanes=cards,
        ranking=(
            _configured_ranking(translator, snapshot, layout, between_ranking.height)
            if between_ranking is not None
            else _ranking(
                translator,
                snapshot,
                next((item.width for item in panels if item.id == "ranking"), 0),
            )
        ),
        message_caption=_caption(
            translate("hud.widget.race_message"),
            visible=_panel_height(panels, "messages") >= MESSAGE_CAPTION_MIN_HEIGHT,
        ),
        message=HudText(
            message,
            "left",
            "text",
            False,
            _panel_height(panels, "messages") > 0,
            font_px=None,
        ),
        warning=HudText(warning, "left", "error", False, bool(warning)),
        controls=_race_controls(translate, presentation),
        lights=lights,
        records=(
            _records(translator, snapshot, status_text, tone, warning, presentation, records_box)
            if show_records and records_box is not None
            else None
        ),
        briefing=_briefing(layout, translator, presentation.briefing, panels[0])
        if show_briefing and presentation.briefing is not None
        else None,
        scrolls=scrolls,
        horizontal_overflow=horizontal,
        readable=readable,
        limits=tuple(dict.fromkeys(limits)),
        notes=notes,
        measurements=HOST_MEASUREMENTS,
        colors=COLOR_ROLES,
        stage_gap=STAGE_GAP,
        card_gap=LANE_CARD_MARGIN,
        row_weights=STAGE_ROW_WEIGHTS,
    )


def scene_payload(scene: HudScene) -> dict[str, object]:
    """A JSON-ready document. Tuples become lists. There is no widget and no callback."""
    plain = _plain(asdict(scene))
    if not isinstance(plain, dict):
        raise TypeError("HUD scene payload must be an object")
    return plain


@dataclass(frozen=True, slots=True)
class _StagePlace:
    panels: tuple[HudBox, ...]
    lanes_box: HudBox
    columns: int
    rows: int
    scrolls: bool
    horizontal: bool
    limits: tuple[str, ...]


def _surfaces(
    layout: HudLayout,
    width: int,
    height: int,
    lane_count: int,
    *,
    show_records: bool,
) -> tuple[HudBox, HudBox | None, bool]:
    content_h, _columns, _rows, canvas_w = _stage_need(layout, width, lane_count)
    if not show_records:
        stage_h = max(height, content_h)
        stage = _box(
            "stage",
            0,
            0,
            max(width, canvas_w),
            stage_h,
            share=1,
            minimum_height=content_h,
        )
        return stage, None, stage_h > height or canvas_w > width
    heights = _lengths(height, (1, 1), (content_h, RECORDS_MIN_HEIGHT), 0)
    stage = _box("stage", 0, 0, max(width, canvas_w), heights[0], share=1, minimum_height=content_h)
    records = _box(
        "records",
        0,
        heights[0],
        width,
        heights[1],
        share=1,
        minimum_height=RECORDS_MIN_HEIGHT,
    )
    overflow = heights[0] + heights[1] > height or canvas_w > width
    return stage, records, overflow


def _place_stage(layout: HudLayout, stage: HudBox, lane_count: int) -> _StagePlace:
    custom = _place_regions(layout, stage, lane_count)
    if custom is not None:
        return custom
    content_h, columns, rows, canvas_w = _stage_need(layout, stage.width, lane_count)
    canvas_h = max(stage.height, content_h)
    top_h, mid_h, bot_h = _lengths(
        canvas_h,
        STAGE_ROW_WEIGHTS,
        (CLOCK_MIN_HEIGHT, _middle_min(rows), BOTTOM_MIN_HEIGHT),
        STAGE_GAP,
    )
    origins = _origins((top_h, mid_h, bot_h), STAGE_GAP, stage.y)
    clock_w, status_w = _lengths(
        stage.width,
        (layout.clock_share, layout.status_share),
        (CLOCK_MIN_WIDTH, CLOCK_MIN_WIDTH),
        STAGE_GAP,
    )
    lanes_w, ranking_w = _lengths(
        stage.width, (layout.lanes_share, layout.ranking_share), (0, RANKING_MIN_WIDTH), STAGE_GAP
    )
    message_w, control_w = _lengths(stage.width, BOTTOM_WEIGHTS, (0, 0), STAGE_GAP)
    top_x = _origins((clock_w, status_w), STAGE_GAP, stage.x)
    mid_x = _origins((lanes_w, ranking_w), STAGE_GAP, stage.x)
    bot_x = _origins((message_w, control_w), STAGE_GAP, stage.x)
    panels = (
        _box(
            "clock",
            top_x[0],
            origins[0],
            clock_w,
            top_h,
            layout.clock_share,
            CLOCK_MIN_WIDTH,
            CLOCK_MIN_HEIGHT,
        ),
        _box(
            "status",
            top_x[1],
            origins[0],
            status_w,
            top_h,
            layout.status_share,
            CLOCK_MIN_WIDTH,
            CLOCK_MIN_HEIGHT,
        ),
        _box(
            "lanes",
            mid_x[0],
            origins[1],
            lanes_w,
            mid_h,
            layout.lanes_share,
            0,
            LANES_MIN_HEIGHT,
        ),
        _box(
            "ranking",
            mid_x[1],
            origins[1],
            ranking_w,
            mid_h,
            layout.ranking_share,
            RANKING_MIN_WIDTH,
            RANKING_MIN_HEIGHT,
        ),
        _box("messages", bot_x[0], origins[2], message_w, bot_h, 1, 0, BOTTOM_MIN_HEIGHT),
        _box("controls", bot_x[1], origins[2], control_w, bot_h, 1, 0, BOTTOM_MIN_HEIGHT),
    )
    limits: list[str] = []
    if canvas_h > stage.height:
        limits.append("stage-scrolls")
    if canvas_w > stage.width:
        limits.append("horizontal-overflow")
    return _StagePlace(
        panels,
        panels[2],
        columns,
        rows,
        scrolls=canvas_h > stage.height,
        horizontal=canvas_w > stage.width,
        limits=tuple(limits),
    )


def _place_regions(layout: HudLayout, stage: HudBox, lane_count: int) -> _StagePlace | None:
    """Stored rectangles, in the same fractions the PC stage uses. Shares stay untouched."""
    if not regions_ready(layout) or stage.width <= 0 or stage.height <= 0:
        return None
    panels: list[HudBox] = []
    for region_id in REGION_IDS:
        frame = region_frame(layout, region_id)
        if frame is None:
            return None
        rect = to_pixels(
            HudWidgetConfig(frame.id, True, frame.x, frame.y, frame.width, frame.height),
            stage.width,
            stage.height,
        )
        panels.append(
            _box(
                region_id,
                stage.x + rect.x,
                stage.y + rect.y,
                rect.width,
                rect.height,
                share=1,
            )
        )
    lanes_box = panels[REGION_IDS.index("lanes")]
    columns, rows = _lane_grid(lane_count, lanes_box.width)
    return _StagePlace(tuple(panels), lanes_box, columns, rows, False, False, ())


def _stage_need(layout: HudLayout, width: int, lane_count: int) -> tuple[int, int, int, int]:
    min_width = max(CLOCK_MIN_WIDTH * 2 + STAGE_GAP, RANKING_MIN_WIDTH + STAGE_GAP)
    canvas_w = max(width, min_width)
    lanes_w, _ranking_w = _lengths(
        canvas_w, (layout.lanes_share, layout.ranking_share), (0, RANKING_MIN_WIDTH), STAGE_GAP
    )
    columns, rows = _lane_grid(lane_count, lanes_w)
    content_h = CLOCK_MIN_HEIGHT + _middle_min(rows) + BOTTOM_MIN_HEIGHT + 2 * STAGE_GAP
    return content_h, columns, rows, canvas_w


def _middle_min(rows: int) -> int:
    grid = 0 if rows < 1 else rows * LANE_CARD_MIN_HEIGHT + (rows - 1) * LANE_CARD_MARGIN
    return max(LANES_MIN_HEIGHT, RANKING_MIN_HEIGHT, grid)


def _lane_grid(lane_count: int, lanes_width: int) -> tuple[int, int]:
    if lane_count < 1:
        return 1, 0
    columns = lane_columns(lane_count, lanes_width)
    return columns, ceil(lane_count / columns)


def _cards(
    layout: HudLayout,
    snapshot: RaceSnapshot,
    presentation: HudPresentation,
    lanes_box: HudBox,
    columns: int,
    rows: int,
) -> tuple[HudLaneCard, ...]:
    count = max(0, presentation.lane_count)
    if count < 1 or rows < 1 or columns < 1:
        return ()
    widths = _lengths(
        lanes_box.width,
        tuple(1 for _ in range(columns)),
        tuple(0 for _ in range(columns)),
        LANE_CARD_MARGIN,
    )
    heights = _lengths(
        lanes_box.height,
        tuple(1 for _ in range(rows)),
        tuple(LANE_CARD_MIN_HEIGHT for _ in range(rows)),
        LANE_CARD_MARGIN,
    )
    origins_x = _origins(widths, LANE_CARD_MARGIN, lanes_box.x)
    origins_y = _origins(heights, LANE_CARD_MARGIN, lanes_box.y)
    by_lane = {row.lane: row for row in snapshot.rows if row.seated}
    paused = snapshot.status is RaceStatus.PAUSED
    ended = snapshot.status is RaceStatus.FINISHED
    free = presentation.translator.translate("race.time_trial.free")
    cards: list[HudLaneCard] = []
    for index in range(count):
        row_index, column_index = divmod(index, columns)
        lane = index + 1
        standing = by_lane.get(lane)
        fields = _fields(
            layout,
            presentation,
            snapshot,
            standing,
            lane,
            free,
            paused=paused,
            ended=ended,
        )
        cards.append(
            HudLaneCard(
                lane=lane,
                occupied=standing is not None,
                driver=free if standing is None else standing.driver_label,
                vehicle=EMPTY_DISPLAY if standing is None else standing.vehicle_label,
                position=None if standing is None else standing.position,
                x=origins_x[column_index],
                y=origins_y[row_index],
                width=widths[column_index],
                height=heights[row_index],
                row=row_index,
                column=column_index,
                fields=fields,
                nominal_stack_px=_nominal_stack(fields),
                fit_floor=LANE_FIT_FLOOR,
                font_floor=FONT_FLOOR,
                line_gap=LANE_LINE_GAP,
                margin=LANE_CARD_MARGIN,
            )
        )
    return tuple(cards)


def _fields(
    layout: HudLayout,
    presentation: HudPresentation,
    snapshot: RaceSnapshot,
    standing: LiveRow | None,
    lane: int,
    free: str,
    *,
    paused: bool,
    ended: bool,
) -> tuple[HudField, ...]:
    mode = presentation.mode
    laps_left = None if standing is None else remaining_laps(standing.laps_completed, snapshot.laps)
    time_left = (
        None
        if standing is None
        else remaining_time(snapshot.elapsed_ns, presentation.duration_minutes)
    )
    translate = presentation.translator.translate
    if standing is None:
        values = _free_values(translate, lane, free)
    else:
        best_number, best_time = best_lap_display(standing.lap_times_ns)
        values = {
            "lane": f"{translate('race.live.lane')} {lane}",
            "position": f"P{standing.position}",
            "driver": standing.driver_label,
            "vehicle": standing.vehicle_label,
            "start": start_number_text(standing.start_number),
            "lap": format_lap_progress(standing.current_lap, snapshot.laps),
            "remaining_laps": laps_left or EMPTY_DISPLAY,
            "remaining_time": time_left or EMPTY_DISPLAY,
            "last": format_lap_time(standing.last_lap_ns),
            "best": best_number,
            "best_time": best_time,
            "total": format_duration(standing.total_time_ns),
            "status": translate(
                participant_status_key(finished=standing.finished, paused=paused, ended=ended)
            ),
        }
    fields: list[HudField] = []
    for field_id in FIELD_IDS:
        style = layout.field(field_id)
        available = _available(field_id, laps_left, time_left)
        visible = field_shown(style, field_id, mode, available=available)
        pixels = effective_px(FIELD_BASE_PX[field_id], layout.font_scale, style.scale)
        caption_key = _CAPTION_KEYS.get(field_id, "")
        fields.append(
            HudField(
                id=field_id,
                visible=visible,
                text=values[field_id],
                font_px=pixels,
                caption=translate(caption_key) if caption_key else "",
                caption_px=caption_px(pixels) if caption_key else 0,
                align=layout.alignment,
                bold=True,
                color_role="text",
            )
        )
    return tuple(fields)


def _free_values(translate: Callable[[str], str], lane: int, free: str) -> dict[str, str]:
    return {
        "lane": f"{translate('race.live.lane')} {lane}",
        "position": EMPTY_DISPLAY,
        "driver": free,
        "vehicle": EMPTY_DISPLAY,
        "start": EMPTY_DISPLAY,
        "lap": EMPTY_DISPLAY,
        "remaining_laps": EMPTY_DISPLAY,
        "remaining_time": EMPTY_DISPLAY,
        "last": "-",
        "best": "-",
        "best_time": "-",
        "total": "-",
        "status": EMPTY_DISPLAY,
    }


def _available(field_id: str, laps_left: str | None, time_left: str | None) -> bool:
    if field_id == FIELD_REMAINING_LAPS:
        return laps_left is not None
    if field_id == FIELD_REMAINING_TIME:
        return time_left is not None
    return True


def _nominal_stack(fields: Sequence[HudField]) -> int:
    shown = [item for item in fields if item.visible]
    if not shown:
        return LANE_CARD_MARGIN * 2
    body = sum(item.font_px + item.caption_px for item in shown)
    return body + LANE_LINE_GAP * (len(shown) - 1) + LANE_CARD_MARGIN * 2


def _configured_ranking(
    translator: Translator, snapshot: RaceSnapshot, layout: HudLayout, height: int
) -> HudRanking:
    """The between-heats ranking. Same rows as the live table, configured columns only.

    ``height`` is the panel. The payload keeps every row and asks the host to scroll.
    """
    del height
    frame = layout.between_ranking
    ended = snapshot.status is RaceStatus.FINISHED
    paused = snapshot.status is RaceStatus.PAUSED
    column_ids = visible_ranking_columns(frame)
    style = {column[0]: column for column in _RANKING_COLUMNS}
    columns = tuple(
        HudColumn(
            column_id,
            translator.translate(style[column_id][1]),
            style[column_id][2],
            style[column_id][3],
            style[column_id][4],
            True,
        )
        for column_id in column_ids
    )
    font_px = effective_px(BETWEEN_RANKING_FONT_BASE, frame.font_scale, 100)
    row_px = max(RANKING_ROW_PX, font_px + 10)
    rows: list[HudRankingRow] = []
    for standing in snapshot.standings or snapshot.rows:
        cells = ranking_cells(translator, snapshot, standing, ended=ended, paused=paused)
        rows.append(
            HudRankingRow(
                position=standing.position,
                lane=standing.lane,
                cells=project_ranking_cells(cells, frame),
                leader=standing.position == 1,
                participant_id=standing.participant_id,
            )
        )
    return HudRanking(
        caption=translator.translate("hud.widget.live_ranking"),
        columns=columns,
        rows=tuple(rows),
        row_px=row_px,
        columns_are_complete=True,
        primary_ids=tuple(
            column_id for column_id in RANKING_PRIMARY_IDS if column_id in column_ids
        ),
        best_id=RANKING_BEST_ID if RANKING_BEST_ID in column_ids else "",
        best_reserve_px=RANKING_BEST_RESERVE,
        width_floor_px=RANKING_WIDTH_FLOOR,
        cell_pad_px=RANKING_CELL_PAD,
        font_px=font_px,
        scrolls=True,
        fit_columns=False,
    )


def _ranking(translator: Translator, snapshot: RaceSnapshot, width: int) -> HudRanking:
    ended = snapshot.status is RaceStatus.FINISHED
    paused = snapshot.status is RaceStatus.PAUSED
    rows = []
    for standing in snapshot.standings or snapshot.rows:
        cells = ranking_cells(translator, snapshot, standing, ended=ended, paused=paused)
        rows.append(
            HudRankingRow(
                position=standing.position,
                lane=standing.lane,
                cells=cells,
                leader=cells[0] == "1",
                participant_id=standing.participant_id,
            )
        )
    columns = tuple(
        HudColumn(
            column_id,
            translator.translate(key),
            priority,
            role,
            bold,
            ranking_column_visible(column_id, width),
        )
        for column_id, key, priority, role, bold in _RANKING_COLUMNS
    )
    return HudRanking(
        caption=translator.translate("hud.widget.live_ranking"),
        columns=columns,
        rows=tuple(rows),
        row_px=RANKING_ROW_PX,
        columns_are_complete=True,
        primary_ids=RANKING_PRIMARY_IDS,
        best_id=RANKING_BEST_ID,
        best_reserve_px=RANKING_BEST_RESERVE,
        width_floor_px=RANKING_WIDTH_FLOOR,
        cell_pad_px=RANKING_CELL_PAD,
    )


def _clock(translate: Callable[[str], str], snapshot: RaceSnapshot) -> HudReadout:
    return HudReadout(
        caption=_caption(translate("race.live.time"), visible=True),
        value=HudText(
            format_duration(snapshot.elapsed_ns),
            "center",
            "text",
            True,
            font_min_px=FIT_FONT_MIN,
            font_max_px=FIT_FONT_MAX,
        ),
    )


def _status_line(translate: Callable[[str], str], status_text: str, tone: str) -> HudReadout:
    panel = tone if tone in {"ok", "warn"} else ""
    return HudReadout(
        caption=_caption(translate("hud.widget.race_status"), visible=True),
        value=HudText(
            f"● {status_text}",
            "center",
            _TONE_ROLE.get(tone, "text"),
            True,
            font_min_px=FIT_FONT_MIN,
            font_max_px=FIT_FONT_MAX,
        ),
        tone=tone,
        panel_tone=panel,
    )


def _caption(text: str, *, visible: bool) -> HudText:
    return HudText(text, "left", "text_muted", True, visible, font_px=CARD_TITLE_PX)


def _race_controls(
    translate: Callable[[str], str],
    presentation: HudPresentation,
    *,
    single_row: bool = False,
) -> tuple[HudControl, ...]:
    enabled = {
        "pause": presentation.can_pause,
        "resume": presentation.can_resume,
        "abort": presentation.can_stop,
        "results": presentation.can_results,
        "back": True,
    }
    # The live stage uses two rows. The records board keeps the same buttons on one row.
    spec = (
        ("pause", "hud.control.pause", "", 0),
        ("resume", "hud.control.resume", "", 0),
        ("abort", "hud.control.abort", "danger", 1),
        ("results", "hud.control.results", "primary", 1),
        ("back", "hud.control.back", "ghost", 1),
    )
    return tuple(
        HudControl(
            control_id,
            translate(key),
            enabled[control_id],
            True,
            tone,
            0 if single_row else row,
        )
        for control_id, key, tone, row in spec
    )


def _lights(layout: HudLayout, cue: HudCue, stage: HudBox) -> HudLightGeometry:
    rect = light_pixels(layout.lights, stage.width, stage.height)
    lit = 0 if cue.go else min(LIGHT_LAMP_COUNT, max(0, cue.lit_lights))
    lamps: tuple[HudLamp, ...] = ()
    housing = (0.0, 0.0, 0.0, 0.0)
    if rect.width > 0 and rect.height > 0:
        gantry = light_gantry(rect.width, rect.height, lamps=LIGHT_LAMP_COUNT)
        housing = gantry.housing
        lamps = tuple(
            HudLamp(index, face[0], face[1], face[2], index < lit and cue.active and not cue.go)
            for index, face in enumerate(gantry.lamps)
        )
    return HudLightGeometry(
        visible=layout.lights.visible and cue.active and rect.width > 0,
        active=cue.active,
        lit_lights=lit,
        go=cue.go,
        x=stage.x + rect.x,
        y=stage.y + rect.y,
        width=rect.width,
        height=rect.height,
        aspect=LIGHT_ASPECT,
        housing=housing,
        lamps=lamps,
        on_role="error",
        off_role="error",
        housing_role="background",
        border_role="border",
    )


def _records(
    translator: Translator,
    snapshot: RaceSnapshot,
    status_text: str,
    tone: str,
    warning: str,
    presentation: HudPresentation,
    box: HudBox,
) -> HudRecords:
    board = presentation.records
    missing = "-"
    free = translator.translate("race.time_trial.free")
    records_rows: list[tuple[str, ...]] = []
    active_rows: list[tuple[str, ...]] = []
    muted: list[int] = []
    attempts: list[HudAttempt] = []
    if board is not None:
        records_rows.extend(
            (
                str(line.lane),
                line.driver_label or missing,
                line.vehicle_label or missing,
                format_lap_seconds(line.time_ns),
            )
            for line in board.records
        )
        for index, line in enumerate(board.active):
            if line.occupied:
                active_rows.append(
                    (str(line.lane), line.driver_label or missing, line.vehicle_label or missing)
                )
            else:
                active_rows.append((str(line.lane), free, missing))
                muted.append(index)
        for attempt in board.attempts:
            attempts.append(
                HudAttempt(
                    lane=attempt.lane,
                    badge=str(attempt.lane),
                    title=translator.format(
                        "race.time_trial.attempt",
                        lane=attempt.lane,
                        driver=attempt.driver_label,
                        vehicle=attempt.vehicle_label,
                    ),
                    columns=(
                        translator.translate("race.column.lap"),
                        translator.translate("race.column.measured_time"),
                    ),
                    rows=tuple(
                        (str(line.offset), format_lap_seconds(line.time_ns))
                        for line in attempt.recent
                    ),
                    session_best=translator.format(
                        "race.time_trial.session_best",
                        time=format_lap_seconds(attempt.session_best_ns),
                    ),
                    lane_record=translator.format(
                        "race.time_trial.lane_record",
                        time=format_lap_seconds(attempt.lane_record_ns),
                    ),
                    badge_px=RECORD_BADGE_PX,
                )
            )
    return HudRecords(
        box=box,
        name=snapshot.name,
        status_text=f"● {status_text}",
        status_tone=tone,
        elapsed_text=format_duration(snapshot.elapsed_ns),
        warning=warning,
        records_title=translator.translate("race.time_trial.records"),
        records_columns=(
            translator.translate("race.time_trial.column.lane"),
            translator.translate("race.column.driver"),
            translator.translate("race.column.vehicle"),
            translator.translate("race.time_trial.column.best"),
        ),
        records_rows=tuple(records_rows),
        active_title=translator.translate("race.time_trial.active"),
        active_columns=(
            translator.translate("race.time_trial.column.lane"),
            translator.translate("race.column.driver"),
            translator.translate("race.column.vehicle"),
        ),
        active_rows=tuple(active_rows),
        active_muted_rows=tuple(muted),
        attempts=tuple(attempts),
        badge_px=RECORD_BADGE_PX,
        lane_px=RECORD_LANE_PX,
        body_px=RECORD_BODY_PX,
        row_px=RECORD_ROW_PX,
        header_px=RECORD_HEADER_PX,
        section_px=RECORD_SECTION_PX,
        controls=_race_controls(translator.translate, presentation, single_row=True),
    )


def _briefing(
    layout: HudLayout, translator: Translator, briefing: HeatBriefing, box: HudBox
) -> HudBriefing:
    view = layout.view(VIEW_BETWEEN)
    header = view.text_scale(TEXT_HEADER)
    driver_scale = view.text_scale(TEXT_DRIVER)
    free = translator.translate("race.time_trial.free")
    width = box.width
    columns = 2 if len(briefing.seats) > 1 else 1
    seats: list[HudSeat] = []
    for index, seat in enumerate(briefing.seats):
        seats.append(
            HudSeat(
                lane=seat.lane,
                lane_text=f"{translator.translate('race.live.lane')} {seat.lane}",
                driver=seat.driver_label or free,
                row=index // columns,
                column=index % columns,
                lane_px=briefing_px(BRIEFING_CAPTION_BASE, width, header),
                driver_px=briefing_px(BRIEFING_DRIVER_BASE, width, driver_scale),
            )
        )
    changes = tuple(
        translator.format(
            "race.heat.change",
            driver=change.driver_label,
            from_lane=change.from_lane,
            to_lane=change.to_lane,
        )
        for change in briefing.changes
        if change.from_lane is not None
    )
    present = bool(changes)
    return HudBriefing(
        box=box,
        sequence=briefing.sequence,
        title=translator.format(
            "race.heat.title", sequence=briefing.sequence, total=briefing.heat_count
        ),
        title_px=briefing_px(BRIEFING_TITLE_BASE, width, view.text_scale(TEXT_PLACE)),
        seats=tuple(seats),
        seat_columns=columns,
        rotation=translator.translate("race.heat.rotation") if present else "",
        rotation_px=briefing_px(BRIEFING_CAPTION_BASE, width, header),
        changes=changes,
        change_px=briefing_px(BRIEFING_CHANGE_BASE, width, driver_scale),
        hint=translator.translate("race.heat.ready_hint"),
        hint_px=briefing_px(BRIEFING_HINT_BASE, width, view.text_scale(TEXT_TIME)),
        drivers=tuple(label for _participant_id, label in briefing.drivers),
        controls=(
            HudControl(
                "postpone",
                translator.translate("race.heat.postpone"),
                True,
                True,
                "secondary",
                0,
            ),
            HudControl(
                "disqualify",
                translator.translate("race.heat.disqualify"),
                True,
                True,
                "danger",
                1,
            ),
            HudControl("start", translator.translate("race.heat.start"), True, True, "primary", 2),
        ),
    )


def _warning_text(presentation: HudPresentation, snapshot: RaceSnapshot) -> str:
    if presentation.warning:
        return presentation.warning
    if not snapshot.source_errors:
        return ""
    return presentation.translator.format(
        "race.live.warning", detail="; ".join(snapshot.source_errors)
    )


def _phase(snapshot: RaceSnapshot, presentation: HudPresentation) -> str:
    if presentation.view == VIEW_BETWEEN:
        return "between"
    if presentation.cue.active:
        return "start"
    if snapshot.status is RaceStatus.FINISHED and snapshot.aborted:
        return "aborted"
    if snapshot.status is RaceStatus.FINISHED:
        return "finished"
    if snapshot.status is RaceStatus.PAUSED:
        return "paused"
    if snapshot.status is RaceStatus.RUNNING:
        return "running"
    return "prepared"


def _notes(presentation: HudPresentation) -> tuple[str, ...]:
    notes: list[str] = []
    if presentation.view == VIEW_BETWEEN and presentation.briefing is None:
        notes.append("briefing-missing")
    if presentation.view == VIEW_RESULTS:
        notes.append("results-not-live-hud")
    return tuple(notes)


def _panel_height(panels: Sequence[HudBox], panel_id: str) -> int:
    for item in panels:
        if item.id == panel_id:
            return item.height
    return 0


def _lengths(
    total: int, weights: tuple[int, ...], minimums: tuple[int, ...], gap: int
) -> tuple[int, ...]:
    """Minimum first, then stretch. A short total keeps the minimums and overflows."""
    floors = tuple(max(0, item) for item in minimums)
    gaps = gap * max(len(weights) - 1, 0)
    extra = total - gaps - sum(floors)
    if extra <= 0 or sum(weights) <= 0:
        return floors
    shares = [extra * weight // sum(weights) for weight in weights]
    leftover = extra - sum(shares)
    for index in range(leftover):
        shares[index] += 1
    return tuple(floor + share for floor, share in zip(floors, shares, strict=True))


def _origins(sizes: tuple[int, ...], gap: int, origin: int) -> tuple[int, ...]:
    cursor = origin
    placed: list[int] = []
    for size in sizes:
        placed.append(cursor)
        cursor += size + gap
    return tuple(placed)


def _box(
    panel_id: str,
    x: int,
    y: int,
    width: int,
    height: int,
    share: int,
    minimum_width: int = 0,
    minimum_height: int = 0,
) -> HudBox:
    return HudBox(
        panel_id,
        x,
        y,
        width,
        height,
        share,
        True,
        minimum_width,
        minimum_height,
        width >= minimum_width and height >= minimum_height,
    )


def _plain(value: object) -> object:
    if isinstance(value, dict):
        return {str(key): _plain(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_plain(item) for item in value]
    if isinstance(value, float | int | str | bool) or value is None:
        return value
    raise TypeError(f"HUD scene cannot be sent as {type(value).__name__}")
