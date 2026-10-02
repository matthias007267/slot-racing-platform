"""The editing model and the test session behind the timing configuration UI."""

from __future__ import annotations

import pytest

from carrera.core.clock import NANOS_PER_SECOND as S
from carrera.core.clock import ManualClock
from carrera.core.domain import TimingPositionType, default_timing_setup
from carrera.core.errors import ValidationError
from carrera.core.timing import (
    SensorSink,
    TimingSessionSpec,
    TimingSource,
    TimingSourceFactory,
)
from carrera.modules.timing.simulation import SimulationTimingFactory
from carrera.modules.tracks.timing_editor import TimingDraft
from carrera.modules.tracks.timing_test import TimingTestSession


def draft() -> TimingDraft:
    return TimingDraft.from_setup(default_timing_setup())


def ids(d: TimingDraft) -> list[str]:
    return [e.position_id for e in d.entries]


def test_draft_mirrors_the_setup_and_builds_it_again() -> None:
    assert draft().to_setup() == default_timing_setup()


def test_add_sector_uses_free_ids_and_appends() -> None:
    d = draft()
    entry = d.add_sector()
    assert (entry.position_id, entry.sensor_id) == ("sector_3", "sensor_03")
    assert ids(d)[-1] == "sector_3"
    d.remove("sector_1")
    assert d.add_sector().position_id == "sector_1"
    assert len(d.to_setup().layout.positions) == 4


def test_remove_a_sector_and_keep_start_finish() -> None:
    d = draft()
    d.remove("sector_2")
    assert ids(d) == ["start_finish", "sector_1"]
    with pytest.raises(ValidationError) as error:
        d.remove("start_finish")
    assert error.value.key == "error.timing.start_finish_fixed"
    assert ids(d)[0] == "start_finish"


def test_all_sectors_can_be_removed() -> None:
    d = draft()
    d.remove("sector_1")
    d.remove("sector_2")
    assert [p.type for p in d.to_setup().layout.positions] == [TimingPositionType.START_FINISH]


def test_move_changes_the_order_but_never_before_start_finish() -> None:
    d = draft()
    assert d.move("sector_2", -1)
    assert ids(d) == ["start_finish", "sector_2", "sector_1"]
    assert not d.move("sector_2", -1)
    assert d.move("sector_2", 5)
    assert ids(d) == ["start_finish", "sector_1", "sector_2"]
    assert not d.move("sector_2", 1)
    with pytest.raises(ValidationError):
        d.move("start_finish", 1)
    assert [p.order for p in d.to_setup().layout.positions] == [1, 2, 3]


def test_unknown_position_is_a_validation_error() -> None:
    with pytest.raises(ValidationError):
        draft().entry("nope")


def test_update_changes_the_sensor_assignment() -> None:
    d = draft()
    d.update(
        "sector_1",
        name=" Kurve ",
        sensor_id=" S-17 ",
        sensor_name="Lichtschranke",
        hardware_id="GPIO 17",
        active=True,
    )
    setup = d.to_setup()
    assert setup.layout.position("sector_1").name == "Kurve"
    assert setup.sensor_at("sector_1").id == "S-17"
    assert setup.sensor_at("sector_1").hardware_id == "GPIO 17"


def test_an_invalid_update_is_rejected_and_leaves_the_draft_unchanged() -> None:
    d = draft()
    before = d.to_setup()
    with pytest.raises(ValidationError) as error:
        d.update(
            "sector_1",
            name=None,
            sensor_id="start_finish",
            sensor_name=None,
            hardware_id=None,
            active=True,
        )
    assert error.value.key == "error.timing.sensor_duplicate"
    assert d.to_setup() == before
    d.update("sector_1", name=None, sensor_id="a", sensor_name=None, hardware_id="H", active=True)
    with pytest.raises(ValidationError) as hardware:
        d.update(
            "sector_2", name=None, sensor_id="b", sensor_name=None, hardware_id="H", active=True
        )
    assert hardware.value.key == "error.timing.hardware_duplicate"


def test_a_draft_with_blank_sensor_cannot_become_a_setup() -> None:
    d = draft()
    d.add_sector().sensor_id = ""
    with pytest.raises(ValidationError) as error:
        d.to_setup()
    assert error.value.key == "error.timing.sensor_blank"


def test_default_sensor_ids_are_assigned_by_index() -> None:
    d = draft()
    d.add_sector()
    d.move("sector_3", -2)
    d.assign_default_sensor_ids()
    assert [e.sensor_id for e in d.entries] == [
        "start_finish",
        "sensor_01",
        "sensor_02",
        "sensor_03",
    ]


def make_session(draft_: TimingDraft | None = None) -> tuple[TimingTestSession, ManualClock]:
    clock = ManualClock()
    factories: list[TimingSourceFactory] = [SimulationTimingFactory(clock)]
    setup = (draft_ or draft()).to_setup()
    return TimingTestSession(setup, lambda: factories, clock), clock


def test_each_trigger_creates_one_event_in_layout_order() -> None:
    session, clock = make_session()
    assert session.count == 0 and session.last is None and session.expected_next == "sector_1"
    session.trigger()
    clock.advance(2 * S)
    session.trigger()
    assert session.count == 2
    last = session.last
    assert last is not None
    assert (last.number, last.position_id, last.sensor_id, last.time_ns) == (
        2,
        "sector_2",
        "sensor_02",
        2 * S,
    )
    assert session.detected_order == ("sector_1", "sector_2")
    assert session.in_order and session.expected_next == "start_finish"


def test_a_lap_runs_through_all_positions_and_starts_over() -> None:
    d = draft()
    d.add_sector()
    session, _ = make_session(d)
    session.trigger_lap()
    assert session.detected_order == ("sector_1", "sector_2", "sector_3", "start_finish")
    session.trigger()
    assert session.last is not None and session.last.position_id == "sector_1"
    assert session.in_order


def test_reset_clears_events_and_restarts_the_order() -> None:
    session, _ = make_session()
    session.trigger_lap()
    session.reset()
    assert session.count == 0 and not session.is_running
    session.trigger()
    assert session.detected_order == ("sector_1",)


def test_the_test_needs_a_simulating_source() -> None:
    session = TimingTestSession(default_timing_setup(), lambda: [], ManualClock())
    with pytest.raises(ValidationError) as error:
        session.trigger()
    assert error.value.key == "error.timing.test_no_source"


class PassiveSource(TimingSource):
    @property
    def source_id(self) -> str:
        return "passive"

    @property
    def is_running(self) -> bool:
        return False

    def start(self, sink: SensorSink) -> None:
        return None

    def stop(self) -> None:
        return None


class PassiveFactory(TimingSourceFactory):
    name = "passive"
    display_name = "Passive"

    def create_source(self, spec: TimingSessionSpec) -> TimingSource:
        return PassiveSource()


def test_a_source_that_cannot_be_simulated_is_reported() -> None:
    session = TimingTestSession(default_timing_setup(), lambda: [PassiveFactory()], ManualClock())
    with pytest.raises(ValidationError) as error:
        session.trigger()
    assert error.value.key == "error.timing.test_not_simulatable"
