<#
.SYNOPSIS
Builds the Windows program and its installer.

.DESCRIPTION
Run from any folder on Windows with uv and Inno Setup 6 installed. The result is
build\installer\SlotRacingPlatform-Setup-<version>.exe. The program has no update function.
#>
param(
    [switch]$SkipInstaller
)

$ErrorActionPreference = "Stop"
Set-Location (Resolve-Path (Join-Path $PSScriptRoot "..\.."))

function Invoke-Checked {
    param([string]$What, [scriptblock]$Command)
    Write-Host "== $What"
    & $Command
    if ($LASTEXITCODE -ne 0) {
        throw "$What failed with exit code $LASTEXITCODE"
    }
}

function Invoke-Gui {
    # A program without a console is not waited for by "&", so wait for it and read its exit code.
    param([string]$What, [string]$Path, [string[]]$Arguments = @())
    Write-Host "== $What"
    if ($Arguments.Count -gt 0) {
        $process = Start-Process -FilePath $Path -ArgumentList $Arguments -Wait -PassThru
    } else {
        $process = Start-Process -FilePath $Path -Wait -PassThru
    }
    if ($process.ExitCode -ne 0) {
        throw "$What failed with exit code $($process.ExitCode)"
    }
}

function Find-InnoSetup {
    $found = Get-Command iscc -ErrorAction SilentlyContinue
    if ($found) { return $found.Source }
    $candidates = @(
        "${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe",
        "${env:ProgramFiles}\Inno Setup 6\ISCC.exe",
        "${env:LOCALAPPDATA}\Programs\Inno Setup 6\ISCC.exe"
    )
    foreach ($candidate in $candidates) {
        if ($candidate -and (Test-Path $candidate)) { return $candidate }
    }
    throw "Inno Setup 6 was not found. Install it from https://jrsoftware.org/isinfo.php"
}

Invoke-Checked "Install build tools" { uv sync --locked --group installer }
Invoke-Checked "Prepare icon, version and commit" { uv run python packaging\windows\prepare_build.py }

Remove-Item -Recurse -Force build\dist, build\work -ErrorAction SilentlyContinue
Invoke-Checked "Build the program" {
    uv run pyinstaller packaging\windows\slot-racing.spec --noconfirm --distpath build\dist --workpath build\work
}

$exe = Join-Path (Get-Location) "build\dist\SlotRacingPlatform\SlotRacingPlatform.exe"
if (-not (Test-Path $exe)) { throw "The program file was not built: $exe" }

# The program starts, loads every module, migrates a fresh database and exits.
$home_dir = Join-Path ([System.IO.Path]::GetTempPath()) ("slot-racing-build-check-" + [guid]::NewGuid())
$env:SLOT_RACING_HOME = $home_dir
try {
    Invoke-Gui "Start the built program" $exe @("--smoke-test")
    if (-not (Test-Path (Join-Path $home_dir "slot_racing.db"))) {
        throw "The built program did not create its database"
    }
} finally {
    Remove-Item Env:SLOT_RACING_HOME -ErrorAction SilentlyContinue
    Remove-Item -Recurse -Force $home_dir -ErrorAction SilentlyContinue
}

if ($SkipInstaller) { return }

$iscc = Find-InnoSetup
Invoke-Checked "Build the installer" { & $iscc packaging\windows\slot-racing.iss }
Get-ChildItem build\installer\*.exe | ForEach-Object { Write-Host "Installer: $($_.FullName)" }
