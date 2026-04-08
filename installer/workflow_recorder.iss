#define MyAppName "Workflow Recorder"
#define MyAppVersion "0.1.0"
#define MyAppPublisher "Workflow Recorder"
#define MyAppExeName "workflow-recorder.exe"
#define MyAppURL "https://github.com/gaozhi-ustc/computer-use"

[Setup]
AppId={{B7E4F2A1-3D5C-4E8A-9B1F-2C6D7E8F9A0B}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
AppPublisherURL={#MyAppURL}
DefaultDirName={autopf}\WorkflowRecorder
DefaultGroupName={#MyAppName}
AllowNoIcons=yes
OutputDir=..\dist
OutputBaseFilename=WorkflowRecorder-{#MyAppVersion}-Setup
Compression=lzma2/ultra64
SolidCompression=yes
WizardStyle=modern
PrivilegesRequired=admin
ArchitecturesInstallIn64BitMode=x64compatible
UninstallDisplayIcon={app}\workflow-recorder.exe
UninstallFilesDir={app}\uninst

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked
Name: "addtopath"; Description: "Add to system PATH (run from any terminal)"; GroupDescription: "System integration:"
Name: "installservice"; Description: "Install as Windows service (auto-start on boot)"; GroupDescription: "Service options:"; Flags: unchecked

[Files]
; PyInstaller output
Source: "..\dist\WorkflowRecorder\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

; Config: copy to app root for easy access; only create config.yaml on first install
Source: "..\config.example.yaml"; DestDir: "{app}"; DestName: "config.example.yaml"; Flags: ignoreversion
Source: "..\config.example.yaml"; DestDir: "{app}"; DestName: "config.yaml"; Flags: onlyifdoesntexist uninsneveruninstall
; Also keep the bundled copy in _internal for the exe to find
Source: "..\config.example.yaml"; DestDir: "{app}\_internal"; DestName: "config.example.yaml"; Flags: ignoreversion

[Dirs]
Name: "{app}\workflows"; Permissions: users-full
Name: "{app}\logs"; Permissions: users-full

[Icons]
Name: "{group}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Parameters: "-c ""{app}\config.yaml"""; WorkingDir: "{app}"
Name: "{group}\Edit Configuration"; Filename: "notepad.exe"; Parameters: """{app}\config.yaml"""
Name: "{group}\{cm:UninstallProgram,{#MyAppName}}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Parameters: "-c ""{app}\config.yaml"""; WorkingDir: "{app}"; Tasks: desktopicon

[Run]
Filename: "notepad.exe"; Parameters: """{app}\config.yaml"""; Description: "Edit configuration (set your API key)"; Flags: nowait postinstall skipifsilent unchecked

[UninstallRun]
Filename: "{app}\workflow-recorder-service.exe"; Parameters: "stop"; Flags: runhidden; RunOnceId: "StopSvc"
Filename: "{app}\workflow-recorder-service.exe"; Parameters: "remove"; Flags: runhidden; RunOnceId: "RemoveSvc"

[Code]
procedure AddToPath(Dir: string);
var
  Path: string;
begin
  if not RegQueryStringValue(HKLM, 'SYSTEM\CurrentControlSet\Control\Session Manager\Environment', 'Path', Path) then
    Path := '';
  if Pos(Uppercase(Dir), Uppercase(Path)) = 0 then
  begin
    if Path <> '' then Path := Path + ';';
    Path := Path + Dir;
    RegWriteStringValue(HKLM, 'SYSTEM\CurrentControlSet\Control\Session Manager\Environment', 'Path', Path);
  end;
end;

procedure RemoveFromPath(Dir: string);
var
  Path, NewPath: string;
  P: Integer;
begin
  if not RegQueryStringValue(HKLM, 'SYSTEM\CurrentControlSet\Control\Session Manager\Environment', 'Path', Path) then exit;
  P := Pos(Uppercase(Dir), Uppercase(Path));
  if P > 0 then
  begin
    NewPath := Copy(Path, 1, P - 1) + Copy(Path, P + Length(Dir) + 1, MaxInt);
    if (Length(NewPath) > 0) and (NewPath[1] = ';') then
      NewPath := Copy(NewPath, 2, MaxInt);
    RegWriteStringValue(HKLM, 'SYSTEM\CurrentControlSet\Control\Session Manager\Environment', 'Path', NewPath);
  end;
end;

procedure CurStepChanged(CurStep: TSetupStep);
var
  ResultCode: Integer;
begin
  if CurStep = ssPostInstall then
  begin
    if IsTaskSelected('addtopath') then
      AddToPath(ExpandConstant('{app}'));
    if IsTaskSelected('installservice') then
    begin
      Exec(ExpandConstant('{app}\workflow-recorder-service.exe'), 'install',
           ExpandConstant('{app}'), SW_HIDE, ewWaitUntilTerminated, ResultCode);
      Exec(ExpandConstant('{app}\workflow-recorder-service.exe'), 'start',
           ExpandConstant('{app}'), SW_HIDE, ewWaitUntilTerminated, ResultCode);
    end;
  end;
end;

procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
begin
  if CurUninstallStep = usPostUninstall then
    RemoveFromPath(ExpandConstant('{app}'));
end;
