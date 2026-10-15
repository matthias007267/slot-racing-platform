"""Insert a retired catalogue part only when a test still needs that geometry.

The active library is the imported Carrera source. These helpers do not seed
it. They add the historical generator output as a custom part so accessory,
package and editor tests can keep using the old article numbers.
"""

from __future__ import annotations

from slot_racing.modules.track_planner.packages import PackageSpec, PackageView, standard_packages
from slot_racing.modules.track_planner.parts import PartRecord, standard_catalog
from slot_racing.modules.track_planner.service import TrackPlannerService

# Old article, then the imported article and piece key that replaced it.
LIVE_ARTICLE = {
    "20020601": ("20509", ""),
    "20020518": ("20509", "20509.2"),
    "20020571": ("20571", ""),
    "20020572": ("20572", ""),
    "20020573": ("20573", ""),
    "20020577": ("20577", ""),
    "20020578": ("20578", ""),
    "20020517": ("20517", ""),
    "20020611": ("20611", ""),
    "20020612": ("20612", ""),
    "20030341": ("30341", ""),
}


def live_part(planner: TrackPlannerService, article: str) -> PartRecord | None:
    """The imported part for an old article, when the new library has one."""
    mapped = LIVE_ARTICLE.get(article)
    if mapped is None:
        return None
    wanted, piece_key = mapped
    planner.list_parts()
    for record in planner.list_parts():
        if record.spec.article_number == wanted and record.spec.piece_key == piece_key:
            return record
    return None


def ensure_article(planner: TrackPlannerService, article: str) -> int:
    """Return one stored part for ``article``, inserting the old spec when it is absent.

    A legacy row counts. Inserting another body with the same name would be rejected
    and would mix the archived stock into the active library.
    """
    from sqlalchemy import select

    from slot_racing.modules.track_planner.models import TrackPartDefinition

    planner.list_parts()
    with planner.library._database.session() as session:
        rows = list(session.scalars(select(TrackPartDefinition)))
    matches = [row for row in rows if row.is_current and row.article_number.strip() == article]
    if matches:
        selectable = [
            row for row in matches if row.selectable and not row.legacy and not row.piece_key
        ]
        plain = [row for row in matches if not row.piece_key]
        return (selectable or plain or matches)[0].id
    specs = [spec for spec in standard_catalog() if spec.article_number == article]
    if not specs:
        raise AssertionError(article)
    created = 0
    for spec in specs:
        created = planner.add_part(spec).id
    return created


def ensure_named(planner: TrackPlannerService, name: str, article: str) -> int:
    from sqlalchemy import select

    from slot_racing.modules.track_planner.models import TrackPartDefinition

    def match() -> int | None:
        with planner.library._database.session() as session:
            rows = list(session.scalars(select(TrackPartDefinition)))
        for row in rows:
            if row.is_current and row.name == name and row.article_number.strip() == article:
                return int(row.id)
        return None

    found = match()
    if found is not None:
        return found
    ensure_article(planner, article)
    found = match()
    if found is None:
        raise AssertionError(name)
    return found


def ensure_package(planner: TrackPlannerService, article: str) -> PackageView:
    for package in planner.list_packages():
        if package.article_number == article:
            return package
    spec = next((item for item in standard_packages() if item.article_number == article), None)
    if spec is None:
        raise AssertionError(article)
    for content in spec.contents:
        ensure_named(planner, content.part_name, content.article_number)
    return planner.add_package(spec)


def package_spec(article: str) -> PackageSpec:
    found = next((item for item in standard_packages() if item.article_number == article), None)
    if found is None:
        raise AssertionError(article)
    return found
