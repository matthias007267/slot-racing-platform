from __future__ import annotations

from typing import ClassVar

from slot_racing.core.catalog import TrackCatalog
from slot_racing.core.plugin import NavigationItem, Plugin, PluginContext, PluginManifest
from slot_racing.core.storage import Database
from slot_racing.core.timing import TimingSetupService
from slot_racing.modules.timing_sensor.link import SensorLink
from slot_racing.modules.timing_sensor.provider import SensorTimingFactory
from slot_racing.modules.timing_sensor.store import SensorConfigurationStore


class SensorTimingPlugin(Plugin):
    manifest: ClassVar[PluginManifest] = PluginManifest(
        name="timing_sensor",
        version="0.2.0",
        title="Sensor timing",
        optional=("timing",),
        enabled_by_default=False,
    )
    translations: ClassVar[dict[str, dict[str, str]]] = {
        "de": {
            "plugin.timing_sensor.title": "Sensor-Zeitmessung",
            "timing.provider.sensor": "Sensor",
            "nav.sensor_timing": "Sensor-Zeitmessung",
            "sensor.mode.simulation": "Simulation",
            "sensor.protocol.note": (
                "Vorläufiges Textformat PSF/1 für den Simulator. "
                "Das ist nicht das endgültige USB-Protokoll."
            ),
            "sensor.field.mode": "Betriebsart",
            "sensor.field.connection": "Verbindung",
            "sensor.field.sync": "Zeitzuordnung",
            "sensor.field.readiness": "Messbereitschaft",
            "sensor.field.track": "Strecke",
            "sensor.connection.open": "Verbunden",
            "sensor.connection.closed": "Getrennt",
            "sensor.sync.unsynchronized": "Keine Zeitzuordnung",
            "sensor.sync.synchronized": "Synchronisiert",
            "sensor.sync.lost": "Zeitzuordnung verloren",
            "sensor.readiness.ready": "Messbereit",
            "sensor.readiness.not_ready": "Nicht messbereit",
            "sensor.column.physical": "Sensor",
            "sensor.column.position": "Messpunkt",
            "sensor.column.lane": "Spur",
            "sensor.column.device_time": "Gerätezeit",
            "sensor.column.host_time": "Host-Zeit",
            "sensor.column.result": "Ergebnis",
            "sensor.action.add": "Hinzufügen",
            "sensor.action.remove": "Entfernen",
            "sensor.action.save": "Speichern",
            "sensor.action.apply_mapping": "Zeitzuordnung setzen",
            "sensor.action.clear_mapping": "Zeitzuordnung verwerfen",
            "sensor.action.inject": "Meldung einspeisen",
            "sensor.line.placeholder": "PSF/1 sitzung 1 0 sensor-a",
            "sensor.saved": "Zuordnung gespeichert.",
            "sensor.mapping.applied": "Zeitzuordnung übernommen.",
            "sensor.mapping.cleared": "Zeitzuordnung verworfen. Die Erfassung ist gesperrt.",
            "sensor.error.no_track": "Keine Strecke ausgewählt.",
            "sensor.error.mapping": "Zeitzuordnung ungültig: {detail}",
            "sensor.error.not_simulation": "Simulierte Meldungen nur im Simulatorbetrieb.",
            "sensor.error.not_open": "Der Simulator ist nicht geöffnet.",
            "sensor.observation.accepted": "Übernommen",
            "sensor.observation.duplicate": "Duplikat",
            "sensor.observation.gap": "Lücke ({gap})",
            "sensor.observation.restart": "Neustart",
            "sensor.observation.parse_error": "Ungültige Meldung",
            "sensor.observation.unassigned": "Nicht zugeordnet",
            "sensor.observation.unsynchronized": "Ohne Zeitzuordnung",
            "sensor.observation.clock_error": "Zeit nicht umrechenbar",
            "sensor.observation.ignored": "Spur nicht im Rennen",
            "error.timing_provider.sensor_time_unsynchronized": (
                "Die Sensor-Zeitmessung hat keine gültige Zeitzuordnung."
            ),
            "error.timing_provider.sensor_time_lost": (
                "Die Zeitzuordnung der Sensor-Zeitmessung ist verloren. "
                "Es werden keine Rennereignisse ausgegeben."
            ),
            "error.timing_provider.sensor_assignments_missing": (
                "Dieser Strecke sind keine physischen Sensoren zugeordnet."
            ),
            "error.timing_provider.sensor_position_unknown": (
                "Der Sensor „{physical}“ verweist auf den unbekannten Messpunkt „{position}“."
            ),
            "error.timing_provider.sensor_lane_invalid": (
                "Der Sensor „{physical}“ liegt auf der ungültigen Spur {lane}."
            ),
            "error.timing_provider.sensor_physical_duplicate": (
                "Die physische Sensor-ID „{physical}“ ist mehrfach zugeordnet."
            ),
            "error.timing_provider.sensor_role_duplicate": (
                "Messpunkt „{position}“ und Spur {lane} sind mehrfach zugeordnet."
            ),
            "error.timing_provider.sensor_start_finish_missing": (
                "Start/Ziel hat keinen physischen Sensor."
            ),
            "error.timing_provider.sensor_capacity": (
                "Das Gerät meldet höchstens {limit} Sensoren."
            ),
            "error.timing_provider.sensor_track_missing": (
                "Die Sensor-Zeitmessung braucht eine Strecke."
            ),
            "error.timing_provider.sensor_configuration_invalid": (
                "Die gespeicherte Sensorzuordnung ist ungültig."
            ),
            "error.timing_provider.sensor_busy": (
                "Die Sensor-Zeitmessung wird bereits von einem Rennen verwendet."
            ),
        }
    }

    def activate(self, context: PluginContext) -> None:
        store = SensorConfigurationStore(context.get_service(Database))
        link = SensorLink()
        self._link: SensorLink | None = link
        tracks = context.find_service(TrackCatalog)
        setups = context.find_service(TimingSetupService)
        context.register_timing_provider(SensorTimingFactory(link, store, tracks))

        def sensor_page() -> object:
            from slot_racing.modules.timing_sensor.ui.page import SensorTimingPage

            return SensorTimingPage(context.translator, link, store, setups, tracks)

        context.add_navigation(
            NavigationItem(
                id="sensor_timing",
                title_key="nav.sensor_timing",
                order=56,
                page_factory=sensor_page,
            )
        )

    def deactivate(self) -> None:
        link = getattr(self, "_link", None)
        self._link = None
        if link is not None:
            link.force_close()
