"""User facing texts of errors raised by core domain objects.

Domain validation raises :class:`ValidationError` with a translation key; the application
registers this catalog so every module can show the message.
"""

from __future__ import annotations

CORE_TRANSLATIONS: dict[str, dict[str, str]] = {
    "de": {
        "error.timing.no_positions": "Das Timing-Layout braucht mindestens eine Position.",
        "error.timing.start_finish_missing": "Das Timing-Layout braucht eine Start/Ziel-Position.",
        "error.timing.start_finish_duplicate": (
            "Das Timing-Layout darf nur eine Start/Ziel-Position enthalten."
        ),
        "error.timing.start_finish_first": "Start/Ziel muss die erste Position sein.",
        "error.timing.position_blank": "Eine Position hat keine ID.",
        "error.timing.position_duplicate": "Die Position „{position}“ kommt doppelt vor.",
        "error.timing.order_invalid": (
            "Die Reihenfolge der Positionen ist ungültig. Sie muss bei 1 beginnen und "
            "lückenlos und eindeutig sein."
        ),
        "error.timing.position_unknown": "Die Position „{position}“ existiert nicht.",
        "error.timing.sensor_blank": "Jede Position braucht eine Sensor-ID.",
        "error.timing.sensor_duplicate": "Die Sensor-ID „{sensor}“ kommt doppelt vor.",
        "error.timing.sensor_unknown": "Der Sensor „{sensor}“ existiert nicht.",
        "error.timing.hardware_duplicate": (
            "Die Hardware-ID „{hardware_id}“ ist mehreren Sensoren zugeordnet."
        ),
        "error.timing.sensor_unknown_position": (
            "Der Sensor „{sensor}“ gehört zu der unbekannten Position „{position}“."
        ),
        "error.timing.position_without_sensor": (
            "Der Position {position} ist kein Sensor zugeordnet."
        ),
        "error.timing.position_multiple_sensors": (
            "Der Position {position} sind mehrere Sensoren zugeordnet."
        ),
        "error.timing.sensor_inactive": (
            "Der Sensor „{sensor}“ der Position {position} ist deaktiviert und kann nicht "
            "zur Zeitmessung verwendet werden."
        ),
        "error.timing.text_too_long": "Ein Text im Timing-Layout ist länger als {limit} Zeichen.",
        "error.timing.track_unknown": "Die Strecke existiert nicht mehr.",
        "error.timing_provider.none_registered": (
            "Es ist keine Zeitmessung verfügbar. Bitte aktivieren Sie ein Zeitmessungs-Modul."
        ),
        "error.timing_provider.unknown": "Die Zeitmessung „{provider}“ ist nicht registriert.",
        "error.timing_provider.unavailable": "Die gewählte Zeitmessung ist nicht verfügbar.",
        "error.timing_provider.single_lane": (
            "Die gewählte Zeitmessung unterstützt nur eine Spur."
        ),
        "error.timing_provider.failed": (
            "Die Zeitmessung „{provider}“ konnte nicht vorbereitet werden: {detail}"
        ),
        "error.timing_provider.id_blank": "Eine Zeitmessung hat keine Kennung.",
        "error.timing_provider.duplicate": (
            "Die Kennung „{provider}“ ist für mehrere Zeitmessungen registriert."
        ),
        "error.timing_provider.lanes_duplicate": "Eine Spur darf nur einmal vorkommen.",
        "timing.provider.available": "verfügbar",
        "timing.provider.unavailable": "nicht verfügbar",
    }
}
