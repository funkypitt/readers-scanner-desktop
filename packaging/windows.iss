; The Windows installer of Reader's Scanner (Inno Setup 6). Built by the workflow:
;   iscc /DVersion=1.0.0 /DSource=..\dist\Readers Scanner /DOut=..\out packaging\windows.iss
; Installs for the current user, without asking for administrator rights.

#ifndef Version
  #define Version "0.0.0"
#endif
#ifndef Source
  #define Source "..\dist\Readers Scanner"
#endif
#ifndef Out
  #define Out "..\out"
#endif

[Setup]
AppId={{6F0B1C7E-52B0-4B7C-9E0D-7A3B7C1D5A21}
AppName=Reader's Scanner
AppVersion={#Version}
AppPublisher=Pierre Gallaz
AppPublisherURL=https://github.com/funkypitt/readers-scanner-desktop
DefaultDirName={autopf}\Readers Scanner
DefaultGroupName=Reader's Scanner
DisableProgramGroupPage=yes
DisableDirPage=yes
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
OutputDir={#Out}
OutputBaseFilename=readers-scanner_{#Version}_windows_x64_setup
SetupIconFile=readers-scanner.ico
UninstallDisplayIcon={app}\Readers Scanner.exe
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
InfoBeforeFile=windows-before.txt

[Languages]
Name: "en"; MessagesFile: "compiler:Default.isl"
Name: "fr"; MessagesFile: "compiler:Languages\French.isl"
Name: "de"; MessagesFile: "compiler:Languages\German.isl"
Name: "es"; MessagesFile: "compiler:Languages\Spanish.isl"
Name: "pt"; MessagesFile: "compiler:Languages\Portuguese.isl"
Name: "ru"; MessagesFile: "compiler:Languages\Russian.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"

[Files]
Source: "{#Source}\*"; DestDir: "{app}"; Flags: recursesubdirs ignoreversion

[Icons]
Name: "{autoprograms}\Reader's Scanner"; Filename: "{app}\Readers Scanner.exe"
Name: "{autodesktop}\Reader's Scanner"; Filename: "{app}\Readers Scanner.exe"; Tasks: desktopicon

[Run]
Filename: "{app}\Readers Scanner.exe"; Description: "{cm:LaunchProgram,Reader's Scanner}"; Flags: nowait postinstall skipifsilent
