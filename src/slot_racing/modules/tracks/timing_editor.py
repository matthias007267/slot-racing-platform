"""Editing model for the timing configuration of a track. No UI toolkit, no persistence.

The editor works on a :class:`TimingDraft`, which may be incomplete while the user edits it.
:meth:`TimingDraft.to_setup` builds the validated domain object; all rules live in the core
domain, the draft only offers the editing operations.
"""

from __future__ import annotations

from dataclasses import dataclass, replace

from slot_racing.core.domain import (
    TimingLayout,
    TimingPosition,
    TimingPositionType,
    TimingSensor,
    TimingSetup,
)
from slot_racing.core.errors import ValidationError

START_FINISH_ID = "start_finish"


@dataclass(slots=True)
class DraftEntry:
    """One position together with the sensor assigned to it."""

    position_id: str
    type: TimingPositionType
    name: str | None = None
    sensor_id: str = ""
    sensor_name: str | None = None
    hardware_id: str | None = None
    active: bool = True


def _clean(value: str | None) -> str | None:
    text = (value or "").strip()
    return text or None


class TimingDraft:
    def __init__(self, entries: list[DraftEntry]) -> None:
        self.entries = entries

    @classmethod
    def from_setup(cls, setup: TimingSetup) -> TimingDraft:
        entries = []
        for position in setup.layout.positions:
            sensor = setup.sensor_at(position.id)
            entries.append(
                DraftEntry(
                    position_id=position.id,
                    type=position.type,
                    name=position.name,
                    sensor_id=sensor.id,
                    sensor_name=sensor.name,
                    hardware_id=sensor.hardware_id,
                    active=sensor.active,
                )
            )
        return cls(entries)

    def entry(self, position_id: str) -> DraftEntry:
        for entry in self.entries:
            if entry.position_id == position_id:
                return entry
        raise ValidationError("error.timing.position_unknown", position=position_id)

    def add_sector(self) -> DraftEntry:
        """Append a sector position with a free id and a suggested sensor id."""
        position_id = self._free("sector_", {e.position_id for e in self.entries}, width=1)
        sensor_id = self._free("sensor_", {e.sensor_id for e in self.entries}, width=2)
        entry = DraftEntry(position_id, TimingPositionType.SECTOR, sensor_id=sensor_id)
        self.entries.append(entry)
        return entry

    def remove(self, position_id: str) -> None:
        entry = self.entry(position_id)
        if entry.type is TimingPositionType.START_FINISH:
            raise ValidationError("error.timing.start_finish_fixed")
        self.entries.remove(entry)

    def move(self, position_id: str, offset: int) -> bool:
        """Move a sector position by ``offset`` places. Start/finish stays first.

        Returns whether anything changed."""
        entry = self.entry(position_id)
        if entry.type is TimingPositionType.START_FINISH:
            raise ValidationError("error.timing.start_finish_fixed")
        index = self.entries.index(entry)
        target = min(max(index + offset, 1), len(self.entries) - 1)
        if target == index:
            return False
        self.entries.insert(target, self.entries.pop(index))
        return True

    def update(
        self,
        position_id: str,
        *,
        name: str | None,
        sensor_id: str,
        sensor_name: str | None,
        hardware_id: str | None,
        active: bool,
    ) -> None:
        """Change a position and its sensor. An edit that would make the setup invalid is
        rejected with a :class:`ValidationError` and leaves the draft unchanged."""
        entry = self.entry(position_id)
        previous = replace(entry)
        entry.name = _clean(name)
        entry.sensor_id = sensor_id.strip()
        entry.sensor_name = _clean(sensor_name)
        entry.hardware_id = _clean(hardware_id)
        entry.active = active
        try:
            self.to_setup()
        except ValidationError:
            self.entries[self.entries.index(entry)] = previous
            raise

    def assign_default_sensor_ids(self) -> None:
        """Give every position the conventional sensor id (``start_finish``, ``sensor_01``...)."""
        for index, entry in enumerate(self.entries):
            entry.sensor_id = (
                START_FINISH_ID
                if entry.type is TimingPositionType.START_FINISH
                else f"sensor_{index:02d}"
            )

    def to_setup(self) -> TimingSetup:
        """Build the validated setup. Raises :class:`ValidationError` if the draft is invalid."""
        positions = tuple(
            TimingPosition(id=e.position_id, type=e.type, order=index, name=e.name)
            for index, e in enumerate(self.entries, start=1)
        )
        sensors = tuple(
            TimingSensor(
                id=e.sensor_id,
                position_id=e.position_id,
                name=e.sensor_name,
                hardware_id=e.hardware_id,
                active=e.active,
            )
            for e in self.entries
        )
        return TimingSetup(TimingLayout(positions), sensors)

    @staticmethod
    def _free(prefix: str, used: set[str], width: int) -> str:
        number = 1
        while f"{prefix}{number:0{width}d}" in used:
            number += 1
        return f"{prefix}{number:0{width}d}"
