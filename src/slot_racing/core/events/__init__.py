"""Typed, immutable events and the in-process event bus."""

from slot_racing.core.events.base import Event
from slot_racing.core.events.bus import EventBus, EventDispatcher, Subscription
from slot_racing.core.events.types import (
    HeatAttemptDiscarded,
    LapCompleted,
    LapStarted,
    MeasurementsReset,
    PersistentStoreReplacing,
    PluginDisabled,
    PluginEnabled,
    RaceFinished,
    RacePaused,
    RaceResumed,
    RaceStarted,
    RaceStarting,
    SectorCompleted,
    SensorTriggered,
    TimingDecision,
    WinnerDetermined,
)

__all__ = [
    "Event",
    "EventBus",
    "EventDispatcher",
    "HeatAttemptDiscarded",
    "LapCompleted",
    "LapStarted",
    "MeasurementsReset",
    "PersistentStoreReplacing",
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
    "TimingDecision",
    "WinnerDetermined",
]
