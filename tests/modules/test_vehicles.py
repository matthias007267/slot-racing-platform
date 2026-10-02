from __future__ import annotations

import pytest

from carrera.core.domain import DriverId
from carrera.core.errors import ValidationError
from carrera.modules.drivers_vehicles.service import VehicleInput
from tests.modules.conftest import Env


def test_create_vehicle(env: Env) -> None:
    vehicle = env.vehicles.create_vehicle(
        VehicleInput(name=" Rennwagen ", model="Porsche 911", manufacturer="Carrera")
    )
    assert vehicle.name == "Rennwagen"
    assert vehicle.manufacturer == "Carrera"
    assert vehicle.driver_id is None
    assert vehicle.is_active
    assert vehicle.label == "Rennwagen (Porsche 911)"


def test_vehicle_requires_name_and_model(env: Env) -> None:
    with pytest.raises(ValidationError) as caught:
        env.vehicles.create_vehicle(VehicleInput(name="", model="x"))
    assert caught.value.key == "error.vehicle.name.required"
    with pytest.raises(ValidationError) as caught:
        env.vehicles.create_vehicle(VehicleInput(name="x", model=" "))
    assert caught.value.key == "error.vehicle.model.required"


def test_assign_and_remove_driver(env: Env) -> None:
    driver = env.driver()
    vehicle = env.vehicle()
    assigned = env.vehicles.assign_driver(vehicle.id, driver.id)
    assert assigned.driver_id == driver.id
    assert [v.id for v in env.vehicles.list_vehicles(driver_id=driver.id)] == [vehicle.id]
    removed = env.vehicles.assign_driver(vehicle.id, None)
    assert removed.driver_id is None
    assert env.vehicles.list_vehicles(driver_id=driver.id) == []


def test_driver_may_own_several_vehicles(env: Env) -> None:
    driver = env.driver()
    env.vehicle("A", driver_id=driver.id)
    env.vehicle("B", driver_id=driver.id)
    assert len(env.vehicles.list_vehicles(driver_id=driver.id)) == 2


def test_cannot_assign_unknown_or_inactive_driver(env: Env) -> None:
    vehicle = env.vehicle()
    with pytest.raises(ValidationError) as caught:
        env.vehicles.assign_driver(vehicle.id, DriverId(999))
    assert caught.value.key == "error.driver.not_found"
    driver = env.driver()
    env.drivers.set_active(driver.id, False)
    with pytest.raises(ValidationError) as caught:
        env.vehicles.assign_driver(vehicle.id, driver.id)
    assert caught.value.key == "error.vehicle.driver_inactive"


def test_vehicle_keeps_owner_that_became_inactive(env: Env) -> None:
    driver = env.driver()
    vehicle = env.vehicle(driver_id=driver.id)
    env.drivers.set_active(driver.id, False)
    updated = env.vehicles.update_vehicle(
        vehicle.id, VehicleInput(name="Neu", model="911", driver_id=driver.id)
    )
    assert updated.driver_id == driver.id
    assert updated.name == "Neu"


def test_deactivate_and_delete_vehicle(env: Env) -> None:
    vehicle = env.vehicle()
    env.vehicles.set_active(vehicle.id, False)
    assert env.vehicles.list_vehicles(active_only=True) == []
    env.vehicles.delete_vehicle(vehicle.id)
    assert env.vehicles.get_vehicle(vehicle.id) is None


def test_vehicle_in_a_race_cannot_be_deleted(env: Env) -> None:
    driver_id, vehicle_id = env.pair(1)
    race = env.races.create_race("R", env.track_id(), 3)
    env.races.add_participant(race.id, driver_id, vehicle_id, 1)
    with pytest.raises(ValidationError) as caught:
        env.vehicles.delete_vehicle(vehicle_id)
    assert caught.value.key == "error.vehicle.in_use"
