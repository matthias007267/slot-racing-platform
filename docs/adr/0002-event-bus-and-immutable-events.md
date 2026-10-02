# 0002 Synchronous in-process event bus with immutable events

## Context
Modules react to what happens in a race (audio, statistics, persistence) without the race engine
knowing them.

## Decision
- Events are frozen, slotted dataclasses derived from `Event` and live in `carrera.core.events`.
- `EventBus` delivers synchronously, in subscription order, to handlers of the event type or a
  base class.
- A handler exception is logged and swallowed; it never reaches the publisher or other handlers.
- Plugins subscribe through a scoped view so that subscriptions disappear with the plugin.

## Consequences
- Deterministic and trivially testable (no threads, no event loop).
- Handlers run in the publisher's thread. Hardware sources that use threads must marshal events
  into the bus thread themselves (to be solved in the sensor/camera modules, not in the core).
- Slow handlers slow down the publisher. If this becomes a problem a queued dispatcher can be
  added behind the same `EventDispatcher` protocol.
