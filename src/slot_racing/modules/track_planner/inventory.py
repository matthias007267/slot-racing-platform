"""Owned stock compared with the parts placed on one plan.

``used`` is counted from the plan. It is not stored beside the stock. The plan
keeps instances in creation order (new ones are appended, and that order is
written as ``sort_order``). The first ``owned`` instances of a part are covered.
Every later instance of that part is excess.
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


def require_quantity(quantity: object) -> int:
    """A whole number of owned parts. Zero is allowed. There is no upper limit."""
    if isinstance(quantity, bool) or not isinstance(quantity, int) or quantity < 0:
        raise ValidationError("error.planner.stock")
    return quantity


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
