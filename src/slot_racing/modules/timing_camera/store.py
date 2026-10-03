"""Load and save the camera configuration in the existing settings table.

One global document, key :data:`CAMERA_CONFIGURATION_KEY`. It is not a row of
``timing_sensors`` and it does not mention a track: those rows belong to one
track's timing layout, while these zones belong to the camera.

A missing row is the default configuration (device defaults, no zones). A
broken document raises :class:`CameraConfigurationError` instead of crashing
the process. Callers turn that into a provider error.
"""

from __future__ import annotations

from typing import Protocol

from pydantic import ValidationError
from sqlalchemy.orm.attributes import flag_modified

from slot_racing.core.storage import Database, Setting
from slot_racing.modules.timing_camera.configuration import CameraConfiguration

CAMERA_CONFIGURATION_KEY = "timing_camera.configuration"


class CameraConfigurationError(Exception):
    """The stored document cannot be read. The database itself is unchanged."""


class CameraConfigurationSource(Protocol):
    """Something that returns the current camera configuration.

    The factory reads it while building a race session. A capture thread does
    not. Tests can pass any object with this method.
    """

    def load(self) -> CameraConfiguration:
        """The saved configuration, or the defaults when nothing is stored."""


class CameraConfigurationStore:
    """The settings-table implementation of :class:`CameraConfigurationSource`."""

    def __init__(self, database: Database) -> None:
        self._database = database

    def load(self) -> CameraConfiguration:
        with self._database.session() as session:
            row = session.get(Setting, CAMERA_CONFIGURATION_KEY)
            if row is None:
                return CameraConfiguration()
            payload = row.value
        if not isinstance(payload, dict):
            raise CameraConfigurationError("stored camera configuration is not an object")
        try:
            return CameraConfiguration.model_validate(payload)
        except ValidationError as error:
            raise CameraConfigurationError("stored camera configuration is invalid") from error

    def save(self, configuration: CameraConfiguration) -> None:
        """Replace the saved document. The previous value is left in place if this fails."""
        if not isinstance(configuration, CameraConfiguration):
            raise TypeError("configuration must be a CameraConfiguration")
        document = configuration.model_dump(mode="json")
        CameraConfiguration.model_validate(document)
        with self._database.session() as session:
            row = session.get(Setting, CAMERA_CONFIGURATION_KEY)
            if row is None:
                session.add(Setting(key=CAMERA_CONFIGURATION_KEY, value=document))
            else:
                row.value = document
                flag_modified(row, "value")
