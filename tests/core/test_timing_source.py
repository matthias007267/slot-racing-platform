import pytest

from carrera.core.events import SensorTriggered
from carrera.core.timing import SensorSink, TimingSource


class _FakeSource(TimingSource):
    def __init__(self) -> None:
        self._sink: SensorSink | None = None

    @property
    def source_id(self) -> str:
        return "fake"

    @property
    def is_running(self) -> bool:
        return self._sink is not None

    def start(self, sink: SensorSink) -> None:
        self._sink = sink

    def stop(self) -> None:
        self._sink = None

    def trigger(self) -> None:
        assert self._sink is not None
        self._sink(SensorTriggered(timestamp_ns=1, source_id=self.source_id, sensor_id="a", lane=1))


def test_interface_is_abstract() -> None:
    with pytest.raises(TypeError):
        TimingSource()  # type: ignore[abstract]


def test_any_implementation_reports_through_standard_events() -> None:
    received: list[SensorTriggered] = []
    source = _FakeSource()
    source.start(received.append)
    source.trigger()
    source.stop()
    assert [e.source_id for e in received] == ["fake"]
    assert not source.is_running
