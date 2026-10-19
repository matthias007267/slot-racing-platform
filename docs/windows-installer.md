# Windows-Installer

Der Installer enthält das Programm samt Python, Qt und allen Bibliotheken. Anwender brauchen weder
Python noch uv noch eine Entwicklungsumgebung. Eine Updatefunktion gibt es nicht: Eine neuere
Setup-Datei ersetzt bei der Installation die Programmdateien der älteren Version, die Daten bleiben.

## Für Anwender

1. `SlotRacingPlatform-Setup-<Version>.exe` ausführen. Voraussetzung ist Windows 10 oder neuer (64 Bit).
2. Die Installation läuft ohne Administratorrechte in den eigenen Benutzerordner. Im ersten Dialog
   lässt sich stattdessen „für alle Benutzer“ (Programme-Ordner) wählen; dafür fragt Windows nach
   Administratorrechten.
3. Gestartet wird die Anwendung über das Startmenü („Slot-Racing Platform“). Auf Wunsch legt der
   Installer eine Desktopverknüpfung an.
4. Deinstallieren: Windows-Einstellungen, Apps, „Slot-Racing Platform“. Fahrer, Fahrzeuge, Strecken,
   Rennen und Einstellungen liegen unter `%APPDATA%\SlotRacingPlatform` und werden dabei **nicht** gelöscht.

Eine neue Version wird installiert, indem die neue Setup-Datei über die alte ausgeführt wird.
Die Datenbank wird beim nächsten Start automatisch auf den neuen Stand gebracht.

## Installer bauen

Voraussetzungen auf einem Windows-Rechner: [uv](https://docs.astral.sh/uv/) und
[Inno Setup 6](https://jrsoftware.org/isinfo.php).

```powershell
./packaging/windows/build.ps1
```

Das Skript

1. installiert die Build-Werkzeuge (`uv sync --locked --group installer`),
2. erzeugt Icon, Versionsangaben und Commit-Kennung aus dem Projekt (`prepare_build.py`),
3. baut das Programm mit PyInstaller (`slot-racing.spec`, Ordnerausgabe unter `build\dist`),
4. startet das gebaute Programm mit `--smoke-test` (alle Module laden, Datenbank anlegen),
5. baut den Installer mit Inno Setup (`slot-racing.iss`) nach `build\installer`.

`./packaging/windows/build.ps1 -SkipInstaller` baut nur das Programm.
`./packaging/windows/verify-installer.ps1 -Installer <Datei>` installiert still in einen
Temp-Ordner, startet das installierte Programm, prüft den Startmenüeintrag, deinstalliert wieder und
prüft, dass die Benutzerdaten erhalten bleiben.

Die GitHub-Action `Windows installer` führt beides aus (manuell, bei Tags `v*` und bei Änderungen
unter `packaging/`) und legt die Setup-Datei als Artefakt ab.

## Version

Die Version steht in `pyproject.toml` und `src/slot_racing/__init__.py` und muss gleich sein; der Build
bricht sonst ab. Sie bestimmt den Dateinamen des Installers und die Eigenschaften der EXE-Datei.

## Hinweise

* Das Programm verwendet für Module die Einstiegspunkte aus `pyproject.toml`. Der Build nimmt deshalb
  die Paketmetadaten mit auf (`copy_metadata`). Fehlt ein Modul, schlägt `--smoke-test` fehl.
* Die Migrationsskripte, die Streckenplaner-Kataloge und das Icon werden als Dateien mitgeliefert.
* Das Programm ist nicht signiert. Windows SmartScreen kann deshalb bei der ersten Ausführung warnen.
  Ein Codesignatur-Zertifikat lässt sich später in `build.ps1` und `slot-racing.iss` ergänzen.
* Die Lizenzdatei (`LICENSE`) liegt im Installationsordner. Der Installer zeigt keine Lizenzseite,
  weil für das Projekt noch keine Lizenz gewählt ist.
