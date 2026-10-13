"""Load and save the part library and the instances of one plan."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import replace
from typing import Any

from sqlalchemy import delete, func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from slot_racing.core.errors import ValidationError
from slot_racing.core.storage import Database
from slot_racing.core.storage.base import Base
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
    ORIGIN_CATALOG,
    ORIGIN_CUSTOM,
    ORIGIN_MODIFIED,
    AttachmentProfile,
    ConnectorSpec,
    PartInstance,
    PartRecord,
    PartSpec,
    catalog_storage_key,
    decode_geometry,
    encode_geometry,
    encode_slot_paths,
    geometry_payload_corrupt,
    identity_key,
    merge_preserved_geometry,
    require_scale,
    same_geometry,
    standard_catalog,
    validate_part_geometry,
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
                .where(
                    TrackPartDefinition.suppressed.is_(False),
                    TrackPartDefinition.is_current.is_(True),
                )
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
            stored.update(_stored_catalog_keys(rows))
            for spec in standard_catalog():
                key = identity_key(spec.name, spec.article_number)
                if key not in stored:
                    self._insert(session, spec, origin=ORIGIN_CATALOG)
                    stored.add(key)
            for row in rows:
                if not row.is_current or row.origin != ORIGIN_CATALOG:
                    continue
                known = catalog.get(identity_key(row.name, row.article_number))
                if known is None and row.catalog_key and "\t" in row.catalog_key:
                    stored_name, stored_article = row.catalog_key.split("\t", 1)
                    known = catalog.get((stored_name, stored_article))
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
        validate_part_geometry(spec)
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

    def update_part(self, part_id: int, spec: PartSpec, *, origin: str | None = None) -> PartRecord:
        """Save a definition.

        A geometry change of a revision that a plan already uses inserts the next
        revision and leaves the used row untouched. A rename of an unused revision
        stays on the same row. ``origin`` forces catalog, modified or custom.
        """
        try:
            with self._database.session() as session:
                definition = session.get(TrackPartDefinition, part_id)
                if definition is None:
                    raise ValidationError("error.planner.part")
                if not definition.is_current:
                    definition = self._current_of(session, definition.lineage_id)
                stored_record = self._record(session, definition)
                stored = stored_record.spec
                raw_paths = definition.slot_paths
                corrupt = stored_record.geometry_corrupt
                spec = merge_preserved_geometry(stored, spec)
                if corrupt and not same_geometry(stored, spec):
                    raise ValidationError("error.planner.geometry")
                validate_part_geometry(spec)
                self._reject_duplicate(session, spec, except_id=definition.id)
                geometry_changed = not same_geometry(stored, spec)
                chosen = origin
                if chosen is None:
                    chosen = (
                        ORIGIN_MODIFIED
                        if geometry_changed and definition.catalog_key
                        else definition.origin
                    )
                if geometry_changed and definition.id in _referenced_part_ids(session):
                    updated = self._fork(session, definition, spec, chosen)
                else:
                    self._apply(session, definition, spec, chosen)
                    if corrupt:
                        definition.slot_paths = raw_paths
                    session.flush()
                    updated = self._record(session, definition)
        except IntegrityError as error:
            raise self._duplicate(spec) from error
        trace("PART_UPDATE", result="updated", part_id=updated.id)
        return updated

    def restore_catalog(self, part_id: int) -> PartRecord:
        """Put the bundled geometry back. Plans that still use the edited row stay on it."""
        with self._database.session() as session:
            definition = session.get(TrackPartDefinition, part_id)
            if definition is None or not definition.catalog_key:
                raise ValidationError("error.planner.part")
            key = definition.catalog_key
            description = definition.description or ""
        spec = _catalog_by_key().get(key)
        if spec is None:
            raise ValidationError("error.planner.part")
        restored = replace(spec, manufacturer="Carrera", description=description)
        return self.update_part(part_id, restored, origin=ORIGIN_CATALOG)

    def records_for(self, part_ids: Sequence[int]) -> tuple[PartRecord, ...]:
        """Definitions for these ids, including revisions the library no longer offers."""
        self.ensure_seed()
        wanted = {part_id for part_id in part_ids if isinstance(part_id, int)}
        if not wanted:
            return ()
        with self._database.session() as session:
            rows = session.scalars(
                select(TrackPartDefinition).where(TrackPartDefinition.id.in_(wanted))
            )
            return tuple(self._record(session, row) for row in rows)

    def current_part_ids(self) -> dict[int, int]:
        """Map every stored id to the current revision of its lineage."""
        self.ensure_seed()
        with self._database.session() as session:
            rows = session.execute(
                select(
                    TrackPartDefinition.id,
                    TrackPartDefinition.lineage_id,
                    TrackPartDefinition.is_current,
                )
            ).all()
        current = {lineage: part_id for part_id, lineage, is_current in rows if is_current}
        return {part_id: current.get(lineage, part_id) for part_id, lineage, _current in rows}

    def track_ids_on_previous_geometry(self, part_id: int) -> tuple[int, ...]:
        """Tracks whose saved plan still points at an older revision of this part."""
        self.ensure_seed()
        with self._database.session() as session:
            definition = session.get(TrackPartDefinition, part_id)
            if definition is None:
                raise ValidationError("error.planner.part")
            current = self._current_of(session, definition.lineage_id)
            previous = set(
                session.scalars(
                    select(TrackPartDefinition.id).where(
                        TrackPartDefinition.lineage_id == definition.lineage_id,
                        TrackPartDefinition.id != current.id,
                    )
                )
            )
            if not previous:
                return ()
            found = set(
                session.scalars(
                    select(TrackPlanInstance.track_id)
                    .where(TrackPlanInstance.part_id.in_(previous))
                    .distinct()
                )
            )
            layouts = _layouts()
            for track_id, data in session.execute(
                select(layouts.c.track_id, layouts.c.data).where(layouts.c.name == "plan")
            ):
                if _part_ids_in_document(data) & previous:
                    found.add(int(track_id))
        return tuple(sorted(int(track_id) for track_id in found))

    def adopt_revision(self, part_id: int, track_ids: Sequence[int]) -> int:
        """Point the chosen tracks at the current geometry. Position and rotation stay.

        Archived layout rows are not rewritten. An empty track list changes nothing.
        The result is how many of the chosen tracks changed.
        """
        chosen = {int(track_id) for track_id in track_ids}
        if not chosen:
            return 0
        with self._database.session() as session:
            definition = session.get(TrackPartDefinition, part_id)
            if definition is None:
                raise ValidationError("error.planner.part")
            current = self._current_of(session, definition.lineage_id)
            previous = set(
                session.scalars(
                    select(TrackPartDefinition.id).where(
                        TrackPartDefinition.lineage_id == current.lineage_id,
                        TrackPartDefinition.id != current.id,
                    )
                )
            )
            if not previous:
                return 0
            affected = set(
                session.scalars(
                    select(TrackPlanInstance.track_id)
                    .where(
                        TrackPlanInstance.track_id.in_(chosen),
                        TrackPlanInstance.part_id.in_(previous),
                    )
                    .distinct()
                )
            )
            session.execute(
                update(TrackPlanInstance)
                .where(
                    TrackPlanInstance.track_id.in_(chosen),
                    TrackPlanInstance.part_id.in_(previous),
                )
                .values(part_id=current.id)
            )
            layouts = _layouts()
            for layout_id, track_id, data in session.execute(
                select(layouts.c.id, layouts.c.track_id, layouts.c.data).where(
                    layouts.c.name == "plan",
                    layouts.c.track_id.in_(chosen),
                )
            ):
                rewritten = _retarget_document(data, previous, current.id)
                if rewritten is not data:
                    session.execute(
                        update(layouts).where(layouts.c.id == layout_id).values(data=rewritten)
                    )
                    affected.add(int(track_id))
        return len(affected)

    def current_with_article(self, article_number: str) -> tuple[PartRecord, ...]:
        """Current parts that share this article number. Several matches are left as they are."""
        from slot_racing.modules.track_planner.parts import normalize_article

        wanted = normalize_article(article_number)
        return tuple(
            record
            for record in self.list_parts()
            if normalize_article(record.spec.article_number) == wanted
        )

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
            lineage_ids = set(
                session.scalars(
                    select(TrackPartDefinition.id).where(
                        TrackPartDefinition.lineage_id == definition.lineage_id
                    )
                )
            )
            used = session.scalar(
                select(func.count())
                .select_from(TrackPlanInstance)
                .where(TrackPlanInstance.part_id.in_(lineage_ids))
            )
            if not used and lineage_ids & _referenced_part_ids(session):
                used = 1
            if used:
                trace("PART_DELETE_BLOCKED", result="in_use", part_id=part_id)
                raise ValidationError("error.planner.part_in_use", count=int(used))
            referenced = session.scalar(
                select(func.count())
                .select_from(TrackPartPackageContent)
                .where(TrackPartPackageContent.part_id.in_(lineage_ids))
            )
            session.execute(
                delete(TrackPartDefinition).where(
                    TrackPartDefinition.lineage_id == definition.lineage_id,
                    TrackPartDefinition.is_current.is_(False),
                )
            )
            key = identity_key(definition.name, definition.article_number)
            if key in catalog or definition.catalog_key:
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
            definition = session.get(TrackPartDefinition, part_id)
            if definition is None:
                raise ValidationError("error.planner.part")
            if not definition.is_current:
                definition = self._current_of(session, definition.lineage_id)
            part_id = definition.id
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
        by_key: dict[tuple[str, str], TrackPartDefinition] = {}
        for row in definitions:
            key = identity_key(row.name, row.article_number)
            previous = by_key.get(key)
            if previous is None or (row.is_current and not previous.is_current):
                by_key[key] = row
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
        found: TrackPartDefinition | None = None
        for row in session.scalars(select(TrackPartDefinition)):
            if identity_key(row.name, row.article_number) != key:
                continue
            if row.is_current:
                return row
            found = row
        if found is not None:
            return found
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

    def _insert(
        self,
        session: Session,
        spec: PartSpec,
        *,
        origin: str = ORIGIN_CUSTOM,
        lineage_id: str | None = None,
        revision: int = 1,
        catalog_key: str | None = None,
    ) -> TrackPartDefinition:
        from uuid import uuid4

        if origin == ORIGIN_CATALOG and catalog_key is None:
            catalog_key = catalog_storage_key(spec.name, spec.article_number)
        manufacturer = spec.manufacturer.strip()
        if not manufacturer and origin == ORIGIN_CATALOG:
            manufacturer = "Carrera"
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
            slot_paths=encode_geometry(spec),
            lineage_id=lineage_id or uuid4().hex,
            revision=revision,
            is_current=True,
            origin=origin,
            catalog_key=catalog_key,
            manufacturer=manufacturer,
            description=spec.description.strip() or None,
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
        corrupt = geometry_payload_corrupt(definition.slot_paths)
        slots, contours, edges = decode_geometry(definition.slot_paths)
        profile = _attachment_profile(definition)
        if profile is not None:
            matched = next((edge.spans for edge in edges if edge.slot in profile.slots), ())
            profile = replace(profile, edge=matched)
            edges = ()
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
            slot_paths=slots,
            attachment=profile,
            contours=contours,
            border_edges=edges,
            manufacturer=definition.manufacturer or "",
            description=definition.description or "",
        )
        return PartRecord(
            definition.id,
            spec,
            lineage_id=definition.lineage_id,
            revision=definition.revision,
            origin=definition.origin,
            is_current=definition.is_current,
            geometry_corrupt=corrupt,
        )

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
            if not row.is_current:
                continue
            if except_id is not None and row.id == except_id:
                continue
            if identity_key(row.name, row.article_number) == key:
                raise self._duplicate(spec)

    def _current_of(self, session: Session, lineage_id: str) -> TrackPartDefinition:
        current = session.scalar(
            select(TrackPartDefinition).where(
                TrackPartDefinition.lineage_id == lineage_id,
                TrackPartDefinition.is_current.is_(True),
            )
        )
        if current is None:
            raise ValidationError("error.planner.part")
        return current

    def _apply(
        self, session: Session, definition: TrackPartDefinition, spec: PartSpec, origin: str
    ) -> None:
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
        definition.slot_paths = encode_geometry(spec)
        definition.origin = origin
        definition.manufacturer = spec.manufacturer.strip()
        definition.description = spec.description.strip() or None
        self._assign_attachment(definition, spec)
        session.execute(
            delete(TrackPartConnector).where(TrackPartConnector.part_id == definition.id)
        )
        self._add_connectors(session, definition.id, spec)

    def _fork(
        self,
        session: Session,
        definition: TrackPartDefinition,
        spec: PartSpec,
        origin: str,
    ) -> PartRecord:
        """Insert the next revision and move stock onto it. The used row stays frozen."""
        highest = session.scalar(
            select(func.max(TrackPartDefinition.revision)).where(
                TrackPartDefinition.lineage_id == definition.lineage_id
            )
        )
        definition.is_current = False
        session.flush()
        created = self._insert(
            session,
            spec,
            origin=origin,
            lineage_id=definition.lineage_id,
            revision=int(highest or definition.revision) + 1,
            catalog_key=definition.catalog_key,
        )
        _move_stock(session, definition.id, created.id)
        _move_package_contents(session, definition.id, created.id)
        session.flush()
        return self._record(session, created)

    @staticmethod
    def _duplicate(spec: PartSpec) -> ValidationError:
        return ValidationError(
            "error.planner.part_exists",
            name=spec.name.strip(),
            article=spec.article_number.strip(),
        )


def _stored_catalog_keys(rows: Sequence[TrackPartDefinition]) -> set[tuple[str, str]]:
    stored: set[tuple[str, str]] = set()
    for row in rows:
        if not row.catalog_key or "\t" not in row.catalog_key:
            continue
        name, article = row.catalog_key.split("\t", 1)
        stored.add((name, article))
    return stored


def _catalog_by_key() -> dict[str, PartSpec]:
    return {
        catalog_storage_key(spec.name, spec.article_number): spec for spec in standard_catalog()
    }


def _layouts() -> Any:
    try:
        return Base.metadata.tables["track_layouts"]
    except KeyError as error:
        raise ValidationError("error.planner.invalid") from error


def _referenced_part_ids(session: Session) -> set[int]:
    found = {int(part_id) for part_id in session.scalars(select(TrackPlanInstance.part_id))}
    for data in session.scalars(select(_layouts().c.data)):
        found.update(_part_ids_in_document(data))
    return found


def _part_ids_in_document(data: object) -> set[int]:
    if not isinstance(data, dict):
        return set()
    raw = data.get("instances")
    if not isinstance(raw, list):
        return set()
    found: set[int] = set()
    for item in raw:
        if not isinstance(item, dict):
            continue
        part_id = item.get("part_id")
        if isinstance(part_id, bool) or not isinstance(part_id, int):
            continue
        found.add(part_id)
    return found


def _retarget_document(data: object, previous: set[int], current_id: int) -> object:
    if not isinstance(data, dict):
        return data
    raw = data.get("instances")
    if not isinstance(raw, list):
        return data
    changed = False
    instances: list[object] = []
    for item in raw:
        if isinstance(item, dict) and item.get("part_id") in previous:
            instances.append({**item, "part_id": current_id})
            changed = True
        else:
            instances.append(item)
    if not changed:
        return data
    updated = dict(data)
    updated["instances"] = instances
    return updated


def _move_stock(session: Session, old_id: int, new_id: int) -> None:
    row = session.get(TrackPartStock, old_id)
    if row is None:
        return
    amount = row.quantity
    session.delete(row)
    session.flush()
    session.add(TrackPartStock(part_id=new_id, quantity=amount))


def _move_package_contents(session: Session, old_id: int, new_id: int) -> None:
    session.execute(
        update(TrackPartPackageContent)
        .where(TrackPartPackageContent.part_id == old_id)
        .values(part_id=new_id)
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
