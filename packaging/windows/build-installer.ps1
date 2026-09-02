[CmdletBinding()]
param(
    [string]$PythonPath = "python",
    [string]$BuildRoot = (Join-Path ([System.IO.Path]::GetTempPath()) "skywriter-task111-build"),
    [string]$OutputDirectory = (Join-Path $PSScriptRoot "..\..\artifacts\windows"),
    [string]$InnoSetupCompiler = "",
    [switch]$SkipInstallerSmoke
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$requiredPython = "3.12.13"
$innoVersion = "6.7.3"
$innoSha256 = "9C73C3BAE7ED48D44112A0F48E66742C00090BDB5BEF71D9D3C056C66E97B732"
$innoUri = "https://github.com/jrsoftware/issrc/releases/download/is-6_7_3/innosetup-6.7.3.exe"
$repositoryRoot = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot "..\..")).Path
$buildPath = [System.IO.Path]::GetFullPath($BuildRoot)
$outputPath = [System.IO.Path]::GetFullPath($OutputDirectory)

function Assert-SafeBuildPath([string]$Path) {
    $root = [System.IO.Path]::GetPathRoot($Path)
    $leaf = Split-Path -Leaf $Path
    if ($Path -eq $root -or $Path -eq $repositoryRoot -or $leaf -notmatch "skywriter|sw10[6-9]|sw11[01]") {
        throw "BuildRoot must be a dedicated path whose name contains 'skywriter' or 'sw106' through 'sw111': $Path"
    }
}

function Invoke-Checked([string]$Executable, [string[]]$Arguments) {
    & $Executable @Arguments
    if ($LASTEXITCODE -ne 0) {
        throw "$Executable failed with exit code $LASTEXITCODE."
    }
}

function Get-SignTool {
    if (Test-Path Env:SKYWRITER_SIGNTOOL) {
        return (Resolve-Path -LiteralPath $env:SKYWRITER_SIGNTOOL).Path
    }
    $command = Get-Command signtool.exe -ErrorAction SilentlyContinue
    if ($null -ne $command) {
        return $command.Source
    }
    $candidates = Get-ChildItem "${env:ProgramFiles(x86)}\Windows Kits\10\bin" -Filter signtool.exe -File -Recurse -ErrorAction SilentlyContinue |
        Where-Object { $_.DirectoryName -like "*\x64" } |
        Sort-Object FullName -Descending
    if ($candidates.Count -eq 0) {
        throw "Signing was requested, but signtool.exe was not found."
    }
    return $candidates[0].FullName
}

function Invoke-Sign([string]$Artifact, [string]$Certificate, [string]$Password) {
    $signTool = Get-SignTool
    $arguments = @("sign", "/fd", "SHA256", "/f", $Certificate, "/p", $Password)
    if (Test-Path Env:SKYWRITER_SIGNING_TIMESTAMP_URL) {
        $arguments += @("/tr", $env:SKYWRITER_SIGNING_TIMESTAMP_URL, "/td", "SHA256")
    }
    $arguments += $Artifact
    Invoke-Checked $signTool $arguments
    Invoke-Checked $signTool @("verify", "/pa", $Artifact)
}

Assert-SafeBuildPath $buildPath
New-Item -ItemType Directory -Force -Path $buildPath, $outputPath | Out-Null

$pythonCommand = Get-Command $PythonPath -ErrorAction Stop
$python = $pythonCommand.Source
$pythonIdentity = & $python -c "import platform, struct; print(f'{platform.python_version()}|{struct.calcsize(chr(80))*8}|{platform.system()}')"
if ($pythonIdentity -ne "$requiredPython|64|Windows") {
    throw "The pinned build requires CPython $requiredPython x64 on Windows; found $pythonIdentity."
}

$venv = Join-Path $buildPath "venv"
$venvPython = Join-Path $venv "Scripts\python.exe"
if (-not (Test-Path -LiteralPath $venvPython -PathType Leaf)) {
    Invoke-Checked $python @("-m", "venv", $venv)
}
Invoke-Checked $venvPython @(
    "-m", "pip", "install", "--disable-pip-version-check", "--no-input",
    "--requirement", (Join-Path $repositoryRoot "requirements.lock"),
    "--requirement", (Join-Path $repositoryRoot "packaging\requirements-build.lock")
)
Invoke-Checked $venvPython @(
    "-m", "pip", "install", "--disable-pip-version-check", "--no-input",
    "--no-build-isolation", "--no-deps", "--editable", $repositoryRoot
)
Invoke-Checked $venvPython @("-m", "pip", "check")

$notices = Join-Path $buildPath "notices"
Invoke-Checked $venvPython @(
    (Join-Path $repositoryRoot "tools\packaging\collect_licenses.py"),
    "--output", $notices
)

$icon = Join-Path $repositoryRoot "packaging\assets\skywriter-provisional.ico"
Invoke-Checked $venvPython @(
    (Join-Path $repositoryRoot "tools\packaging\create_provisional_icon.py"),
    "--output", $icon
)

$payloadDist = Join-Path $buildPath "dist"
$pyinstallerWork = Join-Path $buildPath "pyinstaller"
foreach ($path in ($payloadDist, $pyinstallerWork)) {
    if (Test-Path -LiteralPath $path) {
        Remove-Item -LiteralPath $path -Recurse -Force
    }
}
$previousNotices = $env:SKYWRITER_NOTICES_ROOT
try {
    $env:SKYWRITER_NOTICES_ROOT = $notices
    Invoke-Checked $venvPython @(
        "-m", "PyInstaller", "--noconfirm", "--clean",
        "--distpath", $payloadDist,
        "--workpath", $pyinstallerWork,
        (Join-Path $repositoryRoot "packaging\windows\skywriter.spec")
    )
}
finally {
    $env:SKYWRITER_NOTICES_ROOT = $previousNotices
}

$payload = Join-Path $payloadDist "SKYWriter"
$payloadExecutable = Join-Path $payload "SKYWriter.exe"
if (-not (Test-Path -LiteralPath $payloadExecutable -PathType Leaf)) {
    throw "PyInstaller did not produce the expected payload executable."
}

$hasCertificate = Test-Path Env:SKYWRITER_SIGN_CERTIFICATE_FILE
$hasPassword = Test-Path Env:SKYWRITER_SIGN_CERTIFICATE_PASSWORD
if ($hasCertificate -ne $hasPassword) {
    throw "Signing requires both SKYWRITER_SIGN_CERTIFICATE_FILE and SKYWRITER_SIGN_CERTIFICATE_PASSWORD."
}
$signed = $hasCertificate -and $hasPassword
if ($signed) {
    $certificate = (Resolve-Path -LiteralPath $env:SKYWRITER_SIGN_CERTIFICATE_FILE).Path
    Invoke-Sign $payloadExecutable $certificate $env:SKYWRITER_SIGN_CERTIFICATE_PASSWORD
}

if (-not $InnoSetupCompiler) {
    $innoDownload = Join-Path $buildPath "innosetup-$innoVersion.exe"
    $innoInstall = Join-Path $buildPath "inno-$innoVersion"
    $InnoSetupCompiler = Join-Path $innoInstall "ISCC.exe"
    if (-not (Test-Path -LiteralPath $InnoSetupCompiler -PathType Leaf)) {
        if (-not (Test-Path -LiteralPath $innoDownload -PathType Leaf)) {
            Invoke-WebRequest -Uri $innoUri -OutFile $innoDownload
        }
        $downloadHash = (Get-FileHash -LiteralPath $innoDownload -Algorithm SHA256).Hash
        if ($downloadHash -ne $innoSha256) {
            throw "Inno Setup download hash mismatch: $downloadHash"
        }
        $install = Start-Process -FilePath $innoDownload -ArgumentList @(
            "/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART", "/CURRENTUSER",
            "/NOICONS", "/DIR=$innoInstall"
        ) -Wait -PassThru -WindowStyle Hidden
        if ($install.ExitCode -ne 0) {
            throw "Inno Setup tool installation failed with exit code $($install.ExitCode)."
        }
    }
}
$innoCompiler = (Resolve-Path -LiteralPath $InnoSetupCompiler).Path

$version = & $venvPython -c "from skywriter import __version__; print(__version__)"
$installerName = "SKYWriter-Prototype-Setup-$version.exe"
$installer = Join-Path $outputPath $installerName
foreach ($path in ($installer, (Join-Path $outputPath "SHA256SUMS.txt"), (Join-Path $outputPath "build-metadata.json"))) {
    Remove-Item -LiteralPath $path -Force -ErrorAction SilentlyContinue
}
$signedDefine = if ($signed) { "1" } else { "0" }
Invoke-Checked $innoCompiler @(
    "/DAppVersion=$version",
    "/DPayloadDir=$payload",
    "/DOutputDir=$outputPath",
    "/DAppIcon=$icon",
    "/DSignedBuild=$signedDefine",
    (Join-Path $repositoryRoot "packaging\windows\installer.iss")
)
if (-not (Test-Path -LiteralPath $installer -PathType Leaf)) {
    throw "Inno Setup did not produce $installerName."
}
if ($signed) {
    Invoke-Sign $installer $certificate $env:SKYWRITER_SIGN_CERTIFICATE_PASSWORD
}

if (-not $SkipInstallerSmoke) {
    $smokeRoot = Join-Path $buildPath "installer-smoke"
    if (Test-Path -LiteralPath $smokeRoot) {
        Remove-Item -LiteralPath $smokeRoot -Recurse -Force
    }
    & (Join-Path $repositoryRoot "packaging\windows\test-installer.ps1") -InstallerPath $installer -WorkingRoot $smokeRoot
    if ($LASTEXITCODE -ne 0) {
        throw "Installer smoke script failed with exit code $LASTEXITCODE."
    }
    Copy-Item -LiteralPath (Join-Path $smokeRoot "packaged-map-smoke.json") -Destination $outputPath
    Copy-Item -LiteralPath (Join-Path $smokeRoot "packaged-map-smoke.png") -Destination $outputPath
    $acceptanceOutput = Join-Path $outputPath "installed-ui-acceptance"
    if (Test-Path -LiteralPath $acceptanceOutput) {
        Remove-Item -LiteralPath $acceptanceOutput -Recurse -Force
    }
    Copy-Item -LiteralPath (Join-Path $smokeRoot "installed-ui-acceptance") -Destination $acceptanceOutput -Recurse
}

$artifact = Get-Item -LiteralPath $installer
$hash = (Get-FileHash -LiteralPath $installer -Algorithm SHA256).Hash.ToLowerInvariant()
$manifest = "$hash  $installerName`n"
[System.IO.File]::WriteAllText((Join-Path $outputPath "SHA256SUMS.txt"), $manifest, [System.Text.UTF8Encoding]::new($false))
$metadata = [ordered]@{
    product = "SKYWriter Prototype"
    version = $version
    installer = $installerName
    bytes = $artifact.Length
    sha256 = $hash
    signed = $signed
    python = $requiredPython
    pyinstaller = "6.22.2"
    inno_setup = $innoVersion
}
$metadata | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $outputPath "build-metadata.json") -Encoding utf8NoBOM

Write-Host "Built $installerName"
Write-Host "Bytes: $($artifact.Length)"
Write-Host "SHA-256: $hash"
Write-Host "Signed: $signed"
