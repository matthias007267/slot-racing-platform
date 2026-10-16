# Sensor-Firmware (Arduino Mega 2560)

Die Firmware spricht dasselbe `PSF/1` wie der Simulator und der USB-Transport der Plattform. Es gibt keinen zweiten Parser und keine zusätzliche Debug-Ausgabe auf der Schnittstelle.

## Anschluss

| Signal | Mega |
| --- | --- |
| Sensor 1, digitaler Ausgang D0 der LM393-Gabel | D2 |
| Sensor 2, D0 | D3 |
| Versorgung | 5 V und GND |
| A0 | unbeschaltet |

Frei ist LOW, unterbrochen ist HIGH. Erfasst wird die erste akzeptierte steigende Flanke. `s1` ist der Sensor an D2, `s2` der an D3. Diese Kennungen sind fest und unabhängig von Spur und Messpunkt; die Zuordnung steht in der Sensor-Diagnose der Plattform.

Weitere externe Interrupt-Pins des Mega sind D21, D20, D19 und D18. Sie sind in der Firmware aufgeführt und aus. Andere Mega-Pins werden nicht als interruptfähig behandelt.

Jeder Sensor sperrt für sich. Die nächste Durchfahrt gilt erst, wenn seit der akzeptierten Flanke mindestens 1 000 ms vergangen sind und das Signal zusätzlich mindestens 20 ms am Stück frei war. Ignorierte Flanken verlängern die 1 000 ms nicht. Ein beim Start bereits unterbrochener Sensor zählt nicht als Durchfahrt; er wird erst nach 20 ms stabilem Frei scharf.

## Protokoll

Baudrate ist immer **115200**, 8N1, eine Zeile pro Nachricht, Abschluss `\n`.

```
PSF/1 HELLO <session> mega2560 <firmware> ns <anzahl> <sensor>...
PSF/1 STATUS <session> WAIT|READY|TIME
PSF/1 ERROR <session> BUFFER <anzahl>
PSF/1 ERROR <session> PROTOCOL 0
PSF/1 <session> <nummer> <geraetezeit_ns> <sensor>
PSF/1 SYNC <host_seq>
PSF/1 SYNC <session> <host_seq> <device_rx_ns> <device_tx_ns>
```

`geraetezeit_ns` ist die Controller-Uhr zum Zeitpunkt der Flanke, in Nanosekunden. Das ist die Dateneinheit, keine Genauigkeitszusage. Die Host-Empfangszeit ist nicht die Durchfahrtszeit.

Die Sitzungskennung kommt aus einem Zähler im EEPROM und wechselt bei jedem Reset. Eine neue Sitzung beendet die laufende Zeitzuordnung. Der 32-Bit-`micros()`-Zähler wird auf 64-Bit-Nanosekunden erweitert; ein erkannter Überlauf meldet `STATUS TIME`, die Zeitstempel bleiben monoton. Zwei übersehene Überläufe (länger als etwa 71,6 Minuten ohne Abfrage) kann die Firmware nicht rekonstruieren. Ein voller Ereignispuffer meldet `ERROR BUFFER` und verbraucht trotzdem die Ereignisnummer, damit die Lücke sichtbar bleibt. Die Sperre des Sensors läuft trotzdem.

Der Host sendet `PSF/1 SYNC <host_seq>` und merkt sich seine monotone Sendezeit. Das Board antwortet mit der Controller-Zeit beim Ende der Anfragezeile und unmittelbar vor der Antwort. Der Host stempelt den Empfang der Antwortzeile. Offset und Gang werden nur aus diesen vier Zeitpunkten bestimmt.

Öffnen des COM-Ports setzt das Board über DTR zurück. Die erste gültige Zeile danach ist `HELLO`.

## Bauen

Host-Prüfung der Erfassung, ohne Board:

```bash
make -C firmware test
```

Image für den Mega 2560, mit installierter Arduino CLI und dem Core `arduino:avr`:

```bash
arduino-cli core install arduino:avr
make -C firmware firmware
```

## Hochladen unter Windows

1. Den seriellen Monitor und jedes andere Programm schließen, das COM-Port des Boards offen hat. Auch die Plattform darf den Port zu diesem Zeitpunkt nicht verbunden haben.
2. Board „Arduino Mega or Mega 2560“, Prozessor passend zur Karte, Port auswählen (im bestätigten Aufbau COM5).
3. Sketch `firmware/slot_sensor` hochladen.
4. Den seriellen Monitor nach dem Upload wieder schließen, bevor die Plattform verbindet. Zwei Programme können den Port nicht gleichzeitig offen halten.
5. In der Plattform unter Sensor-Zeitmessung die Betriebsart USB wählen, denselben Port auswählen und verbinden. COM5 ist nicht voreingestellt.
6. Nach `HELLO` die Synchronisation abwarten, `s1` und `s2` dem Messpunkt und der Spur zuordnen, speichern, dann die Gabel von Hand unterbrechen.
