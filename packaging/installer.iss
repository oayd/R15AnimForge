; R15 AnimForge Per-User Installer Script (Inno Setup 6+)
#define AppName "R15 AnimForge"
#define AppVersion "1.0.0"
#define AppExeName "AnimForge.exe"
#define DistRoot "..\dist\AnimForge"

[Setup]
AppId={{8E9A7C2D-4C2B-4E11-9F60-A15F02DE4B10}
AppName={#AppName}
AppVersion={#AppVersion}
AppPublisher=R15 AnimForge
DefaultDirName={localappdata}\Programs\R15AnimForge
DefaultGroupName={#AppName}
PrivilegesRequired=lowest
OutputDir=..\dist
OutputBaseFilename=AnimForge_Setup
SetupIconFile=animforge.ico
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
UninstallDisplayIcon={app}\{#AppExeName}
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked

[Files]
Source: "{#DistRoot}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\{#AppName}"; Filename: "{app}\{#AppExeName}"
Name: "{group}\Uninstall {#AppName}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\{#AppExeName}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#AppExeName}"; Description: "{cm:LaunchProgram,{#StringChange(AppName, '&', '&&')}}"; Flags: nowait postinstall skipifsilent
