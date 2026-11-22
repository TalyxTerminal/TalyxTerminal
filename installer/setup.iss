[Setup]
AppId=TALYX.Desktop
AppName=TALYX
AppVersion={#Version}
; The desktop holds this mutex while it runs (host/desktop/app.py): Setup asks to quit TALYX before replacing its files.
AppMutex=TALYX.Desktop
DefaultDirName={localappdata}\Programs\TALYX
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
DisableProgramGroupPage=yes
OutputDir={#Build}
OutputBaseFilename=TALYX-Setup-x64
SetupIconFile={#Payload}\host\desktop\talyx.ico
UninstallDisplayIcon={app}\host\desktop\talyx.ico
Compression=lzma2/fast
LZMANumBlockThreads=1
SolidCompression=yes
WizardStyle=modern
SetupLogging=no

[InstallDelete]
; An upgrade replaces the program, not adds to it: a file the new version no longer has (a removed module, an old
; package's files in the environment) would otherwise stay and still be loaded. The user's data and files are not here
; (they are under %LOCALAPPDATA%\TALYX\Data), so these folders are the program alone.
Type: filesandordirs; Name: "{app}\host"
Type: filesandordirs; Name: "{app}\kernel"
Type: filesandordirs; Name: "{app}\modules"
Type: filesandordirs; Name: "{app}\sdk"
Type: filesandordirs; Name: "{app}\vocabulary"
Type: filesandordirs; Name: "{app}\contracts"
Type: filesandordirs; Name: "{app}\scripts"
Type: filesandordirs; Name: "{app}\web"
Type: filesandordirs; Name: "{app}\examples"
Type: filesandordirs; Name: "{app}\python"
Type: filesandordirs; Name: "{app}\.venv"

[Files]
Source: "{#Payload}\*"; DestDir: "{app}"; Flags: recursesubdirs createallsubdirs ignoreversion; Excludes: "__pycache__,*.pyc"
Source: "{#Build}\MicrosoftEdgeWebview2Setup.exe"; Flags: dontcopy

[Icons]
Name: "{userprograms}\TALYX"; Filename: "{app}\.venv\Scripts\pythonw.exe"; Parameters: "-B -X utf8 ""{app}\launch.py"""; WorkingDir: "{app}"; IconFilename: "{app}\host\desktop\talyx.ico"; AppUserModelID: "TALYX.Desktop"
Name: "{userdesktop}\TALYX"; Filename: "{app}\.venv\Scripts\pythonw.exe"; Parameters: "-B -X utf8 ""{app}\launch.py"""; WorkingDir: "{app}"; IconFilename: "{app}\host\desktop\talyx.ico"; AppUserModelID: "TALYX.Desktop"

[Run]
Filename: "{app}\.venv\Scripts\pythonw.exe"; Parameters: "-B -X utf8 ""{app}\launch.py"""; Description: "Open TALYX"; Flags: nowait postinstall skipifsilent

[UninstallRun]
Filename: "{app}\.venv\Scripts\python.exe"; Parameters: "-B -X utf8 ""{app}\launch.py"" --uninstall"; Flags: runhidden waituntilterminated

[Code]
const
  WebViewKey = 'Software\Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}';

function HasWebView(Root: Integer): Boolean;
var
  Version: String;
begin
  Result := RegQueryStringValue(Root, WebViewKey, 'pv', Version) and
    (Version <> '') and (Version <> '0.0.0.0');
end;

function PrepareToInstall(var NeedsRestart: Boolean): String;
var
  Code: Integer;
begin
  Result := '';
  if HasWebView(HKLM32) or HasWebView(HKCU) then Exit;
  ExtractTemporaryFile('MicrosoftEdgeWebview2Setup.exe');
  if not Exec(ExpandConstant('{tmp}\MicrosoftEdgeWebview2Setup.exe'), '/silent /install',
      '', SW_HIDE, ewWaitUntilTerminated, Code) or (Code <> 0) or
      not (HasWebView(HKLM32) or HasWebView(HKCU)) then
    Result := Format('Microsoft WebView2 installation failed (%d). Check the internet connection and try again.', [Code]);
end;

procedure CurStepChanged(Step: TSetupStep);
begin
  if Step = ssPostInstall then
    if not SaveStringToFile(ExpandConstant('{app}\.venv\pyvenv.cfg'),
      Utf8Encode('home = ' + ExpandConstant('{app}\python') + #13#10 +
        'include-system-site-packages = false' + #13#10 + 'relocatable = true' + #13#10), False) then
      RaiseException('Could not configure the installed Python runtime.');
end;
