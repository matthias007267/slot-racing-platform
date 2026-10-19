"""The Windows installer files stay consistent with the project. Nothing here needs Windows."""

from __future__ import annotations

import importlib.util
import re
import tomllib
from pathlib import Path
from types import ModuleType

from slot_racing import __version__
from slot_racing.app.icon import APPLICATION_ICON_NAME

ROOT = Path(__file__).resolve().parents[2]
PACKAGING = ROOT / "packaging" / "windows"


def _prepare() -> ModuleType:
    spec = importlib.util.spec_from_file_location("prepare_build", PACKAGING / "prepare_build.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_the_installer_version_is_the_project_version() -> None:
    declared = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    assert _prepare().project_version() == declared["project"]["version"] == __version__


def test_every_file_the_program_needs_at_run_time_is_bundled() -> None:
    spec = (PACKAGING / "slot-racing.spec").read_text(encoding="utf-8")
    for needed in (
        '"core" / "storage" / "migrations"',
        '"modules" / "track_planner" / "data"',
        '"resources" / "icons"',
        'copy_metadata("slot-racing-platform")',
        'collect_submodules("slot_racing")',
    ):
        assert needed in spec
    package = ROOT / "src" / "slot_racing"
    assert (package / "core" / "storage" / "migrations" / "env.py").is_file()
    assert any((package / "modules" / "track_planner" / "data").glob("*.json"))
    assert (ROOT / "resources" / "icons" / APPLICATION_ICON_NAME).is_file()


def test_the_installer_script_installs_the_program_and_has_no_update_function() -> None:
    script = (PACKAGING / "slot-racing.iss").read_text(encoding="utf-8")
    assert re.search(r"^AppId=\{\{[0-9A-F-]{36}\}$", script, re.MULTILINE)
    assert '{autoprograms}\\{#AppName}"; Filename: "{app}\\{#AppExe}"' in script
    assert "PrivilegesRequired=lowest" in script
    assert "[UninstallDelete]" not in script
    lowered = script.lower()
    assert "update" not in lowered.replace("the installer has no update function", "")
    assert "InstallDelete" not in script


def test_every_module_entry_point_is_a_package_that_the_bundle_collects() -> None:
    declared = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    entry_points = declared["project"]["entry-points"]["slot_racing.plugins"]
    for target in entry_points.values():
        module = target.split(":")[0]
        assert module.startswith("slot_racing.")
        relative = Path("src", *module.split("."))
        assert (ROOT / relative).with_suffix(".py").is_file()
