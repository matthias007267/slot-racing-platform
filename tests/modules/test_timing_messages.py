"""Every timing error key the code can raise has a German text."""

from __future__ import annotations

import re
from pathlib import Path

from carrera.core.messages import CORE_TRANSLATIONS
from carrera.modules.tracks.translations import TRANSLATIONS

SOURCE = Path(__file__).resolve().parents[2] / "src" / "carrera"
KEY = re.compile(r'"(error\.timing\.[a-z_]+)"')
DYNAMIC = re.compile(r'f"error\.timing\.\{field\}_blank"')


def test_all_timing_error_keys_are_translated() -> None:
    texts = {**CORE_TRANSLATIONS["de"], **TRANSLATIONS["de"]}
    used = {
        key
        for path in SOURCE.rglob("*.py")
        if path.name not in {"messages.py", "translations.py"}
        for key in KEY.findall(path.read_text(encoding="utf-8"))
    }
    assert used
    assert not sorted(used - set(texts))


def test_blank_field_keys_built_from_the_field_name_are_translated() -> None:
    domain = (SOURCE / "core" / "domain" / "timing.py").read_text(encoding="utf-8")
    assert DYNAMIC.search(domain)
    texts = CORE_TRANSLATIONS["de"]
    assert {"error.timing.position_blank", "error.timing.sensor_blank"} <= set(texts)
