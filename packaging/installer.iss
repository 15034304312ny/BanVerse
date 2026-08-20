; 伴界 BanVerse Windows 安装包（Inno Setup 6）
; 必须经 packaging\build_windows.ps1 构建，由脚本从唯一版本源注入版本号。

#define MyAppName "伴界 BanVerse"
#ifndef MyAppVersion
  #error MyAppVersion is required; run packaging\build_windows.ps1
#endif
#define MyAppExeName "BanVerse-" + MyAppVersion + ".exe"
#define MyAppPublisher "BanVerse"
#define MyAppExeFullPath "..\dist\" + MyAppExeName

[Setup]
AppId={{4A2E6E3C-9A1B-4D2F-B7C8-1E5A0F6D3B92}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppVerName={#MyAppName} {#MyAppVersion}
AppPublisher={#MyAppPublisher}
DefaultDirName={autopf}\伴界 BanVerse
DefaultGroupName={#MyAppName}
OutputDir=..\dist
OutputBaseFilename=BanVerse-{#MyAppVersion}-Setup
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
#ifdef BanVerseSignedBuild
SignTool=banverse
SignedUninstaller=yes
SignToolRetryCount=3
#endif

[Languages]
Name: "chinesesimplified"; MessagesFile: "languages\ChineseSimplified.isl"
Name: "english"; MessagesFile: "compiler:Default.isl"

[Files]
#ifdef BanVerseSignedBuild
Source: "{#MyAppExeFullPath}"; DestDir: "{app}"; Flags: ignoreversion signonce
#else
Source: "{#MyAppExeFullPath}"; DestDir: "{app}"; Flags: ignoreversion
#endif
Source: "..\LICENSE"; DestDir: "{app}"; DestName: "LICENSE.txt"; Flags: ignoreversion
Source: "..\NOTICE"; DestDir: "{app}"; DestName: "NOTICE.txt"; Flags: ignoreversion
Source: "..\THIRD_PARTY_NOTICES.md"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
Name: "{group}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"
Name: "{group}\卸载 {#MyAppName}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "{cm:LaunchProgram,{#MyAppName}}"; Flags: nowait postinstall skipifsilent

; 会话数据库、角色、API 配置与媒体文件属于用户数据，卸载时明确保留。
; 用户可在重新安装后继续使用；彻底删除应由应用内显式操作完成。
