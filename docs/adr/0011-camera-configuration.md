# 0011 Camera configuration is global and not tied to a track

## Context
The camera timing provider can open a device and detect lane crossings (ADR 0004, 0009).
The rectangles it watches have to survive a restart, and they have to come back for the next
race. A slot-racing layout usually sits still in front of one camera, so those rectangles
describe the camera, not whichever track happens to be selected for a race.

`timing_sensors.settings` is the wrong place. That JSON belongs to one sensor of one track's
timing layout. A camera watches several positions and lanes at once, and selecting another
track must not replace the zones. `AppConfig` is the shell configuration in the core; the core
must not learn the camera's types. A new table would only repeat the global `settings`
key/value store that already exists.

## Decision
- One document under `settings['timing_camera.configuration']`, owned by the camera module.
  No new table and no migration. Other settings rows are left untouched.
- The document has a `version`. It stores the requested device (`device_index`, `width`,
  `height`, `fps`) and the detection zones (`position_id`, `lane`, normalized roi). It has no
  `track_id`.
- Zone geometry is stored as fractions of the frame, from 0 to 1. Detection still runs on pixel
  rectangles. Those pixels are computed from the saved resolution when the race session is
  created, not later by the capture thread.
- `CameraTimingFactory` reads the document while it builds a `TimingSource` and passes that
  snapshot in. The capture thread does not open the database or the configuration file.
- An empty zone list is valid stored data and is not filled with a placeholder zone. A race
  that uses the camera cannot start until zones exist (`ProviderConfigurationError`). A missing
  or broken document does not crash the application.
- Injected frame sources, devices and zones in tests bypass the saved document. The race engine
  and `TimingSessionSpec` do not learn about the camera.

## Consequences
- The last saved zones are what the next program start and the next camera race use, whichever
  track that race is on.
- A race whose timing setup has no active sensor for a saved `position_id` is rejected. The
  saved zones stay as they are.
- Pixel rectangles follow the saved resolution. If a driver delivers a different size, that is
  still the step-3 behavior: the request is not treated as the actual picture.
- A future document format bumps `version` and is rejected by this code until a migration of
  the document is written. The SQLite schema does not need a revision for that.
- The setup page that edits this document is a separate decision (ADR 0012). It writes the same
  document and does not add a track id.
