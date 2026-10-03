"""Driver and vehicle management. Implements the catalogs other modules look up."""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from slot_racing.core.catalog import DriverCatalog, DriverInfo, VehicleCatalog, VehicleInfo
from slot_racing.core.domain import DriverId, VehicleId
from slot_racing.core.errors import ValidationError
from slot_racing.core.storage import Database
from slot_racing.modules.drivers_vehicles.models import Driver, Vehicle

MAX_START_NUMBER = 999
MAX_SCALE_LENGTH = 16
MAX_NOTES_LENGTH = 2000


@dataclass(frozen=True, slots=True)
class DriverInput:
    name: str
    display_name: str | None = None
    start_number: int | str | None = None


@dataclass(frozen=True, slots=True)
class VehicleInput:
    name: str
    model: str
    manufacturer: str | None = None
    scale: str | None = None
    notes: str | None = None
    start_number: int | str | None = None
    driver_id: DriverId | None = None


def _required(value: str, key: str, limit: int) -> str:
    text = value.strip()
    if not text:
        raise ValidationError(f"{key}.required")
    if len(text) > limit:
        raise ValidationError(f"{key}.too_long", limit=limit)
    return text


def _optional(value: str | None, key: str, limit: int) -> str | None:
    text = (value or "").strip()
    if len(text) > limit:
        raise ValidationError(f"{key}.too_long", limit=limit)
    return text or None


def _defined_start_tokens(session: Session) -> set[str]:
    """Text form of every start number already stored on a driver or a vehicle."""
    tokens: set[str] = set()
    for column in (Driver.start_number, Vehicle.start_number):
        for value in session.scalars(select(column).where(column.is_not(None))):
            if value is not None:
                tokens.add(str(value))
    return tokens


def ordered_start_numbers(session: Session) -> list[str]:
    """Defined start numbers, numeric ones first, then any other stored token."""

    def order(token: str) -> tuple[int, int | str]:
        if token.isdigit():
            return (0, int(token))
        return (1, token.casefold())

    return sorted(_defined_start_tokens(session), key=order)


def _coerce_start_number(value: int | str | None, key: str, defined: set[str]) -> int | str | None:
    """Keep a stored token as it is. A new numeric number still has to fit the existing range."""
    if value is None:
        return None
    token = str(value).strip()
    if not token:
        return None
    if token in defined:
        return int(token) if token.isdigit() else token
    number = _integer_token(token)
    if number is not None:
        if 1 <= number <= MAX_START_NUMBER:
            return number
        raise ValidationError(f"{key}.range", maximum=MAX_START_NUMBER)
    raise ValidationError(f"{key}.unknown", number=token)


def _integer_token(token: str) -> int | None:
    digits = token[1:] if token.startswith("-") else token
    if digits.isdigit():
        return int(token)
    return None


def _driver_info(driver: Driver) -> DriverInfo:
    return DriverInfo(
        id=DriverId(driver.id),
        name=driver.name,
        display_name=driver.display_name,
        start_number=driver.start_number,
        is_active=driver.is_active,
        created_at=driver.created_at,
        updated_at=driver.updated_at,
    )


def _vehicle_info(vehicle: Vehicle) -> VehicleInfo:
    return VehicleInfo(
        id=VehicleId(vehicle.id),
        name=vehicle.name,
        model=vehicle.model,
        manufacturer=vehicle.manufacturer,
        scale=vehicle.scale,
        notes=vehicle.notes,
        start_number=vehicle.start_number,
        is_active=vehicle.is_active,
        driver_id=None if vehicle.driver_id is None else DriverId(vehicle.driver_id),
        created_at=vehicle.created_at,
        updated_at=vehicle.updated_at,
    )


class DriverService(DriverCatalog):
    def __init__(self, database: Database) -> None:
        self._database = database

    def list_drivers(self, *, active_only: bool = False) -> list[DriverInfo]:
        query = select(Driver).order_by(Driver.name, Driver.id)
        if active_only:
            query = query.where(Driver.is_active.is_(True))
        with self._database.session() as session:
            return [_driver_info(driver) for driver in session.scalars(query)]

    def get_driver(self, driver_id: DriverId) -> DriverInfo | None:
        with self._database.session() as session:
            driver = session.get(Driver, driver_id)
            return None if driver is None else _driver_info(driver)

    def defined_start_numbers(self) -> list[str]:
        """Start numbers that already exist. The selection offers only these."""
        with self._database.session() as session:
            return ordered_start_numbers(session)

    def create_driver(self, data: DriverInput) -> DriverInfo:
        with self._database.session() as session:
            name, display_name, start_number = self._validate(data, session)
            self._check_start_number_free(session, start_number, own_id=None)
            driver = Driver(name=name, display_name=display_name, start_number=start_number)
            session.add(driver)
            self._flush(session, start_number)
            return _driver_info(driver)

    def update_driver(self, driver_id: DriverId, data: DriverInput) -> DriverInfo:
        with self._database.session() as session:
            name, display_name, start_number = self._validate(data, session)
            driver = self._load(session, driver_id)
            self._check_start_number_free(session, start_number, own_id=driver.id)
            driver.name = name
            driver.display_name = display_name
            driver.start_number = start_number
            self._flush(session, start_number)
            return _driver_info(driver)

    def set_active(self, driver_id: DriverId, active: bool) -> DriverInfo:
        with self._database.session() as session:
            driver = self._load(session, driver_id)
            driver.is_active = active
            session.flush()
            return _driver_info(driver)

    def delete_driver(self, driver_id: DriverId) -> None:
        """Delete a driver. Fails while the driver took part in a race; deactivate instead."""
        try:
            with self._database.session() as session:
                session.delete(self._load(session, driver_id))
                session.flush()
        except IntegrityError as error:
            raise ValidationError("error.driver.in_use") from error

    @staticmethod
    def _validate(data: DriverInput, session: Session) -> tuple[str, str | None, int | str | None]:
        return (
            _required(data.name, "error.driver.name", 100),
            _optional(data.display_name, "error.driver.display_name", 50),
            _coerce_start_number(
                data.start_number,
                "error.driver.start_number",
                _defined_start_tokens(session),
            ),
        )

    @staticmethod
    def _load(session: Session, driver_id: int) -> Driver:
        driver = session.get(Driver, driver_id)
        if driver is None:
            raise ValidationError("error.driver.not_found")
        return driver

    @staticmethod
    def _check_start_number_free(
        session: Session, number: int | str | None, own_id: int | None
    ) -> None:
        if number is None:
            return
        owner = session.scalar(select(Driver).where(Driver.start_number == number))
        if owner is not None and owner.id != own_id:
            raise ValidationError("error.driver.start_number_taken", number=number)

    @staticmethod
    def _flush(session: Session, start_number: int | str | None) -> None:
        try:
            session.flush()
        except IntegrityError as error:
            raise ValidationError("error.driver.start_number_taken", number=start_number) from error


class VehicleService(VehicleCatalog):
    def __init__(self, database: Database) -> None:
        self._database = database

    def list_vehicles(
        self, *, active_only: bool = False, driver_id: DriverId | None = None
    ) -> list[VehicleInfo]:
        query = select(Vehicle).order_by(Vehicle.name, Vehicle.id)
        if active_only:
            query = query.where(Vehicle.is_active.is_(True))
        if driver_id is not None:
            query = query.where(Vehicle.driver_id == driver_id)
        with self._database.session() as session:
            return [_vehicle_info(vehicle) for vehicle in session.scalars(query)]

    def get_vehicle(self, vehicle_id: VehicleId) -> VehicleInfo | None:
        with self._database.session() as session:
            vehicle = session.get(Vehicle, vehicle_id)
            return None if vehicle is None else _vehicle_info(vehicle)

    def create_vehicle(self, data: VehicleInput) -> VehicleInfo:
        with self._database.session() as session:
            values = self._validate(data, session)
            self._check_driver(session, data.driver_id, current=None)
            vehicle = Vehicle(driver_id=data.driver_id, **values)
            session.add(vehicle)
            session.flush()
            return _vehicle_info(vehicle)

    def update_vehicle(self, vehicle_id: VehicleId, data: VehicleInput) -> VehicleInfo:
        with self._database.session() as session:
            values = self._validate(data, session)
            vehicle = self._load(session, vehicle_id)
            self._check_driver(session, data.driver_id, current=vehicle.driver_id)
            for field, value in values.items():
                setattr(vehicle, field, value)
            vehicle.driver_id = data.driver_id
            session.flush()
            return _vehicle_info(vehicle)

    def assign_driver(self, vehicle_id: VehicleId, driver_id: DriverId | None) -> VehicleInfo:
        """Give the vehicle to a driver, or take it away again with ``None``."""
        with self._database.session() as session:
            vehicle = self._load(session, vehicle_id)
            self._check_driver(session, driver_id, current=vehicle.driver_id)
            vehicle.driver_id = driver_id
            session.flush()
            return _vehicle_info(vehicle)

    def set_active(self, vehicle_id: VehicleId, active: bool) -> VehicleInfo:
        with self._database.session() as session:
            vehicle = self._load(session, vehicle_id)
            vehicle.is_active = active
            session.flush()
            return _vehicle_info(vehicle)

    def delete_vehicle(self, vehicle_id: VehicleId) -> None:
        """Delete a vehicle. Fails while it took part in a race; deactivate instead."""
        try:
            with self._database.session() as session:
                session.delete(self._load(session, vehicle_id))
                session.flush()
        except IntegrityError as error:
            raise ValidationError("error.vehicle.in_use") from error

    @staticmethod
    def _validate(data: VehicleInput, session: Session) -> dict[str, str | int | None]:
        return {
            "name": _required(data.name, "error.vehicle.name", 100),
            "model": _required(data.model, "error.vehicle.model", 100),
            "manufacturer": _optional(data.manufacturer, "error.vehicle.manufacturer", 100),
            "scale": _optional(data.scale, "error.vehicle.scale", MAX_SCALE_LENGTH),
            "notes": _optional(data.notes, "error.vehicle.notes", MAX_NOTES_LENGTH),
            "start_number": _coerce_start_number(
                data.start_number,
                "error.vehicle.start_number",
                _defined_start_tokens(session),
            ),
        }

    @staticmethod
    def _load(session: Session, vehicle_id: int) -> Vehicle:
        vehicle = session.get(Vehicle, vehicle_id)
        if vehicle is None:
            raise ValidationError("error.vehicle.not_found")
        return vehicle

    @staticmethod
    def _check_driver(session: Session, driver_id: int | None, current: int | None) -> None:
        """The driver must exist and be active. Keeping the current owner is always allowed."""
        if driver_id is None or driver_id == current:
            return
        driver = session.get(Driver, driver_id)
        if driver is None:
            raise ValidationError("error.driver.not_found")
        if not driver.is_active:
            raise ValidationError("error.vehicle.driver_inactive", driver=driver.name)
