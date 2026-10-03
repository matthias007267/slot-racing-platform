# 0013 The race HUD is a versioned layout document

## Context
The live race page needs a motorsport display that a user can rearrange: which panels are
shown, where they sit and how large they are. The same arrangement should later fit a monitor,
a television or a second race screen, including a fullscreen window that does not exist yet.

Hard-coding that grid in the live widget would make every layout change a code change. A new
table would repeat the global `settings` key/value store. Putting the document in `AppConfig`
would teach the core about race panels. The shell cannot import the races module, so the
editor cannot be a widget the settings page constructs itself.

The race clock, ranking and standings already exist on `RaceSnapshot` and the race events.
The HUD must not grow a second clock or a second ranking.

## Decision
- One active layout under `settings['ui.race_hud.configuration']`, owned by the races module.
  No new table and no migration. The document has a `version` and a `name` (today always
  `"standard"`). A later version can hold several named layouts; this version does not.
- Each widget stores `id`, `visible`, `x`, `y`, `width`, `height` and `z_index`. The rectangle
  is a fraction of the display, from 0 to 1, not a pixel size. `canvas` records the reference
  aspect (16 by 9) and is not the pixel resolution. Pixels are computed when a stage is drawn.
- Overlap is allowed. Higher `z_index` is painted later. The editor can raise or lower the
  selected widget. There is no layer tree.
- A missing row, a document that fails to parse, or an unknown `version` falls back to the
  built-in layout. One broken widget is skipped. Missing known widgets are filled from that
  layout. Unknown ids are preserved. None of this crashes the application.
- The settings page learns a `SettingsSection` contribution (id, title, order, factory). The
  races plugin registers the HUD editor. The shell never imports the races package. Disabling
  the plugin removes the section with the rest of that plugin's contributions.
- The live stage only places panels. Race time is `RaceSnapshot.elapsed_ns`. Ranking, laps and
  status come from the same snapshot the previous live view used. Pause, resume and abort go
  through `RaceRunner`.

## Consequences
- One saved layout is what the next program start and the open live view use. The editor and
  the live stage share the store, so saving relayouts a race that is already on screen.
- A future fullscreen window can host the same stage and the same document. It does not need
  a second coordinate system.
- Adding a named-layout library means a new document version and a reader for version 1.
  The SQLite schema does not need a revision for that.
- The race engine, the timing providers and the recorder do not know the HUD exists.
