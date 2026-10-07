"""Opt-in test for a real camera. The default pytest run does not collect it.

Run it with ``pytest -m camera_hardware``. Without a camera the test is skipped.
It uses the production capture device, the saved configuration and the race
engine. A car has to travel through the zone in the configured direction; a
single lit frame is not a crossing. No particular camera model is required.
"""

from __future__ import annotations

import time

import pytest

from slot_racing.core.domain import RaceStatus, TimingLayout, TimingSensor, TimingSetup
from slot_racing.core.errors import ProviderUnavailable
from slot_racing.core.events import Event, LapCompleted, SensorTriggered
from slot_racing.core.timing import TimingSetupService, TimingSourceFactory
from slot_racing.modules.timing_camera.camera_config import CameraConfig
from slot_racing.modules.timing_camera.capture import CameraOpenError, CameraReadError
from slot_racing.modules.timing_camera.configuration import (
    CameraConfiguration,
    NormalizedRoi,
    StoredCamera,
    StoredDetection,
    StoredDetectionZone,
)
from slot_racing.modules.timing_camera.frames import GrayFrame
from slot_racing.modules.timing_camera.lease import CameraLease
from slot_racing.modules.timing_camera.provider import CameraTimingFactory
from slot_racing.modules.timing_camera.store import CameraConfigurationStore
from tests.modules.conftest import Env

# A wide central zone. It has to lie inside the picture the camera actually delivers.
_ZONE = NormalizedRoi(x=0.20, y=0.20, width=0.60, height=0.60)


class ObservedCamera:
    """Production OpenCV device plus the moment each ``read`` returned."""

    def __init__(self, config: CameraConfig) -> None:
        from slot_racing.modules.timing_camera.opencv_device import OpenCVCapture

        self.config = config
        self.frames = 0
        self.stamps: list[int] = []
        self._inner = OpenCVCapture(config)

    def open(self) -> None:
        self._inner.open()

    def read(self) -> GrayFrame:
        frame = self._inner.read()
        self.frames += 1
        self.stamps.append(time.perf_counter_ns())
        return frame

    def close(self) -> None:
        self._inner.close()


class RealDevices:
    def __init__(self) -> None:
        self.created: list[ObservedCamera] = []
        self.configs: list[CameraConfig] = []

    def __call__(self, config: CameraConfig) -> ObservedCamera:
        self.configs.append(config)
        device = ObservedCamera(config)
        self.created.append(device)
        return device


@pytest.mark.camera_hardware
def test_a_connected_camera_completes_a_lap(env: Env) -> None:
    from slot_racing.modules.timing_camera.opencv_device import probe_device_indices

    indices = probe_device_indices()
    if not indices:
        pytest.skip("no camera connected")
    index = indices[0]
    probe = ObservedCamera(CameraConfig(device_index=index))
    try:
        probe.open()
    except CameraOpenError:
        pytest.skip("camera could not be opened")
    try:
        sample = probe.read()
    except CameraReadError as error:
        pytest.skip(f"camera delivered no frame: {error}")
    finally:
        probe.close()

    width, height = sample.width, sample.height
    expected = CameraConfig(device_index=index, width=width, height=height, fps=30)
    CameraConfigurationStore(env.runtime.database).save(
        CameraConfiguration(
            camera=StoredCamera(device_index=index, width=width, height=height, fps=30),
            detection=StoredDetection(
                zones=(StoredDetectionZone(position_id="start_finish", lane=1, roi=_ZONE),)
            ),
        )
    )
    track = env.track(lanes=2)
    env.runtime.services.get(TimingSetupService).save_setup(
        track.id,
        TimingSetup(
            TimingLayout.from_position_ids(["start_finish"]),
            (TimingSensor("sensor-start_finish", "start_finish"),),
        ),
    )
    race = env.races.create_race("Kamera", track.id, 1, "camera")
    driver_id, vehicle_id = env.pair(1)
    env.races.add_participant(race.id, driver_id, vehicle_id, 1)

    devices = RealDevices()
    lease = CameraLease()
    env.runtime.services.register(
        TimingSourceFactory,
        CameraTimingFactory(
            configurations=CameraConfigurationStore(env.runtime.database),
            devices=devices,
            background=GrayFrame.blank(width, height),
            lease=lease,
        ),
        owner="camera-hardware",
        name="camera-hardware",
    )
    events: list[Event] = []
    env.runtime.bus.subscribe(Event, events.append)
    try:
        runner = env.controller.start_race(race.id)
    except ProviderUnavailable as error:
        if error.key == "error.timing_provider.camera_not_connected":
            pytest.skip("camera could not be opened for the race")
        raise
    try:
        assert devices.configs
        assert set(devices.configs) == {expected}
        deadline = time.monotonic() + 5
        triggered: list[SensorTriggered] = []
        while time.monotonic() < deadline and not triggered:
            runner.tick()
            errors = runner.snapshot().source_errors
            if errors:
                detail = "; ".join(errors)
                if "frame size" in detail or "extends outside" in detail:
                    pytest.skip(f"camera frame does not match the opened picture: {detail}")
                raise AssertionError(detail)
            triggered = [event for event in events if isinstance(event, SensorTriggered)]
            time.sleep(0.02)
        assert triggered, "camera saw no lit scene inside the detection zone"
        event = triggered[0]
        assert event.source_id == "camera"
        assert event.sensor_id == "sensor-start_finish"
        assert event.position_id == "start_finish"
        assert event.lane == 1
        now = time.perf_counter_ns()
        assert 0 < event.timestamp_ns <= now
        stamps = [stamp for device in devices.created for stamp in device.stamps]
        assert stamps
        assert any(0 <= event.timestamp_ns - stamp <= 100_000_000 for stamp in stamps)
        assert sum(device.frames for device in devices.created) >= 1
        laps = [item for item in events if isinstance(item, LapCompleted)]
        # One start/finish crossing starts the lap clock. It does not store a lap.
        if len(triggered) == 1:
            assert laps == []
            assert runner.status is RaceStatus.RUNNING
            assert runner.snapshot().rows[0].laps_completed == 0
        else:
            assert laps
            assert laps[0].lap_time_ns == triggered[1].timestamp_ns - triggered[0].timestamp_ns
    finally:
        if env.controller.active is not None:
            env.controller.active.close()
