"""Saved camera configuration. No camera and no track are required."""

from __future__ import annotations

from dataclasses import replace

import pytest
from pydantic import ValidationError

from slot_racing.core.domain import TrackId
from slot_racing.core.errors import ProviderConfigurationError
from slot_racing.core.events import SensorTriggered
from slot_racing.core.storage import Database, Setting
from slot_racing.core.timing import TimingSessionSpec
from slot_racing.modules.timing_camera.camera_config import CameraConfig
from slot_racing.modules.timing_camera.configuration import (
    SCHEMA_VERSION,
    CameraConfiguration,
    NormalizedRoi,
    StoredCamera,
    StoredDetection,
    StoredDetectionZone,
    pixels_to_roi,
    roi_to_pixels,
    to_camera_config,
    to_detector_settings,
)
from slot_racing.modules.timing_camera.frame_source import ManualFrameSource
from slot_racing.modules.timing_camera.frames import GrayFrame
from slot_racing.modules.timing_camera.geometry import DetectionRoi
from slot_racing.modules.timing_camera.provider import CameraTimingFactory
from slot_racing.modules.timing_camera.store import (
    CAMERA_CONFIGURATION_KEY,
    CameraConfigurationError,
    CameraConfigurationStore,
)
from tests.modules.test_camera_provider import spec_for

WIDTH = 80
HEIGHT = 40


def database() -> Database:
    stored = Database.in_memory()
    stored.migrate()
    return stored


def zone(position_id: str, lane: int, *, y: float) -> StoredDetectionZone:
    return StoredDetectionZone(
        position_id=position_id,
        lane=lane,
        roi=NormalizedRoi(x=0.25, y=y, width=0.125, height=0.25),
    )


def sample() -> CameraConfiguration:
    return CameraConfiguration(
        camera=StoredCamera(device_index=2, width=WIDTH, height=HEIGHT, fps=15),
        detection=StoredDetection(
            zones=(
                zone("sector_1", 1, y=0.5),
                zone("start_finish", 2, y=0.0),
                zone("start_finish", 1, y=0.0),
                zone("sector_2", 3, y=0.25),
            )
        ),
    )


def test_defaults_are_a_usable_camera_without_zones() -> None:
    configuration = CameraConfiguration()
    assert configuration.version == SCHEMA_VERSION
    assert configuration.camera == StoredCamera()
    assert configuration.detection.zones == ()
    assert to_camera_config(configuration) == CameraConfig()
    assert to_detector_settings(configuration) is None


def test_save_and_load_keep_every_value_and_the_zone_order() -> None:
    store = CameraConfigurationStore(database())
    store.save(sample())
    loaded = store.load()
    assert loaded == sample()
    store.save(CameraConfiguration())
    assert store.load() == CameraConfiguration()
    store.save(sample())
    loaded = store.load()
    assert loaded == sample()
    assert [(item.position_id, item.lane) for item in loaded.detection.zones] == [
        ("sector_1", 1),
        ("start_finish", 2),
        ("start_finish", 1),
        ("sector_2", 3),
    ]


def test_a_new_store_reads_what_the_previous_one_saved() -> None:
    stored = database()
    CameraConfigurationStore(stored).save(sample())
    assert CameraConfigurationStore(stored).load() == sample()


def test_a_missing_document_loads_the_defaults() -> None:
    stored = database()
    assert CameraConfigurationStore(stored).load() == CameraConfiguration()
    with stored.session() as session:
        session.add(Setting(key=CAMERA_CONFIGURATION_KEY, value={}))
    assert CameraConfigurationStore(stored).load() == CameraConfiguration()


def test_saving_the_camera_leaves_other_settings_in_place() -> None:
    stored = database()
    with stored.session() as session:
        session.add(Setting(key="unrelated", value={"keep": True}))
    CameraConfigurationStore(stored).save(sample())
    with stored.session() as session:
        kept = session.get(Setting, "unrelated")
        document = session.get(Setting, CAMERA_CONFIGURATION_KEY)
    assert kept is not None and kept.value == {"keep": True}
    assert document is not None
    assert "track_id" not in str(document.value)


def test_zones_are_not_stored_with_a_track() -> None:
    stored = database()
    CameraConfigurationStore(stored).save(sample())
    with stored.session() as session:
        document = session.get(Setting, CAMERA_CONFIGURATION_KEY)
    assert document is not None
    assert set(document.value) == {"version", "camera", "detection"}
    assert "track" not in str(document.value)


def test_invalid_values_are_rejected() -> None:
    with pytest.raises(ValidationError):
        StoredCamera(device_index=-1)
    with pytest.raises(ValidationError):
        StoredCamera(width=0)
    with pytest.raises(ValidationError):
        StoredCamera(height=0)
    with pytest.raises(ValidationError):
        StoredCamera(fps=0)
    with pytest.raises(ValidationError):
        StoredCamera(device_index=True)
    with pytest.raises(ValidationError):
        StoredDetectionZone(
            position_id=" ",
            lane=1,
            roi=NormalizedRoi(x=0, y=0, width=1, height=1),
        )
    with pytest.raises(ValidationError):
        StoredDetectionZone(
            position_id="start",
            lane=0,
            roi=NormalizedRoi(x=0, y=0, width=1, height=1),
        )
    with pytest.raises(ValidationError):
        NormalizedRoi(x=-0.1, y=0, width=0.2, height=0.2)
    with pytest.raises(ValidationError):
        NormalizedRoi(x=0, y=0, width=0, height=0.2)
    with pytest.raises(ValidationError):
        NormalizedRoi(x=0.8, y=0, width=0.3, height=0.2)
    with pytest.raises(ValidationError):
        StoredDetection(
            zones=(
                zone("start_finish", 1, y=0),
                zone("start_finish", 1, y=0.5),
            )
        )
    with pytest.raises(ValidationError):
        CameraConfiguration(version=2)


def test_a_broken_document_is_reported_and_does_not_crash() -> None:
    stored = database()
    with stored.session() as session:
        session.add(Setting(key=CAMERA_CONFIGURATION_KEY, value={"version": 99}))
    store = CameraConfigurationStore(stored)
    with pytest.raises(CameraConfigurationError):
        store.load()

    factory = CameraTimingFactory(configurations=store, devices=lambda _camera: _Idle())
    availability = factory.availability()
    assert not availability.available
    assert availability.reason_key == "error.timing_provider.camera_configuration_invalid"
    with pytest.raises(ProviderConfigurationError) as caught:
        factory.validate(spec_for(("start_finish",), {"start_finish": "sensor-sf"}))
    assert caught.value.key == "error.timing_provider.camera_configuration_invalid"


def test_normalized_zones_become_the_same_pixels_every_time() -> None:
    roi = NormalizedRoi(x=0.25, y=0.5, width=0.125, height=0.25)
    pixels = DetectionRoi(20, 20, 10, 10)
    assert roi_to_pixels(roi, WIDTH, HEIGHT) == pixels
    assert roi_to_pixels(roi, WIDTH, HEIGHT) == pixels
    full = NormalizedRoi(x=0, y=0, width=1, height=1)
    assert roi_to_pixels(full, 640, 480) == DetectionRoi(0, 0, 640, 480)
    drawn = pixels_to_roi(64, 120, 128, 48, 640, 480)
    assert drawn == NormalizedRoi(x=0.1, y=0.25, width=0.2, height=0.1)
    assert roi_to_pixels(drawn, 640, 480) == DetectionRoi(64, 120, 128, 48)

    settings = to_detector_settings(sample())
    assert settings is not None
    assert [(item.position_id, item.lane, item.roi) for item in settings.zones] == [
        ("sector_1", 1, DetectionRoi(20, 20, 10, 10)),
        ("start_finish", 2, DetectionRoi(20, 0, 10, 10)),
        ("start_finish", 1, DetectionRoi(20, 0, 10, 10)),
        ("sector_2", 3, DetectionRoi(20, 10, 10, 10)),
    ]


def test_a_race_without_zones_is_refused() -> None:
    store = CameraConfigurationStore(database())
    factory = CameraTimingFactory(ManualFrameSource(), configurations=store)
    session = spec_for(("start_finish",), {"start_finish": "sensor-sf"})
    with pytest.raises(ProviderConfigurationError) as caught:
        factory.create_source(session)
    assert caught.value.key == "error.timing_provider.camera_zones_missing"
    with pytest.raises(ProviderConfigurationError):
        factory.validate(session)


def test_the_factory_uses_the_saved_zones_for_every_track() -> None:
    stored = database()
    configuration = CameraConfiguration(
        camera=StoredCamera(device_index=1, width=WIDTH, height=HEIGHT, fps=15),
        detection=StoredDetection(
            zones=(
                zone("start_finish", 1, y=0),
                zone("sector_1", 2, y=0.5),
            )
        ),
    )
    CameraConfigurationStore(stored).save(configuration)
    seen: list[CameraConfig] = []

    def devices(camera: CameraConfig) -> _Idle:
        seen.append(camera)
        return _Idle()

    opened = CameraTimingFactory(devices=devices, configurations=CameraConfigurationStore(stored))
    session = _session()
    source = opened.create_source(session)
    assert not source.is_running
    assert seen == [CameraConfig(device_index=1, width=WIDTH, height=HEIGHT, fps=15)]

    frames = ManualFrameSource()
    guard = _Guard(CameraConfigurationStore(stored))
    factory = CameraTimingFactory(frames, background=_blank(), configurations=guard)
    first = factory.create_source(replace(session, track_id=TrackId(1)))
    second = factory.create_source(replace(session, track_id=TrackId(2)))
    guard.closed = True
    received: list[SensorTriggered] = []
    first.start(received.append)
    frames.submit(_car(2), 1_000)
    first.poll()
    frames.submit(_car(22), 2_000)
    second.start(received.append)
    second.poll()
    assert [(event.position_id, event.lane, event.timestamp_ns) for event in received] == [
        ("start_finish", 1, 1_000),
        ("sector_1", 2, 2_000),
    ]
    assert CameraConfigurationStore(stored).load() == configuration


def test_a_saved_zone_for_an_unknown_position_does_not_change_the_document() -> None:
    stored = database()
    CameraConfigurationStore(stored).save(sample())
    factory = CameraTimingFactory(
        ManualFrameSource(), configurations=CameraConfigurationStore(stored)
    )
    with pytest.raises(ProviderConfigurationError) as caught:
        factory.validate(spec_for(("start_finish",), {"start_finish": "sensor-sf"}))
    assert caught.value.key == "error.timing_provider.camera_position_unknown"
    assert CameraConfigurationStore(stored).load() == sample()


class _Idle:
    def open(self) -> None:
        raise AssertionError("device opened while building the source")

    def read(self) -> GrayFrame:
        raise AssertionError("read")

    def close(self) -> None:
        return None


class _Guard:
    def __init__(self, inner: CameraConfigurationStore) -> None:
        self._inner = inner
        self.closed = False

    def load(self) -> CameraConfiguration:
        if self.closed:
            raise AssertionError("persistence read while the source is running")
        return self._inner.load()


def _blank() -> GrayFrame:
    return GrayFrame.blank(WIDTH, HEIGHT)


def _car(y: int) -> GrayFrame:
    return _blank().paint(DetectionRoi(22, y, 8, 6), 255)


def _session() -> TimingSessionSpec:
    return spec_for(
        ("start_finish", "sector_1"),
        {"start_finish": "sensor-sf", "sector_1": "sensor-s1"},
        (1, 2),
    )
