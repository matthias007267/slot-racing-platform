"""Physical sensor assignment for one track.

A physical id names the device that fired. It maps to one logical position and
one lane. Two physical sensors may share a position when their lanes differ.
The logical ``hardware_id`` of a timing position is not used.

The document has no fixed upper bound. A device that later reports how many
sensors it can serve may pass that number as :class:`DeviceCapacity`.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from slot_racing.core.errors import ProviderConfigurationError, ValidationError
from slot_racing.modules.timing_sensor.protocol import is_sensor_token

SCHEMA_VERSION = 2
ACCEPTED_SCHEMA_VERSIONS = frozenset({1, 2})
OperatingMode = Literal["simulation", "serial"]


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


class DeviceHint(BaseModel):
    """Stable facts used to recognise one USB device again.

    A display name is not an identity and is not stored here. ``port`` is only the
    last port name. It is not enough when more than one candidate matches.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    vid: int | None = None
    pid: int | None = None
    serial_number: str = ""
    location: str = ""
    port: str = ""

    @field_validator("vid", "pid", mode="before")
    @classmethod
    def _usb_id(cls, value: object) -> object:
        if value is None:
            return None
        if isinstance(value, bool) or not isinstance(value, int):
            raise ValueError("usb id must be an integer")
        if value < 0 or value > 0xFFFF:
            raise ValueError("usb id is out of range")
        return value

    @field_validator("serial_number", "location", "port", mode="before")
    @classmethod
    def _hint_text(cls, value: object) -> str:
        if not isinstance(value, str):
            raise ValueError("device hint text must be a string")
        text = value.strip()
        if any(character.isspace() for character in text):
            raise ValueError("device hint text must be a single token")
        if len(text) > 128:
            raise ValueError("device hint text is too long")
        return text


class BoardName(BaseModel):
    """A name the user gave one USB sensor board.

    The name is the label shown in the program. The device hint is the identity.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    name: str
    device: DeviceHint

    @field_validator("name", mode="before")
    @classmethod
    def _name(cls, value: object) -> str:
        if not isinstance(value, str):
            raise ValueError("board name must be text")
        text = " ".join(value.split())
        if not text or len(text) > 40:
            raise ValueError("board name must be 1 to 40 characters")
        return text


class SensorConfiguration(BaseModel):
    """Sensor preferences and every track's physical sensor map.

    Documents written as version 1 are still read. Saving writes version 2.
    Tracks that are absent have no assignments.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: int = SCHEMA_VERSION
    tracks: tuple[TrackSensorMap, ...] = ()
    mode: OperatingMode = "simulation"
    port: str = ""
    device: DeviceHint = Field(default_factory=DeviceHint)
    board_names: tuple[BoardName, ...] = ()
    track_id: int | None = None
    developer_expanded: bool = False
    developer_offset_ns: int | None = None
    developer_numerator: int | None = None
    developer_denominator: int | None = None

    @field_validator("schema_version", mode="before")
    @classmethod
    def _version(cls, value: object) -> object:
        if isinstance(value, bool) or not isinstance(value, int):
            raise ValueError("unsupported sensor configuration version")
        if value not in ACCEPTED_SCHEMA_VERSIONS:
            raise ValueError("unsupported sensor configuration version")
        return value

    @field_validator("mode", mode="before")
    @classmethod
    def _mode(cls, value: object) -> object:
        if value not in {"simulation", "serial"}:
            raise ValueError("operating mode must be simulation or serial")
        return value

    @field_validator("port", mode="before")
    @classmethod
    def _port(cls, value: object) -> str:
        if not isinstance(value, str):
            raise ValueError("port must be text")
        text = value.strip()
        if any(character.isspace() for character in text) or len(text) > 64:
            raise ValueError("port must be a single token")
        return text

    @field_validator("track_id", mode="before")
    @classmethod
    def _track_id(cls, value: object) -> object:
        if value is None:
            return None
        if isinstance(value, bool) or not isinstance(value, int) or value < 1:
            raise ValueError("track id must be a positive integer")
        return value

    @field_validator("developer_expanded", mode="before")
    @classmethod
    def _expanded(cls, value: object) -> object:
        if not isinstance(value, bool):
            raise ValueError("developer_expanded must be a boolean")
        return value

    @field_validator(
        "developer_offset_ns",
        "developer_numerator",
        "developer_denominator",
        mode="before",
    )
    @classmethod
    def _developer_int(cls, value: object) -> object:
        if value is None:
            return None
        if isinstance(value, bool) or not isinstance(value, int):
            raise ValueError("developer clock value must be an integer")
        return value

    @model_validator(mode="after")
    def _developer_rate(self) -> SensorConfiguration:
        if self.developer_numerator is not None and self.developer_numerator < 1:
            raise ValueError("developer numerator must be positive")
        if self.developer_denominator is not None and self.developer_denominator < 1:
            raise ValueError("developer denominator must be positive")
        return self

    @model_validator(mode="after")
    def _unique_board_names(self) -> SensorConfiguration:
        seen: set[str] = set()
        for board in self.board_names:
            key = board.name.casefold()
            if key in seen:
                raise ValueError("board names must be unique")
            seen.add(key)
        return self

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
