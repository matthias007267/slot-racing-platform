"""Texts used by the shared widgets. Modules merge this catalog into their own translations."""

from __future__ import annotations

UIKIT_TRANSLATIONS: dict[str, dict[str, str]] = {
    "de": {
        "common.add": "Hinzufügen",
        "common.edit": "Bearbeiten",
        "common.delete": "Löschen",
        "common.activate": "Aktivieren",
        "common.deactivate": "Deaktivieren",
        "common.confirm": "Bestätigen",
        "common.confirm_delete": "„{name}“ wirklich löschen?",
        "common.saved": "Gespeichert.",
        "common.deleted": "Gelöscht.",
        "common.yes": "Ja",
        "common.no": "Nein",
        "common.active": "Aktiv",
        "common.inactive": "Inaktiv",
        "common.none": "(keine)",
        "common.name": "Name",
        "common.description": "Beschreibung",
        "error.database": (
            "Die Datenbank konnte die Aktion nicht ausführen. Bitte versuchen Sie es erneut."
        ),
        "error.unexpected": "Unerwarteter Fehler: {detail}",
    }
}
