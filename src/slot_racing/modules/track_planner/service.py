"""Load and save one plan per track in the existing ``track_layouts`` table.

The row is named ``plan``. Other layout rows are left alone. Saving a plan never updates the
track itself, so the stored lane count stays whatever the track already has.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import delete, insert, select, update
from sqlalchemy.orm import Session

from slot_racing.core.catalog import TrackCatalog, TrackInfo
from slot_racing.core.domain import TrackId
from slot_racing.core.errors import ValidationError
from slot_racing.core.storage import Database
from slot_racing.core.storage.base import Base
from slot_racing.modules.track_planner.document import (
    TrackPlan,
    clone_plan,
    empty_plan,
    parse_plan,
    to_document,
    validate_lanes,
    with_instances,
)
from slot_racing.modules.track_planner.library import PartLibrary
from slot_racing.modules.track_planner.parts import PartRecord, PartSpec

PLAN_NAME = "plan"


class TrackPlannerService:
    def __init__(self, database: Database, tracks: TrackCatalog) -> None:
        self._database = database
        self._tracks = tracks
        self.library = PartLibrary(database)

    def load(self, track_id: TrackId) -> TrackPlan:
        """The stored plan, or an empty one when this track has no plan yet."""
        self._require_track(track_id)
        with self._database.session() as session:
            payload = self._read(session, track_id)
        plan = empty_plan(track_id) if payload is None else parse_plan(track_id, payload)
        with self._database.session() as session:
            stored = self.library.read_instances(session, int(track_id))
        if stored:
            return with_instances(plan, stored)
        return plan

    def save(self, plan: TrackPlan) -> TrackPlan:
        """Replace the plan document. The track row, including its lane count, is not written."""
        track = self._require_track(plan.track_id)
        validate_lanes(plan, track.lane_count)
        document = to_document(plan)
        with self._database.session() as session:
            self.library.write_instances(session, int(plan.track_id), plan.instances)
            self._write(session, int(plan.track_id), document)
        return plan

    def list_parts(self) -> tuple[PartRecord, ...]:
        return self.library.list_parts()

    def add_part(self, spec: PartSpec) -> PartRecord:
        return self.library.add_part(spec)

    def update_part(self, part_id: int, spec: PartSpec) -> PartRecord:
        return self.library.update_part(part_id, spec)

    def delete_part(self, part_id: int) -> None:
        self.library.delete_part(part_id)

    def save_as_new(self, plan: TrackPlan, name: str) -> TrackPlan:
        """Store a copy as its own track. The open track and its definitions stay as they are."""
        source = self._require_track(plan.track_id)
        title = name.strip()
        if not title:
            raise ValidationError("error.track.name.required")
        if len(title) > 100:
            raise ValidationError("error.track.name.too_long", limit=100)
        validate_lanes(plan, source.lane_count)
        with self._database.session() as session:
            table = Base.metadata.tables["tracks"]
            new_id = session.execute(
                insert(table)
                .values(
                    name=title,
                    lane_count=source.lane_count,
                    description=source.description,
                    is_active=True,
                    image_path=source.image_path,
                )
                .returning(table.c.id)
            ).scalar_one()
            copied = clone_plan(plan, TrackId(int(new_id)))
            self.library.write_instances(session, int(copied.track_id), copied.instances)
            self._write(session, int(copied.track_id), to_document(copied))
        return copied

    def _require_track(self, track_id: TrackId) -> TrackInfo:
        track = self._tracks.get_track(track_id)
        if track is None:
            raise ValidationError("error.planner.track_missing")
        return track

    def _read(self, session: Session, track_id: TrackId) -> object | None:
        table = _layouts()
        return session.execute(
            select(table.c.data)
            .where(table.c.track_id == int(track_id), table.c.name == PLAN_NAME)
            .order_by(table.c.id)
            .limit(1)
        ).scalar_one_or_none()

    def _write(self, session: Session, track_id: int, document: dict[str, Any]) -> None:
        table = _layouts()
        ids = list(
            session.execute(
                select(table.c.id)
                .where(table.c.track_id == track_id, table.c.name == PLAN_NAME)
                .order_by(table.c.id)
            ).scalars()
        )
        if not ids:
            session.execute(insert(table).values(track_id=track_id, name=PLAN_NAME, data=document))
            return
        session.execute(update(table).where(table.c.id == ids[0]).values(data=document))
        if len(ids) > 1:
            session.execute(delete(table).where(table.c.id.in_(ids[1:])))


def _layouts() -> Any:
    try:
        return Base.metadata.tables["track_layouts"]
    except KeyError as error:
        raise ValidationError("error.planner.invalid") from error
