# PyInstaller specification of the Windows program. Build with packaging/windows/build.ps1.
# It also runs on Linux so the bundle contents can be checked in CI without Windows.
import os
import sys
from pathlib import Path

from PyInstaller.utils.hooks import collect_submodules, copy_metadata

ROOT = Path(SPECPATH).resolve().parents[1]
BUILD = ROOT / "build"
ICON = BUILD / "slot-racing.ico"
COMMIT = BUILD / "BUILD_COMMIT"
PACKAGE = ROOT / "src" / "slot_racing"

# Plugins are found through entry points, so the distribution metadata has to ship with the
# program, and every module has to be importable by name (plugins import by string).
hiddenimports = collect_submodules("slot_racing")
datas = copy_metadata("slot-racing-platform")
# Alembic loads the migration scripts from disk, so the whole folder is copied as files.
datas += [(str(PACKAGE / "core" / "storage" / "migrations"), "slot_racing/core/storage/migrations")]
datas += [(str(PACKAGE / "modules" / "track_planner" / "data"), "slot_racing/modules/track_planner/data")]
datas += [(str(ROOT / "resources" / "icons"), "resources/icons")]
datas += [(str(ROOT / "LICENSE"), ".")]
if COMMIT.is_file():
    datas += [(str(COMMIT), ".")]

excludes = ["tkinter", "pytest", "mypy", "ruff", "IPython", "PyInstaller", "PIL"]

a = Analysis(
    [str(ROOT / "packaging" / "windows" / "launcher.py")],
    pathex=[str(ROOT / "src")],
    datas=datas,
    hiddenimports=hiddenimports,
    excludes=excludes,
    noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="SlotRacingPlatform",
    console=False,
    icon=str(ICON) if sys.platform == "win32" and ICON.is_file() else None,
    version=str(BUILD / "version_info.txt") if (BUILD / "version_info.txt").is_file() else None,
    upx=False,
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    upx=False,
    name="SlotRacingPlatform",
)
