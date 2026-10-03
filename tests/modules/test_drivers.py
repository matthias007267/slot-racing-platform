from __future__ import annotations

import pytest

from slot_racing.core.errors import ValidationError
from slot_racing.modules.drivers_vehicles.models import Driver
from slot_racing.modules.drivers_vehicles.service import DriverInput, VehicleInput
from tests.modules.conftest import Env


def test_create_and_list_driver(env: Env) -> None:
    driver = env.drivers.create_driver(
        DriverInput(name="  Anna Schmidt ", display_name="Anni", start_number=7)
    )
    assert driver.name == "Anna Schmidt"
    assert driver.label == "Anni"
    assert driver.is_active
    assert driver.created_at is not None and driver.updated_at is not None
    assert [d.id for d in env.drivers.list_drivers()] == [driver.id]
    assert env.drivers.get_driver(driver.id) == driver


def test_edit_driver(env: Env) -> None:
    driver = env.driver("Anna", number=3)
    updated = env.drivers.update_driver(
        driver.id, DriverInput(name="Anna B", display_name="", start_number=3)
    )
    assert updated.name == "Anna B"
    assert updated.display_name is None
    assert updated.start_number == 3  # keeping the own number is not a conflict


@pytest.mark.parametrize("name", ["", "   "])
def test_name_is_required(env: Env, name: str) -> None:
    with pytest.raises(ValidationError) as caught:
        env.drivers.create_driver(DriverInput(name=name))
    assert caught.value.key == "error.driver.name.required"


@pytest.mark.parametrize("number", [0, -1, 1000])
def test_start_number_must_be_in_range(env: Env, number: int) -> None:
    with pytest.raises(ValidationError) as caught:
        env.drivers.create_driver(DriverInput(name="X", start_number=number))
    assert caught.value.key == "error.driver.start_number.range"


def test_start_number_must_be_unique(env: Env) -> None:
    first = env.driver("Anna", number=5)
    with pytest.raises(ValidationError) as caught:
        env.drivers.create_driver(DriverInput(name="Ben", start_number=5))
    assert caught.value.key == "error.driver.start_number_taken"
    assert caught.value.params["number"] == 5
    other = env.driver("Cem", number=6)
    with pytest.raises(ValidationError):
        env.drivers.update_driver(other.id, DriverInput(name="Cem", start_number=5))
    assert env.drivers.get_driver(first.id) is not None
    assert len(env.drivers.list_drivers()) == 2


def test_start_number_is_optional_and_may_repeat_when_unset(env: Env) -> None:
    env.driver("Anna")
    env.driver("Ben")
    assert len(env.drivers.list_drivers()) == 2


def test_deactivated_driver_is_hidden_from_active_listing(env: Env) -> None:
    driver = env.driver()
    env.drivers.set_active(driver.id, False)
    assert env.drivers.list_drivers(active_only=True) == []
    assert not env.drivers.list_drivers()[0].is_active
    env.drivers.set_active(driver.id, True)
    assert len(env.drivers.list_drivers(active_only=True)) == 1


def test_delete_driver_unassigns_vehicles(env: Env) -> None:
    driver = env.driver()
    vehicle = env.vehicle(driver_id=driver.id)
    env.drivers.delete_driver(driver.id)
    assert env.drivers.get_driver(driver.id) is None
    stored = env.vehicles.get_vehicle(vehicle.id)
    assert stored is not None and stored.driver_id is None


def test_driver_in_a_race_cannot_be_deleted(env: Env) -> None:
    driver_id, vehicle_id = env.pair(1)
    race = env.races.create_race("R", env.track_id(), 3)
    env.races.add_participant(race.id, driver_id, vehicle_id, 1)
    with pytest.raises(ValidationError) as caught:
        env.drivers.delete_driver(driver_id)
    assert caught.value.key == "error.driver.in_use"
    assert env.drivers.get_driver(driver_id) is not None


def test_unknown_driver_is_reported(env: Env) -> None:
    driver = env.driver()
    env.drivers.delete_driver(driver.id)
    with pytest.raises(ValidationError) as caught:
        env.drivers.set_active(driver.id, False)
    assert caught.value.key == "error.driver.not_found"


def test_an_already_stored_text_start_number_can_be_kept(env: Env) -> None:
    with env.runtime.database.session() as session:
        session.add(Driver(name="Bee", start_number="B", is_active=True))
    assert env.drivers.defined_start_numbers() == ["B"]
    stored = next(driver for driver in env.drivers.list_drivers() if driver.name == "Bee")
    assert stored.start_number == "B"
    updated = env.drivers.update_driver(
        stored.id, DriverInput(name="Bee", display_name="Biene", start_number="B")
    )
    assert updated.start_number == "B"
    vehicle = env.vehicles.create_vehicle(VehicleInput(name="Wagen", model="GT", start_number="B"))
    assert vehicle.start_number == "B"
    with pytest.raises(ValidationError) as caught:
        env.drivers.create_driver(DriverInput(name="Neu", start_number="C"))
    assert caught.value.key == "error.driver.start_number.unknown"
