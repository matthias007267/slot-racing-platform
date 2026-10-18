"""Estimate the host mapping from several clock exchanges.

Each sample carries the host send and receive times and the device receive and
transmit times. The crossing time is never one of those host stamps. A sample
whose round trip is too uncertain is ignored. The rate is estimated only when
the samples span enough device time for the slope to be distinguishable from
the round-trip noise; otherwise the rate stays 1.

Nanoseconds are the integer unit of the mapping. The reported uncertainty is
the median one-way delay bound of the accepted samples, not a claim that a
gate time is known to a nanosecond.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from math import gcd

from slot_racing.modules.timing_sensor.clock_map import DeviceClockMapping, to_host_ns

# One-way delay still trusted for an offset. A Mega 2560 USB-serial reply on
# Windows is often slower than 20 ms, and a short line used to sit until the
# serial read timed out, so every real sample was dropped. A stable offset
# cancels in lap differences. Slower samples are rejected and reported. The
# crossing time stays the device timestamp converted by the mapping.
MAX_UNCERTAINTY_NS = 250_000_000
MIN_SAMPLES = 4
# Below this span a rate fit is smaller than the delay noise, so the rate stays 1.
MIN_RATE_SPAN_NS = 2_000_000_000
# A fit beyond one percent is a bad sample, not a crystal. Keep rate 1.
_MAX_RATE_ERROR_PERCENT = 1
_WINDOW = 16


@dataclass(frozen=True, slots=True)
class SyncSample:
    """One finished exchange. All four stamps are required."""

    host_send_ns: int
    host_recv_ns: int
    device_rx_ns: int
    device_tx_ns: int
    request_id: int = 0

    def __post_init__(self) -> None:
        for name in ("host_send_ns", "host_recv_ns", "device_rx_ns", "device_tx_ns"):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int):
                raise TypeError(f"{name} must be an int")
        if self.host_recv_ns < self.host_send_ns or self.device_tx_ns < self.device_rx_ns:
            raise ValueError("clock sample times must not run backwards")
        if isinstance(self.request_id, bool) or not isinstance(self.request_id, int):
            raise TypeError("request_id must be an int")

    @property
    def uncertainty_ns(self) -> int:
        """Half of the host round trip that was not spent on the controller."""
        adjusted = (self.host_recv_ns - self.host_send_ns) - (self.device_tx_ns - self.device_rx_ns)
        if adjusted < 0:
            return self.host_recv_ns - self.host_send_ns
        return adjusted // 2

    @property
    def device_mid_ns(self) -> int:
        return (self.device_rx_ns + self.device_tx_ns) // 2

    @property
    def host_mid_ns(self) -> int:
        return (self.host_send_ns + self.host_recv_ns) // 2

    @property
    def offset_ns(self) -> int:
        """Host minus device at the middle of the exchange. Not the installed mapping."""
        return self.host_mid_ns - self.device_mid_ns


@dataclass(frozen=True, slots=True)
class SyncQuality:
    """What the current window supports. ``accepted`` is false until enough samples agree."""

    samples: int
    uncertainty_ns: int
    numerator: int
    denominator: int
    accepted: bool


@dataclass(frozen=True, slots=True)
class ClockReport:
    """Why the window has the rate it has. Diagnostic only; the mapping does not read it.

    ``rate_status`` is ``fitted`` when the slope was used, ``too_few_samples``,
    ``span_too_short`` while the samples cover less than the minimum span, and
    ``skew_rejected`` when the raw slope is beyond the allowed error and the rate
    stays 1. ``skew_ppm`` is host time over device time minus one, in parts per
    million, whenever it could be computed, even when it was rejected. A positive
    value means the device clock runs slow.
    """

    rate_status: str
    skew_ppm: int | None
    span_ns: int
    window_samples: int
    anchor_host_mid_ns: int | None
    anchor_device_mid_ns: int | None
    anchor_uncertainty_ns: int | None


class ClockSync:
    """A rolling window of accepted exchanges."""

    def __init__(self) -> None:
        self._samples: deque[SyncSample] = deque(maxlen=_WINDOW)
        self.quality = SyncQuality(0, 0, 1, 1, False)
        self.mapping: DeviceClockMapping | None = None
        self.rewound = False

    def clear(self) -> None:
        """Drop the window. Used when the session or the connection ends."""
        self._samples.clear()
        self.quality = SyncQuality(0, 0, 1, 1, False)
        self.mapping = None
        self.rewound = False

    def report(self) -> ClockReport:
        """The rate decision and the anchor of the current window."""
        return clock_report(tuple(self._samples))

    def add(self, sample: SyncSample) -> str | None:
        """Add one sample. A device clock that steps backwards clears the window.

        Returns ``uncertainty`` when the sample is too slow to use, ``rewind``
        when the device clock stepped backwards, and ``None`` when the sample
        joined the window.
        """
        if not isinstance(sample, SyncSample):
            raise TypeError("sample must be a SyncSample")
        reason: str | None = None
        if (
            self._samples
            and sample.device_mid_ns + MAX_UNCERTAINTY_NS < self._samples[-1].device_mid_ns
        ):
            self._samples.clear()
            self.mapping = None
            self.rewound = True
            reason = "rewind"
        if sample.uncertainty_ns > MAX_UNCERTAINTY_NS:
            self.quality, self.mapping = mapping_for(tuple(self._samples))
            return "uncertainty"
        self._samples.append(sample)
        self.quality, self.mapping = mapping_for(tuple(self._samples))
        return reason


def evaluate(samples: tuple[SyncSample, ...]) -> SyncQuality:
    """The rate supported by ``samples``. Fewer than four samples are not accepted."""
    good = tuple(sample for sample in samples if sample.uncertainty_ns <= MAX_UNCERTAINTY_NS)
    numerator, denominator = _rate(good) if len(good) >= MIN_SAMPLES else (1, 1)
    return SyncQuality(
        len(good),
        _median_uncertainty(good),
        numerator,
        denominator,
        len(good) >= MIN_SAMPLES,
    )


def clock_report(samples: tuple[SyncSample, ...]) -> ClockReport:
    """The rate decision for ``samples`` and the sample the offset is anchored on."""
    good = tuple(sample for sample in samples if sample.uncertainty_ns <= MAX_UNCERTAINTY_NS)
    if not good:
        return ClockReport("too_few_samples", None, 0, 0, None, None, None)
    earliest = min(good, key=lambda sample: sample.device_mid_ns)
    latest = max(good, key=lambda sample: sample.device_mid_ns)
    device_delta = latest.device_mid_ns - earliest.device_mid_ns
    host_delta = latest.host_mid_ns - earliest.host_mid_ns
    skew = None
    if device_delta > 0:
        skew = (host_delta - device_delta) * 1_000_000 // device_delta
    anchor = min(good, key=lambda sample: sample.uncertainty_ns)
    if len(good) < MIN_SAMPLES:
        status = "too_few_samples"
    elif device_delta < MIN_RATE_SPAN_NS or host_delta <= 0:
        status = "span_too_short"
    elif _rate(good) == (1, 1) and host_delta != device_delta:
        status = "skew_rejected"
    else:
        status = "fitted"
    return ClockReport(
        status,
        skew,
        device_delta,
        len(good),
        anchor.host_mid_ns,
        anchor.device_mid_ns,
        anchor.uncertainty_ns,
    )


def mapping_for(samples: tuple[SyncSample, ...]) -> tuple[SyncQuality, DeviceClockMapping | None]:
    """Quality and, when accepted, the absolute mapping for this window."""
    quality = evaluate(samples)
    if not quality.accepted:
        return quality, None
    good = tuple(sample for sample in samples if sample.uncertainty_ns <= MAX_UNCERTAINTY_NS)
    anchor = min(good, key=lambda sample: sample.uncertainty_ns)
    offset = anchor.host_mid_ns - anchor.device_mid_ns * quality.numerator // quality.denominator
    mapping = DeviceClockMapping(offset, quality.numerator, quality.denominator)
    try:
        if to_host_ns(anchor.device_mid_ns, mapping) < 0:
            return SyncQuality(quality.samples, quality.uncertainty_ns, 1, 1, False), None
    except ValueError:
        return SyncQuality(quality.samples, quality.uncertainty_ns, 1, 1, False), None
    return quality, mapping


def continuous_mapping(
    estimated: DeviceClockMapping,
    *,
    last_device_ns: int,
    last_host_ns: int,
) -> DeviceClockMapping | None:
    """The estimated rate, anchored so the last published instant does not move.

    Returns ``None`` when the anchor cannot be represented without a host time
    that is earlier than the event already emitted.
    """
    if not isinstance(estimated, DeviceClockMapping):
        raise TypeError("estimated must be a DeviceClockMapping")
    offset = last_host_ns - last_device_ns * estimated.numerator // estimated.denominator
    candidate = DeviceClockMapping(offset, estimated.numerator, estimated.denominator)
    hosted = to_host_ns(last_device_ns, candidate)
    if hosted < last_host_ns:
        candidate = DeviceClockMapping(
            offset + (last_host_ns - hosted),
            estimated.numerator,
            estimated.denominator,
        )
        hosted = to_host_ns(last_device_ns, candidate)
    if hosted < last_host_ns:
        return None
    if to_host_ns(last_device_ns + estimated.denominator, candidate) < hosted:
        return None
    return candidate


def _rate(samples: tuple[SyncSample, ...]) -> tuple[int, int]:
    earliest = min(samples, key=lambda sample: sample.device_mid_ns)
    latest = max(samples, key=lambda sample: sample.device_mid_ns)
    device_delta = latest.device_mid_ns - earliest.device_mid_ns
    host_delta = latest.host_mid_ns - earliest.host_mid_ns
    if device_delta < MIN_RATE_SPAN_NS or host_delta <= 0:
        return 1, 1
    if abs(host_delta - device_delta) * 100 > device_delta * _MAX_RATE_ERROR_PERCENT:
        return 1, 1
    divisor = gcd(host_delta, device_delta)
    return host_delta // divisor, device_delta // divisor


def _median_uncertainty(samples: tuple[SyncSample, ...]) -> int:
    if not samples:
        return 0
    ordered = tuple(sorted(sample.uncertainty_ns for sample in samples))
    return ordered[len(ordered) // 2]
