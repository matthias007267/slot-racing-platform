"""Retail packages. A package contains physical part definitions; it is not placed."""

from __future__ import annotations

from dataclasses import dataclass

from slot_racing.modules.track_planner.parts import (
    END_PIECE_K3_NAME,
    OUTER_BORDER_K3_ARTICLE,
    OUTER_BORDER_K3_NAME,
    STANDARD_STRAIGHT_ARTICLE,
    STANDARD_STRAIGHT_NAME,
)


@dataclass(frozen=True, slots=True)
class PackageContentSpec:
    """``quantity`` pieces of one physical definition inside a single box."""

    part_name: str
    article_number: str
    quantity: int


@dataclass(frozen=True, slots=True)
class PackageSpec:
    """One sales article. Contents are data, not a special case in the formula."""

    manufacturer: str
    article_number: str
    name: str
    contents: tuple[PackageContentSpec, ...]


@dataclass(frozen=True, slots=True)
class PackageContentView:
    part_id: int
    part_name: str
    quantity: int


@dataclass(frozen=True, slots=True)
class PackageView:
    """A stored package plus how many boxes the user currently owns."""

    id: int
    manufacturer: str
    article_number: str
    name: str
    quantity: int
    contents: tuple[PackageContentView, ...]


def standard_packages() -> tuple[PackageSpec, ...]:
    """Known boxes only. Contents that are not certain are left out."""
    return (
        PackageSpec(
            manufacturer="Carrera",
            article_number=OUTER_BORDER_K3_ARTICLE,
            name=OUTER_BORDER_K3_NAME,
            contents=(
                PackageContentSpec(OUTER_BORDER_K3_NAME, OUTER_BORDER_K3_ARTICLE, 6),
                PackageContentSpec(END_PIECE_K3_NAME, OUTER_BORDER_K3_ARTICLE, 2),
            ),
        ),
        PackageSpec(
            manufacturer="Carrera",
            article_number=STANDARD_STRAIGHT_ARTICLE,
            name=STANDARD_STRAIGHT_NAME,
            contents=(PackageContentSpec(STANDARD_STRAIGHT_NAME, STANDARD_STRAIGHT_ARTICLE, 1),),
        ),
    )
