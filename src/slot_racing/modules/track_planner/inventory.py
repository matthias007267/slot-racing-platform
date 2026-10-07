"""Physical stock compared with the parts placed on one plan.

A package quantity times its contents is the derived count. The manual
adjustment (which may be negative) is added, then clamped at zero. That
physical count is ``owned`` here.

``used`` is counted from the open plan only. It is not stored beside the stock.
The plan keeps instances in creation order (new ones are appended, and that
order is written as ``sort_order``). The first ``owned`` instances of a part
are covered. Every later instance of that part is excess.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from slot_racing.core.errors import ValidationError
from slot_racing.modules.track_planner.parts import PartInstance


@dataclass(frozen=True, slots=True)
class PartBalance:
    """One definition: what is owned, what this plan uses, and which copies are extra."""

    part_id: int
    owned: int
    used: int
    available: int
    excess_ids: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class InventoryReport:
    """Balances for every part that is owned or placed. Missing parts read as zero."""

    lines: tuple[PartBalance, ...]

    def balance(self, part_id: int) -> PartBalance:
        for line in self.lines:
            if line.part_id == part_id:
                return line
        return PartBalance(part_id, 0, 0, 0, ())

    def excess_ids(self) -> frozenset[str]:
        return frozenset(item for line in self.lines for item in line.excess_ids)

    def over_capacity(self) -> bool:
        return any(line.used > line.owned for line in self.lines)


@dataclass(frozen=True, slots=True)
class PackageContribution:
    """One package contributes ``per_package`` pieces of one physical part."""

    package_id: int
    part_id: int
    per_package: int


@dataclass(frozen=True, slots=True)
class PhysicalStock:
    """Derived pieces, the manual correction, and the clamped physical count."""

    part_id: int
    derived: int
    adjustment: int
    physical: int


def require_quantity(quantity: object) -> int:
    """A whole number of owned parts or packages. Zero is allowed."""
    if isinstance(quantity, bool) or not isinstance(quantity, int) or quantity < 0:
        raise ValidationError("error.planner.stock")
    return quantity


def require_adjustment(quantity: object) -> int:
    """A whole correction. Negative means pieces lost or never owned."""
    if isinstance(quantity, bool) or not isinstance(quantity, int):
        raise ValidationError("error.planner.stock_adjustment")
    return quantity


def derived_quantities(
    package_stock: Mapping[int, int], contents: Sequence[PackageContribution]
) -> dict[int, int]:
    """Sum ``package stock x pieces per package`` for every physical part."""
    totals: dict[int, int] = {}
    for line in contents:
        packs = package_stock.get(line.package_id, 0)
        if packs <= 0 or line.per_package <= 0:
            continue
        totals[line.part_id] = totals.get(line.part_id, 0) + packs * line.per_package
    return totals


def physical_quantity(derived: int, adjustment: int) -> int:
    """Owned pieces. A negative sum is not stored as a negative stock."""
    total = derived + adjustment
    return total if total > 0 else 0


def physical_stocks(
    derived: Mapping[int, int], adjustments: Mapping[int, int]
) -> dict[int, PhysicalStock]:
    """One line per part that has derived pieces or a stored correction."""
    lines: dict[int, PhysicalStock] = {}
    for part_id in set(derived) | set(adjustments):
        produced = derived.get(part_id, 0)
        correction = adjustments.get(part_id, 0)
        lines[part_id] = PhysicalStock(
            part_id=part_id,
            derived=produced,
            adjustment=correction,
            physical=physical_quantity(produced, correction),
        )
    return lines


def analyze_inventory(
    instances: Sequence[PartInstance], owned: Mapping[int, int]
) -> InventoryReport:
    """Cover the earliest instances. Mark only the ones past ``owned`` as excess.

    ``instances`` must already be in creation order. Sorting by id would mark a
    different copy after a reload, because instance ids are not chronological.
    """
    order: dict[int, list[str]] = {}
    for instance in instances:
        order.setdefault(instance.part_id, []).append(instance.id)
    part_ids = tuple(sorted(set(order) | set(owned)))
    lines: list[PartBalance] = []
    for part_id in part_ids:
        quantity = owned.get(part_id, 0)
        ids = order.get(part_id, [])
        covered = quantity if quantity > 0 else 0
        lines.append(
            PartBalance(
                part_id=part_id,
                owned=quantity,
                used=len(ids),
                available=quantity - len(ids),
                excess_ids=tuple(ids[covered:]),
            )
        )
    return InventoryReport(tuple(lines))
