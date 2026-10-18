from __future__ import annotations

import logging
from typing import ClassVar

from slot_racing.core.catalog import TrackCatalog
from slot_racing.core.events import RaceFinished, RaceStarted, TimingDecision
from slot_racing.core.plugin import NavigationItem, Plugin, PluginContext, PluginManifest
from slot_racing.core.storage import Database
from slot_racing.core.timing import TimingSetupService
from slot_racing.modules.timing_sensor.devices import attach_remembered_serial, list_serial_devices
from slot_racing.modules.timing_sensor.link import SensorLink
from slot_racing.modules.timing_sensor.provider import SensorTimingFactory
from slot_racing.modules.timing_sensor.store import (
    SensorConfigurationError,
    SensorConfigurationStore,
)

logger = logging.getLogger(__name__)


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
            "sensor.mode.serial": "USB",
            "sensor.protocol.note": (
                "PSF/1 ist das Textprotokoll für Simulator und USB: Ereignisse, Kennung, "
                "Status, Fehler und Uhrensynchronisation. Die Gerätezeit steht in "
                "Nanosekunden; das ist keine Genauigkeitszusage."
            ),
            "sensor.field.current": "Aktueller Betrieb",
            "sensor.field.source": "Quelle",
            "sensor.field.mode": "Betriebsart",
            "sensor.field.port": "COM-Port",
            "sensor.field.baud": "Baudrate",
            "sensor.field.identity": "Gerät",
            "sensor.field.quality": "Synchronisationsqualität",
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
            "sensor.action.save": "Zuordnung speichern",
            "sensor.action.open_timing": "Timing einrichten",
            "sensor.action.open_tracks": "Strecke anlegen",
            "sensor.action.apply_mapping": "Zeitzuordnung setzen",
            "sensor.action.clear_mapping": "Zeitzuordnung verwerfen",
            "sensor.action.inject": "Meldung einspeisen",
            "sensor.action.connect": "Verbinden",
            "sensor.action.ports": "COM-Ports suchen",
            "sensor.action.sync": "Uhr erneut synchronisieren",
            "sensor.line.placeholder": "PSF/1 sitzung 1 0 sensor-a",
            "sensor.saved": "Zuordnung gespeichert.",
            "sensor.developer": "Entwickler",
            "sensor.field.offset": "Offset (ns)",
            "sensor.field.numerator": "Zähler",
            "sensor.field.denominator": "Nenner",
            "sensor.field.line": "Protokollzeile",
            "sensor.log.title": "Ereignisprotokoll",
            "sensor.position.choose": "Messpunkt wählen",
            "sensor.position.start_finish": "Start/Ziel",
            "sensor.position.sector": "Sektor {number}",
            "sensor.lane.value": "Spur {lane}",
            "sensor.track.inactive": "inaktiv",
            "sensor.hint.no_catalog": (
                "Die Streckenverwaltung ist nicht aktiv. Vorhandene Strecken können "
                "deshalb nicht geladen werden."
            ),
            "sensor.hint.no_tracks": (
                "Noch keine Strecke vorhanden. Zum Anlegen genügen ein Name und die Spurzahl."
            ),
            "sensor.hint.no_setup": (
                "Für „{name}“ ist noch keine Timing-Konfiguration mit Start/Ziel gespeichert."
            ),
            "sensor.hint.position_missing": "Für Sensor {sensor} ist kein Messpunkt gewählt.",
            "sensor.hint.lane_missing": "Für Sensor {sensor} ist keine Spur gewählt.",
            "sensor.hint.unsaved": "Die Zuordnung ist geändert und noch nicht gespeichert.",
            "sensor.hint.sync_started": "Die Uhrensynchronisation wurde gestartet.",
            "sensor.error.timing_navigation": (
                "Die Timing-Konfiguration kann von hier nicht geöffnet werden."
            ),
            "sensor.mapping.applied": "Zeitzuordnung für die Simulation gesetzt.",
            "sensor.mapping.stored": (
                "Entwicklerwerte gespeichert. Sie ersetzen die gemessene USB-Zeitzuordnung nicht."
            ),
            "sensor.hint.device_ambiguous": (
                "Mehrere USB-Geräte passen zur letzten Verbindung. Es wird keines ausgewählt."
            ),
            "sensor.hint.device_absent": (
                "Das zuletzt verbundene USB-Gerät ist nicht angeschlossen."
            ),
            "sensor.action.export": "Diagnose exportieren",
            "sensor.export.title": "Sensordiagnose speichern",
            "sensor.export.saved": "Sensordiagnose gespeichert.",
            "sensor.export.failed": "Die Sensordiagnose konnte nicht gespeichert werden.",
            "sensor.mapping.cleared": "Zeitzuordnung verworfen. Die Erfassung ist gesperrt.",
            "sensor.error.no_track": "Keine Strecke ausgewählt.",
            "sensor.error.configuration": (
                "Die Sensoreinstellungen sind ungültig und wurden nicht gespeichert."
            ),
            "sensor.error.mapping": "Zeitzuordnung ungültig: {detail}",
            "sensor.error.not_simulation": "Simulierte Meldungen nur im Simulatorbetrieb.",
            "sensor.error.not_open": "Der Simulator ist nicht geöffnet.",
            "sensor.error.port": "Kein COM-Port ausgewählt.",
            "sensor.error.serial": "Die serielle Verbindung ist fehlgeschlagen: {detail}",
            "sensor.error.race_holds_reader": (
                "Ein Rennen benutzt den Leser. Die Verbindung bleibt, bis das Rennen endet."
            ),
            "sensor.error.sync": "Die Uhrensynchronisation braucht den verbundenen USB-Leser.",
            "sensor.connected_serial": (
                "USB-Leser geöffnet. Das Board setzt sich beim Öffnen zurück."
            ),
            "sensor.already_open": "Der USB-Leser ist bereits verbunden.",
            "sensor.quality.none": "Noch keine gültige Messung",
            "sensor.quality.value": (
                "{samples} Messungen, Unsicherheit {uncertainty} ns, Gang {numerator}/{denominator}"
            ),
            "sensor.identity.none": "Kein Gerät erkannt",
            "sensor.observation.accepted": "Empfangen",
            "sensor.delivery.received": "Empfangen",
            "sensor.delivery.published": "Veröffentlicht",
            "sensor.delivery.lap": "Runde abgeschlossen",
            "sensor.engine.started": "Von der Engine empfangen: Rundenstart",
            "sensor.engine.sector": "Von der Engine empfangen: Sektor, keine volle Runde",
            "sensor.engine.completed": "Runde abgeschlossen",
            "sensor.engine.invalid_timestamp": "Von der Engine abgelehnt: Zeit vor dem Rennstart",
            "sensor.engine.wrong_position": "Von der Engine abgelehnt: falscher Messpunkt",
            "sensor.engine.duplicate": "Von der Engine abgelehnt: gleiche Durchfahrt zu schnell",
            "sensor.engine.unknown_lane": "Von der Engine abgelehnt: unbekannte Spur",
            "sensor.engine.stale_assignment": "Von der Engine abgelehnt: Zeit vor der Zuordnung",
            "sensor.engine.paused": "Von der Engine abgelehnt: Pause",
            "sensor.engine.race_not_running": "Von der Engine abgelehnt: Rennen läuft nicht",
            "sensor.engine.reset": "Von der Engine abgelehnt: Rücksetzen",
            "sensor.engine.no_participant": "Von der Engine abgelehnt: Spur ist fertig",
            "sensor.observation.duplicate": "Duplikat",
            "sensor.observation.gap": "Lücke ({gap})",
            "sensor.observation.restart": "Neustart",
            "sensor.observation.parse_error": "Ungültige Meldung",
            "sensor.observation.unassigned": "Nicht zugeordnet",
            "sensor.observation.unsynchronized": "Ohne Zeitzuordnung",
            "sensor.observation.clock_error": "Zeit nicht umrechenbar",
            "sensor.observation.ignored": "Spur nicht im Rennen",
            "sensor.observation.withheld": "Nicht an das Rennen übergeben: {detail}",
            "sensor.withheld.restart": "neue Gerätesitzung",
            "sensor.withheld.time_fault": "Geräteuhr verloren",
            "sensor.withheld.clock": "Geräteuhr sprang zurück",
            "sensor.withheld.disconnected": "Verbindung verloren",
            "sensor.withheld.handshake_timeout": "Handshake abgelaufen",
            "sensor.withheld.muted": "Messung unterbrochen",
            "sensor.observation.hello": "Gerät erkannt",
            "sensor.observation.status": "Status {detail}",
            "sensor.observation.rollover": "Zeitüberlauf erweitert",
            "sensor.observation.buffer": "Pufferüberlauf ({detail})",
            "sensor.observation.time_fault": "Geräteuhr verloren",
            "sensor.observation.protocol": "Protokollfehler",
            "sensor.observation.sync": "Zeittausch {detail}",
            "sensor.observation.disconnect": "Verbindung verloren",
            "sensor.observation.handshake": "Handshake: {detail}",
            "sensor.observation.partial": "Unvollständige Zeile",
            "sensor.observation.host_overflow": "Hostpuffer voll",
            "error.timing_provider.sensor_time_unsynchronized": (
                "Die Sensor-Zeitmessung hat keine gültige Zeitzuordnung."
            ),
            "error.timing_provider.sensor_time_unmeasured": (
                "Die Sensor-Zeitmessung hat keine gemessene USB-Zeitzuordnung."
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
        self._remember_device(link, store)
        self._watch_race(context, link)
        context.register_timing_provider(
            SensorTimingFactory(link, store, lambda: context.find_service(TrackCatalog))
        )

        def sensor_page() -> object:
            from slot_racing.modules.timing_sensor.ui.page import SensorTimingPage

            return SensorTimingPage(
                context.translator,
                link,
                store,
                lambda: context.find_service(TimingSetupService),
                lambda: context.find_service(TrackCatalog),
            )

        context.add_navigation(
            NavigationItem(
                id="timing",
                title_key="nav.sensor_timing",
                order=50,
                page_factory=sensor_page,
            )
        )

    def _remember_device(self, link: SensorLink, store: SensorConfigurationStore) -> None:
        """Open a uniquely recognised USB device. A failure leaves the link closed."""
        try:
            configuration = store.load()
        except SensorConfigurationError:
            return
        try:
            attach_remembered_serial(link, configuration, list_serial_devices(), hold=True)
        except Exception:
            logger.exception("Remembered sensor port was not opened")

    def _watch_race(self, context: PluginContext, link: SensorLink) -> None:
        """Copy race mode and engine decisions onto the link. The race rules stay in the engine."""

        def started(event: RaceStarted) -> None:
            link.note_race_started(
                int(event.race_id),
                event.mode,
                event.timing_provider,
                event.timestamp_ns,
            )

        def finished(event: RaceFinished) -> None:
            link.note_race_finished(int(event.race_id))

        def decision(event: TimingDecision) -> None:
            link.note_timing_decision(
                timestamp_ns=event.timestamp_ns,
                source_id=event.source_id,
                position_id=event.position_id,
                lane=event.lane,
                result=event.result,
                race_start_ns=event.race_start_ns,
            )

        context.events.subscribe(RaceStarted, started)
        context.events.subscribe(RaceFinished, finished)
        context.events.subscribe(TimingDecision, decision)

    def deactivate(self) -> None:
        link = getattr(self, "_link", None)
        self._link = None
        if link is not None:
            link.force_close()
