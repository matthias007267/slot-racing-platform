from __future__ import annotations

from typing import ClassVar

from slot_racing.core.catalog import TrackCatalog
from slot_racing.core.plugin import NavigationItem, Plugin, PluginContext, PluginManifest
from slot_racing.core.storage import Database
from slot_racing.modules.timing_camera.lease import CameraLease
from slot_racing.modules.timing_camera.preview import CameraPreview
from slot_racing.modules.timing_camera.provider import CameraTimingFactory
from slot_racing.modules.timing_camera.store import CameraConfigurationStore


class CameraTimingPlugin(Plugin):
    manifest: ClassVar[PluginManifest] = PluginManifest(
        name="timing_camera",
        version="0.5.0",
        title="Camera timing",
        enabled_by_default=False,
    )
    translations: ClassVar[dict[str, dict[str, str]]] = {
        "de": {
            "plugin.timing_camera.title": "Kamera-Zeitmessung",
            "timing.provider.camera": "Kamera",
            "nav.camera_setup": "Kamera-Timing",
            "camera.heading": "Kamera-Timing",
            "camera.section.camera": "Kamera",
            "camera.section.zones": "Zonen",
            "camera.field.device": "Gerät",
            "camera.field.resolution": "Auflösung",
            "camera.field.fps": "FPS",
            "camera.field.position": "Position",
            "camera.field.lane": "Lane",
            "camera.device": "Kamera {index}",
            "camera.resolution": "{width} × {height}",  # noqa: RUF001
            "camera.fps": "{fps}",
            "camera.action.add": "+ Zone hinzufügen",
            "camera.action.delete": "Zone löschen",
            "camera.action.refresh": "Kamera aktualisieren",
            "camera.action.cancel": "Abbrechen",
            "camera.action.save": "Speichern",
            "camera.status.live": "Live",
            "camera.status.offline": "Kamera nicht verfügbar",
            "camera.status.offline_detail": "Kamera konnte nicht geöffnet werden.",
            "camera.status.busy": "Die Kamera wird gerade für ein Rennen verwendet.",
            "camera.status.dirty": "Änderungen nicht gespeichert",
            "camera.status.saved": "Konfiguration gespeichert.",
            "camera.status.draw": "Ziehe ein Rechteck im Bild.",
            "camera.status.too_small": "Die Zone ist zu klein.",
            "camera.status.too_many_zones": (
                "Für diese Bahnanzahl sind keine weiteren Zonen möglich."
            ),
            "camera.status.incomplete": "Jede Zone braucht eine Position.",
            "camera.status.invalid": "Die Konfiguration ist ungültig.",
            "camera.status.save_failed": "Die Konfiguration konnte nicht gespeichert werden.",
            "camera.zone.incomplete": "Neue Zone",
            "camera.zone.label": "{position} – Lane {lane}",  # noqa: RUF001
            "camera.zone.number": "Zone {number}",
            "error.timing_provider.camera_not_connected": "Es ist keine Kamera angeschlossen.",
            "error.timing_provider.camera_in_use": "Die Kamera wird gerade verwendet.",
            "error.timing_provider.camera_position_unknown": (
                "Die Kamera überwacht die Position „{position}“, "
                "die im Timing-Setup keinen aktiven Sensor hat."
            ),
            "error.timing_provider.camera_zones_missing": (
                "Die Kamera hat noch keine Erkennungszonen. "
                "Lege die Zonen fest, bevor ein Rennen mit der Kamera gestartet wird."
            ),
            "error.timing_provider.camera_configuration_invalid": (
                "Die gespeicherte Kamerakonfiguration kann nicht gelesen werden."
            ),
            "error.database": (
                "Die Datenbank konnte die Aktion nicht ausführen. Bitte versuchen Sie es erneut."
            ),
            "error.unexpected": "Unerwarteter Fehler: {detail}",
        }
    }

    def activate(self, context: PluginContext) -> None:
        store = CameraConfigurationStore(context.get_service(Database))
        lease = CameraLease()
        preview = CameraPreview(lease)
        translator = context.translator
        context.register_timing_provider(CameraTimingFactory(configurations=store, lease=lease))

        def camera_page() -> object:
            from slot_racing.modules.timing_camera.ui.page import CameraSetupPage, zone_limit

            tracks = context.find_service(TrackCatalog)

            def lane_limit() -> int:
                if tracks is None:
                    return zone_limit(())
                return zone_limit(
                    track.lane_count for track in tracks.list_tracks(active_only=True)
                )

            return CameraSetupPage(translator, store, preview, lane_limit=lane_limit)

        context.add_navigation(
            NavigationItem(
                id="camera_setup",
                title_key="nav.camera_setup",
                order=55,
                page_factory=camera_page,
            )
        )
