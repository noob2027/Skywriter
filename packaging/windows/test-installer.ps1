[CmdletBinding()]
param(
    [Parameter(Mandatory)]
    [string]$InstallerPath,
    [Parameter(Mandatory)]
    [string]$WorkingRoot
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$installer = (Resolve-Path -LiteralPath $InstallerPath).Path
$working = [System.IO.Path]::GetFullPath($WorkingRoot)
$installRoot = Join-Path $working "installed"
$installLog = Join-Path $working "install.log"
$uninstallLog = Join-Path $working "uninstall.log"
$shortcut = Join-Path $env:APPDATA "Microsoft\Windows\Start Menu\Programs\SKYWriter Prototype\SKYWriter Prototype.lnk"
$uninstallKey = "HKCU:\Software\Microsoft\Windows\CurrentVersion\Uninstall\{C67A9057-226E-4BB8-9437-31A092733D88}_is1"

if ((Test-Path -LiteralPath $uninstallKey) -or (Test-Path -LiteralPath $shortcut)) {
    throw "A per-user SKYWriter Prototype install already exists; refusing to replace it during smoke testing."
}

New-Item -ItemType Directory -Force -Path $working | Out-Null
$installArguments = @(
    "/VERYSILENT",
    "/SUPPRESSMSGBOXES",
    "/NORESTART",
    "/DIR=$installRoot",
    "/MERGETASKS=!desktopicon",
    "/LOG=$installLog"
)
$process = Start-Process -FilePath $installer -ArgumentList $installArguments -Wait -PassThru -WindowStyle Hidden
if ($process.ExitCode -ne 0) {
    throw "Installer smoke install failed with exit code $($process.ExitCode)."
}

$application = Join-Path $installRoot "SKYWriter.exe"
$uninstaller = Join-Path $installRoot "unins000.exe"
if (-not (Test-Path -LiteralPath $application -PathType Leaf)) {
    throw "Installed application is missing: $application"
}
if (-not (Test-Path -LiteralPath $shortcut -PathType Leaf)) {
    throw "Start-menu shortcut is missing: $shortcut"
}

$previousPlatform = $env:QT_QPA_PLATFORM
$previousFlags = $env:QTWEBENGINE_CHROMIUM_FLAGS
try {
    $env:QT_QPA_PLATFORM = "offscreen"
    $env:QTWEBENGINE_CHROMIUM_FLAGS = "--disable-gpu"
    Push-Location $env:SystemRoot
    try {
        $smoke = Start-Process -FilePath $application -ArgumentList "--packaged-smoke-test" -Wait -PassThru -WindowStyle Hidden
    }
    finally {
        Pop-Location
    }
    if ($smoke.ExitCode -ne 0) {
        throw "Packaged application launch smoke failed with exit code $($smoke.ExitCode)."
    }
}
finally {
    $env:QT_QPA_PLATFORM = $previousPlatform
    $env:QTWEBENGINE_CHROMIUM_FLAGS = $previousFlags
}

$uninstallArguments = @(
    "/VERYSILENT",
    "/SUPPRESSMSGBOXES",
    "/NORESTART",
    "/LOG=$uninstallLog"
)
$uninstall = Start-Process -FilePath $uninstaller -ArgumentList $uninstallArguments -Wait -PassThru -WindowStyle Hidden
if ($uninstall.ExitCode -ne 0) {
    throw "Installer smoke uninstall failed with exit code $($uninstall.ExitCode)."
}
if (Test-Path -LiteralPath $application) {
    throw "Application remained after uninstall: $application"
}
if (Test-Path -LiteralPath $shortcut) {
    throw "Start-menu shortcut remained after uninstall: $shortcut"
}

Write-Host "Install, arbitrary-working-directory launch, Start-menu shortcut, and uninstall smoke passed."
