# Startampel messen

Diese Anleitung ist für einen Start am Renn-PC und für die Browser, die
dieselbe Ampel anzeigen. Sie setzt keine Kenntnis der Programmstruktur voraus.

Die Messung ändert den Rennstart nicht. Die grüne Freigabe (GO) erscheint im
Browser erst, wenn der Renn-PC das Rennen wirklich gestartet und das bestätigt
hat. Ein geplanter Zeitpunkt, eine lokale Uhr oder eine unterbrochene
Verbindung geben GO nicht frei.

Es gibt drei verschiedene Zeiten. Sie dürfen nicht vermischt werden.

| Art | Bedeutung |
|---|---|
| Messwert | Ein Zeitpunkt, den dasselbe Gerät auf seiner eigenen Uhr festgehalten hat. |
| Schätzung | Ein Browser-Zeitpunkt, umgerechnet auf die Uhr des Renn-PCs. Die Umrechnung hat eine Unsicherheit. |
| Geplanter Zeitpunkt | Der Zeitpunkt, den der PC für eine Lampe oder für GO vorgesehen hat. |
| Sichtbarer Wechsel | Der Moment, in dem das Licht auf dem Display tatsächlich zu sehen ist. Den zeichnet diese Software nicht auf. |

Ein JavaScript-Aufruf und auch der nächste Bild-Callback (`requestAnimationFrame`)
beweisen nicht, wann ein Pixel auf dem Bildschirm war.

## Was automatisch erfasst wird

Auf dem Renn-PC, sobald die Live-Anzeige läuft:

- die Kennung der Startsequenz
- die geplanten Lampenzeitpunkte
- der Zeitpunkt, zu dem der PC eine rote Phase an die Browser übergibt
- wie weit dieser Zeitpunkt hinter dem Plan liegt
- wie viele rote Phasen dabei übersprungen wurden
- der tatsächliche Startstempel der Renn-Engine
- der Zeitpunkt, zu dem die Startbestätigung an die Browser übergeben wird

Im Browser, auf der Diagnoseseite, und auf der Rennansicht nur mit dem Zusatz
`messung=1` in der Adresse:

- geschätzter Abstand zur PC-Uhr, Unsicherheit, aktuelle und beste Laufzeit
- ob die roten Lampen lokal oder nach den Nachrichten des PCs laufen
- Zeitpunkt der JavaScript-Änderung für jede rote Phase und für GO
- der nächste Bild-Callback danach, als eigener Eintrag
- Empfang der Startbestätigung
- geschätzte GO-Verzögerung und ihre drei Teile
- übersprungene rote Phasen
- Verbindungsabbrüche, erneute Verbindungen, Wechsel zurück auf den PC,
  verworfene Zeitproben

Die drei Teile der GO-Verzögerung:

| Teil | Art |
|---|---|
| Engine-Start bis Veröffentlichung | Messwert auf der PC-Uhr |
| Veröffentlichung bis Empfang | Schätzung, mit der Unsicherheit der Uhr |
| Empfang bis Darstellung | Messwert auf der Browser-Uhr, nicht der sichtbare Pixel |

Die GO-Verzögerung insgesamt ist der geschätzte Darstellungszeitpunkt minus
den Engine-Start. Ein negativer Wert wird gespeichert und gekennzeichnet.
Er beweist kein vorzeitiges sichtbares GO. Er ist ein Hinweis auf einen
Mess- oder Synchronisationsfehler und soll geprüft werden.

Die Diagnoseseite zeigt Verbindung, Sequenz, Lampenphase, Modus, Unsicherheit,
Laufzeit, Offset, letzte und durchschnittliche Lampenabweichung, letzte
GO-Verzögerung, Zahl der Startsequenzen, Abbrüche und Fallback-Wechsel.
Schätzungen sind als Schätzung beschriftet. Die Seite enthält kein
Sitzungstoken.

## Export

Auf der Diagnoseseite gibt es **JSON exportieren** und **CSV exportieren**.
Die Datei wird im Browser erzeugt und nicht hochgeladen.

Dieselbe Möglichkeit erscheint auf der Rennansicht, wenn die Adresse
`messung=1` enthält, zum Beispiel:

```text
http://192.168.1.20:8765/?token=…&messung=1
```

Ohne `messung=1` bleibt die Rennansicht ohne diese Leiste. Die Diagnoseseite
ist eine eigene Sitzung. Ihre Zahlen gehören zu dieser Seite, nicht automatisch
zu einer daneben geöffneten Rennansicht. Für die Anzeige, die man sieht, die
Adresse mit `messung=1` verwenden. Für eine zweite, ruhige Messseite die
Diagnoseadresse desselben PCs öffnen:

```text
http://192.168.1.20:8765/diagnostics?token=…
```

Jede Datei enthält nur das Gerät, das sie gespeichert hat. Eine anonyme
Kennung (`c` und zehn Zeichen) ist kein Sitzungstoken. Große Zeitwerte stehen
als Text, damit sie nicht gerundet werden.

JSON-Felder eines Ereignisses: `client_id`, `sequence_id`, `event`, `phase`,
`time_basis`, `browser_ns`, `estimated_pc_ns`, `planned_pc_ns`,
`pc_observed_ns`, `quality`, `uncertainty_ns`, `deviation_ns`, `delay_ns`,
`note`.

`time_basis` sagt, worauf sich der Wert stützt: `browser`, `pc` oder
`estimated_pc`.

Die Lampenabweichung ist der geschätzte Browser-Zeitpunkt minus den
Veröffentlichungszeitpunkt des PCs. Die Unsicherheit der Uhr gilt für diese
Differenz mit. Es werden höchstens 400 Ereignisse behalten. Ältere fallen weg.
Es gibt keine Dauerschleife und keine zusätzliche Anfrage an einen Server.

Mehrere JSON-Dateien vergleichen, auf dem Rechner, ohne Netzwerk:

```bash
uv run python scripts/compare_start_measurements.py datei-a.json datei-b.json
```

Das Skript vergleicht nur Lampen, für die jedes Gerät einen geschätzten
PC-Zeitpunkt gespeichert hat. Es erfindet keine Werte für ein Gerät, das
keine Datei geliefert hat. Ausgegeben werden Minimum, Maximum, Median,
Spanne zwischen dem frühesten und dem spätesten Gerät, und ab fünf Werten
P95, ab zwanzig Werten P99.

## Was die automatische Simulation gezeigt hat

Die folgenden Läufe sind feste Verzögerungen im Test, keine Messung eines
echten Netzes. Dieselben Eingaben ergeben immer dasselbe Ergebnis. Die
Stufen Gut (bis 8 ms), Brauchbar (bis 20 ms), Halten bis 25 ms und
Probenalter 45 Sekunden wurden dabei nicht verändert.

| Simulation | Ergebnis mit den aktuellen Regeln |
|---|---|
| Ideales LAN, 1–3 ms Laufzeit | Gut, Offsetfehler 0 |
| Gutes WLAN, 5–15 ms | Gut, Offsetfehler 0 |
| Normales WLAN, 15–40 ms | Brauchbar, lokale rote Lampen |
| Schlechtes WLAN, 50–150 ms | Schlecht, Lampen folgen dem PC |
| Stark schwankend, 5–200 ms | Gut, solange schnelle Proben dabei sind |
| Asymmetrisch, 1 ms hin und 30 ms zurück | Brauchbar, Offsetfehler 14,5 ms, innerhalb der ausgewiesenen Unsicherheit von 15,5 ms |
| Nur zwei von sechs Proben kommen an | Schlecht, zu wenige Proben |
| Drei schnelle und drei langsame Proben | Gut, die schnellen Proben bestimmen die Schätzung |
| Browser pausiert länger als 60 s | Schlecht, keine lokale Vorhersage |
| PC-Start später als der Plan | Fünf rote Lampen bleiben an, kein GO |

Eine blockierte PC-Oberfläche kann die roten Browserlampen gegenüber dem PC
verschieben. Sie gibt GO nicht früher frei. GO bleibt an die Bestätigung nach
dem Engine-Start gebunden.

Mehrere Browser gleichzeitig, im Test 1, 2, 5 und 16 Clients, erhalten ihre
Zeitproben, ohne dass die Startbestätigung auf sie wartet. Ein 17. Client
wird abgewiesen. Das ist die bestehende Grenze.

Diese Tabelle ist kein Freigabewert für die Bahn. Dafür die Geräteprüfung
unten ausführen und die exportierten Dateien behalten.

## Prüfung mit echten Geräten

Alle Geräte im selben lokalen Netzwerk. Der Renn-PC stellt die Anzeige bereit.
Die Adresse steht in der Anwendung. Jedes Gerät öffnet sie. Für Zahlen die
Diagnoseseite oder die Rennansicht mit `messung=1` verwenden.

Es müssen nicht alle Geräte da sein. Jede Teilmenge ist eine gültige Prüfung,
solange notiert wird, welche Geräte fehlten.

| Gerät | Nutzen |
|---|---|
| Renn-PC unter Windows | Startet das Rennen und zeigt die PC-Ampel |
| Zweiter Windows-PC oder Laptop | Zweiter Browser |
| Android-Smartphone | Kleines Display, anderes System |
| Android-Tablet, falls vorhanden | Größeres Display |
| iPhone | Apple-Browser |
| iPad, falls vorhanden | Größeres Apple-Display |

Vor dem ersten Start auf jedem Messgerät einmal JSON oder CSV speichern, damit
der Speicherweg klar ist. Danach die Datei der Runde anhängen oder neu
speichern und die Runde im Dateinamen notieren.

### Reihen

1. Zehn normale Starts, Geräte nah am Zugangspunkt.
2. Dieselben zehn Starts mit allen vorhandenen Browsern gleichzeitig.
3. Starts mit größerem Abstand zum Zugangspunkt.
4. Starts, während im Netz etwas anderes lädt (ein großer Kopiervorgang
   reicht; die Last notieren, nicht eine bestimmte Megabit-Zahl behaupten).
5. Ein Browser in den Hintergrund legen, bevor die Ampel beginnt, und wieder
   hervorholen.
6. Display sperren und entsperren, einmal vor dem Start und einmal während
   der roten Lampen.
7. WLAN kurz trennen und wieder verbinden, außerhalb einer Startsequenz.
8. WLAN während der roten Lampen trennen und noch vor GO wieder verbinden.
9. WLAN nach GO trennen und wieder verbinden. GO darf nicht erneut oder von
   allein erscheinen.
10. Einen Start abbrechen und danach einen neuen starten. Die alte Sequenz
    darf kein GO mehr erzeugen.

Nach jeder Reihe die Diagnoseseite ansehen und die Datei sichern.

### Was man abliest und was man hinschreibt

Automatisch in der Datei: die Tabelle oben.

Von Hand, weil es kein Messwert der Software ist:

- ob die roten Lampen auf den Geräten gemeinsam zu springen schienen
- ob GO auf einem Gerät sichtbar vor dem Renn-PC kam
- ob ein Gerät schwarz blieb, hängen blieb oder nach dem Aufwecken falsch lag
- die ungefähre Entfernung und was sonst im Netz los war
- welches Gerät welche Datei ist

Ein Gerät, das keine Datei geschrieben hat, hat keine Messwerte. Diese Werte
nicht aus einem anderen Gerät übernehmen.

## Optische Prüfung

Eine externe Kamera filmt gleichzeitig den Monitor des Renn-PCs und die
Geräte. Alle sollen im selben Bild scharf genug sein, dass die Lampen
erkennbar sind. Die Software auf den Geräten ersetzt diese Aufnahme nicht.

Die erreichbare Aussage hängt an der Aufnahme, nicht an der Ampel:

| Einfluss | Folge |
|---|---|
| Bildrate | Ein Wechsel ist nur auf ein Bild genau. Bei 60 Bildern/s sind das etwa 17 ms, bei 30 Bildern/s etwa 33 ms. |
| Belichtungszeit | Eine lange Belichtung mischt zwei Zustände in einem Bild. Den Wechsel nicht auf einen Punkt innerhalb dieses Bildes legen. |
| Bildwiederholrate der Displays | Ein Panel kann den neuen Zustand erst zum nächsten Auffrischen zeigen. 60 Hz sind wieder etwa 17 ms. |
| Rolling Shutter | Oben und unten im Bild entstehen nicht gleichzeitig. Geräte an verschiedenen Bildhöhen sind darum nicht auf die Mikrosekunde vergleichbar. |
| Helligkeit und Automatik | Belichtung, Weißabgleich und Rauschfilter können eine Lampe ein Bild zu spät oder zu früh erscheinen lassen. Automatik möglichst aus, Helligkeit fest. |

Auswertung: das erste Bild notieren, in dem eine Lampe eindeutig an ist, und
das letzte, in dem sie eindeutig aus ist. Der Wechsel liegt dazwischen. Die
Unsicherheit ist mindestens ein Bildabstand, zuzüglich der Belichtungszeit,
und sie ist größer, wenn die Geräte nicht in derselben Sensorzeile liegen.
Eine Differenz unterhalb dieser Unsicherheit ist kein Nachweis einer
Abweichung.

Hilfsskript, nur für eine Videodatei, nicht Teil des Rennbetriebs. Es braucht
OpenCV in derselben Umgebung. Fehlt OpenCV, endet es mit einem Hinweis und
ändert nichts am Rennen.

```bash
uv run python scripts/optical_start_frames.py aufnahme.mp4 \
  --roi pc=0.05,0.10,0.25,0.30 \
  --roi telefon=0.55,0.15,0.20,0.35
```

Die vier Zahlen sind Anteile des Bildes: links, oben, Breite, Höhe. Das Skript
meldet das Bild, in dem ein Bereich deutlich rot wird, und die Bilddauer.
Es behauptet nicht, der Pixel sei zu einem genaueren Zeitpunkt sichtbar gewesen.

## Ergebnisse lesen

Für eine Lampenphase, über die geschätzte PC-Zeit:

- Spanne: spätester minus frühester Browser. Das ist der Abstand der Geräte
  untereinander, nicht der Abstand zu einem Foto.
- Die Unsicherheit jedes Geräts muss daneben stehen. Eine Spanne von 5 ms bei
  20 ms Unsicherheit ist kein genauer Gleichstand.
- Die Abweichung zum PC ist geschätzter Browserzeitpunkt minus
  Veröffentlichungszeitpunkt des PCs. Positiv heißt: der Browser war in der
  Schätzung später. Negativ ist ein Prüffall, kein Beweis für ein frühes Licht.

Ausreißer:

- eine einzelne Probe mit sehr großer Laufzeit, während die übrigen klein sind,
  soll die Schätzung nicht umwerfen; die Regeln bevorzugen die kleinen Laufzeiten
- ein Gerät, das im Hintergrund war, neu verbinden musste oder das Display
  gesperrt hatte, gehört in eine eigene Notiz und nicht in den Mittelwert der
  ruhigen Starts
- ein negativer GO-Wert wird nicht geglättet. Die drei Teile zeigen, welcher
  Abschnitt negativ ist
- fehlen geschätzte PC-Zeiten, weil die Synchronisation schlecht war, gibt es
  für dieses Gerät keinen Vergleich. Der Fallback auf die PC-Nachrichten ist
  dann das erwartete Verhalten, kein fehlender Messpunkt zum Auffüllen

Die Schwellen werden erst geändert, wenn solche Dateien von echten Geräten
dasselbe Bild mehrfach zeigen. Die Simulation allein reicht dafür nicht.

In dieser Ausbaustufe wurden keine Mobilgeräte gemessen. Zahlen aus der
Simulationstabelle sind keine Messergebnisse von Windows, Android oder iOS.
