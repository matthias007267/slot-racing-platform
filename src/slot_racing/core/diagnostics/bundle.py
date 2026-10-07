"""Build a local zip the user can hand to someone who is diagnosing a problem."""

from __future__ import annotations

import zipfile
from collections.abc import Sequence
from datetime import datetime
from pathlib import Path

from slot_racing.core.diagnostics.sanitize import sanitize_text

LOG_NAME = "slot-racing.log"
FAULT_NAME = "slot-racing-fault.log"


def suggested_bundle_name(moment: datetime | None = None) -> str:
    stamp = (moment or datetime.now()).strftime("%Y-%m-%d-%H%M%S")
    return f"slot-racing-diagnostics-{stamp}.zip"


def log_candidates(directory: Path | None, backup_count: int) -> tuple[Path, ...]:
    if directory is None:
        return ()
    names = [LOG_NAME, *[f"{LOG_NAME}.{index}" for index in range(1, backup_count + 1)]]
    return tuple(directory / name for name in names)


def write_support_bundle(
    destination: Path,
    *,
    report_facts: str,
    breadcrumbs: str,
    config_text: str,
    logs: Sequence[Path],
    fault_file: Path | None,
) -> tuple[str, ...]:
    """Write ``destination``. A missing optional file is noted, not fatal.

    Raises ``OSError`` when the zip itself cannot be created.
    """
    notes: list[str] = []
    payloads: list[tuple[str, str]] = []
    for path in logs:
        _read_member(payloads, notes, path, f"logs/{path.name}")
    if fault_file is not None:
        _read_member(payloads, notes, fault_file, f"faults/{fault_file.name}")
    report = report_facts
    if notes:
        report = report.rstrip() + "\n\nNotes:\n" + "\n".join(f"- {note}" for note in notes) + "\n"
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_suffix(destination.suffix + ".partial")
    try:
        with zipfile.ZipFile(temporary, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            archive.writestr("diagnostics.txt", report)
            archive.writestr("breadcrumbs.txt", breadcrumbs)
            archive.writestr("config.txt", config_text)
            for name, text in payloads:
                archive.writestr(name, text)
        temporary.replace(destination)
    except Exception:
        if temporary.exists():
            temporary.unlink(missing_ok=True)
        raise
    return tuple(notes)


def _read_member(
    payloads: list[tuple[str, str]],
    notes: list[str],
    path: Path,
    member: str,
) -> None:
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        notes.append(f"log {path.name} unavailable")
        return
    payloads.append((member, sanitize_text(text)))
