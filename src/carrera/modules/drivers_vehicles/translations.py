from __future__ import annotations

from carrera.uikit import UIKIT_TRANSLATIONS

TRANSLATIONS: dict[str, dict[str, str]] = {
    "de": {
        **UIKIT_TRANSLATIONS["de"],
        "plugin.drivers_vehicles.title": "Fahrer und Fahrzeuge",
        "nav.drivers": "Fahrer",
        "nav.vehicles": "Fahrzeuge",
        "driver.dialog.new": "Neuer Fahrer",
        "driver.dialog.edit": "Fahrer bearbeiten",
        "driver.field.name": "Name",
        "driver.field.display_name": "Anzeigename",
        "driver.field.start_number": "Startnummer",
        "error.driver.name.required": "Bitte geben Sie einen Namen für den Fahrer ein.",
        "error.driver.name.too_long": "Der Name darf höchstens {limit} Zeichen lang sein.",
        "error.driver.display_name.too_long": (
            "Der Anzeigename darf höchstens {limit} Zeichen lang sein."
        ),
        "error.driver.start_number.range": "Die Startnummer muss zwischen 1 und {maximum} liegen.",
        "error.driver.start_number_taken": "Die Startnummer {number} ist bereits vergeben.",
        "error.driver.not_found": "Der Fahrer existiert nicht mehr.",
        "error.driver.in_use": (
            "Der Fahrer hat an Rennen teilgenommen und kann nicht gelöscht werden. "
            "Deaktivieren Sie ihn stattdessen."
        ),
        "vehicle.dialog.new": "Neues Fahrzeug",
        "vehicle.dialog.edit": "Fahrzeug bearbeiten",
        "vehicle.field.name": "Name",
        "vehicle.field.model": "Modell",
        "vehicle.field.manufacturer": "Hersteller",
        "vehicle.field.start_number": "Startnummer",
        "vehicle.field.driver": "Fahrer",
        "vehicle.unassign": "Fahrer entfernen",
        "error.vehicle.name.required": "Bitte geben Sie einen Namen für das Fahrzeug ein.",
        "error.vehicle.name.too_long": "Der Name darf höchstens {limit} Zeichen lang sein.",
        "error.vehicle.model.required": "Bitte geben Sie das Modell des Fahrzeugs ein.",
        "error.vehicle.model.too_long": "Das Modell darf höchstens {limit} Zeichen lang sein.",
        "error.vehicle.manufacturer.too_long": (
            "Der Hersteller darf höchstens {limit} Zeichen lang sein."
        ),
        "error.vehicle.start_number.range": (
            "Die Startnummer muss zwischen 1 und {maximum} liegen."
        ),
        "error.vehicle.not_found": "Das Fahrzeug existiert nicht mehr.",
        "error.vehicle.driver_inactive": "Der Fahrer „{driver}“ ist deaktiviert.",
        "error.vehicle.in_use": (
            "Das Fahrzeug wurde in Rennen verwendet und kann nicht gelöscht werden. "
            "Deaktivieren Sie es stattdessen."
        ),
    }
}
