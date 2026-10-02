"""Texts of the shell itself. Module texts live with the modules."""

from __future__ import annotations

SHELL_TRANSLATIONS: dict[str, dict[str, str]] = {
    "de": {
        "app.title": "Carrera Racing Platform",
        "nav.dashboard": "Dashboard",
        "nav.settings": "Einstellungen",
        "dashboard.heading": "Dashboard",
        "dashboard.active_modules": "Aktive Module",
        "dashboard.no_modules": "Keine Module aktiv.",
        "settings.heading": "Einstellungen",
        "settings.modules": "Module",
        "settings.state.enabled": "aktiv",
        "settings.state.registered": "nicht aktiviert",
        "settings.state.disabled": "deaktiviert",
        "settings.state.failed": "Fehler",
        "page.placeholder": "Diese Ansicht ist noch nicht implementiert.",
        "page.error": "Diese Ansicht konnte nicht geladen werden.",
    }
}
