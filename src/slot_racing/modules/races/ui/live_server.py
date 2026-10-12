"""Local HTTP and WebSocket display. The race clock never waits on a socket.

One ``QTcpServer`` on its own thread accepts browsers. HTTP serves the live HUD,
and ``/diagnostics`` keeps the diagnostic page. The same port upgrades to
WebSocket and then only sends the mailbox. A viewport message recomposes that
socket's scene. ``write`` queues bytes. Nothing here calls
``waitForBytesWritten`` or ``flush``, and nothing here holds a race controller.

The listen address is one IPv4 address, never every interface. The session
token stays in the access URL and out of logs. HTTP and WebSocket on the LAN
are not encrypted.
"""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Mapping
from dataclasses import dataclass

from PySide6.QtCore import QCoreApplication, QObject, Qt, QThread, QTimer, Signal, Slot
from PySide6.QtNetwork import (
    QAbstractSocket,
    QHostAddress,
    QNetworkInterface,
    QTcpServer,
    QTcpSocket,
)

from slot_racing.core.diagnostics import record
from slot_racing.modules.races.broadcast import (
    DEFAULT_PORT,
    HARD_OUTBOUND_BYTES,
    MAX_CLIENTS,
    MAX_HEADER_BYTES,
    MAX_INBOUND_BYTES,
    SOFT_OUTBOUND_BYTES,
    TYPE_SCENE,
    TYPE_START_SCHEDULE,
    TYPE_START_SCHEDULE_REVOKED,
    BindError,
    BroadcastDiagnostics,
    BroadcastFrame,
    BroadcastMailbox,
    DecodedFrame,
    classify_client_message,
    decide_access,
    diagnostic_page,
    dump_message,
    encode_control_frame,
    encode_text_frame,
    error_message,
    hello_message,
    http_response,
    new_session_token,
    normalize_bind_host,
    parse_http_request,
    plan_outbound,
    scene_message_for,
    schedule_message,
    take_frames,
    websocket_upgrade,
)
from slot_racing.modules.races.hud import HudLayout
from slot_racing.modules.races.hud_scene import HudPresentation, HudViewport
from slot_racing.modules.races.live_page import hud_page
from slot_racing.modules.races.runner import RaceSnapshot

logger = logging.getLogger(__name__)

_PLAIN = "text/plain; charset=utf-8"
_HTML = "text/html; charset=utf-8"


def interface_candidates() -> list[tuple[str, bool]]:
    """Up IPv4 addresses that can be private. Loopback and wildcards are left out."""
    found: list[tuple[str, bool]] = []
    for interface in QNetworkInterface.allInterfaces():
        flags = interface.flags()
        up = bool(flags & QNetworkInterface.InterfaceFlag.IsUp) and bool(
            flags & QNetworkInterface.InterfaceFlag.IsRunning
        )
        for entry in interface.addressEntries():
            ip = entry.ip()
            if ip.protocol() != QAbstractSocket.NetworkLayerProtocol.IPv4Protocol:
                continue
            if ip.isLoopback() or ip.isNull():
                continue
            text = ip.toString()
            if text and text != "0.0.0.0":
                found.append((text, up))
    return found


class _Stats:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._running = False
        self._host = ""
        self._port = 0
        self._clients = 0
        self._last_send_ns: int | None = None
        self._state = 0
        self._control = 0
        self._error = ""

    def snapshot(self) -> BroadcastDiagnostics:
        with self._lock:
            return BroadcastDiagnostics(
                running=self._running,
                host=self._host,
                port=self._port,
                client_count=self._clients,
                last_send_ns=self._last_send_ns,
                state_updates_sent=self._state,
                control_updates_sent=self._control,
                error=self._error,
            )

    def ready(self, host: str, port: int) -> None:
        with self._lock:
            self._running = True
            self._host = host
            self._port = port
            self._error = ""
            self._clients = 0
            self._state = 0
            self._control = 0
            self._last_send_ns = None

    def failed(self, message: str) -> None:
        with self._lock:
            self._running = False
            self._error = message

    def stopped(self) -> None:
        with self._lock:
            self._running = False
            self._clients = 0

    def set_clients(self, count: int) -> None:
        with self._lock:
            self._clients = count

    def mark_sent(self, kind: str) -> None:
        """``last_send_ns`` is a diagnostic clock, not the race clock."""
        with self._lock:
            self._last_send_ns = time.perf_counter_ns()
            if kind == TYPE_SCENE:
                self._state += 1
            elif kind in {TYPE_START_SCHEDULE, TYPE_START_SCHEDULE_REVOKED}:
                self._control += 1


@dataclass(slots=True)
class _Client:
    socket: QTcpSocket
    inbound: bytearray
    websocket: bool = False
    hello_sent: bool = False
    scene_seq: int = 0
    schedule_seq: int = 0
    rejected: bool = False
    dropped: bool = False
    closing: bool = False
    viewport: HudViewport | None = None
    viewport_pending: bool = False


class _Poster(QObject):
    """Lives on the GUI thread. Emitting only queues work for the server thread."""

    wake = Signal()
    stop = Signal()

    def kick(self) -> None:
        self.wake.emit()


class _Worker(QObject):
    def __init__(
        self,
        *,
        host: str,
        port: int,
        token: str,
        mailbox: BroadcastMailbox,
        stats: _Stats,
        ready: threading.Event,
        done: threading.Event,
        failure: dict[str, str],
    ) -> None:
        super().__init__()
        self._host = host
        self._port = port
        self._token = token
        self._mailbox = mailbox
        self._stats = stats
        self._ready = ready
        self._done = done
        self._failure = failure
        self._server: QTcpServer | None = None
        self._clients: dict[int, _Client] = {}
        self._closed = False
        self._rewriting = False
        self._scene_cache: dict[tuple[int, tuple[int, int] | None], str] = {}
        self._cache_seq = -1

    @Slot()
    def listen(self) -> None:
        try:
            self._open()
        except Exception:
            logger.exception("Live display failed to listen")
            self._failure["error"] = "Der Live-Dienst konnte nicht gestartet werden."
        if self._failure.get("error"):
            self._ready.set()
            return
        # Fire once the thread is actually pumping events, so callers can connect.
        QTimer.singleShot(0, self._announce)

    @Slot()
    def _announce(self) -> None:
        if self._closed or self._failure.get("error"):
            self._ready.set()
            return
        self._stats.ready(self._host, self._port)
        self._ready.set()

    @Slot()
    def flush(self) -> None:
        if self._closed:
            return
        self._mailbox.wake.acknowledge()
        frame = self._mailbox.current()
        if self._cache_seq != frame.scene_seq:
            self._scene_cache.clear()
            self._cache_seq = frame.scene_seq
        for client in list(self._clients.values()):
            if client.websocket and not client.dropped:
                self._flush_client(client, frame)

    @Slot()
    def shutdown(self) -> None:
        try:
            if not self._closed:
                self._closed = True
                for client in list(self._clients.values()):
                    self._release(client, abort=True)
                server = self._server
                self._server = None
                if server is not None:
                    server.close()
                self._stats.stopped()
        finally:
            self._done.set()

    def _open(self) -> None:
        server = QTcpServer(self)
        self._server = server
        address = QHostAddress(self._host)
        if (
            address.isNull()
            or address.protocol() != QAbstractSocket.NetworkLayerProtocol.IPv4Protocol
            or address.toString() == "0.0.0.0"
        ):
            self._failure["error"] = "Die Live-Anzeige benötigt eine lokale IPv4-Adresse."
            return
        server.setMaxPendingConnections(MAX_CLIENTS)
        if not server.listen(address, self._port):
            self._failure["error"] = _listen_error(server, self._port)
            return
        bound = server.serverAddress().toString()
        self._host = bound or self._host
        self._port = server.serverPort()
        server.newConnection.connect(self._accept)

    def _accept(self) -> None:
        server = self._server
        if server is None or self._closed:
            return
        while server.hasPendingConnections():
            socket = server.nextPendingConnection()
            if socket is None:
                continue
            socket.setParent(server)
            if len(self._clients) >= MAX_CLIENTS:
                _send_http(socket, 503, "Zu viele Verbindungen", b"Zu viele Verbindungen")
                socket.disconnected.connect(socket.deleteLater)
                continue
            socket.setSocketOption(QAbstractSocket.SocketOption.LowDelayOption, 1)
            socket.setReadBufferSize(MAX_INBOUND_BYTES)
            client = _Client(socket=socket, inbound=bytearray())
            self._clients[id(socket)] = client
            socket.readyRead.connect(self._on_ready)
            socket.disconnected.connect(self._on_disconnected)
            socket.errorOccurred.connect(self._on_error)
            socket.bytesWritten.connect(self._on_bytes_written)
            self._stats.set_clients(len(self._clients))
            if socket.bytesAvailable():
                self._read(client)

    def _on_ready(self) -> None:
        client = self._client_from_sender()
        if client is not None:
            self._read(client)

    def _on_disconnected(self) -> None:
        client = self._client_from_sender()
        if client is not None:
            self._release(client, abort=False)

    def _on_error(self, error: QAbstractSocket.SocketError) -> None:
        if error == QAbstractSocket.SocketError.RemoteHostClosedError:
            return
        client = self._client_from_sender()
        if client is not None:
            self._release(client, abort=True)

    def _on_bytes_written(self, _count: int) -> None:
        if self._rewriting or self._closed:
            return
        client = self._client_from_sender()
        if client is None or not client.websocket or client.dropped:
            return
        self._rewriting = True
        try:
            self._flush_client(client, self._mailbox.current())
        finally:
            self._rewriting = False

    def _read(self, client: _Client) -> None:
        if client.dropped or client.closing:
            return
        chunk = client.socket.readAll().data()
        if not chunk:
            return
        if not client.websocket and len(client.inbound) + len(chunk) > MAX_HEADER_BYTES:
            self._reject(client, 400, "Anfrage zu groß")
            return
        client.inbound.extend(chunk)
        if client.websocket:
            self._read_frames(client)
        else:
            self._read_http(client)

    def _read_http(self, client: _Client) -> None:
        split_at = client.inbound.find(b"\r\n\r\n")
        if split_at < 0:
            if len(client.inbound) > MAX_HEADER_BYTES:
                self._reject(client, 400, "Anfrage zu groß")
            return
        head = bytes(client.inbound[:split_at])
        client.inbound = bytearray(client.inbound[split_at + 4 :])
        request = parse_http_request(head)
        if request is None:
            self._reject(client, 400, "Anfrage abgelehnt")
            return
        decision = decide_access(request, bound_host=self._host, port=self._port, token=self._token)
        if decision.kind in {"page", "diagnostics"}:
            body = diagnostic_page() if decision.kind == "diagnostics" else hud_page()
            if client.socket.write(http_response(200, "OK", body, _HTML)) < 0:
                self._release(client, abort=True)
                return
            client.socket.disconnectFromHost()
            return
        if decision.kind == "websocket":
            key = request.headers.get("sec-websocket-key", "")
            if client.socket.write(websocket_upgrade(key)) < 0:
                self._release(client, abort=True)
                return
            client.websocket = True
            self._flush_client(client, self._mailbox.current())
            if client.inbound:
                self._read_frames(client)
            return
        logger.info("Live display rejected a connection")
        self._reject(client, decision.status, decision.reason)

    def _read_frames(self, client: _Client) -> None:
        frames, error = take_frames(client.inbound, expect_mask=True, limit=MAX_INBOUND_BYTES)
        if error == "too_large":
            self._close_ws(client, 1009)
            return
        if error is not None:
            self._close_ws(client, 1002)
            return
        refresh = False
        for frame in frames:
            if client.dropped:
                return
            if self._handle_frame(client, frame):
                refresh = True
        if refresh and not client.dropped:
            self._flush_client(client, self._mailbox.current())

    def _handle_frame(self, client: _Client, frame: DecodedFrame) -> bool:
        """Return true when this socket needs its scene again. A viewport is not a command."""
        if frame.opcode == 0x8:
            self._close_ws(client, 1000)
            return False
        if frame.opcode == 0x9:
            self._write_raw(client, encode_control_frame(0xA, frame.payload))
            return False
        if frame.opcode == 0xA:
            return False
        if frame.opcode != 0x1:
            self._close_ws(client, 1002)
            return False
        if client.rejected:
            return False
        try:
            text = frame.payload.decode("utf-8")
        except UnicodeDecodeError:
            verdict_code = "invalid_message"
            viewport = None
        else:
            verdict = classify_client_message(text)
            verdict_code = verdict.code
            viewport = verdict.viewport
        if verdict_code == "viewport" and viewport is not None:
            client.viewport = viewport
            client.viewport_pending = True
            return True
        client.rejected = True
        self._send_text(client, dump_message(error_message(verdict_code)), kind="")
        return False

    def _flush_client(self, client: _Client, frame: BroadcastFrame) -> None:
        if client.dropped or not client.websocket:
            return
        plan = plan_outbound(
            client.socket.bytesToWrite(),
            hello_due=not client.hello_sent,
            schedule_due=frame.schedule is not None and frame.schedule_seq != client.schedule_seq,
            scene_due=frame.scene is not None
            and (frame.scene_seq != client.scene_seq or client.viewport_pending),
            soft_bytes=SOFT_OUTBOUND_BYTES,
            hard_bytes=HARD_OUTBOUND_BYTES,
        )
        if plan.drop:
            self._release(client, abort=True)
            return
        if plan.send_hello and not self._send_text(
            client, dump_message(hello_message(frame)), kind="hello"
        ):
            return
        if plan.send_hello:
            client.hello_sent = True
        if plan.send_schedule:
            document = schedule_message(frame)
            if document is not None and self._send_text(
                client, dump_message(document), kind=str(document["type"])
            ):
                client.schedule_seq = frame.schedule_seq
        if plan.send_scene:
            text = self._scene_text(frame, client.viewport)
            if text is not None and self._send_text(client, text, kind=TYPE_SCENE):
                client.scene_seq = frame.scene_seq
                client.viewport_pending = False

    def _scene_text(self, frame: BroadcastFrame, viewport: HudViewport | None) -> str | None:
        key = (frame.scene_seq, None if viewport is None else (viewport.width, viewport.height))
        cached = self._scene_cache.get(key)
        if cached is not None:
            return cached
        document = scene_message_for(frame, viewport)
        if document is None:
            return None
        text = dump_message(document)
        if len(self._scene_cache) >= MAX_CLIENTS:
            self._scene_cache.clear()
        self._scene_cache[key] = text
        return text

    def _send_text(self, client: _Client, text: str, *, kind: str) -> bool:
        try:
            payload = encode_text_frame(text)
        except ValueError:
            logger.error("Live display skipped a message that is too large")
            return False
        return self._write_raw(client, payload, kind=kind)

    def _write_raw(self, client: _Client, payload: bytes, *, kind: str = "") -> bool:
        # Queue only. waitForBytesWritten() and flush() can block this thread.
        if client.dropped:
            return False
        if client.socket.bytesToWrite() >= HARD_OUTBOUND_BYTES:
            self._release(client, abort=True)
            return False
        if len(payload) > HARD_OUTBOUND_BYTES:
            return False
        if client.socket.write(payload) < 0:
            self._release(client, abort=True)
            return False
        if kind:
            self._stats.mark_sent(kind)
        return True

    def _reject(self, client: _Client, status: int, reason: str) -> None:
        # Leave the socket until ``disconnected``. Releasing it here drops the reply.
        if client.closing or client.dropped:
            return
        client.closing = True
        _send_http(client.socket, status, _http_reason(status), reason.encode("utf-8"))

    def _close_ws(self, client: _Client, code: int) -> None:
        if not client.dropped:
            self._write_raw(client, encode_control_frame(0x8, code.to_bytes(2, "big")))
        self._release(client, abort=False)

    def _release(self, client: _Client, *, abort: bool) -> None:
        if client.dropped:
            return
        client.dropped = True
        socket = client.socket
        self._clients.pop(id(socket), None)
        self._stats.set_clients(len(self._clients))
        _disconnect(socket.readyRead, self._on_ready)
        _disconnect(socket.disconnected, self._on_disconnected)
        _disconnect(socket.errorOccurred, self._on_error)
        _disconnect(socket.bytesWritten, self._on_bytes_written)
        if abort:
            socket.abort()
        elif socket.state() != QAbstractSocket.SocketState.UnconnectedState:
            socket.disconnectFromHost()
        socket.deleteLater()

    def _client_from_sender(self) -> _Client | None:
        source = self.sender()
        if not isinstance(source, QTcpSocket):
            return None
        return self._clients.get(id(source))


class LiveBroadcast:
    """Start and stop the local display. Publishing only copies state into the mailbox."""

    def __init__(self) -> None:
        self._stats = _Stats()
        self._mailbox = BroadcastMailbox()
        self._token = ""
        self._accepting = False
        self._thread: QThread | None = None
        self._worker: _Worker | None = None
        self._poster: _Poster | None = None
        self._done = threading.Event()
        self._done.set()

    def start(self, host: str | None = None, port: int = DEFAULT_PORT) -> BroadcastDiagnostics:
        """Listen until :meth:`stop`. A failure is reported and does not raise."""
        if self._accepting and self.is_running():
            return self.diagnostics()
        self.stop()
        if QCoreApplication.instance() is None:
            self._stats.failed("Der Live-Dienst braucht die laufende Anwendung.")
            return self.diagnostics()
        if port < 0 or port > 65535:
            self._stats.failed("Der Port liegt außerhalb des gültigen Bereichs.")
            return self.diagnostics()
        try:
            chosen = normalize_bind_host(host, interface_candidates())
        except BindError as error:
            self._stats.failed(str(error))
            return self.diagnostics()
        token = new_session_token()
        mailbox = BroadcastMailbox()
        ready = threading.Event()
        failure: dict[str, str] = {}
        poster = _Poster()
        worker = _Worker(
            host=chosen,
            port=port,
            token=token,
            mailbox=mailbox,
            stats=self._stats,
            ready=ready,
            done=self._done,
            failure=failure,
        )
        thread = QThread()
        worker.moveToThread(thread)
        thread.started.connect(worker.listen)
        poster.wake.connect(worker.flush, Qt.ConnectionType.QueuedConnection)
        poster.stop.connect(worker.shutdown, Qt.ConnectionType.QueuedConnection)
        mailbox.wake.set_callback(poster.kick)
        self._poster = poster
        self._worker = worker
        self._thread = thread
        self._mailbox = mailbox
        self._token = token
        self._done.clear()
        thread.start()
        if not ready.wait(2.0):
            self._stats.failed("Der Live-Dienst hat nicht rechtzeitig geantwortet.")
            self.stop()
            return self.diagnostics()
        listen_error = failure.get("error", "")
        if listen_error:
            self._stats.failed(listen_error)
            self.stop()
            return self.diagnostics()
        self._accepting = True
        snapshot = self.diagnostics()
        logger.info("Live display listening on %s:%s", snapshot.host, snapshot.port)
        record(
            "LIVE_BROADCAST",
            module="races",
            page="broadcast",
            result="started",
            host=snapshot.host,
            port=snapshot.port,
        )
        return snapshot

    def ensure_started(self, *, enabled: bool, port: int) -> BroadcastDiagnostics:
        if not enabled or self.is_running():
            return self.diagnostics()
        return self.start(port=port)

    def stop(self) -> None:
        """Close the port. A running race is left untouched."""
        self._accepting = False
        self._mailbox.wake.set_callback(None)
        poster = self._poster
        thread = self._thread
        if poster is not None and thread is not None and thread.isRunning():
            self._done.clear()
            poster.stop.emit()
            self._done.wait(3.0)
            thread.quit()
            thread.wait(3000)
        self._poster = None
        self._worker = None
        self._thread = None
        self._token = ""
        self._mailbox.clear()
        self._done.set()
        if self._stats.snapshot().running:
            self._stats.stopped()

    def publish_source(
        self, layout: HudLayout, snapshot: RaceSnapshot, presentation: HudPresentation
    ) -> int:
        """Copy one live view into the mailbox. Per-client sizes are composed later."""
        if not self._accepting:
            return 0
        try:
            return self._mailbox.publish_source(layout, snapshot, presentation)
        except Exception:
            logger.exception("Live display ignored a scene that cannot be sent")
            return 0

    def publish_scene(self, payload: Mapping[str, object]) -> int:
        if not self._accepting:
            return 0
        try:
            return self._mailbox.publish_scene(payload)
        except ValueError:
            logger.error("Live display ignored a scene that cannot be sent")
            return 0

    def publish_schedule(self, document: Mapping[str, object], *, revoked: bool) -> int:
        if not self._accepting:
            return 0
        try:
            return self._mailbox.publish_schedule(document, revoked=revoked)
        except ValueError:
            logger.error("Live display ignored a schedule that cannot be sent")
            return 0

    def clear_scene(self) -> None:
        if self._accepting:
            self._mailbox.clear_scene()

    def is_running(self) -> bool:
        return self._accepting and self._stats.snapshot().running

    def diagnostics(self) -> BroadcastDiagnostics:
        return self._stats.snapshot()

    @property
    def access_url(self) -> str:
        """In-process URL. Do not log it: the query holds the session token."""
        snapshot = self.diagnostics()
        token = self._token
        if not self.is_running() or not token:
            return ""
        return f"http://{snapshot.host}:{snapshot.port}/live?token={token}"


def _listen_error(server: QTcpServer, port: int) -> str:
    if server.serverError() == QAbstractSocket.SocketError.AddressInUseError:
        return f"Der Port {port} ist bereits belegt."
    detail = server.errorString().strip() or "unbekannter Fehler"
    return f"Der Live-Dienst konnte nicht gestartet werden: {detail}"


def _http_reason(status: int) -> str:
    if status == 403:
        return "Forbidden"
    if status == 404:
        return "Not Found"
    if status == 405:
        return "Method Not Allowed"
    if status == 503:
        return "Service Unavailable"
    return "Bad Request"


def _send_http(socket: QTcpSocket, status: int, reason: str, body: bytes) -> None:
    socket.write(http_response(status, reason, body, _PLAIN))
    socket.disconnectFromHost()


def _disconnect(signal: object, slot: object) -> None:
    disconnect = getattr(signal, "disconnect", None)
    if not callable(disconnect):
        return
    try:
        disconnect(slot)
    except (RuntimeError, TypeError):
        return
