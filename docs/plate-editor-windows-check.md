# Bodenplatte und Fahrspuren unter Windows prüfen

Diese Liste ist die manuelle Abnahme der grafischen Bodenplatte und der Fahrspuren. Sie
wurde in dieser Änderung **nicht** auf einem Windows-Rechner ausgeführt. Die gleichen
Abläufe sind unter Linux mit pytest-qt automatisiert (siehe unten). Ein automatisierter
Lauf ersetzt die Prüfung mit echter Maus, echtem Monitor und der Windows-Qt-Plattform nicht.

## Manuell — Bodenplatte

1. Anwendung starten.
2. Bauteilbibliothek öffnen.
3. Neues Bauteil anlegen. Bezeichnung und Artikelnummer eintragen.
4. Reiter **Grafisch** öffnen. Der Reiter **Parameter** bleibt unverändert erreichbar.
5. **Bodenplatte** wählen. **Fahrspur** ist ebenfalls wählbar. Mittellinie,
   Start-/Ziellinie, Randstück, Schienenanschluss und Randanschluss bleiben deaktiviert.
6. Vier Punkte setzen, zum Beispiel (0, 0), (345, 0), (345, 200), (0, 200). Währenddessen
   muss eine gestrichelte Vorschau vom letzten Punkt zur Maus erscheinen.
7. Kontur schließen: Anfangspunkt anklicken oder **Kontur schließen**. Die Fläche ist
   gefüllt. Eine zweite Bodenplatte lässt sich nicht daneben zeichnen.
8. **Auswahl** wählen und einen Eckpunkt anklicken, auch nach Mausrad-Zoom und nach
   Verschieben mit der mittleren Maustaste.
9. X oder Y in der rechten Leiste ändern. Die Fläche folgt sofort.
10. **Rückgängig** und **Wiederholen** prüfen: Punkt, Koordinate, Schließen und Ersetzen.
11. Speichern. Der Entwurf bleibt in der Bibliothek.
12. Dialog schließen.
13. Denselben Entwurf erneut öffnen, Reiter **Grafisch**.
14. Dieselben vier Eckpunkte und die geschlossene Fläche prüfen.

Zusätzlich: eine Carrera-Gerade öffnen, beide Reiter ansehen, ohne Änderung schließen.
Die Geometrie darf sich nicht ändern, und der Streckenplaner darf den Entwurf nicht als
platzierbares Bauteil anbieten.

## Manuell — Fahrspuren

Diese Schritte setzen eine geschlossene Bodenplatte voraus. Sie wurden ebenfalls **nicht**
manuell ausgeführt.

1. Erste Fahrspur zeichnen: **Fahrspur**, **Fahrspur hinzufügen**, mehrere Linksklicks,
   **Fahrspur abschließen**. Die Spur bleibt offen und wird nicht zum Anfang geschlossen.
2. Zweite Fahrspur zeichnen. Beide Spuren bleiben sichtbar, die aktive ist hervorgehoben.
3. Fahrspur auswählen: **Auswahl**, dann einen Punkt oder ein Segment der gewünschten Spur.
4. Punkt numerisch ändern: X und Y in Millimetern, drei Nachkommastellen.
5. Segment in Kreisbogen umwandeln: Segment wählen, **Kreisbogen**.
6. Radius ändern. Ein Radius unter der halben Sehne wird abgelehnt, der Bogen bleibt.
7. Drehrichtung ändern: **Rechts** oder **Links**. **Kurzer Bogen** und **Langer Bogen**
   unterscheiden die beiden Bögen mit demselben Radius.
8. Undo/Redo ausführen. Die Bodenplatte bleibt dabei unverändert.
9. Entwurf speichern.
10. Entwurf erneut öffnen.
11. Beide Fahrspuren und den Kreisbogen prüfen: dieselben Punkte, derselbe Radius, dieselbe
    Drehrichtung, dieselbe Spurlänge.

## Automatisiert, nicht manuell

Unter Linux, Offscreen-Qt, in `tests/modules/test_plate_session.py`,
`tests/modules/test_lane_session.py`, `tests/modules/test_plate_editor.py` und
`tests/modules/test_lane_editor.py`:

- Vier Klicks, Schließen, erwartete Kontur.
- Vorschau wird nicht gespeichert.
- Numerische Koordinate und Undo.
- Speichern und erneutes Öffnen mit denselben Koordinaten.
- Treffer nach Zoom und Verschieben.
- Zwei Punkte, Nullsegment, offene Kontur, Selbstüberschneidung, ungültige Koordinaten.
- Zwei unabhängige Fahrspuren, Abschluss ohne zusätzliches Segment.
- Gerade wird zum Kreisbogen; Radius und Drehrichtung; zu kleiner Radius bleibt liegen.
- Punkt verschieben, einschließlich eines abgelehnten Bogens, Undo ohne Änderung der Platte.
- Spurlängen für Gerade, Viertelkreis, Halbkreis und einen zusammengesetzten Pfad.
- Speichern und erneutes Öffnen von Platte, zwei Spuren und einem Bogen.
- Entwurf ohne Änderung erzeugt keine Revision; eine Änderung eines unbenutzten Entwurfs
  schreibt dieselbe Zeile fort.
- Carrera-Kurve, Weiche, Schikane und ein Bézier-Bauteil bleiben bei Textänderung
  geometrisch unverändert.
- Erster und letzter Punkt, Segment, Wechsel zwischen Bodenplatte und Fahrspur, Abbruch
  einer angefangenen Spur, wiederholtes Öffnen.

Nicht automatisiert und noch offen: die Bodenplatten-Schritte 1–14 und die
Fahrspur-Schritte 1–11 auf Windows mit sichtbarem Fenster.
