"""Load and save the part library and the instances of one plan."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, replace
from typing import Any

from sqlalchemy import delete, func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from slot_racing.core.errors import ValidationError
from slot_racing.core.storage import Database
from slot_racing.core.storage.base import Base
from slot_racing.modules.track_planner.catalog_import import (
    LibraryDraft,
    PackageDraft,
    TemplateDraft,
    load_carrera_library,
    source_anchor,
)
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
    TrackPartTemplate,
    TrackPartTemplateMember,
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
    PATH_SLOT,
    AttachmentProfile,
    ConnectorSpec,
    PartInstance,
    PartRecord,
    PartSpec,
    SlotPath,
    catalog_storage_key,
    decode_geometry,
    encode_geometry,
    geometry_payload_corrupt,
    identity_key,
    merge_preserved_geometry,
    require_scale,
    same_geometry,
    standard_catalog,
    validate_part_geometry,
)
from slot_racing.modules.track_planner.trace import trace


@dataclass(frozen=True, slots=True)
class TemplateMemberView:
    part_id: int
    x_mm: float
    y_mm: float
    angle_deg: float


@dataclass(frozen=True, slots=True)
class TemplateView:
    id: int
    source_id: str
    article_number: str
    name: str
    members: tuple[TemplateMemberView, ...]


@dataclass(frozen=True, slots=True)
class CatalogEntryChange:
    """One definition the catalogue reconcile will add, update, remove or retire."""

    kind: str
    article_number: str
    name: str
    stock: int | None = None


@dataclass(frozen=True, slots=True)
class CatalogReconcileReport:
    """What a catalogue reconcile changes. Empty means the library already matches.

    ``removed`` entries had no reference from a plan, a package, a template or
    the collection, so the row and its stock go away. ``retired`` entries stay
    in the database because something still points at them. They leave the
    active library and the collection picker; the reference keeps resolving.
    Stock of a part that is still in the catalogue stays on that part's id.
    """

    added: tuple[CatalogEntryChange, ...] = ()
    updated: tuple[CatalogEntryChange, ...] = ()
    removed: tuple[CatalogEntryChange, ...] = ()
    retired: tuple[CatalogEntryChange, ...] = ()

    def is_empty(self) -> bool:
        return not (self.added or self.updated or self.removed or self.retired)


class PartLibrary:
    def __init__(self, database: Database) -> None:
        self._database = database

    def list_parts(self) -> tuple[PartRecord, ...]:
        self.ensure_seed()
        return self.list_active()

    def list_active(self) -> tuple[PartRecord, ...]:
        """Current selectable parts. This does not import or retire anything."""
        with self._database.session() as session:
            rows = session.scalars(
                select(TrackPartDefinition)
                .where(
                    TrackPartDefinition.suppressed.is_(False),
                    TrackPartDefinition.is_current.is_(True),
                    TrackPartDefinition.legacy.is_(False),
                    TrackPartDefinition.selectable.is_(True),
                )
                .order_by(TrackPartDefinition.id)
            )
            return tuple(self._record(session, row) for row in rows)

    def ensure_seed(self) -> None:
        """Bring the stored catalogue in line with the bundled source files.

        The first import used to stop as soon as the active straight existed,
        and a row that already had a source id was never compared again. A
        supplied definition that has since left the files therefore stayed in
        the active library. This pass adds missing source rows and drops or
        retires those obsolete supplied rows. It does not rewrite geometry the
        user already has, and it does not recreate a part they suppressed.
        Source paint that was dropped by an older import is filled in on the
        same row when the driving grooves still match the file.
        """
        with self._database.session() as session:
            if not self._catalog_is_current(session):
                self._reconcile(session, update_existing=False, import_missing=True)
            self._backfill_source_paint(session)

    def _backfill_source_paint(self, session: Session) -> None:
        """Put source drawings back on a catalogue row whose grooves still match.

        The same definition id keeps stock, plans and templates. A modified row
        and a groove that no longer matches the file are left alone. A second
        call writes nothing.
        """
        draft = {spec.source_id: spec for spec in load_carrera_library().parts}
        rows = session.scalars(select(TrackPartDefinition)).all()
        for row in rows:
            spec = draft.get(row.source_id)
            if (
                spec is None
                or not row.is_current
                or row.origin != ORIGIN_CATALOG
                or geometry_payload_corrupt(row.slot_paths)
            ):
                continue
            stored = self._record(session, row).spec
            if _slot_identity(stored.slot_paths) != _slot_identity(spec.slot_paths):
                continue
            paint = tuple(path for path in spec.slot_paths if path.kind != PATH_SLOT)
            stored_paint = tuple(path for path in stored.slot_paths if path.kind != PATH_SLOT)
            if stored_paint == paint:
                continue
            physical = tuple(path for path in stored.slot_paths if path.kind == PATH_SLOT)
            row.slot_paths = encode_geometry(replace(stored, slot_paths=physical + paint))

    def retire_bundled_catalog(self) -> int:
        """Retire or remove supplied definitions the current catalogue no longer has.

        Returns how many parts were retired or removed. Stock on a part that
        stays, plans and layouts stay. The active library no longer lists them.
        """
        with self._database.session() as session:
            report = self._reconcile(
                session,
                update_existing=False,
                import_missing=False,
                retire_legacy_packages=True,
            )
            return sum(1 for entry in (*report.retired, *report.removed) if entry.kind == "part")

    def preview_library_reset(self) -> CatalogReconcileReport:
        """Describe a reset without writing. A second call after the reset is empty."""
        with self._database.session() as session:
            return self._reconcile(session, update_existing=True, import_missing=True, dry_run=True)

    def reset_to_catalog(self) -> CatalogReconcileReport:
        """Add, update and drop supplied rows so the library matches the bundled catalogue.

        One transaction. Custom parts stay. Stock and package counts of a part
        or box that is still supplied stay on the same id. Repeating the reset
        adds nothing and changes nothing.
        """
        with self._database.session() as session:
            return self._reconcile(session, update_existing=True, import_missing=True)

    def reset_collection_quantities(self) -> int:
        """Set every personal part and package quantity to zero.

        One transaction, and only rows that are not already zero. Hidden,
        filtered and retired owners are included because the write does not
        look at the dialog. Definitions, package contents and plans stay.
        The catalogue is not reconciled. A second call changes nothing.
        """
        with self._database.session() as session:
            part_ids = tuple(
                session.scalars(select(TrackPartStock.part_id).where(TrackPartStock.quantity != 0))
            )
            package_ids = tuple(
                session.scalars(
                    select(TrackPartPackageStock.package_id).where(
                        TrackPartPackageStock.quantity != 0
                    )
                )
            )
            if part_ids:
                session.execute(
                    update(TrackPartStock)
                    .where(TrackPartStock.part_id.in_(part_ids))
                    .values(quantity=0)
                )
            if package_ids:
                session.execute(
                    update(TrackPartPackageStock)
                    .where(TrackPartPackageStock.package_id.in_(package_ids))
                    .values(quantity=0)
                )
            changed = len(part_ids) + len(package_ids)
        if changed:
            trace("COLLECTION_QUANTITIES_RESET", result="reset", rows=changed)
        return changed

    def import_carrera_library(self) -> LibraryDraft:
        """Insert source parts that are not stored yet. A second call adds nothing."""
        with self._database.session() as session:
            return self._import_sources(session)

    def _catalog_is_current(self, session: Session) -> bool:
        """True when nothing supplied is missing or still active after leaving the catalogue."""
        draft = load_carrera_library()
        catalog_ids = {spec.source_id for spec in draft.parts}
        rows = list(session.scalars(select(TrackPartDefinition)))
        current = [row for row in rows if row.is_current]
        by_source = {row.source_id: row for row in current if row.source_id}
        for source_id in catalog_ids:
            row = by_source.get(source_id)
            if row is None or (row.legacy and not row.suppressed):
                return False
        for row in current:
            if not _is_supplied_part(row) or row.source_id in catalog_ids:
                continue
            if not row.legacy and not row.suppressed:
                return False
        if not self._hidden_obsolete_parts_are_kept(session, rows, catalog_ids):
            return False
        template_ids = {
            row.source_id for row in session.scalars(select(TrackPartTemplate)) if row.source_id
        }
        draft_templates = {template.source_id for template in draft.templates}
        if template_ids != draft_templates:
            return False
        draft_packages = {_draft_package_identity(package) for package in draft.packages}
        packages = list(session.scalars(select(TrackPartPackage)))
        stored = {_package_identity(package): package for package in packages}
        if not draft_packages <= set(stored):
            return False
        for package in packages:
            if not _is_supplied_package(package, draft_packages):
                continue
            identity = _package_identity(package)
            if identity in draft_packages:
                if not package.bundled:
                    return False
                continue
            if not package.suppressed:
                return False
        return True

    def _hidden_obsolete_parts_are_kept(
        self,
        session: Session,
        rows: Sequence[TrackPartDefinition],
        catalog_ids: set[str],
    ) -> bool:
        """False when a hidden obsolete part could be deleted without breaking a reference."""
        hidden = [
            row
            for row in rows
            if row.is_current
            and _is_supplied_part(row)
            and row.source_id not in catalog_ids
            and (row.legacy or row.suppressed)
        ]
        if not hidden:
            return True
        blocking = _blocking_part_ids(session)
        lineages: dict[str, list[int]] = {}
        for row in rows:
            lineages.setdefault(row.lineage_id, []).append(row.id)
        return all(set(lineages.get(row.lineage_id, [])) & blocking for row in hidden)

    def _reconcile(
        self,
        session: Session,
        *,
        update_existing: bool,
        import_missing: bool,
        dry_run: bool = False,
        retire_legacy_packages: bool = False,
    ) -> CatalogReconcileReport:
        draft = load_carrera_library()
        added: list[CatalogEntryChange] = []
        updated: list[CatalogEntryChange] = []
        removed: list[CatalogEntryChange] = []
        retired: list[CatalogEntryChange] = []
        self._retire_obsolete_templates(session, draft, removed=removed, dry_run=dry_run)
        by_source = self._sync_catalog_parts(
            session,
            draft,
            update_existing=update_existing,
            import_missing=import_missing,
            dry_run=dry_run,
            added=added,
            updated=updated,
        )
        self._sync_catalog_templates(
            session,
            draft,
            by_source,
            update_existing=update_existing,
            import_missing=import_missing,
            dry_run=dry_run,
            added=added,
            updated=updated,
        )
        self._sync_catalog_packages(
            session,
            draft,
            by_source,
            update_existing=update_existing,
            import_missing=import_missing,
            dry_run=dry_run,
            retire_legacy_packages=retire_legacy_packages,
            added=added,
            updated=updated,
            removed=removed,
            retired=retired,
        )
        self._retire_obsolete_parts(
            session,
            draft,
            dry_run=dry_run,
            removed=removed,
            retired=retired,
        )
        return CatalogReconcileReport(
            added=tuple(added),
            updated=tuple(updated),
            removed=tuple(removed),
            retired=tuple(retired),
        )

    def _import_sources(self, session: Session) -> LibraryDraft:
        draft = load_carrera_library()
        stored = {
            row.source_id for row in session.scalars(select(TrackPartDefinition)) if row.source_id
        }
        for spec in draft.parts:
            if spec.source_id in stored:
                continue
            self._insert(session, spec, origin=ORIGIN_CATALOG, catalog_key=spec.source_id)
            stored.add(spec.source_id)
        by_source = {
            row.source_id: row
            for row in session.scalars(select(TrackPartDefinition))
            if row.source_id and row.is_current
        }
        present = {
            row.source_id for row in session.scalars(select(TrackPartTemplate)) if row.source_id
        }
        for template in draft.templates:
            if template.source_id in present:
                continue
            self._insert_template(session, template, by_source)
        self._import_packages(session, draft, by_source)
        return draft

    def _insert_template(
        self,
        session: Session,
        template: TemplateDraft,
        by_source: dict[str, TrackPartDefinition],
    ) -> None:
        row = TrackPartTemplate(
            source_id=template.source_id,
            article_number=template.article_number,
            name=template.name,
        )
        session.add(row)
        session.flush()
        self._append_template_members(session, row.id, template, by_source)

    def _import_packages(
        self,
        session: Session,
        draft: LibraryDraft,
        by_source: dict[str, TrackPartDefinition],
    ) -> None:
        stored = {_package_identity(row) for row in session.scalars(select(TrackPartPackage))}
        for package in draft.packages:
            identity = ("carrera", package.article_number.strip())
            if identity in stored:
                continue
            contents: list[PackageContentSpec] = []
            missing = False
            for source_id, name, article in package.contents:
                part = by_source.get(source_id)
                if part is None:
                    missing = True
                    break
                contents.append(PackageContentSpec(name, article, 1, source_id=part.source_id))
            if missing or not contents:
                continue
            self._insert_package(
                session,
                PackageSpec("Carrera", package.article_number, package.name, tuple(contents)),
                bundled=True,
            )
            stored.add(identity)

    def _retire_obsolete_templates(
        self,
        session: Session,
        draft: LibraryDraft,
        *,
        removed: list[CatalogEntryChange],
        dry_run: bool,
    ) -> None:
        keep = {template.source_id for template in draft.templates}
        for row in list(session.scalars(select(TrackPartTemplate))):
            if not row.source_id or row.source_id in keep:
                continue
            removed.append(_entry_change("template", row.article_number, row.name))
            if not dry_run:
                session.delete(row)
        if not dry_run:
            session.flush()

    def _sync_catalog_parts(
        self,
        session: Session,
        draft: LibraryDraft,
        *,
        update_existing: bool,
        import_missing: bool,
        dry_run: bool,
        added: list[CatalogEntryChange],
        updated: list[CatalogEntryChange],
    ) -> dict[str, TrackPartDefinition]:
        current = {
            row.source_id: row
            for row in session.scalars(select(TrackPartDefinition))
            if row.source_id and row.is_current
        }
        for spec in draft.parts:
            row = current.get(spec.source_id)
            if row is None:
                if not import_missing:
                    continue
                added.append(_entry_change("part", spec.article_number, spec.name))
                if dry_run:
                    continue
                inserted = self._insert(
                    session, spec, origin=ORIGIN_CATALOG, catalog_key=spec.source_id
                )
                current[spec.source_id] = inserted
                continue
            if row.suppressed:
                continue
            if row.legacy:
                added.append(_entry_change("part", spec.article_number, spec.name))
                if not dry_run:
                    row.legacy = False
                    row.selectable = spec.selectable
                    if update_existing and not self._catalog_part_matches(session, row, spec):
                        self._apply(session, row, spec, ORIGIN_CATALOG)
                        row.catalog_key = spec.source_id
                        row.legacy = False
                        row.selectable = spec.selectable
                continue
            if not update_existing or self._catalog_part_matches(session, row, spec):
                continue
            updated.append(
                _entry_change(
                    "part", row.article_number, row.name, _stock_quantity(session, row.id)
                )
            )
            if dry_run:
                continue
            self._apply(session, row, spec, ORIGIN_CATALOG)
            row.catalog_key = spec.source_id
            row.legacy = False
            row.selectable = spec.selectable
        if not dry_run:
            session.flush()
        return {
            row.source_id: row
            for row in session.scalars(select(TrackPartDefinition))
            if row.source_id and row.is_current
        }

    def _catalog_part_matches(
        self, session: Session, row: TrackPartDefinition, spec: PartSpec
    ) -> bool:
        if row.legacy or row.origin != ORIGIN_CATALOG or row.selectable != spec.selectable:
            return False
        if (row.catalog_key or "") != spec.source_id:
            return False
        stored = self._record(session, row).spec
        return (
            stored.name == spec.name
            and stored.article_number == spec.article_number
            and stored.piece_key == spec.piece_key
            and stored.systems == spec.systems
            and stored.manufacturer == (spec.manufacturer.strip() or "Carrera")
            and stored.description == spec.description
            and stored.source_id == spec.source_id
            and same_geometry(stored, spec)
        )

    def _sync_catalog_templates(
        self,
        session: Session,
        draft: LibraryDraft,
        by_source: dict[str, TrackPartDefinition],
        *,
        update_existing: bool,
        import_missing: bool,
        dry_run: bool,
        added: list[CatalogEntryChange],
        updated: list[CatalogEntryChange],
    ) -> None:
        stored = {
            row.source_id: row
            for row in session.scalars(select(TrackPartTemplate))
            if row.source_id
        }
        members = list(session.scalars(select(TrackPartTemplateMember)))
        grouped: dict[int, list[TrackPartTemplateMember]] = {}
        for member in members:
            grouped.setdefault(member.template_id, []).append(member)
        for template in draft.templates:
            row = stored.get(template.source_id)
            if row is None:
                if not import_missing:
                    continue
                added.append(_entry_change("template", template.article_number, template.name))
                if not dry_run:
                    self._insert_template(session, template, by_source)
                continue
            if not update_existing or _template_members_match(
                grouped.get(row.id, []), template, by_source
            ):
                continue
            updated.append(_entry_change("template", row.article_number, row.name))
            if dry_run:
                continue
            session.execute(
                delete(TrackPartTemplateMember).where(TrackPartTemplateMember.template_id == row.id)
            )
            session.flush()
            self._append_template_members(session, row.id, template, by_source)
            row.article_number = template.article_number
            row.name = template.name

    def _append_template_members(
        self,
        session: Session,
        template_id: int,
        template: TemplateDraft,
        by_source: dict[str, TrackPartDefinition],
    ) -> None:
        for index, member in enumerate(template.members):
            part = by_source.get(member.source_id)
            if part is None:
                continue
            session.add(
                TrackPartTemplateMember(
                    template_id=template_id,
                    part_id=part.id,
                    x_mm=member.x_mm,
                    y_mm=member.y_mm,
                    angle_deg=member.angle_deg,
                    sort_order=index,
                )
            )

    def _sync_catalog_packages(
        self,
        session: Session,
        draft: LibraryDraft,
        by_source: dict[str, TrackPartDefinition],
        *,
        update_existing: bool,
        import_missing: bool,
        dry_run: bool,
        retire_legacy_packages: bool,
        added: list[CatalogEntryChange],
        updated: list[CatalogEntryChange],
        removed: list[CatalogEntryChange],
        retired: list[CatalogEntryChange],
    ) -> None:
        draft_packages = {_draft_package_identity(package): package for package in draft.packages}
        old_packages = {_package_identity(spec) for spec in standard_packages()}
        stored = {
            _package_identity(package): package
            for package in session.scalars(select(TrackPartPackage))
        }
        for identity, package in draft_packages.items():
            row = stored.get(identity)
            if row is None:
                if not import_missing:
                    continue
                if dry_run:
                    added.append(_entry_change("package", package.article_number, package.name))
                    continue
                contents = _draft_package_contents(package, by_source)
                if contents is None:
                    continue
                added.append(_entry_change("package", package.article_number, package.name))
                self._insert_package(
                    session,
                    PackageSpec("Carrera", package.article_number, package.name, contents),
                    bundled=True,
                )
                continue
            if not dry_run and not row.bundled:
                row.bundled = True
            if row.suppressed or not update_existing:
                continue
            if _package_contents_match(session, row, package, by_source):
                continue
            contents = _draft_package_contents(package, by_source)
            if contents is None:
                continue
            quantity = _package_quantity(session, row.id)
            updated.append(_entry_change("package", row.article_number, row.name, quantity))
            if dry_run:
                continue
            row.name = package.name
            session.execute(
                delete(TrackPartPackageContent).where(TrackPartPackageContent.package_id == row.id)
            )
            session.flush()
            for content in contents:
                part = self._definition_for_content(session, content)
                session.add(
                    TrackPartPackageContent(
                        package_id=row.id,
                        part_id=part.id,
                        quantity=content.quantity,
                    )
                )
        if not dry_run:
            session.flush()
        for row in list(session.scalars(select(TrackPartPackage))):
            identity = _package_identity(row)
            legacy_box = retire_legacy_packages and identity in old_packages
            if not _is_supplied_package(row, set(draft_packages)) and not legacy_box:
                continue
            if identity in draft_packages:
                continue
            if row.suppressed:
                if not dry_run:
                    row.bundled = True
                continue
            retired.append(
                _entry_change(
                    "package",
                    row.article_number,
                    row.name,
                    _package_quantity(session, row.id),
                )
            )
            if not dry_run:
                row.suppressed = True
                row.bundled = True
        if not dry_run:
            session.flush()

    def _retire_obsolete_parts(
        self,
        session: Session,
        draft: LibraryDraft,
        *,
        dry_run: bool,
        removed: list[CatalogEntryChange],
        retired: list[CatalogEntryChange],
    ) -> None:
        catalog_ids = {spec.source_id for spec in draft.parts}
        rows = list(session.scalars(select(TrackPartDefinition)))
        lineages: dict[str, list[TrackPartDefinition]] = {}
        for row in rows:
            lineages.setdefault(row.lineage_id, []).append(row)
        blocking = _blocking_part_ids(session)
        for row in rows:
            if not row.is_current or not _is_supplied_part(row) or row.source_id in catalog_ids:
                continue
            members = lineages.get(row.lineage_id, [row])
            member_ids = {member.id for member in members}
            stock = _stock_quantity(session, row.id)
            if member_ids & blocking:
                if row.legacy and not row.selectable:
                    continue
                retired.append(_entry_change("part", row.article_number, row.name, stock))
                if dry_run:
                    continue
                row.legacy = True
                row.selectable = False
                continue
            removed.append(_entry_change("part", row.article_number, row.name, stock))
            if dry_run:
                continue
            for member in members:
                session.delete(member)
        if not dry_run:
            session.flush()

    def list_templates(self) -> tuple[TemplateView, ...]:
        self.ensure_seed()
        with self._database.session() as session:
            templates = list(
                session.scalars(select(TrackPartTemplate).order_by(TrackPartTemplate.id))
            )
            members = list(
                session.scalars(
                    select(TrackPartTemplateMember).order_by(
                        TrackPartTemplateMember.sort_order, TrackPartTemplateMember.id
                    )
                )
            )
        grouped: dict[int, list[TemplateMemberView]] = {}
        for member in members:
            grouped.setdefault(member.template_id, []).append(
                TemplateMemberView(member.part_id, member.x_mm, member.y_mm, member.angle_deg)
            )
        return tuple(
            TemplateView(
                row.id,
                row.source_id,
                row.article_number,
                row.name,
                tuple(grouped.get(row.id, [])),
            )
            for row in templates
        )

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

    def known_part_ids(self) -> set[int]:
        with self._database.session() as session:
            return set(session.scalars(select(TrackPartDefinition.id)))

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
                host_dx=row.host_dx,
                host_dy=row.host_dy,
                host_da=row.host_da,
            )
            for row in rows
        )

    def write_instances(
        self, session: Session, track_id: int, instances: tuple[PartInstance, ...]
    ) -> None:
        known = set(session.scalars(select(TrackPartDefinition.id)))
        storable = tuple(instance for instance in instances if instance.part_id in known)
        session.execute(delete(TrackPlanInstance).where(TrackPlanInstance.track_id == track_id))
        # Hosts first so a reader that walks the insert order sees the parent
        # before the accessory. There is no foreign key; the plan enforces it.
        ordered = sorted(storable, key=lambda instance: instance.host_id is not None)
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
                    host_dx=instance.host_dx,
                    host_dy=instance.host_dy,
                    host_da=instance.host_da,
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

    def _insert_package(
        self, session: Session, spec: PackageSpec, *, bundled: bool = False
    ) -> TrackPartPackage:
        package = TrackPartPackage(
            manufacturer=spec.manufacturer.strip(),
            article_number=spec.article_number.strip(),
            name=spec.name.strip(),
            bundled=bundled,
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
        if content.source_id:
            for row in session.scalars(select(TrackPartDefinition)):
                if row.source_id != content.source_id:
                    continue
                if row.is_current:
                    return row
            raise ValidationError("error.planner.part")
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
            catalog_key = spec.source_id or catalog_storage_key(spec.name, spec.article_number)
        manufacturer = spec.manufacturer.strip()
        if not manufacturer and origin == ORIGIN_CATALOG:
            manufacturer = "Carrera"
        lineage = lineage_id or (spec.source_id.replace("-", "") if spec.source_id else uuid4().hex)
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
            lineage_id=lineage,
            revision=revision,
            is_current=True,
            origin=origin,
            catalog_key=catalog_key,
            manufacturer=manufacturer,
            description=spec.description.strip() or None,
            source_id=spec.source_id,
            piece_key=spec.piece_key,
            systems=",".join(spec.systems),
            selectable=spec.selectable,
            legacy=False,
        )
        self._assign_attachment(definition, spec)
        session.add(definition)
        session.flush()
        self._add_connectors(session, definition.id, spec)
        return definition

    def _add_connectors(self, session: Session, part_id: int, spec: PartSpec) -> None:
        for index, connector in enumerate(spec.connectors):
            points: list[list[object]] = [list(point) for point in connector.lane_points]
            if connector.edge_length_mm is not None:
                # The column stays a point list. The marker is not a groove end.
                points.insert(0, [_EDGE_LENGTH_MARK, connector.edge_length_mm])
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
                    target_source_id=connector.target_source_id,
                    edge_types=",".join(connector.edge_types),
                    exclusive=connector.exclusive,
                    has_neighbor=connector.has_neighbor,
                    neighbor_x=connector.neighbor_x,
                    neighbor_y=connector.neighbor_y,
                    neighbor_angle=connector.neighbor_angle,
                    lane_points=points,
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
        stored_connectors = tuple(
            ConnectorSpec(
                name=connector.name,
                x_mm=connector.x_mm,
                y_mm=connector.y_mm,
                z_mm=connector.z_mm,
                direction_deg=connector.direction_deg,
                kind=connector.kind,
                lanes=tuple(int(lane) for lane in connector.lanes),
                target_source_id=connector.target_source_id or "",
                edge_types=tuple(part for part in (connector.edge_types or "").split(",") if part),
                exclusive=bool(connector.exclusive),
                has_neighbor=bool(connector.has_neighbor),
                neighbor_x=connector.neighbor_x or 0.0,
                neighbor_y=connector.neighbor_y or 0.0,
                neighbor_angle=connector.neighbor_angle or 0.0,
                lane_points=_lane_points(connector.lane_points),
                edge_length_mm=_edge_length(connector.lane_points),
            )
            for connector in connectors
        )
        center_x_mm = 0.0
        center_y_mm = 0.0
        center_angle_deg = 0.0
        short_label = ""
        # The file stays the geometry of an untouched catalogue body. Stored
        # rows are not rewritten. A part the user has changed keeps its joints.
        # The short label is paint and is not a stored column.
        anchor = source_anchor(definition.source_id or "")
        if anchor is not None:
            center_x_mm, center_y_mm, center_angle_deg, short_label, file_connectors = anchor
            if definition.origin == ORIGIN_CATALOG:
                stored_connectors = file_connectors
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
            connectors=stored_connectors,
            outline=tuple((float(point[0]), float(point[1])) for point in definition.outline),
            slot_paths=slots,
            attachment=profile,
            contours=contours,
            border_edges=edges,
            manufacturer=definition.manufacturer or "",
            description=definition.description or "",
            source_id=definition.source_id or "",
            piece_key=definition.piece_key or "",
            systems=tuple(part for part in (definition.systems or "").split(",") if part),
            selectable=bool(definition.selectable),
            center_x_mm=center_x_mm,
            center_y_mm=center_y_mm,
            short_label=short_label,
            center_angle_deg=center_angle_deg,
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
        for row in session.scalars(select(TrackPartDefinition)):
            if not row.is_current:
                continue
            if except_id is not None and row.id == except_id:
                continue
            if spec.source_id and row.source_id == spec.source_id:
                raise self._duplicate(spec)
            if spec.source_id or row.source_id:
                continue
            if (
                identity_key(row.name, row.article_number)
                == identity_key(spec.name, spec.article_number)
                and (row.piece_key or "") == spec.piece_key
            ):
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
        definition.source_id = spec.source_id
        definition.piece_key = spec.piece_key
        definition.systems = ",".join(spec.systems)
        definition.selectable = spec.selectable
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


def _slot_identity(paths: tuple[SlotPath, ...]) -> tuple[object, ...]:
    """Driving grooves only. Paint on the same row must not count as a new joint."""
    return tuple((path.lane, path.slot, path.spans) for path in paths if path.kind == PATH_SLOT)


def _is_supplied_part(row: TrackPartDefinition) -> bool:
    """A bundled definition. An own part has neither key nor catalogue origin."""
    if row.source_id.strip():
        return True
    if (row.catalog_key or "").strip():
        return True
    return row.origin == ORIGIN_CATALOG


def _is_supplied_package(package: TrackPartPackage, draft_ids: set[tuple[str, str]]) -> bool:
    """A box from the bundled catalogue. The flag is set on import, not from the name."""
    return bool(package.bundled) or _package_identity(package) in draft_ids


def _draft_package_identity(package: PackageDraft) -> tuple[str, str]:
    return ("carrera", package.article_number.strip())


def _entry_change(
    kind: str, article_number: str, name: str, stock: int | None = None
) -> CatalogEntryChange:
    return CatalogEntryChange(kind, article_number.strip(), name.strip(), stock)


def _stock_quantity(session: Session, part_id: int) -> int | None:
    row = session.get(TrackPartStock, part_id)
    if row is None:
        return None
    return row.quantity


def _package_quantity(session: Session, package_id: int) -> int | None:
    row = session.get(TrackPartPackageStock, package_id)
    if row is None:
        return None
    return row.quantity


def _blocking_part_ids(session: Session) -> set[int]:
    """Plans, package contents, template members and collection rows that still point at a part."""
    found = {int(part_id) for part_id in session.scalars(select(TrackPlanInstance.part_id))}
    for data in session.scalars(select(_layouts().c.data)):
        found.update(_part_ids_in_document(data))
    found.update(
        int(part_id) for part_id in session.scalars(select(TrackPartPackageContent.part_id))
    )
    found.update(
        int(part_id) for part_id in session.scalars(select(TrackPartTemplateMember.part_id))
    )
    found.update(int(part_id) for part_id in session.scalars(select(TrackPartStock.part_id)))
    return found


def _template_members_match(
    stored: Sequence[TrackPartTemplateMember],
    template: TemplateDraft,
    by_source: dict[str, TrackPartDefinition],
) -> bool:
    expected: list[tuple[int, float, float, float]] = []
    for member in template.members:
        part = by_source.get(member.source_id)
        if part is None:
            return False
        expected.append((part.id, member.x_mm, member.y_mm, member.angle_deg))
    actual = sorted(
        (member.part_id, member.x_mm, member.y_mm, member.angle_deg) for member in stored
    )
    return actual == sorted(expected)


def _draft_package_contents(
    package: PackageDraft, by_source: dict[str, TrackPartDefinition]
) -> tuple[PackageContentSpec, ...] | None:
    contents: list[PackageContentSpec] = []
    for source_id, name, article in package.contents:
        part = by_source.get(source_id)
        if part is None:
            return None
        contents.append(PackageContentSpec(name, article, 1, source_id=part.source_id))
    if not contents:
        return None
    return tuple(contents)


def _package_contents_match(
    session: Session,
    package: TrackPartPackage,
    draft: PackageDraft,
    by_source: dict[str, TrackPartDefinition],
) -> bool:
    if package.name != draft.name:
        return False
    rows = list(
        session.scalars(
            select(TrackPartPackageContent)
            .where(TrackPartPackageContent.package_id == package.id)
            .order_by(TrackPartPackageContent.id)
        )
    )
    expected: list[tuple[int, int]] = []
    for source_id, _name, _article in draft.contents:
        part = by_source.get(source_id)
        if part is None:
            return False
        expected.append((part.id, 1))
    actual = [(row.part_id, row.quantity) for row in rows]
    return sorted(actual) == sorted(expected)


def _stored_catalog_keys(rows: Sequence[TrackPartDefinition]) -> set[tuple[str, str]]:
    stored: set[tuple[str, str]] = set()
    for row in rows:
        if not row.catalog_key or "\t" not in row.catalog_key:
            continue
        name, article = row.catalog_key.split("\t", 1)
        stored.add((name, article))
    return stored


def _catalog_by_key() -> dict[str, PartSpec]:
    found = {
        catalog_storage_key(spec.name, spec.article_number): spec for spec in standard_catalog()
    }
    for spec in load_carrera_library().parts:
        found[spec.source_id] = spec
    return found


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
    seen: set[str] = set()
    contents: list[PackageContentSpec] = []
    for line in spec.contents:
        amount = line.quantity
        if isinstance(amount, bool) or not isinstance(amount, int) or amount < 1:
            raise ValidationError("error.planner.package")
        if line.source_id:
            key = line.source_id
        else:
            part_name, part_article = identity_key(line.part_name, line.article_number)
            key = f"{part_name}\t{part_article}"
        if key in seen:
            raise ValidationError("error.planner.package")
        seen.add(key)
        contents.append(
            PackageContentSpec(
                line.part_name.strip(),
                line.article_number.strip(),
                amount,
                source_id=line.source_id,
            )
        )
    return PackageSpec(manufacturer, article, name, tuple(contents))


# Stored in lane_points ahead of the groove ends. It is not a lane coordinate.
_EDGE_LENGTH_MARK = "edge-length"


def _point_rows(raw: object) -> list[list[object] | tuple[object, ...]]:
    if not isinstance(raw, list):
        return []
    return [point for point in raw if isinstance(point, (list, tuple)) and len(point) >= 2]


def _number(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)


def _lane_points(raw: object) -> tuple[tuple[float, float], ...]:
    points: list[tuple[float, float]] = []
    for point in _point_rows(raw):
        if point[0] == _EDGE_LENGTH_MARK:
            continue
        x_mm = _number(point[0])
        y_mm = _number(point[1])
        if x_mm is None or y_mm is None:
            continue
        points.append((x_mm, y_mm))
    return tuple(points)


def _edge_length(raw: object) -> float | None:
    for point in _point_rows(raw):
        if point[0] != _EDGE_LENGTH_MARK:
            continue
        length = _number(point[1])
        if length is None or length <= 0.0:
            return None
        return length
    return None


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
