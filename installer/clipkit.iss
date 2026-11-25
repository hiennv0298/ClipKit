; Inno Setup 6 script for ClipKit. Built by scripts\build.ps1 after PyInstaller.
; Installs per user (no admin) into %LOCALAPPDATA%\Programs\ClipKit.

#ifndef AppVersion
  #define AppVersion "0.3.2"
#endif

[Setup]
AppId={{8C1E5B52-4E0B-4E52-9A43-6E3B7C2F9D11}
AppName=ClipKit
AppVersion={#AppVersion}
AppPublisher=ClipKit
AppComments=Cut long videos into captioned vertical clips (yt-dlp + Whisper + FFmpeg)
DefaultDirName={localappdata}\Programs\ClipKit
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
OutputDir=..\dist
OutputBaseFilename=ClipKit-Setup-{#AppVersion}
Compression=lzma2
SolidCompression=yes
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
ChangesEnvironment=yes
WizardStyle=modern
SetupIconFile=..\src\clipkit\webui\clipkit.ico
UninstallDisplayIcon={app}\ClipKitApp.exe
UninstallDisplayName=ClipKit

[Languages]
Name: "en"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "Create a desktop shortcut"; GroupDescription: "Shortcuts:"
Name: "addtopath"; Description: "Add ClipKit to PATH (lets you type 'clipkit' in cmd)"; GroupDescription: "Command line:"

[Files]
Source: "..\dist\ClipKit\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{autoprograms}\ClipKit"; Filename: "{app}\ClipKitApp.exe"
Name: "{autodesktop}\ClipKit"; Filename: "{app}\ClipKitApp.exe"; Tasks: desktopicon

[Registry]
Root: HKCU; Subkey: "Environment"; ValueType: expandsz; ValueName: "Path"; \
  ValueData: "{olddata};{app}"; Tasks: addtopath; Check: NeedsAddPath(ExpandConstant('{app}'))

[Run]
Filename: "{app}\ClipKitApp.exe"; Description: "Open ClipKit now"; Flags: postinstall nowait skipifsilent
; The in-app updater runs this installer with /VERYSILENT /RELAUNCH=1: reopen ClipKit when done.
Filename: "{app}\ClipKitApp.exe"; Flags: nowait; Check: RelaunchRequested

[Code]
function RelaunchRequested: Boolean;
begin
  Result := ExpandConstant('{param:relaunch|0}') = '1';
end;

function NeedsAddPath(Dir: string): Boolean;
var
  Paths: string;
begin
  if not RegQueryStringValue(HKEY_CURRENT_USER, 'Environment', 'Path', Paths) then
  begin
    Result := True;
    exit;
  end;
  Result := Pos(';' + Uppercase(Dir) + ';', ';' + Uppercase(Paths) + ';') = 0;
end;

procedure RemoveFromPath(Dir: string);
var
  Paths: string;
  P: Integer;
begin
  if not RegQueryStringValue(HKEY_CURRENT_USER, 'Environment', 'Path', Paths) then
    exit;
  P := Pos(';' + Uppercase(Dir), Uppercase(Paths));
  if P > 0 then
  begin
    Delete(Paths, P, Length(Dir) + 1);
    RegWriteExpandStringValue(HKEY_CURRENT_USER, 'Environment', 'Path', Paths);
  end;
end;

procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
begin
  if CurUninstallStep = usPostUninstall then
    RemoveFromPath(ExpandConstant('{app}'));
end;
