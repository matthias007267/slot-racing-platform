"""Start.bat launches the documented command from the project directory."""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
START_BAT = ROOT / "Start.bat"


def test_windows_start_script_uses_the_project_directory_and_official_command() -> None:
    raw = START_BAT.read_bytes()
    assert b"\r\n" in raw
    text = raw.decode("utf-8")
    lines = text.splitlines()

    assert lines[0] == "@echo off"
    cd_at = text.index('cd /d "%~dp0."')
    official_at = text.index('"%UV_CMD%" run slot-racing')
    assert "uv run slot-racing" in text
    assert cd_at < official_at
    assert ".venv\\Scripts\\python.exe" in text
    assert "-m slot_racing.app" in text
    assert "Activate.ps1" not in text
    assert "powershell" not in text.casefold()
    assert text.index("exit /b 0") < text.index("pause")
    assert ":start_failed" in text
    assert ":cd_failed" in text
