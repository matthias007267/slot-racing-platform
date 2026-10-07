"""Load and save the part library and the instances of one plan."""

from __future__ import annotations

from sqlalchemy import delete, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from slot_racing.core.errors import ValidationError
from slot_racing.core.storage import Database
from slot_racing.modules.track_planner.inventory import (
    PackageContribution,
    PhysicalStock,
    derived_quantities,
    physical_stocks,
    require_adjustment,
    require_quantity,
)
from slot_racing.modules.track_planner.models import (
    TrackPartConnector,
    TrackPartDefinition,
    TrackPartPackage,
    TrackPartPackageContent,
    TrackPartPackageStock,
    TrackPartStock,
    TrackPlanInstance,
)
from slot_racing.modules.track_planner.packages import (
    PackageContentSpec,
    PackageContentView,
    PackageSpec,
    PackageView,
    standard_packages,
)
from slot_racing.modules.track_planner.parts import (
    AttachmentProfile,
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
from slot_racing.modules.track_planner.trace import trace


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
                if known is None:
                    continue
                if known.slot_paths and not row.slot_paths:
                    row.slot_paths = encode_slot_paths(known.slot_paths)
                if self._needs_attachment_refresh(session, row, known):
                    self._assign_attachment(row, known)
                    row.outline = [list(point) for point in known.outline]
                    row.length_mm = known.length_mm
                    row.width_mm = known.width_mm
                    row.radius_mm = known.radius_mm
                    row.angle_deg = known.angle_deg
                    row.category = known.category
                    row.lane_count = known.lane_count
                    session.execute(
                        delete(TrackPartConnector).where(TrackPartConnector.part_id == row.id)
                    )
                    self._add_connectors(session, row.id, known)
            self._ensure_packages(session)

    def add_part(self, spec: PartSpec) -> PartRecord:
        try:
            with self._database.session() as session:
                self._reject_duplicate(session, spec)
                definition = self._insert(session, spec)
                session.flush()
                created = self._record(session, definition)
        except IntegrityError as error:
            raise self._duplicate(spec) from error
        trace("PART_CREATE", result="created", part_id=created.id)
        return created

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
                self._assign_attachment(definition, spec)
                session.execute(
                    delete(TrackPartConnector).where(TrackPartConnector.part_id == part_id)
                )
                self._add_connectors(session, part_id, spec)
                session.flush()
                updated = self._record(session, definition)
        except IntegrityError as error:
            raise self._duplicate(spec) from error
        trace("PART_UPDATE", result="updated", part_id=part_id)
        return updated

    def delete_part(self, part_id: int) -> None:
        """Remove a part from the placeable library.

        A part that is already on a plan is kept. Deleting it would break that
        plan. An unused catalogue part is suppressed, because seeding would
        otherwise insert it again. An unused custom part is deleted, and its
        stock row goes with it.
        """
        trace("PART_DELETE_REQUEST", result="requested", part_id=part_id)
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
                trace("PART_DELETE_BLOCKED", result="in_use", part_id=part_id)
                raise ValidationError("error.planner.part_in_use", count=int(used))
            referenced = session.scalar(
                select(func.count())
                .select_from(TrackPartPackageContent)
                .where(TrackPartPackageContent.part_id == part_id)
            )
            key = identity_key(definition.name, definition.article_number)
            if key in catalog:
                definition.suppressed = True
            elif referenced:
                trace("PART_DELETE_BLOCKED", result="in_package", part_id=part_id)
                raise ValidationError("error.planner.part_in_package")
            else:
                session.delete(definition)
        trace("PART_DELETE_SUCCESS", result="deleted", part_id=part_id)

    def stock_quantities(self) -> dict[int, int]:
        """Physical counts keyed by part id. A missing part means none."""
        return {part_id: line.physical for part_id, line in self.stock_breakdown().items()}

    def stock_breakdown(self) -> dict[int, PhysicalStock]:
        """Derived pieces, the manual correction, and the clamped physical count."""
        self.ensure_seed()
        with self._database.session() as session:
            return self._breakdown(session)

    def set_stock(self, part_id: int, quantity: int) -> None:
        """Store a non-negative manual correction. Zero is kept on purpose.

        With no owned packages this is also the physical count. Package-derived
        pieces are added when the physical count is read.
        """
        amount = require_quantity(quantity)
        self._store_adjustment(part_id, amount, trace_change=False)

    def set_stock_adjustment(self, part_id: int, quantity: int) -> None:
        """Store the manual correction. The physical count may not go below zero."""
        amount = require_adjustment(quantity)
        self._store_adjustment(part_id, amount, trace_change=True)

    def list_packages(self) -> tuple[PackageView, ...]:
        self.ensure_seed()
        with self._database.session() as session:
            return self._package_views(session)

    def add_package(self, spec: PackageSpec) -> PackageView:
        prepared = _require_package_spec(spec)
        try:
            with self._database.session() as session:
                self._reject_package_duplicate(session, prepared)
                package = self._insert_package(session, prepared)
                session.flush()
                created = self._one_package(session, package)
        except IntegrityError as error:
            raise ValidationError("error.planner.package_exists") from error
        return created

    def set_package_stock(self, package_id: int, quantity: int) -> None:
        """Store how many of this box the user owns. Plans are left as they are."""
        amount = require_quantity(quantity)
        with self._database.session() as session:
            package = session.get(TrackPartPackage, package_id)
            if package is None:
                raise ValidationError("error.planner.package")
            row = session.get(TrackPartPackageStock, package_id)
            previous = 0 if row is None else row.quantity
            if row is None:
                session.add(TrackPartPackageStock(package_id=package_id, quantity=amount))
            else:
                row.quantity = amount
            article = package.article_number
        if previous != amount:
            trace(
                "PART_PACKAGE_STOCK_UPDATE",
                result="updated",
                package_id=package_id,
                article=article,
                quantity=amount,
            )

    def delete_package(self, package_id: int) -> None:
        """Hide a bundled package, or remove a custom one that has no stock.

        Contents of a removed custom package go with it. Plan instances and the
        manual corrections of the physical parts stay.
        """
        catalog = {_package_identity(spec) for spec in standard_packages()}
        with self._database.session() as session:
            package = session.get(TrackPartPackage, package_id)
            if package is None:
                raise ValidationError("error.planner.package")
            owned = session.get(TrackPartPackageStock, package_id)
            if owned is not None and owned.quantity > 0:
                raise ValidationError("error.planner.package_in_use")
            if _package_identity(package) in catalog:
                package.suppressed = True
                return
            if owned is not None:
                session.delete(owned)
                session.flush()
            session.delete(package)

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
                host_id=row.host_instance_id,
                attachment_slot=row.attachment_slot,
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
        # Hosts first so a reader that walks the insert order sees the parent
        # before the accessory. There is no foreign key; the plan enforces it.
        ordered = sorted(instances, key=lambda instance: instance.host_id is not None)
        for index, instance in enumerate(ordered):
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
                    host_instance_id=instance.host_id,
                    attachment_slot=instance.attachment_slot,
                )
            )

    def _store_adjustment(self, part_id: int, amount: int, *, trace_change: bool) -> None:
        self.ensure_seed()
        with self._database.session() as session:
            if session.get(TrackPartDefinition, part_id) is None:
                raise ValidationError("error.planner.part")
            if trace_change:
                produced = self._derived_map(session).get(part_id, 0)
                if produced + amount < 0:
                    raise ValidationError("error.planner.stock_adjustment")
            row = session.get(TrackPartStock, part_id)
            previous = None if row is None else row.quantity
            if row is None:
                session.add(TrackPartStock(part_id=part_id, quantity=amount))
            else:
                row.quantity = amount
        if trace_change and previous != amount:
            trace(
                "PART_STOCK_ADJUSTMENT",
                result="updated",
                part_id=part_id,
                quantity=amount,
            )

    def _breakdown(self, session: Session) -> dict[int, PhysicalStock]:
        adjustments = {row.part_id: row.quantity for row in session.scalars(select(TrackPartStock))}
        return physical_stocks(self._derived_map(session), adjustments)

    def _derived_map(self, session: Session) -> dict[int, int]:
        stocks = {
            row.package_id: row.quantity for row in session.scalars(select(TrackPartPackageStock))
        }
        contents = tuple(
            PackageContribution(row.package_id, row.part_id, row.quantity)
            for row in session.scalars(select(TrackPartPackageContent))
        )
        return derived_quantities(stocks, contents)

    def _ensure_packages(self, session: Session) -> None:
        definitions = list(session.scalars(select(TrackPartDefinition)))
        by_key = {identity_key(row.name, row.article_number): row for row in definitions}
        stored = {_package_identity(row) for row in session.scalars(select(TrackPartPackage))}
        for spec in standard_packages():
            if _package_identity(spec) in stored:
                continue
            resolved: list[tuple[TrackPartDefinition, int]] = []
            missing = False
            for content in spec.contents:
                part = by_key.get(identity_key(content.part_name, content.article_number))
                if part is None:
                    missing = True
                    break
                resolved.append((part, content.quantity))
            if missing:
                continue
            package = TrackPartPackage(
                manufacturer=spec.manufacturer.strip(),
                article_number=spec.article_number.strip(),
                name=spec.name.strip(),
            )
            session.add(package)
            session.flush()
            for part, quantity in resolved:
                session.add(
                    TrackPartPackageContent(
                        package_id=package.id,
                        part_id=part.id,
                        quantity=quantity,
                    )
                )
            stored.add(_package_identity(spec))

    def _insert_package(self, session: Session, spec: PackageSpec) -> TrackPartPackage:
        package = TrackPartPackage(
            manufacturer=spec.manufacturer.strip(),
            article_number=spec.article_number.strip(),
            name=spec.name.strip(),
        )
        session.add(package)
        session.flush()
        for content in spec.contents:
            part = self._definition_for_content(session, content)
            session.add(
                TrackPartPackageContent(
                    package_id=package.id,
                    part_id=part.id,
                    quantity=content.quantity,
                )
            )
        return package

    def _definition_for_content(
        self, session: Session, content: PackageContentSpec
    ) -> TrackPartDefinition:
        key = identity_key(content.part_name, content.article_number)
        for row in session.scalars(select(TrackPartDefinition)):
            if identity_key(row.name, row.article_number) == key:
                return row
        raise ValidationError("error.planner.part")

    def _reject_package_duplicate(self, session: Session, spec: PackageSpec) -> None:
        key = _package_identity(spec)
        for row in session.scalars(select(TrackPartPackage)):
            if _package_identity(row) == key:
                raise ValidationError("error.planner.package_exists")

    def _package_views(
        self, session: Session, *, include_suppressed: bool = False
    ) -> tuple[PackageView, ...]:
        query = select(TrackPartPackage).order_by(
            TrackPartPackage.article_number, TrackPartPackage.id
        )
        if not include_suppressed:
            query = query.where(TrackPartPackage.suppressed.is_(False))
        packages = list(session.scalars(query))
        stocks = {
            row.package_id: row.quantity for row in session.scalars(select(TrackPartPackageStock))
        }
        names = {row.id: row.name for row in session.scalars(select(TrackPartDefinition))}
        grouped: dict[int, list[PackageContentView]] = {}
        content_rows = session.scalars(
            select(TrackPartPackageContent).order_by(TrackPartPackageContent.id)
        )
        for content in content_rows:
            grouped.setdefault(content.package_id, []).append(
                PackageContentView(
                    part_id=content.part_id,
                    part_name=names.get(content.part_id, ""),
                    quantity=content.quantity,
                )
            )
        return tuple(
            PackageView(
                id=package.id,
                manufacturer=package.manufacturer,
                article_number=package.article_number,
                name=package.name,
                quantity=stocks.get(package.id, 0),
                contents=tuple(grouped.get(package.id, [])),
            )
            for package in packages
        )

    def _one_package(self, session: Session, package: TrackPartPackage) -> PackageView:
        for view in self._package_views(session, include_suppressed=True):
            if view.id == package.id:
                return view
        raise ValidationError("error.planner.package")

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
        self._assign_attachment(definition, spec)
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
            attachment=_attachment_profile(definition),
        )
        return PartRecord(definition.id, spec)

    def _needs_attachment_refresh(
        self, session: Session, row: TrackPartDefinition, spec: PartSpec
    ) -> bool:
        """True when a stored catalogue accessory still has the pre-attachment joints."""
        profile = spec.attachment
        if profile is None:
            return False
        if row.attachment_host_shape != profile.host_shape:
            return True
        if (row.attachment_slots or "") != ",".join(profile.slots):
            return True
        stored = session.scalars(
            select(TrackPartConnector).where(TrackPartConnector.part_id == row.id)
        ).all()
        return len(stored) != len(spec.connectors)

    @staticmethod
    def _assign_attachment(definition: TrackPartDefinition, spec: PartSpec) -> None:
        profile = spec.attachment
        if profile is None:
            definition.attachment_host_shape = None
            definition.attachment_slots = None
            definition.attachment_host_length_mm = None
            definition.attachment_host_radius_mm = None
            definition.attachment_host_angle_deg = None
            definition.attachment_host_lanes = None
            return
        definition.attachment_host_shape = profile.host_shape
        definition.attachment_slots = ",".join(profile.slots)
        definition.attachment_host_length_mm = profile.host_length_mm
        definition.attachment_host_radius_mm = profile.host_radius_mm
        definition.attachment_host_angle_deg = profile.host_angle_deg
        definition.attachment_host_lanes = profile.host_lanes

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


def _package_identity(item: PackageSpec | TrackPartPackage) -> tuple[str, str]:
    return (item.manufacturer.strip().casefold(), item.article_number.strip())


def _require_package_spec(spec: PackageSpec) -> PackageSpec:
    manufacturer = spec.manufacturer.strip() if isinstance(spec.manufacturer, str) else ""
    name = spec.name.strip() if isinstance(spec.name, str) else ""
    article = spec.article_number.strip() if isinstance(spec.article_number, str) else ""
    if not manufacturer or not name or not article or not spec.contents:
        raise ValidationError("error.planner.package")
    seen: set[tuple[str, str]] = set()
    contents: list[PackageContentSpec] = []
    for line in spec.contents:
        amount = line.quantity
        if isinstance(amount, bool) or not isinstance(amount, int) or amount < 1:
            raise ValidationError("error.planner.package")
        key = identity_key(line.part_name, line.article_number)
        if key in seen:
            raise ValidationError("error.planner.package")
        seen.add(key)
        contents.append(
            PackageContentSpec(line.part_name.strip(), line.article_number.strip(), amount)
        )
    return PackageSpec(manufacturer, article, name, tuple(contents))


def _attachment_profile(definition: TrackPartDefinition) -> AttachmentProfile | None:
    shape = definition.attachment_host_shape
    if not shape:
        return None
    slots = tuple(part for part in (definition.attachment_slots or "").split(",") if part)
    return AttachmentProfile(
        host_shape=shape,
        slots=slots,
        host_length_mm=definition.attachment_host_length_mm,
        host_radius_mm=definition.attachment_host_radius_mm,
        host_angle_deg=definition.attachment_host_angle_deg,
        host_lanes=definition.attachment_host_lanes,
    )
