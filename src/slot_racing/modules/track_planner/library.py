"""Load and save the part library and the instances of one plan."""

from __future__ import annotations

from sqlalchemy import delete, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from slot_racing.core.errors import ValidationError
from slot_racing.core.storage import Database
from slot_racing.modules.track_planner.inventory import require_quantity
from slot_racing.modules.track_planner.models import (
    TrackPartConnector,
    TrackPartDefinition,
    TrackPartStock,
    TrackPlanInstance,
)
from slot_racing.modules.track_planner.parts import (
    ConnectorSpec,
    PartInstance,
    PartRecord,
    PartSpec,
    decode_slot_paths,
    encode_slot_paths,
    identity_key,
    require_scale,
    standard_catalog,
)


class PartLibrary:
    def __init__(self, database: Database) -> None:
        self._database = database

    def list_parts(self) -> tuple[PartRecord, ...]:
        self.ensure_seed()
        with self._database.session() as session:
            rows = session.scalars(
                select(TrackPartDefinition)
                .where(TrackPartDefinition.suppressed.is_(False))
                .order_by(TrackPartDefinition.id)
            )
            return tuple(self._record(session, row) for row in rows)

    def ensure_seed(self) -> None:
        """Insert the original catalogue when a part is not stored yet.

        A part that was stored before grooves existed gets those grooves once.
        The outline and the joints are left as they are. A suppressed catalogue
        row still counts as stored, so deleting a bundled part does not recreate it.
        """
        catalog = {
            identity_key(spec.name, spec.article_number): spec for spec in standard_catalog()
        }
        with self._database.session() as session:
            rows = list(session.scalars(select(TrackPartDefinition)))
            stored = {identity_key(row.name, row.article_number) for row in rows}
            for spec in standard_catalog():
                key = identity_key(spec.name, spec.article_number)
                if key not in stored:
                    self._insert(session, spec)
                    stored.add(key)
            for row in rows:
                known = catalog.get(identity_key(row.name, row.article_number))
                if known is None or not known.slot_paths or row.slot_paths:
                    continue
                row.slot_paths = encode_slot_paths(known.slot_paths)

    def add_part(self, spec: PartSpec) -> PartRecord:
        try:
            with self._database.session() as session:
                self._reject_duplicate(session, spec)
                definition = self._insert(session, spec)
                session.flush()
                return self._record(session, definition)
        except IntegrityError as error:
            raise self._duplicate(spec) from error

    def update_part(self, part_id: int, spec: PartSpec) -> PartRecord:
        try:
            with self._database.session() as session:
                definition = session.get(TrackPartDefinition, part_id)
                if definition is None:
                    raise ValidationError("error.planner.part")
                self._reject_duplicate(session, spec, except_id=part_id)
                definition.article_number = spec.article_number.strip()
                definition.scale = require_scale(spec.scale)
                definition.name = spec.name.strip()
                definition.category = spec.category
                definition.length_mm = spec.length_mm
                definition.width_mm = spec.width_mm
                definition.height_mm = spec.height_mm
                definition.radius_mm = spec.radius_mm
                definition.angle_deg = spec.angle_deg
                definition.lane_count = spec.lane_count
                definition.outline = [list(point) for point in spec.outline]
                definition.slot_paths = encode_slot_paths(spec.slot_paths)
                session.execute(
                    delete(TrackPartConnector).where(TrackPartConnector.part_id == part_id)
                )
                self._add_connectors(session, part_id, spec)
                session.flush()
                return self._record(session, definition)
        except IntegrityError as error:
            raise self._duplicate(spec) from error

    def delete_part(self, part_id: int) -> None:
        """Remove a part from the placeable library.

        A part that is already on a plan is kept. Deleting it would break that
        plan. An unused catalogue part is suppressed, because seeding would
        otherwise insert it again. An unused custom part is deleted, and its
        stock row goes with it.
        """
        catalog = {identity_key(spec.name, spec.article_number) for spec in standard_catalog()}
        with self._database.session() as session:
            definition = session.get(TrackPartDefinition, part_id)
            if definition is None:
                raise ValidationError("error.planner.part")
            used = session.scalar(
                select(func.count())
                .select_from(TrackPlanInstance)
                .where(TrackPlanInstance.part_id == part_id)
            )
            if used:
                raise ValidationError("error.planner.part_in_use", count=int(used))
            key = identity_key(definition.name, definition.article_number)
            if key in catalog:
                definition.suppressed = True
                return
            session.delete(definition)

    def stock_quantities(self) -> dict[int, int]:
        """Owned counts keyed by part id. A missing row means the user owns none."""
        with self._database.session() as session:
            rows = session.scalars(select(TrackPartStock))
            return {row.part_id: row.quantity for row in rows}

    def set_stock(self, part_id: int, quantity: int) -> None:
        """Store how many of this definition the user owns. Zero is kept on purpose."""
        amount = require_quantity(quantity)
        with self._database.session() as session:
            if session.get(TrackPartDefinition, part_id) is None:
                raise ValidationError("error.planner.part")
            row = session.get(TrackPartStock, part_id)
            if row is None:
                session.add(TrackPartStock(part_id=part_id, quantity=amount))
            else:
                row.quantity = amount

    def require(self, part_id: int) -> PartRecord:
        with self._database.session() as session:
            definition = session.get(TrackPartDefinition, part_id)
            if definition is None:
                raise ValidationError("error.planner.part")
            return self._record(session, definition)

    def read_instances(self, session: Session, track_id: int) -> tuple[PartInstance, ...]:
        rows = session.scalars(
            select(TrackPlanInstance)
            .where(TrackPlanInstance.track_id == track_id)
            .order_by(TrackPlanInstance.sort_order, TrackPlanInstance.id)
        )
        return tuple(
            PartInstance(
                id=row.id,
                part_id=row.part_id,
                x_mm=row.x_mm,
                y_mm=row.y_mm,
                z_mm=row.z_mm,
                rotation_x_deg=row.rotation_x_deg,
                rotation_y_deg=row.rotation_y_deg,
                rotation_z_deg=row.rotation_z_deg,
                start_straight=bool(row.is_start_straight),
                group_id=row.group_id,
            )
            for row in rows
        )

    def write_instances(
        self, session: Session, track_id: int, instances: tuple[PartInstance, ...]
    ) -> None:
        known = set(session.scalars(select(TrackPartDefinition.id)))
        for instance in instances:
            if instance.part_id not in known:
                raise ValidationError("error.planner.part")
        session.execute(delete(TrackPlanInstance).where(TrackPlanInstance.track_id == track_id))
        for index, instance in enumerate(instances):
            session.add(
                TrackPlanInstance(
                    id=instance.id,
                    track_id=track_id,
                    part_id=instance.part_id,
                    x_mm=instance.x_mm,
                    y_mm=instance.y_mm,
                    z_mm=instance.z_mm,
                    rotation_x_deg=instance.rotation_x_deg,
                    rotation_y_deg=instance.rotation_y_deg,
                    rotation_z_deg=instance.rotation_z_deg,
                    is_start_straight=instance.start_straight,
                    group_id=instance.group_id,
                    sort_order=index,
                )
            )

    def _insert(self, session: Session, spec: PartSpec) -> TrackPartDefinition:
        definition = TrackPartDefinition(
            article_number=spec.article_number.strip(),
            scale=require_scale(spec.scale),
            name=spec.name.strip(),
            category=spec.category,
            length_mm=spec.length_mm,
            width_mm=spec.width_mm,
            height_mm=spec.height_mm,
            radius_mm=spec.radius_mm,
            angle_deg=spec.angle_deg,
            lane_count=spec.lane_count,
            outline=[list(point) for point in spec.outline],
            slot_paths=encode_slot_paths(spec.slot_paths),
        )
        session.add(definition)
        session.flush()
        self._add_connectors(session, definition.id, spec)
        return definition

    def _add_connectors(self, session: Session, part_id: int, spec: PartSpec) -> None:
        for index, connector in enumerate(spec.connectors):
            session.add(
                TrackPartConnector(
                    part_id=part_id,
                    name=connector.name,
                    x_mm=connector.x_mm,
                    y_mm=connector.y_mm,
                    z_mm=connector.z_mm,
                    direction_deg=connector.direction_deg,
                    kind=connector.kind,
                    lanes=list(connector.lanes),
                    sort_order=index,
                )
            )

    def _record(self, session: Session, definition: TrackPartDefinition) -> PartRecord:
        connectors = session.scalars(
            select(TrackPartConnector)
            .where(TrackPartConnector.part_id == definition.id)
            .order_by(TrackPartConnector.sort_order, TrackPartConnector.id)
        )
        spec = PartSpec(
            article_number=definition.article_number,
            scale=definition.scale,
            name=definition.name,
            category=definition.category,
            length_mm=definition.length_mm,
            width_mm=definition.width_mm,
            height_mm=definition.height_mm,
            radius_mm=definition.radius_mm,
            angle_deg=definition.angle_deg,
            lane_count=definition.lane_count,
            connectors=tuple(
                ConnectorSpec(
                    name=connector.name,
                    x_mm=connector.x_mm,
                    y_mm=connector.y_mm,
                    z_mm=connector.z_mm,
                    direction_deg=connector.direction_deg,
                    kind=connector.kind,
                    lanes=tuple(int(lane) for lane in connector.lanes),
                )
                for connector in connectors
            ),
            outline=tuple((float(point[0]), float(point[1])) for point in definition.outline),
            slot_paths=decode_slot_paths(definition.slot_paths),
        )
        return PartRecord(definition.id, spec)

    def _reject_duplicate(
        self, session: Session, spec: PartSpec, *, except_id: int | None = None
    ) -> None:
        key = identity_key(spec.name, spec.article_number)
        for row in session.scalars(select(TrackPartDefinition)):
            if except_id is not None and row.id == except_id:
                continue
            if identity_key(row.name, row.article_number) == key:
                raise self._duplicate(spec)

    @staticmethod
    def _duplicate(spec: PartSpec) -> ValidationError:
        return ValidationError(
            "error.planner.part_exists",
            name=spec.name.strip(),
            article=spec.article_number.strip(),
        )
