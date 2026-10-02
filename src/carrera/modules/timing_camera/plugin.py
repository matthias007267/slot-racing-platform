from __future__ import annotations

from typing import ClassVar

from carrera.core.plugin import Plugin, PluginManifest


class CameraTimingPlugin(Plugin):
    manifest: ClassVar[PluginManifest] = PluginManifest(
        name="timing_camera",
        version="0.1.0",
        title="Camera timing",
        enabled_by_default=False,
    )
    translations: ClassVar[dict[str, dict[str, str]]] = {
        "de": {"plugin.timing_camera.title": "Kamera-Zeitmessung"}
    }
