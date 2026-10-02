"""Small Qt helpers shared by the UI parts of the modules.

Modules may use this package; it depends on the core only, never on the app shell or on a
module. Business rules do not belong here.
"""

from carrera.uikit.dialog import FormDialog
from carrera.uikit.entity_page import EntityPage, EntityRow
from carrera.uikit.errors import describe_error
from carrera.uikit.providers import availability_text, provider_item_text, provider_label
from carrera.uikit.translations import UIKIT_TRANSLATIONS
from carrera.uikit.widgets import (
    StatusLabel,
    fill_table,
    format_datetime,
    heading,
    make_table,
    selected_id,
)

__all__ = [
    "UIKIT_TRANSLATIONS",
    "EntityPage",
    "EntityRow",
    "FormDialog",
    "StatusLabel",
    "availability_text",
    "describe_error",
    "fill_table",
    "format_datetime",
    "heading",
    "make_table",
    "provider_item_text",
    "provider_label",
    "selected_id",
]
