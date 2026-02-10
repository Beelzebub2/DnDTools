; Inno Setup script for packaging the DnDTools application
#define MyAppName "DnDTools"
#define MyAppPublisher "Beelzebub2"
#define MyAppURL "https://github.com/Beelzebub2/DnDTools"
#define MyAppExeName "DnDTools.exe"

#ifndef MyAppVersion
  #include "version.iss"
#endif

#ifndef MyAppVersion
  #define MyAppVersion "0.0.0"
#endif

#define MyAppGuid "{{53B7AA0E-3147-4E8E-AC93-5C6E39A10676}}"

[Setup]
AppId={#MyAppGuid}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
AppPublisherURL={#MyAppURL}
DefaultDirName={autopf}\{#MyAppName}
DefaultGroupName={#MyAppName}
DisableProgramGroupPage=yes
LicenseFile=..\LICENSE
Compression=lzma2
SolidCompression=yes
WizardStyle=modern dynamic includetitlebar hidebevels

; Dark theme colours that match the application's palette
WizardBackColor=#111111
WizardBackColorDynamicDark=#0b0b0b

; Main left-side image (welcome/finish). Provide a dark variant for dynamic dark mode as well.
WizardImageFile=..\UI\assets\banner.jpg
WizardImageFileDynamicDark=..\UI\assets\banner.jpg
WizardImageBackColor=#0e0e0e
WizardImageBackColorDynamicDark=#0b0b0b

; Small logo (used in titlebars and dialogs)
WizardSmallImageFile=..\UI\assets\logo.png
WizardSmallImageFileDynamicDark=..\UI\assets\logo.png
WizardSmallImageBackColor=#111111

SetupIconFile=..\UI\assets\logo.ico
UninstallDisplayIcon={app}\{#MyAppExeName}
OutputBaseFilename={#MyAppName}-Setup-{#MyAppVersion}
OutputDir=..\dist
ArchitecturesInstallIn64BitMode=x64compatible
ArchitecturesAllowed=x64compatible
PrivilegesRequired=admin
CloseApplications=force
RestartApplications=yes
AllowNoIcons=yes
ChangesAssociations=no

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked

[Files]
Source: "..\dist\DnDTools\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "..\dist\update.exe"; DestDir: "{app}"; Flags: ignoreversion 

[Icons]
Name: "{autoprograms}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "{cm:LaunchProgram,{#MyAppName}}"; Flags: nowait postinstall skipifsilent
