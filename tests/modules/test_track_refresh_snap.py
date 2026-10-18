"""Refresh seating closes a gap that is already the neighbouring joint.

The plan dock radius stays out of this pass. A part inside that radius but not
already against its neighbour stays where it is.
"""

from __future__ import annotations

from dataclasses import replace
from uuid import uuid4

import pytest
from PySide6.QtWidgets import QPushButton
from pytestqt.qtbot import QtBot

from slot_racing.core.domain import TrackId
from slot_racing.modules.track_planner.document import (
    TrackPlan,
    empty_plan,
    refresh_snapping,
    set_plan_grid,
    with_instances,
)
from slot_racing.modules.track_planner.parts import (
    BORDER_JOINT,
    REFRESH_SNAP_MM,
    SNAP_ANCHOR_SOURCE_IDS,
    STANDARD_STRAIGHT_ARTICLE,
    ConnectorSpec,
    PartInstance,
    PartSpec,
    is_snap_anchor,
    standard_catalog,
)
from slot_racing.modules.track_planner.ui.page import PlannerPage
from tests.modules.conftest import Env
from tests.modules.test_track_extend_arrows import _open, _place

_LAP_COUNTER = "69dbddea-14d4-4de6-b9ad-c266f10d9e37"
_BLACKBOX = "cf1dfdd3-1cfa-4b8f-8d7a-304232ee5de8"
_CONTROL_UNIT = "6e7e4f2a-a8c6-40ed-b70c-a5441b257fc0"


def test_anchor_names_and_source_ids_are_the_start_straights() -> None:
    straight = _rail()
    assert not is_snap_anchor(straight)
    assert is_snap_anchor(replace(straight, source_id=_LAP_COUNTER, name="Gerade"))
    assert is_snap_anchor(replace(straight, source_id=_BLACKBOX, name="Gerade"))
    assert is_snap_anchor(replace(straight, source_id=_CONTROL_UNIT, name="Gerade"))
    assert is_snap_anchor(replace(straight, name="Controll Unit"))
    assert frozenset({_LAP_COUNTER, _BLACKBOX, _CONTROL_UNIT}) == SNAP_ANCHOR_SOURCE_IDS


def test_refresh_walks_out_from_the_anchor_and_closes_small_gaps() -> None:
    anchor = _anchor()
    rail = _rail()
    catalog = {1: anchor, 2: rail}
    plan = _stored(_at(1, 0, 0), _at(2, 345, 4), _at(2, 690, 7))
    updated = refresh_snapping(plan, catalog)
    seated = updated.instances
    assert (seated[0].x_mm, seated[0].y_mm, seated[0].rotation_z_deg) == pytest.approx((0, 0, 0))
    assert (seated[1].x_mm, seated[1].y_mm, seated[1].rotation_z_deg) == pytest.approx((345, 0, 0))
    assert (seated[2].x_mm, seated[2].y_mm, seated[2].rotation_z_deg) == pytest.approx((690, 0, 0))


def test_refresh_starts_at_an_anchor_that_is_not_the_first_part() -> None:
    rail = _rail()
    anchor = _anchor("Control Unit", _CONTROL_UNIT)
    catalog = {1: rail, 2: anchor}
    plan = _stored(_at(1, 0, 8), _at(2, 345, 0))
    updated = refresh_snapping(plan, catalog)
    assert (updated.instances[1].x_mm, updated.instances[1].y_mm) == pytest.approx((345, 0))
    assert (updated.instances[0].x_mm, updated.instances[0].y_mm) == pytest.approx((0, 0))


def test_refresh_picks_one_part_when_the_plan_has_no_anchor() -> None:
    rail = _rail()
    catalog = {1: rail}
    plan = _stored(_at(1, 0, 0), _at(1, 345, 6))
    updated = refresh_snapping(plan, catalog)
    assert updated.instances[0] == plan.instances[0]
    assert (updated.instances[1].x_mm, updated.instances[1].y_mm) == pytest.approx((345, 0))


def test_refresh_does_not_use_the_dock_radius() -> None:
    anchor = _anchor()
    rail = _rail()
    catalog = {1: anchor, 2: rail}
    plan = _stored(_at(1, 0, 0), _at(2, 345, REFRESH_SNAP_MM + 120), snap_mm=200)
    assert plan.snap_mm == pytest.approx(200)
    assert refresh_snapping(plan, catalog) is plan


def test_a_part_that_faces_the_wrong_way_stays_put() -> None:
    anchor = _anchor()
    rail = _rail()
    catalog = {1: anchor, 2: rail}
    # The joint sits on the anchor joint, but the part is turned 90°.
    plan = _stored(_at(1, 0, 0), _at(2, 172.5, 172.5, 90))
    assert refresh_snapping(plan, catalog) is plan


def test_a_separate_run_keeps_its_own_start_and_still_closes() -> None:
    anchor = _anchor()
    rail = _rail()
    catalog = {1: anchor, 2: rail}
    plan = _stored(_at(1, 0, 0), _at(2, 345, 5), _at(2, 4000, 0), _at(2, 4345, 8))
    updated = refresh_snapping(plan, catalog)
    assert (updated.instances[0].x_mm, updated.instances[0].y_mm) == pytest.approx((0, 0))
    assert (updated.instances[1].x_mm, updated.instances[1].y_mm) == pytest.approx((345, 0))
    assert updated.instances[2] == plan.instances[2]
    assert (updated.instances[3].x_mm, updated.instances[3].y_mm) == pytest.approx((4345, 0))


def test_an_exact_chain_is_left_untouched() -> None:
    anchor = _anchor()
    rail = _rail()
    catalog = {1: anchor, 2: rail}
    plan = _stored(_at(1, 0, 0), _at(2, 345, 0), _at(2, 690, 0))
    assert refresh_snapping(plan, catalog) is plan


def test_a_border_that_already_touches_its_rail_is_reseated() -> None:
    rail = replace(
        _rail(),
        connectors=(ConnectorSpec("edge", 0, -99, 0, 270, BORDER_JOINT, (), edge_length_mm=100),),
    )
    border = replace(
        rail,
        name="Rand",
        connectors=(ConnectorSpec("edge", 0, 10, 0, 90, BORDER_JOINT, (), edge_length_mm=100),),
    )
    catalog = {1: rail, 2: border}
    plan = _stored(_at(1, 0, 0), _at(2, 3, -109))
    updated = refresh_snapping(plan, catalog)
    assert updated.instances[0] == plan.instances[0]
    assert (updated.instances[1].x_mm, updated.instances[1].y_mm) == pytest.approx((0, -109))


def test_the_button_reseats_a_shifted_part_and_undo_restores_it(qtbot: QtBot, env: Env) -> None:
    page = _open(qtbot, env, "Snapping")
    page.snap_distance.setValue(0)
    anchor = _place(page, "30342", x=0, y=0)
    shifted = _place(page, "20509", x=345, y=8)
    page.snap_distance.setValue(200)
    assert shifted.y_mm == pytest.approx(8)
    button = page.findChild(QPushButton, "planner-refresh-snap")
    assert button is not None
    assert button.text() == "Snapping aktualisieren"
    button.click()
    seated = {item.id: item for item in page.plan().instances}
    assert (seated[anchor.id].x_mm, seated[anchor.id].y_mm) == pytest.approx((0, 0))
    assert (seated[shifted.id].x_mm, seated[shifted.id].y_mm) == pytest.approx((345, 0))
    assert "ausgerichtet" in page.status.text()
    page.undo()
    restored = {item.id: item for item in page.plan().instances}
    assert restored[shifted.id].y_mm == pytest.approx(8)
    assert isinstance(page, PlannerPage)


def _rail() -> PartSpec:
    return next(
        spec for spec in standard_catalog() if spec.article_number == STANDARD_STRAIGHT_ARTICLE
    )


def _anchor(name: str = "Rundenzähler", source_id: str = _LAP_COUNTER) -> PartSpec:
    return replace(_rail(), name=name, source_id=source_id, article_number="30342")


def _at(part_id: int, x_mm: float, y_mm: float, rotation: float = 0.0) -> PartInstance:
    return PartInstance(str(uuid4()), part_id, x_mm, y_mm, rotation_z_deg=rotation)


def _stored(*instances: PartInstance, snap_mm: float = 200) -> TrackPlan:
    plan = set_plan_grid(empty_plan(TrackId(1)), enabled=False, grid_mm=100, snap_mm=snap_mm)
    return with_instances(plan, instances)
