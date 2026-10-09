"""Automatic lane rotation for lap races and time trials.

Two-lane races pair drivers from a circular list. The index advances by two after every
heat, so an even field is consecutive pairs and an odd field keeps sharing the spare driver.
Three and four lanes still give every driver one visit to every lane. The order of those
heats may change when somebody cannot start or is disqualified; the lane obligation does not.
"""

from __future__ import annotations

import math
from collections.abc import Sequence


def build_rotation(
    driver_ids: Sequence[int], lane_count: int
) -> tuple[tuple[tuple[int, int], ...], ...]:
    """One heat per entry. Each item is ``(driver_id, lane)`` with lanes starting at 1.

    Two lanes use :func:`pairwise_cycle`. With at most as many drivers as lanes, the field
    rotates together and empty lanes stay empty. With more drivers than lanes, consecutive
    drivers fill each heat and the window moves by one driver, so everybody still visits
    every lane once.
    """
    drivers = list(driver_ids)
    count = len(drivers)
    if count == 0 or lane_count < 1:
        return ()
    if lane_count == 2 and count >= 2:
        return pairwise_cycle(drivers)
    if count <= lane_count:
        heats = [
            tuple(
                sorted(
                    (
                        (driver, (index + offset) % lane_count + 1)
                        for index, driver in enumerate(drivers)
                    ),
                    key=lambda item: item[1],
                )
            )
            for offset in range(lane_count)
        ]
        return tuple(heats)
    heats = []
    for offset in range(count):
        heat = tuple(
            (drivers[(offset + lane_index) % count], lane_index + 1)
            for lane_index in range(lane_count)
        )
        heats.append(heat)
    return tuple(heats)


def pairwise_period(driver_count: int) -> int:
    """Heats until the circular index is back at the first driver.

    Even fields return after ``driver_count / 2`` heats. Odd fields need one heat per driver,
    because stepping by two only then lands on every index.
    """
    if driver_count < 2:
        return 0
    return driver_count // math.gcd(driver_count, 2)


def pairwise_heats(
    driver_ids: Sequence[int], heat_count: int
) -> tuple[tuple[tuple[int, int], ...], ...]:
    """``heat_count`` pairs. Lane 1 is the driver at the index, lane 2 is the next one.

    The index starts at zero and moves two places after each pair. It wraps around the list.
    """
    drivers = list(driver_ids)
    count = len(drivers)
    if count < 2 or heat_count < 1:
        return ()
    heats: list[tuple[tuple[int, int], ...]] = []
    index = 0
    for _ in range(heat_count):
        heats.append(((drivers[index], 1), (drivers[(index + 1) % count], 2)))
        index = (index + 2) % count
    return tuple(heats)


def pairwise_cycle(
    driver_ids: Sequence[int],
) -> tuple[tuple[tuple[int, int], ...], ...]:
    """One full pass of :func:`pairwise_heats`. The next pass repeats this sequence."""
    return pairwise_heats(driver_ids, pairwise_period(len(driver_ids)))


def pending_pairwise(
    driver_ids: Sequence[int],
    completed: set[tuple[int, int]],
    *,
    deferred: frozenset[int] = frozenset(),
) -> tuple[tuple[tuple[int, int], ...], ...]:
    """The open seats of one pairwise cycle.

    A seat that was already driven is left out. ``deferred`` drivers sit out the next heat
    only; their heats stay in the plan after that heat.
    """
    pending: list[tuple[tuple[int, int], ...]] = []
    for heat in pairwise_cycle(driver_ids):
        open_seats = tuple(seat for seat in heat if seat not in completed)
        if open_seats:
            pending.append(open_seats)
    if not deferred or not pending:
        return tuple(pending)
    ready = [heat for heat in pending if all(driver not in deferred for driver, _lane in heat)]
    held = [heat for heat in pending if any(driver in deferred for driver, _lane in heat)]
    if not ready:
        return tuple(pending)
    return (ready[0], *held, *ready[1:])


def plan_remaining(
    obligations: Sequence[tuple[int, int]],
    lane_count: int,
    *,
    deferred: frozenset[int] = frozenset(),
) -> tuple[tuple[tuple[int, int], ...], ...]:
    """Plan the heats that are still open.

    ``obligations`` are ``(driver_id, lane)`` pairs that have not been driven. ``deferred``
    drivers are left out of the next heat only and are planned again afterwards. A lane with
    nobody left who still needs it stays empty. A driver is never placed twice in one heat.
    """
    remaining = set(obligations)
    if lane_count < 1 or not remaining:
        return ()
    skip = set(deferred)
    heats: list[tuple[tuple[int, int], ...]] = []
    guard = 0
    while remaining and guard < 10000:
        guard += 1
        drivers = sorted(
            {driver for driver, _lane in remaining},
            key=lambda driver: (
                -sum(1 for candidate, _lane in remaining if candidate == driver),
                driver,
            ),
        )
        chosen: list[tuple[int, int]] = []
        used_lanes: set[int] = set()
        for driver in drivers:
            if driver in skip:
                continue
            open_lanes = sorted(
                lane
                for candidate, lane in remaining
                if candidate == driver and lane not in used_lanes
            )
            if not open_lanes:
                continue
            chosen.append((driver, open_lanes[0]))
            used_lanes.add(open_lanes[0])
            if len(chosen) == lane_count:
                break
        if not chosen:
            if skip:
                skip = set()
                continue
            break
        for pair in chosen:
            remaining.remove(pair)
        heats.append(tuple(sorted(chosen, key=lambda item: item[1])))
        skip = set()
    return tuple(heats)


def rotation_is_complete(
    heats: Sequence[Sequence[tuple[int, int]]],
    driver_ids: Sequence[int],
    lane_count: int,
) -> bool:
    """Every listed driver uses every lane once, and no heat seats a driver or a lane twice."""
    expected = {(driver, lane) for driver in driver_ids for lane in range(1, lane_count + 1)}
    seen: set[tuple[int, int]] = set()
    for heat in heats:
        drivers = [driver for driver, _lane in heat]
        lanes = [lane for _driver, lane in heat]
        if len(drivers) != len(set(drivers)) or len(lanes) != len(set(lanes)):
            return False
        if any(lane < 1 or lane > lane_count for lane in lanes):
            return False
        seen.update(heat)
    return seen == expected
