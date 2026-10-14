# Bodenplatte unter Windows prüfen

Diese Liste ist die manuelle Abnahme der grafischen Bodenplatte. Sie wurde in dieser
Änderung **nicht** auf einem Windows-Rechner ausgeführt. Die gleichen Abläufe sind unter
Linux mit pytest-qt automatisiert (siehe unten). Ein automatisierter Lauf ersetzt die
Prüfung mit echter Maus, echtem Monitor und der Windows-Qt-Plattform nicht.

## Manuell

1. Anwendung starten.
2. Bauteilbibliothek öffnen.
3. Neues Bauteil anlegen. Bezeichnung und Artikelnummer eintragen.
4. Reiter **Grafisch** öffnen. Der Reiter **Parameter** bleibt unverändert erreichbar.
5. **Bodenplatte** wählen. Die späteren Elemente (Fahrspur, Mittellinie, Start-/Ziellinie,
   Randstück, Schienenanschluss, Randanschluss) sind deaktiviert.
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

## Automatisiert, nicht manuell

Unter Linux, Offscreen-Qt, in `tests/modules/test_plate_session.py` und
`tests/modules/test_plate_editor.py`:

- Vier Klicks, Schließen, erwartete Kontur.
- Vorschau wird nicht gespeichert.
- Numerische Koordinate und Undo.
- Speichern und erneutes Öffnen mit denselben Koordinaten.
- Treffer nach Zoom und Verschieben.
- Zwei Punkte, Nullsegment, offene Kontur, Selbstüberschneidung, ungültige Koordinaten.
- Carrera-Gerade und Bézier-Bauteil bleiben unverändert, auch nach dem Reiterwechsel.
- Ein verwendetes Bauteil ohne Änderung erzeugt keine Revision.
- Undo und Redo über mehrere Schritte.
- Leere Fläche, Abbrechen, Moduswechsel, wiederholtes Öffnen.

Nicht automatisiert und noch offen: die Schritte 1–14 auf Windows mit sichtbarem Fenster.
