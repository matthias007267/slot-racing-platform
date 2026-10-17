"""Bounded sensor diagnostic. It records delivery, not a single accepted flag.

Personal names are not part of the document. Race identity is the numeric id,
the mode and the timing provider.
"""

from __future__ import annotations

from importlib.metadata import PackageNotFoundError, version
from typing import TypedDict

from slot_racing.modules.timing_sensor.configuration import SensorAssignment
from slot_racing.modules.timing_sensor.intake import SensorObservation
from slot_racing.modules.timing_sensor.link import SensorLink
from slot_racing.modules.timing_sensor.protocol import PROTOCOL

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
        }
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
    }


def _stage(item: SensorObservation) -> _Stage:
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
