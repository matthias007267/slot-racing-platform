"""Bounded sensor diagnostic. It records delivery, not a single accepted flag.

Personal names are not part of the document. Race identity is the numeric id,
the mode and the timing provider.
"""

from __future__ import annotations

import hashlib
import sys
from functools import lru_cache
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from typing import TypedDict

from slot_racing.modules.timing_sensor.configuration import SensorAssignment
from slot_racing.modules.timing_sensor.intake import SensorObservation
from slot_racing.modules.timing_sensor.link import SensorLink
from slot_racing.modules.timing_sensor.protocol import PROTOCOL, firmware_supports_arm

_EVENT_LIMIT = 200


class _Stage(TypedDict):
    stage: str
    reason: str


def application_version() -> str:
    """Installed distribution version, or the project version when metadata is absent."""
    try:
        return version("slot-racing-platform")
    except PackageNotFoundError:
        return "0.1.0"


@lru_cache(maxsize=1)
def code_id() -> str:
    """Short hash of the installed Python sources. It names the code that wrote the export."""
    digest = hashlib.sha256()
    if getattr(sys, "frozen", False):
        # The sources are inside the program file, so the program file names the code.
        digest.update(Path(sys.executable).read_bytes())
        return digest.hexdigest()[:16]
    root = Path(__file__).resolve().parents[2]
    for path in sorted(root.rglob("*.py")):
        digest.update(path.relative_to(root).as_posix().encode("utf-8"))
        digest.update(path.read_bytes())
    return digest.hexdigest()[:16]


def sensor_diagnostic(
    link: SensorLink,
    *,
    track_id: int | None,
    positions: tuple[str, ...],
    assignments: tuple[SensorAssignment, ...],
) -> dict[str, object]:
    """One JSON-ready document covering the current link and the last race attempt."""
    identity = link.identity
    samples, uncertainty, numerator, denominator, accepted = link.sync_quality
    watch = link.race_watch
    firmware: dict[str, object] | None = None
    if identity is not None:
        firmware = {
            "device": identity.device,
            "version": identity.firmware,
            "session_id": identity.session_id,
            "sensors": list(identity.sensors),
            "capabilities": {
                "arm": firmware_supports_arm(identity.firmware),
                "arm_basis": "reported firmware version",
            },
        }
    report = link.clock_report()
    race: dict[str, object] | None = None
    if watch.race_id is not None:
        race = {
            "id": watch.race_id,
            "mode": watch.mode,
            "state": watch.state,
            "provider": watch.provider,
            "started_at_ns": watch.started_at_ns,
            "bound": link.race_bound,
        }
    crossings = [item for item in link.observations if item.sequence is not None]
    return {
        "kind": "sensor-timing-diagnostic",
        "application_version": application_version(),
        "build": {"application_version": application_version(), "code_id": code_id()},
        "protocol": PROTOCOL,
        "firmware": firmware,
        "connection": {
            "mode": link.mode.value,
            "connected": link.connected,
            "port": link.port,
            "sync": link.sync.value,
            "mapping_origin": link.mapping_origin,
            "samples": samples,
            "uncertainty_ns": uncertainty,
            "numerator": numerator,
            "denominator": denominator,
            "sync_accepted": accepted,
            "clock": {
                "rate_status": report.rate_status,
                "raw_skew_ppm": report.skew_ppm,
                "window_span_ns": report.span_ns,
                "window_samples": report.window_samples,
                "anchor_host_mid_ns": report.anchor_host_mid_ns,
                "anchor_device_mid_ns": report.anchor_device_mid_ns,
                "anchor_uncertainty_ns": report.anchor_uncertainty_ns,
            },
            "sync_log": link.sync_log(),
            "mappings": link.mapping_log(),
        },
        "changes": [{"kind": kind, "detail": detail} for kind, detail in link.changes],
        "timing": {
            "track_id": track_id,
            "positions": list(positions),
            "assignments": [
                {
                    "sensor_id": item.physical_id,
                    "position_id": item.position_id,
                    "lane": item.lane,
                }
                for item in assignments
            ],
        },
        "race": race,
        "start_window": link.start_window_state(),
        "start_sequences": link.start_sequences(),
        "event_limit": _EVENT_LIMIT,
        "events": [_event(item, watch.started_at_ns) for item in crossings[-_EVENT_LIMIT:]],
    }


def _event(item: SensorObservation, race_started_ns: int | None) -> dict[str, object]:
    start = item.race_start_ns if item.race_start_ns is not None else race_started_ns
    relative = None if item.host_time_ns is None or start is None else item.host_time_ns - start
    stage = _stage(item)
    return {
        "sensor_id": item.physical_id,
        "sequence": item.sequence,
        "device_time_ns": item.device_time_ns,
        "host_time_ns": item.host_time_ns,
        "race_relative_ns": relative,
        "position_id": item.position_id,
        "lane": item.lane,
        "stage": stage["stage"],
        "reason": stage["reason"],
        "converted_host_time_ns": item.host_time_ns,
        "start_phase_begin_ns": item.window_opened_ns,
        "release_ns": item.released_ns,
        "delta_to_release_ns": item.release_delta_ns,
        "sync_status": item.sync_status,
        "classification": item.classification,
        "classification_reason": item.classification_reason,
        "receive_host_ns": item.receive_host_ns,
        "receive_minus_capture_ns": (
            None
            if item.receive_host_ns is None or item.host_time_ns is None
            else item.receive_host_ns - item.host_time_ns
        ),
        "mapping_id": item.mapping_id,
        "mapping_age_ns": item.mapping_age_ns,
    }


def _stage(item: SensorObservation) -> _Stage:
    if item.kind == "false_start":
        return {"stage": "false_start", "reason": "before_release"}
    if item.engine_result == "completed":
        return {"stage": "lap", "reason": "completed"}
    if item.engine_result:
        return {"stage": "engine", "reason": item.engine_result}
    if item.delivery == "published":
        return {"stage": "published", "reason": item.kind}
    if item.delivery == "withheld":
        return {"stage": "withheld", "reason": item.detail or "muted"}
    if item.delivery == "received" or item.kind in {"accepted", "gap"}:
        return {"stage": "received", "reason": item.kind}
    return {"stage": item.kind, "reason": item.detail or item.kind}
