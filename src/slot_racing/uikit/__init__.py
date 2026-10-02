"""Small Qt helpers shared by the UI parts of the modules.

Modules may use this package; it depends on the core only, never on the app shell or on a
module. Business rules do not belong here.
"""

from slot_racing.uikit.dialog import FormDialog
from slot_racing.uikit.entity_page import EntityPage, EntityRow
from slot_racing.uikit.errors import describe_error
from slot_racing.uikit.providers import availability_text, provider_item_text, provider_label
from slot_racing.uikit.translations import UIKIT_TRANSLATIONS
from slot_racing.uikit.widgets import (
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
