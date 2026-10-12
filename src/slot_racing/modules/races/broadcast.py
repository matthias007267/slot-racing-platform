"""Read-only live display protocol. Nothing here starts or changes a race.

The PC builds one HUD scene with ``build_hud_scene`` and, while a start sequence is
planned, one ``StartSchedule``.
This module copies those documents into a mailbox. A network service reads the
mailbox. It never receives a race controller, and it never computes lap times,
positions, driver assignment, field visibility, status text or layout rules.

JSON envelope
-------------
Every message is one JSON object:

``{"protocol_version": 3, "type": "<type>", "seq": <int>, "payload": { ... }}``

``protocol_version`` is the broadcast protocol, currently ``3``.
``seq`` identifies the state. Scene, schedule, start-phase and start-commit
messages each have their own sequence. ``hello`` uses the mailbox generation.
An ``error`` reply and a ``time_sample`` use ``seq`` 0 because they are not a
race state. A client that does not speak version 3 must not treat any message
as GO.

Message types that are sent:

- ``hello`` — protocol, reference viewport, and whether a race scene is present.
  Sent once when a client connects.
- ``scene`` — the full HUD scene from :func:`scene_payload`, or
  ``{"present": false}`` when the view has cleared it. The newest scene replaces
  older ones. A slow client may skip intermediates and then receives the latest
  complete scene.
- ``start_schedule`` — the schedule document from ``StartSchedule.to_document``.
  Timestamps are host monotonic nanoseconds (``time_base`` ``host_monotonic_ns``)
  written as decimal strings so a browser does not round them. They are not a
  browser clock and not permission to start.
- ``start_schedule_revoked`` — the same document after ``revoke``. A scene update
  cannot replace it. A newer schedule can. A revoke for an older sequence does
  not replace the sequence now held.
- ``start_phase`` — one red lamp phase the race PC has already reached.
  ``authorizes_go`` is false. A later or duplicate phase cannot restore an
  older lamp count. The newest phase replaces the previous one, so a slow
  client skips intermediates and does not replay them.
- ``start_committed`` — the race engine has started. ``actual_start_ns`` is
  that engine stamp and ``authorizes_go`` is true. This is the only start
  authorization. It is not sent for a planned time, a timer callback, or a
  failed start. A scene cannot replace it. After it is sent the held red
  phase is dropped, so a new connection does not replay the countdown.
- ``error`` — the client sent something this display does not accept. The code
  is ``invalid_message`` or ``read_only``. No race action follows.
  ``start_phase`` and ``start_committed`` from a client are ``read_only``.
- ``time_probe`` — a browser asks for one clock sample. It carries a probe id,
  ``protocol_version``, and ``t1_us`` (browser ``performance.now()`` in
  microseconds, as a decimal string). The server thread stamps ``t2`` when the
  frame is in hand and ``t3`` immediately before the reply. The reply is a
  ``time_sample`` and does not pass through the mailbox or the GUI thread.
  A client may send about ten probes a second. Further probes are ignored.
  A malformed probe is ignored and does not close the socket.
- ``time_sample`` — ``probe_id``, echoed ``t1_us``, and ``t2_ns`` / ``t3_ns``
  as decimal host nanoseconds. The browser stamps ``t4`` on receipt. The
  resulting offset is an estimate. It may light red phases early. It must not
  light GO.

``snapshot``, ``race_update`` and ``connection_status`` are not sent. The scene
already contains the snapshot, a partial update could contradict the last scene,
and an open socket is the connection.

A new client receives ``hello``, then the current schedule if one is held,
then the current red phase if one is still held, then ``start_committed`` if
the race start was confirmed, then the current scene. It does not wait for
the next lap. After a reconnect the same current state is sent again. Older
scenes and finished lamp steps are not replayed.

Safety-critical documents are not merged into a scene. Under the soft send
limit a scene may be skipped; a pending schedule, phase or commit is still
sent. At the hard limit the client is dropped and must reconnect for a fresh
snapshot. A dropped browser has no effect on the race clock.

The reference viewport is 1280 by 720. :func:`compose_scene` accepts another
viewport and does not write the PC layout. A client may ask for its own size
with ``{"type": "viewport", "width": <int>, "height": <int>}``. That request is
display only. The service clamps it, composes a scene for that socket, and
leaves the PC layout and every other client unchanged. A client that has not
asked yet receives the reference scene. ``planned_go_ns`` stays a host
monotonic timestamp, on the wire as a decimal string, and is not a start
authorization. A browser with a fresh offset may show the red phases of the
current schedule from that estimate. Poor, stale, or missing samples fall
back to ``start_phase``. Disconnect stops that estimate. GO is still only
``start_committed``. The offset is not a real-time guarantee.

HTTP and WebSocket on the local network are not encrypted. The session token
only keeps a casual visitor off the page. Anyone who can already see the
traffic can read it. The token is random, checked on HTTP and WebSocket, renewed
each time the service starts, and never stored or written to a log.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import re
import secrets
import threading
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from urllib.parse import parse_qs, urlsplit

from slot_racing.core.domain import RaceStatus
from slot_racing.modules.races.hud import HudLayout
from slot_racing.modules.races.hud_scene import (
    HudPresentation,
    HudViewport,
    build_hud_scene,
    scene_payload,
)
from slot_racing.modules.races.runner import RaceSnapshot
from slot_racing.modules.races.start_display import (
    PROTOCOL_VERSION as _START_PROTOCOL,
)
from slot_racing.modules.races.start_display import (
    parse_commit_document,
    parse_phase_document,
)
from slot_racing.modules.races.start_metrics import metrics_source
from slot_racing.modules.races.start_schedule import TIME_BASE, TIMESTAMP_MEANING
from slot_racing.modules.races.time_sync import encode_timestamps, sync_source

logger = logging.getLogger(__name__)

BROADCAST_PROTOCOL_VERSION = _START_PROTOCOL
APP_NAME = "Slot-Racing Platform"
REFERENCE_VIEWPORT = HudViewport(1280, 720)
DEFAULT_PORT = 8765
MAX_CLIENTS = 16
MAX_HEADER_BYTES = 8 * 1024
MAX_INBOUND_BYTES = 64 * 1024
MAX_DOCUMENT_BYTES = 512 * 1024
SOFT_OUTBOUND_BYTES = 256 * 1024
HARD_OUTBOUND_BYTES = 1024 * 1024
WEBSOCKET_GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"

TYPE_HELLO = "hello"
TYPE_SCENE = "scene"
TYPE_START_SCHEDULE = "start_schedule"
TYPE_START_SCHEDULE_REVOKED = "start_schedule_revoked"
TYPE_START_PHASE = "start_phase"
TYPE_START_COMMITTED = "start_committed"
TYPE_ERROR = "error"
TYPE_VIEWPORT = "viewport"
TYPE_TIME_PROBE = "time_probe"
TYPE_TIME_SAMPLE = "time_sample"
VIEWPORT_MIN = 1
VIEWPORT_MAX = 8192

_ACTIVE_RACE = frozenset({RaceStatus.RUNNING.value, RaceStatus.PAUSED.value})
_LOOPBACK = "127.0.0.1"


class BindError(ValueError):
    """The requested address must not be used for the live display."""


@dataclass(frozen=True, slots=True)
class BroadcastDiagnostics:
    """What an operator can see. This never includes the session token."""

    running: bool
    host: str
    port: int
    client_count: int
    last_send_ns: int | None
    state_updates_sent: int
    control_updates_sent: int
    error: str


@dataclass(frozen=True, slots=True)
class SceneSource:
    """Immutable inputs for one more viewport. The layout object is never written."""

    layout: HudLayout
    snapshot: RaceSnapshot
    presentation: HudPresentation


@dataclass(frozen=True, slots=True)
class BroadcastFrame:
    """The one scene and the one schedule a client should know about."""

    scene: dict[str, object] | None
    scene_seq: int
    schedule: dict[str, object] | None
    schedule_seq: int
    schedule_revoked: bool
    generation: int
    phase: dict[str, object] | None = None
    phase_seq: int = 0
    commit: dict[str, object] | None = None
    commit_seq: int = 0
    source: SceneSource | None = None


@dataclass(frozen=True, slots=True)
class TimeProbe:
    """One clock request. ``t1_us`` is the browser send time in microseconds."""

    probe_id: str
    t1_us: str


@dataclass(frozen=True, slots=True)
class ClientVerdict:
    """Why a client message was ignored, or the display request it contained."""

    code: str
    viewport: HudViewport | None = None
    probe: TimeProbe | None = None


@dataclass(frozen=True, slots=True)
class OutboundPlan:
    """What one client may receive without letting its buffer grow without limit."""

    drop: bool
    send_hello: bool
    send_schedule: bool
    send_scene: bool
    send_phase: bool = False
    send_commit: bool = False


@dataclass(frozen=True, slots=True)
class HttpRequest:
    method: str
    target: str
    headers: dict[str, str]


@dataclass(frozen=True, slots=True)
class AccessDecision:
    kind: str
    status: int
    reason: str


@dataclass(frozen=True, slots=True)
class DecodedFrame:
    opcode: int
    payload: bytes


class WakeGate:
    """Coalesce wake-ups so a fast publisher cannot queue one event per update."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._pending = False
        self._callback: Callable[[], None] | None = None

    def set_callback(self, callback: Callable[[], None] | None) -> None:
        with self._lock:
            self._callback = callback

    def kick(self) -> None:
        with self._lock:
            if self._pending:
                return
            self._pending = True
            callback = self._callback
        if callback is None:
            with self._lock:
                self._pending = False
            return
        try:
            callback()
        except Exception:
            logger.exception("Live display wake failed")
            with self._lock:
                self._pending = False

    def acknowledge(self) -> None:
        """Call this before reading the mailbox, so a newer update wakes again."""
        with self._lock:
            self._pending = False


class BroadcastMailbox:
    """Latest scene plus latest schedule. Old scenes are dropped. A revoke is not."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._wake = WakeGate()
        self._scene: dict[str, object] | None = None
        self._scene_seq = 0
        self._schedule: dict[str, object] | None = None
        self._schedule_seq = 0
        self._schedule_revoked = False
        self._phase: dict[str, object] | None = None
        self._phase_seq = 0
        self._phase_step = -1
        self._commit: dict[str, object] | None = None
        self._commit_seq = 0
        self._generation = 0
        self._source: SceneSource | None = None

    @property
    def wake(self) -> WakeGate:
        return self._wake

    def publish_scene(self, payload: Mapping[str, object]) -> int:
        copied = copy_document(payload)
        with self._lock:
            self._scene_seq += 1
            self._scene = copied
            self._source = None
            self._generation += 1
            seq = self._scene_seq
        self._wake.kick()
        return seq

    def publish_source(
        self,
        layout: HudLayout,
        snapshot: RaceSnapshot,
        presentation: HudPresentation,
    ) -> int:
        """Store the reference scene and the inputs a client viewport can reuse."""
        reference = copy_document(compose_scene(layout, snapshot, presentation))
        source = SceneSource(layout, snapshot, presentation)
        with self._lock:
            self._scene_seq += 1
            self._scene = reference
            self._source = source
            self._generation += 1
            seq = self._scene_seq
        self._wake.kick()
        return seq

    def publish_schedule(self, document: Mapping[str, object], *, revoked: bool) -> int:
        copied = copy_document(document)
        with self._lock:
            incoming = copied.get("sequence_id")
            held = self._schedule
            held_id = None if held is None else held.get("sequence_id")
            if revoked and held_id is not None and incoming != held_id:
                return 0
            if revoked or held_id != incoming or self._schedule_revoked:
                self._phase = None
                self._phase_step = -1
                self._commit = None
            self._schedule_seq += 1
            self._schedule = copied
            self._schedule_revoked = revoked
            self._generation += 1
            seq = self._schedule_seq
        self._wake.kick()
        return seq

    def publish_start_phase(self, document: Mapping[str, object]) -> int:
        """Hold the newest red phase. A stale lamp, a revoke, or GO is refused."""
        copied = copy_document(document)
        sequence_id, step_index, _lit = parse_phase_document(copied)
        with self._lock:
            schedule = self._schedule
            if schedule is None or self._schedule_revoked:
                return 0
            if schedule.get("sequence_id") != sequence_id or step_index <= self._phase_step:
                return 0
            if self._commit is not None and self._commit.get("sequence_id") == sequence_id:
                return 0
            self._phase_step = step_index
            self._phase = copied
            self._phase_seq += 1
            self._generation += 1
            seq = self._phase_seq
        self._wake.kick()
        return seq

    def publish_start_committed(self, document: Mapping[str, object]) -> int:
        """Hold GO after the engine stamp. The same sequence is not announced twice."""
        copied = copy_document(document)
        sequence_id, _actual = parse_commit_document(copied)
        with self._lock:
            schedule = self._schedule
            if schedule is None or self._schedule_revoked:
                return 0
            if schedule.get("sequence_id") != sequence_id:
                return 0
            if self._commit is not None and self._commit.get("sequence_id") == sequence_id:
                return self._commit_seq
            self._commit = copied
            self._phase = None
            self._commit_seq += 1
            self._generation += 1
            seq = self._commit_seq
        self._wake.kick()
        return seq

    def clear_scene(self) -> int:
        return self.publish_scene({"present": False})

    def clear(self) -> None:
        """Drop held state without waking. Used when the service stops."""
        with self._lock:
            self._scene = None
            self._scene_seq = 0
            self._schedule = None
            self._schedule_seq = 0
            self._schedule_revoked = False
            self._phase = None
            self._phase_seq = 0
            self._phase_step = -1
            self._commit = None
            self._commit_seq = 0
            self._generation = 0
            self._source = None

    def current(self) -> BroadcastFrame:
        with self._lock:
            return BroadcastFrame(
                scene=self._scene,
                scene_seq=self._scene_seq,
                schedule=self._schedule,
                schedule_seq=self._schedule_seq,
                schedule_revoked=self._schedule_revoked,
                generation=self._generation,
                phase=self._phase,
                phase_seq=self._phase_seq,
                commit=self._commit,
                commit_seq=self._commit_seq,
                source=self._source,
            )


def compose_scene(
    layout: HudLayout,
    snapshot: RaceSnapshot,
    presentation: HudPresentation,
    viewport: HudViewport | None = None,
) -> dict[str, object]:
    """JSON scene for one viewport. The layout object is only read."""
    return scene_payload(
        build_hud_scene(layout, snapshot, viewport or REFERENCE_VIEWPORT, presentation)
    )


def message(message_type: str, seq: int, payload: Mapping[str, object]) -> dict[str, object]:
    return {
        "protocol_version": BROADCAST_PROTOCOL_VERSION,
        "type": message_type,
        "seq": seq,
        "payload": dict(payload),
    }


def dump_message(document: Mapping[str, object]) -> str:
    return json.dumps(document, ensure_ascii=False, separators=(",", ":"))


def hello_message(frame: BroadcastFrame) -> dict[str, object]:
    scene = frame.scene
    status: object = None
    present = False
    if scene is not None:
        present = scene.get("present") is not False and "status" in scene
        status = scene.get("status")
    return message(
        TYPE_HELLO,
        frame.generation,
        {
            "application": APP_NAME,
            "read_only": True,
            "time_base": TIME_BASE,
            "timestamp_meaning": TIMESTAMP_MEANING,
            "viewport": {
                "width": REFERENCE_VIEWPORT.width,
                "height": REFERENCE_VIEWPORT.height,
            },
            "viewport_note": "client_viewport_selects_a_scene",
            "viewport_limits": {"min": VIEWPORT_MIN, "max": VIEWPORT_MAX},
            "planned_go_note": "host_monotonic_not_a_start_authorization",
            "timestamp_encoding": "decimal_nanoseconds",
            "sync_note": "estimate_only_not_a_start_authorization",
            "start_authorization": TYPE_START_COMMITTED,
            "start_lights": "red_phases_local_when_synced",
            "capabilities": [
                TYPE_HELLO,
                TYPE_SCENE,
                TYPE_START_SCHEDULE,
                TYPE_START_SCHEDULE_REVOKED,
                TYPE_START_PHASE,
                TYPE_START_COMMITTED,
                TYPE_TIME_PROBE,
                TYPE_TIME_SAMPLE,
            ],
            "transport": "unencrypted_local_network",
            "race_active": status in _ACTIVE_RACE,
            "receiving_hud": present,
        },
    )


def scene_message(frame: BroadcastFrame) -> dict[str, object] | None:
    if frame.scene is None:
        return None
    return message(TYPE_SCENE, frame.scene_seq, frame.scene)


def scene_payload_for(
    frame: BroadcastFrame, viewport: HudViewport | None
) -> dict[str, object] | None:
    """The scene for one client. A missing source keeps the shared reference scene."""
    if frame.scene is None:
        return None
    source = frame.source
    if source is None or viewport is None or viewport == REFERENCE_VIEWPORT:
        return frame.scene
    try:
        return compose_scene(source.layout, source.snapshot, source.presentation, viewport)
    except Exception:
        logger.exception("Live display kept the reference scene")
        return frame.scene


def scene_message_for(
    frame: BroadcastFrame, viewport: HudViewport | None
) -> dict[str, object] | None:
    payload = scene_payload_for(frame, viewport)
    if payload is None:
        return None
    return message(TYPE_SCENE, frame.scene_seq, payload)


def parse_viewport(payload: Mapping[str, object]) -> HudViewport | None:
    """Clamp a display size. Anything that is not an integer is refused."""
    width = payload.get("width")
    height = payload.get("height")
    if isinstance(width, bool) or isinstance(height, bool):
        return None
    if not isinstance(width, int) or not isinstance(height, int):
        return None
    return HudViewport(_clamp_viewport(width), _clamp_viewport(height))


def _clamp_viewport(value: int) -> int:
    return min(VIEWPORT_MAX, max(VIEWPORT_MIN, value))


def phase_message(frame: BroadcastFrame) -> dict[str, object] | None:
    if frame.phase is None:
        return None
    return message(TYPE_START_PHASE, frame.phase_seq, encode_timestamps(frame.phase))


def commit_message(frame: BroadcastFrame) -> dict[str, object] | None:
    if frame.commit is None:
        return None
    return message(TYPE_START_COMMITTED, frame.commit_seq, encode_timestamps(frame.commit))


def schedule_message(frame: BroadcastFrame) -> dict[str, object] | None:
    if frame.schedule is None:
        return None
    kind = TYPE_START_SCHEDULE_REVOKED if frame.schedule_revoked else TYPE_START_SCHEDULE
    return message(kind, frame.schedule_seq, encode_timestamps(frame.schedule))


def time_sample_message(probe: TimeProbe, t2_ns: int, t3_ns: int) -> dict[str, object]:
    """The server-thread reply. ``t3_ns`` is stamped immediately before it is queued."""
    return message(
        TYPE_TIME_SAMPLE,
        0,
        {
            "probe_id": probe.probe_id,
            "t1_us": probe.t1_us,
            "t2_ns": str(t2_ns),
            "t3_ns": str(t3_ns),
        },
    )


def error_message(code: str) -> dict[str, object]:
    return message(TYPE_ERROR, 0, {"code": code, "detail": "display_only"})


def classify_client_message(raw: str) -> ClientVerdict:
    """A viewport or a clock probe is display traffic. Anything else is refused."""
    parsed = json_object(raw)
    kind = None if parsed is None else parsed.get("type")
    if not isinstance(kind, str) or not kind:
        return ClientVerdict("invalid_message")
    if kind == TYPE_VIEWPORT:
        if parsed is None:
            return ClientVerdict("invalid_message")
        viewport = parse_viewport(parsed)
        if viewport is None:
            return ClientVerdict("invalid_message")
        return ClientVerdict("viewport", viewport)
    if kind == TYPE_TIME_PROBE:
        if parsed is None:
            return ClientVerdict("ignore")
        probe = parse_time_probe(parsed)
        if probe is None:
            return ClientVerdict("ignore")
        return ClientVerdict("time_probe", probe=probe)
    return ClientVerdict("read_only")


_PROBE_ID = re.compile(r"[A-Za-z0-9_-]{1,64}")
_PROBE_US = re.compile(r"[0-9]{1,18}")


def parse_time_probe(document: Mapping[str, object]) -> TimeProbe | None:
    """A clock request. A bad one is ignored so a single typo does not drop the HUD."""
    if document.get("protocol_version") != BROADCAST_PROTOCOL_VERSION:
        return None
    probe_id = document.get("probe_id")
    t1_us = document.get("t1_us")
    if not isinstance(probe_id, str) or _PROBE_ID.fullmatch(probe_id) is None:
        return None
    if not isinstance(t1_us, str) or _PROBE_US.fullmatch(t1_us) is None:
        return None
    return TimeProbe(probe_id, t1_us)


def plan_outbound(
    pending_bytes: int,
    *,
    hello_due: bool,
    schedule_due: bool,
    scene_due: bool,
    phase_due: bool = False,
    commit_due: bool = False,
    soft_bytes: int = SOFT_OUTBOUND_BYTES,
    hard_bytes: int = HARD_OUTBOUND_BYTES,
) -> OutboundPlan:
    """Prefer the newest scene, but do not drop a start document to make room.

    A schedule, a red phase and a start confirmation still go out while the
    buffer is above the soft limit. The scene can wait. At the hard cap the
    client is disconnected and reconnects for the current snapshot.
    """
    if pending_bytes >= hard_bytes:
        return OutboundPlan(drop=True, send_hello=False, send_schedule=False, send_scene=False)
    return OutboundPlan(
        drop=False,
        send_hello=hello_due,
        send_schedule=schedule_due,
        send_phase=phase_due,
        send_commit=commit_due,
        send_scene=scene_due and pending_bytes < soft_bytes,
    )


def new_session_token() -> str:
    return secrets.token_urlsafe(24)


def token_matches(expected: str, presented: str) -> bool:
    if not expected or not presented or len(expected) != len(presented):
        return False
    return hmac.compare_digest(expected, presented)


def choose_bind_host(addresses: Sequence[tuple[str, bool]]) -> str:
    """One IPv4 address. Prefer an up private address, otherwise loopback.

    ``addresses`` is ``(address, is_up)``. ``0.0.0.0`` is never returned, so the
    display is not opened on every interface and not on the public internet by
    itself. There is no port forwarding.
    """
    for address, up in addresses:
        if up and _is_private_ipv4(address):
            return address
    return _LOOPBACK


def normalize_bind_host(host: str | None, addresses: Sequence[tuple[str, bool]]) -> str:
    if host is None:
        return choose_bind_host(addresses)
    literal = host.strip()
    if literal in {"0.0.0.0", "::", ""}:
        raise BindError("Die Live-Anzeige bindet nicht an alle Schnittstellen.")
    if not _is_ipv4(literal):
        raise BindError("Die Live-Anzeige benötigt eine lokale IPv4-Adresse.")
    return literal


def copy_document(payload: Mapping[str, object]) -> dict[str, object]:
    """A JSON copy. The caller can keep or change the original afterwards."""
    try:
        encoded = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    except (TypeError, ValueError) as error:
        raise ValueError("payload is not JSON") from error
    if len(encoded.encode("utf-8")) > MAX_DOCUMENT_BYTES:
        raise ValueError("payload is too large")
    copied = json_object(encoded)
    if copied is None:
        raise ValueError("payload must be a JSON object")
    return copied


def json_object(raw: str) -> dict[str, object] | None:
    try:
        loaded: object = json.loads(raw)
    except json.JSONDecodeError:
        return None
    if not isinstance(loaded, dict):
        return None
    copied: dict[str, object] = {}
    for key, item in loaded.items():
        if not isinstance(key, str):
            return None
        copied[key] = item
    return copied


def parse_http_request(head: bytes) -> HttpRequest | None:
    try:
        text = head.decode("iso-8859-1")
    except UnicodeDecodeError:
        return None
    lines = text.split("\r\n")
    if not lines:
        return None
    parts = lines[0].split(" ")
    if len(parts) != 3:
        return None
    method, target, version = parts
    if version not in {"HTTP/1.0", "HTTP/1.1"} or not target.startswith("/"):
        return None
    headers: dict[str, str] = {}
    for line in lines[1:]:
        if not line:
            continue
        name, separator, value = line.partition(":")
        if not separator or not name.strip():
            return None
        headers[name.strip().lower()] = value.strip()
    return HttpRequest(method.upper(), target, headers)


def decide_access(
    request: HttpRequest,
    *,
    bound_host: str,
    port: int,
    token: str,
) -> AccessDecision:
    """Token first in spirit: a wrong token is always 403, and no file is opened."""
    expected_host = f"{bound_host}:{port}"
    origin = request.headers.get("origin")
    allowed_origin = f"http://{bound_host}:{port}"
    if request.headers.get("host", "").lower() != expected_host.lower():
        return AccessDecision("deny", 403, "Zugriff verweigert")
    if origin is not None and origin.lower() != allowed_origin.lower():
        return AccessDecision("deny", 403, "Zugriff verweigert")
    if not token_matches(token, request_token(request.target)):
        return AccessDecision("deny", 403, "Zugriff verweigert")
    path = urlsplit(request.target).path
    if ".." in request.target or path not in {"/", "/live", "/diagnostics", "/session"}:
        return AccessDecision("deny", 404, "Nicht gefunden")
    if request.method != "GET":
        return AccessDecision("deny", 405, "Nicht erlaubt")
    length = request.headers.get("content-length", "")
    if length not in {"", "0"}:
        return AccessDecision("deny", 400, "Anfrage abgelehnt")
    upgrade = request.headers.get("upgrade", "")
    if not upgrade:
        if path == "/session":
            return AccessDecision("session", 204, "No Content")
        if path == "/diagnostics":
            return AccessDecision("diagnostics", 200, "OK")
        return AccessDecision("page", 200, "OK")
    if upgrade.lower() != "websocket" or path != "/live":
        return AccessDecision("deny", 400, "Upgrade nicht unterstützt")
    connection = request.headers.get("connection", "")
    if "upgrade" not in connection.lower():
        return AccessDecision("deny", 400, "Upgrade fehlt")
    if request.headers.get("sec-websocket-version") != "13":
        return AccessDecision("deny", 400, "WebSocket-Version nicht unterstützt")
    if not request.headers.get("sec-websocket-key", ""):
        return AccessDecision("deny", 400, "WebSocket-Schlüssel fehlt")
    return AccessDecision("websocket", 101, "Switching Protocols")


def request_token(target: str) -> str:
    found = parse_qs(urlsplit(target).query).get("token", [])
    if len(found) != 1:
        return ""
    return found[0]


def websocket_accept(key: str) -> str:
    digest = hashlib.sha1((key.strip() + WEBSOCKET_GUID).encode("ascii")).digest()
    return base64.b64encode(digest).decode("ascii")


def http_response(status: int, reason: str, body: bytes, content_type: str) -> bytes:
    head = (
        f"HTTP/1.1 {status} {reason}\r\n"
        f"Content-Type: {content_type}\r\n"
        f"Content-Length: {len(body)}\r\n"
        "Connection: close\r\n"
        "Cache-Control: no-store\r\n"
        "X-Content-Type-Options: nosniff\r\n"
        "\r\n"
    )
    return head.encode("ascii") + body


def websocket_upgrade(key: str) -> bytes:
    accept = websocket_accept(key)
    text = (
        "HTTP/1.1 101 Switching Protocols\r\n"
        "Upgrade: websocket\r\n"
        "Connection: Upgrade\r\n"
        f"Sec-WebSocket-Accept: {accept}\r\n"
        "\r\n"
    )
    return text.encode("ascii")


def encode_text_frame(text: str) -> bytes:
    payload = text.encode("utf-8")
    return _frame(0x1, payload)


def encode_control_frame(opcode: int, payload: bytes = b"") -> bytes:
    if len(payload) > 125:
        raise ValueError("control frame is too large")
    return _frame(opcode, payload)


def take_frames(
    buffer: bytearray, *, expect_mask: bool, limit: int
) -> tuple[list[DecodedFrame], str | None]:
    """Remove complete WebSocket frames from ``buffer``.

    Client frames must be masked. Server frames must not. Fragmented messages
    are rejected: the display only sends and accepts whole JSON texts.
    """
    frames: list[DecodedFrame] = []
    while True:
        if len(buffer) < 2:
            return frames, None
        first = buffer[0]
        second = buffer[1]
        if first & 0x70:
            return frames, "protocol"
        opcode = first & 0x0F
        fin = bool(first & 0x80)
        masked = bool(second & 0x80)
        if masked != expect_mask:
            return frames, "unmasked" if expect_mask else "protocol"
        if not fin:
            return frames, "fragmented"
        length = second & 0x7F
        offset = 2
        if length == 126:
            if len(buffer) < offset + 2:
                return frames, None
            length = int.from_bytes(buffer[offset : offset + 2], "big")
            offset += 2
        elif length == 127:
            if len(buffer) < offset + 8:
                return frames, None
            length = int.from_bytes(buffer[offset : offset + 8], "big")
            offset += 8
        if length > limit:
            return frames, "too_large"
        if opcode in {0x8, 0x9, 0xA} and length > 125:
            return frames, "protocol"
        mask_len = 4 if masked else 0
        total = offset + mask_len + length
        if len(buffer) < total:
            return frames, None
        raw = bytes(buffer[offset + mask_len : total])
        if masked:
            mask = bytes(buffer[offset : offset + 4])
            raw = bytes(byte ^ mask[index % 4] for index, byte in enumerate(raw))
        del buffer[:total]
        if opcode not in {0x1, 0x8, 0x9, 0xA}:
            return frames, "protocol"
        frames.append(DecodedFrame(opcode, raw))


def diagnostic_page() -> bytes:
    """Static diagnostic page. It estimates the clock and does not start a race."""
    page = _DIAGNOSTIC_HTML.replace("__SYNC_SOURCE__", sync_source())
    page = page.replace("__METRICS_SOURCE__", metrics_source())
    return page.encode("utf-8")


def _frame(opcode: int, payload: bytes) -> bytes:
    length = len(payload)
    header = bytearray([0x80 | opcode])
    if length < 126:
        header.append(length)
    elif length < 65536:
        header.append(126)
        header.extend(length.to_bytes(2, "big"))
    else:
        header.append(127)
        header.extend(length.to_bytes(8, "big"))
    return bytes(header) + payload


def _is_ipv4(address: str) -> bool:
    parts = address.split(".")
    if len(parts) != 4:
        return False
    try:
        numbers = tuple(int(part) for part in parts)
    except ValueError:
        return False
    return all(0 <= number <= 255 for number in numbers) and all(
        part == str(number) for part, number in zip(parts, numbers, strict=True)
    )


def _is_private_ipv4(address: str) -> bool:
    if not _is_ipv4(address):
        return False
    first_text, second_text, _rest, _last = address.split(".")
    first = int(first_text)
    second = int(second_text)
    if first == 10:
        return True
    if first == 192 and second == 168:
        return True
    return first == 172 and 16 <= second <= 31


_DIAGNOSTIC_HTML = """<!DOCTYPE html>
<html lang="de">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Slot-Racing Platform</title>
<style>
  body { margin: 0; font: 16px/1.4 sans-serif; background: #121418; color: #e8eaed; }
  main { max-width: 40rem; margin: 0 auto; padding: 1.5rem; }
  h1 { font-size: 1.4rem; margin: 0 0 0.5rem; }
  p { margin: 0.4rem 0; }
  dt { color: #9aa0a6; }
  dd { margin: 0 0 0.8rem; }
</style>
</head>
<body>
<main>
  <h1 id="app">Slot-Racing Platform</h1>
  <p>Diagnose der lokalen Live-Anzeige. Die Rennansicht liegt auf der Hauptadresse.</p>
  <dl>
    <dt>Verbindung</dt><dd id="connection">getrennt</dd>
    <dt>Rennen aktiv</dt><dd id="race">unbekannt</dd>
    <dt>HUD-Daten</dt><dd id="hud">nein</dd>
    <dt>Letzte Aktualisierung (Empfangszeit dieses Geräts)</dt><dd id="last">—</dd>
    <dt>Nachrichten</dt><dd id="count">0</dd>
    <dt>Protokollfehler</dt><dd id="errors">0</dd>
    <dt>Viewport</dt><dd id="viewport">—</dd>
    <dt>Startzeitplan</dt><dd id="plan">keiner</dd>
    <dt>Synchronisation</dt><dd id="sync">warte auf Proben</dd>
    <dt>Qualität</dt><dd id="quality">unbekannt</dd>
    <dt>RTT (Schätzung)</dt><dd id="rtt">—</dd>
    <dt>Beste RTT (Schätzung)</dt><dd id="best-rtt">—</dd>
    <dt>Offset (Schätzung)</dt><dd id="offset">—</dd>
    <dt>Unsicherheit (Schätzung)</dt><dd id="uncertainty">—</dd>
    <dt>Alter der letzten Probe</dt><dd id="age">—</dd>
    <dt>Proben</dt><dd id="samples">0</dd>
    <dt>Sequenz</dt><dd id="sequence">—</dd>
    <dt>Aktive Lampenphase</dt><dd id="lamp-phase">—</dd>
    <dt>Lampenmodus</dt><dd id="lamp-mode">servergesteuert</dd>
    <dt>Letzte Lampenabweichung (Schätzung)</dt><dd id="lamp-deviation">—</dd>
    <dt>Durchschnittliche Lampenabweichung (Schätzung)</dt><dd id="lamp-average">—</dd>
    <dt>Letzte GO-Verzögerung (Schätzung)</dt><dd id="go-delay">nicht messbar</dd>
    <dt>Engine bis Veröffentlichung (Messwert)</dt><dd id="engine-publish">—</dd>
    <dt>Veröffentlichung bis Empfang (Schätzung)</dt><dd id="publish-receive">—</dd>
    <dt>Empfang bis Darstellung (Messwert, Browseruhr)</dt><dd id="receive-paint">—</dd>
    <dt>Startsequenzen dieser Sitzung</dt><dd id="sequences">0</dd>
    <dt>Verbindungsabbrüche</dt><dd id="disconnects">0</dd>
    <dt>Fallback-Wechsel</dt><dd id="fallbacks">0</dd>
    <dt>Verworfene Proben</dt><dd id="discarded">0</dd>
  </dl>
  <p>
    <button type="button" id="export-json">JSON exportieren</button>
    <button type="button" id="export-csv">CSV exportieren</button>
  </p>
  <p>Der Export bleibt in diesem Browser. Er enthält nur diese Sitzung,
  kein Sitzungstoken und keine Messwerte anderer Geräte.</p>
  <p>Ein JavaScript-Zeitpunkt ist nicht der Moment, in dem ein Pixel sichtbar wurde.</p>
  <p>Die Verbindung im lokalen Netzwerk ist nicht verschlüsselt.</p>
  <p>Der geplante GO-Zeitpunkt ist eine Host-Zeit und keine Startfreigabe.</p>
  <p>Rote Lampen können lokal geschätzt werden. GO gilt erst mit start_committed.</p>
  <p>RTT, Offset und Unsicherheit sind Schätzungen und keine Echtzeitgarantie.</p>
  <p>Die Unsicherheit begrenzt asymmetrische Laufzeiten nicht hart.</p>
</main>
<script>
(function () {
  var connection = document.getElementById("connection");
  var race = document.getElementById("race");
  var hud = document.getElementById("hud");
  var last = document.getElementById("last");
  var count = document.getElementById("count");
  var plan = document.getElementById("plan");
  var errors = document.getElementById("errors");
  var viewport = document.getElementById("viewport");
  var messages = 0;
  var protocolErrors = 0;
  var socket = null;
  var retryTimer = 0;
  var retryDelay = 1000;
  var stopped = false;
  var syncSamples = [];
  var pendingProbes = {};
  var probeSerial = 0;
  var syncOffset = null;
  var syncQuality = "poor";
  var syncTimer = 0;
  var sequenceId = "";
  var counting = false;
  var committed = false;
  var planGo = null;
  var planPhases = [];
  var lastLocal = false;
  var goDelay = null;
  var serverLit = 0;
  var lampPeak = 0;
  var lampLit = -1;
  var lampTimer = 0;
  var lampFrame = 0;
  var sawConnection = false;
  var commitStamp = null;
  __SYNC_SOURCE__
  __METRICS_SOURCE__
  measureEnabled = true;
  function text(node, value) { node.textContent = value; }
  function showViewport() {
    var width = window.innerWidth;
    var height = window.innerHeight;
    text(viewport, String(width) + " x " + String(height));
  }
  function noteError() {
    protocolErrors += 1;
    text(errors, String(protocolErrors));
  }
  function connect() {
    text(connection, socket ? "wiederverbindung" : "verbinde");
    var opened = new WebSocket("ws://" + location.host + "/live" + location.search);
    socket = opened;
    opened.onmessage = function (event) {
      if (socket !== opened) return;
      messages += 1;
      text(count, String(messages));
      text(last, new Date().toLocaleString());
      var msg;
      try { msg = JSON.parse(event.data); } catch (error) { noteError(); return; }
      if (!msg || typeof msg.type !== "string") { noteError(); return; }
      var payload = msg.payload || {};
      if (msg.type === "error") noteError();
      if (msg.type === "hello") onHello(msg, payload);
      else if (msg.type === "scene") onScene(payload);
      else if (msg.type === "start_schedule" || msg.type === "start_schedule_revoked") {
        onPlan(msg.type, payload);
      } else if (msg.type === "start_phase") onPhase(payload);
      else if (msg.type === "start_committed") onCommit(payload);
      else if (msg.type === "time_sample") onSample(payload);
    };
    opened.onerror = function () {
      if (socket === opened) text(connection, "fehler");
    };
    opened.onclose = function () {
      if (socket !== opened) return;
      text(connection, "getrennt");
      noteDisconnect();
      dropSync();
      paintSync();
      schedule();
    };
    opened.onopen = function () {
      if (socket !== opened) return;
      text(connection, "verbunden");
      showViewport();
      if (sawConnection) noteResync();
      sawConnection = true;
    };
  }
  function onHello(msg, payload) {
    text(document.getElementById("app"), payload.application || "Slot-Racing Platform");
    text(race, payload.race_active ? "ja" : "nein");
    text(hud, payload.receiving_hud ? "ja" : "nein");
    if (msg.protocol_version === 3 && payload.start_authorization === "start_committed") {
      dropSync();
      burst(8, 40);
      scheduleSync();
    }
  }
  function onScene(payload) {
    if (payload.present === false) {
      text(hud, "nein");
      text(race, "nein");
      return;
    }
    text(hud, "ja");
    var active = payload.status === "running" || payload.status === "paused";
    text(race, active ? "ja" : "nein");
    var box = payload.viewport || {};
    if (box.width && box.height) {
      text(viewport, String(box.width) + " x " + String(box.height));
    }
  }
  function onPlan(kind, payload) {
    var revoked = kind === "start_schedule_revoked" || payload.status === "revoked";
    text(plan, "Sequenz " + payload.sequence_id + ", Status " + payload.status
      + ", geplantes GO " + payload.planned_go_ns
      + " (Host-Zeit, keine Startfreigabe)");
    if (revoked) {
      if (!sequenceId || sequenceId === payload.sequence_id) {
        counting = false;
        planGo = null;
        planPhases = [];
        serverLit = 0;
        lampPeak = 0;
        lampLit = -1;
        clearLampTimer();
      }
      paintSync();
      return;
    }
    sequenceId = typeof payload.sequence_id === "string" ? payload.sequence_id : "";
    text(document.getElementById("sequence"), sequenceId || "—");
    committed = false;
    counting = true;
    goDelay = null;
    commitStamp = null;
    serverLit = 0;
    lampPeak = 0;
    lampLit = -1;
    planGo = hostNs(payload.planned_go_ns);
    planPhases = [];
    var rows = payload.phases || [];
    var index;
    for (index = 0; index < rows.length; index += 1) {
      var row = rows[index];
      if (!row || row.phase !== "counting") continue;
      var at = hostNs(row.at_ns);
      var lit = row.lit_lights;
      if (at === null || typeof lit !== "number" || lit < 1) continue;
      planPhases.push({at: at, lit: lit, step: index});
    }
    if (planGo === null) counting = false;
    paintSync();
    noteDiagnosticLamp();
    armLamps();
    burst(4, 40);
  }
  function onPhase(payload) {
    if (payload.sequence_id !== sequenceId || payload.authorizes_go !== false) return;
    if (typeof payload.lit_lights === "number" && payload.lit_lights >= 1) {
      serverLit = payload.lit_lights;
    }
    text(plan, "Rote Lampen " + payload.lit_lights + " (keine Startfreigabe)");
    noteDiagnosticLamp();
    attachObserved(sequenceId, serverLit, payload.observed_ns, payload.at_ns);
    paintSync();
  }
  function onCommit(payload) {
    if (payload.sequence_id !== sequenceId || payload.authorizes_go !== true) return;
    if (payload.race_status !== "running") return;
    committed = true;
    counting = false;
    clearLampTimer();
    var actual = hostNs(payload.actual_start_ns);
    var published = hostNs(payload.published_ns);
    var now = clientNowNs();
    var estimated = now === null || syncOffset === null ? null : now + syncOffset;
    if (actual === null || estimated === null) goDelay = null;
    else goDelay = estimated - actual;
    commitStamp = {
      sequence: sequenceId,
      actual: actual === null ? "" : actual.toString(),
      published: published === null ? "" : published.toString(),
      receiveBrowser: now === null ? "" : now.toString(),
      receiveEstimated: estimated === null ? "" : estimated.toString()
    };
    var uncertainty = currentUncertainty();
    noteCommitArrival({
      sequence: sequenceId,
      actual: commitStamp.actual,
      published: commitStamp.published,
      receiveBrowser: commitStamp.receiveBrowser,
      receiveEstimated: commitStamp.receiveEstimated,
      quality: syncQuality,
      uncertainty: uncertainty
    });
    text(plan, "Start freigegeben, Sequenz " + payload.sequence_id);
    var paint = clientNowNs();
    var paintEstimated = paint === null || syncOffset === null ? null : paint + syncOffset;
    noteGoPaint({
      sequence: sequenceId,
      actual: commitStamp.actual,
      receiveBrowser: commitStamp.receiveBrowser,
      paintBrowser: paint === null ? "" : paint.toString(),
      paintEstimated: paintEstimated === null ? "" : paintEstimated.toString(),
      quality: syncQuality,
      uncertainty: uncertainty
    });
    if (window.requestAnimationFrame) {
      window.requestAnimationFrame(function () {
        var frame = clientNowNs();
        recordMeasure({
          sequence_id: sequenceId,
          event: "frame",
          phase: "go",
          time_basis: "browser",
          browser_ns: frame === null ? "" : frame.toString(),
          note: "Nächster Frame-Callback, nicht der sichtbare Pixel"
        });
      });
    }
    paintSync();
  }
  function clearLampTimer() {
    if (lampTimer) {
      clearTimeout(lampTimer);
      lampTimer = 0;
    }
    if (lampFrame && window.cancelAnimationFrame) {
      window.cancelAnimationFrame(lampFrame);
      lampFrame = 0;
    }
  }
  function currentUncertainty() {
    var now = clientNowNs();
    if (now === null) return "";
    var estimate = assessSync(syncSamples, now, syncQuality, syncOffset);
    if (estimate.uncertainty === null || estimate.uncertainty === undefined) return "";
    return estimate.uncertainty.toString();
  }
  function noteDiagnosticLamp() {
    if (!counting || committed || !sequenceId) return;
    var browser = clientNowNs();
    var estimated = browser === null || syncOffset === null ? null : browser + syncOffset;
    var predicted = null;
    var local = lastLocal && !document.hidden && estimated !== null && planGo !== null;
    if (local) {
      var found = predictRed(planPhases, planGo, estimated, true);
      if (found) predicted = found.lit_lights;
    }
    var lit = shownLamps(true, false, serverLit, predicted, lampPeak, local);
    if (local && lit > lampPeak) lampPeak = lit;
    if (lit === lampLit) return;
    var skipped = lampLit >= 0 && lit > lampLit + 1 ? lit - lampLit - 1 : 0;
    lampLit = lit;
    recordMeasure({
      sequence_id: sequenceId,
      event: "lamp",
      phase: String(lit),
      time_basis: "estimated_pc",
      browser_ns: browser === null ? "" : browser.toString(),
      estimated_pc_ns: estimated === null ? "" : estimated.toString(),
      quality: syncQuality,
      uncertainty_ns: currentUncertainty(),
      note: skipped
        ? "JavaScript-Zustandswechsel, übersprungene Phasen " + String(skipped)
        : "JavaScript-Zustandswechsel, nicht der sichtbare Pixel"
    });
    if (window.requestAnimationFrame) {
      var shown = lit;
      window.requestAnimationFrame(function () {
        var frame = clientNowNs();
        recordMeasure({
          sequence_id: sequenceId,
          event: "frame",
          phase: String(shown),
          time_basis: "browser",
          browser_ns: frame === null ? "" : frame.toString(),
          note: "Nächster Frame-Callback, nicht der sichtbare Pixel"
        });
      });
    }
  }
  function armLamps() {
    clearLampTimer();
    if (!lastLocal || document.hidden || !counting || committed) return;
    if (planGo === null || syncOffset === null) return;
    var now = clientNowNs();
    if (now === null) return;
    var wake = nextLampNs(planPhases, planGo, now + syncOffset, true);
    if (wake === null) return;
    var delay = wake - (now + syncOffset);
    if (delay < 0n) delay = 0n;
    if (delay > 60000000000n) delay = 60000000000n;
    var ms = Number(delay / 1000000n);
    if (ms < 16 && window.requestAnimationFrame) {
      lampFrame = window.requestAnimationFrame(function () {
        lampFrame = 0;
        noteDiagnosticLamp();
        armLamps();
      });
      return;
    }
    lampTimer = setTimeout(function () {
      lampTimer = 0;
      noteDiagnosticLamp();
      armLamps();
    }, ms);
  }
  function sendProbe() {
    if (!socket || socket.readyState !== 1) return;
    var now = clientNowNs();
    if (now === null) return;
    probeSerial += 1;
    var id = "d" + String(probeSerial);
    var t1 = (now / 1000n).toString();
    pendingProbes[id] = t1;
    socket.send(JSON.stringify({
      type: "time_probe", protocol_version: 3, probe_id: id, t1_us: t1
    }));
    setTimeout(function () { delete pendingProbes[id]; }, 2000);
  }
  function burst(count, gap) {
    var left = count;
    function step() {
      if (left <= 0 || !socket || socket.readyState !== 1) return;
      left -= 1;
      sendProbe();
      if (left > 0) setTimeout(step, gap);
    }
    step();
  }
  function scheduleSync() {
    if (syncTimer) clearTimeout(syncTimer);
    var jitter = Math.floor(Math.random() * 5000);
    syncTimer = setTimeout(function () {
      syncTimer = 0;
      if (!socket || socket.readyState !== 1) return;
      burst(syncQuality === "poor" ? 8 : 2, 40);
      scheduleSync();
    }, 30000 + jitter);
  }
  function dropSync() {
    syncSamples = [];
    pendingProbes = {};
    syncOffset = null;
    syncQuality = "poor";
    lastLocal = false;
    noteMode(false);
    clearLampTimer();
    if (syncTimer) {
      clearTimeout(syncTimer);
      syncTimer = 0;
    }
  }
  function onSample(payload) {
    var id = payload.probe_id;
    if (typeof id !== "string" || pendingProbes[id] === undefined) return;
    var sent = pendingProbes[id];
    delete pendingProbes[id];
    if (payload.t1_us !== sent) return;
    var t4 = clientNowNs();
    var t1 = hostNs(sent);
    var t2 = hostNs(payload.t2_ns);
    var t3 = hostNs(payload.t3_ns);
    if (t4 === null || t1 === null || t2 === null || t3 === null) {
      noteDiscarded();
      return;
    }
    var sample = ntpSample(t1 * 1000n, t2, t3, t4);
    if (!sample) {
      noteDiscarded();
      return;
    }
    var pruned = pruneSamples(syncSamples, t4);
    if (pruned.jumped) {
      noteDiscarded();
      dropSync();
      burst(8, 40);
      scheduleSync();
      paintSync();
      return;
    }
    syncSamples = pruned.samples;
    syncSamples.push({id: id, t4: t4, rtt: sample.rtt, offset: sample.offset});
    if (syncSamples.length > 16) syncSamples = syncSamples.slice(-16);
    applyEstimate();
  }
  function applyEstimate() {
    var now = clientNowNs();
    if (now === null) return;
    var estimate = assessSync(syncSamples, now, syncQuality, syncOffset);
    syncQuality = estimate.quality;
    syncOffset = estimate.offset;
    lastLocal = estimate.local && !document.hidden;
    noteMode(lastLocal);
    noteDiagnosticLamp();
    armLamps();
    paintSync(estimate);
  }
  function paintSync(estimate) {
    var now = clientNowNs();
    var current = estimate;
    if (!current && now !== null) {
      current = assessSync(syncSamples, now, syncQuality, syncOffset);
    }
    if (!current) return;
    var syncNode = document.getElementById("sync");
    var qualityNode = document.getElementById("quality");
    text(syncNode, current.count ? (current.local ? "gültig" : "ungenügend") : "warte auf Proben");
    var label = "schlecht";
    if (current.quality === "good") label = "gut";
    if (current.quality === "usable") label = "brauchbar";
    text(qualityNode, label);
    text(document.getElementById("rtt"), nsText(current.rtt));
    text(document.getElementById("best-rtt"), nsText(current.bestRtt));
    text(document.getElementById("offset"), nsText(current.offset));
    text(document.getElementById("uncertainty"), nsText(current.uncertainty));
    text(document.getElementById("age"), ageText(current.age));
    text(document.getElementById("samples"), String(current.count));
    text(document.getElementById("sequence"), sequenceId || "—");
    var predictable = counting && !committed && planGo !== null && !document.hidden;
    var mode = lastLocal && predictable ? "lokal synchronisiert" : "servergesteuert";
    text(document.getElementById("lamp-mode"), mode);
    var summary = measureSummary();
    text(document.getElementById("lamp-phase"), summary.phase || "—");
    text(document.getElementById("lamp-deviation"), delayText(summary.lastDeviation));
    text(document.getElementById("lamp-average"), delayText(summary.averageDeviation));
    var delayNode = document.getElementById("go-delay");
    var goShown = summary.lastGo || (goDelay === null ? "" : goDelay.toString());
    text(delayNode, goShown ? delayText(goShown) : "nicht messbar");
    text(document.getElementById("engine-publish"), delayText(summary.engineToPublish));
    text(document.getElementById("publish-receive"), delayText(summary.publishToReceive));
    text(document.getElementById("receive-paint"), delayText(summary.receiveToPaint));
    text(document.getElementById("sequences"), String(summary.sequences));
    text(document.getElementById("disconnects"), String(summary.disconnects));
    text(document.getElementById("fallbacks"), String(summary.fallbacks));
    text(document.getElementById("discarded"), String(summary.discarded));
  }
  function nsText(value) {
    if (value === null || value === undefined) return "—";
    return value.toString() + " ns";
  }
  function ageText(value) {
    if (value === null || value === undefined) return "—";
    return (value / 1000000n).toString() + " ms";
  }
  function schedule() {
    if (stopped || retryTimer) return;
    retryTimer = setTimeout(function () {
      retryTimer = 0;
      if (stopped) return;
      var xhr = new XMLHttpRequest();
      xhr.open("GET", "/session" + location.search, true);
      xhr.timeout = 4000;
      xhr.onload = function () {
        if (xhr.status === 204) {
          retryDelay = 1000;
          connect();
          return;
        }
        if (xhr.status === 403 || xhr.status === 404 || xhr.status === 405) {
          stopped = true;
          text(connection, "sitzung abgelaufen");
          return;
        }
        text(connection, "nicht erreichbar");
        schedule();
      };
      xhr.onerror = function () {
        text(connection, "nicht erreichbar");
        schedule();
      };
      xhr.ontimeout = xhr.onerror;
      xhr.send();
    }, retryDelay);
    retryDelay = Math.min(retryDelay * 2, 8000);
  }
  window.addEventListener("resize", showViewport);
  document.addEventListener("visibilitychange", function () {
    dropSync();
    paintSync();
    if (!document.hidden && socket && socket.readyState === 1) {
      burst(8, 40);
      scheduleSync();
    }
  });
  document.getElementById("export-json").addEventListener("click", function () {
    downloadMeasure("json");
  });
  document.getElementById("export-csv").addEventListener("click", function () {
    downloadMeasure("csv");
  });
  showViewport();
  connect();
})();
</script>
</body>
</html>
"""
