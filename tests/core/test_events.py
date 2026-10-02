from dataclasses import FrozenInstanceError, dataclass

import pytest

from slot_racing.core.domain import DriverId, RaceId
from slot_racing.core.events import (
    Event,
    EventBus,
    LapCompleted,
    RaceStarting,
    SensorTriggered,
)


@dataclass(frozen=True, slots=True, kw_only=True)
class _Custom(Event):
    value: int = 0


def _sensor(timestamp_ns: int = 1) -> SensorTriggered:
    return SensorTriggered(
        timestamp_ns=timestamp_ns, source_id="s", sensor_id="a", position_id="p", lane=1
    )


def test_events_are_immutable() -> None:
    event = _sensor()
    with pytest.raises(FrozenInstanceError):
        event.lane = 2  # type: ignore[misc]


@pytest.mark.parametrize("bad", [1.5, "1", True])
def test_timestamp_must_be_integer_nanoseconds(bad: object) -> None:
    with pytest.raises(TypeError):
        SensorTriggered(timestamp_ns=bad, source_id="s", sensor_id="a", position_id="p", lane=1)  # type: ignore[arg-type]


def test_negative_times_are_rejected() -> None:
    with pytest.raises(ValueError, match="negative"):
        RaceStarting(timestamp_ns=-1, race_id=RaceId(1))


def test_all_ns_fields_are_validated() -> None:
    with pytest.raises(TypeError):
        LapCompleted(
            timestamp_ns=1,
            race_id=RaceId(1),
            driver_id=DriverId(1),
            lane=1,
            lap_number=1,
            lap_time_ns=1.5,  # type: ignore[arg-type]
            race_time_ns=2,
        )


def test_handlers_receive_matching_events_in_subscription_order() -> None:
    bus = EventBus()
    calls: list[str] = []
    bus.subscribe(SensorTriggered, lambda e: calls.append("first"))
    bus.subscribe(SensorTriggered, lambda e: calls.append("second"))
    bus.subscribe(RaceStarting, lambda e: calls.append("other"))
    bus.publish(_sensor())
    assert calls == ["first", "second"]


def test_subscribing_to_base_class_receives_all_events() -> None:
    bus = EventBus()
    seen: list[Event] = []
    bus.subscribe(Event, seen.append)
    bus.publish(_sensor())
    bus.publish(_Custom(timestamp_ns=2))
    assert [type(e) for e in seen] == [SensorTriggered, _Custom]


def test_cancelled_subscription_stops_receiving() -> None:
    bus = EventBus()
    seen: list[Event] = []
    subscription = bus.subscribe(SensorTriggered, seen.append)
    bus.publish(_sensor())
    subscription.cancel()
    bus.publish(_sensor())
    assert len(seen) == 1
    assert not subscription.active


def test_failing_handler_does_not_affect_publisher_or_other_handlers() -> None:
    errors: list[Exception] = []
    bus = EventBus(on_error=lambda _event, _handler, error: errors.append(error))
    seen: list[Event] = []

    def broken(_event: SensorTriggered) -> None:
        raise RuntimeError("boom")

    bus.subscribe(SensorTriggered, broken)
    bus.subscribe(SensorTriggered, seen.append)
    bus.publish(_sensor())
    assert len(seen) == 1
    assert [str(e) for e in errors] == ["boom"]


def test_handler_may_subscribe_and_publish_while_handling() -> None:
    bus = EventBus()
    seen: list[str] = []

    def on_sensor(_event: SensorTriggered) -> None:
        bus.subscribe(RaceStarting, lambda e: seen.append("late"))
        bus.publish(RaceStarting(timestamp_ns=2, race_id=RaceId(1)))

    bus.subscribe(SensorTriggered, on_sensor)
    bus.publish(_sensor())
    assert seen == ["late"]
