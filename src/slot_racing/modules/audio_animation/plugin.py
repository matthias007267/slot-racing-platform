from __future__ import annotations

from typing import ClassVar

from slot_racing.core.plugin import Plugin, PluginManifest


class AudioAnimationPlugin(Plugin):
    manifest: ClassVar[PluginManifest] = PluginManifest(
        name="audio_animation",
        version="0.1.0",
        title="Audio and animation",
    )
    translations: ClassVar[dict[str, dict[str, str]]] = {
        "de": {"plugin.audio_animation.title": "Audio und Animationen"}
    }
