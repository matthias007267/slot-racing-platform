from __future__ import annotations

from carrera.uikit import UIKIT_TRANSLATIONS

TRANSLATIONS: dict[str, dict[str, str]] = {
    "de": {
        **UIKIT_TRANSLATIONS["de"],
        "plugin.tracks.title": "Strecken",
        "nav.tracks": "Strecken",
        "track.dialog.new": "Neue Strecke",
        "track.dialog.edit": "Strecke bearbeiten",
        "track.field.name": "Name",
        "track.field.description": "Beschreibung",
        "track.field.lane_count": "Spuren ({minimum} bis {maximum})",
        "track.field.image": "Bild / Symbol",
        "track.column.lanes": "Spuren",
        "track.browse": "Durchsuchen …",
        "error.track.name.required": "Bitte geben Sie einen Namen für die Strecke ein.",
        "error.track.name.too_long": "Der Name darf höchstens {limit} Zeichen lang sein.",
        "error.track.lane_count": "Die Spurenzahl muss zwischen {minimum} und {maximum} liegen.",
        "error.track.image.too_long": "Der Bildpfad darf höchstens {limit} Zeichen lang sein.",
        "error.track.not_found": "Die Strecke existiert nicht mehr.",
        "error.track.in_use": (
            "Die Strecke wird in Rennen verwendet und kann nicht gelöscht werden. "
            "Deaktivieren Sie sie stattdessen."
        ),
    }
}
