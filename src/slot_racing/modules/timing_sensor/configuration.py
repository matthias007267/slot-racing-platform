"""Physical sensor assignment for one track.

A physical id names the device that fired. It maps to one logical position and
one lane. Two physical sensors may share a position when their lanes differ.
The logical ``hardware_id`` of a timing position is not used.

The document has no fixed upper bound. A device that later reports how many
sensors it can serve may pass that number as :class:`DeviceCapacity`.
"""

from __future__ import annotations

from collections.abc import Sequence

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from slot_racing.core.errors import ProviderConfigurationError, ValidationError
from slot_racing.modules.timing_sensor.protocol import is_sensor_token

SCHEMA_VERSION = 1


class DeviceCapacity:
    """Optional limit reported by a connected device. ``None`` means it has not said."""

    def __init__(self, max_sensors: int | None) -> None:
        if max_sensors is not None and (
            isinstance(max_sensors, bool) or not isinstance(max_sensors, int) or max_sensors < 1
        ):
            raise ValueError("max_sensors must be a positive int or None")
        self.max_sensors = max_sensors


class SensorAssignment(BaseModel):
    """One physical sensor aimed at one position and one lane."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    physical_id: str
    position_id: str
    lane: int = Field(ge=1)

    @field_validator("physical_id", mode="before")
    @classmethod
    def _physical(cls, value: object) -> str:
        if not isinstance(value, str) or not is_sensor_token(value):
            raise ValueError("physical sensor id must be a single token")
        return value

    @field_validator("position_id", mode="before")
    @classmethod
    def _position(cls, value: object) -> str:
        if not isinstance(value, str):
            raise ValueError("position id must be text")
        text = value.strip()
        if not text or len(text) > 64:
            raise ValueError("position id must be 1 to 64 characters")
        return text

    @field_validator("lane", mode="before")
    @classmethod
    def _lane(cls, value: object) -> object:
        if isinstance(value, bool):
            raise ValueError("lane must be an integer")
        return value


class TrackSensorMap(BaseModel):
    """The assignments stored for one track."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    track_id: int = Field(ge=1)
    assignments: tuple[SensorAssignment, ...]

    @field_validator("track_id", mode="before")
    @classmethod
    def _track(cls, value: object) -> object:
        if isinstance(value, bool):
            raise ValueError("track id must be an integer")
        return value

    @model_validator(mode="after")
    def _unique(self) -> TrackSensorMap:
        _reject_duplicates(self.assignments)
        return self


class SensorConfiguration(BaseModel):
    """Every track's physical sensor map. Tracks that are absent have none."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: int = SCHEMA_VERSION
    tracks: tuple[TrackSensorMap, ...] = ()

    @field_validator("schema_version")
    @classmethod
    def _version(cls, value: int) -> int:
        if value != SCHEMA_VERSION:
            raise ValueError("unsupported sensor configuration version")
        return value

    @model_validator(mode="after")
    def _one_row_per_track(self) -> SensorConfiguration:
        seen: set[int] = set()
        for track in self.tracks:
            if track.track_id in seen:
                raise ValueError("a track may have only one sensor map")
            seen.add(track.track_id)
        return self

    def for_track(self, track_id: int) -> tuple[SensorAssignment, ...]:
        for track in self.tracks:
            if track.track_id == track_id:
                return track.assignments
        return ()


def assignment_issues(
    assignments: Sequence[SensorAssignment],
    *,
    positions: frozenset[str],
    lane_count: int,
    start_finish_id: str,
    capacity: DeviceCapacity | None = None,
) -> tuple[ValidationError, ...]:
    """Problems that keep this map from timing ``positions``. An empty map is not ready."""
    issues: list[ValidationError] = []
    if not assignments:
        issues.append(ValidationError("error.timing_provider.sensor_assignments_missing"))
        return tuple(issues)
    issues.extend(_duplicate_issues(assignments))
    for assignment in assignments:
        if assignment.position_id not in positions:
            issues.append(
                ValidationError(
                    "error.timing_provider.sensor_position_unknown",
                    position=assignment.position_id,
                    physical=assignment.physical_id,
                )
            )
        if assignment.lane > lane_count:
            issues.append(
                ValidationError(
                    "error.timing_provider.sensor_lane_invalid",
                    lane=assignment.lane,
                    physical=assignment.physical_id,
                )
            )
    if not any(item.position_id == start_finish_id for item in assignments):
        issues.append(ValidationError("error.timing_provider.sensor_start_finish_missing"))
    if (
        capacity is not None
        and capacity.max_sensors is not None
        and len(assignments) > capacity.max_sensors
    ):
        issues.append(
            ValidationError(
                "error.timing_provider.sensor_capacity",
                limit=capacity.max_sensors,
            )
        )
    return tuple(issues)


def require_assignments(
    assignments: Sequence[SensorAssignment],
    *,
    positions: frozenset[str],
    lane_count: int,
    start_finish_id: str,
    capacity: DeviceCapacity | None = None,
) -> None:
    """Raise the first problem. A usable map raises nothing."""
    issues = assignment_issues(
        assignments,
        positions=positions,
        lane_count=lane_count,
        start_finish_id=start_finish_id,
        capacity=capacity,
    )
    if issues:
        error = issues[0]
        raise ProviderConfigurationError(error.key, **error.params)


def _reject_duplicates(assignments: Sequence[SensorAssignment]) -> None:
    physical: set[str] = set()
    roles: set[tuple[str, int]] = set()
    for assignment in assignments:
        if assignment.physical_id in physical:
            raise ValueError("duplicate physical sensor id")
        physical.add(assignment.physical_id)
        role = (assignment.position_id, assignment.lane)
        if role in roles:
            raise ValueError("duplicate position and lane")
        roles.add(role)


def _duplicate_issues(assignments: Sequence[SensorAssignment]) -> list[ValidationError]:
    issues: list[ValidationError] = []
    physical: set[str] = set()
    roles: set[tuple[str, int]] = set()
    for assignment in assignments:
        if assignment.physical_id in physical:
            issues.append(
                ValidationError(
                    "error.timing_provider.sensor_physical_duplicate",
                    physical=assignment.physical_id,
                )
            )
        physical.add(assignment.physical_id)
        role = (assignment.position_id, assignment.lane)
        if role in roles:
            issues.append(
                ValidationError(
                    "error.timing_provider.sensor_role_duplicate",
                    position=assignment.position_id,
                    lane=assignment.lane,
                )
            )
        roles.add(role)
    return issues
