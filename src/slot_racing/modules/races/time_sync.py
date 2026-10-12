"""Clock estimate for browser start lamps. It never starts a race.

The race PC clock is ``time.perf_counter_ns``. Each browser uses
``performance.now()`` and sends a ``time_probe``. The server thread answers
with a ``time_sample`` immediately. Four timestamps give one round-trip and
one offset:

``RTT = (t4 - t1) - (t3 - t2)``

``offset = ((t2 - t1) + (t3 - t4)) / 2``

``offset`` maps browser time onto host monotonic time
(``host ≈ browser + offset``). Division truncates toward zero, matching
JavaScript ``BigInt``.

The uncertainty is the median RTT of the preferred samples, halved, widened
by how far those offsets disagree. It assumes a symmetric path. It is an
estimate, not a hard bound on asymmetric delay, event-loop delay, or the
error of a clock step outside the four timestamps.

Quality starts from that estimate. At least three fresh samples are required.
Up to 8 ms is good, up to 20 ms is usable, and anything wider or thinner is
poor. A previous good or usable result stays usable while the estimate is
still within 25 ms and at least two fresh samples remain, so a single noisy
probe does not flip the lamps back to the server. Samples older than 45 s
are too old to predict from. Samples older than 60 s are dropped. A jump of
more than 50 ms is accepted only when the selected samples agree within 20 ms.

Local prediction may light red counting phases from the current schedule.
At ``planned_go_ns`` it keeps the last red phase. It has no GO result.
``start_committed`` remains the only start authorization.

Absolute host nanoseconds travel as decimal strings. JavaScript numbers are
exact only to ``2**53 - 1``, and a long-running PC clock passes that.
Browser probe times travel as decimal microseconds from ``performance.now()``,
which stays inside that range for the life of a tab, and are scaled to
nanoseconds before the formulas run.

Nothing here waits on a socket, and nothing here is a real-time guarantee.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

QUALITY_GOOD = "good"
QUALITY_USABLE = "usable"
QUALITY_POOR = "poor"

GOOD_UNCERTAINTY_NS = 8_000_000
USABLE_UNCERTAINTY_NS = 20_000_000
HOLD_UNCERTAINTY_NS = 25_000_000
MIN_SAMPLES = 3
HOLD_MIN_SAMPLES = 2
STALE_NS = 45_000_000_000
EXPIRE_NS = 60_000_000_000
OFFSET_JUMP_NS = 50_000_000
MAX_RTT_NS = 500_000_000
SELECT_LIMIT = 8
SAMPLE_KEEP = 16
JUMP_GRACE_NS = 1_000_000
PROBE_LIMIT = 10
PROBE_WINDOW_NS = 1_000_000_000

_LOCAL = frozenset({QUALITY_GOOD, QUALITY_USABLE})
_NS_TEXT = re.compile(r"-?\d+")
_TIMESTAMP_KEYS = frozenset(
    {
        "planned_go_ns",
        "finished_ns",
        "reference_ns",
        "step_interval_ns",
        "at_ns",
        "actual_start_ns",
    }
)


@dataclass(frozen=True, slots=True)
class TimeSample:
    """One accepted probe. Times are nanoseconds on the clock named by the field."""

    probe_id: str
    t4_ns: int
    rtt_ns: int
    offset_ns: int


@dataclass(frozen=True, slots=True)
class SyncEstimate:
    """A labelled estimate. ``local_prediction`` is red lamps only, never GO."""

    quality: str
    offset_ns: int | None
    uncertainty_ns: int | None
    rtt_ns: int | None
    best_rtt_ns: int | None
    sample_count: int
    age_ns: int | None
    local_prediction: bool

    def to_document(self) -> dict[str, object]:
        return {
            "quality": self.quality,
            "offset_ns": _text(self.offset_ns),
            "uncertainty_ns": _text(self.uncertainty_ns),
            "rtt_ns": _text(self.rtt_ns),
            "best_rtt_ns": _text(self.best_rtt_ns),
            "sample_count": self.sample_count,
            "age_ns": _text(self.age_ns),
            "local_prediction": self.local_prediction,
        }


@dataclass(frozen=True, slots=True)
class RedPhase:
    """One counting step. The start signal is not a red phase."""

    at_ns: int
    lit_lights: int
    step_index: int


@dataclass(frozen=True, slots=True)
class RedPrediction:
    """How many red lamps the estimate would show. Holding is not GO."""

    lit_lights: int
    step_index: int
    holding: bool


@dataclass(frozen=True, slots=True)
class PrunedSamples:
    """Samples still on one clock timeline. A backward jump drops all of them."""

    samples: tuple[TimeSample, ...]
    jumped: bool


def toward_zero(value: int) -> int:
    """Half of ``value``, truncated toward zero. JavaScript ``BigInt`` does this."""
    return int(value / 2)


def parse_timestamp(value: object) -> int | None:
    """Host nanoseconds from an int or a decimal string. Floats and bools are refused."""
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str) and _NS_TEXT.fullmatch(value):
        return int(value)
    return None


def format_timestamp(value: int) -> str:
    """Decimal form that survives a JSON number parser in a browser."""
    if isinstance(value, bool):
        raise ValueError("a timestamp is not a bool")
    return str(value)


def encode_timestamps(document: Mapping[str, object]) -> dict[str, object]:
    """Copy a schedule, phase, or commit with host nanoseconds written as strings."""
    encoded = _encode(document)
    if not isinstance(encoded, dict):
        raise ValueError("document must be an object")
    return encoded


def ntp_sample(t1_ns: int, t2_ns: int, t3_ns: int, t4_ns: int) -> tuple[int, int] | None:
    """Return ``(rtt, offset)`` in nanoseconds, or nothing if the RTT is unusable."""
    rtt = (t4_ns - t1_ns) - (t3_ns - t2_ns)
    if rtt < 0 or rtt > MAX_RTT_NS:
        return None
    offset = toward_zero((t2_ns - t1_ns) + (t3_ns - t4_ns))
    return rtt, offset


def allow_probe(marks: list[int], now_ns: int) -> bool:
    """True when this client may take another probe. Extra probes are ignored."""
    fresh = [mark for mark in marks if 0 <= now_ns - mark < PROBE_WINDOW_NS]
    marks[:] = fresh
    if len(marks) >= PROBE_LIMIT:
        return False
    marks.append(now_ns)
    return True


def accept_sample(samples: list[TimeSample], pending: set[str], sample: TimeSample) -> bool:
    """Keep one reply for a probe this client still expects. Duplicates are dropped."""
    if sample.probe_id not in pending:
        return False
    pending.remove(sample.probe_id)
    if any(item.probe_id == sample.probe_id for item in samples):
        return False
    samples.append(sample)
    if len(samples) > SAMPLE_KEEP:
        del samples[:-SAMPLE_KEEP]
    return True


def prune_samples(samples: Sequence[TimeSample], now_ns: int) -> PrunedSamples:
    """Drop a timeline that jumped backward. A paused browser clock is not trusted."""
    if any(sample.t4_ns > now_ns + JUMP_GRACE_NS for sample in samples):
        return PrunedSamples((), True)
    kept = tuple(sample for sample in samples if sample.t4_ns <= now_ns)
    return PrunedSamples(kept, False)


def assess_sync(
    samples: Sequence[TimeSample],
    now_ns: int,
    *,
    previous_quality: str = QUALITY_POOR,
    previous_offset_ns: int | None = None,
) -> SyncEstimate:
    """Prefer low-RTT samples and label the estimate. Poor means server-driven lamps."""
    fresh = [sample for sample in samples if 0 <= now_ns - sample.t4_ns <= EXPIRE_NS]
    if not fresh:
        return _empty()
    newest = max(fresh, key=lambda sample: sample.t4_ns)
    age = now_ns - newest.t4_ns
    ordered = sorted(fresh, key=lambda sample: (sample.rtt_ns, sample.t4_ns))
    pool = ordered[:SELECT_LIMIT]
    chosen = pool[: max(1, (len(pool) + 1) // 2)]
    offsets = [sample.offset_ns for sample in chosen]
    rtts = [sample.rtt_ns for sample in chosen]
    offset = _median(offsets)
    spread = max(offsets) - min(offsets)
    uncertainty = max(toward_zero(_median(rtts)), spread)
    best = min(sample.rtt_ns for sample in fresh)
    count = len(fresh)
    stale = age > STALE_NS
    if stale or count < MIN_SAMPLES:
        raw = QUALITY_POOR
    elif uncertainty <= GOOD_UNCERTAINTY_NS:
        raw = QUALITY_GOOD
    elif uncertainty <= USABLE_UNCERTAINTY_NS:
        raw = QUALITY_USABLE
    else:
        raw = QUALITY_POOR
    quality = raw
    local = quality in _LOCAL
    previous_local = previous_quality in _LOCAL
    hold = count >= HOLD_MIN_SAMPLES and uncertainty <= HOLD_UNCERTAINTY_NS
    if previous_local and not local and not stale and hold:
        quality = QUALITY_USABLE
        local = True
    jumped = previous_offset_ns is not None and abs(offset - previous_offset_ns) > OFFSET_JUMP_NS
    if jumped and spread > USABLE_UNCERTAINTY_NS:
        quality = QUALITY_POOR
        local = False
    return SyncEstimate(
        quality=quality,
        offset_ns=offset,
        uncertainty_ns=uncertainty,
        rtt_ns=newest.rtt_ns,
        best_rtt_ns=best,
        sample_count=count,
        age_ns=age,
        local_prediction=local,
    )


def predict_red_lamps(
    phases: Sequence[RedPhase],
    *,
    planned_go_ns: int,
    now_ns: int,
    active: bool,
) -> RedPrediction | None:
    """The red phase at ``now_ns``. At the planned GO time the last red phase stays on."""
    if not active:
        return None
    ordered = tuple(sorted(phases, key=lambda phase: (phase.at_ns, phase.step_index)))
    counting = tuple(phase for phase in ordered if phase.lit_lights >= 1)
    if not counting:
        return None
    if now_ns >= planned_go_ns:
        last = counting[-1]
        return RedPrediction(last.lit_lights, last.step_index, True)
    chosen: RedPhase | None = None
    for phase in counting:
        if phase.at_ns <= now_ns:
            chosen = phase
        else:
            break
    if chosen is None:
        return RedPrediction(0, -1, False)
    return RedPrediction(chosen.lit_lights, chosen.step_index, False)


def next_lamp_ns(
    phases: Sequence[RedPhase],
    *,
    planned_go_ns: int,
    now_ns: int,
    active: bool,
) -> int | None:
    """The next red boundary, or the planned GO time so the last red phase can be held."""
    if not active or now_ns >= planned_go_ns:
        return None
    upcoming = [
        phase.at_ns
        for phase in phases
        if phase.lit_lights >= 1 and now_ns < phase.at_ns < planned_go_ns
    ]
    if planned_go_ns > now_ns:
        upcoming.append(planned_go_ns)
    if not upcoming:
        return None
    return min(upcoming)


def shown_lamps(
    *,
    counting: bool,
    authorizes_go: bool,
    server_lit: int,
    predicted: int | None,
    peak: int,
    local: bool,
) -> int:
    """Lamps on the gantry. GO is lamps off only when the server commit says so."""
    if authorizes_go or not counting:
        return 0
    if not local or predicted is None:
        return server_lit
    return max(server_lit, predicted, peak)


def sync_source() -> str:
    """Browser math. The HUD and the diagnostic page both embed this text."""
    return _SYNC_JS


def _empty() -> SyncEstimate:
    return SyncEstimate(QUALITY_POOR, None, None, None, None, 0, None, False)


def _median(values: Sequence[int]) -> int:
    ordered = sorted(values)
    count = len(ordered)
    mid = count // 2
    if count % 2 == 1:
        return ordered[mid]
    return toward_zero(ordered[mid - 1] + ordered[mid])


def _text(value: int | None) -> str | None:
    if value is None:
        return None
    return str(value)


def _encode(value: object) -> object:
    if isinstance(value, dict):
        encoded: dict[str, object] = {}
        for key, item in value.items():
            if not isinstance(key, str):
                continue
            if key in _TIMESTAMP_KEYS and isinstance(item, int) and not isinstance(item, bool):
                encoded[key] = str(item)
            else:
                encoded[key] = _encode(item)
        return encoded
    if isinstance(value, list):
        return [_encode(item) for item in value]
    return value


_SYNC_JS = """\
/*__SYNC_START__*/
function hostNs(value) {
  if (typeof value === "bigint") return value;
  if (typeof value === "string" && /^-?\\d+$/.test(value)) {
    try { return BigInt(value); } catch (error) { return null; }
  }
  if (typeof value === "number" && Number.isSafeInteger(value)) {
    return BigInt(value);
  }
  return null;
}
function storeNs(value) {
  var parsed = hostNs(value);
  if (parsed === null) return null;
  var limit = BigInt(Number.MAX_SAFE_INTEGER);
  if (parsed >= -limit && parsed <= limit) return Number(parsed);
  return parsed.toString();
}
function clientNowNs() {
  if (typeof performance === "undefined" || !performance.now) return null;
  var micros = Math.round(performance.now() * 1000);
  if (!Number.isSafeInteger(micros) || micros < 0) return null;
  return BigInt(micros) * 1000n;
}
var MAX_RTT_NS = 500000000n;
var GOOD_NS = 8000000n;
var USABLE_NS = 20000000n;
var HOLD_NS = 25000000n;
var STALE_NS = 45000000000n;
var EXPIRE_NS = 60000000000n;
var JUMP_NS = 50000000n;
function ntpSample(t1, t2, t3, t4) {
  var rtt = (t4 - t1) - (t3 - t2);
  if (rtt < 0n || rtt > MAX_RTT_NS) return null;
  var offset = ((t2 - t1) + (t3 - t4)) / 2n;
  return {rtt: rtt, offset: offset};
}
function medianNs(values) {
  var ordered = values.slice().sort(function (left, right) {
    if (left < right) return -1;
    if (left > right) return 1;
    return 0;
  });
  var count = ordered.length;
  var mid = Math.floor(count / 2);
  if (count % 2 === 1) return ordered[mid];
  return (ordered[mid - 1] + ordered[mid]) / 2n;
}
function assessSync(samples, now, previousQuality, previousOffset) {
  var fresh = [];
  var index;
  for (index = 0; index < samples.length; index += 1) {
    var age = now - samples[index].t4;
    if (age >= 0n && age <= EXPIRE_NS) fresh.push(samples[index]);
  }
  if (!fresh.length) {
    return {
      quality: "poor", offset: null, uncertainty: null, rtt: null,
      bestRtt: null, count: 0, age: null, local: false
    };
  }
  var newest = fresh[0];
  for (index = 1; index < fresh.length; index += 1) {
    if (fresh[index].t4 > newest.t4) newest = fresh[index];
  }
  var sampleAge = now - newest.t4;
  var pool = fresh.slice().sort(function (left, right) {
    if (left.rtt < right.rtt) return -1;
    if (left.rtt > right.rtt) return 1;
    if (left.t4 < right.t4) return -1;
    if (left.t4 > right.t4) return 1;
    return 0;
  });
  if (pool.length > 8) pool = pool.slice(0, 8);
  var take = Math.max(1, Math.floor((pool.length + 1) / 2));
  var chosen = pool.slice(0, take);
  var offsets = [];
  var rtts = [];
  for (index = 0; index < chosen.length; index += 1) {
    offsets.push(chosen[index].offset);
    rtts.push(chosen[index].rtt);
  }
  var offset = medianNs(offsets);
  var spread = offsets[0];
  var low = offsets[0];
  for (index = 1; index < offsets.length; index += 1) {
    if (offsets[index] > spread) spread = offsets[index];
    if (offsets[index] < low) low = offsets[index];
  }
  spread -= low;
  var uncertainty = medianNs(rtts) / 2n;
  if (spread > uncertainty) uncertainty = spread;
  var best = fresh[0].rtt;
  for (index = 1; index < fresh.length; index += 1) {
    if (fresh[index].rtt < best) best = fresh[index].rtt;
  }
  var stale = sampleAge > STALE_NS;
  var raw = "poor";
  if (!stale && fresh.length >= 3) {
    if (uncertainty <= GOOD_NS) raw = "good";
    else if (uncertainty <= USABLE_NS) raw = "usable";
  }
  var quality = raw;
  var local = quality === "good" || quality === "usable";
  var previousLocal = previousQuality === "good" || previousQuality === "usable";
  if (previousLocal && !local && !stale && fresh.length >= 2 && uncertainty <= HOLD_NS) {
    quality = "usable";
    local = true;
  }
  if (previousOffset !== null && previousOffset !== undefined) {
    var delta = offset - previousOffset;
    if (delta < 0n) delta = -delta;
    if (delta > JUMP_NS && spread > USABLE_NS) {
      quality = "poor";
      local = false;
    }
  }
  return {
    quality: quality, offset: offset, uncertainty: uncertainty, rtt: newest.rtt,
    bestRtt: best, count: fresh.length, age: sampleAge, local: local
  };
}
function pruneSamples(samples, now) {
  var keep = [];
  var index;
  var jumped = false;
  for (index = 0; index < samples.length; index += 1) {
    if (samples[index].t4 > now + 1000000n) jumped = true;
    else keep.push(samples[index]);
  }
  if (jumped) return {samples: [], jumped: true};
  return {samples: keep, jumped: false};
}
function predictRed(phases, plannedGo, now, active) {
  if (!active || !phases || !phases.length) return null;
  var ordered = phases.slice().sort(function (left, right) {
    if (left.at < right.at) return -1;
    if (left.at > right.at) return 1;
    return left.step - right.step;
  });
  var last = null;
  var index;
  for (index = 0; index < ordered.length; index += 1) {
    if (ordered[index].lit >= 1) last = ordered[index];
  }
  if (!last) return null;
  if (now >= plannedGo) {
    return {lit_lights: last.lit, step_index: last.step, holding: true};
  }
  var chosen = null;
  for (index = 0; index < ordered.length; index += 1) {
    if (ordered[index].lit < 1) continue;
    if (ordered[index].at <= now) chosen = ordered[index];
    else break;
  }
  if (!chosen) return {lit_lights: 0, step_index: -1, holding: false};
  return {lit_lights: chosen.lit, step_index: chosen.step, holding: false};
}
function nextLampNs(phases, plannedGo, now, active) {
  if (!active || now >= plannedGo) return null;
  var next = null;
  var index;
  for (index = 0; index < phases.length; index += 1) {
    var at = phases[index].at;
    if (phases[index].lit < 1) continue;
    if (at > now && at < plannedGo && (next === null || at < next)) next = at;
  }
  if (plannedGo > now && (next === null || plannedGo < next)) next = plannedGo;
  return next;
}
function shownLamps(counting, authorizesGo, serverLit, predicted, peak, local) {
  if (authorizesGo || !counting) return 0;
  if (!local || predicted === null || predicted === undefined) return serverLit;
  var lit = serverLit;
  if (predicted > lit) lit = predicted;
  if (peak > lit) lit = peak;
  return lit;
}
/*__SYNC_END__*/
"""
