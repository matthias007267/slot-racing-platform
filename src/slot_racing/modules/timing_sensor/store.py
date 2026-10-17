"""Load and save physical sensor maps in the settings table.

One document, key :data:`SENSOR_CONFIGURATION_KEY`, holds every track. It is
not a row of ``timing_sensors`` and it does not write ``hardware_id``.
"""

from __future__ import annotations

from collections.abc import Sequence

from pydantic import ValidationError
from sqlalchemy.orm.attributes import flag_modified

from slot_racing.core.storage import Database, Setting
from slot_racing.modules.timing_sensor.configuration import (
    SCHEMA_VERSION,
    SensorAssignment,
    SensorConfiguration,
    TrackSensorMap,
)

SENSOR_CONFIGURATION_KEY = "timing_sensor.configuration"


class SensorConfigurationError(Exception):
    """The stored document cannot be read. The database itself is unchanged."""


class SensorConfigurationStore:
    """Settings-table storage for :class:`SensorConfiguration`."""

    def __init__(self, database: Database) -> None:
        self._database = database

    def load(self) -> SensorConfiguration:
        """The saved document, or an empty one when nothing is stored."""
        with self._database.session() as session:
            row = session.get(Setting, SENSOR_CONFIGURATION_KEY)
            if row is None:
                return SensorConfiguration()
            payload = row.value
        if not isinstance(payload, dict):
            raise SensorConfigurationError("stored sensor configuration is not an object")
        try:
            return SensorConfiguration.model_validate(payload)
        except ValidationError as error:
            raise SensorConfigurationError("stored sensor configuration is invalid") from error

    def save(self, configuration: SensorConfiguration) -> None:
        """Replace the saved document. The written schema version is current."""
        if not isinstance(configuration, SensorConfiguration):
            raise TypeError("configuration must be a SensorConfiguration")
        if configuration.schema_version != SCHEMA_VERSION:
            configuration = configuration.model_copy(update={"schema_version": SCHEMA_VERSION})
        document = configuration.model_dump(mode="json")
        try:
            SensorConfiguration.model_validate(document)
        except ValidationError as error:
            raise SensorConfigurationError("sensor configuration is invalid") from error
        with self._database.session() as session:
            row = session.get(Setting, SENSOR_CONFIGURATION_KEY)
            if row is None:
                session.add(Setting(key=SENSOR_CONFIGURATION_KEY, value=document))
            else:
                row.value = document
                flag_modified(row, "value")

    def track_assignments(self, track_id: int) -> tuple[SensorAssignment, ...]:
        return self.load().for_track(track_id)

    def save_track(self, track_id: int, assignments: Sequence[SensorAssignment]) -> None:
        """Replace one track's map. An empty assignment list removes that track."""
        current = self.load()
        kept = [track for track in current.tracks if track.track_id != track_id]
        if assignments:
            kept.append(TrackSensorMap(track_id=track_id, assignments=tuple(assignments)))
        kept.sort(key=lambda track: track.track_id)
        self.save(current.model_copy(update={"tracks": tuple(kept)}))

    def save_preferences(self, **changes: object) -> None:
        """Update connection and developer preferences. Assignments stay as they are.

        Invalid values raise :class:`SensorConfigurationError` and are not written.
        """
        current = self.load()
        document = current.model_dump(mode="python")
        document.update(changes)
        try:
            updated = SensorConfiguration.model_validate(document)
        except ValidationError as error:
            raise SensorConfigurationError("sensor configuration is invalid") from error
        self.save(updated)
