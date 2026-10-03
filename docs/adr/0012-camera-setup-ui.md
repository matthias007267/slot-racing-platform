# 0012 Camera setup is a global page, separate from race timing

## Context
The camera configuration is one global document (ADR 0011). Someone has to edit the device,
the resolution, the frame rate and the detection zones without starting a race and without
picking a track. The rectangles are easiest to edit on the picture itself.

The page cannot live in the app shell: the shell does not import modules. It cannot live in
`tracks`: that would hide a camera setting inside one track, and modules do not import each
other. The camera package is the module that already owns the document. Its timing logic must
stay free of Qt, and the page must stay free of OpenCV and of the race engine.

There is no global catalog of positions and lanes. Those belong to a track's timing setup.
The camera zones are not tied to a track, so the page cannot offer a track's sensor list
without inventing that coupling.

A race and the setup page must not open the same USB camera at the same time. The race
controller does not know about the camera, and that stays so.

## Decision
- The camera plugin adds one navigation page, id `camera_setup`, title "Kamera-Timing",
  order 55. It is a global page. It is not a button inside track management. It is shown
  only while the camera plugin is enabled.
- The page lives in `timing_camera.ui`. The plugin is the composition root and creates the
  page lazily. Capture, detection, the configuration document, the store, the provider and
  the preview do not import PySide6 or `uikit` (import-linter). The page does not import
  `cv2`, `opencv_device` or the races module (import-linter).
- The live picture is a `CameraPreview`. It opens a `CameraFrameSource` and the page polls
  frames on a timer. It does not construct a `CameraTimingSource`, a `RaceEngine` or a
  `RaceController`, and it does not publish `SensorTriggered`. No race has to be running.
- The plugin owns one `CameraLease`. A race source acquires it as `race`, the preview as
  `preview`. The second request fails before the device is opened. `availability()` reports
  `camera_in_use` without probing while the lease is held. `RaceController` is unchanged.
- Leaving the page stops the preview and releases the device. The unsaved draft stays in the
  page widget. "Abbrechen" reloads the saved document. "Speichern" writes it through
  `CameraConfigurationStore` and no other store.
- Zones are drawn, moved and resized in pixels of the current frame, including the four
  corners. The page stores the same normalized `x`, `y`, `width` and `height` as ADR 0011,
  converted with `pixels_to_roi` and `roi_to_pixels`. A new zone starts with an empty
  `position_id` and cannot be saved until that id is a valid identifier. Lane is an integer
  of at least 1. Overlapping zones stay allowed. The page does not check them again.
- A missing camera, a camera already taken by a race, or a broken document is shown and does
  not discard the saved document. An explicit save of a valid document may replace a broken
  one.

## Consequences
- Configuring the camera does not require a track and does not start a race.
- The race engine remains provider-neutral. It still never imports the camera module.
- The setup page and a running camera race cannot hold the device together. Whichever
  opened it first keeps it until it releases the lease.
- Tests drive the page with a fake preview. They do not need a camera.
- The saved document format is unchanged. There is no new table and no migration.
