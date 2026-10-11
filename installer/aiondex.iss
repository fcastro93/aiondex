; AionDex installer (Inno Setup 6). Built by build_exe.py --installer and by the release workflow.
; Per-user install (no admin prompt), so the app can update itself silently.

#ifndef AppVersion
  #define AppVersion "0.0.0"
#endif
#define AppName "AionDex"
#define AppExe "AionDex.exe"

[Setup]
AppId={{6F1C2B8E-4E7A-4C1B-9F2D-AD10DE5C0A11}
AppName={#AppName}
AppVersion={#AppVersion}
AppVerName={#AppName} {#AppVersion}
AppPublisher=fcastro93
AppPublisherURL=https://github.com/fcastro93/aiondex
AppSupportURL=https://github.com/fcastro93/aiondex/issues
AppUpdatesURL=https://github.com/fcastro93/aiondex/releases
DefaultDirName={localappdata}\Programs\{#AppName}
DisableProgramGroupPage=yes
DisableDirPage=yes
PrivilegesRequired=lowest
OutputDir=..\dist\installer
OutputBaseFilename={#AppName}-Setup-{#AppVersion}
SetupIconFile=..\assets\app.ico
UninstallDisplayIcon={app}\{#AppExe}
WizardStyle=modern
Compression=lzma2/max
SolidCompression=yes
CloseApplications=force
RestartApplications=no

[Languages]
Name: "es"; MessagesFile: "compiler:Languages\Spanish.isl"
Name: "en"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"

[Files]
Source: "..\dist\{#AppName}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[InstallDelete]
; old program files from a previous version (user data lives in %LOCALAPPDATA%\AionDex and is kept)
Type: filesandordirs; Name: "{app}\_internal"

[Icons]
Name: "{autoprograms}\{#AppName}"; Filename: "{app}\{#AppExe}"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\{#AppExe}"; Tasks: desktopicon

[Run]
; interactive install: offer to launch
Filename: "{app}\{#AppExe}"; Description: "{cm:LaunchProgram,{#AppName}}"; Flags: nowait postinstall skipifsilent
; silent self-update from the app (/UPDATE): always start it again
Filename: "{app}\{#AppExe}"; Flags: nowait; Check: IsSelfUpdate

[Code]
function IsSelfUpdate: Boolean;
var
  I: Integer;
begin
  Result := False;
  for I := 1 to ParamCount do
    if CompareText(ParamStr(I), '/UPDATE') = 0 then
      Result := True;
end;

function NpcapInstalled: Boolean;
begin
  Result := RegKeyExists(HKLM, 'SOFTWARE\Npcap') or RegKeyExists(HKLM, 'SOFTWARE\WOW6432Node\Npcap')
    or RegKeyExists(HKLM, 'SYSTEM\CurrentControlSet\Services\npcap');
end;

procedure CurStepChanged(CurStep: TSetupStep);
var
  ErrorCode: Integer;
begin
  if (CurStep = ssDone) and (not WizardSilent) and (not NpcapInstalled) then
    if MsgBox('AionDex necesita Npcap para leer el tráfico del juego y no está instalado.' + #13#10 +
              '¿Abrir la página de descarga de Npcap?', mbConfirmation, MB_YESNO) = IDYES then
      ShellExec('open', 'https://npcap.com/#download', '', '', SW_SHOWNORMAL, ewNoWait, ErrorCode);
end;
