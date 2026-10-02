from __future__ import annotations

from typing import ClassVar

from slot_racing.core.plugin import Plugin, PluginContext, PluginManifest
from slot_racing.modules.timing_camera.provider import CameraTimingFactory


class CameraTimingPlugin(Plugin):
    manifest: ClassVar[PluginManifest] = PluginManifest(
        name="timing_camera",
        version="0.2.0",
        title="Camera timing",
        enabled_by_default=False,
    )
    translations: ClassVar[dict[str, dict[str, str]]] = {
        "de": {
            "plugin.timing_camera.title": "Kamera-Zeitmessung",
            "timing.provider.camera": "Kamera",
            "error.timing_provider.camera_not_connected": "Es ist keine Kamera angeschlossen.",
            "error.timing_provider.camera_position_unknown": (
                "Die Kamera überwacht die Position „{position}“, "
                "die im Timing-Setup keinen aktiven Sensor hat."
            ),
        }
    }

    def activate(self, context: PluginContext) -> None:
        context.register_timing_provider(CameraTimingFactory())
