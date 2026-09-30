; Inno Setup 脚本 —— Windows Service Editor 引导式安装程序
; 需要 Inno Setup 6.x  https://jrsoftware.org/isinfo.php

#ifndef AppVersion
  #define AppVersion "0.8.0"
#endif

[Setup]
AppId={{B7E3F2A1-5C84-4D9B-A1F0-3E8C6D7A9B2F}
AppName=Windows Service Editor
AppVersion={#AppVersion}
AppVerName=Windows Service Editor {#AppVersion}
AppPublisher=Paper-Dragon
AppPublisherURL=https://github.com/Paper-Dragon/WindowsServiceEditor
AppSupportURL=https://github.com/Paper-Dragon/WindowsServiceEditor/issues
AppUpdatesURL=https://github.com/Paper-Dragon/WindowsServiceEditor/releases
DefaultDirName={autopf}\WindowsServiceEditor
DefaultGroupName=Windows Service Editor
DisableProgramGroupPage=yes
; 如果项目根目录有 LICENSE 文件，取消下行注释即可在安装向导中展示许可协议
; LicenseFile=LICENSE
OutputDir=dist
OutputBaseFilename=svc-edit-setup
SetupIconFile=app.ico
Compression=lzma2/ultra64
SolidCompression=yes
WizardStyle=modern
PrivilegesRequired=admin
UninstallDisplayIcon={app}\svc-edit.exe
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0

[Languages]
Name: "chinesesimplified"; MessagesFile: "compiler:Languages\ChineseSimplified.isl"
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked
Name: "quicklaunchicon"; Description: "{cm:CreateQuickLaunchIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked; OnlyBelowVersion: 6.1; Check: not IsAdminInstallMode

[Files]
Source: "dist\svc-edit.exe"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
Name: "{group}\Windows Service Editor"; Filename: "{app}\svc-edit.exe"
Name: "{group}\{cm:UninstallProgram,Windows Service Editor}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\Windows Service Editor"; Filename: "{app}\svc-edit.exe"; Tasks: desktopicon

[Run]
Filename: "{app}\svc-edit.exe"; Description: "{cm:LaunchProgram,Windows Service Editor}"; Flags: nowait postinstall skipifsilent runascurrentuser
