"""Generate the files the Windows build needs under ``build/``.

* ``slot-racing.ico`` from the application picture in ``resources/icons``
* ``version_info.txt`` for the properties of the program file
* ``BUILD_COMMIT`` with the commit the program was built from
* ``version.iss`` with the version the installer is named after

The version comes from ``slot_racing.__version__`` and has to equal the project version.
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
BUILD = ROOT / "build"
SOURCE = ROOT / "resources" / "icons" / "Rennwagen auf glänzender Slotcar-Strecke.png"
SIZES = [(256, 256), (128, 128), (64, 64), (48, 48), (32, 32), (24, 24), (16, 16)]
_VERSION = re.compile(r"^\d+\.\d+\.\d+$")

_VERSION_INFO = """VSVersionInfo(
  ffi=FixedFileInfo(
    filevers=({major}, {minor}, {patch}, 0),
    prodvers=({major}, {minor}, {patch}, 0),
    mask=0x3f,
    flags=0x0,
    OS=0x40004,
    fileType=0x1,
    subtype=0x0,
    date=(0, 0)
  ),
  kids=[
    StringFileInfo([
      StringTable('040704B0', [
        StringStruct('CompanyName', 'Slot-Racing Platform'),
        StringStruct('FileDescription', 'Slot-Racing Platform'),
        StringStruct('FileVersion', '{version}'),
        StringStruct('InternalName', 'SlotRacingPlatform'),
        StringStruct('OriginalFilename', 'SlotRacingPlatform.exe'),
        StringStruct('ProductName', 'Slot-Racing Platform'),
        StringStruct('ProductVersion', '{version}')
      ])
    ]),
    VarFileInfo([VarStruct('Translation', [1031, 1200])])
  ]
)
"""


def project_version() -> str:
    declared = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"][
        "version"
    ]
    init = (ROOT / "src" / "slot_racing" / "__init__.py").read_text(encoding="utf-8")
    match = re.search(r'__version__\s*=\s*"([^"]+)"', init)
    if match is None or match.group(1) != declared:
        raise SystemExit("slot_racing.__version__ and pyproject.toml disagree")
    if not _VERSION.match(declared):
        raise SystemExit(f"the version must look like 1.2.3, not {declared!r}")
    return declared


def commit_id() -> str:
    value = os.environ.get("SLOT_RACING_COMMIT") or os.environ.get("GITHUB_SHA") or ""
    if not value:
        try:
            value = subprocess.run(
                ["git", "rev-parse", "HEAD"],
                cwd=ROOT,
                capture_output=True,
                text=True,
                check=True,
            ).stdout.strip()
        except (OSError, subprocess.CalledProcessError):
            value = ""
    return value[:40] if value.isalnum() else "unknown"


def write_icon(target: Path) -> None:
    from PIL import Image

    if not SOURCE.is_file():
        raise SystemExit(f"icon source is missing: {SOURCE}")
    with Image.open(SOURCE) as picture:
        picture.convert("RGBA").save(target, format="ICO", sizes=SIZES)


def main() -> int:
    version = project_version()
    major, minor, patch = (int(part) for part in version.split("."))
    BUILD.mkdir(parents=True, exist_ok=True)
    write_icon(BUILD / "slot-racing.ico")
    (BUILD / "version_info.txt").write_text(
        _VERSION_INFO.format(version=version, major=major, minor=minor, patch=patch),
        encoding="utf-8",
    )
    (BUILD / "BUILD_COMMIT").write_text(commit_id() + "\n", encoding="ascii")
    (BUILD / "version.iss").write_text(f'#define AppVersion "{version}"\n', encoding="utf-8")
    print(f"prepared build files for version {version}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
