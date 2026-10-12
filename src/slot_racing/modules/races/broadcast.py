"""Read-only live display protocol. Nothing here starts or changes a race.

The PC builds one HUD scene with ``build_hud_scene`` and, while a start sequence is
planned, one ``StartSchedule``.
This module copies those documents into a mailbox. A network service reads the
mailbox. It never receives a race controller, and it never computes lap times,
positions, driver assignment, field visibility, status text or layout rules.

JSON envelope
-------------
Every message is one JSON object:

``{"protocol_version": 1, "type": "<type>", "seq": <int>, "payload": { ... }}``

``protocol_version`` is the broadcast protocol, currently ``1``.
``seq`` identifies the state. Scene messages use the scene sequence, schedule
messages use the schedule sequence, and ``hello`` uses the mailbox generation.
An ``error`` reply uses ``seq`` 0 because it is not a race state.

Message types that are sent:

- ``hello`` — protocol, reference viewport, and whether a race scene is present.
  Sent once when a client connects.
- ``scene`` — the full HUD scene from :func:`scene_payload`, or
  ``{"present": false}`` when the view has cleared it. The newest scene replaces
  older ones. A slow client may skip intermediates and then receives the latest
  complete scene.
- ``start_schedule`` — the schedule document from ``StartSchedule.to_document``.
  Timestamps are host monotonic nanoseconds (``time_base`` ``host_monotonic_ns``).
  They are not a browser clock and not permission to start.
- ``start_schedule_revoked`` — the same document after ``revoke``. A scene update
  cannot replace it. A newer schedule can.
- ``error`` — the client sent something. The display is read-only, so the code
  is ``invalid_message`` or ``read_only``. No race action follows.

``snapshot``, ``race_update`` and ``connection_status`` are not sent. The scene
already contains the snapshot, a partial update could contradict the last scene,
and an open socket is the connection.

A new client receives ``hello``, then the current schedule if one is held, then
the current scene. It does not wait for the next lap. After a reconnect the
same full state is sent again. Older scenes are not replayed.

The reference viewport is 1280 by 720. :func:`compose_scene` accepts another
viewport and does not write the PC layout. A client may ask for its own size
with ``{"type": "viewport", "width": <int>, "height": <int>}``. That request is
display only. The service clamps it, composes a scene for that socket, and
leaves the PC layout and every other client unchanged. A client that has not
asked yet receives the reference scene. ``planned_go_ns`` stays a host
monotonic timestamp and is not a start authorization.

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
from slot_racing.modules.races.start_schedule import TIME_BASE, TIMESTAMP_MEANING

logger = logging.getLogger(__name__)

BROADCAST_PROTOCOL_VERSION = 1
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
TYPE_ERROR = "error"
TYPE_VIEWPORT = "viewport"
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
    source: SceneSource | None = None


@dataclass(frozen=True, slots=True)
class ClientVerdict:
    """Why a client message was ignored, or the viewport it may display."""

    code: str
    viewport: HudViewport | None = None


@dataclass(frozen=True, slots=True)
class OutboundPlan:
    """What one client may receive without letting its buffer grow without limit."""

    drop: bool
    send_hello: bool
    send_schedule: bool
    send_scene: bool


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
            self._schedule_seq += 1
            self._schedule = copied
            self._schedule_revoked = revoked
            self._generation += 1
            seq = self._schedule_seq
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


def schedule_message(frame: BroadcastFrame) -> dict[str, object] | None:
    if frame.schedule is None:
        return None
    kind = TYPE_START_SCHEDULE_REVOKED if frame.schedule_revoked else TYPE_START_SCHEDULE
    return message(kind, frame.schedule_seq, frame.schedule)


def error_message(code: str) -> dict[str, object]:
    return message(TYPE_ERROR, 0, {"code": code, "detail": "display_only"})


def classify_client_message(raw: str) -> ClientVerdict:
    """A viewport is a display request. Every other client message is ignored."""
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
    return ClientVerdict("read_only")


def plan_outbound(
    pending_bytes: int,
    *,
    hello_due: bool,
    schedule_due: bool,
    scene_due: bool,
    soft_bytes: int = SOFT_OUTBOUND_BYTES,
    hard_bytes: int = HARD_OUTBOUND_BYTES,
) -> OutboundPlan:
    """Prefer the newest scene, but do not drop a pending schedule to make room for one.

    At the hard cap the client is disconnected. It reconnects and receives the
    current scene and the current schedule. Memory stays bounded either way.
    """
    if pending_bytes >= hard_bytes:
        return OutboundPlan(drop=True, send_hello=False, send_schedule=False, send_scene=False)
    return OutboundPlan(
        drop=False,
        send_hello=hello_due,
        send_schedule=schedule_due,
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
    if ".." in request.target or path not in {"/", "/live", "/diagnostics"}:
        return AccessDecision("deny", 404, "Nicht gefunden")
    if request.method != "GET":
        return AccessDecision("deny", 405, "Nicht erlaubt")
    length = request.headers.get("content-length", "")
    if length not in {"", "0"}:
        return AccessDecision("deny", 400, "Anfrage abgelehnt")
    upgrade = request.headers.get("upgrade", "")
    if not upgrade:
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
    """Static diagnostic page. It has no asset URL and does not run the start lights."""
    return _DIAGNOSTIC_HTML.encode("utf-8")


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
  </dl>
  <p>Die Verbindung im lokalen Netzwerk ist nicht verschlüsselt.</p>
  <p>Der geplante GO-Zeitpunkt ist eine Host-Zeit und keine Startfreigabe.</p>
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
    opened.onopen = function () {
      if (socket !== opened) return;
      text(connection, "verbunden");
      showViewport();
    };
    opened.onerror = function () {
      if (socket === opened) text(connection, "fehler");
    };
    opened.onclose = function () {
      if (socket !== opened) return;
      text(connection, "getrennt");
      setTimeout(connect, 1000);
    };
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
      if (msg.type === "hello") {
        text(document.getElementById("app"), payload.application || "Slot-Racing Platform");
        text(race, payload.race_active ? "ja" : "nein");
        text(hud, payload.receiving_hud ? "ja" : "nein");
      } else if (msg.type === "scene") {
        if (payload.present === false) {
          text(hud, "nein");
          text(race, "nein");
        } else {
          text(hud, "ja");
          var active = payload.status === "running" || payload.status === "paused";
          text(race, active ? "ja" : "nein");
          var box = payload.viewport || {};
          if (box.width && box.height) {
            text(viewport, String(box.width) + " x " + String(box.height));
          }
        }
      } else if (msg.type === "start_schedule" || msg.type === "start_schedule_revoked") {
        text(plan, "Sequenz " + payload.sequence_id + ", Status " + payload.status
          + ", geplantes GO " + payload.planned_go_ns
          + " (Host-Zeit, keine Startfreigabe)");
      }
    };
  }
  window.addEventListener("resize", showViewport);
  showViewport();
  connect();
})();
</script>
</body>
</html>
"""
