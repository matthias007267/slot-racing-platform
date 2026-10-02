"""Typed, immutable events and the in-process event bus."""

from slot_racing.core.events.base import Event
from slot_racing.core.events.bus import EventBus, EventDispatcher, Subscription
from slot_racing.core.events.types import (
    LapCompleted,
    LapStarted,
    PluginDisabled,
    PluginEnabled,
    RaceFinished,
    RacePaused,
    RaceResumed,
    RaceStarted,
    RaceStarting,
    SectorCompleted,
    SensorTriggered,
    WinnerDetermined,
)

__all__ = [
    "Event",
    "EventBus",
    "EventDispatcher",
    "LapCompleted",
    "LapStarted",
    "PluginDisabled",
    "PluginEnabled",
    "RaceFinished",
    "RacePaused",
    "RaceResumed",
    "RaceStarted",
    "RaceStarting",
    "SectorCompleted",
    "SensorTriggered",
    "Subscription",
    "WinnerDetermined",
]
