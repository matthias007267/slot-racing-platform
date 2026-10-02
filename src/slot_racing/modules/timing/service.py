"""Stores the timing configuration of tracks."""

from __future__ import annotations

from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from slot_racing.core.domain import (
    TimingLayout,
    TimingPosition,
    TimingPositionType,
    TimingSensor,
    TimingSetup,
    TrackId,
)
from slot_racing.core.errors import ValidationError
from slot_racing.core.storage import Database
from slot_racing.core.timing import TimingSetupService
from slot_racing.modules.timing import models

DEFAULT_SOURCE_TYPE = "simulation"


class TimingSetupManager(TimingSetupService):
    """Persists a track's :class:`TimingSetup` in ``timing_positions`` and ``timing_sensors``.

    Saving replaces the stored positions and sensors in one transaction, so a failed save leaves
    the previous configuration untouched.
    """

    def __init__(self, database: Database) -> None:
        self._database = database

    def get_setup(self, track_id: TrackId) -> TimingSetup | None:
        with self._database.session() as session:
            configuration = self._configuration(session, track_id)
            if configuration is None:
                return None
            rows = list(
                session.scalars(
                    select(models.TimingPosition)
                    .where(models.TimingPosition.configuration_id == configuration.id)
                    .order_by(models.TimingPosition.sequence_index)
                )
            )
            if not rows:
                return None
            positions = tuple(
                TimingPosition(
                    id=row.position_id,
                    type=TimingPositionType(row.type),
                    order=row.sequence_index,
                    name=row.name,
                )
                for row in rows
            )
            by_row_id = {row.id: row.position_id for row in rows}
            sensors = tuple(
                TimingSensor(
                    id=sensor.sensor_id,
                    position_id=by_row_id[sensor.position_id],
                    name=sensor.name,
                    hardware_id=sensor.hardware_id,
                    active=sensor.is_active,
                )
                for sensor in session.scalars(
                    select(models.TimingSensor)
                    .where(
                        models.TimingSensor.configuration_id == configuration.id,
                        models.TimingSensor.position_id.in_(by_row_id),
                    )
                    .order_by(models.TimingSensor.sequence_index)
                )
                if sensor.position_id is not None
            )
            return TimingSetup(TimingLayout(positions), sensors)

    def save_setup(self, track_id: TrackId, setup: TimingSetup) -> None:
        try:
            with self._database.session() as session:
                configuration = self._configuration(session, track_id)
                if configuration is None:
                    configuration = models.TimingConfiguration(
                        track_id=track_id,
                        name="Timing",
                        source_type=DEFAULT_SOURCE_TYPE,
                        is_default=True,
                    )
                    session.add(configuration)
                    session.flush()
                self._clear(session, configuration.id)
                rows: dict[str, models.TimingPosition] = {}
                for position in setup.layout.positions:
                    row = models.TimingPosition(
                        configuration_id=configuration.id,
                        position_id=position.id,
                        type=position.type.value,
                        sequence_index=position.order,
                        name=position.name,
                    )
                    session.add(row)
                    rows[position.id] = row
                session.flush()
                for sensor in setup.sensors:
                    position = setup.layout.position(sensor.position_id)
                    session.add(
                        models.TimingSensor(
                            configuration_id=configuration.id,
                            sensor_id=sensor.id,
                            role=position.type.value,
                            sequence_index=position.order,
                            settings={},
                            name=sensor.name,
                            position_id=rows[sensor.position_id].id,
                            hardware_id=sensor.hardware_id,
                            is_active=sensor.active,
                        )
                    )
                session.flush()
        except IntegrityError as error:
            raise ValidationError("error.timing.track_unknown") from error

    def clear_setup(self, track_id: TrackId) -> None:
        with self._database.session() as session:
            configuration = self._configuration(session, track_id)
            if configuration is not None:
                self._clear(session, configuration.id)

    @staticmethod
    def _configuration(session: Session, track_id: int) -> models.TimingConfiguration | None:
        return session.scalar(
            select(models.TimingConfiguration).where(
                models.TimingConfiguration.track_id == track_id
            )
        )

    @staticmethod
    def _clear(session: Session, configuration_id: int) -> None:
        session.execute(
            delete(models.TimingSensor).where(
                models.TimingSensor.configuration_id == configuration_id
            )
        )
        session.execute(
            delete(models.TimingPosition).where(
                models.TimingPosition.configuration_id == configuration_id
            )
        )
