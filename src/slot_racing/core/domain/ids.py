"""Typed identifiers. They match the integer primary keys used by the database."""

from typing import NewType

DriverId = NewType("DriverId", int)
VehicleId = NewType("VehicleId", int)
TrackId = NewType("TrackId", int)
RaceId = NewType("RaceId", int)
