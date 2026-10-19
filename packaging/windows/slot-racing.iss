; Inno Setup script of the Slot-Racing Platform. Build with packaging\windows\build.ps1.
; The version comes from build\version.iss, written by prepare_build.py from the project version.
; The installer has no update function. A newer installer replaces the program files of an older one.

#include "..\..\build\version.iss"

#define AppName "Slot-Racing Platform"
#define AppExe "SlotRacingPlatform.exe"

[Setup]
AppId={{420BCDF9-EE05-4C77-91D1-CDC80D360FB9}
AppName={#AppName}
AppVersion={#AppVersion}
AppVerName={#AppName} {#AppVersion}
AppPublisher={#AppName}
VersionInfoVersion={#AppVersion}
DefaultDirName={autopf}\{#AppName}
DefaultGroupName={#AppName}
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0
OutputDir=..\..\build\installer
OutputBaseFilename=SlotRacingPlatform-Setup-{#AppVersion}
SetupIconFile=..\..\build\slot-racing.ico
UninstallDisplayIcon={app}\{#AppExe}
UninstallDisplayName={#AppName}
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
CloseApplications=yes
RestartApplications=no

[Languages]
Name: "german"; MessagesFile: "compiler:Languages\German.isl"
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked

[Files]
Source: "..\..\build\dist\SlotRacingPlatform\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{autoprograms}\{#AppName}"; Filename: "{app}\{#AppExe}"; WorkingDir: "{app}"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\{#AppExe}"; WorkingDir: "{app}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#AppExe}"; Description: "{cm:LaunchProgram,{#AppName}}"; Flags: nowait postinstall skipifsilent

[CustomMessages]
german.DataKept=Ihre Fahrer, Fahrzeuge, Strecken, Rennen und Einstellungen wurden nicht gelöscht. Sie liegen im Ordner %1.
english.DataKept=Your drivers, vehicles, tracks, races and settings were not deleted. They are in the folder %1.

[Code]
procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
begin
  if CurUninstallStep = usPostUninstall then
    SuppressibleMsgBox(FmtMessage(CustomMessage('DataKept'), [ExpandConstant('{userappdata}\SlotRacingPlatform')]),
      mbInformation, MB_OK, IDOK);
end;
