"""Track management. Implements the catalog other modules look up."""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from slot_racing.core.catalog import TrackCatalog, TrackInfo
from slot_racing.core.domain import TrackId
from slot_racing.core.errors import ValidationError
from slot_racing.core.storage import Database
from slot_racing.modules.tracks.models import Track

MIN_LANES = 1
MAX_LANES = 6


@dataclass(frozen=True, slots=True)
class TrackInput:
    name: str
    lane_count: int = 2
    description: str | None = None
    image_path: str | None = None


def _track_info(track: Track) -> TrackInfo:
    return TrackInfo(
        id=TrackId(track.id),
        name=track.name,
        description=track.description,
        lane_count=track.lane_count,
        is_active=track.is_active,
        image_path=track.image_path,
        created_at=track.created_at,
        updated_at=track.updated_at,
    )


class TrackService(TrackCatalog):
    def __init__(self, database: Database) -> None:
        self._database = database

    def list_tracks(self, *, active_only: bool = False) -> list[TrackInfo]:
        query = select(Track).order_by(Track.name, Track.id)
        if active_only:
            query = query.where(Track.is_active.is_(True))
        with self._database.session() as session:
            return [_track_info(track) for track in session.scalars(query)]

    def get_track(self, track_id: TrackId) -> TrackInfo | None:
        with self._database.session() as session:
            track = session.get(Track, track_id)
            return None if track is None else _track_info(track)

    def create_track(self, data: TrackInput) -> TrackInfo:
        values = self._validate(data)
        with self._database.session() as session:
            track = Track(**values)
            session.add(track)
            session.flush()
            return _track_info(track)

    def update_track(self, track_id: TrackId, data: TrackInput) -> TrackInfo:
        values = self._validate(data)
        with self._database.session() as session:
            track = self._load(session, track_id)
            for field, value in values.items():
                setattr(track, field, value)
            session.flush()
            return _track_info(track)

    def set_active(self, track_id: TrackId, active: bool) -> TrackInfo:
        with self._database.session() as session:
            track = self._load(session, track_id)
            track.is_active = active
            session.flush()
            return _track_info(track)

    def delete_track(self, track_id: TrackId) -> None:
        """Delete a track. Fails while races use it; deactivate it instead."""
        try:
            with self._database.session() as session:
                session.delete(self._load(session, track_id))
                session.flush()
        except IntegrityError as error:
            raise ValidationError("error.track.in_use") from error

    @staticmethod
    def _validate(data: TrackInput) -> dict[str, str | int | None]:
        name = data.name.strip()
        if not name:
            raise ValidationError("error.track.name.required")
        if len(name) > 100:
            raise ValidationError("error.track.name.too_long", limit=100)
        if not MIN_LANES <= data.lane_count <= MAX_LANES:
            raise ValidationError("error.track.lane_count", minimum=MIN_LANES, maximum=MAX_LANES)
        image = (data.image_path or "").strip()
        if len(image) > 500:
            raise ValidationError("error.track.image.too_long", limit=500)
        return {
            "name": name,
            "lane_count": data.lane_count,
            "description": (data.description or "").strip() or None,
            "image_path": image or None,
        }

    @staticmethod
    def _load(session: Session, track_id: int) -> Track:
        track = session.get(Track, track_id)
        if track is None:
            raise ValidationError("error.track.not_found")
        return track
