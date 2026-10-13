"""German texts of the graphical part editor."""

from __future__ import annotations

GRAPHIC_TRANSLATIONS: dict[str, str] = {
    "planner.gfx.tab.params": "Parameter",
    "planner.gfx.tab.draw": "Zeichnen",
    "planner.gfx.tool.select": "Auswählen",
    "planner.gfx.tool.select.tip": "Elemente anklicken; erneutes Klicken wechselt bei Überlagerung",
    "planner.gfx.tool.move": "Verschieben",
    "planner.gfx.tool.move.tip": "Punkte, Verbinder oder ganze Linien mit der Maus ziehen",
    "planner.gfx.tool.line": "Linie",
    "planner.gfx.tool.line.tip": "Gerade Segmente zeichnen; Rechtsklick oder Esc beendet",
    "planner.gfx.tool.arc": "Bogen",
    "planner.gfx.tool.arc.tip": "Kreisbogen durch Start, Zwischenpunkt und Ende",
    "planner.gfx.tool.bezier": "Bézier",
    "planner.gfx.tool.bezier.tip": "Kubische Bézierkurve; Kontrollpunkte danach ziehen",
    "planner.gfx.tool.add_point": "Punkt +",
    "planner.gfx.tool.add_point.tip": "Einen Punkt auf einem Segment einfügen",
    "planner.gfx.tool.remove_point": "Punkt -",
    "planner.gfx.tool.remove_point.tip": "Punkt entfernen oder Kontrollpunkt zurücksetzen",
    "planner.gfx.tool.split": "Teilen",
    "planner.gfx.tool.split.tip": "Segment teilen oder die Linie an einem Punkt trennen",
    "planner.gfx.tool.delete_segment": "Segment löschen",
    "planner.gfx.tool.delete_segment.tip": "Das angeklickte Segment entfernen",
    "planner.gfx.tool.connector": "Verbinder",
    "planner.gfx.tool.connector.tip": "Schienenverbinder verschieben und drehen",
    "planner.gfx.tool.edge": "Randanschluss",
    "planner.gfx.tool.edge.tip": "Randkanten und Anschlusskanten bearbeiten",
    "planner.gfx.tool.contour": "Außenkontur",
    "planner.gfx.tool.contour.tip": "Außenkonturen bearbeiten",
    "planner.gfx.undo": "Rückgängig",
    "planner.gfx.redo": "Wiederholen",
    "planner.gfx.fit": "Ansicht einpassen",
    "planner.gfx.draw_on": "Zeichnen auf",
    "planner.gfx.layer.groove": "Spur",
    "planner.gfx.layer.center": "Mittellinie",
    "planner.gfx.layer.contour": "Kontur",
    "planner.gfx.layer.edge": "Randkante",
    "planner.gfx.layer.attach": "Anschlusskante",
    "planner.gfx.snap.grid": "Raster",
    "planner.gfx.snap.points": "Punkte",
    "planner.gfx.snap.tangent": "Tangente",
    "planner.gfx.snap.angle": "Winkel",
    "planner.gfx.elements": "Elemente",
    "planner.gfx.element": "{name}: {count} Segmente, {length} mm",
    "planner.gfx.connector": "{kind} {name}: Richtung {direction}°, Spuren {lanes}",
    "planner.gfx.rail": "Schienenverbinder",
    "planner.gfx.joint_other": "Sonstiger Verbinder",
    "planner.gfx.selection": "Auswahl",
    "planner.gfx.nothing": "Nichts ausgewählt.",
    "planner.gfx.segment": "Segment",
    "planner.gfx.sel.connector": "Schienenverbinder {name}, Richtung {direction}°",
    "planner.gfx.shape.line": "Linie",
    "planner.gfx.shape.arc": "Bogen",
    "planner.gfx.shape.bezier": "Bézier",
    "planner.gfx.handle": "Punkt",
    "planner.gfx.handle.start": "Anfang",
    "planner.gfx.handle.end": "Ende",
    "planner.gfx.handle.center": "Mittelpunkt",
    "planner.gfx.handle.mid": "Bogenpunkt",
    "planner.gfx.handle.p0": "P0 (Anfang)",
    "planner.gfx.handle.p1": "P1 (Kontrollpunkt)",
    "planner.gfx.handle.p2": "P2 (Kontrollpunkt)",
    "planner.gfx.handle.p3": "P3 (Ende)",
    "planner.gfx.line_length": "Länge (mm)",
    "planner.gfx.line_angle": "Richtung (°)",
    "planner.gfx.arc_start": "Startwinkel (°)",
    "planner.gfx.arc_sweep": "Öffnungswinkel (°)",
    "planner.gfx.tangent": "Übergang",
    "planner.gfx.tangent.kink": "Knick",
    "planner.gfx.tangent.smooth": "Glatt (gleiche Richtung)",
    "planner.gfx.tangent.symmetric": "Symmetrisch",
    "planner.gfx.conn_x": "Verbinder X",
    "planner.gfx.conn_y": "Verbinder Y",
    "planner.gfx.conn_dir": "Verbinder Richtung (°)",
    "planner.gfx.conn_lanes": "Verbinder Spuren",
    "planner.gfx.lane_number": "Spurnummer",
    "planner.gfx.to_bezier": "In Bézier umwandeln",
    "planner.gfx.to_line": "In Linie umwandeln",
    "planner.gfx.flip_arc": "Bogen umdrehen",
    "planner.gfx.reset_handle": "Kontrollpunkt zurücksetzen",
    "planner.gfx.lanes": "Spuren",
    "planner.gfx.lane_add": "Spur hinzufügen",
    "planner.gfx.lane_remove": "Spur entfernen",
    "planner.gfx.lane_length": "Spur {lane}: {length}",
    "planner.gfx.route_simple": "Durchgehende Strecke ohne Verzweigung.",
    "planner.gfx.route_open": (
        "Die Spuren bilden keine eindeutige durchgehende Strecke. "
        "Das Bauteil kann so nicht gespeichert werden."
    ),
    "planner.gfx.paths": "Linien und Verbinder",
    "planner.gfx.path_reverse": "Richtung umkehren",
    "planner.gfx.path_join": "Mit nächster Linie verbinden",
    "planner.gfx.gap_close": "Lücken schließen",
    "planner.gfx.conn_add": "Verbinder hinzufügen",
    "planner.gfx.conn_remove": "Verbinder entfernen",
    "planner.gfx.contours": "Außenkontur",
    "planner.gfx.contour_from_outline": "Kontur aus Umriss",
    "planner.gfx.contour_close": "Kontur schließen",
    "planner.gfx.hole_note": (
        "Mehrere Konturen: Die erste geschlossene Kontur bestimmt die Fläche. "
        "Weitere Konturen werden gespeichert, aber nicht aus der Fläche ausgeschnitten."
    ),
    "planner.gfx.strip_bezier_note": (
        "Bézier-Anschlusskanten werden für den Randstreifen in kurze Geraden zerlegt."
    ),
    "planner.gfx.borders": "Rand und Anschluss",
    "planner.gfx.edges_add": "Seitenkanten für Randstreifen anlegen",
    "planner.gfx.border_convert": "In individuellen Randstreifen umwandeln",
    "planner.gfx.border_convert_confirm": (
        "Der Standard-Randstreifen wird in einen individuellen Randstreifen mit eigener "
        "Anschlusskante und Kontur umgewandelt. Er passt danach nur noch auf Bauteile mit "
        "genau dieser Kante. Fortfahren?"
    ),
    "planner.gfx.strip_width": "Streifenbreite (mm)",
    "planner.gfx.strip_side": "Streifen liegt",
    "planner.gfx.strip_left": "links der Kante",
    "planner.gfx.strip_right": "rechts der Kante",
    "planner.gfx.strip_make": "Streifenkontur an der Kante zeichnen",
    "planner.gfx.host": "Anschlusskante von Bauteil übernehmen",
    "planner.gfx.copy_edge": "Kante übernehmen",
    "planner.gfx.compat": "Passende Bauteile",
    "planner.gfx.fits_host": "Passt auf {name} ({slot})",
    "planner.gfx.fits_border": "Randstreifen {name} passt ({slot})",
    "planner.gfx.problems": "Prüfung",
    "planner.gfx.problems_none": "Keine Probleme. Das Bauteil kann gespeichert werden.",
    "planner.gfx.problems_count": "{count} Problem(e) verhindern das Speichern.",
    "planner.gfx.read_only": "Dieses Bauteil kann hier nicht gezeichnet werden.",
    "planner.gfx.reason.corrupt": (
        "Die gespeicherte Geometrie ist beschädigt. Sie wird angezeigt, aber nicht verändert."
    ),
    "planner.gfx.reason.category": (
        "Weichen, Spurwechsel, Kreuzungen und Pitlane-Teile haben verzweigende Strecken. "
        "Der Planer kennt keine Weichenlogik, deshalb wird ihre Geometrie hier nicht "
        "gezeichnet. Bezeichnung und Texte bleiben änderbar."
    ),
    "planner.gfx.reason.branch": (
        "Eine Spur dieses Bauteils verzweigt. Es wird angezeigt, aber nicht gezeichnet."
    ),
    "planner.gfx.reason.standard_border": (
        "Dies ist ein Standard-Randstreifen. Zum Zeichnen muss er zuerst in einen "
        "individuellen Randstreifen umgewandelt werden."
    ),
    "planner.gfx.reason.end_piece": (
        "Endstücke haben keine eigene Anschlusskante und werden hier nicht gezeichnet."
    ),
    "planner.gfx.reason.params": (
        "Die Parameter sind noch ungültig. Bitte zuerst im Reiter Parameter korrigieren."
    ),
    "planner.gfx.locked_by_drawing": (
        "Die Geometrie wurde im Reiter Zeichnen bearbeitet. Hier sind nur noch Bezeichnung "
        "und Texte änderbar. Mit Zurück zur Parameterform kann eine einfache Form wieder "
        "übernommen werden."
    ),
    "planner.gfx.reparametrize": "Zurück zur Parameterform …",
    "planner.gfx.reparametrize_confirm": (
        "Die gezeichnete Geometrie wird durch die einfache Parameterform ersetzt, die genau "
        "dieses Bauteil beschreibt. Gezeichnete Kontrollpunkte gehen dabei verloren. "
        "Fortfahren?"
    ),
    "error.planner.editor.arc_points": "Die drei Punkte liegen auf einer Linie.",
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
    "error.planner.editor.remove_point": "Dieser Punkt kann nicht entfernt werden.",
}
