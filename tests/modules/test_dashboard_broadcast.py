"""Dashboard address, start order, and the one race the browser is allowed to show."""

from __future__ import annotations

import socket
import sys
from collections.abc import Mapping
from pathlib import Path

import cv2
import numpy as np
import pytest
from PySide6.QtCore import QUrl
from PySide6.QtGui import QImage
from PySide6.QtWidgets import QApplication, QLabel, QPushButton
from pytestqt.qtbot import QtBot

from slot_racing.app.dashboard import DashboardPage
from slot_racing.app.main_window import MainWindow
from slot_racing.app.qr_code import qr_image
from slot_racing.app.runtime import Runtime
from slot_racing.core.config import AppConfig, load_config
from slot_racing.core.domain import RaceId, RaceStatus
from slot_racing.core.errors import ValidationError
from slot_racing.core.live_display import (
    LiveDisplay,
    LiveDisplayState,
    describe_live_display,
)
from slot_racing.modules.races.broadcast import DEFAULT_PORT, BroadcastDiagnostics, choose_bind_host
from slot_racing.modules.races.live_display import BroadcastDisplay
from slot_racing.modules.races.ui.live_server import LiveBroadcast, interface_candidates
from slot_racing.modules.races.ui.live_view import LiveRaceView
from tests.database import migrated_database
from tests.modules.conftest import Env
from tests.modules.test_live_broadcast import _Client, _endpoint, _payload
from tests.modules.test_live_race_ui import _ready_race
from tests.modules.test_race_flow import drive_until_done
from tests.support.start_sequence import release_start_lights

_HOST = "127.0.0.1"


def test_display_state_hides_the_address_unless_the_server_is_ready() -> None:
    ready = describe_live_display(
        phase="ready",
        enabled=True,
        running=True,
        url="http://127.0.0.1:9/live?token=secret",
        client_count=0,
        error="",
    )
    assert ready.state is LiveDisplayState.READY
    assert ready.usable
    assert ready.client_count == 0
    hidden = describe_live_display(
        phase="idle",
        enabled=True,
        running=False,
        url="http://127.0.0.1:9/live?token=secret",
        client_count=4,
        error="Der Port 9 ist bereits belegt.",
    )
    assert hidden.state is LiveDisplayState.ERROR
    assert hidden.url == ""
    assert hidden.client_count == 0
    assert "secret" not in hidden.detail
    assert (
        describe_live_display(
            phase="starting",
            enabled=True,
            running=False,
            url="http://127.0.0.1:9/live?token=secret",
            client_count=1,
            error="",
        ).state
        is LiveDisplayState.STARTING
    )
    assert (
        describe_live_display(
            phase="stopping",
            enabled=True,
            running=True,
            url="http://127.0.0.1:9/live?token=secret",
            client_count=1,
            error="",
        ).state
        is LiveDisplayState.STOPPING
    )
    off = describe_live_display(
        phase="idle",
        enabled=False,
        running=False,
        url="http://127.0.0.1:9/live?token=secret",
        client_count=0,
        error="alt",
    )
    assert off.state is LiveDisplayState.NOT_STARTED
    assert off.url == ""
    assert off.detail == ""


def test_the_qr_image_contains_only_the_given_address() -> None:
    url = "http://192.168.1.20:8765/live?token=session-token"
    image = qr_image(url)
    assert _decode_qr(image) == url
    assert _decode_qr(qr_image("http://192.168.1.20:8765/live?token=other")) != url
    with pytest.raises(ValueError):
        qr_image("")


def test_dashboard_shows_a_usable_address_and_updates_it(qtbot: QtBot, tmp_path: Path) -> None:
    port = _free_port()
    path = tmp_path / "config.json"
    runtime = Runtime.create(
        AppConfig(live_broadcast_port=port),
        config_path=path,
        database=migrated_database(),
    )
    try:
        window = MainWindow(runtime)
        qtbot.addWidget(window)
        window.show()
        page = window.current_page()
        assert isinstance(page, DashboardPage)
        assert _label(page, "dashboard-live-status").text() == "● Nicht gestartet"
        assert _label(page, "dashboard-live-url").text() == "Nicht verfügbar"
        assert not _label(page, "dashboard-live-qr").isVisible()
        assert not _button(page, "dashboard-live-open").isEnabled()
        assert not _button(page, "dashboard-live-copy").isEnabled()
        _button(page, "dashboard-live-start").click()
        url = _label(page, "dashboard-live-url")
        shown = url.property("live-url")
        assert isinstance(shown, str)
        assert shown.startswith("http://")
        assert "/live?token=" in shown
        assert shown == runtime.services.get(LiveBroadcast).access_url
        assert url.text().replace("\u200b", "") == shown
        assert _label(page, "dashboard-live-status").text() == "● Bereit"
        assert _label(page, "dashboard-live-clients").text() == "Keine Browser verbunden"
        qr = _label(page, "dashboard-live-qr")
        assert qr.isVisible()
        assert _decode_qr(_image_of(qr)) == shown
        opened: list[str] = []

        def _remember(target: QUrl) -> bool:
            opened.append(target.toString())
            return True

        page._open_url = _remember
        _button(page, "dashboard-live-open").click()
        assert opened == [shown]
        _button(page, "dashboard-live-copy").click()
        stored = QApplication.clipboard()
        assert stored is not None
        assert stored.text() == shown
        assert load_config(path).live_broadcast_enabled is True
        first = shown
        runtime.services.get(LiveBroadcast).stop()
        page.refresh()
        updated = _label(page, "dashboard-live-url").property("live-url")
        assert isinstance(updated, str)
        assert updated.startswith("http://")
        assert updated != first
        assert _decode_qr(_image_of(_label(page, "dashboard-live-qr"))) == updated
        assert "token=" not in _label(page, "dashboard-live-detail").text()
    finally:
        runtime.shutdown()


def test_dashboard_shows_a_start_failure_without_a_link(
    qtbot: QtBot, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Paint a failed start without opening a socket.

    Holding ``0.0.0.0`` does not stop Qt from listening on the specific address
    chosen for the broadcast. On Windows that listen succeeds, so the dashboard
    correctly shows ready. This test injects the failure ``start`` already
    reports to the dashboard.
    """
    port = 8765

    def fail_start(
        self: LiveBroadcast, host: str | None = None, port: int = DEFAULT_PORT
    ) -> BroadcastDiagnostics:
        del host
        self._phase = "idle"
        self._stats.failed(f"Der Port {port} ist bereits belegt.")
        return self.diagnostics()

    monkeypatch.setattr(LiveBroadcast, "start", fail_start)
    runtime = Runtime.create(
        AppConfig(live_broadcast_enabled=True, live_broadcast_port=port),
        database=migrated_database(),
    )
    try:
        window = MainWindow(runtime)
        qtbot.addWidget(window)
        window.show()
        page = window.current_page()
        assert isinstance(page, DashboardPage)
        assert _label(page, "dashboard-live-status").text() == "● Fehler"
        assert _label(page, "dashboard-live-url").text() == "Nicht verfügbar"
        assert _label(page, "dashboard-live-url").property("live-url") == ""
        qr = _label(page, "dashboard-live-qr")
        assert not qr.isVisible()
        pixmap = qr.pixmap()
        assert pixmap is None or pixmap.isNull()
        assert qr.property("qr-url") in {None, ""}
        assert not _label(page, "dashboard-live-clients").isVisible()
        assert not _button(page, "dashboard-live-open").isEnabled()
        assert not _button(page, "dashboard-live-copy").isEnabled()
        detail = _label(page, "dashboard-live-detail").text()
        assert str(port) in detail
        assert "token" not in detail
        status = runtime.services.get(LiveDisplay).status()
        assert status.state is LiveDisplayState.ERROR
        assert status.url == ""
        assert status.client_count == 0
        opened: list[str] = []

        def _remember(target: QUrl) -> bool:
            opened.append(target.toString())
            return True

        page._open_url = _remember
        page._open_live_url()
        assert opened == []
        stored = QApplication.clipboard()
        assert stored is not None
        before = stored.text()
        page._copy_live_url()
        assert stored.text() == before
        assert _button(page, "dashboard-live-start").text() == "Erneut versuchen"
        _button(page, "dashboard-live-start").click()
        assert _label(page, "dashboard-live-status").text() == "● Fehler"
        assert _label(page, "dashboard-live-url").property("live-url") == ""
        assert not _label(page, "dashboard-live-qr").isVisible()
        assert runtime.services.get(LiveDisplay).status().state is LiveDisplayState.ERROR
    finally:
        runtime.shutdown()


def test_an_occupied_bind_address_fails_and_restart_keeps_one_server(qapp) -> None:  # type: ignore[no-untyped-def]
    """The address Qt will bind stays occupied until this socket is closed.

    The holder uses that address, not ``0.0.0.0``. On Windows it also sets
    ``SO_EXCLUSIVEADDRUSE`` so a later listen cannot share the port.
    """
    assert qapp is not None
    host = choose_bind_host(interface_candidates())
    listener = _exclusive_listener(host)
    held: socket.socket | None = listener
    port = int(listener.getsockname()[1])
    service = LiveBroadcast()
    display = BroadcastDisplay(
        service,
        AppConfig(live_broadcast_enabled=True, live_broadcast_port=port),
        None,
    )
    try:
        busy = service.start(host, port)
        assert busy.running is False
        assert busy.error == f"Der Port {port} ist bereits belegt."
        assert service.access_url == ""
        assert service.is_running() is False
        assert service.phase == "idle"
        failed = display.status()
        assert failed.state is LiveDisplayState.ERROR
        assert failed.url == ""
        assert failed.client_count == 0
        assert failed.detail == busy.error
        assert "token" not in failed.detail
        listener.close()
        held = None
        opened = service.start(host, port)
        assert opened.running is True
        assert opened.host == host
        assert opened.port == port
        assert service.phase == "ready"
        ready = display.status()
        assert ready.state is LiveDisplayState.READY
        assert ready.usable
        assert ready.url == service.access_url
        assert ready.client_count == 0
        address = service.access_url
        thread = service._thread
        token = service._token
        assert thread is not None
        assert token
        again = service.start(host, port)
        assert again.running is True
        assert service.access_url == address
        assert service._thread is thread
        assert service._token == token
        with socket.create_connection((host, port), timeout=2):
            pass
    finally:
        if held is not None:
            held.close()
        service.stop()


def test_the_schedule_is_published_before_the_first_lamp_without_restarting(
    qtbot: QtBot, env: Env
) -> None:
    spy = _SpyBroadcast()
    assert spy.start(_HOST, 0).running
    spy.note_request(enabled=True, port=spy.diagnostics().port)
    started = spy.starts
    live: LiveRaceView | None = None
    try:
        _ready_race(env, laps=2)
        live = LiveRaceView(
            env.runtime.translator, env.controller, service=env.races, broadcast=spy
        )
        qtbot.addWidget(live)
        spy.view = live
        runner = env.controller.prepare_race(env.races.list_races()[0].id)
        live.show_runner(runner)
        spy.events.clear()
        live.begin_start_cue(interval_ms=60_000)
        assert spy.starts == started
        assert spy.events.index("prepare") < spy.events.index("schedule")
        assert spy.events.index("schedule") < spy.events.index("phase")
        assert spy.lights_at_schedule == 0
        assert live.start_lights.lit_lights == 1
        assert runner.status is RaceStatus.CREATED
    finally:
        spy.stop()
        if live is not None and live.runner is not None:
            live.runner.close()


def test_a_race_starts_with_no_browser_a_slow_browser_or_a_disconnected_one(
    qtbot: QtBot, env: Env
) -> None:
    service = LiveBroadcast()
    assert service.start(_HOST, 0).running
    service.note_request(enabled=True, port=service.diagnostics().port)
    live: LiveRaceView | None = None
    try:
        _ready_race(env, laps=1)
        live = LiveRaceView(
            env.runtime.translator, env.controller, service=env.races, broadcast=service
        )
        qtbot.addWidget(live)
        runner = env.controller.prepare_race(env.races.list_races()[0].id)
        live.show_runner(runner)
        live.begin_start_cue(interval_ms=60_000)
        assert service.diagnostics().client_count == 0
        release_start_lights(live)
        assert runner.status is RaceStatus.RUNNING
        assert service.diagnostics().client_count == 0
        runner.stop()
        assert not runner.is_active

        _second_race(env, "Langsam")
        slow = env.controller.prepare_race(_race_id(env, "Langsam"))
        host, port, token = _endpoint(service)
        client = _Client.connect(host, port, token)
        try:
            live.show_runner(slow)
            live.begin_start_cue(interval_ms=60_000)
            release_start_lights(live)
            assert slow.status is RaceStatus.RUNNING
        finally:
            client.close()
        slow.stop()

        _second_race(env, "Getrennt")
        gone = env.controller.prepare_race(_race_id(env, "Getrennt"))
        dropped = _Client.connect(host, port, token)
        dropped.close()
        live.show_runner(gone)
        live.begin_start_cue(interval_ms=60_000)
        release_start_lights(live)
        assert gone.status is RaceStatus.RUNNING
        gone.stop()
    finally:
        service.stop()
        if live is not None and live.runner is not None:
            live.runner.close()


def test_a_broadcast_failure_does_not_block_the_local_start(qtbot: QtBot, env: Env) -> None:
    dead = _DeadBroadcast()
    dead.note_request(enabled=True, port=9)
    live: LiveRaceView | None = None
    try:
        _ready_race(env, laps=1)
        live = LiveRaceView(
            env.runtime.translator, env.controller, service=env.races, broadcast=dead
        )
        qtbot.addWidget(live)
        runner = env.controller.prepare_race(env.races.list_races()[0].id)
        live.show_runner(runner)
        live.begin_start_cue(interval_ms=60_000)
        assert dead.prepare_calls == 1
        assert live.start_lights.lit_lights == 1
        assert "Browser-Anzeige" in live.warning.text()
        assert "token" not in live.warning.text()
        release_start_lights(live)
        assert runner.status is RaceStatus.RUNNING
    finally:
        if live is not None and live.runner is not None:
            live.runner.close()


def test_one_race_owns_the_display_and_the_next_race_keeps_the_address(
    qtbot: QtBot, env: Env
) -> None:
    service = LiveBroadcast()
    assert service.start(_HOST, 0).running
    service.note_request(enabled=True, port=service.diagnostics().port)
    address = service.access_url
    live: LiveRaceView | None = None
    try:
        _ready_race(env, laps=1)
        live = LiveRaceView(
            env.runtime.translator, env.controller, service=env.races, broadcast=service
        )
        qtbot.addWidget(live)
        first = env.controller.prepare_race(env.races.list_races()[0].id)
        _second_race(env, "Zweites")
        with pytest.raises(ValidationError) as caught:
            env.controller.prepare_race(_race_id(env, "Zweites"))
        assert caught.value.key == "error.race.already_running"
        live.show_runner(first)
        live.begin_start_cue(interval_ms=60_000)
        sent = service.diagnostics().state_updates_sent
        service.follow(int(first.race.id) + 1)
        live.refresh()
        assert service.diagnostics().state_updates_sent == sent
        service.follow(int(first.race.id))
        release_start_lights(live)
        drive_until_done(env, first)
        assert first.is_finished
        assert not first.is_active
        assert not first.snapshot().aborted

        second = env.controller.prepare_race(_race_id(env, "Zweites"))
        assert env.controller.active is second
        live.show_runner(second)
        live.begin_start_cue(interval_ms=60_000)
        release_start_lights(live)
        assert second.status is RaceStatus.RUNNING
        assert service.access_url == address
        host, port, token = _endpoint(service)
        client = _Client.connect(host, port, token)
        try:
            scenes = client.take_matching(lambda item: item.get("type") == "scene", timeout=2)
            assert _payload(scenes[-1])["race_name"] == "Zweites"
        finally:
            client.close()
        second.stop()
        assert not second.is_active
        assert second.snapshot().aborted
    finally:
        service.stop()
        if live is not None and live.runner is not None:
            live.runner.close()


def test_releasing_the_live_view_returns_the_browser_to_waiting(qtbot: QtBot, env: Env) -> None:
    service = LiveBroadcast()
    assert service.start(_HOST, 0).running
    live: LiveRaceView | None = None
    try:
        _ready_race(env, laps=1)
        live = LiveRaceView(
            env.runtime.translator, env.controller, service=env.races, broadcast=service
        )
        qtbot.addWidget(live)
        runner = env.controller.start_race(env.races.list_races()[0].id)
        live.show_runner(runner)
        runner.stop()
        live._release()
        host, port, token = _endpoint(service)
        client = _Client.connect(host, port, token)
        try:
            hello = client.take_matching(lambda item: item.get("type") == "hello", timeout=2)
            assert _payload(hello[-1])["receiving_hud"] is False
        finally:
            client.close()
    finally:
        service.stop()
        if live is not None and live.runner is not None:
            live.runner.close()


class _SpyBroadcast(LiveBroadcast):
    def __init__(self) -> None:
        super().__init__()
        self.events: list[str] = []
        self.starts = 0
        self.lights_at_schedule = -1
        self.view: LiveRaceView | None = None

    def start(self, host: str | None = None, port: int = DEFAULT_PORT) -> BroadcastDiagnostics:
        self.starts += 1
        self.events.append("start")
        return super().start(host, port)

    def prepare_for_countdown(self) -> BroadcastDiagnostics:
        self.events.append("prepare")
        return super().prepare_for_countdown()

    def publish_schedule(self, document: Mapping[str, object], *, revoked: bool) -> int:
        self.events.append("revoked" if revoked else "schedule")
        if not revoked and self.view is not None:
            self.lights_at_schedule = self.view.start_lights.lit_lights
        return super().publish_schedule(document, revoked=revoked)

    def publish_start_phase(self, document: Mapping[str, object]) -> int:
        self.events.append("phase")
        return super().publish_start_phase(document)


class _DeadBroadcast(LiveBroadcast):
    def __init__(self) -> None:
        super().__init__()
        self.prepare_calls = 0

    def prepare_for_countdown(self) -> BroadcastDiagnostics:
        self.prepare_calls += 1
        self._stats.failed("Der Port ist bereits belegt.")
        self._phase = "idle"
        return self.diagnostics()


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind((_HOST, 0))
        return int(sock.getsockname()[1])


def _exclusive_listener(host: str) -> socket.socket:
    """Bind ``host`` on an OS-assigned port and keep that socket open.

    Closing the socket and binding the port again is a race. Binding every
    interface does not reserve the specific address Qt listens on: Windows
    allows that second bind unless the first socket set ``SO_EXCLUSIVEADDRUSE``.
    """
    held = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        if sys.platform == "win32":
            exclusive = getattr(socket, "SO_EXCLUSIVEADDRUSE", None)
            if not isinstance(exclusive, int):
                raise OSError("SO_EXCLUSIVEADDRUSE is required on Windows")
            held.setsockopt(socket.SOL_SOCKET, exclusive, 1)
        held.bind((host, 0))
        held.listen(1)
    except OSError:
        held.close()
        raise
    return held


def _label(page: object, name: str) -> QLabel:
    assert isinstance(page, DashboardPage)
    label = page.findChild(QLabel, name)
    assert isinstance(label, QLabel)
    return label


def _button(page: DashboardPage, name: str) -> QPushButton:
    button = page.findChild(QPushButton, name)
    assert isinstance(button, QPushButton)
    return button


def _image_of(label: QLabel) -> QImage:
    pixmap = label.pixmap()
    assert pixmap is not None and not pixmap.isNull()
    return pixmap.toImage()


def _decode_qr(image: QImage) -> str:
    converted = image.convertToFormat(QImage.Format.Format_RGB32)
    width = converted.width()
    height = converted.height()
    line = converted.bytesPerLine()
    raw = np.frombuffer(converted.constBits(), dtype=np.uint8, count=line * height)
    pixels = raw.reshape((height, line))[:, : width * 4].reshape((height, width, 4))
    gray = cv2.cvtColor(np.ascontiguousarray(pixels), cv2.COLOR_BGRA2GRAY)
    value, _points, _straight = cv2.QRCodeDetector().detectAndDecode(gray)
    assert isinstance(value, str)
    return value


def _second_race(env: Env, name: str) -> None:
    track = env.tracks.list_tracks()[0]
    drivers = env.drivers.list_drivers()
    vehicles = env.vehicles.list_vehicles()
    race = env.races.create_race(name, track.id, 1)
    env.races.add_participant(race.id, drivers[0].id, vehicles[0].id, 1)


def _race_id(env: Env, name: str) -> RaceId:
    found = next(race for race in env.races.list_races() if race.name == name)
    return found.id
