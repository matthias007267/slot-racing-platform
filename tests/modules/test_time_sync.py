"""Clock estimate, red-lamp prediction, and the browser copy of those rules."""

from __future__ import annotations

import json
import shutil
import subprocess

import pytest

from slot_racing.modules.races.live_page import hud_page
from slot_racing.modules.races.start_display import (
    PROTOCOL_VERSION,
    initial_display,
    phase_document,
    reduce_start_display,
)
from slot_racing.modules.races.time_sync import (
    EXPIRE_NS,
    HOLD_UNCERTAINTY_NS,
    PROBE_LIMIT,
    PROBE_WINDOW_NS,
    STALE_NS,
    RedPhase,
    TimeSample,
    accept_sample,
    allow_probe,
    assess_sync,
    encode_timestamps,
    next_lamp_ns,
    ntp_sample,
    parse_timestamp,
    predict_red_lamps,
    prune_samples,
    shown_lamps,
    sync_source,
)

_HUGE = 9_007_199_254_740_993


def _sample(probe: str, t4: int, rtt: int, offset: int) -> TimeSample:
    return TimeSample(probe, t4, rtt, offset)


def _phases() -> tuple[RedPhase, ...]:
    return tuple(
        RedPhase(at_ns=1_000_000_000 * (index + 1), lit_lights=index + 1, step_index=index)
        for index in range(5)
    )


def _event(kind: str, seq: int, payload: dict[str, object]) -> dict[str, object]:
    return {"protocol_version": PROTOCOL_VERSION, "type": kind, "seq": seq, "payload": payload}


def test_ntp_offsets_rtt_and_implausible_samples() -> None:
    assert ntp_sample(1_000, 1_100, 1_100, 1_200) == (200, 0)
    assert ntp_sample(1_000, 6_100, 6_100, 1_200) == (200, 5_000)
    assert ntp_sample(6_000, 1_100, 1_100, 6_200) == (200, -5_000)
    assert ntp_sample(0, 5_000_000, 5_100_000, 5_200_000) == (5_100_000, 2_450_000)
    assert ntp_sample(0, 0, 0, 3) == (3, -1)
    assert ntp_sample(0, 20_000_000, 20_000_000, 20_000_000) == (20_000_000, 10_000_000)
    base = 10**18
    assert ntp_sample(base, base + 100, base + 100, base + 200) == (200, 0)
    assert ntp_sample(0, 0, 10, 0) is None
    assert ntp_sample(0, 0, 0, 500_000_001) is None
    assert parse_timestamp(True) is None
    assert parse_timestamp(1.5) is None
    assert parse_timestamp("9007199254740993") == _HUGE
    encoded = encode_timestamps(
        {"planned_go_ns": _HUGE, "lit_lights": 5, "phases": [{"at_ns": _HUGE}]}
    )
    assert encoded["planned_go_ns"] == str(_HUGE)
    phases = encoded["phases"]
    assert isinstance(phases, list)
    assert isinstance(phases[0], dict)
    assert phases[0]["at_ns"] == str(_HUGE)
    assert encoded["lit_lights"] == 5


def test_quality_uses_low_rtt_samples_and_does_not_flap() -> None:
    now = 5_000
    good = assess_sync([_sample(str(i), 1_000 + i, 2_000_000, 4_000) for i in range(4)], now)
    assert good.quality == "good"
    assert good.local_prediction is True
    assert good.offset_ns == 4_000
    assert good.uncertainty_ns == 1_000_000
    assert good.best_rtt_ns == 2_000_000
    usable = assess_sync([_sample(str(i), 1_000 + i, 30_000_000, 4_000) for i in range(3)], now)
    assert usable.quality == "usable"
    assert usable.local_prediction is True
    poor = assess_sync([_sample(str(i), 1_000 + i, 80_000_000, 4_000) for i in range(3)], now)
    assert poor.quality == "poor"
    assert poor.local_prediction is False
    thin = assess_sync([_sample("a", 1_000, 1_000_000, 1), _sample("b", 1_001, 1_000_000, 1)], now)
    assert thin.local_prediction is False
    noisy = [
        _sample("a", 100, 1_000_000, 70_000_000),
        _sample("b", 101, 1_000_000, 92_000_000),
        _sample("c", 102, 1_000_000, 70_000_000),
        _sample("d", 103, 1_000_000, 70_000_000),
    ]
    held = assess_sync(noisy, 103, previous_quality="good")
    assert held.quality == "usable"
    assert held.local_prediction is True
    assert held.uncertainty_ns is not None
    assert held.uncertainty_ns <= HOLD_UNCERTAINTY_NS
    jumped = assess_sync(noisy, 103, previous_quality="good", previous_offset_ns=0)
    assert jumped.quality == "poor"
    assert jumped.local_prediction is False
    again = assess_sync(
        [_sample(str(i), 2_000 + i, 2_000_000, 4_000) for i in range(4)],
        3_000,
        previous_quality="poor",
    )
    assert again.quality == "good"
    assert again.local_prediction is True
    stale_at = 1_000 + STALE_NS + 1
    stale = assess_sync([_sample(str(i), 1_000, 1_000_000, 1) for i in range(4)], stale_at)
    assert stale.quality == "poor"
    assert stale.local_prediction is False
    expired = assess_sync([_sample("a", 1_000, 1_000_000, 1)], 1_000 + EXPIRE_NS + 1)
    assert expired.sample_count == 0
    outliers = [_sample(str(i), 100 + i, 1_000_000, 5_000) for i in range(6)]
    outliers.append(_sample("x", 200, 100_000_000, 50_000_000))
    outliers.append(_sample("y", 201, 90_000_000, 40_000_000))
    preferred = assess_sync(outliers, 201)
    assert preferred.offset_ns == 5_000
    assert preferred.quality == "good"
    assert preferred.best_rtt_ns == 1_000_000
    pending = {"fresh"}
    kept: list[TimeSample] = []
    sample = _sample("fresh", 10, 100, 5)
    assert accept_sample(kept, pending, sample) is True
    assert accept_sample(kept, pending, sample) is False
    assert accept_sample(kept, {"other"}, _sample("stale", 11, 100, 5)) is False
    assert prune_samples([_sample("a", 2_000_000, 1, 1)], 10).jumped is True
    assert prune_samples([_sample("a", 50, 1, 1)], 10).samples == ()


def test_probe_rate_and_red_phases_stop_before_go() -> None:
    marks: list[int] = []
    assert all(allow_probe(marks, 0) for _index in range(PROBE_LIMIT))
    assert allow_probe(marks, 0) is False
    assert allow_probe(marks, PROBE_WINDOW_NS) is True
    phases = _phases()
    planned = 6_000_000_000
    assert predict_red_lamps(phases, planned_go_ns=planned, now_ns=500_000_000, active=True)
    early = predict_red_lamps(phases, planned_go_ns=planned, now_ns=500_000_000, active=True)
    assert early is not None and early.lit_lights == 0 and early.holding is False
    third = predict_red_lamps(phases, planned_go_ns=planned, now_ns=3_000_000_000, active=True)
    assert third is not None and third.lit_lights == 3 and third.holding is False
    uneven = (
        RedPhase(0, 1, 0),
        RedPhase(100_000_000, 2, 1),
        RedPhase(500_000_000, 3, 2),
        RedPhase(600_000_000, 4, 3),
        RedPhase(1_000_000_000, 5, 4),
    )
    skipped = predict_red_lamps(
        uneven, planned_go_ns=2_000_000_000, now_ns=550_000_000, active=True
    )
    assert skipped is not None and skipped.lit_lights == 3
    assert next_lamp_ns(uneven, planned_go_ns=2_000_000_000, now_ns=550_000_000, active=True) == (
        600_000_000
    )
    held = predict_red_lamps(phases, planned_go_ns=planned, now_ns=planned, active=True)
    assert held is not None and held.holding is True and held.lit_lights == 5
    assert next_lamp_ns(phases, planned_go_ns=planned, now_ns=planned, active=True) is None
    assert predict_red_lamps(phases, planned_go_ns=planned, now_ns=planned, active=False) is None
    ahead = shown_lamps(
        counting=True, authorizes_go=False, server_lit=1, predicted=5, peak=0, local=True
    )
    assert ahead == 5
    no_rewind = shown_lamps(
        counting=True, authorizes_go=False, server_lit=2, predicted=2, peak=4, local=True
    )
    assert no_rewind == 4
    fallback = shown_lamps(
        counting=True, authorizes_go=False, server_lit=2, predicted=5, peak=5, local=False
    )
    assert fallback == 2
    assert (
        shown_lamps(
            counting=True, authorizes_go=True, server_lit=5, predicted=5, peak=5, local=True
        )
        == 0
    )


def test_go_stays_server_confirmed_for_the_security_cases() -> None:
    phases = _phases()
    planned = 6_000_000_000
    schedule = _event("start_schedule", 1, {"sequence_id": "seq", "status": "active"})
    counting = reduce_start_display(initial_display(), schedule)
    counting = reduce_start_display(
        counting,
        _event(
            "start_phase",
            1,
            phase_document(sequence_id="seq", step_index=0, lit_lights=1, at_ns=1_000_000_000),
        ),
    )
    predicted = predict_red_lamps(phases, planned_go_ns=planned, now_ns=planned + 1, active=True)
    assert predicted is not None and predicted.holding is True
    lit = shown_lamps(
        counting=True,
        authorizes_go=counting.authorizes_go,
        server_lit=counting.lit_lights,
        predicted=predicted.lit_lights,
        peak=0,
        local=True,
    )
    assert counting.authorizes_go is False
    assert lit == 5
    blocked = shown_lamps(
        counting=True,
        authorizes_go=False,
        server_lit=1,
        predicted=5,
        peak=1,
        local=True,
    )
    assert blocked == 5
    wrong = reduce_start_display(
        counting,
        _event(
            "start_committed",
            2,
            {
                "sequence_id": "other",
                "actual_start_ns": "80",
                "race_status": "running",
                "authorizes_go": True,
            },
        ),
    )
    assert wrong.authorizes_go is False
    assert wrong.lit_lights == 1
    lost = reduce_start_display(counting, {"type": "disconnect"})
    assert lost.authorizes_go is False
    assert (
        shown_lamps(
            counting=False,
            authorizes_go=lost.authorizes_go,
            server_lit=0,
            predicted=5,
            peak=5,
            local=True,
        )
        == 0
    )
    resumed = reduce_start_display(lost, {"type": "reconnect"})
    nxt = reduce_start_display(
        resumed, _event("start_schedule", 2, {"sequence_id": "next", "status": "active"})
    )
    stale_commit = reduce_start_display(
        nxt,
        _event(
            "start_committed",
            1,
            {
                "sequence_id": "seq",
                "actual_start_ns": str(_HUGE),
                "race_status": "running",
                "authorizes_go": True,
            },
        ),
    )
    assert stale_commit.sequence_id == "next"
    assert stale_commit.authorizes_go is False
    assert stale_commit.lit_lights == 0


def test_the_browser_math_matches_and_keeps_huge_timestamps() -> None:
    node = shutil.which("node")
    if node is None:
        pytest.skip("node is not installed")
    script = hud_page().decode().split("<script>", 1)[1].split("</script>", 1)[0]
    page = script[script.index("function hostNs(") : _function_end(script, "function reduceStart(")]
    samples = [_sample(str(index), 1_000 + index, 2_000_000, 4_000) for index in range(4)]
    estimate = assess_sync(samples, 5_000).to_document()
    payload = {
        "estimate": {
            "samples": [
                {
                    "t4": str(sample.t4_ns),
                    "rtt": str(sample.rtt_ns),
                    "offset": str(sample.offset_ns),
                }
                for sample in samples
            ],
            "now": "5000",
            "previous": "poor",
            "previous_offset": None,
        },
        "expect": estimate,
        "huge": str(_HUGE),
    }
    completed = subprocess.run(
        [node, "-e", page + "\n" + _NODE],
        input=json.dumps(payload),
        text=True,
        capture_output=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
    result = json.loads(completed.stdout)
    assert result["estimate"] == estimate
    assert result["huge"] == str(_HUGE)
    assert result["unsafe"] is None
    assert result["go"] is False
    assert result["held"] == 5
    assert result["committed"] is True
    assert result["stamp"] == str(_HUGE)
    assert sync_source() in hud_page().decode()


def _function_end(script: str, signature: str) -> int:
    brace = script.index("{", script.index(signature))
    depth = 0
    for index in range(brace, len(script)):
        if script[index] == "{":
            depth += 1
        elif script[index] == "}":
            depth -= 1
            if depth == 0:
                return index + 1
    raise AssertionError("browser function is not closed")


_NODE = """
const chunks = [];
process.stdin.on("data", function (chunk) { chunks.push(chunk); });
process.stdin.on("end", function () {
  const payload = JSON.parse(chunks.join(""));
  const samples = payload.estimate.samples.map(function (item) {
    return {t4: BigInt(item.t4), rtt: BigInt(item.rtt), offset: BigInt(item.offset)};
  });
  const previous = payload.estimate.previous_offset === null
    ? null
    : BigInt(payload.estimate.previous_offset);
  const estimate = assessSync(
    samples, BigInt(payload.estimate.now), payload.estimate.previous, previous
  );
  function text(value) { return value === null ? null : value.toString(); }
  const packed = {
    quality: estimate.quality,
    offset_ns: text(estimate.offset),
    uncertainty_ns: text(estimate.uncertainty),
    rtt_ns: text(estimate.rtt),
    best_rtt_ns: text(estimate.bestRtt),
    sample_count: estimate.count,
    age_ns: text(estimate.age),
    local_prediction: estimate.local
  };
  const phases = [1, 2, 3, 4, 5].map(function (lit, index) {
    return {at: BigInt(index + 1) * 1000000000n, lit: lit, step: index};
  });
  const found = predictRed(phases, 6000000000n, 6000000001n, true);
  let counting = freshStart("idle");
  counting = reduceStart(counting, {
    protocol_version: 3, type: "start_schedule", seq: 1,
    payload: {sequence_id: "seq", status: "active"}
  });
  const lit = shownLamps(
    counting.state === "counting", counting.authorizes_go,
    counting.lit_lights, found.lit_lights, 0, true
  );
  const committed = reduceStart(counting, {
    protocol_version: 3, type: "start_committed", seq: 2,
    payload: {
      sequence_id: "seq", actual_start_ns: payload.huge,
      race_status: "running", authorizes_go: true
    }
  });
  const output = {
    estimate: packed,
    huge: hostNs(payload.huge).toString(),
    unsafe: hostNs(Number.MAX_SAFE_INTEGER + 2),
    go: counting.authorizes_go,
    held: lit,
    committed: committed.authorizes_go,
    stamp: committed.actual_start_ns
  };
  process.stdout.write(JSON.stringify(output));
});
"""
