# Browser-Anzeige

Die Live-Anzeige für Zuschauer liegt auf dem Dashboard, im Bereich
**Live-Anzeige im Browser**. Dafür ist keine eigene Einstellungsseite nötig.

## Öffnen

1. Slot-Racing-Platform starten.
2. Auf dem Dashboard **Übertragung starten** wählen, falls der Status
   „Nicht gestartet“ ist. Die Wahl wird in der Konfiguration gespeichert
   (`live_broadcast_enabled`). Ein Neustart der Anwendung startet die
   Übertragung dann von selbst.
3. Den QR-Code mit der Kamera eines Smartphones, Tablets oder Computers
   scannen, oder **Im Browser öffnen** auf dem Renn-PC verwenden.
   **Link kopieren** legt dieselbe Adresse in die Zwischenablage.

Die Adresse hat die Form `http://<rechner>:<port>/live?token=<sitzung>`.
Der Token gehört zur laufenden Übertragung und steht nicht im Log.

## Geräte

Geeignet sind aktuelle Browser auf Smartphone, Tablet und Computer.
Das Gerät muss dasselbe lokale Netz verwenden wie der Renn-PC (LAN oder WLAN).
Ein Gastnetz, das Geräte voneinander trennt, reicht nicht.

## Status

| Anzeige | Bedeutung |
|---|---|
| Nicht gestartet | Die Übertragung ist aus. Es gibt keinen gültigen Link. |
| Wird gestartet | Der lokale Dienst öffnet den Port. |
| Bereit | QR-Code und Link gelten. Die Zahl nennt verbundene Browser. |
| Fehler | Der Dienst ist nicht erreichbar. Der Link wird nicht als gültig angezeigt. |
| Wird beendet | Der Dienst schließt den Port. |

Null verbundene Browser sind ein normaler Zustand. Das Rennen startet trotzdem.

## Startampel

Während der Startvorbereitung und des Countdowns ist die Ampel sichtbar.
Nach dem bestätigten Start (`start_committed`) verschwindet sie vollständig,
auch das Gehäuse. Die Live-Ansicht darunter bleibt stehen. Ein Browser, der
erst während des laufenden Rennens verbindet, sieht kein Ampelgehäuse.
Der nächste Durchlauf zeigt die Ampel für seine eigene Startsequenz wieder.
Nach Abbruch, Rennende und zwischen zwei Durchläufen bleibt sie aus.
Ein geplanter Zeitpunkt oder ein Verbindungsverlust startet das Rennen nicht.

## Wenn etwas nicht erreichbar ist

Der QR-Code verschwindet und der Link steht auf „Nicht verfügbar“, sobald der
Dienst aus ist oder nicht starten konnte. Die Meldung auf dem Dashboard nennt
den Grund, zum Beispiel einen belegten Port. Das Rennen auf dem Renn-PC läuft
weiter. **Erneut versuchen** startet den Dienst noch einmal.

## Wechsel zwischen Rennen

Dieselbe Adresse gilt für die ganze Sitzung. Nach einem Rennen zeigt der
Browser das abgeschlossene Ergebnis, bis das nächste Rennen beginnt oder die
Live-Ansicht geschlossen wird. Ohne laufende Szene steht dort
„Warte auf das nächste Rennen“. Zuschauer müssen den QR-Code nicht erneut
scannen. Es wird immer nur das eine Rennen übertragen, das der Renn-PC gerade
führt.
