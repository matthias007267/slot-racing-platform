"""Last lap, best valid lap and lap count stay on the live view until the next start."""

from __future__ import annotations

from datetime import datetime

from pytestqt.qtbot import QtBot

from slot_racing.core.clock import NANOS_PER_SECOND as S
from slot_racing.core.clock import ManualClock
from slot_racing.core.domain import (
    DriverId,
    Participant,
    RaceId,
    RaceMode,
    RaceStatus,
    TimingLayout,
    VehicleId,
)
from slot_racing.core.events import EventBus, SensorTriggered
from slot_racing.modules.races.engine import RaceConfig, RaceEngine
from slot_racing.modules.races.runner import RaceRunner
from slot_racing.modules.races.types import ParticipantInfo, RaceInfo
from slot_racing.modules.races.ui.formatting import format_lap_time
from slot_racing.modules.races.ui.live_stage import LiveHudStage
from tests.modules.conftest import Env
from tests.modules.test_ui_management import column_text

_LINE = TimingLayout.from_position_ids(["start_finish"])


def test_a_missing_or_zero_lap_is_not_shown_as_a_time() -> None:
    assert format_lap_time(None) == "-"
    assert format_lap_time(0) == "-"
    assert format_lap_time(-1) == "-"
    assert format_lap_time(5 * S) == "0:05.000"


def test_live_values_follow_valid_laps_and_reset_on_the_next_start() -> None:
    first = _Running(RaceId(1))
    row = first.runner.snapshot().rows[0]
    assert row.laps_completed == 0
    assert row.last_lap_ns is None
    assert row.best_lap_ns is None

    first.cross(10 * S)
    still_open = first.runner.snapshot().rows[0]
    assert still_open.laps_completed == 0
    assert still_open.last_lap_ns is None
    assert still_open.best_lap_ns is None

    first.cross(15 * S)
    one = first.runner.snapshot().rows[0]
    assert one.laps_completed == 1
    assert one.last_lap_ns == 5 * S
    assert one.best_lap_ns == 5 * S
    held = first.runner.snapshot().rows[0]
    assert (held.last_lap_ns, held.best_lap_ns, held.laps_completed) == (5 * S, 5 * S, 1)

    first.cross(22 * S)
    two = first.runner.snapshot().rows[0]
    assert two.laps_completed == 2
    assert two.last_lap_ns == 7 * S
    assert two.best_lap_ns == 5 * S

    first.runner.stop()
    assert first.runner.is_finished
    finished = first.runner.snapshot().rows[0]
    assert (finished.last_lap_ns, finished.best_lap_ns, finished.laps_completed) == (
        7 * S,
        5 * S,
        2,
    )

    second = _Running(RaceId(2))
    fresh = second.runner.snapshot().rows[0]
    assert fresh.laps_completed == 0
    assert fresh.last_lap_ns is None
    assert fresh.best_lap_ns is None
    kept = first.runner.snapshot().rows[0]
    assert (kept.last_lap_ns, kept.best_lap_ns, kept.laps_completed) == (7 * S, 5 * S, 2)


def test_the_live_stage_keeps_lap_values_until_the_next_race_starts(qtbot: QtBot, env: Env) -> None:
    stage = LiveHudStage(env.runtime.translator)
    qtbot.addWidget(stage)
    stage.resize(1280, 800)
    stage.show()

    _paint(stage, _row(completed=0, last=0, best=0, times=(0,)))
    card = stage.lanes.cards[1]
    assert card.last_label.text() == "-"
    assert card.best_label.text() == "-"
    assert card.best_time_label.text() == "-"
    assert column_text(stage.table, 0, "Runden") == "0"
    assert column_text(stage.table, 0, "Letzte Runde") == "-"
    assert column_text(stage.table, 0, "Beste Runde") == "-"

    _paint(stage, _row(completed=1, last=5 * S, best=5 * S, times=(5 * S,)))
    assert card.last_label.text() == "0:05.000"
    assert card.best_label.text() == "1"
    assert card.best_time_label.text() == "5,000 s"
    assert column_text(stage.table, 0, "Runden") == "1"
    _paint(stage, _row(completed=1, last=5 * S, best=5 * S, times=(5 * S,)))
    assert card.last_label.text() == "0:05.000"
    assert column_text(stage.table, 0, "Runden") == "1"
    assert column_text(stage.table, 0, "Beste Runde") == "0:05.000"

    _paint(stage, _row(completed=2, last=7 * S, best=5 * S, times=(5 * S, 7 * S), finished=True))
    assert card.last_label.text() == "0:07.000"
    assert card.best_time_label.text() == "5,000 s"
    assert column_text(stage.table, 0, "Runden") == "2"
    assert column_text(stage.table, 0, "Letzte Runde") == "0:07.000"

    _paint(stage, _row(completed=0, last=None, best=None, times=()))
    assert card.last_label.text() == "-"
    assert card.best_label.text() == "-"
    assert card.best_time_label.text() == "-"
    assert column_text(stage.table, 0, "Runden") == "0"
    assert column_text(stage.table, 0, "Letzte Runde") == "-"
    assert column_text(stage.table, 0, "Beste Runde") == "-"


class _Running:
    def __init__(self, race_id: RaceId) -> None:
        self.bus = EventBus()
        engine = RaceEngine(
            RaceConfig(race_id, 8, (Participant(DriverId(1), 1),), _LINE),
            self.bus,
            ManualClock(),
        )
        self.runner = RaceRunner(_info(race_id), engine, self.bus)
        self.runner.start()

    def cross(self, timestamp_ns: int) -> None:
        self.bus.publish(
            SensorTriggered(
                timestamp_ns=timestamp_ns,
                source_id="manual",
                sensor_id="start_finish",
                position_id="start_finish",
                lane=1,
            )
        )


def _info(race_id: RaceId) -> RaceInfo:
    moment = datetime(2026, 5, 1, 12, 0)
    return RaceInfo(
        id=race_id,
        name="Live",
        track_id=None,
        track_name="Ring",
        lane_count=1,
        status=RaceStatus.READY,
        mode=RaceMode.LAPS,
        laps=8,
        timing_provider="simulation",
        participants=(
            ParticipantInfo(
                id=1,
                driver_id=DriverId(1),
                driver_label="Zoe",
                vehicle_id=VehicleId(1),
                vehicle_label="Porsche",
                lane=1,
                start_number=7,
            ),
        ),
        created_at=moment,
        started_at=None,
        finished_at=None,
    )


def _paint(stage: LiveHudStage, row: object) -> None:
    from slot_racing.modules.races.runner import LiveRow, RaceSnapshot

    assert isinstance(row, LiveRow)
    stage.show_standings(
        RaceSnapshot(
            race_id=RaceId(1),
            name="Live",
            track_name="Ring",
            timing_provider="simulation",
            status=RaceStatus.FINISHED if row.finished else RaceStatus.RUNNING,
            aborted=False,
            laps=8,
            elapsed_ns=20 * S,
            rows=(row,),
            source_errors=(),
        ),
        mode=RaceMode.LAPS,
        lane_count=1,
    )


def _row(
    *,
    completed: int,
    last: int | None,
    best: int | None,
    times: tuple[int, ...],
    finished: bool = False,
) -> object:
    from slot_racing.modules.races.runner import LiveRow

    return LiveRow(
        position=1,
        lane=1,
        driver_label="Zoe",
        vehicle_label="Porsche",
        start_number=7,
        current_lap=completed + 1,
        laps_completed=completed,
        last_lap_ns=last,
        total_time_ns=None if completed == 0 else 20 * S,
        best_lap_ns=best,
        finished=finished,
        lap_times_ns=times,
    )
