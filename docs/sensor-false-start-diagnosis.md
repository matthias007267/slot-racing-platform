# Frühstart-Diagnose der Sensorzeitmessung

Diese Anleitung gilt, wenn die Plattform Auslösungen nach „alle Lampen aus“ noch als Frühstart zählt.
Sie braucht keine Firmwareänderung. Der Export enthält seit dieser Version alles, was nötig ist, um die
Ursache zu belegen. Die Firmware meldet jetzt `1.1.0`, damit der Export zeigt, ob die Platine `PSF/1 ARM`
kennt. Ein erneutes Hochladen ist dafür nicht Pflicht; ohne Upload meldet der Export `arm: false`, wenn
die alte Version `1.0.0` läuft.

## Messablauf

1. Nur die Plattform mit der Platine verbinden. Den seriellen Monitor der Arduino IDE geschlossen lassen.
2. Zwei Minuten verbunden warten, damit das Fenster der Zeitabgleiche voll ist.
3. Ein Rennen oder einen Durchlauf starten. Beim Erlöschen der Lampen mit der Hand einmal durch die
   Lichtschranke fahren, möglichst genau beim Erlöschen und notieren, was der Bildschirm zeigte.
4. Den Start mit Handdurchfahrt mindestens vier Mal wiederholen, verteilt über etwa fünf Minuten.
5. Auf der Sensor-Seite den Diagnoseexport speichern.

## Was im Export zu lesen ist

| Feld | Bedeutung |
| --- | --- |
| `build.code_id` | Hash des Python-Codes, der den Export geschrieben hat. |
| `firmware.version`, `firmware.capabilities.arm` | Von der Platine gemeldete Version und ob sie `ARM` kennt. |
| `connection.clock.rate_status` | `fitted`: Steigung der Platinenuhr wird verwendet. `span_too_short`: Fenster zu kurz. `skew_rejected`: Die Steigung weicht um mehr als 5 % ab und gilt als unplausibel. `fit_inconsistent`: Eine Messung liegt nicht innerhalb ihrer Rundlaufgrenze auf der angepassten Geraden. In beiden Fällen gilt Rate 1/1, und die Zuordnung driftet. |
| `connection.clock.raw_skew_ppm` | Gemessene Abweichung (Host- über Platinenzeit) in ppm. Positiv heißt: Die Platinenuhr läuft langsam. |
| `connection.sync_log` | Jeder Zeitabgleich: Anfrage-Id, Sitzung, Sende- und Empfangszeit am Host, Empfangs- und Sendezeit der Platine, Versatz, Unsicherheit und Ergebnis (`accepted`, `uncertainty`, `rewind`, `unknown_request`, `session_mismatch`, `invalid_stamps`). |
| `connection.mappings` | Jede eingebaute Zuordnung mit Id, Versatz, Rate, Quelle (`sync_window` oder `continuous`), Ankerabgleich und Zeitpunkt. |
| `events[].mapping_id`, `mapping_age_ns` | Welche Zuordnung die Erfassungszeit dieser Auslösung berechnet hat und wie alt sie war. |
| `events[].receive_host_ns`, `receive_minus_capture_ns` | Lesezeit am Host und Abstand zur umgerechneten Erfassungszeit. Nur zur Diagnose, nie als Erfassungszeit. Ein negativer Wert wäre unmöglich und zeigt eine zu späte Zuordnung. |
| `start_sequences[]` | Pro Startablauf: Start des Fensters, Freigabe, `markers` und Anzahl der Frühstarts. |

Die Marker in `start_sequences[].markers` sind Instants der Host-Monotonuhr:

* `ui_release_requested`: Der Startablauf ruft den Rennstart auf.
* `engine_release`: Zeitstempel der Rennengine. Er ist die Freigabe, an der Frühstarts gemessen werden.
* `timer_start`: Die Live-Ansicht startet ihre Anzeige-Aktualisierung.
* `ui_lights_out`: Die Ansicht hat den Zustand „alle Lampen aus“ gesetzt. Das ist keine gemessene
  Bildschirmausgabe.

## Auswertung

* Ist `rate_status` bei jedem Export `skew_rejected` oder `fit_inconsistent` und `raw_skew_ppm` groß, läuft
  die Platinenuhr abweichend und die Rate wird nicht verwendet. (Bis Version nach dem Export 7986 wurde jede
  Steigung über 1 % verworfen; ein Board mit 1,09 % langsamer Uhr blieb daher bei Rate 1/1.) Dann sind die Erfassungszeiten nach dem ersten Ereignis einer Sitzung
  um `raw_skew_ppm` mal vergangener Zeit verschoben. Das erklärt Frühstarts nach der Freigabe.
* Liegt `engine_release` deutlich vor `ui_lights_out` (mehr als etwa 100 ms), ist die Anzeige später als
  die Freigabe. Dann ist die Freigabe richtig, aber die Anzeige zu spät.
* Liegen alle Marker eng beieinander, `rate_status` ist `fitted` und `receive_minus_capture_ns` ist
  klein und positiv, stimmt die Zeitbasis. Dann war die Auslösung vor der Freigabe.

Eine Hardwaremessung mit der Platine ist damit nicht ersetzt. Ohne solche Exporte lässt sich die Ursache
am Code allein nicht beweisen.
