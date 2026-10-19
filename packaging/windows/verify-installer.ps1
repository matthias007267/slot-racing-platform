<#
.SYNOPSIS
Installs the built installer silently into a temporary folder, starts the installed program,
checks the Start menu entry and removes everything again. The user data folder is left alone.
#>
param(
    [Parameter(Mandatory = $true)][string]$Installer
)

$ErrorActionPreference = "Stop"
$installer = (Resolve-Path $Installer).Path
$target = Join-Path ([System.IO.Path]::GetTempPath()) ("slot-racing-install-" + [guid]::NewGuid())
$data = Join-Path ([System.IO.Path]::GetTempPath()) ("slot-racing-data-" + [guid]::NewGuid())
$menu = Join-Path $env:APPDATA "Microsoft\Windows\Start Menu\Programs\Slot-Racing Platform.lnk"
$exe = Join-Path $target "SlotRacingPlatform.exe"

function Assert-Condition {
    param([bool]$Condition, [string]$Message)
    if (-not $Condition) { throw $Message }
}

function Start-AndWait {
    param([string]$Path, [string[]]$Arguments)
    $process = Start-Process -FilePath $Path -ArgumentList $Arguments -Wait -PassThru
    return $process.ExitCode
}

$code = Start-AndWait $installer @("/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART", "/CURRENTUSER", "/DIR=`"$target`"", "/LANG=german")
Assert-Condition ($code -eq 0) "Installer exit code $code"
Assert-Condition (Test-Path $exe) "The program was not installed to $target"
Assert-Condition (Test-Path $menu) "No Start menu entry at $menu"
Assert-Condition (Test-Path (Join-Path $target "unins000.exe")) "No uninstaller was installed"

$env:SLOT_RACING_HOME = $data
try {
    $code = Start-AndWait $exe @("--smoke-test")
    Assert-Condition ($code -eq 0) "The installed program failed its start check ($code)"
    Assert-Condition (Test-Path (Join-Path $data "slot_racing.db")) "The installed program created no database"
} finally {
    Remove-Item Env:SLOT_RACING_HOME -ErrorAction SilentlyContinue
}

$uninstaller = Join-Path $target "unins000.exe"
$code = Start-AndWait $uninstaller @("/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART")
Assert-Condition ($code -eq 0) "Uninstaller exit code $code"
# The uninstaller hands over to a copy of itself, so wait until the files are gone.
for ($waited = 0; $waited -lt 30 -and (Test-Path $exe); $waited++) { Start-Sleep -Seconds 1 }
Assert-Condition (-not (Test-Path $exe)) "The program is still installed after uninstalling"
Assert-Condition (-not (Test-Path $menu)) "The Start menu entry is still there after uninstalling"
Assert-Condition (Test-Path (Join-Path $data "slot_racing.db")) "Uninstalling removed user data"
Remove-Item -Recurse -Force $data -ErrorAction SilentlyContinue
Write-Host "Installer check passed."
