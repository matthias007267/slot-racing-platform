@echo off
setlocal EnableExtensions

rem Start the Slot-Racing Platform from this file's folder, wherever the shortcut lives.
rem Official command: uv run slot-racing (see pyproject.toml and README).
rem uv uses the project environment .venv directly.

rem "%~dp0." avoids a trailing backslash escaping the closing quote.
cd /d "%~dp0." || goto :cd_failed

set "UV_CMD="
where uv >nul 2>&1 && set "UV_CMD=uv"
if not defined UV_CMD if exist "%USERPROFILE%\.local\bin\uv.exe" set "UV_CMD=%USERPROFILE%\.local\bin\uv.exe"
if not defined UV_CMD if exist "%USERPROFILE%\.cargo\bin\uv.exe" set "UV_CMD=%USERPROFILE%\.cargo\bin\uv.exe"

if defined UV_CMD (
  set "START_LABEL=uv run slot-racing"
  "%UV_CMD%" run slot-racing %*
  if errorlevel 1 goto :start_failed
  exit /b 0
)

if exist "%~dp0.venv\Scripts\python.exe" (
  set "START_LABEL=python -m slot_racing.app"
  "%~dp0.venv\Scripts\python.exe" -m slot_racing.app %*
  if errorlevel 1 goto :start_failed
  exit /b 0
)

echo.
echo Die Slot-Racing Platform kann nicht gestartet werden.
echo uv wurde nicht gefunden, und die Projektumgebung .venv fehlt.
echo.
echo uv installieren: https://docs.astral.sh/uv/
echo Danach im Projektordner einmal ausfuehren: uv sync
echo Anschliessend Start.bat erneut starten.
echo.
echo Projektordner: %~dp0
echo.
pause
exit /b 1

:cd_failed
echo.
echo Der Projektordner konnte nicht geoeffnet werden:
echo %~dp0
echo.
pause
exit /b 1

:start_failed
echo.
echo Die Slot-Racing Platform konnte nicht gestartet werden.
echo Befehl: %START_LABEL%
echo Projektordner: %~dp0
echo.
echo Falls Abhaengigkeiten fehlen, im Projektordner ausfuehren: uv sync
echo.
pause
exit /b 1
