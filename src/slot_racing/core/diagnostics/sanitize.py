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
    """Home directories that identify the current user, longest first.

    Each path contributes a Windows spelling and a POSIX spelling. ``pathlib``
    on Windows rewrites ``/home/name`` to ``\\home\\name``, so the original
    separator is not recoverable from ``str(path)`` alone.
    """
    found: list[str] = []
    seen: set[str] = set()
    for candidate in (
        *extra,
        Path.home(),
        Path(os.environ.get("USERPROFILE", "")),
        Path(os.environ.get("HOME", "")),
    ):
        for spelling in _spellings(str(candidate)):
            if spelling in seen or _trivial_prefix(spelling):
                continue
            seen.add(spelling)
            found.append(spelling)
    found.sort(key=len, reverse=True)
    return tuple(found)


def sanitize_text(text: str, *, homes: Sequence[Path] = ()) -> str:
    """Replace home prefixes and inline secrets. Other text stays readable.

    A Windows log can contain a POSIX path and the other way around, so both
    separator styles are matched. The separator that followed the home stays.
    """
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


def _spellings(text: str) -> tuple[str, ...]:
    """Return ``text`` with trailing separators removed, in both separator styles."""
    bare = text.rstrip("/\\")
    base = bare if bare else text
    variants = [base]
    if "\\" in base:
        variants.append(base.replace("\\", "/"))
    if "/" in base:
        variants.append(base.replace("/", "\\"))
    unique: list[str] = []
    for variant in variants:
        if variant not in unique:
            unique.append(variant)
    return tuple(unique)


def _trivial_prefix(prefix: str) -> bool:
    """True for values that would erase unrelated text, such as ``/`` or ``.``."""
    if not prefix or prefix in {".", ".."}:
        return True
    if all(character in "/\\" for character in prefix):
        return True
    bare = prefix.rstrip("/\\")
    if not bare or bare in {".", ".."}:
        return True
    return len(bare) == 2 and bare[1] == ":" and bare[0].isalpha()


def _windows_spelling(prefix: str) -> bool:
    """Drive paths and backslash paths compare case-insensitively. POSIX does not."""
    if "\\" in prefix or prefix.startswith("//"):
        return True
    return len(prefix) >= 2 and prefix[0].isalpha() and prefix[1] == ":"


def _replace_prefix(text: str, prefix: str) -> str:
    """Replace ``prefix`` only when it ends on a path boundary."""
    if _trivial_prefix(prefix):
        return text
    pattern = re.compile(
        re.escape(prefix),
        re.IGNORECASE if _windows_spelling(prefix) else 0,
    )
    parts: list[str] = []
    start = 0
    for match in pattern.finditer(text):
        end = match.end()
        if end < len(text) and text[end] not in "/\\":
            continue
        parts.append(text[start : match.start()])
        parts.append(_HOME)
        start = end
    parts.append(text[start:])
    return "".join(parts)
