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
        "planner.category.draft": "Entwurf",
        "planner.save_as": "Als neue Strecke speichern",
        "planner.save_as.title": "Neue Strecke",
        "planner.save_as.prompt": "Name der neuen Strecke",
        "planner.saved_as": "Die Strecke wurde als eigene Strecke gespeichert.",
        "planner.jump_start": "Zur Startgeraden springen",
        "planner.color_coding": "Farbcodierung",
        "planner.start_straight": "Startgerade",
        "planner.start_straight.missing": "Es ist keine Startgerade platziert.",
        "planner.missing_parts": (
            "{count} Bauteile dieser Strecke fehlen in der Bibliothek. "
            "Sie bleiben als Platzhalter erhalten und werden beim Speichern nicht gelöscht."
        ),
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
        "error.planner.start_straight": "Nur die Startgerade kann Start und Ziel sein.",
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
        "planner.stock.search": "Bezeichnung, Artikelnummer oder Kategorie",
        "planner.stock.hide_sets": "Sets/Packungen ausblenden",
        "planner.stock.reset": "Bibliothek zurücksetzen",
        "planner.stock.reset.title": "Bibliothek zurücksetzen?",
        "planner.stock.reset.intro": "Der mitgelieferte Katalog wird so abgeglichen:",
        "planner.stock.reset.added": "Ergänzt ({count})",
        "planner.stock.reset.updated": "Aktualisiert ({count})",
        "planner.stock.reset.removed": "Entfernt ({count})",
        "planner.stock.reset.retired": "Ausgemustert ({count})",
        "planner.stock.reset.keep": (
            "Eigenbauten bleiben. Bestände und Zuordnungen weiterhin vorhandener "
            "Bauteile bleiben an ihrer Kennung. Ein ausgemusterter Eintrag bleibt "
            "gespeichert, solange eine Sammlung, eine Packung oder eine Strecke "
            "ihn noch verwendet, und verschwindet aus der Auswahl. "
            "Rennen und andere unabhängige Daten bleiben."
        ),
        "planner.stock.reset.none": (
            "Die Bibliothek entspricht bereits dem mitgelieferten Katalog. Es ändert sich nichts."
        ),
        "planner.stock.reset.accept": "Zurücksetzen",
        "planner.stock.reset.cancel": "Abbrechen",
        "planner.stock.reset.done": "Die Bibliothek wurde auf den mitgelieferten Katalog gesetzt.",
        "planner.stock.reset_quantities": "Meine Bauteilesammlung zurücksetzen",
        "planner.stock.reset_quantities.title": "Bestandsmengen zurücksetzen?",
        "planner.stock.reset_quantities.confirm": (
            "Alle Bestandsmengen deiner Bauteilesammlung auf 0 zurücksetzen?"
        ),
        "planner.stock.reset_quantities.done": "Alle Bestandsmengen wurden auf 0 gesetzt.",
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
        "planner.editor.save": "Als Bauteil speichern",
        "planner.editor.draft": "Entwurf speichern",
        "planner.editor.cancel": "Abbrechen",
        "planner.editor.elements": "Elemente",
        "planner.editor.properties": "Eigenschaften",
        "planner.editor.metadata": "Bauteil",
        "planner.editor.tool.select": "Auswahl",
        "planner.editor.tool.line": "Linie",
        "planner.editor.tool.arc": "Kreisbogen",
        "planner.editor.tool.bezier": "Bézier",
        "planner.editor.tool.plate": "Bodenplatte",
        "planner.editor.tool.lane": "Fahrspur",
        "planner.editor.tool.joint": "Anschluss",
        "planner.editor.tool.start": "Start/Ziel",
        "planner.editor.tool.center": "Mittelmarkierung",
        "planner.editor.mark_color": "Markierungsfarbe",
        "planner.editor.mark_width": "Linienstärke (mm)",
        "planner.editor.mark_dashed": "Gestrichelt",
        "planner.editor.finish": "Pfad abschließen",
        "planner.editor.undo": "Rückgängig",
        "planner.editor.redo": "Wiederholen",
        "planner.editor.fit": "Alles anzeigen",
        "planner.editor.rotate": "Drehen",
        "planner.editor.mirror_x": "Spiegeln links/rechts",
        "planner.editor.mirror_y": "Spiegeln oben/unten",
        "planner.editor.duplicate": "Duplizieren",
        "planner.editor.delete": "Löschen",
        "planner.editor.visible": "Sichtbar",
        "planner.editor.lock": "Sperren",
        "planner.editor.grid": "Raster (mm)",
        "planner.editor.snap_grid": "Am Raster einrasten",
        "planner.editor.snap_points": "An Punkten einrasten",
        "planner.editor.x": "X (mm)",
        "planner.editor.y": "Y (mm)",
        "planner.editor.length": "Länge (mm)",
        "planner.editor.radius": "Radius (mm)",
        "planner.editor.sweep": "Winkel (°)",
        "planner.editor.direction_deg": "Richtung (°)",
        "planner.editor.lanes": "Spuren",
        "planner.editor.issue.plate": "Die Bodenplatte muss eine geschlossene Kontur sein.",
        "planner.editor.issue.geometry": "Die Geometrie dieses Elements ist ungültig.",
        "planner.editor.issue.lane": (
            "Eine Fahrspur ist unvollständig oder keinem Anschluss zugeordnet."
        ),
        "planner.editor.issue.joint": (
            "Ein Anschluss ist unvollständig oder passt nicht zu den Fahrspuren."
        ),
        "planner.editor.issue.ends": "Es sind mindestens zwei Anschlüsse nötig.",
        "planner.editor.issue.category": (
            "Bitte eine Kategorie wählen. Ein Entwurf ist noch kein verwendbares Bauteil."
        ),
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
            "Bezeichnung, Artikelnummer und Texte bleiben bearbeitbar. "
            "Kurven, Anschlüsse und Kanten bleiben erhalten."
        ),
        "planner.plate.tab.parameter": "Parameter",
        "planner.plate.tab.graphic": "Grafisch",
        "planner.plate.elements": "Elemente",
        "planner.plate.select": "Auswahl",
        "planner.plate.floor": "Bodenplatte",
        "planner.plate.lane": "Fahrspur",
        "planner.plate.center": "Mittellinie",
        "planner.plate.start": "Start-/Ziellinie",
        "planner.plate.border": "Randstück",
        "planner.plate.rail": "Schienenanschluss",
        "planner.plate.edge": "Randanschluss",
        "planner.plate.later": "Folgt in einer späteren Phase und ist noch nicht implementiert.",
        "planner.plate.close": "Kontur schließen",
        "planner.plate.replace": "Bodenplatte ersetzen",
        "planner.plate.undo": "Rückgängig",
        "planner.plate.redo": "Wiederholen",
        "planner.plate.fit": "Einpassen",
        "planner.plate.count": "Punkte",
        "planner.plate.state": "Kontur",
        "planner.plate.open_state": "Offen",
        "planner.plate.closed_state": "Geschlossen",
        "planner.plate.perimeter": "Umfang (mm)",
        "planner.plate.x": "X (mm)",
        "planner.plate.y": "Y (mm)",
        "planner.lane.add": "Fahrspur hinzufügen",
        "planner.lane.finish": "Fahrspur abschließen",
        "planner.lane.remove": "Fahrspur entfernen",
        "planner.lane.insert": "Punkt einfügen",
        "planner.lane.remove_point": "Punkt entfernen",
        "planner.lane.which": "Fahrspur",
        "planner.lane.length": "Spurlänge (mm)",
        "planner.lane.kind": "Segment",
        "planner.lane.line": "Gerade",
        "planner.lane.arc": "Kreisbogen",
        "planner.lane.radius": "Radius (mm)",
        "planner.lane.turn": "Drehrichtung",
        "planner.lane.right": "Rechts",
        "planner.lane.left": "Links",
        "planner.lane.size": "Bogen",
        "planner.lane.short": "Kurzer Bogen",
        "planner.lane.long": "Langer Bogen",
        "planner.lane.angle": "Winkel (°)",
        "planner.lane.segment": "Segmentlänge (mm)",
        "planner.lane.ends": "Endpunkte",
        "planner.lane.other": "Abstand zu anderer Fahrspur (mm)",
        "planner.lane.other_hint": (
            "Kürzester Abstand dieses Punkts zu einer anderen Fahrspur. "
            "Das ist kein fester Spurabstand."
        ),
        "planner.lane.edge": "Abstand zur Bodenkante (mm)",
        "planner.lane.edge_hint": "Kürzester Abstand dieses Punkts zur Kante der Bodenplatte.",
        "planner.lane.drawing": "Fahrspur {number} wird gezeichnet.",
        "planner.lane.editing": "Fahrspur {number} wird bearbeitet.",
        "planner.lane.plate_drawing": "Bodenplatte wird gezeichnet.",
        "planner.lane.plate_editing": "Bodenplatte wird bearbeitet.",
        "planner.plate.separate": (
            "Die Bodenplatte ist noch nicht gespeichert. "
            "Die Maße auf diesem Reiter beschreiben sie nicht."
        ),
        "planner.plate.locked": (
            "Diese Geometrie kann hier nicht punktweise bearbeitet werden. "
            "Bögen, Bézier-Kurven und Fahrspuren bleiben unverändert."
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
        "error.planner.plate.coords": "Die Koordinate ist keine gültige Zahl.",
        "error.planner.plate.zero": (
            "Zwei Punkte liegen aufeinander. Das Segment hätte keine Länge."
        ),
        "error.planner.plate.short": (
            "Eine Bodenplatte braucht mindestens drei unterschiedliche Eckpunkte."
        ),
        "error.planner.plate.open": (
            "Die Kontur ist noch offen. Schließen Sie sie über den Anfangspunkt."
        ),
        "error.planner.plate.degenerate": (
            "Die Fläche ist degeneriert und hat keinen nutzbaren Inhalt."
        ),
        "error.planner.plate.crossing": "Die Kanten der Bodenplatte schneiden sich.",
        "error.planner.plate.exists": (
            "Es gibt bereits eine Bodenplatte. Wählen Sie sie aus oder ersetzen Sie sie."
        ),
        "error.planner.plate.both": (
            "Parameter und Bodenplatte wurden beide geändert. "
            "Machen Sie eine der beiden Änderungen rückgängig, bevor Sie speichern."
        ),
        "error.planner.lane.radius": (
            "Der Radius ist kleiner als die halbe Sehne. Der Bogen bleibt unverändert."
        ),
        "error.planner.lane.zero": "Das Segment hätte keine Länge.",
        "error.planner.lane.open": "Eine Fahrspur ist noch nicht abgeschlossen.",
        "error.planner.lane.short": "Eine Fahrspur braucht mindestens eine Verbindung.",
        "error.planner.lane.limit": "Es sind höchstens vier Fahrspuren möglich.",
        "error.planner.lane.plate": "Zuerst muss die Bodenplatte geschlossen sein.",
        "error.planner.lane.idle": "Legen Sie zuerst eine Fahrspur an.",
        "error.planner.lane.done": (
            "Diese Fahrspur ist abgeschlossen. Wählen Sie sie aus oder legen Sie eine neue an."
        ),
        "error.planner.lane.outside": (
            "Ein Punkt der Fahrspur liegt außerhalb der Bodenplatte. "
            "Der Entwurf bleibt unvollständig."
        ),
        "error.planner.lane.gap": "Die Fahrspur hat eine Lücke.",
        "error.planner.lane.arc": "Der Kreisbogen ist so nicht bestimmt.",
    }
}
