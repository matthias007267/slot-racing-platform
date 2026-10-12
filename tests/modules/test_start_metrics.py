"""Start-light measurements. They describe a start; they do not authorize one."""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from slot_racing.modules.races.broadcast import diagnostic_page
from slot_racing.modules.races.live_page import hud_page
from slot_racing.modules.races.start_display import (
    commit_document,
    initial_display,
    phase_document,
    reduce_start_display,
)
from slot_racing.modules.races.start_metrics import (
    COLUMNS,
    FORMAT,
    LOG_LIMIT,
    NEGATIVE_NOTE,
    TimingLog,
    TimingRow,
    browser_spread,
    builtin_scenarios,
    contains_sensitive,
    delay_note,
    go_breakdown,
    go_delay,
    lamp_deviation,
    load_session,
    metrics_source,
    report_scenario,
    session_csv,
    session_document,
    skipped_phases,
    spread,
    stalled_red_lamps,
)
from slot_racing.modules.races.time_sync import encode_timestamps

_CLIENT = "c0123456789"


def _row(**fields: str) -> TimingRow:
    return TimingRow(client_id=_CLIENT, **fields)


def test_lamp_and_go_delays_keep_their_sign() -> None:
    assert lamp_deviation(1_020_000_000, 1_000_000_000) == 20_000_000
    assert go_delay(5_000_000, 8_000_000) == -3_000_000
    parts = go_breakdown(
        engine_ns=1_000,
        published_ns=1_200,
        receive_estimated_ns=5_000,
        receive_browser_ns=100,
        paint_browser_ns=130,
        paint_estimated_ns=5_040,
    )
    assert parts.engine_to_publish_ns == 200
    assert parts.publish_to_receive_ns == 3_800
    assert parts.receive_to_paint_ns == 30
    assert parts.overall_ns == 4_040
    assert parts.suspect is False
    early = go_breakdown(
        engine_ns=9_000,
        published_ns=9_100,
        receive_estimated_ns=8_000,
        receive_browser_ns=50,
        paint_browser_ns=40,
        paint_estimated_ns=8_100,
    )
    assert early.suspect is True
    assert NEGATIVE_NOTE in delay_note(early.overall_ns, "Schätzung")


def test_spread_needs_enough_values_for_the_upper_percentiles() -> None:
    small = spread((1, 2, 3, 4, 10))
    assert small.minimum == 1
    assert small.maximum == 10
    assert small.median == 3
    assert small.span == 9
    assert small.p95 == 10
    assert small.p99 is None
    wide = spread(tuple(range(20)))
    assert wide.p95 is not None
    assert wide.p99 == 19
    assert spread(()).count == 0


def test_two_browsers_are_compared_only_on_the_estimated_pc_clock() -> None:
    first = load_session(
        session_document(
            _CLIENT,
            (
                _row(
                    sequence_id="seq",
                    event="lamp",
                    phase="3",
                    estimated_pc_ns="1000",
                    time_basis="estimated_pc",
                ),
                _row(sequence_id="seq", event="lamp", phase="3", time_basis="browser"),
            ),
        )
    )
    second = load_session(
        session_document(
            "cabcdefghij",
            (_row(sequence_id="seq", event="lamp", phase="3", estimated_pc_ns="1400"),),
        )
    )
    compared = browser_spread((first, second))
    assert len(compared) == 1
    assert compared[0].phase == "3"
    assert compared[0].spread.count == 2
    assert compared[0].spread.span == 400


def test_export_is_bounded_and_refuses_a_session_secret() -> None:
    log = TimingLog(limit=3)
    for index in range(5):
        log.add(_row(event="lamp", phase=str(index)))
    assert len(log.rows()) == 3
    assert log.rows()[0].phase == "2"
    document = session_document(_CLIENT, log.rows())
    assert document["format"] == FORMAT
    assert "token" not in json.dumps(document)
    text = session_csv(log.rows())
    assert text.splitlines()[0] == ",".join(COLUMNS)
    loaded = load_session(document)
    assert loaded.client_id == _CLIENT
    assert len(loaded.rows) == 3
    leaked = {"format": FORMAT, "client_id": _CLIENT, "events": [], "token": "secret"}
    assert contains_sensitive(leaked)
    with pytest.raises(ValueError, match="exportable"):
        load_session(leaked)
    with pytest.raises(ValueError, match="exportable"):
        session_document(_CLIENT, (_row(note="token=abc"),))


def test_skipped_red_phases_are_counted_without_moving_go() -> None:
    assert skipped_phases(-1, 1) == 0
    assert skipped_phases(-1, 4) == 3
    assert skipped_phases(1, 4) == 2
    assert skipped_phases(4, 2) == 0


def test_measurement_fields_do_not_authorize_go() -> None:
    phase = phase_document(
        sequence_id="seq",
        step_index=4,
        lit_lights=5,
        at_ns=1_000,
        observed_ns=1_050,
        lateness_ns=50,
        skipped_steps=3,
    )
    assert phase["authorizes_go"] is False
    assert phase["skipped_steps"] == 3
    encoded = encode_timestamps(phase)
    assert encoded["observed_ns"] == "1050"
    assert encoded["lateness_ns"] == "50"
    assert encoded["authorizes_go"] is False
    counting = reduce_start_display(
        initial_display(),
        {
            "protocol_version": 3,
            "type": "start_schedule",
            "seq": 1,
            "payload": {"sequence_id": "seq", "status": "active"},
        },
    )
    shown = reduce_start_display(
        counting,
        {"protocol_version": 3, "type": "start_phase", "seq": 2, "payload": encoded},
    )
    assert shown.authorizes_go is False
    assert shown.lit_lights == 5
    planned = reduce_start_display(
        shown,
        {
            "protocol_version": 3,
            "type": "scene",
            "seq": 3,
            "payload": {"planned_go_ns": "1", "lights": {"go": True}},
        },
    )
    assert planned.authorizes_go is False
    stale = reduce_start_display(
        shown,
        {
            "protocol_version": 3,
            "type": "start_committed",
            "seq": 4,
            "payload": {
                "sequence_id": "other",
                "actual_start_ns": "900",
                "published_ns": "950",
                "race_status": "running",
                "authorizes_go": True,
            },
        },
    )
    assert stale.authorizes_go is False
    released = reduce_start_display(
        shown,
        {
            "protocol_version": 3,
            "type": "start_committed",
            "seq": 4,
            "payload": encode_timestamps(
                commit_document(
                    sequence_id="seq",
                    actual_start_ns=2_000,
                    race_status="running",
                    published_ns=2_100,
                )
            ),
        },
    )
    assert released.authorizes_go is True
    lost = reduce_start_display(released, {"type": "disconnect"})
    assert lost.authorizes_go is False
    assert lost.lit_lights == 0


def test_a_stalled_gui_holds_red_lamps_and_does_not_release_go() -> None:
    stalled = stalled_red_lamps()
    assert stalled.lit == 5
    assert stalled.holding is True
    assert stalled.authorizes_go is False


def test_network_scenarios_keep_the_current_quality_rules() -> None:
    reports = {report.name: report for report in map(report_scenario, builtin_scenarios())}
    again = {report.name: report for report in map(report_scenario, builtin_scenarios())}
    assert reports == again
    assert reports["ideales_lan"].quality == "good"
    assert reports["ideales_lan"].offset_error_ns == 0
    assert reports["gutes_wlan"].quality == "good"
    assert reports["normales_wlan"].quality == "usable"
    assert reports["schlechtes_wlan"].quality == "poor"
    assert reports["schlechtes_wlan"].local_prediction is False
    assert reports["schwankendes_wlan"].quality == "good"
    assert reports["schwankendes_wlan"].sample_count == 6
    asymmetric = reports["asymmetrisch"]
    assert asymmetric.quality == "usable"
    assert asymmetric.offset_error_ns is not None
    assert asymmetric.uncertainty_ns is not None
    assert abs(asymmetric.offset_error_ns) <= asymmetric.uncertainty_ns
    assert asymmetric.offset_error_ns != 0
    assert reports["paketverlust"].sample_count == 2
    assert reports["paketverlust"].local_prediction is False
    assert reports["burst"].quality == "good"
    assert reports["browser_pause"].local_prediction is False
    assert reports["browser_pause"].sample_count == 0


def test_the_compare_script_prints_the_span_of_two_files(tmp_path: Path) -> None:
    first = tmp_path / "a.json"
    second = tmp_path / "b.json"
    first.write_text(
        json.dumps(
            session_document(
                _CLIENT,
                (_row(sequence_id="seq", event="lamp", phase="1", estimated_pc_ns="1000"),),
            )
        ),
        encoding="utf-8",
    )
    second.write_text(
        json.dumps(
            session_document(
                "cabcdefghij",
                (
                    TimingRow(
                        client_id="cabcdefghij",
                        sequence_id="seq",
                        event="lamp",
                        phase="1",
                        estimated_pc_ns="1800",
                    ),
                ),
            )
        ),
        encoding="utf-8",
    )
    completed = subprocess.run(
        [
            sys.executable,
            "scripts/compare_start_measurements.py",
            str(first),
            str(second),
        ],
        text=True,
        capture_output=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
    assert "800" in completed.stdout
    refused = subprocess.run(
        [sys.executable, "scripts/optical_start_frames.py"],
        text=True,
        capture_output=True,
        check=False,
    )
    assert refused.returncode != 0


def test_the_browser_log_exports_only_its_own_rows() -> None:
    node = shutil.which("node")
    if node is None:
        pytest.skip("node is not installed")
    script = metrics_source()
    start = script.index("/*__METRICS_START__*/")
    end = script.index("/*__METRICS_END__*/")
    completed = subprocess.run(
        [node, "-e", script[start:end] + "\n" + _NODE],
        text=True,
        capture_output=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
    result = json.loads(completed.stdout)
    assert result["header"] == list(COLUMNS)
    assert result["deviation"] == "1000"
    assert result["negative"] is True
    assert "Negativer Wert" in result["note"]
    assert result["count"] == LOG_LIMIT
    assert result["client"].startswith("c")
    assert len(result["client"]) == 11
    assert "token" not in result["document"]
    hud = hud_page().decode()
    diagnosed = diagnostic_page().decode()
    assert "/*__METRICS_START__*/" in hud
    assert "/*__METRICS_START__*/" in diagnosed
    assert "setInterval" not in hud
    assert "setInterval" not in diagnosed
    for page in (hud, diagnosed):
        source = page.split("<script>", 1)[1].split("</script>", 1)[0]
        checked = subprocess.run([node, "--check", "-"], input=source, text=True, check=False)
        assert checked.returncode == 0, checked.stderr


_NODE = """
measureEnabled = true;
recordMeasure({
  sequence_id: "seq",
  event: "lamp",
  phase: "2",
  time_basis: "estimated_pc",
  browser_ns: "1000",
  estimated_pc_ns: "5000",
  quality: "good",
  uncertainty_ns: "8000000",
  note: "JavaScript-Zustandswechsel, nicht der sichtbare Pixel"
});
attachObserved("seq", 2, "4000", "3000");
recordMeasure({
  sequence_id: "seq",
  event: "lamp",
  phase: "9",
  estimated_pc_ns: "1000",
  note: "JavaScript-Zustandswechsel, nicht der sichtbare Pixel"
});
attachObserved("seq", "9", "4000", "3000");
const negative = measureLog[measureLog.length - 1];
const deviation = measureLog[0].deviation_ns;
const note = negative.note;
for (let index = 0; index < MEASURE_LIMIT; index += 1) {
  recordMeasure({event: "frame", phase: "1", browser_ns: String(index)});
}
const output = {
  header: measureCsv().split("\\n")[0].split(","),
  deviation: deviation,
  negative: negative.deviation_ns === "-3000",
  note: note,
  count: measureLog.length,
  client: ensureClient(),
  document: JSON.stringify(measureDocument())
};
process.stdout.write(JSON.stringify(output));
"""
