"""German texts for the championship area."""

from slot_racing.uikit import UIKIT_TRANSLATIONS

TRANSLATIONS: dict[str, dict[str, str]] = {
    "de": {
        **UIKIT_TRANSLATIONS["de"],
        "plugin.championships.title": "Meisterschaften",
        "nav.championships": "Meisterschaften",
        "championship.status.planned": "Geplant",
        "championship.status.active": "Aktiv",
        "championship.status.completed": "Abgeschlossen",
        "championship.race.created": "Angelegt",
        "championship.race.ready": "Bereit",
        "championship.race.running": "Läuft",
        "championship.race.paused": "Pausiert",
        "championship.race.finished": "Beendet",
        "championship.race.aborted": "Abgebrochen",
        "championship.column.name": "Name",
        "championship.column.season": "Saison",
        "championship.column.status": "Status",
        "championship.column.races": "Rennen",
        "championship.empty": "Noch keine Meisterschaft angelegt.",
        "championship.open": "Öffnen",
        "championship.activate": "Aktivieren",
        "championship.archive": "Abschließen",
        "championship.confirm_archive": (
            "„{name}“ abschließen? Danach sind Regeln und Kalender fest."
        ),
        "championship.back": "Zur Übersicht",
        "championship.closed": (
            "Diese Meisterschaft ist abgeschlossen. Regeln, Kalender und Teams bleiben unverändert."
        ),
        "championship.calendar": "Rennkalender",
        "championship.column.order": "Nr.",
        "championship.column.race": "Rennen",
        "championship.add_race": "Rennen zuordnen",
        "championship.remove_race": "Entfernen",
        "championship.up": "Nach oben",
        "championship.down": "Nach unten",
        "championship.no_races": "Dieser Meisterschaft ist noch kein Rennen zugeordnet.",
        "championship.standings": "Fahrerwertung",
        "championship.column.place": "Platz",
        "championship.column.driver": "Fahrer",
        "championship.column.points": "Punkte",
        "championship.column.counted": "Gewertet",
        "championship.column.wins": "Siege",
        "championship.column.podiums": "Podest",
        "championship.column.bonus": "Bonus",
        "championship.column.gap": "Abstand",
        "championship.no_standings": "Noch keine gewerteten Ergebnisse.",
        "championship.teams": "Teamwertung",
        "championship.column.team": "Team",
        "championship.column.results": "Ergebnisse",
        "championship.column.drivers": "Fahrer",
        "championship.team_name": "Teamname",
        "championship.add_team": "Team anlegen",
        "championship.delete_team": "Team löschen",
        "championship.assign": "Fahrer zuordnen",
        "championship.unassign": "Zuordnung aufheben",
        "championship.refresh_teams": "Zuordnung dieses Rennens aktualisieren",
        "championship.no_teams": "Noch kein Team angelegt.",
        "championship.rules": "Punkteschema",
        "championship.column.place_points": "Punkte für den Platz",
        "championship.bonus": "Bonus für die schnellste gültige Runde",
        "championship.drops": "Streichresultate",
        "championship.add_place": "Platz ergänzen",
        "championship.remove_place": "Letzten Platz entfernen",
        "championship.save_rules": "Regeln speichern",
        "championship.tie_break": (
            "Gleichstand: Gesamtpunkte, dann Siege, dann zweite Plätze und weitere Platzierungen. "
            "Bleibt der Gleichstand bestehen, teilen sich die Fahrer den Platz."
        ),
        "championship.drop_note": (
            "Gestrichen wird das schlechteste gewertete Ergebnis. "
            "Mindestens ein Ergebnis bleibt. "
            "Disqualifizierte und nicht beendete Rennen vergeben keine Punkte "
            "und werden nicht gestrichen."
        ),
        "championship.race_scores": "Einzelwertung",
        "championship.column.total": "Gesamt",
        "championship.column.dropped": "Streichresultat",
        "championship.column.fastest": "Schnellste Runde",
        "championship.column.note": "Hinweis",
        "championship.note.merged": "Mehrfachstart, ein Ergebnis gewertet",
        "championship.note.absent": "Nicht gestartet",
        "championship.note.disqualified": "Disqualifiziert",
        "championship.note.dropped": "Gestrichen",
        "championship.yes": "Ja",
        "championship.no": "Nein",
        "championship.dialog.new": "Meisterschaft anlegen",
        "championship.dialog.edit": "Meisterschaft bearbeiten",
        "championship.field.name": "Name",
        "championship.field.description": "Beschreibung",
        "championship.field.season": "Saison",
        "championship.field.starts": "Beginn",
        "championship.field.ends": "Ende",
        "championship.field.teams": "Teamwertung",
        "championship.result": "Rennergebnis",
        "error.championship.closed": (
            "Eine abgeschlossene Meisterschaft kann nicht mehr geändert werden."
        ),
        "error.championship.status": "Dieser Statuswechsel ist nicht möglich.",
        "error.championship.not_found": "Die Meisterschaft existiert nicht mehr.",
        "error.championship.race_missing": "Das Rennen ist nicht verfügbar.",
        "error.championship.race_taken": "Das Rennen gehört bereits zu einer Meisterschaft.",
        "error.championship.race_open": (
            "Nur ein beendetes Rennen hat eine gespeicherte Teamzuordnung."
        ),
        "error.championship.order": "Die Reihenfolge kann nur um eine Position verschoben werden.",
        "error.championship.teams_off": (
            "Die Teamwertung ist für diese Meisterschaft ausgeschaltet."
        ),
        "error.championship.team_name": "Der Teamname fehlt oder ist bereits vergeben.",
        "error.championship.team_name.long": (
            "Der Teamname darf höchstens {limit} Zeichen lang sein."
        ),
        "error.championship.team_missing": "Das Team existiert nicht mehr.",
        "error.championship.team_in_use": (
            "Das Team ist einem gewerteten Rennen zugeordnet und kann nicht gelöscht werden."
        ),
        "error.championship.driver_missing": "Der Fahrer existiert nicht.",
        "error.championship.name": "Bitte geben Sie einen Namen ein.",
        "error.championship.name.long": "Der Name darf höchstens {limit} Zeichen lang sein.",
        "error.championship.description": (
            "Die Beschreibung darf höchstens {limit} Zeichen lang sein."
        ),
        "error.championship.season": "Die Saison muss zwischen 1950 und 2100 liegen.",
        "error.championship.period": "Das Ende darf nicht vor dem Beginn liegen.",
        "error.championship.points": (
            "Das Punkteschema ist ungültig. Plätze beginnen bei 1 und Punkte sind nicht negativ."
        ),
    }
}
