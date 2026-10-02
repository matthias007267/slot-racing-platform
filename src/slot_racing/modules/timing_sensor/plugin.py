from __future__ import annotations

from typing import ClassVar

from slot_racing.core.plugin import Plugin, PluginManifest


class SensorTimingPlugin(Plugin):
    manifest: ClassVar[PluginManifest] = PluginManifest(
        name="timing_sensor",
        version="0.1.0",
        title="Sensor timing",
        enabled_by_default=False,
    )
    translations: ClassVar[dict[str, dict[str, str]]] = {
        "de": {"plugin.timing_sensor.title": "Sensor-Zeitmessung (Raspberry Pi)"}
    }
