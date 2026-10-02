"""Helpers shared by the timing configuration views."""

from __future__ import annotations

import logging
from collections.abc import Callable

from PySide6.QtWidgets import QWidget

from carrera.core.domain import TimingPositionType
from carrera.core.i18n import Translator
from carrera.modules.tracks.timing_editor import DraftEntry
from carrera.uikit import StatusLabel, describe_error
from carrera.uikit.errors import is_expected

logger = logging.getLogger(__name__)


def position_text(
    translator: Translator, type_: TimingPositionType, sector_number: int, name: str | None
) -> str:
    """Display name of a position: its own name, otherwise one derived from its type."""
    if name:
        return name
    return type_text(translator, type_, sector_number)


def type_text(translator: Translator, type_: TimingPositionType, sector_number: int = 0) -> str:
    if type_ is TimingPositionType.START_FINISH:
        return translator.translate("timing.position.start_finish")
    return translator.format("timing.position.sector", number=sector_number)


def type_label(translator: Translator, type_: TimingPositionType) -> str:
    """Name of a position type, independent of the position's number or display name."""
    if type_ is TimingPositionType.START_FINISH:
        return translator.translate("timing.type.start_finish")
    return translator.translate("timing.type.sector")


def entry_text(translator: Translator, entries: list[DraftEntry], entry: DraftEntry) -> str:
    return position_text(translator, entry.type, entries.index(entry), entry.name)


def yes_no(translator: Translator, value: bool) -> str:
    return translator.translate("common.yes" if value else "common.no")


def format_time(ns: int) -> str:
    return f"{ns / 1_000_000_000:.3f} s".replace(".", ",")


def run_guarded(
    translator: Translator, status: StatusLabel, owner: QWidget, action: Callable[[], None]
) -> bool:
    """Run ``action``; a failure is shown in ``status`` instead of reaching the application."""
    try:
        action()
    except Exception as error:
        if not is_expected(error):
            logger.exception("Action failed on %s", type(owner).__name__)
        status.show_error(describe_error(translator, error))
        return False
    return True
