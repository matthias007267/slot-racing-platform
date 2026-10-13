"""German texts of the track planner."""

from __future__ import annotations

TRANSLATIONS: dict[str, dict[str, str]] = {
    "de": {
        "plugin.track_planner.title": "Streckenplaner",
        "nav.track_planner": "Streckenplaner",
        "planner.track": "Strecke",
        "planner.lanes": "Bahnen: {count}",
        "planner.tool.straight_h": "Gerade horizontal",
        "planner.tool.straight_v": "Gerade vertikal",
        "planner.tool.curve": "90°-Kurve",
        "planner.tool.start": "Start/Ziel",
        "planner.tool.delete": "Entfernen",
        "planner.direction.clockwise": "Uhrzeigersinn",
        "planner.direction.counterclockwise": "Gegen den Uhrzeigersinn",
        "planner.save": "Speichern",
        "planner.undo": "Rückgängig",
        "planner.redo": "Wiederholen",
        "planner.selection.many": "{count} Bauteile",
        "planner.discard": "Verwerfen",
        "planner.reset": "Zurücksetzen",
        "planner.properties": "Eigenschaften",
        "planner.length.title": "Streckenlänge",
        "planner.length.lane": "Spur {lane}   {length}",
        "planner.field.x": "X",
        "planner.field.y": "Y",
        "planner.field.rotation": "Drehung",
        "planner.empty": "Keine Strecke vorhanden.",
        "planner.unsaved": "Bitte zuerst speichern oder verwerfen.",
        "planner.saved": "Streckenplan gespeichert.",
        "planner.discarded": "Änderungen verworfen.",
        "planner.reset_done": "Der Plan wurde geleert. Speichern übernimmt das.",
        "planner.none": "Kein Element ausgewählt.",
        "planner.kind.straight_h": "Horizontale Gerade",
        "planner.kind.straight_v": "Vertikale Gerade",
        "planner.kind.curve_90": "90°-Kurve",
        "planner.kind.start_finish": "Start/Ziel",
        "planner.kind.sensor": "Sensor",
        "planner.kind.camera": "Kamera",
        "planner.kind.timing_point": "Zeitmesspunkt",
        "planner.kind.sector": "Sektor",
        "error.planner.invalid": "Der gespeicherte Streckenplan kann nicht gelesen werden.",
        "error.planner.track_missing": "Die Strecke existiert nicht.",
        "error.planner.position": "Das Element liegt außerhalb der Arbeitsfläche.",
        "error.planner.direction": "Die Fahrtrichtung ist ungültig.",
        "error.planner.lane": "Die Bahn liegt außerhalb der Strecke.",
        "error.planner.piece": "Das Streckenelement ist ungültig.",
        "error.planner.part": "Das Bauteil ist ungültig.",
        "error.planner.geometry": "Die Bauteilgeometrie ist ungültig.",
        "error.planner.extend": "An dieser Stelle kann nicht weitergebaut werden.",
        "error.planner.part_exists": (
            "Ein Bauteil mit der Bezeichnung „{name}“ und der Artikelnummer "
            "„{article}“ existiert bereits."
        ),
        "error.planner.scale": "Der Maßstab muss 1:24, 1:32 oder 1:43 sein.",
        "planner.library": "Bauteilbibliothek",
        "planner.library.add": "Bauteil hinzufügen",
        "planner.library.place": "Auf die Strecke legen",
        "planner.field.article": "Artikelnummer",
        "planner.field.scale": "Maßstab",
        "planner.field.name": "Bezeichnung",
        "planner.field.category": "Kategorie",
        "planner.field.length": "Länge (mm)",
        "planner.field.width": "Breite (mm)",
        "planner.field.height": "Höhe (mm)",
        "planner.field.radius": "Radius (mm)",
        "planner.field.angle": "Winkel (°)",
        "planner.field.lanes": "Spuren",
        "planner.field.grid": "Raster",
        "planner.field.grid_size": "Raster (mm)",
        "planner.field.snap": "Andocken (mm)",
        "planner.field.rotation_free": "Drehung (°)",
        "planner.category.straight": "Gerade",
        "planner.category.curve": "Kurve",
        "planner.category.lane_change": "Spurwechsel",
        "planner.category.crossing": "Kreuzung",
        "planner.category.switch": "Weiche",
        "planner.category.pitlane": "Pitlane",
        "planner.category.special": "Sonderteil",
        "planner.category.border": "Randstreifen",
        "planner.accessory.menu": "Randstreifen",
        "planner.accessory.left": "Links",
        "planner.accessory.right": "Rechts",
        "planner.accessory.inner": "Innen",
        "planner.accessory.outer": "Außen",
        "planner.category.support": "Stütze",
        "planner.save_as": "Als neue Strecke speichern",
        "planner.save_as.title": "Neue Strecke",
        "planner.save_as.prompt": "Name der neuen Strecke",
        "planner.saved_as": "Die Strecke wurde als eigene Strecke gespeichert.",
        "planner.jump_start": "Zur Startgeraden springen",
        "planner.color_coding": "Farbcodierung",
        "planner.start_straight": "Startgerade",
        "planner.start_straight.missing": "Es ist keine Startgerade markiert.",
        "planner.filter.scale": "Maßstab",
        "planner.filter.all": "Alle",
        "planner.filter.compatible": "Nur kompatible Bauteile anzeigen",
        "planner.filter.reset": "Filter zurücksetzen",
        "planner.library.manage": "Bibliothek verwalten",
        "planner.library.delete": "Löschen",
        "planner.library.edit": "Bauteil bearbeiten",
        "planner.library.search": "Suchen",
        "planner.library.updated": "Bauteil gespeichert.",
        "planner.library.deleted": "Bauteil gelöscht.",
        "error.planner.part_in_use": (
            "Dieses Bauteil wird noch von {count} Platzierungen verwendet "
            "und kann nicht gelöscht werden."
        ),
        "error.planner.start_straight": "Nur die Anschlussgerade kann Start und Ziel sein.",
        "error.planner.stock": "Der Bestand muss eine ganze Zahl ab 0 sein.",
        "error.planner.stock_adjustment": (
            "Die Korrektur würde den physischen Bestand unter 0 setzen."
        ),
        "error.planner.package": "Die Packung ist ungültig.",
        "error.planner.package_exists": "Diese Packung gibt es schon.",
        "error.planner.package_in_use": (
            "Diese Packung hat noch einen Bestand und kann nicht gelöscht werden."
        ),
        "error.planner.part_in_package": (
            "Dieses Bauteil ist Inhalt einer Packung und kann nicht gelöscht werden."
        ),
        "planner.mode": "Baumodus",
        "planner.mode.unlimited": "Unbegrenzt",
        "planner.mode.collection": "Eigene Sammlung",
        "planner.stock.manage": "Meine Bauteilsammlung",
        "planner.stock.remaining": "Noch {count}",
        "planner.stock.short": "Fehlt {count}",
        "planner.stock.show_all": "Alle Bauteile anzeigen",
        "planner.stock.close": "Schließen",
        "planner.stock.saved": "Bestand gespeichert.",
        "planner.stock.notice.title": "Bauteilsammlung",
        "planner.stock.notice": (
            "Diese Strecke kann mit deiner aktuellen Bauteilsammlung "
            "nicht vollständig gebaut werden."
        ),
        "planner.stock.notice.detail": (
            "Nicht verfügbare Bauteile sind im Streckenplan rot markiert."
        ),
        "planner.stock.warning": "⚠ Strecke überschreitet deine Bauteilsammlung",
        "planner.stock.packages": "Packungen",
        "planner.stock.parts": "Physische Bauteile",
        "planner.stock.package_qty": "Packungen",
        "planner.stock.per_pack": "Je Packung",
        "planner.stock.contained": "Enthalten",
        "planner.stock.adjustment": "Korrektur",
        "planner.stock.derived_value": "Grundbestand: {count}",
        "planner.stock.physical_value": "Physischer Bestand: {count}",
        "planner.stock.used_value": "Verbaut: {count}",
        "planner.stock.available_value": "Verfügbar: {count}",
        "planner.stock.over": "{count} über Bestand",
        "planner.delete_track": "Strecke löschen",
        "planner.delete_track.title": "Strecke wirklich löschen?",
        "planner.delete_track.confirm": "„{name}“ wird dauerhaft gelöscht.",
        "planner.delete_track.done": "Die Strecke wurde gelöscht.",
        "planner.delete_track.cancel": "Abbrechen",
        "planner.delete_track.accept": "Löschen",
        "planner.library.duplicate": "Bauteil duplizieren",
        "planner.library.adopt": "Geometrie übernehmen",
        "planner.library.adopt_none": "Keine Strecke verwendet noch eine ältere Geometrie.",
        "planner.editor.save": "Speichern",
        "planner.editor.cancel": "Abbrechen",
        "planner.editor.preview": "Vorschau",
        "planner.editor.direction": "Richtung",
        "planner.editor.direction.right": "Rechts",
        "planner.editor.direction.left": "Links",
        "planner.editor.pitch": "Spurabstand (mm)",
        "planner.editor.offsets": "Spurpositionen (mm)",
        "planner.editor.offsets.hint": "-40, 40",
        "planner.editor.side": "Seite",
        "planner.editor.side.both": "Links und rechts",
        "planner.editor.side.left": "Links",
        "planner.editor.side.right": "Rechts",
        "planner.editor.side.inner": "Innen",
        "planner.editor.side.outer": "Außen",
        "planner.editor.manufacturer": "Hersteller",
        "planner.editor.description": "Beschreibung",
        "planner.editor.origin": "Herkunft",
        "planner.editor.origin.catalog": "Katalog",
        "planner.editor.origin.modified": "Geändert",
        "planner.editor.origin.custom": "Eigenbau",
        "planner.editor.locked": (
            "Diese Geometrie kann hier nicht verändert werden. "
            "Eine grafische Bearbeitung ist derzeit nicht verfügbar. "
            "Bezeichnung, Artikelnummer und Texte bleiben bearbeitbar. "
            "Kurven, Anschlüsse und Kanten bleiben erhalten."
        ),
        "planner.editor.corrupt": (
            "Die gespeicherte Geometrie ist beschädigt und wird nicht ersetzt. "
            "Texte können gespeichert werden."
        ),
        "planner.editor.kept": "Bestehende Strecken behalten zunächst die bisherige Geometrie.",
        "planner.editor.current": "Aktuelle Revision: {revision}",
        "planner.editor.adopt": "Neue Geometrie in die ausgewählten Strecken übernehmen",
        "planner.editor.adopt_hint": (
            "Nur die aktuelle Fassung der ausgewählten Strecke wird geändert. "
            "Archivierte Fassungen bleiben unverändert. Nachbarteile werden nicht verschoben."
        ),
        "planner.editor.later": "Später",
        "planner.editor.restore": "Original wiederherstellen",
        "planner.editor.restore_confirm": (
            "Die mitgelieferte Geometrie wird wiederhergestellt. "
            "Bestehende Strecken behalten zunächst die bisherige Geometrie."
        ),
        "planner.editor.discard": "Ungespeicherte Änderungen verwerfen?",
        "planner.editor.copy_name": "Kopie von {name}",
        "planner.editor.select": "Bitte zuerst ein Bauteil in der Bibliothek auswählen.",
        "planner.editor.shape": "Form",
        "planner.editor.host": "Grundform",
        "planner.stock.edit": "Bearbeiten",
        "error.planner.editor.name": (
            "Die Bezeichnung ist erforderlich und darf 120 Zeichen nicht überschreiten."
        ),
        "error.planner.editor.article": (
            "Die Artikelnummer ist erforderlich und darf 40 Zeichen nicht überschreiten."
        ),
        "error.planner.editor.manufacturer": "Der Hersteller darf 80 Zeichen nicht überschreiten.",
        "error.planner.editor.description": (
            "Die Beschreibung darf 500 Zeichen nicht überschreiten."
        ),
        "error.planner.editor.length": "Die Länge muss größer als 0 sein.",
        "error.planner.editor.radius": "Der Radius muss größer als 0 sein.",
        "error.planner.editor.angle": "Der Winkel muss größer als 0° und kleiner als 360° sein.",
        "error.planner.editor.width": "Die Breite muss größer als 0 sein und zur Kurve passen.",
        "error.planner.editor.pitch": "Der Spurabstand ist ungültig.",
        "error.planner.editor.offsets": "Die Spurpositionen müssen zur Spuranzahl passen.",
        "error.planner.editor.side": "Die Seite des Randstreifens ist ungültig.",
        "error.planner.editor.attach_edge": (
            "Die Anschlusskante fehlt oder gehört nicht zu diesem Bauteil."
        ),
        "error.planner.editor.branch": "Mehrere Rillen enden an derselben Spur eines Verbinders.",
        "error.planner.editor.connectors": "Es sind mindestens zwei Schienenverbinder nötig.",
        "error.planner.editor.contour": "Die Außenkontur ist nicht geschlossen oder zu kurz.",
        "error.planner.editor.contour_open": "Eine Kontur ist nicht geschlossen.",
        "error.planner.editor.direction": (
            "Eine Rille verlässt den Schienenverbinder nicht in dessen Richtung."
        ),
        "error.planner.editor.edge_slots": "Randkanten sind doppelt oder gemischt.",
        "error.planner.editor.gap": "Eine Linie hat eine Lücke zwischen zwei Segmenten.",
        "error.planner.editor.groove_end": "Eine Rille endet nicht an einem Schienenverbinder.",
        "error.planner.editor.lane_missing": "Eine Spur des Schienenverbinders hat keine Rille.",
        "error.planner.editor.lane_twice": "Eine Spur kommt mehrfach vor.",
        "error.planner.editor.no_lane": "Das Bauteil hat keine Spur.",
    }
}
