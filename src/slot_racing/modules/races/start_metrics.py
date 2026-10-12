"""Measurements for the start lights. They never start a race.

The PC stamps ``time.perf_counter_ns`` through the race clock. A browser stamps
``performance.now()``. A lamp time on the PC and a lamp time in JavaScript are
different facts. An estimated PC time is a third fact. None of them is the
moment a pixel became visible.

GO stays ``start_committed`` after ``RaceEngine.start``. A negative delay is
kept and marked. It is not evidence that the light was early on the display.

Each browser exports only its own session. Comparing several browsers is an
offline step on those files. The live protocol does not gain a write channel.
"""

from __future__ import annotations

import csv
import io
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from slot_racing.modules.races.time_sync import (
    EXPIRE_NS,
    RedPhase,
    TimeSample,
    assess_sync,
    ntp_sample,
    predict_red_lamps,
    shown_lamps,
    toward_zero,
)

FORMAT = "slot-racing-start-measurement-1"
LOG_LIMIT = 400
TRUE_OFFSET_NS = 5_000_000_000
SERVER_DWELL_NS = 50_000

COLUMNS = (
    "client_id",
    "sequence_id",
    "event",
    "phase",
    "time_basis",
    "browser_ns",
    "estimated_pc_ns",
    "planned_pc_ns",
    "pc_observed_ns",
    "quality",
    "uncertainty_ns",
    "deviation_ns",
    "delay_ns",
    "note",
)

NEGATIVE_NOTE = (
    "Negativer Wert: möglicher Mess- oder Synchronisationsfehler, "
    "kein Nachweis eines vorzeitigen sichtbaren GO"
)
PIXEL_NOTE = "JavaScript-Zustandswechsel, nicht der sichtbare Pixel"
FRAME_NOTE = "Nächster Frame-Callback, nicht der sichtbare Pixel"

_CLIENT = re.compile(r"c[a-z0-9]{10}")
_FORBIDDEN = frozenset({"token", "authorization", "cookie", "password", "session"})


@dataclass(frozen=True, slots=True)
class TimingRow:
    """One event from a single browser. Empty strings mean the value was not measured."""

    client_id: str
    sequence_id: str = ""
    event: str = ""
    phase: str = ""
    time_basis: str = ""
    browser_ns: str = ""
    estimated_pc_ns: str = ""
    planned_pc_ns: str = ""
    pc_observed_ns: str = ""
    quality: str = ""
    uncertainty_ns: str = ""
    deviation_ns: str = ""
    delay_ns: str = ""
    note: str = ""

    def as_dict(self) -> dict[str, str]:
        return {key: getattr(self, key) for key in COLUMNS}


@dataclass(frozen=True, slots=True)
class SessionLog:
    """One anonymous browser session. It does not contain other clients."""

    client_id: str
    rows: tuple[TimingRow, ...]


@dataclass(frozen=True, slots=True)
class Spread:
    """Order statistics. P95 needs 5 values and P99 needs 20. Otherwise they stay empty."""

    count: int
    minimum: int | None
    maximum: int | None
    median: int | None
    p95: int | None
    p99: int | None
    span: int | None


@dataclass(frozen=True, slots=True)
class PhaseSpread:
    """How far apart several browsers were for one lamp, on the estimated PC clock."""

    sequence_id: str
    phase: str
    spread: Spread


@dataclass(frozen=True, slots=True)
class GoBreakdown:
    """Three measured or estimated pieces of the GO delay, plus the overall estimate."""

    engine_to_publish_ns: int | None
    publish_to_receive_ns: int | None
    receive_to_paint_ns: int | None
    overall_ns: int | None
    suspect: bool


@dataclass(frozen=True, slots=True)
class ProbeStep:
    """One simulated probe. Times are one-way delays, not a claim about a real network."""

    forward_ns: int
    reverse_ns: int
    dropped: bool = False


@dataclass(frozen=True, slots=True)
class LinkScenario:
    """A fixed series of probes. The same series always produces the same estimate."""

    name: str
    steps: tuple[ProbeStep, ...]
    pause_ns: int = 0


@dataclass(frozen=True, slots=True)
class ScenarioReport:
    """What the current sync rules do with one simulated series. Thresholds are unchanged."""

    name: str
    quality: str
    local_prediction: bool
    sample_count: int
    uncertainty_ns: int | None
    offset_error_ns: int | None


@dataclass(frozen=True, slots=True)
class StalledLamps:
    """Red lamps while the GUI has not started the engine. GO stays off."""

    lit: int
    holding: bool
    authorizes_go: bool


def lamp_deviation(estimated_pc_ns: int, observed_pc_ns: int) -> int:
    """Browser lamp on the estimated PC clock, minus the PC's published lamp time."""
    return estimated_pc_ns - observed_pc_ns


def go_delay(estimated_go_ns: int, engine_start_ns: int) -> int:
    """Estimated browser GO minus the engine stamp. Negative is a suspect estimate."""
    return estimated_go_ns - engine_start_ns


def go_breakdown(
    *,
    engine_ns: int,
    published_ns: int | None,
    receive_estimated_ns: int | None,
    receive_browser_ns: int | None,
    paint_browser_ns: int | None,
    paint_estimated_ns: int | None,
) -> GoBreakdown:
    """Split one GO. Browser-clock differences are measurements. PC conversions are estimates."""
    engine_to_publish = None if published_ns is None else published_ns - engine_ns
    publish_to_receive = None
    if published_ns is not None and receive_estimated_ns is not None:
        publish_to_receive = receive_estimated_ns - published_ns
    receive_to_paint = None
    if receive_browser_ns is not None and paint_browser_ns is not None:
        receive_to_paint = paint_browser_ns - receive_browser_ns
    overall = None if paint_estimated_ns is None else paint_estimated_ns - engine_ns
    pieces = (engine_to_publish, publish_to_receive, receive_to_paint, overall)
    return GoBreakdown(
        engine_to_publish_ns=engine_to_publish,
        publish_to_receive_ns=publish_to_receive,
        receive_to_paint_ns=receive_to_paint,
        overall_ns=overall,
        suspect=any(piece is not None and piece < 0 for piece in pieces),
    )


def delay_note(delay_ns: int | None, base: str) -> str:
    """Keep a negative value and say what it does not prove."""
    if delay_ns is None or delay_ns >= 0 or NEGATIVE_NOTE in base:
        return base
    if not base:
        return NEGATIVE_NOTE
    return f"{base} | {NEGATIVE_NOTE}"


def skipped_phases(previous: int, current: int) -> int:
    """Red phases that were not shown. ``previous`` below zero means nothing was shown yet."""
    if current <= 0:
        return 0
    if previous < 0:
        return current - 1
    if current <= previous:
        return 0
    return current - previous - 1


def spread(values: Sequence[int]) -> Spread:
    """Min, max, median, and the gap from earliest to latest. P95 and P99 need enough values."""
    if not values:
        return Spread(0, None, None, None, None, None, None)
    ordered = tuple(sorted(values))
    count = len(ordered)
    return Spread(
        count=count,
        minimum=ordered[0],
        maximum=ordered[-1],
        median=_median(ordered),
        p95=_percentile(ordered, 95) if count >= 5 else None,
        p99=_percentile(ordered, 99) if count >= 20 else None,
        span=ordered[-1] - ordered[0],
    )


def browser_spread(logs: Sequence[SessionLog]) -> tuple[PhaseSpread, ...]:
    """Compare lamp estimates that already share the PC clock. Missing estimates are skipped."""
    buckets: dict[tuple[str, str], list[int]] = {}
    for log in logs:
        for row in log.rows:
            if row.event != "lamp" or not row.estimated_pc_ns:
                continue
            key = (row.sequence_id, row.phase)
            buckets.setdefault(key, []).append(int(row.estimated_pc_ns))
    found: list[PhaseSpread] = []
    for sequence_id, phase in sorted(buckets):
        found.append(PhaseSpread(sequence_id, phase, spread(buckets[(sequence_id, phase)])))
    return tuple(found)


class TimingLog:
    """A bounded list. The oldest rows are dropped. Nothing is written to disk."""

    def __init__(self, limit: int = LOG_LIMIT) -> None:
        if limit < 1:
            raise ValueError("limit")
        self.limit = limit
        self._rows: list[TimingRow] = []

    def add(self, row: TimingRow) -> None:
        self._rows.append(row)
        extra = len(self._rows) - self.limit
        if extra > 0:
            del self._rows[:extra]

    def rows(self) -> tuple[TimingRow, ...]:
        return tuple(self._rows)


def session_document(client_id: str, rows: Sequence[TimingRow]) -> dict[str, object]:
    """JSON-ready object for one browser. A sensitive key is refused."""
    kept = tuple(rows[-LOG_LIMIT:])
    document: dict[str, object] = {
        "format": FORMAT,
        "client_id": client_id,
        "events": [row.as_dict() for row in kept],
    }
    if contains_sensitive(document) or _CLIENT.fullmatch(client_id) is None:
        raise ValueError("session document is not exportable")
    return document


def session_csv(rows: Sequence[TimingRow]) -> str:
    """One header and one row per event. Large timestamps stay decimal text."""
    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, fieldnames=COLUMNS, lineterminator="\n")
    writer.writeheader()
    for row in rows[-LOG_LIMIT:]:
        writer.writerow(row.as_dict())
    return buffer.getvalue()


def load_session(document: Mapping[str, object]) -> SessionLog:
    """Read one export. Another client's numbers are not filled in."""
    if contains_sensitive(document):
        raise ValueError("session document is not exportable")
    if document.get("format") != FORMAT:
        raise ValueError("unknown measurement format")
    client_id = document.get("client_id")
    if not isinstance(client_id, str) or _CLIENT.fullmatch(client_id) is None:
        raise ValueError("client id")
    events = document.get("events")
    if not isinstance(events, list) or len(events) > LOG_LIMIT:
        raise ValueError("events")
    rows: list[TimingRow] = []
    for item in events:
        if not isinstance(item, Mapping):
            raise ValueError("event")
        rows.append(_row_from_mapping(item, client_id))
    return SessionLog(client_id, tuple(rows))


def contains_sensitive(value: object) -> bool:
    """True when a measurement document carries a session secret or a credential."""
    if isinstance(value, Mapping):
        for key, item in value.items():
            if str(key).lower() in _FORBIDDEN or contains_sensitive(item):
                return True
        return False
    if isinstance(value, list):
        return any(contains_sensitive(item) for item in value)
    return isinstance(value, str) and "token=" in value.lower()


def simulate_samples(scenario: LinkScenario) -> tuple[TimeSample, ...]:
    """NTP samples for a known offset. Dropped probes leave a gap and no reply."""
    samples: list[TimeSample] = []
    t1 = 0
    for index, step in enumerate(scenario.steps):
        if step.dropped:
            t1 += 40_000_000
            continue
        t2 = t1 + TRUE_OFFSET_NS + step.forward_ns
        t3 = t2 + SERVER_DWELL_NS
        t4 = t3 - TRUE_OFFSET_NS + step.reverse_ns
        measured = ntp_sample(t1, t2, t3, t4)
        t1 = t4 + 40_000_000
        if measured is None:
            continue
        rtt, offset = measured
        samples.append(TimeSample(str(index), t4, rtt, offset))
    return tuple(samples)


def report_scenario(scenario: LinkScenario) -> ScenarioReport:
    """Classify one fixed series with the current quality rules."""
    samples = simulate_samples(scenario)
    now = 0 if not samples else samples[-1].t4_ns + scenario.pause_ns
    estimate = assess_sync(samples, now)
    error = None if estimate.offset_ns is None else estimate.offset_ns - TRUE_OFFSET_NS
    return ScenarioReport(
        name=scenario.name,
        quality=estimate.quality,
        local_prediction=estimate.local_prediction,
        sample_count=estimate.sample_count,
        uncertainty_ns=estimate.uncertainty_ns,
        offset_error_ns=error,
    )


def stalled_red_lamps() -> StalledLamps:
    """The plan has reached the last red lamp. The engine has not started, so GO stays off."""
    phases = tuple(
        RedPhase(at_ns=index * 1_000_000_000, lit_lights=lit, step_index=index)
        for index, lit in enumerate(range(1, 6))
    )
    planned = 5_000_000_000
    prediction = predict_red_lamps(phases, planned_go_ns=planned, now_ns=planned, active=True)
    if prediction is None:
        return StalledLamps(0, False, False)
    lit = shown_lamps(
        counting=True,
        authorizes_go=False,
        server_lit=1,
        predicted=prediction.lit_lights,
        peak=1,
        local=True,
    )
    return StalledLamps(lit, prediction.holding, False)


def builtin_scenarios() -> tuple[LinkScenario, ...]:
    """Simulation settings. They are not measurements of a building's wireless network."""
    return (
        _symmetric("ideales_lan", (1, 2, 3)),
        _symmetric("gutes_wlan", (5, 8, 12, 15)),
        _symmetric("normales_wlan", (15, 22, 30, 40)),
        _symmetric("schlechtes_wlan", (50, 80, 120, 150)),
        _symmetric("schwankendes_wlan", (5, 40, 200, 12, 90, 7)),
        LinkScenario(
            "asymmetrisch",
            tuple(ProbeStep(1_000_000, 30_000_000) for _ in range(4)),
        ),
        LinkScenario(
            "paketverlust",
            (
                ProbeStep(2_000_000, 2_000_000),
                ProbeStep(2_000_000, 2_000_000, dropped=True),
                ProbeStep(3_000_000, 3_000_000),
                ProbeStep(3_000_000, 3_000_000, dropped=True),
                ProbeStep(4_000_000, 4_000_000, dropped=True),
                ProbeStep(4_000_000, 4_000_000, dropped=True),
            ),
        ),
        LinkScenario(
            "burst",
            tuple(ProbeStep(4_000_000, 4_000_000) for _ in range(3))
            + tuple(ProbeStep(90_000_000, 90_000_000) for _ in range(3)),
        ),
        LinkScenario(
            "browser_pause",
            tuple(ProbeStep(1_000_000, 1_000_000) for _ in range(4)),
            pause_ns=EXPIRE_NS + 1,
        ),
    )


def metrics_source() -> str:
    """Browser log and export. The HUD and the diagnostic page both embed this text."""
    return _METRICS_JS


def _symmetric(name: str, rtts_ms: tuple[int, ...]) -> LinkScenario:
    steps: list[ProbeStep] = []
    for rtt_ms in rtts_ms:
        total = rtt_ms * 1_000_000
        forward = total // 2
        steps.append(ProbeStep(forward, total - forward))
    return LinkScenario(name, tuple(steps))


def _median(ordered: Sequence[int]) -> int:
    count = len(ordered)
    mid = count // 2
    if count % 2 == 1:
        return ordered[mid]
    return toward_zero(ordered[mid - 1] + ordered[mid])


def _percentile(ordered: Sequence[int], percent: int) -> int:
    count = len(ordered)
    index = (percent * count + 99) // 100 - 1
    if index < 0:
        index = 0
    if index >= count:
        index = count - 1
    return ordered[index]


def _row_from_mapping(raw: Mapping[str, object], client_id: str) -> TimingRow:
    data: dict[str, str] = {}
    for key in COLUMNS:
        item: object = client_id if key == "client_id" else raw.get(key, "")
        if item is None:
            item = ""
        if not isinstance(item, str):
            raise ValueError(key)
        data[key] = item
    return TimingRow(**data)


_METRICS_JS = """\
/*__METRICS_START__*/
var MEASURE_LIMIT = 400;
var MEASURE_COLUMNS = [
  "client_id", "sequence_id", "event", "phase", "time_basis",
  "browser_ns", "estimated_pc_ns", "planned_pc_ns", "pc_observed_ns",
  "quality", "uncertainty_ns", "deviation_ns", "delay_ns", "note"
];
var NEGATIVE_NOTE = "Negativer Wert: möglicher Mess- oder Synchronisationsfehler, " +
  "kein Nachweis eines vorzeitigen sichtbaren GO";
var measureEnabled = false;
var measureLog = [];
var measureClient = "";
var measureDisconnects = 0;
var measureFallbacks = 0;
var measureDiscarded = 0;
var measureMode = null;
var measureSequences = {};
function ensureClient() {
  if (measureClient) return measureClient;
  var alphabet = "abcdefghijklmnopqrstuvwxyz0123456789";
  var id = "c";
  var index;
  for (index = 0; index < 10; index += 1) {
    id += alphabet.charAt(Math.floor(Math.random() * alphabet.length));
  }
  measureClient = id;
  return id;
}
function measureRow(fields) {
  var row = {
    client_id: ensureClient(), sequence_id: "", event: "", phase: "",
    time_basis: "", browser_ns: "", estimated_pc_ns: "", planned_pc_ns: "",
    pc_observed_ns: "", quality: "", uncertainty_ns: "", deviation_ns: "",
    delay_ns: "", note: ""
  };
  var key;
  for (key in fields) {
    if (!Object.prototype.hasOwnProperty.call(row, key)) continue;
    if (fields[key] === null || fields[key] === undefined) continue;
    row[key] = String(fields[key]);
  }
  row.client_id = ensureClient();
  return row;
}
function recordMeasure(fields) {
  if (!measureEnabled) return;
  var row = measureRow(fields);
  if (row.sequence_id && (row.event === "lamp" || row.event === "go")) {
    measureSequences[row.sequence_id] = true;
  }
  measureLog.push(row);
  if (measureLog.length > MEASURE_LIMIT) {
    measureLog = measureLog.slice(measureLog.length - MEASURE_LIMIT);
  }
}
function negativeNote(note) {
  if (!note) return NEGATIVE_NOTE;
  if (note.indexOf("Negativer Wert") >= 0) return note;
  return note + " | " + NEGATIVE_NOTE;
}
function attachObserved(sequenceId, phase, observed, planned) {
  if (!measureEnabled || sequenceId === null || sequenceId === undefined) return;
  var wanted = String(phase);
  var index;
  for (index = measureLog.length - 1; index >= 0; index -= 1) {
    var row = measureLog[index];
    if (row.event !== "lamp" || row.sequence_id !== sequenceId) continue;
    if (row.phase !== wanted) continue;
    if (observed !== null && observed !== undefined && observed !== "") {
      row.pc_observed_ns = String(observed);
    }
    if (planned !== null && planned !== undefined && planned !== "") {
      row.planned_pc_ns = String(planned);
    }
    if (!row.estimated_pc_ns || !row.pc_observed_ns) return;
    try {
      var delta = BigInt(row.estimated_pc_ns) - BigInt(row.pc_observed_ns);
      row.deviation_ns = delta.toString();
      if (delta < 0n) row.note = negativeNote(row.note);
    } catch (error) {
      row.deviation_ns = "";
    }
    return;
  }
}
function noteDisconnect() {
  if (!measureEnabled) return;
  measureDisconnects += 1;
  measureMode = false;
  recordMeasure({
    event: "disconnect",
    time_basis: "browser",
    note: "Verbindung getrennt, keine Startfreigabe"
  });
}
function noteResync() {
  if (!measureEnabled) return;
  recordMeasure({
    event: "resync",
    time_basis: "browser",
    note: "Neue Verbindung, Proben werden neu gesammelt"
  });
}
function noteDiscarded() {
  if (!measureEnabled) return;
  measureDiscarded += 1;
}
function noteMode(local) {
  if (!measureEnabled) return;
  var next = !!local;
  if (measureMode === null) {
    measureMode = next;
    return;
  }
  if (measureMode === next) return;
  var leaving = measureMode === true && !next;
  measureMode = next;
  if (!leaving) return;
  measureFallbacks += 1;
  recordMeasure({
    event: "fallback",
    time_basis: "browser",
    note: "servergesteuerte Lampen"
  });
}
function noteCommitArrival(fields) {
  if (!measureEnabled) return;
  recordMeasure({
    sequence_id: fields.sequence || "",
    event: "commit_received",
    phase: "go",
    time_basis: "estimated_pc",
    browser_ns: fields.receiveBrowser || "",
    estimated_pc_ns: fields.receiveEstimated || "",
    quality: fields.quality || "",
    uncertainty_ns: fields.uncertainty || "",
    note: "Empfang von start_committed, nicht die Darstellung"
  });
  if (fields.actual && fields.published) {
    var publishDelay = "";
    try {
      publishDelay = (BigInt(fields.published) - BigInt(fields.actual)).toString();
    } catch (error) { publishDelay = ""; }
    recordMeasure({
      sequence_id: fields.sequence || "",
      event: "engine_to_publish",
      phase: "go",
      time_basis: "pc",
      delay_ns: publishDelay,
      note: delayNote(publishDelay, "Messwert auf der PC-Uhr")
    });
  }
  if (fields.published && fields.receiveEstimated) {
    var receiveDelay = "";
    try {
      receiveDelay = (BigInt(fields.receiveEstimated) - BigInt(fields.published)).toString();
    } catch (error) { receiveDelay = ""; }
    recordMeasure({
      sequence_id: fields.sequence || "",
      event: "publish_to_receive",
      phase: "go",
      time_basis: "estimated_pc",
      delay_ns: receiveDelay,
      uncertainty_ns: fields.uncertainty || "",
      note: delayNote(receiveDelay, "Schätzung, Unsicherheit der Uhr gilt mit")
    });
  }
}
function noteGoPaint(fields) {
  if (!measureEnabled) return;
  var paintDelay = "";
  if (fields.receiveBrowser && fields.paintBrowser) {
    try {
      paintDelay = (BigInt(fields.paintBrowser) - BigInt(fields.receiveBrowser)).toString();
    } catch (error) { paintDelay = ""; }
    recordMeasure({
      sequence_id: fields.sequence || "",
      event: "receive_to_paint",
      phase: "go",
      time_basis: "browser",
      browser_ns: fields.paintBrowser,
      delay_ns: paintDelay,
      note: delayNote(paintDelay, "Messwert auf der Browseruhr, nicht der Pixel")
    });
  }
  var overall = "";
  if (fields.paintEstimated && fields.actual) {
    try {
      overall = (BigInt(fields.paintEstimated) - BigInt(fields.actual)).toString();
    } catch (error) { overall = ""; }
  }
  recordMeasure({
    sequence_id: fields.sequence || "",
    event: "go",
    phase: "go",
    time_basis: "estimated_pc",
    browser_ns: fields.paintBrowser || "",
    estimated_pc_ns: fields.paintEstimated || "",
    quality: fields.quality || "",
    uncertainty_ns: fields.uncertainty || "",
    delay_ns: overall,
    note: delayNote(overall, "Schätzung der JavaScript-Darstellung, nicht des Pixels")
  });
}
function delayNote(delay, base) {
  if (!delay) return base;
  try {
    if (BigInt(delay) < 0n) return negativeNote(base);
  } catch (error) { return base; }
  return base;
}
function lastDelay(eventName) {
  var index;
  for (index = measureLog.length - 1; index >= 0; index -= 1) {
    if (measureLog[index].event === eventName && measureLog[index].delay_ns) {
      return measureLog[index].delay_ns;
    }
  }
  return "";
}
function measureSummary() {
  var lastDev = "";
  var sum = 0n;
  var devCount = 0;
  var phase = "";
  var index;
  for (index = 0; index < measureLog.length; index += 1) {
    var row = measureLog[index];
    if (row.event === "lamp") phase = row.phase;
    if (row.event === "go") phase = "GO";
    if (row.event === "lamp" && row.deviation_ns) {
      lastDev = row.deviation_ns;
      try {
        sum += BigInt(row.deviation_ns);
        devCount += 1;
      } catch (error) { lastDev = ""; }
    }
  }
  var average = "";
  if (devCount) average = (sum / BigInt(devCount)).toString();
  var sequences = 0;
  var name;
  for (name in measureSequences) {
    if (Object.prototype.hasOwnProperty.call(measureSequences, name)) sequences += 1;
  }
  return {
    lastDeviation: lastDev,
    averageDeviation: average,
    lastGo: lastDelay("go"),
    engineToPublish: lastDelay("engine_to_publish"),
    publishToReceive: lastDelay("publish_to_receive"),
    receiveToPaint: lastDelay("receive_to_paint"),
    sequences: sequences,
    disconnects: measureDisconnects,
    fallbacks: measureFallbacks,
    discarded: measureDiscarded,
    phase: phase
  };
}
function csvCell(value) {
  var text = value == null ? "" : String(value);
  if (/[",\\n]/.test(text)) return '"' + text.replace(/"/g, '""') + '"';
  return text;
}
function measureCsv() {
  var lines = [MEASURE_COLUMNS.join(",")];
  var index;
  var column;
  for (index = 0; index < measureLog.length; index += 1) {
    var cells = [];
    for (column = 0; column < MEASURE_COLUMNS.length; column += 1) {
      cells.push(csvCell(measureLog[index][MEASURE_COLUMNS[column]]));
    }
    lines.push(cells.join(","));
  }
  return lines.join("\\n") + "\\n";
}
function measureDocument() {
  return {format: "slot-racing-start-measurement-1", client_id: ensureClient(), events: measureLog};
}
function downloadMeasure(kind) {
  var csv = kind === "csv";
  var text = csv ? measureCsv() : JSON.stringify(measureDocument());
  var blob = new Blob([text], {type: csv ? "text/csv" : "application/json"});
  var link = document.createElement("a");
  var address = URL.createObjectURL(blob);
  link.href = address;
  link.download = csv ? "start-messung.csv" : "start-messung.json";
  document.body.appendChild(link);
  link.click();
  document.body.removeChild(link);
  setTimeout(function () { URL.revokeObjectURL(address); }, 1000);
}
function delayText(value) {
  if (value === null || value === undefined || value === "") return "—";
  var ns;
  try { ns = BigInt(value); } catch (error) { return "—"; }
  var negative = ns < 0n;
  if (negative) ns = -ns;
  var ms = ns / 1000000n;
  var tenth = (ns % 1000000n) / 100000n;
  var text = ms.toString() + "." + tenth.toString() + " ms";
  return negative ? "-" + text : text;
}
/*__METRICS_END__*/
"""
