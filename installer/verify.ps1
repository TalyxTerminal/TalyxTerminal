# Run both launches through the installed shortcuts. Exit TALYX from Settings after each check.
$ErrorActionPreference = 'Stop'
$root = Split-Path $PSScriptRoot
$trial = Join-Path $root 'output\trial\installer-package'
$program = Join-Path $trial 'Program Files\TALYX'
$data = Join-Path $trial 'Data'
$setup = Join-Path $root 'output\installer\TALYX-Setup-x64.exe'
$menu = Join-Path ([Environment]::GetFolderPath('Programs')) 'TALYX.lnk'
$desktop = Join-Path ([Environment]::GetFolderPath('DesktopDirectory')) 'TALYX.lnk'
$registration = 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Uninstall\TALYX.Desktop_is1'

foreach ($path in @($trial, $menu, $desktop, $registration)) {
    if (Test-Path -LiteralPath $path) { throw "Acceptance needs a clean target. Already exists: $path" }
}
if (!(Test-Path -LiteralPath $setup)) { throw "Missing installer: $setup" }
[void](New-Item -ItemType Directory -Path $trial)
$env:TEMP = Join-Path $root 'output\tmp'
$env:TMP = $env:TEMP
$env:UV_CACHE_DIR = Join-Path $trial 'uv-cache'
$env:npm_config_cache = Join-Path $trial 'npm-cache'
$env:TALYX_DATA_DIR = $data
$env:PYTHONNOUSERSITE = '1'
$env:PYTHONPATH = ''
$env:PYTHONHOME = ''
$env:PATH = "$env:SystemRoot\System32;$env:SystemRoot"

function Run-Setup($file, $arguments) {
    $timer = [Diagnostics.Stopwatch]::StartNew()
    $process = Start-Process -FilePath $file -ArgumentList $arguments -WindowStyle Hidden -Wait -PassThru
    if ($process.ExitCode -ne 0) { throw "$file exited with code $($process.ExitCode)" }
    Write-Host ("{0} completed in {1:N1} seconds" -f (Split-Path $file -Leaf), $timer.Elapsed.TotalSeconds)
}

Run-Setup $setup "/VERYSILENT /SUPPRESSMSGBOXES /NORESTART /DIR=`"$program`" /LOG=`"$trial\install.log`""
$uninstall = Join-Path $program 'unins000.exe'
$local = $env:LOCALAPPDATA
$roaming = $env:APPDATA
try {
    foreach ($path in @($menu, $desktop, $registration)) {
        if (!(Test-Path -LiteralPath $path)) { throw "Setup did not create $path" }
    }
    $env:LOCALAPPDATA = Join-Path $trial 'profile\Local'
    $env:APPDATA = Join-Path $trial 'profile\Roaming'
    [void](New-Item -ItemType Directory -Path $env:LOCALAPPDATA,$env:APPDATA)
    foreach ($shortcut in @($menu, $desktop)) {
        Write-Host "Opening $shortcut. Verify the chart, then use Settings > Exit TALYX."
        $link = (New-Object -ComObject WScript.Shell).CreateShortcut($shortcut)
        if ($link.TargetPath -ne "$program\.venv\Scripts\pythonw.exe") { throw "Unexpected target: $($link.TargetPath)" }
        $process = Start-Process -FilePath $shortcut -PassThru
        $process.WaitForExit()
        if ($process.ExitCode -ne 0) { throw "Desktop exited with code $($process.ExitCode)" }
    }
    $workspaces = Join-Path $data 'platform\workspaces.json'
    $saved = (Get-FileHash -LiteralPath $workspaces).Hash
} finally {
    $env:LOCALAPPDATA = $local
    $env:APPDATA = $roaming
    Run-Setup $uninstall "/VERYSILENT /SUPPRESSMSGBOXES /NORESTART /LOG=`"$trial\uninstall.log`""
}
foreach ($path in @($program, $menu, $desktop, $registration)) {
    if (Test-Path -LiteralPath $path) { throw "Uninstall left $path" }
}
if ((Get-FileHash -LiteralPath $workspaces).Hash -ne $saved) { throw "Uninstall changed saved workspaces" }
$expected = Join-Path (Resolve-Path -LiteralPath (Join-Path $root 'output\trial')) 'installer-package'
if ((Resolve-Path -LiteralPath $trial).Path -ne $expected) { throw "Unexpected trial directory: $trial" }
Remove-Item -LiteralPath $trial -Recurse
Write-Host "Program, shortcuts and registration removed. Saved workspace hash verified. Trial cleaned up."
