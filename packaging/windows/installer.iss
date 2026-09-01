#ifndef AppVersion
  #error AppVersion is required
#endif
#ifndef PayloadDir
  #error PayloadDir is required
#endif
#ifndef OutputDir
  #error OutputDir is required
#endif
#ifndef AppIcon
  #error AppIcon is required
#endif
#ifndef SignedBuild
  #define SignedBuild "0"
#endif

#define AppName "SKYWriter Prototype"
#define AppExeName "SKYWriter.exe"
#if SignedBuild == "1"
  #define BuildQualifier "signed build"
#else
  #define BuildQualifier "unsigned build"
#endif

[Setup]
AppId={{C67A9057-226E-4BB8-9437-31A092733D88}
AppName={#AppName}
AppVersion={#AppVersion}
AppVerName={#AppName} {#AppVersion}
AppPublisher=305 Skylab
AppSupportURL=https://github.com/noob2027/Skywriter/issues
AppComments=Prototype installer and hardware-candidate precursor; not flight-validated. {#BuildQualifier}.
DefaultDirName={localappdata}\Programs\SKYWriter Prototype
DefaultGroupName=SKYWriter Prototype
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
OutputDir={#OutputDir}
OutputBaseFilename=SKYWriter-Prototype-Setup-{#AppVersion}
SetupIconFile={#AppIcon}
UninstallDisplayIcon={app}\{#AppExeName}
UninstallDisplayName={#AppName} {#AppVersion}
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
CloseApplications=yes
RestartApplications=no
ChangesAssociations=no
VersionInfoVersion={#AppVersion}.0
VersionInfoProductVersion={#AppVersion}.0
VersionInfoCompany=305 Skylab
VersionInfoDescription={#AppName} Setup ({#BuildQualifier})
VersionInfoProductName={#AppName}

[Tasks]
Name: "desktopicon"; Description: "Create a desktop shortcut"; GroupDescription: "Additional shortcuts:"; Flags: checkedonce

[Files]
Source: "{#PayloadDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\SKYWriter Prototype"; Filename: "{app}\{#AppExeName}"; WorkingDir: "{app}"; IconFilename: "{app}\{#AppExeName}"
Name: "{autodesktop}\SKYWriter Prototype"; Filename: "{app}\{#AppExeName}"; WorkingDir: "{app}"; IconFilename: "{app}\{#AppExeName}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#AppExeName}"; WorkingDir: "{app}"; Description: "Launch SKYWriter Prototype"; Flags: nowait postinstall skipifsilent
