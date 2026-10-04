"""Structured track plan. Pieces and markers are data, not a picture.

A plan belongs to one track. The lane count stays on that track; the plan only records where
each piece sits on the grid and where later timing points can attach to a piece and a lane.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from uuid import uuid4

from slot_racing.core.domain import TrackId
from slot_racing.core.errors import ValidationError

PLAN_VERSION = 1

STRAIGHT_H = "straight_h"
STRAIGHT_V = "straight_v"
CURVE_90 = "curve_90"
PIECE_TYPES = frozenset({STRAIGHT_H, STRAIGHT_V, CURVE_90})

START_FINISH = "start_finish"
SENSOR = "sensor"
CAMERA = "camera"
TIMING_POINT = "timing_point"
SECTOR = "sector"
MARKER_KINDS = frozenset({START_FINISH, SENSOR, CAMERA, TIMING_POINT, SECTOR})

CLOCKWISE = "clockwise"
COUNTERCLOCKWISE = "counterclockwise"
DIRECTIONS = frozenset({CLOCKWISE, COUNTERCLOCKWISE})

GRID_LIMIT = 64
_ID_LIMIT = 40

# Footprint in grid cells. The same footprint is used for every lane count; lanes are drawn
# inside it so a 2-lane plan never reserves space for a fourth lane.
_SPANS = {
    STRAIGHT_H: (4, 2),
    STRAIGHT_V: (2, 4),
    CURVE_90: (4, 4),
}


@dataclass(frozen=True, slots=True)
class Piece:
    id: str
    piece_type: str
    x: int
    y: int
    rotation: int


@dataclass(frozen=True, slots=True)
class Marker:
    """A point that can later name a sensor, a camera, a sector or the start/finish line.

    ``piece_id`` ties the point to a piece. ``lane`` is 1-based and empty when the point covers
    every lane, which is how the start/finish line is stored.
    """

    id: str
    kind: str
    x: int
    y: int
    piece_id: str | None = None
    lane: int | None = None


@dataclass(frozen=True, slots=True)
class TrackPlan:
    track_id: TrackId
    version: int
    direction: str
    pieces: tuple[Piece, ...]
    markers: tuple[Marker, ...]

    def start_finish(self) -> Marker | None:
        found = [marker for marker in self.markers if marker.kind == START_FINISH]
        return found[0] if found else None


def new_id() -> str:
    return uuid4().hex[:12]


def empty_plan(track_id: TrackId) -> TrackPlan:
    return TrackPlan(
        track_id=track_id,
        version=PLAN_VERSION,
        direction=CLOCKWISE,
        pieces=(),
        markers=(),
    )


def span(piece_type: str) -> tuple[int, int]:
    """Width and height of a piece in grid cells."""
    try:
        return _SPANS[piece_type]
    except KeyError as error:
        raise ValidationError("error.planner.piece") from error


def lane_centers(lane_count: int, span_px: float) -> tuple[float, ...]:
    """Where each lane is drawn across ``span_px``. The count is the track's, never a fixed 4."""
    if lane_count < 1:
        return ()
    step = span_px / (lane_count + 1)
    return tuple(step * (index + 1) for index in range(lane_count))


def travel_vector(piece_type: str, rotation: int, direction: str) -> tuple[int, int]:
    """Driving direction as a grid step. Positive y points downward. Reversing the plan flips it."""
    if piece_type == STRAIGHT_H:
        vector = (1, 0)
    elif piece_type == STRAIGHT_V:
        vector = (0, 1)
    elif piece_type == CURVE_90:
        vector = {0: (1, 1), 90: (-1, 1), 180: (-1, -1), 270: (1, -1)}[rotation]
    else:
        raise ValidationError("error.planner.piece")
    if direction == COUNTERCLOCKWISE:
        return (-vector[0], -vector[1])
    if direction != CLOCKWISE:
        raise ValidationError("error.planner.direction")
    return vector


def add_piece(plan: TrackPlan, piece_type: str, x: int, y: int, rotation: int = 0) -> TrackPlan:
    piece = Piece(id=new_id(), piece_type=piece_type, x=x, y=y, rotation=rotation)
    _check_piece(piece)
    return _replace(plan, pieces=(*plan.pieces, piece))


def move_piece(plan: TrackPlan, piece_id: str, x: int, y: int) -> TrackPlan:
    pieces = tuple(
        _checked(Piece(piece.id, piece.piece_type, x, y, piece.rotation))
        if piece.id == piece_id
        else piece
        for piece in plan.pieces
    )
    if not any(piece.id == piece_id for piece in plan.pieces):
        raise ValidationError("error.planner.piece")
    return _replace(plan, pieces=pieces)


def rotate_piece(plan: TrackPlan, piece_id: str, rotation: int) -> TrackPlan:
    pieces = tuple(
        _checked(Piece(piece.id, piece.piece_type, piece.x, piece.y, rotation))
        if piece.id == piece_id
        else piece
        for piece in plan.pieces
    )
    if not any(piece.id == piece_id for piece in plan.pieces):
        raise ValidationError("error.planner.piece")
    return _replace(plan, pieces=pieces)


def remove_piece(plan: TrackPlan, piece_id: str) -> TrackPlan:
    pieces = tuple(piece for piece in plan.pieces if piece.id != piece_id)
    if len(pieces) == len(plan.pieces):
        raise ValidationError("error.planner.piece")
    markers = tuple(
        Marker(marker.id, marker.kind, marker.x, marker.y, None, marker.lane)
        if marker.piece_id == piece_id
        else marker
        for marker in plan.markers
    )
    return _replace(plan, pieces=pieces, markers=markers)


def place_start_finish(plan: TrackPlan, x: int, y: int, piece_id: str | None = None) -> TrackPlan:
    _check_cell(x, y)
    if piece_id is not None and piece_id not in {piece.id for piece in plan.pieces}:
        raise ValidationError("error.planner.piece")
    current = plan.start_finish()
    marker = Marker(
        id=current.id if current is not None else new_id(),
        kind=START_FINISH,
        x=x,
        y=y,
        piece_id=piece_id,
        lane=None,
    )
    others = tuple(item for item in plan.markers if item.kind != START_FINISH)
    return _replace(plan, markers=(*others, marker))


def move_marker(plan: TrackPlan, marker_id: str, x: int, y: int) -> TrackPlan:
    if not any(marker.id == marker_id for marker in plan.markers):
        raise ValidationError("error.planner.piece")
    markers = tuple(
        Marker(marker.id, marker.kind, x, y, marker.piece_id, marker.lane)
        if marker.id == marker_id
        else marker
        for marker in plan.markers
    )
    return _replace(plan, markers=markers)


def remove_marker(plan: TrackPlan, marker_id: str) -> TrackPlan:
    markers = tuple(marker for marker in plan.markers if marker.id != marker_id)
    if len(markers) == len(plan.markers):
        raise ValidationError("error.planner.piece")
    return _replace(plan, markers=markers)


def set_direction(plan: TrackPlan, direction: str) -> TrackPlan:
    if direction not in DIRECTIONS:
        raise ValidationError("error.planner.direction")
    return _replace(plan, direction=direction)


def reset_plan(plan: TrackPlan) -> TrackPlan:
    return empty_plan(plan.track_id)


def next_origin(plan: TrackPlan, piece_type: str) -> tuple[int, int]:
    """First free grid origin for a new piece, scanning from the top left."""
    width, height = span(piece_type)
    occupied = [(piece.x, piece.y, *span(piece.piece_type)) for piece in plan.pieces]
    for y in range(0, GRID_LIMIT - height + 1, 2):
        for x in range(0, GRID_LIMIT - width + 1, 2):
            if not any(_overlaps(x, y, width, height, *box) for box in occupied):
                return x, y
    return 0, 0


def to_document(plan: TrackPlan) -> dict[str, Any]:
    checked = _validate(plan)
    return {
        "version": checked.version,
        "direction": checked.direction,
        "pieces": [
            {
                "id": piece.id,
                "type": piece.piece_type,
                "x": piece.x,
                "y": piece.y,
                "rotation": piece.rotation,
            }
            for piece in checked.pieces
        ],
        "markers": [
            {
                "id": marker.id,
                "kind": marker.kind,
                "x": marker.x,
                "y": marker.y,
                "piece_id": marker.piece_id,
                "lane": marker.lane,
            }
            for marker in checked.markers
        ],
    }


def parse_plan(track_id: TrackId, payload: object) -> TrackPlan:
    if not isinstance(payload, dict):
        raise ValidationError("error.planner.invalid")
    version = payload.get("version")
    direction = payload.get("direction")
    raw_pieces = payload.get("pieces")
    raw_markers = payload.get("markers")
    if version != PLAN_VERSION or direction not in DIRECTIONS:
        raise ValidationError("error.planner.invalid")
    if not isinstance(raw_pieces, list) or not isinstance(raw_markers, list):
        raise ValidationError("error.planner.invalid")
    pieces = tuple(_parse_piece(item) for item in raw_pieces)
    markers = tuple(_parse_marker(item) for item in raw_markers)
    return _validate(
        TrackPlan(
            track_id=track_id,
            version=PLAN_VERSION,
            direction=str(direction),
            pieces=pieces,
            markers=markers,
        )
    )


def validate_lanes(plan: TrackPlan, lane_count: int) -> None:
    """Reject a marker lane the track does not have. The track's own lane count is not written."""
    for marker in plan.markers:
        if marker.lane is None:
            continue
        if lane_count < 1 or not 1 <= marker.lane <= lane_count:
            raise ValidationError("error.planner.lane")


def _parse_piece(payload: object) -> Piece:
    if not isinstance(payload, dict):
        raise ValidationError("error.planner.invalid")
    return _checked(
        Piece(
            id=_identity(payload.get("id")),
            piece_type=_text(payload.get("type")),
            x=_cell(payload.get("x")),
            y=_cell(payload.get("y")),
            rotation=_cell(payload.get("rotation")),
        )
    )


def _parse_marker(payload: object) -> Marker:
    if not isinstance(payload, dict):
        raise ValidationError("error.planner.invalid")
    piece_id = payload.get("piece_id")
    lane = payload.get("lane")
    return Marker(
        id=_identity(payload.get("id")),
        kind=_text(payload.get("kind")),
        x=_cell(payload.get("x")),
        y=_cell(payload.get("y")),
        piece_id=None if piece_id is None else _identity(piece_id),
        lane=None if lane is None else _cell(lane),
    )


def _validate(plan: TrackPlan) -> TrackPlan:
    if plan.version != PLAN_VERSION or plan.direction not in DIRECTIONS:
        raise ValidationError("error.planner.invalid")
    seen: set[str] = set()
    for piece in plan.pieces:
        if piece.id in seen:
            raise ValidationError("error.planner.invalid")
        seen.add(piece.id)
        _check_piece(piece)
    piece_ids = {piece.id for piece in plan.pieces}
    marker_ids: set[str] = set()
    starts = 0
    for marker in plan.markers:
        if marker.id in marker_ids or marker.kind not in MARKER_KINDS:
            raise ValidationError("error.planner.invalid")
        marker_ids.add(marker.id)
        _check_cell(marker.x, marker.y)
        if marker.piece_id is not None and marker.piece_id not in piece_ids:
            raise ValidationError("error.planner.invalid")
        if marker.kind == START_FINISH:
            starts += 1
            if marker.lane is not None:
                raise ValidationError("error.planner.invalid")
        elif marker.lane is not None and marker.lane < 1:
            raise ValidationError("error.planner.lane")
    if starts > 1:
        raise ValidationError("error.planner.invalid")
    return plan


def _check_piece(piece: Piece) -> None:
    if piece.piece_type not in PIECE_TYPES:
        raise ValidationError("error.planner.piece")
    if piece.piece_type == CURVE_90:
        if piece.rotation not in {0, 90, 180, 270}:
            raise ValidationError("error.planner.piece")
    elif piece.rotation != 0:
        raise ValidationError("error.planner.piece")
    width, height = span(piece.piece_type)
    if piece.x < 0 or piece.y < 0 or piece.x + width > GRID_LIMIT or piece.y + height > GRID_LIMIT:
        raise ValidationError("error.planner.position")


def _checked(piece: Piece) -> Piece:
    _check_piece(piece)
    return piece


def _check_cell(x: int, y: int) -> None:
    numbers = not isinstance(x, bool) and not isinstance(y, bool)
    if not numbers or not isinstance(x, int) or not isinstance(y, int):
        raise ValidationError("error.planner.position")
    if x < 0 or y < 0 or x >= GRID_LIMIT or y >= GRID_LIMIT:
        raise ValidationError("error.planner.position")


def _cell(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValidationError("error.planner.invalid")
    return value


def _identity(value: object) -> str:
    if not isinstance(value, str):
        raise ValidationError("error.planner.invalid")
    text = value.strip()
    if not text or len(text) > _ID_LIMIT:
        raise ValidationError("error.planner.invalid")
    return text


def _text(value: object) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValidationError("error.planner.invalid")
    return value.strip()


def _replace(
    plan: TrackPlan,
    *,
    direction: str | None = None,
    pieces: tuple[Piece, ...] | None = None,
    markers: tuple[Marker, ...] | None = None,
) -> TrackPlan:
    return _validate(
        TrackPlan(
            track_id=plan.track_id,
            version=plan.version,
            direction=plan.direction if direction is None else direction,
            pieces=plan.pieces if pieces is None else pieces,
            markers=plan.markers if markers is None else markers,
        )
    )


def _overlaps(
    x: int, y: int, width: int, height: int, other_x: int, other_y: int, other_w: int, other_h: int
) -> bool:
    separated_x = x >= other_x + other_w or other_x >= x + width
    separated_y = y >= other_y + other_h or other_y >= y + height
    return not separated_x and not separated_y
