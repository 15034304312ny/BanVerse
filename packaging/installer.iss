; 伴界 BanVerse 1.0.0 Windows 安装包（Inno Setup 6）
; 构建：ISCC.exe packaging\installer.iss
; 产物：dist\BanVerse-1.0.0-Setup.exe

#define MyAppName "伴界 BanVerse"
#define MyAppVersion "1.0.0"
#define MyAppExeName "BanVerse-1.0.0.exe"
#define MyAppPublisher "BanVerse"
#define MyAppExeFullPath "..\dist\BanVerse-1.0.0.exe"

[Setup]
AppId={{4A2E6E3C-9A1B-4D2F-B7C8-1E5A0F6D3B92}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppVerName={#MyAppName} {#MyAppVersion}
AppPublisher={#MyAppPublisher}
DefaultDirName={autopf}\伴界 BanVerse
DefaultGroupName={#MyAppName}
OutputDir=..\dist
OutputBaseFilename=BanVerse-1.0.0-Setup
SetupIconFile=app_icon.ico
UninstallDisplayIcon={app}\{#MyAppExeName}
UninstallDisplayName={#MyAppName}
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0.17763
PrivilegesRequired=admin
CloseApplications=yes
RestartApplications=no

[Languages]
Name: "chinesesimplified"; MessagesFile: "languages\ChineseSimplified.isl"
Name: "english"; MessagesFile: "compiler:Default.isl"

[Files]
Source: "{#MyAppExeFullPath}"; DestDir: "{app}"; Flags: ignoreversion
; 许可证说明由 About 页面显示；无单独 license 文件时不在此引用。

[Icons]
Name: "{group}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"
Name: "{group}\卸载 {#MyAppName}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "{cm:LaunchProgram,{#MyAppName}}"; Flags: nowait postinstall skipifsilent

[UninstallDelete]
Type: filesandordirs; Name: "{userappdata}\BanVerse"
