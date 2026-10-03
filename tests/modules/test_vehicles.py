from __future__ import annotations

import pytest

from slot_racing.core.domain import DriverId
from slot_racing.core.errors import ValidationError
from slot_racing.modules.drivers_vehicles.service import VehicleInput
from tests.modules.conftest import Env


def test_create_vehicle(env: Env) -> None:
    vehicle = env.vehicles.create_vehicle(
        VehicleInput(name=" Rennwagen ", model="Porsche 911", manufacturer="ExampleBrand")
    )
    assert vehicle.name == "Rennwagen"
    assert vehicle.manufacturer == "ExampleBrand"
    assert vehicle.driver_id is None
    assert vehicle.is_active
    assert vehicle.label == "Rennwagen (Porsche 911)"


def test_scale_and_notes_are_stored_and_can_be_cleared(env: Env) -> None:
    created = env.vehicles.create_vehicle(
        VehicleInput(name="Rennwagen", model="911", scale=" 1:32 ", notes=" neue Reifen ")
    )
    assert created.scale == "1:32"
    assert created.notes == "neue Reifen"
    assert env.vehicles.get_vehicle(created.id) == created

    changed = env.vehicles.update_vehicle(
        created.id, VehicleInput(name="Rennwagen", model="911", scale="1:24", notes="andere Räder")
    )
    assert (changed.scale, changed.notes) == ("1:24", "andere Räder")

    cleared = env.vehicles.update_vehicle(
        created.id, VehicleInput(name="Rennwagen", model="911", scale="  ", notes="")
    )
    assert cleared.scale is None
    assert cleared.notes is None


def test_scale_and_notes_reject_text_that_is_too_long(env: Env) -> None:
    with pytest.raises(ValidationError) as caught:
        env.vehicles.create_vehicle(VehicleInput(name="A", model="B", scale="1" * 17))
    assert caught.value.key == "error.vehicle.scale.too_long"
    with pytest.raises(ValidationError) as caught:
        env.vehicles.create_vehicle(VehicleInput(name="A", model="B", notes="n" * 2001))
    assert caught.value.key == "error.vehicle.notes.too_long"
    assert env.vehicles.list_vehicles() == []


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


def test_a_driver_keeps_one_favorite_and_loses_it_without_an_owner(env: Env) -> None:
    anna = env.driver("Anna")
    ben = env.driver("Ben")
    porsche = env.vehicles.create_vehicle(
        VehicleInput(name="Porsche", model="911", driver_id=anna.id, is_favorite=True)
    )
    assert porsche.is_favorite
    alfa = env.vehicles.create_vehicle(
        VehicleInput(name="Alfa", model="Giulia", driver_id=anna.id, is_favorite=True)
    )
    assert alfa.is_favorite
    stored_porsche = env.vehicles.get_vehicle(porsche.id)
    assert stored_porsche is not None and not stored_porsche.is_favorite

    ferrari = env.vehicles.create_vehicle(
        VehicleInput(name="Ferrari", model="911", driver_id=ben.id, is_favorite=True)
    )
    assert ferrari.is_favorite
    still_alfa = env.vehicles.get_vehicle(alfa.id)
    assert still_alfa is not None and still_alfa.is_favorite

    loose = env.vehicles.create_vehicle(VehicleInput(name="Ersatz", model="GT", is_favorite=True))
    assert loose.driver_id is None and not loose.is_favorite

    cleared = env.vehicles.assign_driver(alfa.id, None)
    assert cleared.driver_id is None and not cleared.is_favorite

    moved = env.vehicles.update_vehicle(
        porsche.id,
        VehicleInput(name="Porsche", model="911", driver_id=ben.id, is_favorite=True),
    )
    assert moved.driver_id == ben.id and moved.is_favorite
    stored_ferrari = env.vehicles.get_vehicle(ferrari.id)
    assert stored_ferrari is not None and not stored_ferrari.is_favorite


def test_vehicle_in_a_race_cannot_be_deleted(env: Env) -> None:
    driver_id, vehicle_id = env.pair(1)
    race = env.races.create_race("R", env.track_id(), 3)
    env.races.add_participant(race.id, driver_id, vehicle_id, 1)
    with pytest.raises(ValidationError) as caught:
        env.vehicles.delete_vehicle(vehicle_id)
    assert caught.value.key == "error.vehicle.in_use"
