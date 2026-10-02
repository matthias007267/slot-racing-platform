"""Standardized events exchanged between modules.

Timing sources produce :class:`SensorTriggered`. The race engine turns those into the lap,
sector and race events. Everything else (audio, statistics, ...) only listens.
"""

from __future__ import annotations

from dataclasses import dataclass

from carrera.core.domain.ids import DriverId, RaceId
from carrera.core.domain.race import Participant, ParticipantResult
from carrera.core.events.base import Event


@dataclass(frozen=True, slots=True, kw_only=True)
class SensorTriggered(Event):
    """A car passed a timing position. The origin (camera, Pi, simulation) is irrelevant.

    ``sensor_id`` names the sensor that fired, ``position_id`` the logical position it stands
    for. Consumers such as the race engine rely on the position only."""

    source_id: str
    sensor_id: str
    position_id: str
    lane: int


@dataclass(frozen=True, slots=True, kw_only=True)
class RaceStarting(Event):
    race_id: RaceId


@dataclass(frozen=True, slots=True, kw_only=True)
class RaceStarted(Event):
    race_id: RaceId
    participants: tuple[Participant, ...]


@dataclass(frozen=True, slots=True, kw_only=True)
class RacePaused(Event):
    race_id: RaceId


@dataclass(frozen=True, slots=True, kw_only=True)
class RaceResumed(Event):
    race_id: RaceId


@dataclass(frozen=True, slots=True, kw_only=True)
class RaceFinished(Event):
    race_id: RaceId
    results: tuple[ParticipantResult, ...]
    aborted: bool = False


@dataclass(frozen=True, slots=True, kw_only=True)
class LapStarted(Event):
    race_id: RaceId
    driver_id: DriverId
    lane: int
    lap_number: int
    race_time_ns: int


@dataclass(frozen=True, slots=True, kw_only=True)
class LapCompleted(Event):
    race_id: RaceId
    driver_id: DriverId
    lane: int
    lap_number: int
    lap_time_ns: int
    race_time_ns: int


@dataclass(frozen=True, slots=True, kw_only=True)
class SectorCompleted(Event):
    race_id: RaceId
    driver_id: DriverId
    lane: int
    lap_number: int
    sector_number: int
    sector_time_ns: int
    race_time_ns: int


@dataclass(frozen=True, slots=True, kw_only=True)
class WinnerDetermined(Event):
    race_id: RaceId
    driver_id: DriverId
    lane: int
    laps_completed: int
    total_time_ns: int


@dataclass(frozen=True, slots=True, kw_only=True)
class PluginEnabled(Event):
    plugin_name: str
    plugin_version: str


@dataclass(frozen=True, slots=True, kw_only=True)
class PluginDisabled(Event):
    plugin_name: str
