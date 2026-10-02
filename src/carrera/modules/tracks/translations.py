from __future__ import annotations

from carrera.uikit import UIKIT_TRANSLATIONS

TRANSLATIONS: dict[str, dict[str, str]] = {
    "de": {
        **UIKIT_TRANSLATIONS["de"],
        "plugin.tracks.title": "Strecken",
        "nav.tracks": "Strecken",
        "track.dialog.new": "Neue Strecke",
        "track.dialog.edit": "Strecke bearbeiten",
        "track.field.name": "Name",
        "track.field.description": "Beschreibung",
        "track.field.lane_count": "Spuren ({minimum} bis {maximum})",
        "track.field.image": "Bild / Symbol",
        "track.column.lanes": "Spuren",
        "track.browse": "Durchsuchen …",
        "track.timing": "Timing-Konfiguration",
        "timing.title": "Timing-Konfiguration",
        "timing.title_for": "Timing-Konfiguration: {name}",
        "timing.back": "Zurück zur Streckenliste",
        "timing.position.start_finish": "Start/Ziel",
        "timing.position.sector": "Sektor {number}",
        "timing.type.start_finish": "Start/Ziel",
        "timing.type.sector": "Sektor",
        "timing.column.number": "Nr.",
        "timing.column.position": "Position",
        "timing.column.type": "Typ",
        "timing.column.sensor_id": "Sensor-ID",
        "timing.column.sensor_name": "Sensorname",
        "timing.column.hardware_id": "Hardware-ID",
        "timing.column.active": "Aktiv",
        "timing.add_position": "Position hinzufügen",
        "timing.remove_position": "Position entfernen",
        "timing.move_up": "Nach oben",
        "timing.move_down": "Nach unten",
        "timing.edit_sensor": "Position/Sensor bearbeiten",
        "timing.toggle_sensor": "Sensor aktiv/inaktiv",
        "timing.save": "Speichern",
        "timing.reset": "Auf Standard zurücksetzen",
        "timing.open_test": "Timing-Test",
        "timing.open_wizard": "Assistent",
        "timing.saved": "Timing-Konfiguration gespeichert.",
        "timing.saved_with_inactive": (
            "Gespeichert. Achtung: Mindestens ein Sensor ist inaktiv, "
            "die Konfiguration kann so nicht für ein Rennen verwendet werden."
        ),
        "timing.reset_done": "Die gespeicherte Konfiguration wurde entfernt.",
        "timing.confirm_reset": (
            "Die gespeicherte Timing-Konfiguration dieser Strecke wirklich entfernen "
            "und den Standard verwenden?"
        ),
        "timing.default_hint": (
            "Für diese Strecke ist noch keine Konfiguration gespeichert. Angezeigt wird das "
            "Standard-Layout (Start/Ziel und zwei Sektoren). Speichern Sie, um es zu übernehmen."
        ),
        "timing.service_missing": (
            "Die Zeitmessung ist nicht aktiv. Die Timing-Konfiguration kann nicht "
            "bearbeitet werden."
        ),
        "timing.dialog.position": "Position und Sensor",
        "timing.field.type": "Typ",
        "timing.field.position_name": "Anzeigename der Position",
        "timing.field.sensor_id": "Sensor-ID",
        "timing.field.sensor_name": "Sensorname",
        "timing.field.hardware_id": "Hardware-ID (optional)",
        "timing.hardware_hint": "z. B. GPIO 17, nur für spätere Geräte",
        "timing.sensor.active": "Sensor ist aktiv",
        "timing.test.title": "Timing-Test",
        "timing.test.hint": (
            "Es werden nur simulierte Ereignisse erzeugt. Es wird keine Hardware angesprochen."
        ),
        "timing.test.trigger": "Simulation auslösen",
        "timing.test.trigger_lap": "Ganze Runde simulieren",
        "timing.test.reset": "Zurücksetzen",
        "timing.test.column.number": "Nr.",
        "timing.test.column.time": "Zeit",
        "timing.test.column.sensor": "Sensor",
        "timing.test.column.position": "Position",
        "timing.test.count": "Anzahl Ereignisse",
        "timing.test.last_position": "Letzte Position",
        "timing.test.last_sensor": "Letzte Sensor-ID",
        "timing.test.last_time": "Zeitpunkt",
        "timing.test.detected_order": "Erkannte Reihenfolge",
        "timing.test.order_state": "Reihenfolge",
        "timing.test.order_ok": "Entspricht dem Layout",
        "timing.test.order_wrong": "Weicht vom Layout ab",
        "timing.wizard.title": "Assistent: Timing-Konfiguration",
        "timing.wizard.step_title": "Schritt {number} von {total}: {title}",
        "timing.wizard.step.track": "Strecke",
        "timing.wizard.step.positions": "Positionen",
        "timing.wizard.step.sensors": "Sensoren",
        "timing.wizard.step.order": "Reihenfolge",
        "timing.wizard.step.test": "Test",
        "timing.wizard.step.save": "Speichern",
        "timing.wizard.track_hint": "Für welche Strecke soll das Timing konfiguriert werden?",
        "timing.wizard.positions_hint": (
            "Legen Sie die Positionen fest. Start/Ziel ist immer vorhanden. "
            "Sektoren können beliebig hinzugefügt oder entfernt werden."
        ),
        "timing.wizard.sensors_hint": (
            "Weisen Sie jeder Position einen Sensor zu (Doppelklick oder Schaltfläche)."
        ),
        "timing.wizard.order_hint": (
            "Legen Sie die Reihenfolge fest, in der die Positionen durchfahren werden. "
            "Start/Ziel bleibt an erster Stelle."
        ),
        "timing.wizard.save_hint": "Kontrollieren Sie die Konfiguration und speichern Sie.",
        "timing.wizard.assign": "Sensor zuweisen …",
        "timing.wizard.default_ids": "Standard-IDs vergeben",
        "timing.wizard.cancel": "Abbrechen",
        "timing.wizard.back": "Zurück",
        "timing.wizard.next": "Weiter",
        "error.timing.start_finish_fixed": (
            "Start/Ziel ist immer vorhanden und steht an erster Stelle."
        ),
        "error.timing.test_no_source": "Es ist keine Simulation für den Test verfügbar.",
        "error.timing.test_not_simulatable": (
            "Die Zeitquelle kann im Test nicht simuliert werden."
        ),
        "error.timing.no_track": "Bitte wählen Sie eine Strecke.",
        "error.timing.service_missing": "Die Zeitmessung ist nicht aktiv.",
        "error.track.name.required": "Bitte geben Sie einen Namen für die Strecke ein.",
        "error.track.name.too_long": "Der Name darf höchstens {limit} Zeichen lang sein.",
        "error.track.lane_count": "Die Spurenzahl muss zwischen {minimum} und {maximum} liegen.",
        "error.track.image.too_long": "Der Bildpfad darf höchstens {limit} Zeichen lang sein.",
        "error.track.not_found": "Die Strecke existiert nicht mehr.",
        "error.track.in_use": (
            "Die Strecke wird in Rennen verwendet und kann nicht gelöscht werden. "
            "Deaktivieren Sie sie stattdessen."
        ),
    }
}
