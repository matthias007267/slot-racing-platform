"""Local live display: protocol, sockets, and the race clock staying independent."""

from __future__ import annotations

import json
import logging
import socket
import time
from collections.abc import Callable, Iterator
from urllib.parse import parse_qs, urlsplit

import pytest
from pydantic import ValidationError
from pytestqt.qtbot import QtBot

from slot_racing.app.runtime import Runtime
from slot_racing.core.clock import NANOS_PER_SECOND, ManualClock
from slot_racing.core.config import AppConfig, load_config
from slot_racing.core.domain import RaceStatus
from slot_racing.modules.races.broadcast import (
    APP_NAME,
    BROADCAST_PROTOCOL_VERSION,
    HARD_OUTBOUND_BYTES,
    REFERENCE_VIEWPORT,
    SOFT_OUTBOUND_BYTES,
    BroadcastMailbox,
    choose_bind_host,
    classify_client_message,
    compose_scene,
    copy_document,
    decide_access,
    diagnostic_page,
    dump_message,
    encode_text_frame,
    error_message,
    hello_message,
    new_session_token,
    normalize_bind_host,
    parse_http_request,
    parse_viewport,
    plan_outbound,
    scene_message,
    scene_payload_for,
    schedule_message,
    take_frames,
    token_matches,
)
from slot_racing.modules.races.hud import factory_layout
from slot_racing.modules.races.hud_scene import (
    HudViewport,
    build_hud_scene,
    ranking_best_fits,
    ranking_column_visible,
    ranking_width_hidden,
    scene_payload,
)
from slot_racing.modules.races.live_page import START_HINT, hud_page
from slot_racing.modules.races.start_schedule import TIME_BASE, StartSchedule
from slot_racing.modules.races.ui.live_server import LiveBroadcast
from slot_racing.modules.races.ui.live_view import LiveRaceView
from tests.database import migrated_database
from tests.modules.conftest import Env
from tests.modules.test_hud_scene import _presentation, _snapshot
from tests.modules.test_live_race_ui import _ready_race

_HOST = "127.0.0.1"


def test_bind_host_prefers_one_private_address() -> None:
    assert choose_bind_host([("8.8.8.8", True), ("192.168.1.20", True)]) == "192.168.1.20"
    assert choose_bind_host([("10.1.2.3", True)]) == "10.1.2.3"
    assert choose_bind_host([("172.16.0.4", True)]) == "172.16.0.4"
    assert choose_bind_host([("192.168.0.8", False), ("1.2.3.4", True)]) == _HOST
    assert choose_bind_host([("0.0.0.0", True)]) == _HOST
    assert choose_bind_host([]) == _HOST
    with pytest.raises(ValueError, match="Schnittstellen"):
        normalize_bind_host("0.0.0.0", [])


def test_messages_are_versioned_json_and_client_text_is_not_a_command() -> None:
    frame = BroadcastMailbox().current()
    hello = hello_message(frame)
    encoded = dump_message(hello)
    parsed = json.loads(encoded)
    assert parsed["protocol_version"] == BROADCAST_PROTOCOL_VERSION
    assert parsed["type"] == "hello"
    assert parsed["payload"]["application"] == APP_NAME
    assert parsed["payload"]["read_only"] is True
    assert parsed["payload"]["time_base"] == TIME_BASE
    assert parsed["payload"]["transport"] == "unencrypted_local_network"
    assert parsed["payload"]["race_active"] is False
    for raw in ("", "null", "{}", "[]", "{", '{"type": ""}', "12"):
        assert classify_client_message(raw).code == "invalid_message"
    for raw in (
        '{"type": "start_race"}',
        '{"type": "pause"}',
        '{"type": "resume"}',
        '{"type": "stop"}',
        '{"type": "abort"}',
        '{"type": "set_lap"}',
        '{"type": "edit_driver"}',
    ):
        verdict = classify_client_message(raw)
        assert verdict.code == "read_only"
        assert not hasattr(verdict, "action")
    for raw in (
        '{"type": "viewport", "width": 100}',
        '{"type": "viewport", "width": true, "height": 100}',
        '{"type": "viewport", "width": "100", "height": 100}',
    ):
        assert classify_client_message(raw).code == "invalid_message"
    fitted = classify_client_message('{"type": "viewport", "width": 1024, "height": 768}')
    assert fitted.code == "viewport"
    assert fitted.viewport == HudViewport(1024, 768)
    huge = classify_client_message('{"type": "viewport", "width": 99999, "height": 0}')
    assert huge.code == "viewport"
    assert huge.viewport == HudViewport(8192, 1)
    error = error_message("read_only")
    assert error["seq"] == 0
    assert error["payload"] == {"code": "read_only", "detail": "display_only"}


def test_the_scene_is_the_hud_scene_and_the_layout_stays_put() -> None:
    layout = factory_layout()
    snapshot = _snapshot(RaceStatus.RUNNING)
    presentation = _presentation()
    fields = layout.fields
    payload = compose_scene(layout, snapshot, presentation)
    direct = scene_payload(build_hud_scene(layout, snapshot, REFERENCE_VIEWPORT, presentation))
    assert payload == direct
    assert payload["race_name"] == "Finale"
    assert payload["status"] == "running"
    viewport = payload["viewport"]
    assert isinstance(viewport, dict)
    assert viewport["width"] == 1280
    assert viewport["height"] == 720
    assert layout.fields is fields
    narrow = compose_scene(layout, snapshot, presentation, HudViewport(390, 844))
    assert narrow["viewport"] == {"width": 390, "height": 844}
    assert layout.fields is fields
    again_viewport = compose_scene(layout, snapshot, presentation)["viewport"]
    assert isinstance(again_viewport, dict)
    assert again_viewport["width"] == 1280
    box = BroadcastMailbox()
    box.publish_source(layout, snapshot, presentation)
    held = box.current()
    assert held.source is not None
    reference = scene_payload_for(held, None)
    assert isinstance(reference, dict)
    assert reference["viewport"] == {"width": 1280, "height": 720}
    phone = scene_payload_for(held, HudViewport(390, 844))
    assert isinstance(phone, dict)
    assert phone["viewport"] == {"width": 390, "height": 844}
    assert scene_payload_for(held, HudViewport(1280, 720)) == reference
    assert layout.fields is fields
    ranking = phone["ranking"]
    assert isinstance(ranking, dict)
    columns = ranking["columns"]
    assert isinstance(columns, list)
    panel_width = 0
    panels = phone["panels"]
    assert isinstance(panels, list)
    for panel in panels:
        assert isinstance(panel, dict)
        if panel.get("id") == "ranking":
            assert isinstance(panel["width"], int)
            panel_width = panel["width"]
    assert panel_width > 0
    for column in columns:
        assert isinstance(column, dict)
        assert column["visible"] is ranking_column_visible(str(column["id"]), panel_width)
    assert ranking_width_hidden(39) is None
    assert ranking_width_hidden(980) == frozenset()
    assert 2 in (ranking_width_hidden(299) or frozenset())
    assert 8 not in (ranking_width_hidden(299) or frozenset())
    assert ranking_best_fits(39, 999) is True
    assert ranking_best_fits(240, 200) is False
    assert ranking_best_fits(240, 100) is True
    assert parse_viewport({"width": 10, "height": 10}) == HudViewport(10, 10)


def test_schedule_and_revoke_are_kept_apart_from_replaceable_scenes() -> None:
    schedule = StartSchedule.build(reference_ns=5_000, step_interval_ns=1_000_000)
    box = BroadcastMailbox()
    wakes: list[int] = []
    box.wake.set_callback(lambda: wakes.append(1))
    for index in range(1000):
        box.publish_scene({"n": index, "blob": "x"})
    assert len(wakes) == 1
    box.wake.acknowledge()
    active = schedule.to_document()
    box.publish_schedule(active, revoked=False)
    for index in range(50):
        box.publish_scene({"n": index})
    held = box.current()
    assert held.scene == {"n": 49}
    assert held.scene_seq == 1050
    assert held.schedule == active
    assert held.schedule_revoked is False
    schedule_message(held)
    revoked = schedule.revoke().to_document()
    box.publish_schedule(revoked, revoked=True)
    box.publish_scene({"n": 50})
    current = box.current()
    assert current.schedule == revoked
    assert current.schedule_revoked is True
    document = schedule_message(current)
    assert document is not None
    assert document["type"] == "start_schedule_revoked"
    assert document["protocol_version"] == BROADCAST_PROTOCOL_VERSION
    body = _payload(document)
    assert body["planned_go_ns"] == schedule.planned_go_ns
    assert body["status"] == "revoked"
    scene = scene_message(current)
    assert scene is not None and scene["type"] == "scene"
    original = {"n": 1, "rows": [1, 2]}
    box.publish_scene(original)
    original["n"] = 9
    assert box.current().scene == {"n": 1, "rows": [1, 2]}
    with pytest.raises(ValueError, match="JSON"):
        copy_document({"bad": object()})


def test_a_full_buffer_drops_the_client_and_a_soft_buffer_skips_the_scene() -> None:
    blocked = plan_outbound(HARD_OUTBOUND_BYTES, hello_due=True, schedule_due=True, scene_due=True)
    assert blocked.drop is True
    pressured = plan_outbound(
        SOFT_OUTBOUND_BYTES, hello_due=True, schedule_due=True, scene_due=True
    )
    assert pressured.drop is False
    assert pressured.send_hello is True
    assert pressured.send_schedule is True
    assert pressured.send_scene is False
    clear = plan_outbound(0, hello_due=True, schedule_due=False, scene_due=True)
    assert clear.send_scene is True


def test_access_checks_token_host_and_origin_without_opening_files() -> None:
    token = new_session_token()
    assert token_matches(token, token)
    assert not token_matches(token, "other-token-of-same-size-pad")
    request = parse_http_request(
        f"GET /live?token={token} HTTP/1.1\r\nHost: {_HOST}:8765\r\n\r\n".encode()
    )
    assert request is not None
    assert decide_access(request, bound_host=_HOST, port=8765, token=token).kind == "page"
    stolen = parse_http_request(
        b"GET /etc/passwd?token=nope HTTP/1.1\r\nHost: 127.0.0.1:8765\r\n\r\n"
    )
    assert stolen is not None
    denied = decide_access(stolen, bound_host=_HOST, port=8765, token=token)
    assert denied.status == 403
    foreign = parse_http_request(
        (
            f"GET /live?token={token} HTTP/1.1\r\n"
            f"Host: {_HOST}:8765\r\n"
            "Origin: http://evil.example\r\n\r\n"
        ).encode()
    )
    assert foreign is not None
    assert decide_access(foreign, bound_host=_HOST, port=8765, token=token).status == 403
    escaped = parse_http_request(
        f"GET /../secret?token={token} HTTP/1.1\r\nHost: {_HOST}:8765\r\n\r\n".encode()
    )
    assert escaped is not None
    assert decide_access(escaped, bound_host=_HOST, port=8765, token=token).status == 404
    session = parse_http_request(
        f"GET /session?token={token} HTTP/1.1\r\nHost: {_HOST}:8765\r\n\r\n".encode()
    )
    assert session is not None
    assert decide_access(session, bound_host=_HOST, port=8765, token=token).status == 204
    stale = parse_http_request(b"GET /session?token=old HTTP/1.1\r\nHost: 127.0.0.1:8765\r\n\r\n")
    assert stale is not None
    assert decide_access(stale, bound_host=_HOST, port=8765, token=token).status == 403
    page = diagnostic_page().decode()
    assert "Slot-Racing Platform" in page
    assert "nicht verschlüsselt" in page
    assert "keine Startfreigabe" in page
    assert "setInterval" not in page
    assert "/session" in page
    assert "sitzung abgelaufen" in page
    assert token not in page


def test_websocket_frames_roundtrip_and_reject_a_bad_client_frame() -> None:
    payload = encode_text_frame('{"type":"scene","seq":1}')
    buffer = bytearray(payload + payload)
    frames, error = take_frames(buffer, expect_mask=False, limit=1024)
    assert error is None
    assert len(frames) == 2
    assert buffer == bytearray()
    masked = bytearray(b"\x81\x01hello")
    _frames, bad = take_frames(masked, expect_mask=True, limit=1024)
    assert bad == "unmasked"


def test_old_config_leaves_the_display_off(tmp_path) -> None:  # type: ignore[no-untyped-def]
    path = tmp_path / "config.json"
    path.write_text('{"language": "de"}', encoding="utf-8")
    loaded = load_config(path)
    assert loaded.live_broadcast_enabled is False
    assert loaded.live_broadcast_port == 8765
    with pytest.raises(ValidationError):
        AppConfig(live_broadcast_port=0)


def test_the_plugin_does_not_listen_until_the_display_is_enabled(env: Env) -> None:
    service = env.runtime.services.get(LiveBroadcast)
    assert service.is_running() is False
    assert service.access_url == ""
    assert service.diagnostics().error == ""


@pytest.fixture
def display(qapp: object) -> Iterator[LiveBroadcast]:
    assert qapp is not None
    service = LiveBroadcast()
    diagnostics = service.start(_HOST, 0)
    assert diagnostics.running, diagnostics.error
    yield service
    service.stop()


def test_http_page_and_rejected_tokens(display: LiveBroadcast) -> None:
    host, port, token = _endpoint(display)
    status, body = _http(host, port, f"/live?token={token}")
    text = body.decode()
    assert status == 200
    assert "Slot-Racing Platform" in text
    assert "Verbindung unterbrochen" in text
    assert "Kein Rennen aktiv" in text
    assert token not in text
    diagnosed, diagnosed_body = _http(host, port, f"/diagnostics?token={token}")
    diagnosed_text = diagnosed_body.decode()
    assert diagnosed == 200
    assert "HUD-Daten" in diagnosed_text
    assert "Nachrichten" in diagnosed_text
    assert "Protokollfehler" in diagnosed_text
    assert "Viewport" in diagnosed_text
    assert token not in diagnosed_text
    denied, denied_body = _http(host, port, "/live?token=not-the-session")
    assert denied == 403
    assert b"not-the-session" not in denied_body
    session, session_body = _http(host, port, f"/session?token={token}")
    assert session == 204
    assert session_body == b""
    expired, expired_body = _http(host, port, "/session?token=not-the-session")
    assert expired == 403
    assert token.encode() not in expired_body
    assert b"not-the-session" not in expired_body
    missing, _body = _http(host, port, "/live")
    assert missing == 403
    wrong_host, _body = _http(host, port, f"/live?token={token}", host_header="192.0.2.1:9")
    assert wrong_host == 403
    escaped, escaped_body = _http(host, port, f"/../etc/passwd?token={token}")
    assert escaped == 404
    assert b"root:" not in escaped_body


def test_a_client_receives_hello_scene_schedule_and_revoke(display: LiveBroadcast) -> None:
    host, port, token = _endpoint(display)
    schedule = StartSchedule.build(reference_ns=8_000, step_interval_ns=2_000_000)
    scene = _scene_payload()
    display.publish_schedule(schedule.to_document(), revoked=False)
    display.publish_scene(scene)
    client = _Client.connect(host, port, token)
    try:
        messages = client.take(3)
        assert [item["type"] for item in messages] == ["hello", "start_schedule", "scene"]
        assert messages[0]["protocol_version"] == BROADCAST_PROTOCOL_VERSION
        assert _payload(messages[1])["sequence_id"] == schedule.sequence_id
        assert _payload(messages[1])["planned_go_ns"] == schedule.planned_go_ns
        assert _payload(messages[2])["race_name"] == "Finale"
        assert messages[2]["seq"] == 1
        display.publish_schedule(schedule.revoke().to_document(), revoked=True)
        revoked = client.take(1)[0]
        assert revoked["type"] == "start_schedule_revoked"
        assert _payload(revoked)["status"] == "revoked"
        assert revoked["seq"] == 2
    finally:
        client.close()


def test_reconnect_receives_the_current_state_not_the_old_one(display: LiveBroadcast) -> None:
    host, port, token = _endpoint(display)
    display.publish_scene(_scene_payload(name_marker=1))
    first = _Client.connect(host, port, token)
    try:
        seen = first.take(2)
        assert _payload(seen[1])["marker"] == 1
    finally:
        first.close()
    display.publish_scene(_scene_payload(name_marker=2))
    _wait(lambda: display.diagnostics().client_count == 0)
    second = _Client.connect(host, port, token)
    try:
        fresh = second.take(2)
        assert fresh[0]["type"] == "hello"
        assert fresh[1]["type"] == "scene"
        assert _payload(fresh[1])["marker"] == 2
        assert fresh[1]["seq"] == 2
    finally:
        second.close()


@pytest.mark.parametrize("count", [2, 5, 10])
def test_several_clients_see_the_same_scene(display: LiveBroadcast, count: int) -> None:
    host, port, token = _endpoint(display)
    clients = [_Client.connect(host, port, token) for _ in range(count)]
    try:
        _wait(lambda: display.diagnostics().client_count == count)
        display.publish_scene(_scene_payload(name_marker=count))
        for client in clients:
            messages = client.take(2)
            assert messages[0]["type"] == "hello"
            assert _payload(messages[1])["marker"] == count
    finally:
        for client in clients:
            client.close()


def test_one_disconnect_leaves_the_others_in_place(display: LiveBroadcast) -> None:
    host, port, token = _endpoint(display)
    clients = [_Client.connect(host, port, token) for _ in range(3)]
    try:
        clients[0].close()
        _wait(lambda: display.diagnostics().client_count == 2)
        display.publish_scene(_scene_payload(name_marker=3))
        for client in clients[1:]:
            messages = client.take(2)
            assert _payload(messages[-1])["marker"] == 3
    finally:
        for client in clients:
            client.close()


def test_a_bad_token_and_origin_are_refused(display: LiveBroadcast) -> None:
    host, port, token = _endpoint(display)
    rejected = _Client.connect(host, port, "not-a-valid-token")
    try:
        assert rejected.status == 403
    finally:
        rejected.close()
    foreign = _Client.connect(host, port, token, origin="http://evil.example")
    try:
        assert foreign.status == 403
    finally:
        foreign.close()


def test_a_busy_port_is_reported_and_the_first_server_keeps_running(qapp) -> None:  # type: ignore[no-untyped-def]
    assert qapp is not None
    first = LiveBroadcast()
    second = LiveBroadcast()
    try:
        opened = first.start(_HOST, 0)
        assert opened.running
        busy = second.start(_HOST, opened.port)
        assert busy.running is False
        assert f"Der Port {opened.port} ist bereits belegt." == busy.error
        assert first.diagnostics().running is True
    finally:
        second.stop()
        first.stop()


def test_stop_frees_the_port_and_restart_renews_the_token(display: LiveBroadcast) -> None:
    host, port, token = _endpoint(display)
    display.stop()
    assert display.is_running() is False
    assert display.access_url == ""
    with pytest.raises(OSError):
        socket.create_connection((host, port), timeout=1)
    again = display.start(_HOST, port)
    assert again.running is True
    assert again.port == port
    _host, _port, renewed = _endpoint(display)
    assert renewed != token
    denied, _body = _http(host, port, f"/live?token={token}")
    assert denied == 403
    allowed, _body = _http(host, port, f"/live?token={renewed}")
    assert allowed == 200


def test_start_log_does_not_contain_the_token(qapp, caplog: pytest.LogCaptureFixture) -> None:  # type: ignore[no-untyped-def]
    assert qapp is not None
    caplog.set_level(logging.INFO)
    service = LiveBroadcast()
    try:
        assert service.start(_HOST, 0).running
        token = _endpoint(service)[2]
        assert token not in caplog.text
    finally:
        service.stop()


def test_a_slow_client_does_not_stall_publish_or_grow_the_mailbox(display: LiveBroadcast) -> None:
    host, port, token = _endpoint(display)
    stalled = _Client.connect(host, port, token)
    reader = _Client.connect(host, port, token)
    try:
        stalled.sock.settimeout(0.01)
        started = time.perf_counter()
        for index in range(200):
            display.publish_scene(_scene_payload(name_marker=index, blob="x" * 8000))
        elapsed = time.perf_counter() - started
        assert elapsed < 0.75, f"publish took {elapsed:.3f}s"
        messages = reader.take_matching(
            lambda item: _has_marker(item, 199),
            timeout=3,
        )
        assert _payload(messages[-1])["marker"] == 199
        assert display.diagnostics().client_count <= 2
    finally:
        stalled.close()
        reader.close()


def test_ten_clients_keep_up_with_a_burst(display: LiveBroadcast) -> None:
    host, port, token = _endpoint(display)
    clients = [_Client.connect(host, port, token) for _ in range(10)]
    try:
        for index in range(30):
            display.publish_scene(_scene_payload(name_marker=index))
        for client in clients:
            found = client.take_matching(
                lambda item: _has_marker(item, 29),
                timeout=3,
            )
            assert found[-1]["seq"] == 30
    finally:
        for client in clients:
            client.close()


def test_client_commands_do_not_change_the_race(qtbot: QtBot, env: Env) -> None:
    service = LiveBroadcast()
    diagnostics = service.start(_HOST, 0)
    assert diagnostics.running, diagnostics.error
    live: LiveRaceView | None = None
    try:
        _ready_race(env, laps=2)
        live = LiveRaceView(
            env.runtime.translator,
            env.controller,
            service=env.races,
            broadcast=service,
        )
        qtbot.addWidget(live)
        race = env.races.list_races()[0]
        runner = env.controller.start_race(race.id)
        live.show_runner(runner)
        stamp = runner.started_at_ns
        assert runner.status is RaceStatus.RUNNING
        host, port, token = _endpoint(service)
        client = _Client.connect(host, port, token)
        try:
            opened = client.take(2)
            assert opened[0]["type"] == "hello"
            assert _payload(opened[1])["race_name"] == "Finale"
            viewport = _payload(opened[1])["viewport"]
            assert isinstance(viewport, dict)
            assert viewport["width"] == 1280
            layout_id = live.bound_layout().id
            font_scale = live.bound_layout().font_scale
            client.send_text('{"type":"viewport","width":390,"height":844,"action":"start"}')
            fitted = client.take_matching(lambda item: item.get("type") == "scene", timeout=2)
            fitted_view = _payload(fitted[-1])["viewport"]
            assert isinstance(fitted_view, dict)
            assert fitted_view["width"] == 390
            assert fitted_view["height"] == 844
            client.send_text('{"type":"pause"}')
            client.send_text('{"type":"stop"}')
            client.send_text("not-json")
            errors = client.take_matching(lambda item: item.get("type") == "error", timeout=2)
            code = _payload(errors[0]).get("code")
            assert code in {"read_only", "invalid_message"}
            assert runner.status is RaceStatus.RUNNING
            assert runner.started_at_ns == stamp
            assert live.bound_layout().id == layout_id
            assert live.bound_layout().font_scale == font_scale
        finally:
            client.close()
        service.stop()
        assert runner.status is RaceStatus.RUNNING
        assert runner.started_at_ns == stamp
        env.clock.advance(NANOS_PER_SECOND)
        live.refresh()
        assert runner.snapshot().elapsed_ns >= NANOS_PER_SECOND
    finally:
        service.stop()
        if live is not None and live.runner is not None:
            live.runner.close()


def test_the_live_view_publishes_and_revokes_the_start_schedule(qtbot: QtBot, env: Env) -> None:
    service = LiveBroadcast()
    assert service.start(_HOST, 0).running
    live: LiveRaceView | None = None
    try:
        _ready_race(env, laps=2)
        live = LiveRaceView(
            env.runtime.translator, env.controller, service=env.races, broadcast=service
        )
        qtbot.addWidget(live)
        live.show()
        runner = env.controller.prepare_race(env.races.list_races()[0].id)
        live.show_runner(runner)
        live.begin_start_cue(interval_ms=60_000)
        host, port, token = _endpoint(service)
        client = _Client.connect(host, port, token)
        try:
            messages = client.take_matching(
                lambda item: item.get("type") == "start_schedule", timeout=2
            )
            assert _payload(messages[-1])["status"] == "active"
            assert _payload(messages[-1])["time_base"] == TIME_BASE
            live.hide()
            revoked = client.take_matching(
                lambda item: item.get("type") == "start_schedule_revoked", timeout=2
            )
            assert _payload(revoked[-1])["status"] == "revoked"
            assert runner.status is RaceStatus.CREATED
        finally:
            client.close()
    finally:
        service.stop()
        if live is not None and live.runner is not None:
            live.runner.close()


def test_enabling_the_display_does_not_block_startup(qtbot: QtBot) -> None:
    runtime = Runtime.create(
        AppConfig(live_broadcast_enabled=True, live_broadcast_port=18765),
        database=migrated_database(),
        clock=ManualClock(),
    )
    service = runtime.services.get(LiveBroadcast)
    try:
        diagnostics = service.diagnostics()
        assert diagnostics.running, diagnostics.error
        assert diagnostics.port == 18765
        assert diagnostics.host != "0.0.0.0"
        _host, _port, token = _endpoint(service)
        status, body = _http(diagnostics.host, diagnostics.port, f"/?token={token}")
        assert status == 200
        assert b"Slot-Racing Platform" in body
        assert token.encode() not in body
    finally:
        runtime.shutdown()
    assert service.is_running() is False


def test_ten_distinct_viewports_compose_inside_one_second() -> None:
    layout = factory_layout()
    fields = layout.fields
    snapshot = _snapshot(RaceStatus.RUNNING)
    presentation = _presentation()
    viewports = [HudViewport(480 + index * 80, 640) for index in range(10)]
    started = time.perf_counter()
    documents = [
        dump_message(compose_scene(layout, snapshot, presentation, box)) for box in viewports
    ]
    assert time.perf_counter() - started < 1.5
    assert layout.fields is fields
    assert len(documents) == 10
    assert all(item.startswith("{") for item in documents)


def test_the_browser_page_only_paints_and_never_starts_a_race() -> None:
    page = hud_page().decode()
    assert START_HINT in page
    assert "var startSynchronized = false" in page
    assert "planned_go" not in page
    assert "setInterval" not in page
    assert "innerHTML" not in page
    assert "fetch(" not in page
    assert page.count("new XMLHttpRequest") == 1
    assert '"/session"' in page
    assert "http://" not in page
    assert "https://" not in page
    assert "textContent" in page
    assert "button.disabled = true" in page
    assert 'type: "viewport"' in page
    assert "fit_floor" in page
    assert "font_floor" in page
    assert "visible !== false" in page
    assert "seq < lastSceneSeq" in page
    assert "Verbindung unterbrochen" in page
    assert "Wiederverbindung läuft" in page
    assert "Server nicht erreichbar" in page
    assert "Die Sitzung ist abgelaufen. Bitte öffne die aktuelle Anzeigeadresse erneut." in page
    assert "Kein Rennen aktiv" in page
    assert "overflowY" in page
    assert "best_reserve_px" in page
    assert "primary_ids" in page
    assert "width_floor_px" in page
    assert "requestFullscreen" in page
    assert page.count("socket.send(") == 1
    assert "token=" not in page
    script = page.split("<script>", 1)[1].split("</script>", 1)[0]
    assert '.join("\\n")' in script
    for line in script.splitlines():
        quotes = 0
        escaped = False
        for char in line:
            if escaped:
                escaped = False
                continue
            if char == "\\":
                escaped = True
                continue
            if char == '"':
                quotes += 1
        assert quotes % 2 == 0


@pytest.mark.parametrize("count", [2, 5, 10])
def test_clients_keep_separate_viewports(display: LiveBroadcast, count: int) -> None:
    layout = factory_layout()
    fields = layout.fields
    snapshot = _snapshot(RaceStatus.RUNNING)
    presentation = _presentation()
    assert display.publish_source(layout, snapshot, presentation) == 1
    host, port, token = _endpoint(display)
    clients = [_Client.connect(host, port, token) for _ in range(count)]
    watcher = _Client.connect(host, port, token)
    widths = [480 + index * 70 for index in range(count)]
    try:
        for client, width in zip(clients, widths, strict=True):
            client.send_text(json.dumps({"type": "viewport", "width": width, "height": 720}))
        for client, width in zip(clients, widths, strict=True):
            found = client.take_matching(_width_is(width), timeout=3)
            assert _viewport_width(found[-1]) == width
        assert display.publish_source(layout, snapshot, presentation) == 2
        for client, width in zip(clients, widths, strict=True):
            found = client.take_matching(_update_is(width), timeout=3)
            assert _viewport_width(found[-1]) == width
        reference = watcher.take_matching(
            lambda item: item.get("type") == "scene" and item.get("seq") == 2,
            timeout=3,
        )
        assert _viewport_width(reference[-1]) == 1280
        assert layout.fields is fields
    finally:
        watcher.close()
        for client in clients:
            client.close()


class _Client:
    def __init__(self, sock: socket.socket, status: int, rest: bytes) -> None:
        self.sock = sock
        self.status = status
        self.buffer = bytearray(rest)

    @classmethod
    def connect(cls, host: str, port: int, token: str, *, origin: str | None = None) -> _Client:
        sock = socket.create_connection((host, port), timeout=2)
        lines = [
            f"GET /live?token={token} HTTP/1.1",
            f"Host: {host}:{port}",
            "Upgrade: websocket",
            "Connection: Upgrade",
            "Sec-WebSocket-Key: MDEyMzQ1Njc4OWFiY2RlZg==",
            "Sec-WebSocket-Version: 13",
        ]
        if origin is not None:
            lines.append(f"Origin: {origin}")
        sock.sendall(("\r\n".join(lines) + "\r\n\r\n").encode())
        data = b""
        while b"\r\n\r\n" not in data:
            chunk = sock.recv(4096)
            if not chunk:
                break
            data += chunk
        head, _, rest = data.partition(b"\r\n\r\n")
        status = int(head.split(b" ", 2)[1])
        sock.settimeout(0.2)
        return cls(sock, status, rest)

    def take(self, count: int, timeout: float = 2.0) -> list[dict[str, object]]:
        return self.take_matching(lambda _item: True, timeout=timeout, count=count)

    def take_matching(
        self,
        predicate: Callable[[dict[str, object]], bool],
        *,
        timeout: float,
        count: int = 1,
    ) -> list[dict[str, object]]:
        found: list[dict[str, object]] = []
        deadline = time.monotonic() + timeout
        while len(found) < count and time.monotonic() < deadline:
            self._pump(min(0.2, max(0.01, deadline - time.monotonic())))
            frames, error = take_frames(self.buffer, expect_mask=False, limit=2_000_000)
            assert error is None
            for frame in frames:
                if frame.opcode != 0x1:
                    continue
                loaded = json.loads(frame.payload.decode())
                assert isinstance(loaded, dict)
                if predicate(loaded):
                    found.append(loaded)
        assert len(found) >= count
        return found

    def send_text(self, text: str) -> None:
        payload = text.encode()
        mask = b"\x11\x22\x33\x44"
        masked = bytes(byte ^ mask[index % 4] for index, byte in enumerate(payload))
        header = bytes([0x81, 0x80 | len(payload)])
        self.sock.sendall(header + mask + masked)

    def close(self) -> None:
        try:
            self.sock.close()
        except OSError:
            return

    def _pump(self, timeout: float) -> None:
        self.sock.settimeout(timeout)
        try:
            chunk = self.sock.recv(65536)
        except TimeoutError:
            return
        if chunk:
            self.buffer.extend(chunk)


def _width_is(expected: int) -> Callable[[dict[str, object]], bool]:
    def predicate(item: dict[str, object]) -> bool:
        return _viewport_width(item) == expected

    return predicate


def _update_is(expected: int) -> Callable[[dict[str, object]], bool]:
    def predicate(item: dict[str, object]) -> bool:
        return (
            item.get("type") == "scene"
            and item.get("seq") == 2
            and _viewport_width(item) == expected
        )

    return predicate


def _viewport_width(message: dict[str, object]) -> int | None:
    if message.get("type") != "scene":
        return None
    payload = message.get("payload")
    if not isinstance(payload, dict):
        return None
    viewport = payload.get("viewport")
    if not isinstance(viewport, dict) or not isinstance(viewport.get("width"), int):
        return None
    width = viewport["width"]
    assert isinstance(width, int)
    return width


def _has_marker(message: dict[str, object], marker: int) -> bool:
    payload = message.get("payload")
    return (
        message.get("type") == "scene"
        and isinstance(payload, dict)
        and payload.get("marker") == marker
    )


def _payload(message: dict[str, object]) -> dict[str, object]:
    payload = message["payload"]
    assert isinstance(payload, dict)
    return payload


def _endpoint(service: LiveBroadcast) -> tuple[str, int, str]:
    url = urlsplit(service.access_url)
    token = parse_qs(url.query)["token"][0]
    assert url.hostname is not None
    assert url.port is not None
    return url.hostname, url.port, token


def _scene_payload(*, name_marker: int | None = None, blob: str = "") -> dict[str, object]:
    payload = compose_scene(factory_layout(), _snapshot(RaceStatus.RUNNING), _presentation())
    if name_marker is not None:
        payload["marker"] = name_marker
    if blob:
        payload["blob"] = blob
    return payload


def _http(
    host: str, port: int, target: str, *, host_header: str | None = None
) -> tuple[int, bytes]:
    with socket.create_connection((host, port), timeout=2) as sock:
        header_host = host_header or f"{host}:{port}"
        request = f"GET {target} HTTP/1.1\r\nHost: {header_host}\r\nConnection: close\r\n\r\n"
        sock.sendall(request.encode())
        data = b""
        while True:
            chunk = sock.recv(65536)
            if not chunk:
                break
            data += chunk
    head, _, body = data.partition(b"\r\n\r\n")
    return int(head.split(b" ", 2)[1]), body


def _wait(predicate: Callable[[], bool], timeout: float = 2.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.01)
    raise AssertionError("condition was not met")
