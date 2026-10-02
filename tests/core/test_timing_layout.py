"""Domain rules of the timing layout and the sensors assigned to it."""

from __future__ import annotations

import pytest

from carrera.core.domain import (
    TimingLayout,
    TimingPosition,
    TimingPositionType,
    TimingSensor,
    TimingSetup,
    default_timing_setup,
)
from carrera.core.errors import ValidationError

SF = TimingPositionType.START_FINISH
SECTOR = TimingPositionType.SECTOR


def position(id_: str, type_: TimingPositionType, order: int) -> TimingPosition:
    return TimingPosition(id_, type_, order)


def key_of(error: pytest.ExceptionInfo[ValidationError]) -> str:
    return error.value.key


def test_valid_layout_with_start_finish_and_sectors() -> None:
    layout = TimingLayout(
        (position("sf", SF, 1), position("a", SECTOR, 2), position("b", SECTOR, 3))
    )
    assert [p.label for p in layout.positions] == ["START_FINISH", "SECTOR_1", "SECTOR_2"]
    assert [p.id for p in layout.lap_sequence] == ["a", "b", "sf"]
    assert layout.position("b").sector_number == 2
    assert layout.position("sf").sector_number is None


def test_a_layout_without_sectors_is_valid() -> None:
    layout = TimingLayout((position("sf", SF, 1),))
    assert [p.id for p in layout.lap_sequence] == ["sf"]
    setup = TimingSetup.for_layout(layout)
    assert setup.sensor_at("sf").id == "sf"


def test_the_sector_count_is_not_limited() -> None:
    layout = TimingLayout.from_position_ids([f"p{i}" for i in range(25)])
    assert len(layout.positions) == 25
    assert layout.positions[-1].label == "SECTOR_24"


def test_a_layout_needs_at_least_one_position() -> None:
    with pytest.raises(ValidationError) as error:
        TimingLayout(())
    assert key_of(error) == "error.timing.no_positions"


def test_start_finish_is_required() -> None:
    with pytest.raises(ValidationError) as error:
        TimingLayout((position("a", SECTOR, 1), position("b", SECTOR, 2)))
    assert key_of(error) == "error.timing.start_finish_missing"


def test_start_finish_must_be_unique() -> None:
    with pytest.raises(ValidationError) as error:
        TimingLayout((position("a", SF, 1), position("b", SF, 2)))
    assert key_of(error) == "error.timing.start_finish_duplicate"


def test_start_finish_must_be_first() -> None:
    with pytest.raises(ValidationError) as error:
        TimingLayout((position("a", SECTOR, 1), position("sf", SF, 2)))
    assert key_of(error) == "error.timing.start_finish_first"


def test_position_ids_must_be_unique() -> None:
    with pytest.raises(ValidationError) as error:
        TimingLayout((position("sf", SF, 1), position("a", SECTOR, 2), position("a", SECTOR, 3)))
    assert key_of(error) == "error.timing.position_duplicate"


@pytest.mark.parametrize("orders", [(1, 1, 2), (1, 3, 4), (2, 3, 4), (1, 3, 2)])
def test_orders_must_be_unique_and_consecutive(orders: tuple[int, int, int]) -> None:
    kinds = (SF, SECTOR, SECTOR)
    positions = tuple(
        position(f"p{i}", kind, order)
        for i, (kind, order) in enumerate(zip(kinds, orders, strict=True))
    )
    with pytest.raises(ValidationError) as error:
        TimingLayout(positions)
    assert key_of(error) == "error.timing.order_invalid"


def test_blank_position_ids_are_rejected() -> None:
    with pytest.raises(ValidationError) as error:
        TimingLayout((position("sf", SF, 1), position("  ", SECTOR, 2)))
    assert key_of(error) == "error.timing.position_blank"


def test_setup_binds_one_sensor_to_each_position() -> None:
    layout = TimingLayout.from_position_ids(["sf", "a"])
    setup = TimingSetup(
        layout,
        (TimingSensor("s-a", "a", hardware_id="GPIO17"), TimingSensor("s-sf", "sf")),
    )
    assert [s.id for s in setup.sensors] == ["s-sf", "s-a"]
    assert setup.sensor_at("a").hardware_id == "GPIO17"
    assert setup.order_of("s-a") == 2


def test_hardware_id_is_not_the_logical_position() -> None:
    layout = TimingLayout.from_position_ids(["sf", "a"])
    setup = TimingSetup(
        layout,
        (TimingSensor("s-sf", "sf", hardware_id="GPIO 2"), TimingSensor("s-a", "a")),
    )
    assert setup.sensor("s-sf").position_id == "sf"
    assert setup.sensor("s-sf").hardware_id == "GPIO 2"


def test_sensor_ids_must_be_unique() -> None:
    layout = TimingLayout.from_position_ids(["sf", "a"])
    with pytest.raises(ValidationError) as error:
        TimingSetup(layout, (TimingSensor("x", "sf"), TimingSensor("x", "a")))
    assert key_of(error) == "error.timing.sensor_duplicate"


def test_hardware_ids_must_be_unique_within_a_layout() -> None:
    layout = TimingLayout.from_position_ids(["sf", "a"])
    with pytest.raises(ValidationError) as error:
        TimingSetup(
            layout,
            (TimingSensor("x", "sf", hardware_id="G1"), TimingSensor("y", "a", hardware_id="G1")),
        )
    assert key_of(error) == "error.timing.hardware_duplicate"


def test_every_position_needs_exactly_one_sensor() -> None:
    layout = TimingLayout.from_position_ids(["sf", "a"])
    with pytest.raises(ValidationError) as missing:
        TimingSetup(layout, (TimingSensor("x", "sf"),))
    assert key_of(missing) == "error.timing.position_without_sensor"
    with pytest.raises(ValidationError) as twice:
        TimingSetup(
            layout, (TimingSensor("x", "sf"), TimingSensor("y", "sf"), TimingSensor("z", "a"))
        )
    assert key_of(twice) == "error.timing.position_multiple_sensors"


def test_a_sensor_must_belong_to_a_known_position() -> None:
    layout = TimingLayout.from_position_ids(["sf"])
    with pytest.raises(ValidationError) as error:
        TimingSetup(layout, (TimingSensor("x", "sf"), TimingSensor("y", "nowhere")))
    assert key_of(error) == "error.timing.sensor_unknown_position"


def test_blank_sensor_ids_are_rejected() -> None:
    layout = TimingLayout.from_position_ids(["sf"])
    with pytest.raises(ValidationError) as error:
        TimingSetup(layout, (TimingSensor(" ", "sf"),))
    assert key_of(error) == "error.timing.sensor_blank"


def test_inactive_sensors_cannot_be_used_as_timing_source() -> None:
    layout = TimingLayout.from_position_ids(["sf", "a"])
    setup = TimingSetup(layout, (TimingSensor("x", "sf"), TimingSensor("y", "a", active=False)))
    assert not setup.is_usable
    with pytest.raises(ValidationError) as error:
        setup.ensure_usable()
    assert key_of(error) == "error.timing.sensor_inactive"
    setup.with_sensor(TimingSensor("y", "a")).ensure_usable()


def test_default_setup_is_start_finish_and_two_sectors() -> None:
    setup = default_timing_setup()
    assert [p.id for p in setup.layout.positions] == ["start_finish", "sector_1", "sector_2"]
    assert setup.is_usable
