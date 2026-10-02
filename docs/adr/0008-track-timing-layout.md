# 0008 Track timing layout: logical positions, sensors and setups

## Context
Races used a fixed layout (`start_finish, sector_1, sector_2`). Real tracks have different
numbers of timing points, and later providers (camera, Raspberry Pi) must work with the same
configuration and deliver the same events. The existing ADRs already require that the engine is
independent of providers and hardware and that modules talk through core interfaces and events.
They do not say where the layout lives or how sensors relate to positions, so the smallest
extension of the existing models was chosen.

## Decision
- **Logical positions vs. sensors.** `TimingPosition` (type `START_FINISH` or `SECTOR`, order,
  stable id) is what the engine and the UI reason about. `TimingSensor` (id, position, name,
  optional `hardware_id`, active) is a device assigned to exactly one position. The hardware id is
  an opaque string for providers and is never the position.
- **Core domain.** `TimingLayout`, `TimingSensor` and `TimingSetup` validate themselves in
  `carrera.core.domain` (one start/finish, first; unique ids and orders; unique sensor and
  hardware ids; one sensor per position; inactive sensors are not usable). The sector count is not
  limited. They replace `TimingPoint` and `SensorRole`.
- **Event.** `SensorTriggered` carries `sensor_id` and `position_id`. The engine matches
  `position_id`.
- **Session spec.** `TimingSessionSpec` carries the `TimingSetup`, lanes, laps and optional race and
  track id. Providers receive everything through it; the simulation has no built-in layout.
- **Storage.** `TimingSetupService` is a core interface implemented by the `timing` module, which
  owns the tables. The existing `timing_configurations` and `timing_sensors` are extended and
  `timing_positions` is added in migration `0003` (non-destructive). `TimingConfiguration` becomes
  unique per track.
- **Fallback.** Tracks without a stored setup use `default_timing_setup()` (the former fixed
  layout), so existing tracks and races continue to work unchanged.
- **UI.** The track page gets a timing configuration, a six step wizard and a test mode. They edit
  a Qt-free `TimingDraft` and display the output of a `TimingTestSession`, which uses a
  `ManuallyTriggerable` simulation source. No race logic is computed in the UI.

## Consequences
- A camera or Raspberry Pi module registers a `TimingSourceFactory` and uses `spec.setup`
  (hardware ids, active flags) to configure itself; engine, UI and schema stay unchanged.
- Changing a layout after races were recorded does not alter stored results; races store their
  own laps and sector times.
- One sensor per position keeps the model simple; redundant sensors per position would need an
  extension of `TimingSetup`.
- The tracks module (UI) uses `timing` only through core interfaces and degrades to a read-only
  message when the timing module is disabled.
