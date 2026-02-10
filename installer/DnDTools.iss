; Inno Setup script for packaging the DnDTools application
#define MyAppName "DnDTools"
#define MyAppPublisher "Beelzebub2"
#define MyAppURL "https://github.com/Beelzebub2/DnDTools"
#define MyAppExeName "DnDTools.exe"
#define WiresharkDownloadURL "https://www.wireshark.org/download.html"
#define WiresharkDisplayVer "latest"

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
; Modern style with dynamic light/dark following the OS setting.
; windows11 provides a styled light theme that pairs with the built-in dark style
; so neither mode looks like a plain white or plain black shell.
WizardStyle=modern dynamic windows11

; ── Light-mode colours (used when Windows is in light mode) ──
WizardImageFile=..\\UI\\assets\\banner.bmp
WizardImageBackColor=$F0F0F0
WizardSmallImageFile=..\\UI\\assets\\logo.png
WizardSmallImageBackColor=$F0F0F0

; ── Dark-mode colours (used when Windows is in dark mode) ──
; These match the app's own #0b0b0b / #0e0e0e palette.
WizardImageFileDynamicDark=..\\UI\\assets\\banner.bmp
WizardImageBackColorDynamicDark=$0b0b0b
WizardSmallImageFileDynamicDark=..\\UI\\assets\\logo.png
WizardSmallImageBackColorDynamicDark=$111111

SetupIconFile=..\\UI\\assets\\logo.ico
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

[Code]
// ─── Wireshark detection & notification ───────────────────────────────
// Checks common install paths, the registry, and PATH for tshark.exe.
// If not found, a custom wizard page lets the user open the download site.

var
  WiresharkPage: TWizardPage;
  WiresharkMsgLabel: TNewStaticText;
  WiresharkOpenBtn: TNewButton;
  WiresharkFound: Boolean;

// Return True when tshark.exe (or Wireshark.exe) can be located
function IsWiresharkInstalled: Boolean;
var
  RegPath: String;
begin
  Result := False;

  // 1. Common default directories
  if FileExists(ExpandConstant('{pf}\Wireshark\tshark.exe')) then begin Result := True; Exit; end;
  if FileExists(ExpandConstant('{pf32}\Wireshark\tshark.exe')) then begin Result := True; Exit; end;

  // 2. Registry – Wireshark writes its install dir here
  if RegQueryStringValue(HKLM, 'SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\wireshark.exe', '', RegPath) then
  begin
    RegPath := ExtractFileDir(RegPath);
    if FileExists(RegPath + '\tshark.exe') then begin Result := True; Exit; end;
  end;

  // 3. 32-bit view for 64-bit OS
  if RegQueryStringValue(HKLM32, 'SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\wireshark.exe', '', RegPath) then
  begin
    RegPath := ExtractFileDir(RegPath);
    if FileExists(RegPath + '\tshark.exe') then begin Result := True; Exit; end;
  end;

  // 4. Wireshark uninstall key (another common location)
  if RegQueryStringValue(HKLM, 'SOFTWARE\Wireshark', 'InstallDir', RegPath) then
  begin
    if FileExists(RegPath + '\tshark.exe') then begin Result := True; Exit; end;
  end;
end;

procedure WiresharkOpenBtnClick(Sender: TObject);
var
  ErrorCode: Integer;
begin
  ShellExec('open', '{#WiresharkDownloadURL}', '', '', SW_SHOWNORMAL, ewNoWait, ErrorCode);
end;

procedure InitializeWizard;
var
  IconLabel: TNewStaticText;
  TitleLabel: TNewStaticText;
  HintLabel: TNewStaticText;
begin
  WiresharkFound := IsWiresharkInstalled;

  // Always create the page so the IDs are stable; we skip it dynamically
  WiresharkPage := CreateCustomPage(
    wpSelectDir,
    'Wireshark Required',
    '{#MyAppName} needs Wireshark (tshark) to capture network packets.');

  // ── Icon ──
  IconLabel := TNewStaticText.Create(WiresharkPage);
  IconLabel.Parent  := WiresharkPage.Surface;
  IconLabel.Caption := '⚠';
  IconLabel.Font.Size := 28;
  IconLabel.Font.Color := $0049CF;  // gold-ish (#CF9A00 → BGR)
  IconLabel.Left := ScaleX(0);
  IconLabel.Top  := ScaleY(8);
  IconLabel.AutoSize := True;

  // ── Title ──
  TitleLabel := TNewStaticText.Create(WiresharkPage);
  TitleLabel.Parent  := WiresharkPage.Surface;
  TitleLabel.Caption := 'Wireshark was not detected on this computer.';
  TitleLabel.Font.Size  := 11;
  TitleLabel.Font.Style := [fsBold];
  TitleLabel.Left := ScaleX(48);
  TitleLabel.Top  := ScaleY(16);
  TitleLabel.AutoSize := True;

  // ── Description ──
  WiresharkMsgLabel := TNewStaticText.Create(WiresharkPage);
  WiresharkMsgLabel.Parent   := WiresharkPage.Surface;
  WiresharkMsgLabel.WordWrap := True;
  WiresharkMsgLabel.Left   := ScaleX(0);
  WiresharkMsgLabel.Top    := ScaleY(60);
  WiresharkMsgLabel.Width  := WiresharkPage.SurfaceWidth;
  WiresharkMsgLabel.Caption :=
    '{#MyAppName} requires Wireshark''s tshark component to capture and decode ' +
    'Dark and Darker network traffic.' + #13#10 + #13#10 +
    'You can continue the installation now and install Wireshark later, but ' +
    'packet capture features will not work until tshark.exe is available.' + #13#10 + #13#10 +
    'Click the button below to open the Wireshark download page, or press Next to continue anyway.';

  // ── Download button ──
  WiresharkOpenBtn := TNewButton.Create(WiresharkPage);
  WiresharkOpenBtn.Parent  := WiresharkPage.Surface;
  WiresharkOpenBtn.Caption := '  Download Wireshark  ';
  WiresharkOpenBtn.Left   := ScaleX(0);
  WiresharkOpenBtn.Top    := ScaleY(180);
  WiresharkOpenBtn.Width  := ScaleX(180);
  WiresharkOpenBtn.Height := ScaleY(32);
  WiresharkOpenBtn.OnClick := @WiresharkOpenBtnClick;

  // ── Hint ──
  HintLabel := TNewStaticText.Create(WiresharkPage);
  HintLabel.Parent   := WiresharkPage.Surface;
  HintLabel.Caption  := 'You can also set the Wireshark path later in Settings → Capture & Controls.';
  HintLabel.Font.Size := 8;
  HintLabel.Left  := ScaleX(0);
  HintLabel.Top   := ScaleY(222);
  HintLabel.Width := WiresharkPage.SurfaceWidth;
  HintLabel.AutoSize := True;
end;

// Skip the Wireshark page when it is already installed
function ShouldSkipPage(PageID: Integer): Boolean;
begin
  Result := False;
  if (PageID = WiresharkPage.ID) and WiresharkFound then
    Result := True;
end;