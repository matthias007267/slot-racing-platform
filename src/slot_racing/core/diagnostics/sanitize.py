"""Remove secrets and home-directory prefixes from diagnostic text.

The support bundle stays on the machine that produced it. These filters keep a
copy of that bundle from carrying tokens or a user name inside a path.
"""

from __future__ import annotations

import os
import re
from collections.abc import Mapping, Sequence
from pathlib import Path

_SECRET_NAMES = frozenset(
    {
        "password",
        "passwd",
        "secret",
        "token",
        "access_token",
        "refresh_token",
        "authorization",
        "api_key",
        "apikey",
    }
)
_SECRET_PARTS = frozenset({"password", "passwd", "secret", "token", "authorization"})
_REDACTED = "<REDACTED>"
_HOME = "<USER_HOME>"
_INLINE_SECRET = re.compile(
    r"(?i)([\"']?(?:password|passwd|secret|access_token|refresh_token|authorization|"
    r"api_key|apikey|token)[\"']?\s*[=:]\s*)([\"']?[^\s,\"'}]+[\"']?)"
)


def is_secret_key(key: str) -> bool:
    """True when ``key`` names a credential. Matching is case-insensitive."""
    folded = key.casefold().replace("-", "_")
    compact = folded.replace("_", "")
    if folded in _SECRET_NAMES or compact in {name.replace("_", "") for name in _SECRET_NAMES}:
        return True
    return any(part in _SECRET_PARTS for part in folded.split("_") if part)


def home_prefixes(extra: Sequence[Path] = ()) -> tuple[str, ...]:
    """Directories that identify the current user, longest first."""
    found: list[str] = []
    for candidate in (
        *extra,
        Path.home(),
        Path(os.environ.get("USERPROFILE", "")),
        Path(os.environ.get("HOME", "")),
    ):
        text = str(candidate)
        if text and text not in (".", "") and text not in found:
            found.append(text)
    found.sort(key=len, reverse=True)
    return tuple(found)


def sanitize_text(text: str, *, homes: Sequence[Path] = ()) -> str:
    """Replace home prefixes and inline secrets. Other text stays readable."""
    cleaned = text
    for prefix in home_prefixes(homes):
        cleaned = _replace_prefix(cleaned, prefix)
    return _INLINE_SECRET.sub(lambda match: match.group(1) + _REDACTED, cleaned)


def redact(value: object, *, homes: Sequence[Path] = ()) -> object:
    """Return ``value`` with secret keys and home paths removed."""
    if isinstance(value, Mapping):
        cleaned: dict[object, object] = {}
        for key, item in value.items():
            name = str(key)
            cleaned[key] = _REDACTED if is_secret_key(name) else redact(item, homes=homes)
        return cleaned
    if isinstance(value, list | tuple):
        return [redact(item, homes=homes) for item in value]
    if isinstance(value, Path):
        return sanitize_text(str(value), homes=homes)
    if isinstance(value, str):
        return sanitize_text(value, homes=homes)
    return value


def _replace_prefix(text: str, prefix: str) -> str:
    """Replace every occurrence of ``prefix``. A log line rarely starts with it."""
    if not prefix or prefix in (os.sep, "/"):
        return text
    windows = "\\" in prefix or (len(prefix) > 1 and prefix[1] == ":")
    if not windows:
        return text.replace(prefix, _HOME)
    folded = text.casefold()
    needle = prefix.casefold()
    if needle not in folded:
        return text
    parts: list[str] = []
    start = 0
    while True:
        index = folded.find(needle, start)
        if index < 0:
            parts.append(text[start:])
            return "".join(parts)
        parts.append(text[start:index])
        parts.append(_HOME)
        start = index + len(prefix)
