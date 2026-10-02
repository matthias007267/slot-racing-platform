# 0004 `TimingSource` pushes standard events to a sink

## Context
Cars are detected by a simulator, a camera, Raspberry Pi sensors or Carrera hardware. The race
engine must not know which.

## Decision
`carrera.core.timing.TimingSource` has `source_id`, `is_running`, `start(sink)` and `stop()`.
Detections are reported as `SensorTriggered(source_id, sensor_id, lane, timestamp_ns)` to the
sink. The race engine receives these events from the bus and maps `sensor_id` to timing points
via a `TimingLayout` (START_FINISH first, then SECTOR_1..n). The engine starts and stops the
sources it is given and keeps racing if a source fails to start.

`SimulationTimingProvider` is the first implementation. It uses virtual time and is advanced
explicitly, so full races run instantly and deterministically in tests.

## Consequences
- Camera and Pi modules only have to implement this interface and register as services.
- The future configuration wizard will produce a `TimingLayout` plus a sensor/lane mapping.
- Missed sensors are not compensated yet; unexpected sensors are ignored.
